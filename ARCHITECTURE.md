# AIFeeders — Architecture Reference

> Build #62 · OpenShift `aifeeders` · LangGraph · Jev System One · EKS-portable

---

## 1. Overview

AIFeeders is a fully agentic AI news pipeline. It searches the web for AI news, scores and selects the most relevant articles using the Jev System One decision engine, generates four persona-specific perspectives with an LLM, evaluates the content for quality and safety (again via Jev), and publishes a structured post to LinkedIn — all without human intervention.

The system is built on three architectural pillars:

1. **LangGraph state machine** — deterministic 10-node workflow; every transition is explicit and logged.
2. **Jev System One** — fast, structured multi-question AI scoring replaces three different LLM-as-judge calls.
3. **Deterministic publishing layer** — `PublisherAgent` has no LLM calls; every decision is a threshold comparison.

---

## 2. System Diagram

```
External                    OpenShift / EKS cluster (namespace: aifeeders)
─────────                   ─────────────────────────────────────────────────────────────────────
                            ┌───────────────────────────────────────────────────────────────────┐
                            │                                                                   │
GNews API ──────────────────┼──► news-mcp (pod)                                                │
   gnews.io                 │      FastAPI + GNews adapter                                     │
   2-key rotation           │      /search_latest  /fetch_article                              │
                            │                │                                                  │
                            │                ▼                                                  │
                            │      daily-news-api (pod × 2)                                    │
                            │        FastAPI + LangGraph runner                                 │
                            │        POST /run    GET /health    GET /metrics                   │
                            │                │                                                  │
                            │         LangGraph workflow                                        │
                            │      (10-node state machine)                                     │
                            │                │                                                  │
Jev System One ─────────────┼─────────── jev_prefilter ─── jev_router ─── evaluate            │
   IBM gateway              │                                                                   │
   /v1/systemone            │                │                                                  │
   Bearer auth              │         pageindex-mcp (pod × 1, in-memory)                       │
                            │           /index_document  /get_relevant_sections                 │
                            │                │                                                  │
LLM endpoint ───────────────┼─────── summarize ─── generate_personas                          │
   OpenAI-compat            │                │                                                  │
                            │                ▼                                                  │
                            │      evaluation-mcp (pod × 2)                                    │
                            │        LLM evaluation fallback only                               │
                            │                │                                                  │
                            │                ▼                                                  │
LinkedIn API ───────────────┼──► linkedin-mcp (pod × 1, stateful)                             │
   Posts API                │      OAuth token store + /create_post /create_comment            │
   Comments API             │      /delete_post  /audit  /auth/linkedin                        │
                            │                │                                                  │
CronJob ────────────────────┼──► daily-ai-news (10:00 UTC daily)                               │
                            │      Triggers POST /run on daily-news-api                        │
                            │                                                                   │
ConfigMap ──────────────────┼──► daily-news-config   (non-secret env vars)                    │
Secret ─────────────────────┼──► daily-news-secrets  (API keys, LinkedIn credentials)         │
                            │                                                                   │
Langfuse ───────────────────┼──► tracing (optional, outbound only)                            │
                            └───────────────────────────────────────────────────────────────────┘
```

---

## 3. LangGraph State Machine — 10 Nodes

### State schema ([`daily_news_graph.py`](src/daily_news/workflows/daily_news_graph.py))

```python
class NewsWorkflowState(TypedDict):
    run_id: str                      # "RUN-{12 hex chars}" — unique per trigger

    # News discovery
    raw_articles: list[dict]         # all articles from all 9 GNews queries
    deduplicated_articles: list[dict] # after MD5 hash dedup + PublishedStore filter
    selected_articles: list[dict]    # after jev_prefilter: top 2

    # PageIndex
    pageindex_documents: list[dict]  # RAG index receipts

    # Jev decision signals
    jev_persona_hints: list[str]     # warm start from jev_prefilter (#1 article's persona_fit)
    jev_active_personas: list[str]   # confirmed active personas (["policy","business"])
    jev_prefilter_scores: dict       # dict[article_id → scores] — per-article, keyed by article_id

    # Generation
    summaries: list[dict]            # list of NewsSummary.model_dump()
    persona_outputs: list[dict]      # list of PersonaSetOutput.model_dump()

    # Evaluation
    evaluation_results: list[dict]   # list of EvaluationResult.model_dump()
    retry_count: int                 # incremented on each REGENERATE decision

    # Approval
    approval_status: str             # "PENDING" | "APPROVED" | "REJECTED"

    # Publishing
    linkedin_results: list[dict]     # one per article published

    # Run metadata
    workflow_status: str             # last completed node label
    errors: list[str]                # non-fatal errors (pipeline continues)
```

### Node descriptions

| Node | Function | Key behaviour |
|---|---|---|
| `discover_news` | 9 GNews queries → `raw_articles` | `hours=24`, `limit=10`; errors are non-fatal |
| `deduplicate` | MD5 dedup + PublishedStore filter → `deduplicated_articles` | Pass 1: within-run hash dedup; Pass 2: cross-run PublishedStore.filter_unpublished() |
| `fetch_articles` | Full HTML fetch per article → `selected_articles` | Caps at 30 articles for Jev budget |
| `index_pageindex` | RAG index per article → `pageindex_documents` | Errors are non-fatal; article proceeds without RAG |
| `jev_prefilter` | Jev Decision #1 — score + select → top 2 | 11 questions per article; parallel; falls back to `[:2]` |
| `summarize` | LLM → `NewsSummary` per article | SUMMARY_CAP=350 chars; uses PageIndex for evidence |
| `jev_router` | Jev Decision #2 — persona routing → `jev_active_personas` | 4 noul questions; merges with `jev_persona_hints`; always ≥ 1 |
| `generate_personas` | LLM persona generation — active subset only | Parallel coroutines; skips inactive personas |
| `evaluate` | Jev Decision #3 — quality gate | 10 questions; PASS/REGENERATE/BLOCK/HUMAN_REVIEW |
| `publish` | LinkedIn post + comments | 3-gate dedup; deterministic; no LLM calls |

