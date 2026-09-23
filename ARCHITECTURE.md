# AIFeeders — Complete Architecture

> **Rearchitected with Jev System One.** Four personas. Deterministic publish gates. Zero-accumulation builds. EKS-portable by design. Current as of build #56.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [LangGraph Workflow — Complete State Machine](#2-langgraph-workflow--complete-state-machine)
3. [Jev System One — Why and How](#3-jev-system-one--why-and-how)
4. [PublishedStore — Deduplication and Memory](#4-publishedstore--deduplication-and-memory)
5. [Four-Persona Architecture](#5-four-persona-architecture)
6. [LinkedIn Post Format](#6-linkedin-post-format)
7. [MCP Microservices](#7-mcp-microservices)
8. [OpenShift Deployment Layout](#8-openshift-deployment-layout)
9. [Build Architecture — Zero Old-Build Accumulation](#9-build-architecture--zero-old-build-accumulation)
10. [EKS Portability — Simple and Easy Deployment](#10-eks-portability--simple-and-easy-deployment)
11. [Data Flow — End to End](#11-data-flow--end-to-end)
12. [Technology Stack](#12-technology-stack)
13. [File Layout](#13-file-layout)

---

## 1. System Overview

AIFeeders is an **agentic AI news pipeline** that runs once daily at 10:00 UTC. It discovers the most relevant AI news, scores and filters it via Jev System One (the AI decision engine), generates four audience-specific perspectives via LLM, evaluates them for factual quality, and publishes a single structured LinkedIn post — automatically, with no human pressing a button.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             AIFEEDERS SYSTEM                                     │
│                                                                                  │
│  ┌──────────┐    ┌─────────────────────────────────────────────────────────────┐ │
│  │ CronJob  │───►│               LangGraph Workflow (daily-news)               │ │
│  │10:00 UTC │    │                                                             │ │
│  └──────────┘    │  discover ─► dedup ─► fetch ─► prefilter ─► summarise      │ │
│                  │  ─► route ─► generate ─► evaluate ─► publish               │ │
│                  └──────────────────────┬────────────────────────────────────  │ │
│                                         │                                       │ │
│         ┌───────────────────────────────┼────────────────────────────┐         │ │
│         ▼                               ▼                             ▼         │ │
│  ┌─────────────┐             ┌───────────────────┐         ┌──────────────────┐ │ │
│  │  news-mcp   │             │  evaluation-mcp   │         │  linkedin-mcp    │ │ │
│  │  GNews API  │             │  LLM fallback     │         │  LinkedIn API    │ │ │
│  └─────────────┘             └───────────────────┘         └──────────────────┘ │ │
│                                                                                  │ │
│         ┌──────────────────────────────────────────────────────────────┐        │ │
│         │                  pageindex-mcp  (RAG context)                │        │ │
│         └──────────────────────────────────────────────────────────────┘        │ │
│                                                                                  │ │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │ │
│  │                         JEV GATEWAY  (external)                           │  │ │
│  │     POST /v1/systemone  ·  Bearer auth  ·  question types: choice/score/noul│ │ │
│  │     Model: Qwen/Qwen3.5-2B  ·  method: lora_decision_head                │  │ │
│  └───────────────────────────────────────────────────────────────────────────┘  │ │
└──────────────────────────────────────────────────────────────────────────────────┘ │
```

---

## 2. LangGraph Workflow — Complete State Machine

Every run is a single LangGraph state machine. State is a `TypedDict` passed forward through nodes immutably — each node returns `{**state, changed_keys}`. No shared mutable global state between nodes.

```
START
  └─► discover_news        6 GNews queries → ~18 raw articles
        └─► deduplicate    Pass 1: SHA-256 hash dedup (same article, two sources → one)
              │             Pass 2: PublishedStore.filter_unpublished() → skip published-today
              └─► fetch_articles      Full HTML fetch + parse (capped at 30)
                    └─► index_pageindex    Chunk + index in PageIndex for RAG retrieval
                          └─► jev_prefilter       ← Jev Decision #1
                                │  Scores all articles in parallel (semaphore=5)
                                │  Selects top 2 by composite score
                                └─► summarise             LLM reads article → structured summary
                                      └─► jev_route_personas  ← Jev Decision #2
                                            │  Picks relevant personas from summary
                                            └─► generate_personas  (active subset only, parallel)
                                                  └─► evaluate     ← Jev Decision #3
                                                        │  Factuality / groundedness / hallucination
                                                        ├─► REGENERATE ──► back to summarise (max 2 retries)
                                                        ├─► HUMAN_REVIEW ──► approval gate
                                                        └─► PASS
                                                              └─► publish   3 deterministic gates
                                                                    └─► END
```

### Node responsibilities

| Node | What it does | Cost | Fallback |
|---|---|---|---|
| `discover_news` | 6 GNews searches via news-mcp, ~18 articles | 6 API calls | Logs warnings, continues with 0 |
| `deduplicate` | SHA-256 hash dedup + PublishedStore cross-run filter | Disk read | Skip all if all published |
| `fetch_articles` | Full HTML fetch for each article (capped at 30) | 30 HTTP fetches | Skip unfetchable articles |
| `index_pageindex` | Chunk + index articles for RAG retrieval | In-process | Skip on error |
| `jev_prefilter` | Score every article via Jev (5 concurrent), pick top 2 | 1 Jev call/article | Fall back to `[:2]` |
| `summarise` | LLM reads article + PageIndex context → structured summary | 1 LLM call | Error propagated |
| `jev_route_personas` | Jev decides which of 4 personas are relevant per summary | 1 Jev call | Fall back to all 4 |
| `generate_personas` | Parallel LLM calls for active personas only | 1–4 LLM calls | Stub empty persona |
| `evaluate` | Jev primary / EvaluationMCP fallback — quality scoring | 1 Jev call | PASS on error |
| `publish` | 3-gate dedup → compose post → LinkedIn post + comments | 1–5 LinkedIn calls | Log error, continue |

### Workflow State (`NewsWorkflowState`)

```python
class NewsWorkflowState(TypedDict):
    run_id: str                        # "RUN-{UUID8}" — unique per execution, Langfuse seed

    # News discovery
    raw_articles: list[dict]           # All articles from GNews (up to ~18)
    deduplicated_articles: list[dict]  # After hash dedup + PublishedStore filter
    selected_articles: list[dict]      # Top 2 chosen by Jev prefilter

    # PageIndex (RAG)
    pageindex_documents: list[dict]    # Indexed document handles

    # Jev decision signals — flow forward through the pipeline
    jev_persona_hints: list[str]       # Prefilter → router: warm persona suggestions from article text
    jev_active_personas: list[str]     # Router → generate_personas: confirmed active persona values
    jev_prefilter_scores: dict         # Prefilter → publisher: scores for #1 article (rendered in post)

    # Generation
    summaries: list[dict]              # NewsSummary per selected article
    persona_outputs: list[dict]        # PersonaSetOutput per article (4 personas each)

    # Evaluation
    evaluation_results: list[dict]     # EvaluationResult per article
    retry_count: int                   # Incremented on REGENERATE decision

    # Publishing
    approval_status: str               # "PENDING" | "APPROVED" | "REJECTED"
    linkedin_results: list[dict]       # Final publish audit records

    # Run metadata
    workflow_status: str               # STARTED → DISCOVERED → DEDUPLICATED → … → PUBLISHED
    errors: list[str]                  # Non-fatal errors collected per node
```

---

## 3. Jev System One — Why and How

### The Core Problem It Solves

Before Jev, AIFeeders made these decisions blindly:
- **Article selection**: picked the first 2 articles from GNews in whatever order they arrived
- **Persona routing**: always called all LLM personas regardless of relevance (wasted cost)
- **Content evaluation**: called an LLM to evaluate an LLM — expensive, hallucination risk in the evaluation itself

Jev System One is a **lightweight probabilistic AI decision model** built on a fine-tuned Qwen/Qwen3.5-2B with a lora_decision_head. It answers structured multi-question queries in a single HTTP call, returning calibrated float scores and named values — no free text generation. This makes it:
- **Fast**: < 2 seconds per call (vs. 10–30s for a full LLM generation)
- **Deterministic-enough**: threshold comparisons on calibrated floats (0–1 range)
- **Zero hallucination risk in decisions**: returns scores and named labels, not generated text
- **Cheap**: uses far fewer tokens than a reasoning LLM

### Gateway Contract

```
Health (no auth):
  GET  /health
  → {"status": "ready", "model": "Qwen/Qwen3.5-2B", "method": "lora_decision_head"}

Inference:
  POST /v1/systemone
  Authorization: Bearer <JEV_API_KEY>
  Content-Type: application/json

  Request body:
  {
    "state": "<unstructured text context for the model>",
    "questions": {
      "<key>": {
        "type": "noul|choice|score",
        "instructions": "...",
        "criteria": { ... }  // for choice/score
      }
    }
  }

  Response:
  {
    "answers": {
      "<key>": { "type": "...", "noul": 0.87, "choice": "label", "score": 2.3,
                 "probabilities": {...}, "confidence": 0.93 }
    },
    "model": "Qwen/Qwen3.5-2B",
    "usage": {"input_tokens": 165, "output_tokens": 0}
  }
```

### Question Types

| Type | What it returns | Use cases in AIFeeders |
|---|---|---|
| `noul` | Float 0.0–1.0 (>0.5 = yes) | `is_ai_topic`, `relevance_score`, `persona_fit_*`, `estimated_engagement`, `pii_detected` |
| `choice` | Named label from a provided dict | `event_type` (product_launch/funding/…), `controversy_level` (low/medium/high), `skip_reason`, `policy_check` (PASS/REVIEW/FAIL) |
| `score` | Float 0..N-1 (normalised ÷ 4 → 0–1) | `significance` (0–4), `factuality`, `groundedness`, `hallucination`, `toxicity` |

---

### Jev Integration Point 1 — `jev_prefilter_articles` (Article Selection)

**When**: After `index_pageindex`, before `summarise`. Called once per run.

**Why**: Replaces blind `articles[:2]` slicing. Instead of picking articles by arrival order, Jev scores every article on 11 dimensions in a single batch call.

**Mechanism**:
1. All fetched articles (up to 30) are sent to Jev with `asyncio.Semaphore(5)` — at most 5 concurrent calls
2. Each article gets one `POST /v1/systemone` call with these questions:

```
is_ai_topic           noul   "Is this primarily about AI?" → hard gate (must be > 0.5 to proceed)
relevance_score       noul   "How relevant to AI professionals?" → 0–1
event_type            choice "product_launch|funding|regulation|research|acquisition|other"
significance          score  "0–4 scale: minor → landmark"
controversy_level     choice "low|medium|high"
persona_fit_business  noul   "Relevant to business execs focused on ROI?"
persona_fit_policy    noul   "Relevant to AI policy makers?"
persona_fit_genz      noul   "Relevant to generalist readers?"
persona_fit_linkedin  noul   "Relevant to tech practitioners?"
estimated_engagement  noul   "Likely to drive LinkedIn engagement?"
skip_reason           choice "not_ai|low_quality|none"
```

3. Composite score: `relevance × 0.6 + engagement × 0.4`
4. Top 2 articles by composite score proceed. Non-AI articles (`is_ai_topic < 0.5`) are skipped.
5. `persona_fit_*` scores > 0.5 are stored as `jev_persona_hints` in state (warm signal for the router)
6. The #1 article's full score set is stored in `jev_prefilter_scores` → rendered in the `⚙️ Jev Decision` block of the published post

**Fallback**: If Jev is disabled (`JEV_ENABLED=false`) or any call fails, the node falls back to `articles[:2]` without error.

**State output**:
```python
state["selected_articles"]    = [top_article_1, top_article_2]
state["jev_persona_hints"]    = ["business", "policy"]         # from article-level scoring
state["jev_prefilter_scores"] = {
    "event_type": "research",
    "relevance_score": 0.87,
    "significance": 0.75,
    "estimated_engagement": 0.62,
    "controversy_level": "medium",
    "active_personas": ["business", "policy", "genz", "linkedin"],
    "persona_scores": {"business": 0.91, "policy": 0.83, ...}
}
```

---

### Jev Integration Point 2 — `jev_route_personas` (Persona Routing)

**When**: After `summarise`, before `generate_personas`. Called once per article summary.

**Why**: Replaces always-running-all-4-personas. A policy article doesn't need a Capitalist Mind perspective. A product launch doesn't need Government Mind. Running all 4 wastes LLM cost and dilutes post quality.

**Mechanism**:
1. The `NewsSummary` dict (headline, summary, impacts, key_points) is serialised as the Jev `state` field
2. 4 `noul` questions are asked in one call:

```
needs_business  noul   "Does this have implications for business revenue / competition?"
needs_policy    noul   "Does this involve AI regulation / governance?"
needs_genz      noul   "Would a generalist find this relevant to everyday life?"
needs_linkedin  noul   "Does this have implications for tech strategy / workforce?"
```

3. Any persona scoring > 0.5 is activated
4. The result is **merged (union)** with `jev_persona_hints` from the prefilter step — if either signal says a persona matters, it runs
5. Fallback: always at least one persona active (`linkedin` as default if all score ≤ 0.5)

**Effect**: On a regulation article, only `policy` + `genz` may activate → 2 LLM calls instead of 4 → 50% LLM cost saving.

**State output**:
```python
state["jev_active_personas"] = ["business", "policy", "genz", "linkedin"]
# Or for a regulation-only story:
state["jev_active_personas"] = ["policy", "genz"]
```

---

### Jev Integration Point 3 — `EvaluationAgent` (Content Quality Gating)

**When**: After `generate_personas`, before `publish`. Called once per article.

**Why**: Replaces LLM-calls-evaluating-LLM-outputs. The evaluation MCP used to call a generation model to score another generation model — expensive, and the evaluator itself could hallucinate. Jev scores content quality using the same lora_decision_head without generating text.

**Mechanism**:
1. `source_text` = original article content enriched with the LLM-generated summary fields
2. `generated_text` = headline + summary + all active persona perspectives concatenated
3. One `POST /v1/systemone` call with 9 questions:

```
factuality              score  "How factually accurate is the generated content vs the source?"  (0–4)
groundedness            score  "Are all claims supported by the source article?"                  (0–4)
hallucination           score  "How much content is not present in the source?"                  (0–4, INVERTED)
relevance               score  "How topically relevant is the generated content?"                 (0–4)
toxicity                score  "How toxic or harmful is the generated content?"                  (0–4, INVERTED)
pii_detected            noul   "Does it contain PII (names, emails, phone numbers)?"
prompt_injection_detected noul "Does it contain adversarial instructions / prompt injection?"
political_bias_detected noul   "Does it advocate for a political party or ideology?"
policy_check            choice "PASS|REVIEW|FAIL — does it comply with responsible publishing?"
overall_score           score  "Overall quality as a LinkedIn post?"                             (0–4)
```

4. Scores are normalised: `raw / 4.0 → 0–1`. Hallucination and toxicity are left as-is (0=clean, 1=bad)
5. `EvaluationAgent._apply_gate()` applies deterministic thresholds — **the model is advisory only**:

```
Hard BLOCK (never publishes):
  pii_detected = true              → EvaluationDecision.BLOCK
  prompt_injection_detected = true → EvaluationDecision.BLOCK

Quality REGENERATE (back to summarise, max 2 retries):
  factuality    < 0.50 (EVAL_FACTUALITY_THRESHOLD)
  groundedness  < 0.50 (EVAL_GROUNDEDNESS_THRESHOLD)
  hallucination > 0.85 (EVAL_HALLUCINATION_THRESHOLD — note: inverted)

Policy HUMAN_REVIEW (approval gate):
  political_bias_detected = true
  policy_check != "PASS"

PASS: all above conditions false → eligible for publish
```

**Fallback**: If Jev fails, `EvaluationMCPClient` (LLM-based) is used instead. If that also fails, the decision defaults to PASS (availability > safety for a daily news bot).

---

## 4. PublishedStore — Deduplication and Memory

### The Problem

The CronJob runs once daily at 10:00 UTC, but it can also be:
- **Retried automatically** on pod failure
- **Triggered manually** by an operator
- **Run twice** if two operators trigger it simultaneously

Without persistent deduplication, any of these scenarios would republish the same article on the same day, spamming LinkedIn followers.

### Design

```
File:        /tmp/aifeeders_published.json   (env: AIFEEDERS_STORE_PATH)
Key format:  "{article_id}:{YYYY-MM-DD}"     — one entry per article per calendar day
Value:       ISO-8601 timestamp of publish   — e.g. "2025-07-15T10:04:37.123456+00:00"
TTL:         7 days (env: AIFEEDERS_STORE_TTL_DAYS, default: 7)
Thread safety: threading.Lock — safe for a single process with multiple async tasks
Memory:      In-memory dict cache, loaded once per process lifetime, flushed after each write
```

### Three Deduplication Gates — In Order

```
Gate 1 — graph node: deduplicate
────────────────────────────────
Called: Before any LLM work begins
Method: PublishedStore.filter_unpublished(articles)
Effect: Removes all articles published today from the candidate list
Purpose: Cost avoidance — never summarise or generate personas for an article
         that was already published today
Risk:    None — if store is corrupt/missing, all articles pass through (safe default)

Gate 2 — publisher agent: pre-publish check
────────────────────────────────────────────
Called: Inside PublisherAgent.publish(), right before the LinkedIn API call
Method: PublishedStore.is_published(article_id)
Effect: Skips publishing with reason "already_published_today"
Purpose: Catches race conditions — if two workflow runs reach publish simultaneously,
         the second one will be blocked at this gate even if Gate 1 let it through
Risk:    None — skipping is safe

Gate 3 — LinkedIn MCP: idempotency key
────────────────────────────────────────
Called: By LinkedInMCPClient.create_post(), passed as publication_key
Key:    "{article_id}:{YYYY-MM-DD}:{headline_hash[:12]}"
        Note: NO run_id in the key — it is stable across all retries on the same day
Effect: LinkedIn MCP rejects duplicate posts with the same idempotency key
Purpose: Defence-in-depth — even if both Gate 1 and Gate 2 fail (e.g., pod restart
         between Gate 2 check and mark_published call), LinkedIn MCP itself blocks
         the duplicate
```

### Store Lifecycle per Workflow Run

```
Pod starts (fresh or restarted)
  │
  ▼
Process starts — PublishedStore._loaded = False, _cache = {}
  │
  ▼
First call to any Store method triggers _ensure_loaded()
  ├─► Read /tmp/aifeeders_published.json
  ├─► Purge entries older than 7 days (auto-TTL)
  └─► Load into _cache dict
  │
  ▼
deduplicate node calls filter_unpublished()
  └─► Checks each article_id against _cache
      Returns only articles NOT in cache (unpublished today)
  │
  ▼
[LLM work — summarise, route, generate, evaluate]
  │
  ▼
publish node calls is_published() — Gate 2 re-check
  │
  ▼
LinkedInMCPClient.create_post() succeeds
  │
  ▼
published_store.mark_published(article_id)
  ├─► Writes "{article_id}:{today}" → ISO timestamp to _cache
  └─► Flushes _cache to /tmp/aifeeders_published.json atomically
  │
  ▼
Any subsequent retry today:
  deduplicate → filter_unpublished() → article_id found in cache → skipped
```

### Memory Usage

The store is a lazy-loaded, write-through in-memory cache backed by a JSON file.

- **RAM**: The cache dict holds at most `7 days × 2 articles/day = 14 entries`. Each entry is ~60 bytes. Total in-memory footprint: **< 1 KB**.
- **Disk**: The JSON file contains at most 14 entries after auto-purge. File size: **< 2 KB**.
- **Load cost**: One file read per process lifetime (double-checked locking pattern).
- **Write cost**: One file write after each successful `mark_published()` call (typically 1–2 per day).

### Failure Safety

If the store file cannot be read (permissions error, disk full, corrupt JSON):
- The error is **logged at WARNING level** but NOT raised
- The cache starts empty → all articles are treated as unpublished
- Publishing proceeds normally
- Worst case: a duplicate post. Acceptable for a daily news bot — availability beats deduplication.

---

## 5. Four-Persona Architecture

Personas were consolidated from 5 to 4 by merging "Working Professional Mind" and "Tech Strategist Mind" into a unified "Tech & Workforce Mind". This reduces LLM cost, reduces post length, and produces a richer combined perspective.

| Key | Display name | Emoji | Voice | Focus |
|---|---|---|---|---|
| `business` | Capitalist Mind | 💼 | Founder/operator. P&L lens. Ends with a pointed decision question. | ROI, margins, competitive moat, build-vs-buy |
| `policy` | Government Mind | 🏛️ | Policy memo precision. Names specific regulations. | Compliance, cross-jurisdiction governance, enforcement |
| `genz` | Generalist Mind | 🎓 | Plain English. For people outside the AI bubble. | Everyday impact, what it means for non-specialists |
| `linkedin` | Tech & Workforce Mind | 🧠 | Dual lens: architecture decisions + career reality. | Tech strategy, engineering trade-offs, workforce/skills |

Each persona always produces: **3 sentences of perspective + 2 evidence bullets** from the article.

Prompt files: `prompts/capitalist.txt`, `prompts/policy.txt`, `prompts/genz.txt`, `prompts/linkedin.txt`.
Voice guide: `skills.md`.

### Lenient Parser (`_PersonaOutputRaw`)

The LLM sometimes outputs the display name (`"generalist"`, `"tech & workforce"`) instead of the internal enum key (`"genz"`, `"linkedin"`). The lenient parser avoids Pydantic `ValidationError` by accepting `persona: str` and injecting the correct `PersonaType` from agent context after parsing.

### Persona Routing Logic (Jev-controlled)

```
Article: "EU AI Act enforcement begins Q1 2027"
  jev_route_personas:
    needs_policy    → 0.93  ✓ activate policy
    needs_genz      → 0.71  ✓ activate genz
    needs_linkedin  → 0.55  ✓ activate linkedin
    needs_business  → 0.34  ✗ skip business

  Result: 3 LLM calls instead of 4 (25% savings)
  Merged with prefilter hints (union): ["policy", "genz", "linkedin"]
```

---

## 6. LinkedIn Post Format

The post is composed **deterministically** by `PublisherAgent._compose_main_post()`. No LLM is involved in post composition — it is pure string assembly from structured data with a running character budget tracker.

**Hard limit**: 3000 characters (LinkedIn API). Budget tracked with `remaining` counter, pre-reserving 440 chars for footer.

```
🤖  AI NEWS  ·  Agentic AI

<headline>

<summary — full LLM text, no cap>

⚙️  Jev Decision
  💼  Primary audience : Business (market strategy & ROI)  — 93% Jev score
  📋  Story type       : Research Report
  🎯  AI relevance     : 91%   Market signal: [████░]
  ⚡  Engagement est.  : 74%   Controversy: Medium
  👥  Audience impact  : 💼 Business 93%  ›  🧠 Tech+Workforce 89%  ›  🎓 Generalist 76%
  🔥  AI market shift  : HIGH / MODERATE / INFORMATIONAL

📌  What you need to know
  • <key point 1 — hard-capped at 90 chars>
  • <key point 2>
  • <key point 3>

📈  Business   —  <business_impact — 90 chars max>
👷  Workforce  —  <job_impact — 90 chars max>
🔬  Tech       —  <technology_impact — 90 chars max>
🏛️  Policy     —  <policy_impact — 90 chars max, only if present>

🔗  Read more: <source_url>

🧵  Perspectives  ·  Jev-selected audience mindsets

💼  Capitalist Mind
<perspective — clipped to per_persona budget at sentence boundary>
  • <evidence bullet 1 — always built first, never clipped>
  • <evidence bullet 2>

🏛️  Government Mind     [same structure, only if Jev activated]
🎓  Generalist Mind     [same structure, only if Jev activated]
🧠  Tech & Workforce    [same structure, only if Jev activated]

⚙️  Jev Decision  —  💼 Business Strategists  ›  🧠 Tech & Workforce  ›  🎓 Generalists

💬  Real Human Take — drop yours below. 👇

⚠️ AI-simulated perspectives — not verified opinions or professional advice.
🤖 AIFeeders  ·  Agentic AI  ·  Powered by Jev

#AI #AgenticAI #Jev #AINews #GenerativeAI #TechNews
#MachineLearning #AIStrategy #AIInnovation #DigitalTransformation #AILeadership
```

### Budget Allocation

| Section | Strategy |
|---|---|
| Hook + headline | Variable (~100 chars) |
| Summary | Full LLM text |
| Jev Decision block | ~350 chars fixed |
| Key points | Hard-capped 90 chars each |
| Impact lines | Hard-capped 90 chars each |
| Source | Fixed |
| Footer (verdict + CTA + disclaimer + hashtags) | **Reserved 440 chars upfront** |
| Persona perspectives | `(remaining - header_cost) ÷ n_active_personas` each, min 180 |
| Evidence bullets | **Always 2 per persona** — built before perspective clip, never truncated |

---

## 7. MCP Microservices

Each concern is isolated in its own FastAPI service. The main workflow talks to them over HTTP. They are independently deployable, independently scalable, and independently testable.

| Service | Port | Purpose | Upstream | Replicas |
|---|---|---|---|---|
| `daily-news-api` | 8000 | FastAPI trigger, health, metrics | LangGraph workflow | 2 (HPA 2–4) |
| `news-mcp` | 8000 | Fetch and parse AI news via GNews | GNews API | 2 |
| `pageindex-mcp` | 8000 | Full HTML fetch, chunk, in-memory index for RAG | None | 1 (in-memory) |
| `evaluation-mcp` | 8000 | LLM-based content quality scoring (Jev fallback only) | OpenAI-compatible LLM | 2 |
| `linkedin-mcp` | 8000 | Create LinkedIn posts and comments via OAuth | LinkedIn Marketing API | **1** (stateful OAuth token) |
| `jev-gateway` | — | Jev System One decision engine (external, IBM) | — | — |

All MCP servers expose:
- `GET /health` — liveness check, returns `{"status": "healthy"}`
- `POST /call` — MCP tool invocation: `{ "tool": "...", "arguments": {...} }`

---

## 8. OpenShift Deployment Layout

```
Namespace: aifeeders
│
├── Deployments (always-on)
│   ├── daily-news-api        2 replicas, HPA 2–4
│   │                         Runs: FastAPI + LangGraph workflow
│   ├── news-mcp              2 replicas
│   │                         Runs: GNews adapter
│   ├── evaluation-mcp        2 replicas
│   │                         Runs: LLM evaluation fallback
│   ├── linkedin-mcp          1 replica — stateful (OAuth token in memory)
│   │                         Runs: LinkedIn API adapter
│   └── pageindex-mcp         1 replica — stateful (in-memory article index)
│                             Runs: RAG index server
│
├── CronJob: daily-ai-news
│   └── Schedule: 0 10 * * *  (10:00 UTC daily)
│       Triggers: POST /workflow/daily-news on daily-news-api
│
├── BuildConfigs (binary source builds)
│   ├── daily-news            successfulBuildsHistoryLimit: 1
│   ├── news-mcp              successfulBuildsHistoryLimit: 1
│   ├── evaluation-mcp        successfulBuildsHistoryLimit: 1
│   ├── linkedin-mcp          successfulBuildsHistoryLimit: 1
│   └── pageindex-mcp         successfulBuildsHistoryLimit: 1
│
├── ImageStreams (one per service, :latest tag only)
│
├── ConfigMap: aifeeders-config
│   └── Non-secret settings: URLs, flags, thresholds
│
└── Secret: aifeeders-secrets
    └── API keys: GNEWS_API_KEY, LLM_API_KEY, JEV_API_KEY, LINKEDIN_*, LANGFUSE_*
```

### Network Policy

Only `daily-news-api` has egress to external endpoints (Jev gateway, GNews, LLM, LinkedIn). MCP services are cluster-internal only. `linkedin-mcp` has egress to LinkedIn API endpoints.

---

## 9. Build Architecture — Zero Old-Build Accumulation

### The Core Design Principle

**Old builds are deleted automatically. No manual cleanup. Ever.**

All `BuildConfig` resources are patched with:
```yaml
spec:
  successfulBuildsHistoryLimit: 1
  failedBuildsHistoryLimit: 1
```

After each new successful build, OpenShift deletes the previous build object and its pod automatically. The namespace always has exactly one build per service — the latest one.

### How Binary Builds Work

```
Developer laptop
  │
  ├─ python -m pytest tests/  ← must pass before building
  │
  ├─ rsync -a (exclude: .venv/ __pycache__/ .git/ .env dist/ build/)
  │       └─► clean tmpdir  (< 1 MB uploaded — not 270 MB)
  │
  └─ oc start-build daily-news --from-dir=$TMPDIR
          │
          └─► OpenShift BuildConfig
                  │
                  └─► BuildPod (python:3.11-ubi9 base image)
                          │
                          ├─ COPY src/ prompts/ pyproject.toml README.md
                          ├─ pip install -e . (layer-cached — runs in ~15s warm)
                          └─► push to ImageStream :latest
                                  │
                                  └─► Deployment rollout trigger
                                          │
                                          └─► New pods pull :latest → old pods terminate
```

### Why `.venv/` Must Always Be Excluded

The `.venv/` directory is **269 MB**. Including it in the build context upload causes a timeout before the build even starts. The `pip install` inside the BuildPod is layer-cached by OpenShift — it runs in 10–15 seconds on a warm cache. Never include `.venv/` in build context.

### Build Command (Canonical)

```bash
TMPDIR=$(mktemp -d) && \
rsync -a \
  --exclude='.venv/' \
  --exclude='**/__pycache__/' \
  --exclude='**/*.pyc' \
  --exclude='.git/' \
  --exclude='.pytest_cache/' \
  --exclude='*.egg-info/' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='htmlcov/' \
  --exclude='.coverage' \
  --exclude='.tox/' \
  --exclude='.DS_Store' \
  . "$TMPDIR/" && \
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --output=name
```

---

## 10. EKS Portability — Simple and Easy Deployment

The entire platform is designed so that moving from OpenShift to EKS requires **changing only the image build and registry** steps. Everything else is standard Kubernetes.

### What is Identical on EKS

| Component | Status |
|---|---|
| All Deployment YAML files | ✅ Identical — copy-paste |
| All Service YAML files | ✅ Identical |
| CronJob YAML | ✅ Identical (same schedule, same trigger) |
| ConfigMap YAML | ✅ Identical |
| RBAC (Role/RoleBinding) YAML | ✅ Identical |
| NetworkPolicy YAML | ✅ Identical (requires Calico or Cilium CNI) |
| Application code | ✅ Identical — no OpenShift SDK calls |
| Environment variables | ✅ Identical — all injected from ConfigMap/Secret |

### What Changes for EKS

| Component | OpenShift | EKS |
|---|---|---|
| Image build | `BuildConfig` + `oc start-build` | `docker build` + `docker push` |
| Image registry | Internal `image-registry.openshift-image-registry.svc` | `<account>.dkr.ecr.<region>.amazonaws.com` |
| Image pull auth | Auto from ImageStream | `imagePullSecrets` referencing ECR credentials |
| `image:` field in Deployment YAML | `image-registry.openshift-image-registry.svc/aifeeders/daily-news:latest` | `<account>.dkr.ecr.us-east-1.amazonaws.com/aifeeders/daily-news:latest` |
| Secrets management | Kubernetes Secret (or OpenShift vault) | External Secrets Operator + AWS Secrets Manager (recommended) |
| LinkedIn OAuth port-forward | `oc port-forward` | `kubectl port-forward` (identical syntax) |

### EKS Build Flow

```bash
# Step 1: Run tests (never skip this)
python -m pytest tests/unit tests/workflow -q --tb=short

# Step 2: Set registry variables
export AWS_ACCOUNT=123456789012
export AWS_REGION=us-east-1
export ECR_REGISTRY=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
export IMAGE_NAME=aifeeders/daily-news

# Step 3: Build image (same Dockerfile as OpenShift)
docker build -t $IMAGE_NAME:latest .

# Step 4: Authenticate to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

# Step 5: Tag and push
docker tag $IMAGE_NAME:latest $ECR_REGISTRY/$IMAGE_NAME:latest
docker push $ECR_REGISTRY/$IMAGE_NAME:latest

# Step 6: Old image in ECR is NOT deleted automatically (unlike OpenShift's limit:1)
# Use a lifecycle policy on the ECR repository to keep only N images:
aws ecr put-lifecycle-policy \
  --repository-name aifeeders/daily-news \
  --lifecycle-policy-text '{"rules":[{"rulePriority":1,"description":"Keep last 3","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}'
```

### EKS Launch Flow

```bash
# After image is pushed:

# Option A: kubectl set image (imperative, for quick updates)
kubectl set image deployment/daily-news-api \
  daily-news=$ECR_REGISTRY/$IMAGE_NAME:latest \
  -n aifeeders

kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders --timeout=60s

# Option B: Update image tag in deployment YAML (declarative, for GitOps)
# Edit openshift/deployments/daily-news-api.yaml — change image: field
kubectl apply -f openshift/deployments/daily-news-api.yaml -n aifeeders

# Trigger a run
kubectl create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# Watch
kubectl logs -f job/live-run-<id> -n aifeeders | grep -E "status=|published"
# Expected: Workflow complete — status=PUBLISHED published=2 errors=0
```

### EKS First-Time Setup (Complete)

```bash
# 1. Create namespace
kubectl create namespace aifeeders

# 2. Create ECR repositories (once)
for repo in daily-news news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  aws ecr create-repository --repository-name aifeeders/$repo --region $AWS_REGION
done

# 3. Create imagePullSecret for ECR
kubectl create secret docker-registry ecr-credentials \
  --docker-server=$ECR_REGISTRY \
  --docker-username=AWS \
  --docker-password=$(aws ecr get-login-password --region $AWS_REGION) \
  -n aifeeders

# 4. Apply RBAC, ConfigMap, Secrets
kubectl apply -f openshift/rbac.yaml -n aifeeders
kubectl apply -f openshift/configmap.yaml -n aifeeders
# Edit secrets.yaml with real values:
kubectl apply -f openshift/secrets.yaml -n aifeeders

# 5. Build and push all images (same ECR loop as above)

# 6. Deploy all services
kubectl apply -f openshift/deployments/ -n aifeeders
kubectl apply -f openshift/services/ -n aifeeders
kubectl apply -f openshift/cronjob.yaml -n aifeeders

# 7. Authorise LinkedIn
kubectl port-forward svc/linkedin-mcp 8080:8000 -n aifeeders
# Browser: http://localhost:8080/auth/linkedin

# 8. Smoke test
kubectl patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'
kubectl create job smoke-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders
# Expected: status=EVALUATED published=0 errors=0
kubectl patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### Production EKS Recommendations

| Topic | Recommendation |
|---|---|
| Secrets | Use **External Secrets Operator** + AWS Secrets Manager (not raw K8s Secrets) |
| IAM | Use **IRSA** (IAM Roles for Service Accounts) if any pod accesses AWS services |
| Node sizing | `t3.medium` minimum; `t3.large` recommended for `daily-news-api` (LLM calls are memory-intensive) |
| NetworkPolicy | Requires CNI with NetworkPolicy support — use **Calico** or **Cilium** |
| Image lifecycle | Set ECR lifecycle policy to retain last 3 images (no auto-delete unlike OpenShift) |
| LinkedIn MCP | 1 replica only — stateful OAuth token. Use a `StatefulSet` with a persistent volume if token persistence across pod restarts is needed |

---

## 11. Data Flow — End to End

```
10:00 UTC — CronJob fires
  │
  ▼
discover_news
  ├─ 6 parallel GNews queries across 6 AI categories:
  │    "artificial intelligence LLM agentic"
  │    "AI finance investment funding"
  │    "AI enterprise automation business"
  │    "AI jobs automation workforce"
  │    "AI regulation policy governance"
  │    "AI product launch announcement"
  └─ ~18 raw articles collected
  │
  ▼
deduplicate  (no LLM, no API calls — pure memory + disk)
  ├─ Pass 1: SHA-256(title+url) → skip exact duplicates (same story, two sources)
  └─ Pass 2: PublishedStore.filter_unpublished() → skip articles published today already
  │
  ▼
fetch_articles (capped at 30)
  └─ Full HTML fetch + parse for each article via news-mcp
  │
  ▼
index_pageindex
  └─ Chunk each article, store in PageIndex for RAG retrieval during summarise
  │
  ▼
jev_prefilter  [Jev Decision #1]
  ├─ asyncio.Semaphore(5) — 5 concurrent Jev calls
  ├─ Each article: one POST /v1/systemone → 11 answers
  ├─ Filter: is_ai_topic < 0.5 → skip
  ├─ Score:  composite = relevance×0.6 + engagement×0.4
  ├─ Select: top 2 by composite score → state["selected_articles"]
  ├─ Store:  jev_persona_hints = persona_fit scores for #1 article
  └─ Store:  jev_prefilter_scores = full score set for #1 → rendered in post
  │
  ▼
summarise  [1 LLM call per selected article]
  ├─ Fetch PageIndex context: "key business and technology facts"
  └─ LLM output: headline, summary, key_points[3], business_impact,
                 job_impact, technology_impact, policy_impact, source_url
  │
  ▼
jev_route_personas  [Jev Decision #2]
  ├─ One POST /v1/systemone per summary → 4 noul questions
  ├─ Activate personas where noul > 0.5
  ├─ Merge (union) with jev_persona_hints from prefilter
  └─ state["jev_active_personas"] = ["business", "policy", "genz", "linkedin"]
  │
  ▼
generate_personas  [1 LLM call per active persona]
  ├─ Fetch PageIndex context: "jobs, policy, business, technology evidence"
  ├─ PersonaAgentFactory.generate_all(summary, evidence, personas=active)
  └─ PersonaSetOutput{business, policy, genz, linkedin}
  │
  ▼
evaluate  [Jev Decision #3]
  ├─ source = raw article content + enriched summary fields
  ├─ generated = headline + summary + all persona perspectives
  ├─ JevClient.evaluate_content() → 9 quality scores
  └─ EvaluationAgent._apply_gate() → deterministic decision:
       BLOCK      → never published (PII / prompt injection)
       REGENERATE → back to summarise (max 2 retries, then publish PASS items only)
       HUMAN_REVIEW → approval gate (not yet wired to UI)
       PASS       → proceeds to publish
  │
  ▼
publish  [3 deterministic gates, then LinkedIn API]
  ├─ Gate 1: PUBLISHING_ENABLED=true?  (ConfigMap flag, false for smoke tests)
  ├─ Gate 2: PublishedStore.is_published(article_id)?  → skip if already published today
  ├─ Gate 3: evaluation.publish_eligible = True?
  ├─ Compose: _compose_main_post(summary, personas, jev_scores) → deterministic string ≤ 3000 chars
  ├─ POST to LinkedIn: LinkedInMCPClient.create_post(text, publication_key)
  │      publication_key = "{article_id}:{YYYY-MM-DD}:{headline_hash[:12]}"
  │      On success: PublishedStore.mark_published(article_id)
  └─ Per-persona comments (sequential, never parallel):
         LinkedInMCPClient.create_comment(post_urn, text, comment_key) × 4 personas
         PERMISSION_ERROR = soft skip (Comments API not yet approved — personas in post body)
  │
  ▼
END — Workflow complete — status=PUBLISHED published=2 errors=0
```

---

## 12. Technology Stack

| Layer | Technology | Version | Role |
|---|---|---|---|
| Workflow orchestration | LangGraph | 1.x | State machine with conditional edges |
| LLM calls | LangChain + ChatOpenAI | 1.x | Summarisation + persona generation |
| LLM model | Qwen2.5-72B-Instruct (configurable) | — | Default on IBM gateway |
| AI decision engine | Jev System One | — | Article scoring, persona routing, content evaluation |
| Web framework | FastAPI + Uvicorn | 0.14x | MCP servers + API trigger |
| Data models | Pydantic v2 | 2.x | State validation, settings, API contracts |
| HTTP client | httpx (async) | 0.28x | All outbound HTTP calls |
| Retry logic | tenacity | 9.x | LLM call retries |
| Observability | Langfuse v4 | 4.x | LLM trace per run session |
| Metrics | Prometheus client | 0.26x | `/metrics` endpoint on daily-news-api |
| Container base | python:3.11-ubi9 | — | UBI9 = Red Hat universal base image |
| Container platform | OpenShift 4.x / Kubernetes 1.27+ | — | Production runtime |
| Container registry | OpenShift internal ImageStream / ECR (EKS) | — | Per-build image storage |

---

## 13. File Layout

```
AINewsfeederLinkedin/
├── src/daily_news/
│   ├── agents/
│   │   ├── evaluation_agent.py    Jev primary / EvaluationMCP fallback; deterministic _apply_gate()
│   │   ├── jev_agents.py          jev_prefilter_articles, jev_route_personas graph nodes
│   │   ├── persona_agent.py       4-persona factory; _PersonaOutputRaw lenient parser
│   │   ├── published_store.py     File-backed dedup store; 7-day TTL; thread-safe
│   │   ├── publisher_agent.py     Post composition + 3-gate LinkedIn publish
│   │   └── summary_agent.py       LLM summarisation agent
│   ├── config/
│   │   └── settings.py            Pydantic settings; all env var aliases; lru_cache singleton
│   ├── mcp/
│   │   ├── jev_client.py          JevClient: prefilter_article, route_personas, evaluate_content
│   │   ├── evaluation.py          EvaluationMCPClient (LLM fallback)
│   │   ├── linkedin.py            LinkedInMCPClient (posts + comments)
│   │   ├── news.py                NewsMCPClient (GNews adapter)
│   │   └── pageindex.py           PageIndexMCPClient (RAG index + retrieval)
│   ├── models/
│   │   ├── evaluation.py          EvaluationResult, EvaluationDecision
│   │   ├── news.py                NewsArticle, NewsCategory
│   │   ├── persona.py             PersonaType (4), PersonaOutput, PersonaSetOutput
│   │   └── summary.py             NewsSummary
│   ├── observability/
│   │   └── tracing.py             Langfuse trace callbacks; flush_langfuse()
│   └── workflows/
│       └── daily_news_graph.py    LangGraph state machine; all 10 nodes; build_daily_news_graph()
├── prompts/
│   ├── capitalist.txt             Capitalist Mind prompt (ROI, moat, P&L)
│   ├── policy.txt                 Government Mind prompt (regulation, governance)
│   ├── genz.txt                   Generalist Mind prompt (plain English, everyday impact)
│   └── linkedin.txt               Tech & Workforce Mind prompt (strategy, careers)
├── tests/
│   ├── unit/
│   │   ├── test_jev_client.py     JevClient unit tests (mocked gateway)
│   │   ├── test_published_store.py PublishedStore: dedup gates, TTL, failure safety
│   │   ├── test_evaluation_gate.py _apply_gate() deterministic threshold tests
│   │   └── test_publisher_idempotency.py Publication key stability tests
│   └── workflow/
│       └── test_langgraph_routing.py LangGraph conditional edge routing tests
├── scripts/
│   └── verify_jev.py              Live 5-check Jev gateway probe (health + 3 question types)
├── openshift/
│   ├── buildconfigs/              BuildConfig YAMLs — one per service
│   ├── deployments/               Deployment YAMLs — one per service
│   ├── services/                  Service YAMLs — one per service
│   ├── configmap.yaml             Non-secret settings
│   ├── secrets.yaml.example       Secret template (never commit secrets.yaml)
│   ├── rbac.yaml                  Role + RoleBinding for CronJob SA
│   └── cronjob.yaml               CronJob — 0 10 * * * UTC
├── skills.md                      Persona voice guide (4 personas, tone, evidence rules)
├── ARCHITECTURE.md                This file
├── RUNBOOK.md                     Day-to-day operations guide
└── README.md                      Getting-started guide
```
