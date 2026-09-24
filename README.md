# AIFeeders — AI Daily News Platform

> **Four personas. Jev System One AI decision engine. Publishes daily to LinkedIn automatically.**  
> Build #62 · OpenShift `aifeeders` · EKS-portable · LangGraph state machine

---

## What It Does

AIFeeders runs every day at 10:00 UTC. It searches for AI news across 9 query categories (24-hour window), scores every article with Jev System One, summarises the best two, generates four audience-specific perspectives, evaluates content quality with Jev, and publishes a structured LinkedIn post — automatically, with no human pressing a button.

**A published post looks like this:**

```
🤖  AI REGULATION  ·  Powered by Jev

EU AI Act Enforcement Begins — No Grace Period for Agentic Systems

The EU AI Office confirmed that transparency obligations under Article 14
now cover agentic AI deployments as of March 2026 guidance...

⚙️  Jev Decision
  🏛️  Primary audience : Policy (regulation & governance)  — 94% Jev score
  📋  Story type       : Regulation
  🎯  AI relevance     : 92%   Market signal: [████░]
  ⚡  Engagement est.  : 78%   Controversy: High
  👥  Audience impact  : 🏛️ Policy 94%  ›  💼 Business 87%  ›  🧠 Tech+Workforce 71%
  🔥  AI market shift  : HIGH — potential to reshape the landscape.

📌  What you need to know
  • EU AI Office guidance names agentic systems under Article 14
  • Enforcement begins Q1 2027 with no grace period announced
  • Providers must log reasoning traces for auditable compliance

📈  Business   —  Compliance costs projected at €2M–€8M per enterprise deployment
👷  Workforce  —  New roles: AI compliance officer, agentic systems auditor
🔬  Tech       —  Reasoning trace logging required; LangGraph instrumentation needed
🏛️  Policy     —  EU AI Office enforcement begins Q1 2027; no grace period

🔗  Read more: https://...

🧵  Perspectives  ·  Jev-selected audience mindsets

🏛️  Government Mind
The EU AI Act's transparency obligations now explicitly cover agentic systems.
  • EU AI Office guidance names agentic systems under Article 14
  • Enforcement begins Q1 2027 with no grace period

💼  Capitalist Mind
Compliance is the new moat — those who move first will own the enterprise contracts.
  • €2M–€8M projected compliance cost per enterprise deployment
  • Early adopters set the benchmark others must match

🎓  Generalist Mind
The EU is the first major bloc to enforce rules on AI agents that act autonomously.
  • Affects every business using AI that takes real-world actions
  • Non-compliance fines up to 3% of global annual turnover

🧠  Tech & Workforce Mind
LangGraph tracing is no longer optional — it is the compliance instrument.
  • Reasoning trace logging required for auditable agent decisions
  • Observability tooling is now a legal requirement, not a nice-to-have

⚙️  Jev audience verdict  —  🏛️ Policy Makers  ›  💼 Business Strategists  ›  🎓 Generalists

💬  What's your take? Drop it below. 👇

⚠️ AI-simulated perspectives — not verified opinions or professional advice.
🤖 AIFeeders  ·  Agentic AI  ·  Powered by Jev

#AI #AgenticAI #Jev #AINews #GenerativeAI #TechNews #MachineLearning
#AIStrategy #AIInnovation #DigitalTransformation #AILeadership
```

---

## How the Pipeline Works

```
CronJob (10:00 UTC)
  └─► discover_news        9 GNews queries (hours=24) → ~27 articles
        └─► deduplicate    MD5 hash dedup + PublishedStore filter (Gate 1 — cross-run, 7-day TTL)
              └─► fetch_articles       Full HTML fetch + parse per article
                    └─► index_pageindex    RAG index for evidence retrieval
                          └─► jev_prefilter       [Jev Decision #1]
                                │  Scores all ≤30 articles in parallel (Semaphore(5))
                                │  11 questions per article: is_ai_topic, relevance_score,
                                │  event_type, significance, controversy_level,
                                │  persona_fit × 4, estimated_engagement, skip_reason
                                │  Composite = relevance × 0.6 + engagement × 0.4
                                │  → selected_articles: top 2
                                │  → jev_prefilter_scores: dict[article_id → scores]
                                │  → jev_persona_hints: persona_fit from best article
                                └─► summarize             LLM → structured NewsSummary (≤350 chars)
                                      └─► jev_router         [Jev Decision #2]
                                            │  4 noul questions on the NewsSummary
                                            │  Merges with jev_persona_hints (union)
                                            │  → jev_active_personas (subset, always ≥ 1)
                                            └─► generate_personas  (active subset only, parallel)
                                                  └─► evaluate     [Jev Decision #3]
                                                        │  10 questions: factuality, groundedness,
                                                        │  hallucination, relevance, toxicity,
                                                        │  pii_detected, prompt_injection,
                                                        │  political_bias, policy_check, overall_score
                                                        ├─► REGENERATE → summarize (max 2 retries)
                                                        ├─► BLOCK      → hard stop (PII/injection)
                                                        ├─► HUMAN_REVIEW → approval gate
                                                        └─► PASS → publish
                                                                    │  Gate 2: PublishedStore.is_published()
                                                                    │  Gate 3: LinkedIn MCP publication_key
                                                                    └─► LinkedIn post + persona comments
```