### Routing logic

```
evaluate → route_evaluation()
  ├── any REGENERATE + retry_count < 2  → "summarize"  (retry loop)
  ├── any REGENERATE + retry_count ≥ 2  → "publish"    (publish PASS items, skip the rest)
  ├── any BLOCK                         → "publish"    (only PASS items published)
  ├── all PASS                          → "publish"
  └── no results                        → "__end__"
```

### Graph wiring

```
START
  └─► discover_news
        └─► deduplicate
              └─► fetch_articles
                    └─► index_pageindex
                          └─► jev_prefilter
                                └─► summarize ◄──────────────────────────┐
                                      └─► jev_router                     │ retry (max 2)
                                            └─► generate_personas         │
                                                  └─► evaluate ──────────┘
                                                        └─► publish
                                                              └─► END
```

---

## 4. Jev System One Integration

### Why Jev, not a prompt to the LLM?

| Dimension | LLM-as-judge | Jev System One |
|---|---|---|
| Latency | 3–8s per call | < 2s per call |
| Output | Free text → requires parsing | Structured floats + named labels |
| Determinism | Low — rephrasing changes answer | High — calibrated scoring model |
| Cost | Full LLM token usage | Lightweight lora_decision_head |
| Failure mode | Can hallucinate in its own evaluation | Threshold math — no hallucination possible |
| Multi-question | One question per call (or complex prompt) | All questions in one HTTP call |

### Question types

| Type | Description | Response |
|---|---|---|
| `noul` | Numeric probability — like boolean but returns float 0–1 | `answers.<key>.noul` (float) |
| `choice` | Pick one label from a named dict | `answers.<key>.choice` (string label) |
| `score` | Rate on a descriptive scale (list of 2–10 levels) | `answers.<key>.score` (float 0..N-1) |

### Decision #1 — `jev_prefilter_articles`

**File:** [`src/daily_news/agents/jev_agents.py`](src/daily_news/agents/jev_agents.py)
**Client method:** [`JevClient.prefilter_article()`](src/daily_news/mcp/jev_client.py)

```
Input: up to 30 article dicts (title + source + url + category + content[:2000])
       Each article is a separate POST /v1/systemone call.
       Concurrent: asyncio.Semaphore(5) — at most 5 in-flight at once.

Per article, 11 questions in one call:
  is_ai_topic         noul  — hard gate: article must be primarily about AI
  relevance_score     noul  — overall AI relevance 0-1
  event_type          choice — product_launch|funding|regulation|research|acquisition|other
  significance        score — 0-4 normalised to 0-1 (landmark vs minor)
  controversy_level   choice — low|medium|high
  persona_fit_business  noul — would a business executive find this directly relevant?
  persona_fit_policy    noul — would a policy maker find this directly relevant?
  persona_fit_genz      noul — would a generalist find this accessible and relevant?
  persona_fit_linkedin  noul — would a tech/workforce professional find this relevant?
  estimated_engagement  noul — likelihood of LinkedIn engagement 0-1
  skip_reason         choice — not_ai|low_quality|none

Composite score = relevance_score × 0.6 + estimated_engagement × 0.4
Selection: top 2 by composite score (articles where is_ai_topic > 0.5 only)

State writes (into NewsWorkflowState):
  selected_articles:    top 2 article dicts
  jev_prefilter_scores: {
    "<article_id>": {
      "event_type":           "regulation",
      "relevance_score":      0.92,
      "significance":         0.75,
      "estimated_engagement": 0.78,
      "controversy_level":    "high",
      "active_personas":      ["policy","business","genz"],
      "persona_scores":       {"business": 0.87, "policy": 0.94, "genz": 0.71, "linkedin": 0.63}
    },
    "<article_id_2>": { ... }
  }
  jev_persona_hints:    ["policy","business","genz"]  ← persona_fit from #1 article only

Fallback (JEV_ENABLED=false or exception):
  selected_articles = articles[:2]
  jev_prefilter_scores = {}
  jev_persona_hints = []
```

**How `event_type` drives the post hook label:**

```python
_EVENT_LABEL = {
    "product_launch": "Product Launch",
    "funding":        "Funding & M&A",
    "regulation":     "AI Regulation",
    "research":       "AI Research",
    "acquisition":    "Funding & M&A",
    "other":          "AI News",          # generic fallback
}
hook_category = _EVENT_LABEL.get(event_raw, "AI News")
# → "🤖  AI REGULATION  ·  Powered by Jev"
```

**How `persona_scores` drives Perspectives ordering:**

```python
ranked = sorted(
    [(p, ps_scores.get(p, 0.0)) for p in active_personas],
    key=lambda x: x[1], reverse=True,
)
# → Perspectives section shows highest-scoring persona first.
# → Jev verdict: "🏛️ Policy Makers  ›  💼 Business Strategists  ›  🎓 Generalists"
```

### Decision #2 — `jev_route_personas`

**File:** [`src/daily_news/agents/jev_agents.py`](src/daily_news/agents/jev_agents.py)
**Client method:** [`JevClient.route_personas()`](src/daily_news/mcp/jev_client.py)

```
Input: NewsSummary dict (headline + summary + impact fields + key_points)
       One POST /v1/systemone call per summary.

4 questions (one per persona):
  needs_business  noul — business revenue, market competition, enterprise decisions
  needs_policy    noul — government regulation, AI safety, data privacy, governance
  needs_genz      noul — generalist accessible, everyday life, career learning
  needs_linkedin  noul — tech strategy, engineering, workforce, career choices

Threshold: > 0.5 → persona is active

Merge with jev_persona_hints (union):
  - jev_prefilter ran on raw article text (pre-summarise)
  - jev_router runs on the generated NewsSummary (post-summarise)
  - Union preserves signals from both passes — different information.
  → e.g. prefilter says ["policy","business"], router says ["policy","genz"]
  → merged: ["policy","business","genz"]

Always returns ≥ 1 persona: if all below 0.5, defaults to [linkedin]

State writes:
  jev_active_personas: ["policy", "business", "genz"]

Consumed by generate_personas:
  active_personas = [PersonaType(v) for v in state["jev_active_personas"]]
  → only those LLM calls run; e.g. only 3 of 4 = 1 LLM call saved per run.

Fallback: all 4 personas
```

