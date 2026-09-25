# AIFeeders — Architecture Reference

> Build #76 · OpenShift `aifeeders` · LangGraph · Jev System One · EKS-portable

This document explains **how the system is built, why it was built that way, and what production problems each design decision solves**. It is intended for engineers who need to understand, debug, or extend the system.

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Diagram](#2-system-diagram)
3. [LangGraph State Machine — 10 Nodes](#3-langgraph-state-machine--10-nodes)
4. [Jev System One Integration](#4-jev-system-one-integration)
5. [Deduplication — Three Independent Gates](#5-deduplication--three-independent-gates)
6. [Post Composition — LinkedIn UTF-16 Budget](#6-post-composition--linkedin-utf-16-budget)
7. [MCP Service Architecture](#7-mcp-service-architecture)
8. [Networking — NetworkPolicy Model](#8-networking--networkpolicy-model)
9. [Scaling Architecture](#9-scaling-architecture)
10. [Build Flow](#10-build-flow)
11. [OpenShift Deployment Manifest Summary](#11-openshift-deployment-manifest-summary)
12. [Memory and State Management](#12-memory-and-state-management)
13. [Observability](#13-observability)
14. [Security Architecture](#14-security-architecture)
15. [EKS Deployment](#15-eks-deployment)
16. [Change Log](#16-change-log)

---

## 1. Overview

AIFeeders is a fully autonomous AI news LinkedIn posting pipeline. It runs twice daily (08:00 UTC and 16:00 UTC) with zero human intervention. The system is built on three architectural pillars:

1. **LangGraph state machine** — deterministic 10-node workflow; every transition is explicit and logged. No LLM calls happen outside designated nodes.

2. **Jev System One** — fast, structured multi-question AI scoring replaces three separate LLM-as-judge calls. Jev returns calibrated floats, never free text — which means decisions are pure threshold comparisons with no parsing needed.

3. **Deterministic publishing layer** — `PublisherAgent` has zero LLM calls. Every decision is a threshold comparison or string operation. This is intentional: the publishing side-effect must be predictable and idempotent.

### Design principles

| Principle | Implementation |
|---|---|
| Every failure is recoverable | All LLM node errors are caught; pipeline continues with errors list |
| No silent data loss | Every error is logged with `run_id` and context |
| Safe for retries | 3-gate idempotency prevents duplicate LinkedIn posts |
| Secure by default | `default-deny-all` NetworkPolicy; secrets never in code or ConfigMap |
| Observable | Structured logs with `run_id`; Langfuse traces; LinkedIn audit |

---

## 2. System Diagram

```
External                    OpenShift / EKS cluster (namespace: aifeeders)
─────────                   ──────────────────────────────────────────────────────────────────────
                            ┌────────────────────────────────────────────────────────────────────┐
                            │                                                                    │
GNews API ──────────────────┼──► news-mcp (pod × 2)                                            │
  gnews.io                  │     FastAPI + GNews adapter                                       │
  2-key rotation            │     POST /call → news_search_latest, news_fetch_article           │
                            │              │                                                     │
                            │              ▼                                                     │
                            │     daily-news-api (pod × 2, HPA 2–4)                            │
                            │       FastAPI + LangGraph workflow runner                          │
                            │       POST /run    GET /health    GET /metrics                    │
                            │              │                                                     │
                            │       LangGraph 10-node state machine                             │
                            │              │                                                     │
Jev System One ─────────────┼─── jev_prefilter ─── jev_router ─── evaluate                    │
  IBM gateway               │                                                                    │
  POST /v1/systemone         │              │                                                     │
  Bearer auth                │     pageindex-mcp (pod × 1, in-memory RAG)                      │
                            │       POST /call → index_document, get_relevant_sections          │
                            │              │                                                     │
LLM endpoint ───────────────┼──── summarize ──── generate_personas                             │
  OpenAI-compatible         │              │                                                     │
                            │     evaluation-mcp (pod × 2)                                     │
                            │       LLM evaluation fallback (when JEV_ENABLED=false)            │
                            │              │                                                     │
LinkedIn API ───────────────┼──► linkedin-mcp (pod × 1, stateful OAuth token)                 │
  Posts API                 │     POST /call → linkedin_create_post                             │
  Comments API              │     GET  /audit  GET /health  GET /auth/linkedin                  │
                            │                                                                    │
CronJob ────────────────────┼──► daily-ai-news-morning  (0 8 * * * UTC)                       │
                            │    daily-ai-news-afternoon (0 16 * * * UTC)                      │
                            │      Pod label: app=daily-news-worker  ← required for NetworkPolicy│
                            │                                                                    │
ConfigMap ──────────────────┼──► daily-news-config   (non-secret env vars)                    │
Secret ─────────────────────┼──► daily-news-secrets  (API keys, LinkedIn credentials)         │
                            │                                                                    │
Langfuse ───────────────────┼──► optional LLM tracing (outbound HTTPS only)                   │
                            └────────────────────────────────────────────────────────────────────┘
```

---

## 3. LangGraph State Machine — 10 Nodes

### State schema

```python
class NewsWorkflowState(TypedDict):
    run_id: str                       # "RUN-{12 hex chars}" — unique per CronJob trigger

    # Discovery
    raw_articles: list[dict]          # all articles from all 9 GNews queries
    deduplicated_articles: list[dict] # after MD5 hash dedup + PublishedStore filter
    selected_articles: list[dict]     # after jev_prefilter: top 1

    # PageIndex
    pageindex_documents: list[dict]   # RAG index receipts (one per article)

    # Jev decision signals
    jev_persona_hints: list[str]      # from jev_prefilter: persona_fit signals from article text
    jev_active_personas: list[str]    # from jev_router: confirmed active personas ["policy","business"]
    jev_prefilter_scores: dict        # dict[article_id → {scores}] — keyed per article (NOT shared)

    # Generation
    summaries: list[dict]             # list of NewsSummary.model_dump()
    persona_outputs: list[dict]       # list of PersonaSetOutput.model_dump()

    # Evaluation
    evaluation_results: list[dict]    # list of EvaluationResult.model_dump()
    retry_count: int                  # incremented on each REGENERATE; max 2

    # Approval
    approval_status: str              # "PENDING" | "APPROVED" | "REJECTED"

    # Publishing
    linkedin_results: list[dict]      # one entry per article successfully published

    # Run metadata
    workflow_status: str              # last completed node name
    errors: list[str]                 # non-fatal errors; pipeline continues
```

### Node descriptions

| Node | Input state fields | Output state fields | Key behaviour |
|---|---|---|---|
| `discover_news` | — | `raw_articles` | 9 GNews queries; `hours=24`, `limit=10`; errors non-fatal |
| `deduplicate` | `raw_articles` | `deduplicated_articles` | Pass 1: MD5 within-run; Pass 2: PublishedStore cross-run |
| `fetch_articles` | `deduplicated_articles` | `selected_articles` | Full HTML fetch; caps at 30 for Jev scoring budget |
| `index_pageindex` | `selected_articles` | `pageindex_documents` | RAG index per article; errors non-fatal |
| `jev_prefilter` | `selected_articles` | `selected_articles` (top 1), `jev_prefilter_scores`, `jev_persona_hints` | Jev Decision #1 |
| `summarize` | `selected_articles` | `summaries` | LLM → NewsSummary; uses PageIndex for evidence |
| `jev_router` | `summaries`, `jev_persona_hints` | `jev_active_personas` | Jev Decision #2 |
| `generate_personas` | `summaries`, `jev_active_personas` | `persona_outputs` | LLM; only active personas; parallel coroutines |
| `evaluate` | `summaries`, `persona_outputs` | `evaluation_results`, `retry_count` | Jev Decision #3 |
| `publish` | `summaries`, `persona_outputs`, `evaluation_results` | `linkedin_results` | 3-gate dedup; no LLM |

### Graph wiring

```
START
  └─► discover_news
        └─► deduplicate
              └─► fetch_articles
                    └─► index_pageindex
                          └─► jev_prefilter
                                └─► summarize ◄───────────────────────────┐
                                      └─► jev_router                      │ REGENERATE retry
                                            └─► generate_personas          │ (max 2 times)
                                                  └─► evaluate ────────────┘
                                                        │
                                                        ├─ all PASS → publish → END
                                                        ├─ REGENERATE + retry < 2 → summarize
                                                        ├─ REGENERATE + retry ≥ 2 → publish (PASS only)
                                                        ├─ BLOCK → publish (skip blocked article)
                                                        └─ no results → END
```

### Why LangGraph and not a simple script?

A plain Python script would work for the happy path. LangGraph provides:
- **Retry loop** — the `evaluate → summarize` back-edge is a first-class graph edge, not a fragile `for` loop
- **State persistence** — if the runner crashes mid-run, state can be resumed (with checkpointing configured)
- **Explicit routing** — `route_evaluation()` makes branching visible and testable
- **Observability** — every node boundary is a natural tracing point

---

## 4. Jev System One Integration

### Why Jev instead of LLM-as-judge?

| Dimension | LLM-as-judge | Jev System One |
|---|---|---|
| Latency | 3–8s per call | < 2s per call |
| Output format | Free text → requires parsing | Structured floats + named labels |
| Determinism | Low — same prompt can return different answers | High — calibrated scoring model |
| Failure mode | Can hallucinate in its own evaluation | Pure threshold math; no hallucination |
| Multi-question | One question per call or complex prompt | All questions in one HTTP call |
| Cost | Full LLM token usage | Lightweight LoRA decision head |

### Jev question types

| Type | Returns | How used |
|---|---|---|
| `noul` | Float 0–1 | Numeric probability; >0.5 means "yes" |
| `choice` | Named string label | Picks one option from a defined set |
| `score` | Float 0..N-1 | Rates on a descriptive scale |

### Decision #1 — `jev_prefilter_articles` ([`jev_agents.py`](src/daily_news/agents/jev_agents.py))

```
Input: up to 30 article dicts (title + source + url + category + content[:2000])
       Each article is a separate POST /v1/systemone call.
       Concurrent: asyncio.Semaphore(5) — max 5 in-flight at once.

11 questions per article:
  is_ai_topic         noul  — hard gate: article must be primarily about AI (>0.5)
  relevance_score     noul  — overall AI relevance 0-1
  event_type          choice — product_launch|funding|regulation|research|acquisition|other
  significance        score — 0-4 normalised to 0-1 (landmark vs minor)
  controversy_level   choice — low|medium|high
  persona_fit_business  noul — would a business executive find this relevant?
  persona_fit_policy    noul — would a policy maker find this relevant?
  persona_fit_genz      noul — would a generalist find this accessible?
  persona_fit_linkedin  noul — would a tech/workforce professional find this relevant?
  estimated_engagement  noul — likelihood of LinkedIn engagement 0-1
  skip_reason         choice — not_ai|low_quality|none

Composite score = relevance_score × 0.6 + estimated_engagement × 0.4
Selection: top 1 by composite (only articles where is_ai_topic > 0.5)

State writes:
  selected_articles     → top 1 article dict
  jev_prefilter_scores  → dict[article_id → {event_type, relevance_score, significance,
                                              estimated_engagement, controversy_level,
                                              active_personas, persona_scores}]
  jev_persona_hints     → persona_fit values from the #1 article (warm start for router)

Fallback (JEV_ENABLED=false or ReadTimeout):
  selected_articles → articles[:1]    (first article, no scoring)
  jev_prefilter_scores → {}
  jev_persona_hints → []
```

### Decision #2 — `jev_route_personas` ([`jev_agents.py`](src/daily_news/agents/jev_agents.py))

```
Input: NewsSummary text for the selected article

4 questions:
  needs_business  noul — business revenue, market competition, enterprise decisions?
  needs_policy    noul — government regulation, AI safety legislation, data privacy?
  needs_genz      noul — generalist accessible, everyday life, career/learning?
  needs_linkedin  noul — tech strategy, engineering decisions, workforce reskilling?

Merges with jev_persona_hints from Decision #1 (union — keeps both signals)
Always returns ≥ 1 persona (falls back to "linkedin" if all below threshold)

State writes:
  jev_active_personas → e.g. ["policy", "business", "genz"]
  (only these LLM persona calls will run — others are skipped)

Fallback:
  jev_active_personas → all 4 personas
```

### Decision #3 — EvaluationAgent ([`evaluation_agent.py`](src/daily_news/agents/evaluation_agent.py))

```
Input: NewsSummary + PersonaSetOutput + source article text

10 questions:
  factuality            score 0-4  → /4 → 0-1  (higher = more factual)
  groundedness          score 0-4  → /4 → 0-1  (higher = better grounded in source)
  hallucination         score 0-4             (higher = MORE hallucination — NOT inverted)
  relevance             score 0-4
  toxicity              score 0-4
  pii_detected          noul 0-1   (>0.5 = hard BLOCK)
  prompt_injection_detected  noul  (>0.5 = hard BLOCK)
  political_bias_detected    noul  (logged, not blocking)
  policy_check          choice: PASS|REVIEW|FAIL
  overall_score         score 0-4

Gate logic (_apply_gate() in evaluation_agent.py):
  BLOCK if:   pii_detected > 0.5
           OR prompt_injection_detected > 0.5
           OR policy_check = FAIL

  REGENERATE if: factuality < EVAL_FACTUALITY_THRESHOLD (default 0.50)
              OR groundedness < EVAL_GROUNDEDNESS_THRESHOLD (default 0.50)
              OR hallucination > EVAL_HALLUCINATION_THRESHOLD (default 0.85)

  HUMAN_REVIEW if: policy_check = REVIEW

  PASS otherwise

Fallback (JEV_ENABLED=false):
  Uses EvaluationMCPClient (LLM-based equivalent evaluator)
```

---

## 5. Deduplication — Three Independent Gates

The pipeline can be re-triggered, retried by Kubernetes, or run twice on the same day. Three independent gates prevent any article from being published more than once.

### Gate 1 — deduplicate node (before any LLM work)

```python
# Pass 1: within-run hash dedup
# 9 GNews queries can return the same article multiple times
# MD5(title + URL) is deterministic and fast
for article in raw:
    h = md5(article["title"] + article["url"])
    if h not in seen:
        seen.add(h)
        unique.append(article)

# Pass 2: cross-run dedup
# Removes articles already published today from PublishedStore
unique = published_store.filter_unpublished(unique)
# If this empties the list, no LLM calls are made — cost saved upfront
```

### Gate 2 — publish node (right before the LinkedIn call)

```python
# Race condition guard: two simultaneous runs that both passed Gate 1
if published_store.is_published(summary.article_id):
    logger.info("skipping %s — already published (race condition gate)", article_id)
    continue
```

### Gate 3 — LinkedIn MCP idempotency key

```python
publication_key = f"{article_id}:{date.today().isoformat()}:{body_hash[:12]}"
# Stable across CronJob retries — no run_id in the key
# body_hash = sha256 of full composed post; content change forces new key
# LinkedIn MCP server tracks {publication_key → post_urn} in memory
# On duplicate key: returns existing URN without calling LinkedIn API
```

### PublishedStore — file-backed at `/tmp/aifeeders_published.json`

```json
{
  "news-4cf396b3c7e1:2026-09-25": "2026-09-25T03:52:54+00:00",
  "news-abc123def456:2026-09-24": "2026-09-24T08:31:22+00:00"
}
```

| Property | Value |
|---|---|
| Key format | `"{article_id}:{YYYY-MM-DD}"` |
| TTL | 7 days — entries older than 7 days purged on next load |
| Thread safety | `threading.Lock` — safe for single process |
| File size | < 2 KB (7 days × 2 articles/day × ~50 bytes/entry) |
| Failure mode — unreadable | Starts empty cache; logs WARNING; does NOT block publishing |
| Failure mode — unwritable | In-memory cache still works; Gate 2 still works within same process |
| Override path | `AIFEEDERS_STORE_PATH` env var — point to a PVC for cross-restart persistence |

### Why `/tmp` and not a database?

- A CronJob runs once per day; pod restarts between runs are acceptable data loss
- The same article won't appear in GNews for more than 24 hours (hours=24 query window)
- Gate 3 (LinkedIn idempotency key) catches any duplicate even without the store
- A database adds a dependency, network calls, and failure modes for < 2 KB of state

For **production deployments** where pod restarts mid-day are common, mount a PVC:
```yaml
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

### The production truncation bug (fixed in build #61)

**Problem:** LinkedIn's Posts API counts characters as **UTF-16 code units** — matching how JavaScript's `String.length` works. Python's `len()` counts Unicode code points. Characters outside the Unicode Basic Multilingual Plane (U+10000+) — which includes nearly all emoji used in the post (`💼 🎓 🧠 🔥 📌 📈 👷 🔬 💬 🤖 ⚠️`) — cost:
- Python `len()`: 1
- LinkedIn: 2 (encoded as UTF-16 surrogate pair)

A post with 30 such emoji that Python reports as 2980 characters is actually 3010+ LinkedIn units → silent mid-sentence truncation on LinkedIn.

**Fix:**
```python
def _linkedin_len(text: str) -> int:
    """Count text length as LinkedIn does: UTF-16 code units, not code points."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)
```

This function is used everywhere in [`publisher_agent.py`](src/daily_news/agents/publisher_agent.py) and in [`mcp_servers/linkedin_mcp/server.py`](mcp_servers/linkedin_mcp/server.py) for oversize detection.

**Safety limit:** `POST_LIMIT = 2900` (not 3000) — 100-unit safety margin.

### Budget architecture

```
POST_LIMIT = 2900 UTF-16 units

footer_block = _FOOTER_BASE + _BASE_HASHTAGS + _dynamic_tags
             ≈ 350–500 units (varies with hashtag count)

BODY_LIMIT = POST_LIMIT - _linkedin_len(footer_block)

Section allocation within BODY_LIMIT:
  ① Category pill         ~60 units  (event emoji + category + source + AIFeeders)
  ② Headline              ≤120 units
  ③ Opening hook          ≤200 units (_clip_at_sentence at boundary)
  ④ Signal strip          ~200 units (relevance, engagement, audience scores)
  ⑤ 3 key facts           ≤3×160 = 480 units (KP_CAP=160 per bullet)
  ⑥ Impact quad           ≤4×160 = 640 units (IMPACT_CAP=160 per line)
  ⑦ Source URL            ~80 units
  ⑧ 4 Voices section      remaining budget split across personas
  ⑨ CTA                   ~100 units
  ─────────────────────────────────────────────────────
  Total body              fits within BODY_LIMIT

footer_block appended unconditionally after body
final guard: _hard_clip(full_post, 2900) if somehow still over
```

### _clip_at_sentence vs _hard_clip

All **user-visible text** uses `_clip_at_sentence(text, max_units)` — it finds the last sentence boundary (`.`, `!`, `?`) at or before `max_units`. This prevents mid-sentence cuts.

`_hard_clip(text, max_units)` is used only as a final safety backstop on the complete post. Never on individual fields.

---

## 7. MCP Service Architecture

### What is an MCP server?

Each MCP server is a FastAPI application that exposes:
- `POST /call` — tool dispatcher: `{"tool": "tool_name", "arguments": {...}}` → `{"result": ...}`
- `GET /health` — health check
- `GET /metrics` — Prometheus metrics
- `/mcp` — optional streaming MCP protocol endpoint (not used in production)

The main app (`daily-news-api`) calls these via `MCPHTTPClient` — a thin wrapper around `httpx.AsyncClient`.

### MCPHTTPClient — key design decisions

**File:** [`src/daily_news/mcp/client.py`](src/daily_news/mcp/client.py)

1. **Nested error detection** — MCP servers can return HTTP 200 with a body of `{"result": {"error": "..."}}`. Before build #61, this was treated as success. Fix:
   ```python
   result = data.get("result")
   if isinstance(result, dict) and "error" in result:
       raise RuntimeError(f"MCP tool inner error [{tool}]: {result['error']}")
   ```

2. **Timeout** — 60 seconds for all calls. This is intentional: GNews can be slow during peak hours.

3. **URL normalisation** — ConfigMap URLs end with `/mcp` (e.g. `http://news-mcp:8000/mcp`). The client strips `/mcp` and appends `/call`. This allows the same URL to be used for both the streaming MCP transport and the REST tool dispatch.

### Service-by-service summary

| Server | Replicas | State | Why this replica count |
|---|---|---|---|
| `news-mcp` | 2 | None | Stateless; 2 for HA during pod restarts |
| `evaluation-mcp` | 2 | None | Stateless; 2 for HA |
| `pageindex-mcp` | 1 | In-memory RAG index | Index is built per-run; two replicas would have separate indexes |
| `linkedin-mcp` | 1 | OAuth token in memory | Two pods = two separate OAuth flows; no shared session store |

### linkedin-mcp — stateful singleton

The `linkedin-mcp` pod is special. It holds:

1. **OAuth access token** — stored as a module-level variable in the FastAPI process. Token obtained via `/auth/linkedin` OAuth flow. Lost on pod restart.

2. **Post idempotency dict** — `{publication_key → {post_urn, created_at}}`. Gate 3 deduplication. Lost on pod restart (but Gate 3 is a last resort — Gates 1 and 2 catch duplicates first).

3. **Audit log** — `{publication_key → {post_urn, timestamp, python_len, linkedin_len}}`. Accessible via `GET /audit`. Lost on pod restart.

**Consequence:** Any `linkedin-mcp` pod restart requires re-authorisation via OAuth. This is not a bug — it is a deliberate security trade-off: not persisting the token to disk means a storage breach cannot expose the LinkedIn credentials.

---

## 8. Networking — NetworkPolicy Model

### Why default-deny-all?

The `default-deny-all` policy means:
- A compromised `news-mcp` pod cannot directly call the LinkedIn API
- A compromised `pageindex-mcp` pod cannot exfiltrate data to external services
- Any future service added to the namespace is blocked from all pod communication until explicitly allowed
- External scanners and crawlers that reach a pod cannot pivot to other pods

This is "zero trust" at the pod level — standard practice for production microservices.

### The full policy set

```
NetworkPolicy: default-deny-all
  podSelector: {}         ← applies to all pods in namespace
  policyTypes: [Ingress]  ← blocks all inbound traffic by default

NetworkPolicy: allow-api-to-mcps
  podSelector: {role: mcp-server}        ← applies to MCP server pods
  ingress from:
    - podSelector: {app: daily-news-api}  ← main API pods can call MCPs
    - podSelector: {app: daily-news-worker} ← CronJob pods can call MCPs
  ports: [8000]

NetworkPolicy: allow-egress-internet
  podSelector: {}         ← all pods can reach the internet
  policyTypes: [Egress]
  egress to: all          ← outbound unrestricted (GNews, LinkedIn, Jev, LLM)

NetworkPolicy: allow-router-to-api
  podSelector: {app: daily-news-api}
  ingress from: [namespaceSelector: {openshift.io/cluster-monitoring: true}]
  ports: [8000]

NetworkPolicy: allow-router-to-linkedin-mcp
  podSelector: {app: linkedin-mcp}
  ingress from: [namespaceSelector: router label]
  ports: [8000]
```

### The CronJob label bug (production incident)

The most important NetworkPolicy lesson from operating this system:

**CronJob pod labels must be set under `template.metadata.labels`, not under `template.spec.labels`** (which is not a valid Kubernetes field and is silently ignored).

```yaml
# WRONG — silently ignored by Kubernetes
spec:
  jobTemplate:
    spec:
      template:
        spec:
          labels:              ← field does not exist here
            app: daily-news-worker

# CORRECT
spec:
  jobTemplate:
    spec:
      template:
        metadata:
          labels:
            app: daily-news-worker   ← NetworkPolicy sees this
        spec:
          ...
```

Without the label, CronJob pods have only auto-generated labels (`batch.kubernetes.io/job-name`, `controller-uid`). Neither matches `allow-api-to-mcps`. All outbound calls to MCP services time out silently after 60 seconds.

**How to diagnose:** `oc exec <cronjob-pod> -- curl --max-time 5 http://news-mcp:8000/health` — if it times out, the label is missing.

---

## 9. Scaling Architecture

### Horizontal scaling

`daily-news-api` scales horizontally:
- **Minimum:** 2 replicas (always-on HA)
- **Maximum:** 4 replicas (HPA)
- **Scale-up:** CPU > 70% averaged over 60 seconds
- **Scale-down:** CPU < 30% for 5 minutes

Why `daily-news-api` and not MCP servers?
- MCP servers are I/O-bound (HTTP calls to external APIs) — they don't benefit much from CPU-based scaling
- `daily-news-api` runs LangGraph + multiple parallel LLM calls — CPU-bound during persona generation

### Vertical limits

Resource limits are set conservatively to:
1. Prevent one pod from consuming all node resources (OOM kills neighbours)
2. Allow the HPA to schedule new pods without resource pressure

```
daily-news-api:
  requests: 256Mi memory, 500m CPU
  limits:   2Gi memory, 2 CPU
  ← 2 CPU limit accommodates parallel LLM calls in generate_personas
  ← 2Gi limit accommodates large article content in memory

news-mcp:
  requests: 128Mi memory, 100m CPU
  limits:   256Mi memory, 500m CPU
  ← lightweight HTTP adapter; no heavy processing
```

### PodDisruptionBudget

`pdb.yaml` sets `minAvailable: 1` for `daily-news-api`:
- During node drain (maintenance, upgrade), Kubernetes keeps at least 1 pod running
- Without PDB, all pods could be evicted simultaneously → API unavailable

### CronJob concurrency

`concurrencyPolicy: Forbid` on both CronJobs:
- If the 08:00 UTC run is still in progress at 16:00 UTC, the 16:00 run is skipped
- Prevents two simultaneous runs racing on the same articles
- Prevents GNews quota being consumed twice in short succession

### What explicitly does NOT scale

| Service | Why not scaled |
|---|---|
| `linkedin-mcp` | Stateful OAuth token — scaling requires distributed session store |
| `pageindex-mcp` | In-memory RAG index rebuilt per-run — two replicas = two separate indexes |
| CronJob pods | One pod per run; concurrencyPolicy=Forbid; horizontal scaling irrelevant |

---

## 10. Build Flow

### OpenShift — Source-to-Image (S2I)

OpenShift builds run entirely in-cluster. No Docker daemon on the developer machine.

```
Developer machine                    OpenShift cluster
─────────────────                    ───────────────────────────────────
source code
    │
    ▼
rsync strip → TMPDIR (< 5 MB)
  .venv/ excluded (270 MB)
  __pycache__/ excluded
  .git/ excluded
    │
    ▼
oc start-build --from-dir="$TMPDIR"
    │
    │ tarball streamed via API ──────────────────► BuildPod (temporary pod)
    │                                                1. Receives source tarball
    │                                                2. pip install (layer-cached ~15s)
    │                                                3. Copies src/ into image
    │                                                4. Pushes :latest to internal registry
    │                                                5. Terminates (self-cleaning)
    │                                             ↓
    │                                          Internal registry
    │                                          aifeeders/<service>:latest
    │
oc rollout restart
    │ ───────────────────────────────────────────►
    │                                          New pods pull :latest
    │                                          RollingUpdate: new pod passes health probe
    │                                          before old pod stops → zero downtime
```

### Why rsync to a tmpdir (not `oc start-build --from-dir=.` directly)?

`.venv/` is ~270 MB. `oc start-build --from-dir=.` streams the entire directory to the BuildPod. At cluster upload speeds, this takes 4–5 minutes and exceeds the streaming timeout.

With rsync exclusions, the upload is < 5 MB. `pip install` inside the BuildPod uses Docker layer cache — on subsequent builds it completes in ~15 seconds because the packages haven't changed.

**The rsync exclude list:**
```bash
--exclude='.venv/'          # 270 MB of installed packages
--exclude='**/__pycache__/' # Python bytecode cache
--exclude='**/*.pyc'        # compiled Python files
--exclude='.git/'           # version control history
--exclude='.pytest_cache/'  # test runner cache
--exclude='*.egg-info/'     # package metadata
--exclude='.env'            # local secrets
--exclude='.env.*'          # local secrets variants
--exclude='dist/'           # build artifacts
--exclude='build/'          # build artifacts
```

### Build history self-management

```yaml
# In every BuildConfig
spec:
  successfulBuildsHistoryLimit: 1   # delete previous successful build automatically
  failedBuildsHistoryLimit: 1       # delete previous failed build automatically
```

Effect: `daily-news-75` is deleted automatically when `daily-news-76` succeeds. `oc get builds` always shows exactly 1 record per service.

---

## 11. OpenShift Deployment Manifest Summary

### Services

| Service | Manifest dir | Replicas | Notes |
|---|---|---|---|
| `daily-news-api` | `openshift/api/` | 2 (HPA) | Includes Route for external access |
| `news-mcp` | `openshift/news-mcp/` | 2 | Internal only |
| `evaluation-mcp` | `openshift/evaluation-mcp/` | 2 | Internal only |
| `linkedin-mcp` | `openshift/linkedin-mcp/` | **1** | Includes Route for OAuth callback |
| `pageindex-mcp` | `openshift/pageindex-mcp/` | **1** | Internal only |

### Cluster-wide manifests

| File | What it creates | Key settings |
|---|---|---|
| `cronjob.yaml` | 2 CronJobs | `0 8 * * *` and `0 16 * * *` UTC; `concurrencyPolicy: Forbid`; pod label `app: daily-news-worker` |
| `hpa.yaml` | HPA for `daily-news-api` | min=2, max=4, target CPU=70% |
| `pdb.yaml` | PodDisruptionBudget | `minAvailable: 1` for `daily-news-api` |
| `rbac.yaml` | ServiceAccount + RoleBinding | `daily-news` SA; `create` Jobs permission only |
| `networkpolicy.yaml` | 5 NetworkPolicies | `default-deny-all`, `allow-api-to-mcps`, `allow-egress-internet`, `allow-router-to-api`, `allow-router-to-linkedin-mcp` |
| `configmap.yaml` | `daily-news-config` ConfigMap | Non-sensitive settings |
| `secrets.yaml` | `daily-news-secrets` Secret | Template only — never commit real values |
| `namespace.yaml` | `aifeeders` namespace | Labels for NetworkPolicy namespace selectors |

### ConfigMap vs Secret split

**ConfigMap `daily-news-config`** — safe to version control, visible to anyone with namespace read access:

| Key | Example | What it controls |
|---|---|---|
| `PUBLISHING_ENABLED` | `"true"` | Master switch for LinkedIn publishing |
| `JEV_ENABLED` | `"true"` | Enable/disable Jev; `false` = heuristic fallback |
| `LLM_BASE_URL` | `"https://..."` | LLM endpoint |
| `JEV_BASE_URL` | `"https://..."` | Jev gateway URL |
| `NEWS_MCP_URL` | `"http://news-mcp:8000/mcp"` | Internal service URL |
| `EVAL_FACTUALITY_THRESHOLD` | `"0.50"` | Jev eval gate |
| `EVAL_HALLUCINATION_THRESHOLD` | `"0.85"` | Jev eval gate |

**Secret `daily-news-secrets`** — never commit real values; base64-encoded:

| Key | Description |
|---|---|
| `LLM_API_KEY` | LLM bearer token |
| `GNEWS_API_KEY` | GNews primary key |
| `GNEWS_API_KEY_2` | GNews secondary key (auto-rotation on 403) |
| `JEV_API_KEY` | Jev gateway bearer token |
| `LINKEDIN_CLIENT_ID` | LinkedIn app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn app secret |
| `LANGFUSE_SECRET_KEY` | Optional: Langfuse tracing |

---

## 12. Memory and State Management

### Within a single run — LangGraph state (immutable transitions)

```
NewsWorkflowState is a TypedDict passed through every node.
Each node returns a NEW dict: {**state, "key": new_value}
No shared mutable state between nodes.
LangGraph tracks state at each node boundary.

Key transitions:
  discover_news   → raw_articles (list, immutable from here)
  deduplicate     → deduplicated_articles
  fetch_articles  → selected_articles (enriched)
  jev_prefilter   → selected_articles (top 1), jev_prefilter_scores (per-article dict)
  summarize       → summaries
  jev_router      → jev_active_personas
  generate_personas → persona_outputs
  evaluate        → evaluation_results, retry_count++
  publish         → linkedin_results
```

**Why per-article `jev_prefilter_scores`?**

Before build #61, `jev_prefilter_scores` was a flat dict of scores. When two articles were selected, article #2 would display article #1's Jev scores in its post body — because both used the same shared dict. The fix: `jev_prefilter_scores = dict[article_id → scores]`. Each article always gets its own scores.

### Across runs — persistent memory

**1. PublishedStore** (`/tmp/aifeeders_published.json`)
```
What: tracks {article_id:date → published_at timestamp}
Purpose: prevents republishing same article on re-run or retry
TTL: 7 days (purged on load)
Shared by: all LangGraph runs in the same pod lifetime
Lost on: pod restart (acceptable — see §5 for rationale)
```

**2. linkedin-mcp in-memory state**
```
What: OAuth token + idempotency dict + audit log
Purpose: post authentication + duplicate prevention + audit trail
Lost on: pod restart (token requires re-authorisation)
```

**3. Langfuse traces** (external)
```
What: every LLM call, Jev call, publish attempt
Purpose: debugging + quality tracking over time
Access: https://us.cloud.langfuse.com filtered by run_id
Retention: cloud, indefinite
```

### Cross-run Jev signal memory

```
jev_persona_hints (Decision #1 → Decision #2 warm start):
  Comes from: article raw text analysis (before LLM summary)
  Used by: jev_router to warm-start persona routing
  Merged as: union with Decision #2 output (keeps both signals)
  Ephemeral: LangGraph state only; not persisted

jev_prefilter_scores (Decision #1 → publish node):
  Comes from: per-article Jev scoring
  Used by: publisher_agent to populate signal strip in post
  Keyed by: article_id (not shared between articles — fixed in build #61)
  Ephemeral: LangGraph state only; not persisted
```

---

## 13. Observability

### Three observability layers

**Layer 1 — Structured logs** (always available, zero config)

Every log line includes `[run_id]` prefix and relevant context. All logs are written to stdout and captured by OpenShift's log aggregator.

```bash
# All runs today
oc logs -l app=daily-news-api -n aifeeders --since=12h

# What Jev selected and why
oc logs job/<job-name> -n aifeeders | grep "jev_prefilter:"

# Eval decisions
oc logs job/<job-name> -n aifeeders | grep "eval article="

# Publishing results
oc logs job/<job-name> -n aifeeders | grep -E "post published|post FAILED"

# All errors across all services
oc logs -l app=daily-news-api -n aifeeders --since=12h | grep "ERROR"
```

**Layer 2 — Langfuse traces** (requires `LANGFUSE_SECRET_KEY`)

Each run creates a Langfuse session with `run_id` as the session identifier. Every LLM call, Jev evaluation, and publish attempt creates a span with input/output/metadata.

Useful for:
- Identifying which LLM calls are slow or expensive
- Comparing evaluation scores over time
- Auditing content decisions

**Layer 3 — Prometheus metrics** (at `:8000/metrics` on `daily-news-api`)

```bash
oc port-forward svc/daily-news-api 9090:8000 -n aifeeders &
curl http://localhost:9090/metrics | grep aifeeders
```

### Published store inspection

```bash
oc exec deployment/daily-news-api -n aifeeders -- \
  cat /tmp/aifeeders_published.json | python3 -m json.tool
```

### LinkedIn audit

```bash
oc exec deployment/linkedin-mcp -n aifeeders -- \
  curl -s http://localhost:8000/audit | jq .
# Returns all posts published since pod start:
# {"news-abc123:2026-09-25": {"post_urn": "urn:li:share:...", "created_at": "...", ...}}
```

### GNews key health

```bash
oc exec deployment/news-mcp -n aifeeders -- \
  curl -s http://localhost:8000/health | jq .
# {
#   "keys_configured": 2,
#   "active_key_index": 1,       ← 1-based; key #1 = primary, key #2 = fallback
#   "active_key_prefix": "e6f0db13..."
# }
```

---

## 14. Security Architecture

### Secrets management

```
Developer laptop:
  .env file → never committed to git
  .env.example → placeholder values, safe to commit

OpenShift:
  daily-news-secrets → Kubernetes Secret
  All API keys injected as environment variables at pod start
  Never written to logs (code explicitly avoids logging secret values)
  Never in ConfigMap (ConfigMap is readable by anyone with namespace access)

At runtime:
  Application reads secrets from env vars via Pydantic Settings
  get_settings() is LRU-cached — reads env vars once, then cached
  No runtime file access to secrets
```

### Network security

```
default-deny-all NetworkPolicy:
  → MCP servers can only be reached from daily-news-api / CronJob pods
  → External services (GNews, LinkedIn, Jev) reachable only outbound
  → No inter-namespace traffic without explicit policy
  → Compromised news-mcp cannot directly reach linkedin-mcp

TLS:
  → All external calls use HTTPS
  → Jev gateway: verify=False (IBM internal certificate, self-signed)
  → All other external services: standard TLS verification
  → No plaintext HTTP to external services
```

### Content safety gates

```
Jev evaluation (evaluation_agent.py _apply_gate()):

  BLOCK if pii_detected > 0.5:
    → Article contains personal identifiable information
    → Never published, logged with BLOCK reason
    → Cannot be overridden by JEV_ENABLED=false
    (EvaluationMCPClient fallback runs equivalent PII check)

  BLOCK if prompt_injection_detected > 0.5:
    → Article contains adversarial AI prompts
    → Hard stop; logged
    
  BLOCK if policy_check = FAIL:
    → Article violates content policy
    → Hard stop; logged
```

### LinkedIn OAuth security

```
Token lifecycle:
  1. Obtained via browser OAuth flow (PKCE, no client secret in browser)
  2. Stored in linkedin-mcp process memory
  3. Never written to disk, logs, or any persistent store
  4. 60-day TTL enforced by LinkedIn
  5. Lost on pod restart → re-authorisation required

Blast radius if token is compromised:
  → Attacker can post to LinkedIn as the authenticated user
  → NetworkPolicy prevents pivot to other services
  → Token rotation (next OAuth flow) immediately invalidates the old token
```

### RBAC — principle of least privilege

```
ServiceAccount: daily-news (namespace: aifeeders)
  Permissions:
    - create: Jobs       ← CronJob needs this to spawn itself
    - get,list: Pods     ← health checks
    NO cluster-level permissions
    NO secret read (secrets are injected by Kubernetes, not read by the app)
    NO cross-namespace access
```

---

## 15. EKS Deployment

Everything except the image build mechanism and registry URL is identical between OpenShift and EKS. All YAML manifests (Deployments, Services, CronJob, HPA, PDB, NetworkPolicy, RBAC, ConfigMap) deploy unchanged.

### What changes

| Aspect | OpenShift | EKS |
|---|---|---|
| Image build | `oc start-build` (S2I in-cluster, no Docker daemon) | `docker build` local + push to ECR |
| Image registry | `image-registry.openshift-image-registry.svc:5000/aifeeders/<svc>:latest` | `<account>.dkr.ecr.<region>.amazonaws.com/aifeeders/<svc>:latest` |
| Secrets management | `oc create secret generic` | External Secrets Operator + AWS Secrets Manager |
| Routes / Ingress | `oc expose svc` → OpenShift Route (automatic) | AWS ALB Ingress Controller or `kubectl port-forward` |
| NetworkPolicy enforcement | Built-in | Must install Calico or Cilium (default VPC CNI does not enforce NetworkPolicy) |
| PublishedStore persistence | `/tmp` | Mount EFS PVC at `AIFEEDERS_STORE_PATH` |
| Build cleanup | `successfulBuildsHistoryLimit: 1` (automatic) | ECR lifecycle policy (expire untagged images) |

### EKS build flow

```bash
export ECR_REGISTRY=123456789012.dkr.ecr.us-east-1.amazonaws.com

TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='.env' \
  . "$TMPDIR/"

docker build -t aifeeders/daily-news:latest -f Dockerfile "$TMPDIR"
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_REGISTRY
docker tag aifeeders/daily-news:latest $ECR_REGISTRY/aifeeders/daily-news:latest
docker push $ECR_REGISTRY/aifeeders/daily-news:latest
```

### External Secrets Operator (EKS secrets management)

```yaml
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
            name: daily-news   # IRSA-annotated SA
---
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
    name: daily-news-secrets
  data:
    - secretKey: GNEWS_API_KEY
      remoteRef:
        key: aifeeders/prod
        property: GNEWS_API_KEY
    # ... repeat for all secrets
```

### EKS NetworkPolicy note

The default AWS VPC CNI does not enforce NetworkPolicy resources. You must install a CNI that does:

```bash
# Option 1: Calico
kubectl apply -f https://docs.projectcalico.org/manifests/calico.yaml

# Option 2: Cilium
helm repo add cilium https://helm.cilium.io/
helm install cilium cilium/cilium --namespace kube-system
```

Without a NetworkPolicy-enforcing CNI, the `default-deny-all` policy silently has no effect — all pods can communicate freely.

### EKS PublishedStore persistence

```yaml
# EFS StorageClass
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-sc
provisioner: efs.csi.aws.com
---
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
      storage: 1Mi
---
# In daily-news-api Deployment
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

## 16. Change Log

| Build | Date | Changes |
|---|---|---|
| #76 | 2026-09 | Clean rebuild after old build deletion. Confirmed live post `urn:li:share:7509097557713833984`. Docs rewrite with all production incidents. |
| #75 | 2026-09 | All visible text uses `_clip_at_sentence()` — no more mid-sentence cuts. `KP_CAP` 95→160, `IMPACT_CAP` 115→160. |
| #74 | 2026-09 | Full post format redesign — 10-section humanised engagement-first layout. `_build_signal_block()`, `_build_cta()`, `_extract_dynamic_tags()`. |
| #73 | 2026-09 | Single article per run (top-1 Jev). Dual CronJob 08:00/16:00 UTC. All persona prompts → 1 complete sentence ≤200 chars. `PERSP_MIN` 200→220. |
| #62 | 2026-09 | Documentation redesign. EKS portability guide. Memory model documented. `IMPACT_LINE_CAP` 88→120. |
| #61 | 2026-09 | **LinkedIn truncation fix** — `_linkedin_len()` UTF-16 counting; `POST_LIMIT`=2900. Per-article `jev_prefilter_scores` keyed by `article_id` (article #2 score bug). `MCPHTTPClient` nested error detection. `linkedin_delete_post` tool. `publication_key` includes body hash. |
| #60 | 2026-09 | Post composition rewrite: hook category from Jev `event_type`. `AI_SEARCH_QUERIES` 6→9; `hours`=48→24; `limit`=20→10. |
| #59 | 2026-09 | Initial documentation. |
| #58 | 2026-09 | `evaluation_agent.py` `personas.labor` AttributeError fix. |