**Jev System One** makes three decisions per run:
1. **Which articles matter** — scores all articles, picks top 2 by composite (relevance × 0.6 + engagement × 0.4)
2. **Which personas should respond** — routes only relevant personas (saves 2–4 LLM calls per run)
3. **Is the content safe to publish** — factuality ≥ 0.50, groundedness ≥ 0.50, hallucination ≤ 0.85; PII/injection → hard BLOCK

---

## Why Jev (Not Just Another LLM Call)

| Decision point | Before Jev | With Jev |
|---|---|---|
| Article selection | Blind `[:2]` — first 2 in GNews order, same stale articles repeated | 11-dimension score per article in < 2s; picks genuinely best from fresh pool |
| Persona routing | Always 4 LLM calls regardless of relevance | 1 Jev call → activates only relevant personas (e.g. 2 of 4 for a regulation story) |
| Content evaluation | LLM evaluating LLM output — expensive + hallucination risk in the evaluator | Jev returns calibrated floats (no free text) for 10 quality dimensions |
| Hook category label | Hard-coded `AI NEWS` always | `event_type` from Jev → `AI REGULATION`, `Funding & M&A`, `AI Research`, etc. |
| Post audience section | Ordered by code, not relevance | Ranked by Jev `persona_scores` (highest-scoring persona listed first) |

Jev answers structured multi-question queries (`noul`, `choice`, `score` types) in **one HTTP call** to `POST /v1/systemone`. It returns float scores and named labels — never free text. Every decision is **fast** (< 2s), **cheap**, and **threshold-comparable** (no text interpretation needed downstream).

### Jev Integration — Three Decision Points

```
Article dicts (raw, ≤30)
        │
        ▼
[JEV DECISION #1]  jev_prefilter_articles  (jev_agents.py)
  POST /v1/systemone with 11 questions per article
  Parallel execution with asyncio.Semaphore(5)
  Questions:
    is_ai_topic         noul  → hard gate (must be > 0.5)
    relevance_score     noul  → AI relevance 0-1
    event_type          choice → product_launch|funding|regulation|research|acquisition|other
    significance        score → 0-4 scale (normalised to 0-1)
    controversy_level   choice → low|medium|high
    persona_fit_business  noul  → 0-1
    persona_fit_policy    noul  → 0-1
    persona_fit_genz      noul  → 0-1
    persona_fit_linkedin  noul  → 0-1
    estimated_engagement  noul  → 0-1
    skip_reason         choice → not_ai|low_quality|none
  State writes:
    selected_articles:     top 2 by composite score
    jev_prefilter_scores:  dict[article_id → {event_type, relevance_score, significance,
                                               estimated_engagement, controversy_level,
                                               active_personas, persona_scores}]
    jev_persona_hints:     persona_fit list from the #1 article (warm start for router)

        │
        ▼
  summarize  (LLM → NewsSummary: headline, summary, key_points, business/job/tech/policy_impact)
        │
        ▼
[JEV DECISION #2]  jev_route_personas  (jev_agents.py)
  POST /v1/systemone with 4 noul questions on the NewsSummary text
  Questions:
    needs_business  noul  → business revenue, market competition, enterprise decisions
    needs_policy    noul  → government regulation, AI safety legislation, data privacy
    needs_genz      noul  → generalist accessible, everyday life, career learning
    needs_linkedin  noul  → tech strategy, engineering decisions, workforce reskilling
  Merges answers with jev_persona_hints from Decision #1 (union — keeps both signals)
  Always returns ≥ 1 persona (falls back to linkedin if all below threshold)
  State writes:
    jev_active_personas: e.g. ["policy", "business"]

        │
        ▼
  generate_personas  (LLM — only jev_active_personas, parallel coroutines)
        │
        ▼
[JEV DECISION #3]  EvaluationAgent → JevClient.evaluate_content()  (evaluation_agent.py)
  POST /v1/systemone with 10 questions
  Questions:
    factuality            score → 0-4 (normalised /4 → 0-1)
    groundedness          score → 0-4 (normalised /4 → 0-1)
    hallucination         score → 0-4 (higher = more hallucination, NOT inverted in gate)
    relevance             score → 0-4
    toxicity              score → 0-4
    pii_detected          noul  → 0-1 (>0.5 → hard BLOCK)
    prompt_injection_detected  noul  → 0-1 (>0.5 → hard BLOCK)
    political_bias_detected    noul  → 0-1 (logged, not blocking)
    policy_check          choice → PASS|REVIEW|FAIL
    overall_score         score → 0-4
  Gate thresholds (configurable via env/ConfigMap):
    PASS if: factuality ≥ 0.50 AND groundedness ≥ 0.50
             AND hallucination ≤ 0.85 AND policy_check ≠ FAIL
             AND pii_detected=false AND prompt_injection=false
    REGENERATE: soft quality miss (max 2 retries → summarize)
    BLOCK: pii_detected OR prompt_injection_detected OR policy_check=FAIL
    HUMAN_REVIEW: borderline (policy_check=REVIEW)
```