### Decision #3 — `EvaluationAgent` (via `JevClient.evaluate_content`)

**File:** [`src/daily_news/agents/evaluation_agent.py`](src/daily_news/agents/evaluation_agent.py)
**Client method:** [`JevClient.evaluate_content()`](src/daily_news/mcp/jev_client.py)

```
Input: source_text (original article) + generated_text (summary + personas)
       One POST /v1/systemone call per article.

10 questions:
  factuality            score 0-4 → /4 → 0-1 (fully accurate = 1.0)
  groundedness          score 0-4 → /4 → 0-1 (fully grounded in source = 1.0)
  hallucination         score 0-4 → /4 → 0-1 (no hallucination = 0.0)
  relevance             score 0-4 → /4 → 0-1
  toxicity              score 0-4 → /4 → 0-1 (completely safe = 0.0)
  pii_detected          noul     → 0-1 (>0.5 = PII present)
  prompt_injection_detected  noul → 0-1 (>0.5 = injection present)
  political_bias_detected    noul → 0-1 (logged only, not blocking)
  policy_check          choice   → PASS|REVIEW|FAIL
  overall_score         score 0-4 → /4 → 0-1

Gate logic (EvaluationAgent._apply_gate()):
  BLOCK if: pii_detected OR prompt_injection_detected OR policy_check=FAIL
  REGENERATE if: factuality < 0.50 OR groundedness < 0.50 OR hallucination > 0.85
                 (max 2 retries → back to summarize node)
  HUMAN_REVIEW if: policy_check=REVIEW (borderline content)
  PASS: all gates cleared

Fallback (JEV_ENABLED=false):
  Uses EvaluationMCPClient (LLM-based evaluation via evaluation-mcp pod).
  Same EvaluationResult model + same _apply_gate() logic.
  → switching between Jev and LLM evaluation requires only JEV_ENABLED env change.
```

### Decision Strategy — How Every Post Field Is Derived from Jev Scores

This section maps every visible field in the published LinkedIn post back to the exact Jev question that produced it. All values come from the single `POST /v1/systemone` call in Decision #1 (`prefilter_article`). Nothing in the post is hardcoded — every percentage, label, ordering, and threshold comparison is a live Jev answer for that specific article.

#### Input to Jev — what it reads per article

```
TITLE:    <article headline>
SOURCE:   <publication name>
URL:      <canonical URL>
CATEGORY: <NewsCategory value, e.g. ai_technology>
CONTENT:  <first 2000 chars of article body>
```

#### Full field-to-question mapping

```
Post field                              Jev question         Type    How value is used
──────────────────────────────────────────────────────────────────────────────────────────
🤖  AI REGULATION  ·  Powered by Jev   event_type           choice  mapped via _EVENT_LABEL dict:
                                                                       product_launch → "PRODUCT LAUNCH"
                                                                       funding        → "FUNDING & M&A"
                                                                       regulation     → "AI REGULATION"
                                                                       research       → "AI RESEARCH"
                                                                       acquisition    → "FUNDING & M&A"
                                                                       other          → "AI NEWS"

🏛️  Primary audience : Policy — 85%    persona_fit_policy   noul    highest-scoring persona across all 4
                                        persona_fit_business         raw score displayed as %, label
                                        persona_fit_genz             from _PMETA dict
                                        persona_fit_linkedin

📋  Story type : Regulation             event_type           choice  same answer as hook label,
                                                                     title-cased for display

🎯  AI relevance : 90%                  relevance_score      noul    × 100 → percentage

    Market signal: [███░░]              significance         score   0..4 raw → /4 → 0-1
                                                                     bars = round(sig × 5)
                                                                     █ per bar, ░ for remainder

⚡  Engagement est. : 43%              estimated_engagement noul    × 100 → percentage

    Controversy: Medium                 controversy_level    choice  low|medium|high → title-cased

👥  Audience impact : 🏛️ 85% › 🎓 77%  persona_fit_* × 4   noul    all 4 scores ranked descending;
                                                                     top 3 shown

🔥  AI market shift : HIGH             significance         score   threshold combination:
                                        relevance_score      noul    sig ≥ 0.6 AND rel ≥ 0.75 → 🔥 HIGH
                                                                     sig ≥ 0.4 AND rel ≥ 0.60 → 📡 MODERATE
                                                                     else                      → 📊 INFORMATIONAL

🧵  Perspectives section order          persona_fit_* × 4   noul    ranked list drives which persona
                                                                     block appears first in the post

⚙️  Jev audience verdict               persona_fit_* × 4   noul    same ranked list, top 3 as labels:
                                                                     💼 Business Strategists
                                                                     🏛️ Policy Makers
                                                                     🎓 Generalists
                                                                     🧠 Tech & Workforce
```

#### How scores change by article type — worked examples

| Article | `event_type` | `relevance` | `significance` | Top persona | `engagement` | `controversy` | Market shift |
|---|---|---|---|---|---|---|---|
| EU AI Act enforcement news | `regulation` | 0.94 | 0.80 | 🏛️ Policy 94% | 0.72 | High | 🔥 HIGH |
| OpenAI $40B funding round | `funding` | 0.95 | 0.85 | 💼 Business 93% | 0.88 | Medium | 🔥 HIGH |
| AI existential risk debate | `other` | 0.90 | 0.60 | 🏛️ Policy 85% | 0.43 | Medium | 🔥 HIGH |
| GPT-5 model release | `product_launch` | 0.96 | 0.90 | 🧠 Tech 91% | 0.85 | Medium | 🔥 HIGH |
| AI chip breakthrough paper | `research` | 0.92 | 0.88 | 🧠 Tech 91% | 0.75 | Low | 🔥 HIGH |
| AI job displacement study | `research` | 0.88 | 0.70 | 🎓 Generalist 90% | 0.65 | High | 🔥 HIGH |
| Routine vendor blog post | `product_launch` | 0.60 | 0.20 | 🧠 Tech 65% | 0.18 | Low | 📊 INFORMATIONAL |

Key observations:
- **Primary audience flips** entirely between articles — Policy-first for regulation/risk, Business-first for funding, Tech-first for research/launches, Generalist-first for workforce stories.
- **Hook label** is the one field that distinguishes the story type at a glance before the reader reads the headline.
- **Engagement estimate** is consistently lower for opinion/risk pieces than for announcements — Jev models LinkedIn audience behaviour.
- **Market shift** depends on the product of both `significance` AND `relevance` — a highly relevant but low-significance article stays INFORMATIONAL; a landmark event on a niche AI subfield may also stay MODERATE.

#### Why the same article can produce different scores on re-run

Jev uses a LoRA decision head on Qwen/Qwen3.5-2B. The `lora_decision_head` method returns calibrated probabilities, not greedy argmax outputs. Minor temperature variation across calls means scores can shift by ±0.03–0.05 between runs. This is expected and by design — the system is robust to small score variance because all gates use thresholds (> 0.5, ≥ 0.60, ≥ 0.75) not exact equality.

---

## 5. PublishedStore — Cross-Run Deduplication

### Problem

The CronJob runs every day at 10:00 UTC. It may also be:
- Manually re-triggered (operator testing).
- Retried by Kubernetes if the previous Job failed.
- Run twice in quick succession (misconfigured schedule).

Without a persistent store, the same article would be published multiple times. Worse: if the LLM pipeline succeeds and the LinkedIn call succeeds, but the pod crashes before writing the result, the next run would try to publish the same article again — and LinkedIn's idempotency key would prevent a duplicate post, but the run would report a failure.

### Design

**File:** [`src/daily_news/agents/published_store.py`](src/daily_news/agents/published_store.py)

```
PublishedStore
  File path: $AIFEEDERS_STORE_PATH (default: /tmp/aifeeders_published.json)
  Format: JSON object — {"article_id:YYYY-MM-DD": "ISO-8601 timestamp"}
  Lock: threading.Lock — one instance per process (module-level singleton)
  TTL: 7 days — entries older than 7 days are purged on every _load_locked() call
```

The store is a **module-level singleton** (`published_store = PublishedStore()`) imported by both `daily_news_graph.py` (Gate 1) and `publisher_agent.py` (Gate 2). Within a process, both gates share the same in-memory cache — no double file reads.

### Three-Gate Architecture

```
Gate 1: deduplicate node  ─────────────────────────────────────────────────────────
  Location: src/daily_news/workflows/daily_news_graph.py :: deduplicate()

  Pass 1 — within-run hash dedup:
    For each article in raw_articles:
      content_hash = MD5(title + url)
      if hash seen in this run → skip (duplicate from 9 GNews queries hitting same article)

  Pass 2 — cross-run dedup:
    published_store.filter_unpublished(unique_articles)
    → reads /tmp/aifeeders_published.json (lazy load on first call)
    → purges entries older than TTL
    → returns only articles whose "{article_id}:{today}" key is NOT in the store

  Purpose: block stale articles BEFORE any LLM work starts.
  Benefit: no LLM tokens wasted on already-published articles.

Gate 2: publish node  ──────────────────────────────────────────────────────────────
  Location: src/daily_news/agents/publisher_agent.py :: publish() line 146

  published_store.is_published(article_id)
    → True if "{article_id}:{today}" is in the cache
    → Returns skipped_result("already_published_today") immediately

  Purpose: race-condition guard — two simultaneous runs both pass Gate 1
           (both see the article as unpublished, both proceed through the LLM pipeline).
           Gate 2 catches the second one right before the LinkedIn call.

Gate 3: LinkedIn MCP idempotency key  ──────────────────────────────────────────────
  Location: src/daily_news/agents/publisher_agent.py :: _make_publication_key()
            mcp_servers/linkedin_mcp/server.py :: linkedin_create_post()

  key = f"{article_id}:{date.today().isoformat()}:{sha256(post_body)[:12]}"

  Properties:
    - No run_id → identical across all CronJob retries on the same calendar day.
    - body_hash included → if the post composition changes (new code), a new post is created
      (not the old truncated one stored by LinkedIn's server-side dedup).
    - Stable within a day → LinkedIn MCP can detect and skip duplicate requests.

  Purpose: last-resort guard — even if the pod crashed between mark_published() and
           return, and a retry publishes again, LinkedIn rejects it or returns the URN
           of the already-published post (HTTP 201 with existing URN).
```

### Entry lifecycle

```
1. discover_news
   article_id = MD5(title + url)    ← deterministic, reproducible

2. deduplicate (Gate 1)
   filter_unpublished([...])
   → if article_id:today in store → article dropped, no LLM work
   → otherwise → proceeds to fetch_articles

3. LLM pipeline
   summarize → jev_router → generate_personas → evaluate

4. publish (Gate 2 check)
   is_published(article_id)
   → if True → return skipped_result("already_published_today")

5. publish (Gate 3 + write)
   LinkedIn MCP called with publication_key.
   On success (HTTP 201):
     mark_published(article_id)
     → acquires threading.Lock
     → writes {"{article_id}:{today}": "2026-09-24T10:31:22Z"} to JSON
     → releases lock

6. TTL purge (on next load)
   _load_locked() called once per process lifetime.
   cutoff = today - 7 days
   entries where date_part < cutoff are dropped.
   → file stays small: max ~14 entries (7 days × 2 articles/day)
```

### Failure safety