**Fallback behaviour:** `JEV_ENABLED=false` or gateway timeout/error:
- `jev_prefilter` → first 2 articles (`[:2]`) — no scoring
- `jev_router` → all 4 personas
- evaluator → `EvaluationMCPClient` (LLM-based fallback)

---

## The Four Personas

| Persona | Label | Voice | Focus |
|---|---|---|---|
| 💼 **Capitalist Mind** | Business | Founder/operator. P&L lens. Ends with a pointed decision question. | ROI, margins, competitive moat, build-vs-buy |
| 🏛️ **Government Mind** | Policy | Policy memo precision. Names specific regulations. | Compliance, cross-jurisdiction governance, enforcement |
| 🎓 **Generalist Mind** | Generalist | Plain English. For people outside the AI bubble. | Everyday impact, what it means for non-specialists |
| 🧠 **Tech & Workforce Mind** | LinkedIn | Dual lens: architecture decisions + career reality. | Tech strategy, engineering trade-offs, workforce/skills |

Each persona produces: **3 sentences of perspective + 2 evidence bullets** from the article.

**Personas are Jev-routed** — only the relevant ones run. On a regulation story, only `policy` + `genz` may activate. On a product launch, all 4 may run. The post's `Perspectives` section orders them by Jev persona score (highest-scoring audience listed first). Prompt files: [`prompts/`](prompts/). Voice guide: [`skills.md`](skills.md).

---

## Deduplication — How Duplicate Posts Are Prevented

The CronJob can be retried, manually re-triggered, or run twice on the same day. Three independent gates prevent any article from being published more than once:

```
Gate 1 — deduplicate node (before any LLM work begins)
  ┌─ Pass 1: within-run dedup
  │    MD5 hash of (title + URL) → removes duplicate articles returned by multiple
  │    GNews queries in the same run (9 queries → same article can appear 3×)
  └─ Pass 2: cross-run dedup
       PublishedStore.filter_unpublished(articles)
       Removes articles already published today — checked BEFORE summarise/generate
       Saves cost: no LLM calls for known stale articles

Gate 2 — publish node (right before LinkedIn API call)
  PublishedStore.is_published(article_id)
  Catches race conditions: two simultaneous runs that both passed Gate 1

Gate 3 — LinkedIn MCP idempotency key
  publication_key = "{article_id}:{YYYY-MM-DD}:{body_hash[:12]}"
  Stable across CronJob retries (no run_id in the key)
  body_hash = sha256 of full composed post body → any content change forces a new post
  Even if Gates 1 and 2 both fail, LinkedIn MCP server returns the existing URN
  (or detects the duplicate via its own server-side key lookup)
```

**PublishedStore** — file-backed at `/tmp/aifeeders_published.json`:

| Property | Value |
|---|---|
| Key format | `"{article_id}:{YYYY-MM-DD}"` — one entry per article per calendar day |
| Value | ISO-8601 `published_at` timestamp |
| TTL | 7 days — entries older than 7 days are purged automatically on load |
| Thread safety | `threading.Lock` — safe for a single process |
| File size | < 2 KB (7 days × 2 articles/day × ~50 bytes/entry) |
| Failure mode | I/O error → logs warning + does **not** block publishing (availability beats dedup) |
| Env override | `AIFEEDERS_STORE_PATH` — change path to a mounted PVC for persistence across pod restarts |

**Full lifecycle per entry:**

```
discover_news       article_id = MD5(title + URL) — assigned on first discovery
deduplicate node    Pass 1: MD5 hash dedup within this run
                    Pass 2: PublishedStore.filter_unpublished() — Gate 1
                    → Articles published earlier today are dropped here
                    → No LLM work done for dropped articles
[LLM pipeline]      summarize → jev_router → generate_personas → evaluate
publish node        PublishedStore.is_published(article_id) — Gate 2 (race-condition guard)
                    LinkedIn MCP called with publication_key — Gate 3 (idempotency key)
                    On success: PublishedStore.mark_published(article_id)
                                writes {"article_id:YYYY-MM-DD": "2026-09-24T10:31:22Z"} to disk
7 days later        Entry purged from cache on next load (TTL enforcement)
```

---

## Post Character Budget — LinkedIn UTF-16 Fix (Build #61)

LinkedIn's Posts API counts characters as **UTF-16 code units**, matching JavaScript's `String.length`. Python's `len()` counts Unicode code points — they differ for emoji and other characters outside the Basic Multilingual Plane (U+10000+). Each such character costs **2 UTF-16 units** but only **1** in Python's `len()`.

A post with ~30 such emoji reported as 3000 chars by Python is actually ~3030–3060 UTF-16 units to LinkedIn — causing silent truncation mid-sentence.