- **File unreadable** (permissions, corrupt JSON): `_load_locked()` catches the exception, starts with empty cache, logs a WARNING. Publishing is NOT blocked.
- **File unwritable** (disk full, mounted read-only): `_flush_locked()` catches the exception, logs a WARNING. The in-memory cache is still updated so Gate 2 still works within the same process lifetime.
- **Pod crash between `mark_published` and `return`**: The next run will see the article as published (store was flushed to disk) and will skip at Gate 1 or Gate 2. The LinkedIn post exists from the crashed run.
- **Pod crash before `mark_published`**: The next run will re-publish. Gate 3 (LinkedIn idempotency key) will detect the duplicate and return the existing URN — no duplicate post on LinkedIn.

### Persistence across pod restarts

By default, the store is at `/tmp/aifeeders_published.json` — ephemeral, lost on pod restart. This is acceptable because:
- CronJob runs once per day; pod restarts between runs are the common case.
- The same article won't be in GNews for more than 24 hours (hours=24 query window).
- LinkedIn's Gate 3 key catches any duplicate even without the store.

For **guaranteed cross-restart persistence** (e.g. if the pod restarts mid-day):
```yaml
# In the Deployment manifest:
env:
  - name: AIFEEDERS_STORE_PATH
    value: /data/aifeeders_published.json
volumeMounts:
  - name: store-pvc
    mountPath: /data
volumes:
  - name: store-pvc
    persistentVolumeClaim:
      claimName: aifeeders-store
```

---

## 6. Post Composition — LinkedIn UTF-16 Budget

### The truncation bug (fixed in build #61)

**Root cause:** LinkedIn's Posts API counts characters as UTF-16 code units (matching JavaScript's `String.length`). Python's `len()` counts Unicode code points. For characters outside the Basic Multilingual Plane (U+10000+) — which includes most emoji (`💼 🎓 🧠 🔥 📌 📈 👷 🔬 🧵 💬 🤖 ⚠️ 📋 🎯 ⚡ 👥`) — each character costs:
- Python `len()`: 1 code point.
- LinkedIn: 2 UTF-16 code units (surrogate pair).

A post with 30 such emoji that Python reports as 2980 chars is actually 3010 LinkedIn units → silent truncation.

### Fix

```python
def _linkedin_len(text: str) -> int:
    """Count text length as LinkedIn does: UTF-16 code units."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)
```

This function is used in:
- [`publisher_agent.py`](src/daily_news/agents/publisher_agent.py) — `_add()`, `_clip_at_sentence()`, `_hard_clip()`, `_make_publication_key()`, final safety guard.
- [`mcp_servers/linkedin_mcp/server.py`](mcp_servers/linkedin_mcp/server.py) — oversize detection before sending to LinkedIn API.

### Budget hierarchy

```
POST_LIMIT = 2900 LinkedIn UTF-16 units  (3000 limit - 100 safety margin)

FOOTER_TEXT_COST = _linkedin_len(_FOOTER) + 141 + 3
  _FOOTER:    "⚠️ AI-simulated perspectives — not verified opinions or professional advice.\n
               🤖 AIFeeders  ·  Agentic AI  ·  Powered by Jev\n\n
               #AI #AgenticAI ... #AILeadership"         ≈ 255 units
  verdict:    "⚙️  Jev audience verdict  —  ..."         ≈ 95 units
  blank:      ""                                         = 1 unit
  CTA:        "💬  What's your take? Drop it below. 👇"  ≈ 44 units
  blank:      ""                                         = 1 unit
  ─────────────────────────────────────────────────────────────────
  FOOTER_TEXT_COST ≈ 398 units

BODY_LIMIT = POST_LIMIT - FOOTER_TEXT_COST ≈ 2502 units

Section budgets within BODY_LIMIT:
  ① Hook              ~50  units  (event_type label + Powered by Jev)
  ② Context           ≤350 units  (SUMMARY_CAP — LLM summary clipped at sentence boundary)
  ③ Jev Decision      ~250 units  (7 sub-lines: event, relevance, engagement, audience, shift)
  ④ Key points        ≤3 × 90     = 270 units  (KEY_POINT_CAP=90 per bullet)
  ⑤ Impact snap       ≤4 × 120    = 480 units  (IMPACT_LINE_CAP=120 per line)
  ⑥ Source URL        ~80  units
  ⑦ Perspectives      remaining budget / n_personas (per-persona allocation);
                      each persona: label + clipped perspective + ≤2 evidence bullets;
                      evidence bullet cap: ev_budget // 2 (≥ 60 chars)
  ─────────────────────────────────────────────────────────────────
  Total body          fits within BODY_LIMIT

footer_block = verdict + "" + CTA + "" + _FOOTER  (assembled outside budget loop)
full_post = body.rstrip() + "\n\n" + footer_block
  → final safety guard: _hard_clip(full_post, 2900) if somehow over limit
```

---

## 7. MCP Service Architecture

### MCPHTTPClient (build #61 fix)

**File:** [`src/daily_news/mcp/client.py`](src/daily_news/mcp/client.py)

All five MCP clients (`LinkedInMCPClient`, `NewsMCPClient`, etc.) inherit from `MCPHTTPClient`. The base class handles:

1. **Nested error detection** — LinkedIn MCP and other MCP servers can return HTTP 200 with a body of `{"result": {"error": "...", "code": 403}}`. Before build #61, this was silently treated as success. The fix:
   ```python
   # Detects: {"result": {"error": ...}} at any nesting depth.
   if isinstance(result, dict) and "error" in result:
       raise MCPError(result["error"])
   if isinstance(result, dict):
       inner = result.get("result", {})
       if isinstance(inner, dict) and "error" in inner:
           raise MCPError(inner["error"])
   ```

2. **Retry logic** — 429 and 5xx responses are retry-eligible (configurable via `MCPHTTPClient.RETRY_STATUS_CODES`).

3. **Structured error classification** — all errors mapped to `error_class`: `PERMISSION_ERROR`, `AUTH_ERROR`, `RATE_LIMIT`, `SERVER_ERROR`, `NETWORK_ERROR`.

### MCP Servers

| Server | Type | State | Replicas | Notes |
|---|---|---|---|---|
| `news-mcp` | Stateless | None | 2 | 2-key GNews rotation; automatic on 403 |
| `evaluation-mcp` | Stateless | None | 2 | LLM eval fallback; only used when `JEV_ENABLED=false` |
| `pageindex-mcp` | Stateful | In-memory index | **1** | Index rebuilt per-run; lost on pod restart (acceptable) |
| `linkedin-mcp` | Stateful | OAuth token | **1** | Token stored in memory; must reauth on pod restart |

### linkedin-mcp — stateful singleton

`linkedin-mcp` must be a single pod because:
1. The OAuth token (access + refresh) is stored in memory.
2. Post idempotency key dedup is in-memory.
3. The LinkedIn audit log is in-memory.

**Token management:**
- Token obtained via `/auth/linkedin` OAuth flow (browser-based).
- Stored as module-level state in the server process.
- Survives pod restarts? **No** — token is lost when the pod restarts.
- Solution: use `oc port-forward` and re-auth after any pod restart.
- Token TTL: 60 days — must be renewed before expiry.

**Idempotency key storage:**
- `linkedin-mcp` tracks `publication_key` → `post_urn` in a dict.
- On duplicate key: returns the existing `post_urn` without calling LinkedIn API.
- Gate 3 in `publisher_agent.py` provides the stable key.

---

## 8. Build Flow

### OpenShift (source-to-image, S2I)

OpenShift builds run entirely in-cluster. No Docker daemon needed on the developer machine. The S2I builder:
1. Receives the source directory via `oc start-build --from-dir`.
2. Runs `pip install -e .` inside the BuildPod.
3. Produces an OCI image pushed to the internal registry.
4. Tags it as `image-registry.openshift-image-registry.svc:5000/aifeeders/<service>:latest`.

```
Developer machine                    OpenShift cluster
─────────────────                    ─────────────────────────────────────────
source code
    │
    ▼
rsync (strip .venv/ → < 1 MB)
    │
    ▼
oc start-build <service>             ┌── BuildPod (temporary pod)
  --from-dir="$TMPDIR"  ────────────►│   1. Receives source tarball via stdin
                                     │   2. pip install -e . (layer-cached)
                                     │   3. Copies src/ into image
                                     │   4. Pushes :latest to internal registry
                                     └── BuildPod terminates (self-cleaning)
                                              │
                                              ▼
                                     Internal registry
                                     aifeeders/<service>:latest
                                              │
oc rollout restart deployment  ──────────────►│
                                              ▼
                                     New pods pull :latest
                                     (RollingUpdate — zero downtime)
                                     Old pods terminate
```

### Build history self-management

```yaml
# In every BuildConfig:
spec:
  successfulBuildsHistoryLimit: 1   # keep only the latest successful build
  failedBuildsHistoryLimit: 1       # keep only the latest failed build
```

Effect:
- `daily-news-61` is deleted automatically when `daily-news-62` succeeds.
- No manual cleanup ever needed.
- `oc get builds -n aifeeders` always shows exactly 1 build per service (or 2 during a build).