**Fix (build #61):** `_linkedin_len()` counts UTF-16 units correctly:

```python
def _linkedin_len(text: str) -> int:
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)
```

Used everywhere in the post budget system. Safety limit: **2900 UTF-16 units** (not 3000). Budget architecture:

```
POST_LIMIT = 2900 UTF-16 units
FOOTER_TEXT_COST = _linkedin_len(_FOOTER) + ~141 (verdict + CTA + separators)
BODY_LIMIT = POST_LIMIT - FOOTER_TEXT_COST

_add(block, lines)  — append block only if _linkedin_len(block) + 1 fits in remaining
                       called for every section: hook, context, Jev block, key points,
                       impact snaps, source URL, persona sections

footer_block         — assembled outside the budget loop; always appended unconditionally
                       _hard_clip(body, max_body) if body exceeds POST_LIMIT - footer
```

---

## Architecture

### Services

| Service | Purpose | Replicas |
|---|---|---|
| `daily-news-api` | FastAPI — trigger runs, health checks, runs LangGraph workflow | 2 (HPA 2–4) |
| `news-mcp` | GNews adapter — 2-key auto-rotation on quota exhaustion | 2 |
| `evaluation-mcp` | LLM content quality fallback when Jev is disabled | 2 |
| `linkedin-mcp` | LinkedIn OAuth + post/comment publishing | **1** (stateful token) |
| `pageindex-mcp` | Article RAG / evidence retrieval (in-memory index) | **1** (in-memory state) |
| Jev gateway | IBM Jev System One — external AI decision engine | — (external) |

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the complete technical reference.

---

## Project Structure

```
src/daily_news/
  agents/
    jev_agents.py          jev_prefilter_articles, jev_route_personas (Decisions #1, #2)
    evaluation_agent.py    Jev primary / EvaluationMCP fallback; deterministic _apply_gate()
    persona_agent.py       4-persona factory; _PersonaOutputRaw lenient parser
    published_store.py     File-backed dedup store — 7-day TTL, threading.Lock singleton
    publisher_agent.py     3-gate publish + post composition (no LLM, deterministic)
                           _linkedin_len() UTF-16 counting; _compose_main_post() budget math
    summary_agent.py       LLM summarisation → NewsSummary
  config/
    settings.py            Pydantic settings; env var aliases; lru_cache singleton
  mcp/
    client.py              MCPHTTPClient — nested {"result":{"error":...}} detection
    jev_client.py          JevClient: prefilter_article, route_personas, evaluate_content
    evaluation.py          EvaluationMCPClient (LLM-based fallback evaluator)
    linkedin.py            LinkedInMCPClient
    news.py                NewsMCPClient
    pageindex.py           PageIndexMCPClient
  models/
    persona.py             PersonaType (4 values), PersonaOutput, PersonaSetOutput
    summary.py             NewsSummary
    evaluation.py          EvaluationResult, EvaluationDecision
  workflows/
    daily_news_graph.py    LangGraph 10-node state machine; AI_SEARCH_QUERIES (9, hours=24)

mcp_servers/
  linkedin_mcp/server.py   LinkedIn OAuth stateful server; _linkedin_len() oversize guard
                           linkedin_delete_post tool; publication_key idempotency

prompts/
  capitalist.txt  policy.txt  genz.txt  linkedin.txt

tests/
  unit/            test_jev_client.py  test_published_store.py  test_evaluation_gate.py
  workflow/        test_langgraph_routing.py

openshift/
  api/             deployment.yaml  service.yaml  route.yaml
  news-mcp/        deployment.yaml  service.yaml  route.yaml
  evaluation-mcp/  deployment.yaml  service.yaml  route.yaml
  linkedin-mcp/    deployment.yaml  service.yaml  route.yaml
  pageindex-mcp/   deployment.yaml  service.yaml  route.yaml
  configmap.yaml   cronjob.yaml  hpa.yaml  pdb.yaml  rbac.yaml
  networkpolicy.yaml  namespace.yaml  secrets.yaml

skills.md          Persona voice guide
ARCHITECTURE.md    Full technical reference (Jev, PublishedStore, memory, build, EKS)
RUNBOOK.md         Operations guide (build flow, launch flow, EKS, troubleshooting)
```

---

## Prerequisites

### Accounts needed

| Account | What for |
|---|---|
| GNews API key (×2) | `https://gnews.io` — free tier: 100 req/day; 2nd key for auto-rotation on 403 |
| OpenAI-compatible LLM | Any endpoint: IBM watsonx, Azure OpenAI, OpenAI, etc. |
| Jev System One | Gateway URL + Bearer token (IBM internal) |
| LinkedIn Developer App | Client ID + Secret; scopes: `w_member_social` (post), `r_liteprofile` |
| Langfuse (optional) | LLM tracing at `https://us.cloud.langfuse.com` |

### Tools required

```bash
python3 --version   # 3.11+ required
oc version          # OpenShift CLI — for OpenShift deployment
kubectl version     # for EKS deployment (oc is a superset of kubectl)
docker --version    # only needed for EKS builds
```

---

## Local Development

```bash
# 1. Clone
git clone https://github.com/k-nishant09/AINewsFeederLinkedin.git
cd AINewsFeederLinkedin

# 2. Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Environment variables
cp .env.example .env
# Edit .env — fill in LLM_API_KEY, GNEWS_API_KEY, JEV_BASE_URL, JEV_API_KEY, etc.

# 4. Run tests (must all pass before any build)
python -m pytest tests/unit tests/workflow -q
# Expected: 52 tests pass

# 5. Dry run (no LinkedIn post — use for integration testing)
PUBLISHING_ENABLED=false python -m daily_news.workflow_runner

# 6. Live run
python -m daily_news.workflow_runner
```

### Key environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | ✅ | — | OpenAI-compatible endpoint |
| `LLM_API_KEY` | ✅ | — | LLM bearer token |
| `GNEWS_API_KEY` | ✅ | — | GNews API key (primary) |
| `GNEWS_API_KEY_2` | — | — | GNews API key #2 (auto-rotation on 403) |
| `JEV_BASE_URL` | ✅ | — | Jev gateway URL |
| `JEV_API_KEY` | ✅ | — | Jev bearer token |
| `JEV_ENABLED` | — | `true` | Set `false` to fall back to heuristics |
| `LINKEDIN_CLIENT_ID` | ✅ | — | LinkedIn app client ID |
| `LINKEDIN_CLIENT_SECRET` | ✅ | — | LinkedIn app secret |
| `PUBLISHING_ENABLED` | — | `true` | Set `false` for smoke/dry runs |
| `EVAL_FACTUALITY_THRESHOLD` | — | `0.50` | Jev evaluation gate |
| `EVAL_GROUNDEDNESS_THRESHOLD` | — | `0.50` | Jev evaluation gate |
| `EVAL_HALLUCINATION_THRESHOLD` | — | `0.85` | Jev evaluation gate |
| `AIFEEDERS_STORE_PATH` | — | `/tmp/aifeeders_published.json` | PublishedStore file path |
| `LANGFUSE_SECRET_KEY` | — | — | Optional: LLM trace key |

---

## OpenShift Deployment — End-to-End Steps

### Step 1 — Login and namespace

```bash
oc login https://api.<cluster>:6443 -u <user> -p <pass>
oc new-project aifeeders   # skip if already exists
```

### Step 2 — ConfigMap and Secrets

```bash
oc apply -f openshift/configmap.yaml -n aifeeders

# Never commit real secrets — copy the template and fill in values
cp openshift/secrets.yaml.example openshift/secrets.yaml
# Edit secrets.yaml with real base64-encoded values
oc apply -f openshift/secrets.yaml -n aifeeders

oc apply -f openshift/rbac.yaml -n aifeeders
```

ConfigMap name: `daily-news-config`. Secret name: `daily-news-secrets`.

### Step 3 — Create BuildConfigs (first time only)

```bash
oc apply -f openshift/buildconfigs/ -n aifeeders

# Self-pruning: keep only 1 successful build per service
# Old builds are deleted automatically after each new successful build
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

### Step 4 — Build all images (first time)

```bash
# Rsync to a clean tmpdir — .venv/ is ~270 MB and causes build timeouts if included
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  --exclude='htmlcov/' --exclude='.coverage' --exclude='.tox/' --exclude='.DS_Store' \
  . "$TMPDIR/"

# Build all five services (sequential — one at a time)
for svc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc start-build $svc --from-dir="$TMPDIR" -n aifeeders --follow
done
```

> **Why rsync to a tmpdir?** `.venv/` is ~270 MB. Including it causes upload timeouts.  
> The rsync strip keeps the upload under 1 MB. `pip install` inside the BuildPod uses layer cache and runs in ~15s.

### Step 5 — Deploy all services

```bash
# Apply all manifests
oc apply -f openshift/api/ -n aifeeders
oc apply -f openshift/news-mcp/ -n aifeeders
oc apply -f openshift/evaluation-mcp/ -n aifeeders
oc apply -f openshift/linkedin-mcp/ -n aifeeders
oc apply -f openshift/pageindex-mcp/ -n aifeeders
oc apply -f openshift/cronjob.yaml -n aifeeders
oc apply -f openshift/hpa.yaml -n aifeeders
oc apply -f openshift/pdb.yaml -n aifeeders
oc apply -f openshift/networkpolicy.yaml -n aifeeders

# Verify all pods Running
oc get pods -n aifeeders
```

### Step 6 — Authorise LinkedIn (first time)

```bash
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# Browser: http://localhost:8080/auth/linkedin
# Grant permissions: w_member_social, r_liteprofile
# linkedin-mcp stores the token in-memory; it persists until the pod restarts
```

> LinkedIn tokens expire after **60 days**. Set a calendar reminder and re-authorise before expiry.

### Step 7 — Smoke test (no publishing)

```bash
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

oc create job smoke-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders
# Watch until complete:
oc logs job/smoke-<timestamp> -n aifeeders -f | tail -5
# Expected: "Workflow complete — status=EVALUATED published=0 errors=0"

oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### Step 8 — First live run

```bash
oc create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders --output=name
# Follow logs:
oc logs -f job/live-run-<timestamp> -n aifeeders | grep -E "status=|published|ERROR"
# Expected: "Workflow complete — status=PUBLISHED published=2 errors=0"
```

---

## Build Flow → Launch Flow

Every code change follows this exact sequence. **Never skip the test step.**

```bash
# ── STEP 1: Run tests locally ─────────────────────────────────────────────────
source .venv/bin/activate
python -m pytest tests/unit tests/workflow -q --tb=short
# All 52 tests must pass before proceeding

# ── STEP 2: Build (clean context — excludes .venv/, uploads < 1 MB) ──────────
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  --exclude='htmlcov/' --exclude='.coverage' --exclude='.tox/' --exclude='.DS_Store' \
  . "$TMPDIR/" && \
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --output=name
# Prints: build.build.openshift.io/daily-news-62

# ── STEP 3: Watch build ───────────────────────────────────────────────────────
oc logs -f build/daily-news-62 -n aifeeders | grep -E "STEP|Push|Error"
# Build ends with: "Push successful"

# ── STEP 4: Roll out (zero-downtime rolling restart) ─────────────────────────
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders --timeout=60s
# Output: "deployment 'daily-news-api' successfully rolled out"

# ── STEP 5: Run ───────────────────────────────────────────────────────────────
oc create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders --output=name

# ── STEP 6: Verify ────────────────────────────────────────────────────────────
oc logs job/live-run-<timestamp> -n aifeeders | grep -E "status=|published|ERROR"
# Expected: "Workflow complete — status=PUBLISHED published=2 errors=0"
```

### How upgrading replaces an old build automatically

```
oc start-build daily-news-62
  │
  ├── New BuildPod created (daily-news-62)
  ├── S2I build runs: pip install → copy src/ → compile image
  ├── New image pushed to internal registry as :latest
  └── Old build (daily-news-61) deleted automatically
      (successfulBuildsHistoryLimit: 1 — no manual cleanup ever needed)

oc rollout restart
  │
  ├── New Deployment pods scheduled — pull :latest from internal registry
  ├── Old pods terminated via RollingUpdate (maxUnavailable: 1, maxSurge: 1)
  └── Zero-downtime: new pods pass liveness/readiness probe before old pods stop
```

No manual image cleanup. No dangling BuildPods. No old build artifacts. The namespace stays clean on every upgrade.

---

## EKS Deployment

Moving from OpenShift to EKS requires **only the image build and `image:` field** to change. All Kubernetes resources — Deployments, Services, CronJob, ConfigMap, RBAC, NetworkPolicy, HPA, PDB — are **identical** YAML.

### What changes vs OpenShift

| Aspect | OpenShift | EKS |
|---|---|---|
| Image build | `oc start-build` (S2I, in-cluster) | `docker build` + ECR push |
| Image registry | `image-registry.openshift-image-registry.svc:5000/...` | `<account>.dkr.ecr.<region>.amazonaws.com/...` |
| Secrets | `oc create secret` | External Secrets Operator (ESO) + AWS Secrets Manager |
| OAuth route | `oc expose` + OpenShift Route | AWS ALB Ingress or `kubectl port-forward` |
| RBAC | Same | Same |
| All other YAML | Identical | Identical |

### EKS Build Flow

```bash
# ── Prerequisites ─────────────────────────────────────────────────────────────
export AWS_ACCOUNT=123456789012
export AWS_REGION=us-east-1
export ECR_REGISTRY=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Create ECR repo (once)
aws ecr create-repository --repository-name aifeeders/daily-news --region $AWS_REGION
# Repeat for: aifeeders/linkedin-mcp, aifeeders/news-mcp,
#             aifeeders/evaluation-mcp, aifeeders/pageindex-mcp

# ── Test first (same as OpenShift) ───────────────────────────────────────────
python -m pytest tests/unit tests/workflow -q --tb=short

# ── Build (same rsync pattern to avoid sending .venv/) ───────────────────────
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' \
  . "$TMPDIR/"

docker build -t aifeeders/daily-news:latest -f Dockerfile "$TMPDIR"

# ── Push to ECR ───────────────────────────────────────────────────────────────
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY
docker tag aifeeders/daily-news:latest $ECR_REGISTRY/aifeeders/daily-news:latest
docker push $ECR_REGISTRY/aifeeders/daily-news:latest
```

### EKS Launch Flow

```bash
# ── Update the image reference in deployment ─────────────────────────────────
kubectl set image deployment/daily-news-api \
  daily-news=$ECR_REGISTRY/aifeeders/daily-news:latest \
  -n aifeeders
kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders --timeout=60s

# ── Trigger a run ─────────────────────────────────────────────────────────────
kubectl create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# ── Watch ─────────────────────────────────────────────────────────────────────
kubectl logs -f job/live-run-<id> -n aifeeders | grep -E "status=|published"
```

The only YAML change needed is the `image:` field in each Deployment manifest — swap the OpenShift internal registry URL for the ECR URL. For production EKS, also:
- **External Secrets Operator** — sync AWS Secrets Manager entries to Kubernetes Secrets
- **IRSA** — use IAM Roles for Service Accounts (no hard-coded AWS credentials)
- **CNI with NetworkPolicy** — Calico or Cilium (EKS default VPC CNI does not enforce NetworkPolicy)
- **Persistent PVC for PublishedStore** — mount `AIFEEDERS_STORE_PATH` to a PVC so dedup state survives pod restarts

See [`ARCHITECTURE.md`](ARCHITECTURE.md) §10 for the complete EKS setup guide.

---

## Health Checks

```bash
# All pods
oc get pods -n aifeeders

# Per-service health endpoints
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health | jq -r .status
done

# Jev gateway (no auth required for /health)
curl -s https://<your-jev-gateway>/health | jq .
# Expected: {"status": "ready", "model": "Qwen/Qwen3.5-2B", "method": "lora_decision_head"}

# Today's run status (last 4 hours)
oc logs -l app=daily-news-worker -n aifeeders --since=4h | grep "status="

# PublishedStore — articles published in the last 7 days
oc exec deployment/daily-news-api -n aifeeders -- cat /tmp/aifeeders_published.json
```

---

## Troubleshooting

| Problem | Most likely cause | Fix |
|---|---|---|
| No post, `discovered 0 raw articles` | GNews 100 req/day quota exhausted | Wait until midnight UTC; check `GNEWS_API_KEY_2` is set |
| No post, `published=0 errors=0` | `PUBLISHING_ENABLED=false` in ConfigMap | `oc patch configmap daily-news-config ... PUBLISHING_ENABLED:true` |
| Same articles selected every day | PublishedStore blocking — all fresh articles already published | Check store; `clear_today()` or wait until tomorrow |
| Post failed `http=401` | LinkedIn token expired (60-day TTL) | Re-authorise via `oc port-forward svc/linkedin-mcp 8080:8000` |
| `OutputParserException` | LLM output schema mismatch | Usually transient — retry; check LLM endpoint |
| Build timeout / upload stuck | `.venv/` included in context | Use the rsync command — never `oc start-build . --from-dir=.` directly |
| `eval decision=REGENERATE` every run | Eval thresholds too strict for current articles | Lower `EVAL_FACTUALITY_THRESHOLD` in ConfigMap |
| Old build pods accumulating | `successfulBuildsHistoryLimit` not set | Patch BuildConfigs to limit 1 |
| `jev_prefilter: ReadTimeout` | Jev gateway busy | Gracefully falls back to `[:2]` selection — article still processed |
| `all articles already published today` | Re-run on same day after first run succeeded | Expected — wait until tomorrow or reset store |
| Hook label shows `AI NEWS` instead of `AI REGULATION` | Jev returning `event_type=other` for this article | Expected for generic stories; check Jev scores in logs |
| Post truncated mid-sentence (pre-build #61) | UTF-16/Python `len()` mismatch | Fixed in build #61 — `_linkedin_len()` |
| Article #2 shows article #1's Jev scores (pre-build #61) | Shared `jev_prefilter_scores` not keyed by article_id | Fixed in build #61 — per-article dict |
| Silent publish failure, no LinkedIn post visible | `{"result":{"error":...}}` not detected by MCPHTTPClient | Fixed in build #61 — nested error detection |

Full operations guide: [`RUNBOOK.md`](RUNBOOK.md)

---

## Monitoring

- **Langfuse** — LLM traces at `https://us.cloud.langfuse.com`. Each run is a session keyed by `run_id`. Every persona call, evaluation, and publish is a separate span with input/output/metadata.
- **LinkedIn audit** — `oc exec deployment/linkedin-mcp -n aifeeders -- curl -s http://localhost:8000/audit` returns all posts published since pod start (post URN, key, timestamp).
- **Prometheus** — metrics endpoint at `:8000/metrics` on `daily-news-api`.
- **PublishedStore** — `oc exec deployment/daily-news-api -n aifeeders -- cat /tmp/aifeeders_published.json` shows all articles published in the last 7 days.
- **Jev gateway** — `/health` (no auth) returns `{"status":"ready","model":"...","method":"lora_decision_head"}`.

---

## Build History

| Build | Key changes |
|---|---|
| #62 | Documentation complete rewrite. Build/Launch flow consolidated. EKS portability guide. Memory model documented. hook_category fix (`event_type=other` → `AI NEWS`; regulation → `AI REGULATION`). `IMPACT_LINE_CAP` raised 88 → 120. |
| #61 | **LinkedIn truncation fix** — `_linkedin_len()` UTF-16 counting; safety limit 2900. Per-article `jev_prefilter_scores` keyed by `article_id`. LinkedIn MCP truncates oversize posts with warning. `MCPHTTPClient` nested error detection. `linkedin_delete_post` tool added. `publication_key` includes body hash. |
| #60 | Post composition rewrite: hook category from Jev `event_type`, Jev decision block, footer guarantee, evidence bullet capping. `AI_SEARCH_QUERIES` 6→9; `hours=48→24`; `limit=20→10`. |
| #59 | Initial ARCHITECTURE.md / README.md / RUNBOOK.md. |
| #58 | `evaluation_agent.py` fix for `personas.labor` AttributeError. |