To apply to all BuildConfigs at once:
```bash
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

### Why rsync to a tmpdir?

The `.venv/` directory is ~270 MB. `oc start-build --from-dir` tarballs the entire directory and streams it to the BuildPod. With `.venv/`:
- Upload: ~270 MB → upload timeout at 60s.
- Build total: 5–10 minutes.

Without `.venv/` (rsync exclude):
- Upload: < 1 MB → upload completes in < 3s.
- `pip install` inside BuildPod: ~15s (layer-cached after first build).
- Build total: ~2 minutes.

The rsync exclude list (mirrors `.dockerignore`):
```
.venv/  **/__pycache__/  **/*.pyc  .git/  .pytest_cache/
*.egg-info/  .env  .env.*  dist/  build/  htmlcov/
.coverage  .tox/  .DS_Store
```

---

## 9. OpenShift Deployment Manifest Summary

### Services and their manifests

| Service | Deployment | Service | Route | Replicas | Resources |
|---|---|---|---|---|---|
| `daily-news-api` | `openshift/api/deployment.yaml` | `openshift/api/service.yaml` | `openshift/api/route.yaml` | 2 (HPA) | 256Mi–512Mi |
| `news-mcp` | `openshift/news-mcp/deployment.yaml` | `...service.yaml` | `...route.yaml` | 2 | 128Mi–256Mi |
| `evaluation-mcp` | `openshift/evaluation-mcp/deployment.yaml` | `...service.yaml` | `...route.yaml` | 2 | 256Mi–512Mi |
| `linkedin-mcp` | `openshift/linkedin-mcp/deployment.yaml` | `...service.yaml` | `...route.yaml` | **1** | 128Mi–256Mi |
| `pageindex-mcp` | `openshift/pageindex-mcp/deployment.yaml` | `...service.yaml` | `...route.yaml` | **1** | 128Mi–512Mi |

Other manifests:
- `openshift/cronjob.yaml` — `daily-ai-news` CronJob, schedule `0 10 * * *`, triggers `daily-news-api`.
- `openshift/hpa.yaml` — HPA for `daily-news-api`: min 2, max 4 replicas.
- `openshift/pdb.yaml` — PodDisruptionBudget: `minAvailable: 1` for `daily-news-api`.
- `openshift/rbac.yaml` — ServiceAccount + RoleBinding for the CronJob to create Jobs.
- `openshift/networkpolicy.yaml` — allow intra-namespace traffic; deny external ingress except Routes.
- `openshift/namespace.yaml` — `aifeeders` namespace.
- `openshift/configmap.yaml` — `daily-news-config` ConfigMap.
- `openshift/secrets.yaml` — `daily-news-secrets` Secret (template only — never commit real values).

### ConfigMap vs Secret split

**ConfigMap `daily-news-config`** (non-sensitive, safe to version-control):

| Key | Example |
|---|---|
| `PUBLISHING_ENABLED` | `"true"` |
| `JEV_ENABLED` | `"true"` |
| `LLM_BASE_URL` | `"https://your-llm-endpoint/v1"` |
| `JEV_BASE_URL` | `"https://your-jev-gateway"` |
| `GNEWS_API_BASE_URL` | `"https://gnews.io/api/v4"` |
| `EVAL_FACTUALITY_THRESHOLD` | `"0.50"` |
| `EVAL_GROUNDEDNESS_THRESHOLD` | `"0.50"` |
| `EVAL_HALLUCINATION_THRESHOLD` | `"0.85"` |
| `LOG_LEVEL` | `"INFO"` |
| `LINKEDIN_COMMENT_DELAY_SECONDS` | `"3"` |

**Secret `daily-news-secrets`** (sensitive — never version-control real values):

| Key | Description |
|---|---|
| `LLM_API_KEY` | LLM bearer token |
| `GNEWS_API_KEY` | GNews primary key |
| `GNEWS_API_KEY_2` | GNews secondary key (auto-rotation) |
| `JEV_API_KEY` | Jev gateway bearer token |
| `LINKEDIN_CLIENT_ID` | LinkedIn app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn app secret |
| `LANGFUSE_SECRET_KEY` | Langfuse trace key (optional) |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (optional) |

---

## 10. EKS Deployment

### What changes vs OpenShift

| Aspect | OpenShift | EKS |
|---|---|---|
| Image build | `oc start-build` (S2I in-cluster) | `docker build` local + push to ECR |
| Image registry | `image-registry.openshift-image-registry.svc:5000/aifeeders/<svc>:latest` | `<account>.dkr.ecr.<region>.amazonaws.com/aifeeders/<svc>:latest` |
| Secrets | `oc create secret generic` | External Secrets Operator + AWS Secrets Manager |
| Routes / Ingress | `oc expose svc` → OpenShift Route | AWS ALB Ingress Controller or `kubectl port-forward` |
| RBAC | Same | Same |
| ConfigMap | Same | Same |
| Deployments / Services / CronJob / HPA / PDB / NetworkPolicy | **Identical** | **Identical** |

The only required YAML edit is the `image:` field in each Deployment — swap the registry prefix.

### EKS Build Flow

```bash
# ── Setup (once) ─────────────────────────────────────────────────────────────
export AWS_ACCOUNT=123456789012
export AWS_REGION=us-east-1
export ECR_REGISTRY=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Create ECR repositories.
for svc in daily-news linkedin-mcp news-mcp evaluation-mcp pageindex-mcp; do
  aws ecr create-repository --repository-name aifeeders/$svc --region $AWS_REGION
done

# ── Per-change build cycle ────────────────────────────────────────────────────
# 1. Test (same as OpenShift).
python -m pytest tests/unit tests/workflow -q --tb=short

# 2. Build (rsync first — same reason as OpenShift: exclude .venv/).
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' \
  . "$TMPDIR/"

docker build -t aifeeders/daily-news:latest -f Dockerfile "$TMPDIR"

# 3. Push.
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY
docker tag aifeeders/daily-news:latest $ECR_REGISTRY/aifeeders/daily-news:latest
docker push $ECR_REGISTRY/aifeeders/daily-news:latest

# 4. Update image reference in deployment.
kubectl set image deployment/daily-news-api \
  daily-news=$ECR_REGISTRY/aifeeders/daily-news:latest \
  -n aifeeders

# 5. Rollout.
kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders --timeout=60s

# 6. Run.
kubectl create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders
kubectl logs -f job/live-run-<id> -n aifeeders | grep -E "status=|published"
```

### EKS — Secrets via External Secrets Operator

```yaml
# SecretStore (once per cluster)
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: aws-secrets-manager
  namespace: aifeeders
spec:
  provider:
    aws:
      service: SecretsManager
      region: us-east-1
      auth:
        jwt:
          serviceAccountRef:
            name: aifeeders-sa   # IRSA-annotated SA

---
# ExternalSecret (syncs AWS Secret → Kubernetes Secret)
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: daily-news-secrets
  namespace: aifeeders
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
  target:
    name: daily-news-secrets     # creates this Kubernetes Secret
  data:
    - secretKey: LLM_API_KEY
      remoteRef:
        key: aifeeders/prod
        property: LLM_API_KEY
    # ... repeat for all secrets
```

### EKS — PublishedStore persistence

For production EKS, mount a PVC or use EFS so the store survives pod restarts:

```yaml
# StorageClass (EFS)
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-sc
provisioner: efs.csi.aws.com
---
# PVC
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: aifeeders-store
  namespace: aifeeders
spec:
  accessModes: [ReadWriteMany]
  storageClassName: efs-sc
  resources:
    requests:
      storage: 1Mi    # store is < 2 KB but PVC minimum is 1Mi
---
# In Deployment env + volumeMounts
env:
  - name: AIFEEDERS_STORE_PATH
    value: /data/aifeeders_published.json
volumeMounts:
  - name: store-pvc
    mountPath: /data
volumes:
  - name: store-pvc
    persistentVolumeClaim:
      claimName: aifeeders-store
```

---

## 11. Memory / State Management

### Within a single run

Memory in AIFeeders follows two distinct models:

**Model A — LangGraph state (pass-by-value, immutable transitions)**

```
NewsWorkflowState is a TypedDict passed through every node.
Each node returns a NEW dict (via {**state, "key": new_value}).
LangGraph tracks the full state at each node boundary.
No shared mutable state between nodes within a run.

Key state transitions:
  discover_news   → adds raw_articles
  deduplicate     → adds deduplicated_articles
  fetch_articles  → adds selected_articles (≤30 enriched)
  index_pageindex → adds pageindex_documents
  jev_prefilter   → replaces selected_articles (top 2)
                    adds jev_prefilter_scores (dict[article_id→scores])
                    adds jev_persona_hints
  summarize       → adds summaries
  jev_router      → adds jev_active_personas
  generate_personas → adds persona_outputs
  evaluate        → adds evaluation_results, increments retry_count
  publish         → adds linkedin_results
```

**Model B — singleton services (process-level state)**

```
published_store     Thread-safe file-backed store.
                    Lazy-loaded from disk on first use.
                    Shared across all LangGraph runs in the same process (daily-news-api pod).
                    Survives multiple CronJob-triggered runs within one pod lifetime.

LinkedInMCPClient   Stateless — creates a new httpx session per call.
JevClient           Stateless — creates a new httpx.AsyncClient per call.
NewsMCPClient       Stateless.
PageIndexMCPClient  Stateless.
```

### Across runs (persistent memory)

AIFeeders has two forms of cross-run memory:

**1. PublishedStore** — "What did we publish?"

```
/tmp/aifeeders_published.json
{"news-abc123:2026-09-24": "2026-09-24T10:31:22Z",
 "news-def456:2026-09-24": "2026-09-24T10:31:45Z",
 "news-ghi789:2026-09-23": "2026-09-23T10:29:11Z"}

Purpose: prevent republishing the same article across CronJob runs.
TTL: 7 days → 14 entries max → < 2 KB.
```

**2. LinkedIn audit log** — "What URNs did we create?"

```
linkedin-mcp maintains an in-memory audit dict:
  {publication_key → {post_urn, created_at, python_len, linkedin_len}}

GET http://linkedin-mcp:8000/audit → returns the full dict.
GET http://linkedin-mcp:8000/audit/{key} → returns one entry.

Purpose: record every LinkedIn post URN for potential deletion.
         (delete via `linkedin_delete_post` tool if a post needs removal)
Persistence: in-memory only — lost on pod restart.
             (URNs can be recovered from LinkedIn API if needed)
```

**3. Langfuse traces** — "What decisions were made?"

```
Each run writes spans to Langfuse (if LANGFUSE_SECRET_KEY is set):
  Session ID = run_id
  Spans: discover_news → evaluate → publish
  Each span records: input, output, model, tokens, decision, scores

Purpose: debugging + long-term quality tracking.
Persistence: cloud — indefinite retention at https://us.cloud.langfuse.com.
```

**In-run Jev signal memory:**

```
jev_persona_hints     from jev_prefilter (#1 article raw text analysis)
jev_active_personas   from jev_router (#1 article summary analysis)
                      merged as union: signals from both passes preserved
jev_prefilter_scores  per-article dict — article #2 gets its own scores,
                      not article #1's scores (fixed in build #61)

These are ephemeral — LangGraph state, not persisted to disk.
They are consumed within the same run by generate_personas and publisher_agent.
```

---

## 12. Observability

### Langfuse tracing

Every meaningful operation is wrapped in a Langfuse trace/span:
- `discover_news` span — input: query count; output: article count.
- `evaluate` span — output: decision, factuality, groundedness, hallucination, policy_check.
- `publish` span — output: publication_key, post_urn, post_status, comments_posted/failed.

All spans share the `run_id` as session identifier. Traces are flushed at the end of `publish()` via `flush_langfuse()`.

### Structured logging

Every log line includes `[run_id]` and the relevant context:

```
[RUN-A3F91C2B4E6D] jev_prefilter: #1 article_id=news-abc123 relevance=0.92 engagement=0.78 composite=0.864 personas=['policy','business']
[RUN-A3F91C2B4E6D] post composed article=news-abc123 python_len=2847 linkedin_utf16_len=2873
[RUN-A3F91C2B4E6D] POST TEXT START ---
🤖  AI REGULATION  ·  Powered by Jev
...
--- POST TEXT END
[RUN-A3F91C2B4E6D] post published post_urn=urn:li:share:7508479385549697024 status=published
[RUN-A3F91C2B4E6D] Workflow complete — status=PUBLISHED published=2 errors=0
```

### LinkedIn audit

```bash
oc exec deployment/linkedin-mcp -n aifeeders -- \
  curl -s http://localhost:8000/audit | jq .
```

Returns all posts published since the current pod started — URN, key, timestamp, char counts.

### PublishedStore inspection

```bash
oc exec deployment/daily-news-api -n aifeeders -- \
  cat /tmp/aifeeders_published.json | python3 -m json.tool
```

---

## 13. Security

| Area | Mechanism |
|---|---|
| Secrets | Kubernetes Secret `daily-news-secrets` → env vars; never in ConfigMap or code |
| LinkedIn token | In-memory only in `linkedin-mcp`; never written to disk or logged |
| LLM API key | Secret env var; never logged |
| Jev API key | Secret env var; never logged |
| Network | `NetworkPolicy` restricts inter-pod traffic to namespace only |
| PII gate | Jev `pii_detected > 0.5` → hard BLOCK before any publish attempt |
| Injection gate | Jev `prompt_injection_detected > 0.5` → hard BLOCK |
| TLS | All external calls use HTTPS; Jev client: `verify=False` for internal IBM cert (intentional) |
| Build isolation | S2I builds run in isolated BuildPods; no host access |
| RBAC | CronJob ServiceAccount has minimum permissions: `create` Jobs only |

---

## 14. Change Log

| Build | Date | Changes |
|---|---|---|
| #62 | 2026-09 | Documentation redesign. Memory model fully documented. Build flow consolidated. EKS portability guide. `IMPACT_LINE_CAP` 88→120. hook_category for `event_type=other` → `AI NEWS`. |
| #61 | 2026-09 | `_linkedin_len()` UTF-16 counting fix. `POST_LIMIT`=2900. Per-article `jev_prefilter_scores` keyed by `article_id`. MCPHTTPClient nested error detection. `linkedin_delete_post` tool. `publication_key` includes body hash. |
| #60 | 2026-09 | Post composition rewrite: hook category, Jev decision block, footer guarantee. `AI_SEARCH_QUERIES` 6→9; `hours`=48→24; `limit`=20→10. |
| #59 | 2026-09 | Initial documentation. |
| #58 | 2026-09 | `evaluation_agent.py` `personas.labor` AttributeError fix. |
