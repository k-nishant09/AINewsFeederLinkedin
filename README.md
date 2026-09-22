# AIFeeders — AI News Platform

**Autonomous AI Daily News Multi-Agent Platform** that discovers AI news, summarises it with an LLM, generates five distinct human-perspective commentaries, runs a six-layer content guardrail pipeline, and publishes the result to LinkedIn — every day at 10:00 AM UTC via an OpenShift CronJob.

---

## Table of Contents

1. [What This Does](#what-this-does)
2. [Architecture Overview](#architecture-overview)
3. [Agents & Their Roles](#agents--their-roles)
4. [MCP Servers](#mcp-servers)
5. [Guardrails Pipeline (6 Layers)](#guardrails-pipeline-6-layers)
6. [Project Structure](#project-structure)
7. [Local Development Setup](#local-development-setup)
8. [Environment Variables](#environment-variables)
9. [OpenShift Deployment](#openshift-deployment)
10. [Multi-Platform Deployment (Helm)](#multi-platform-deployment-helm)
11. [LinkedIn OAuth Setup](#linkedin-oauth-setup)
12. [Monitoring & Observability](#monitoring--observability)
13. [Troubleshooting](#troubleshooting)
14. [Key Constraints & Decisions](#key-constraints--decisions)

---

## What This Does

Every day at 10:00 AM UTC (3:30 PM IST), AIFeeders:

1. **Discovers** the latest AI news across 6 categories using GNews API (48-hour window)
2. **Deduplicates** articles by URL hash to prevent repetition
3. **Indexes** article content in an in-memory document store (PageIndex MCP)
4. **Selects** 1 article per run for focused processing
5. **Summarises** the article with structured fields using `qwen2-5-72b-instruct` LLM
6. **Generates** 5 human persona perspectives: Capitalist Mind, Working Professional Mind, Government Mind, Young/Fresher Mind, Techies Mind
7. **Evaluates** content through a 6-layer guardrail pipeline (factuality, hallucination, PII, injection, policy, format)
8. **Publishes** a fully-formatted plain-text post to LinkedIn (≤3000 chars) with all 5 perspectives embedded

The published post looks like:

```
🤖 AI NEWS  |  <headline>

<2-3 sentence summary>

📌 KEY POINTS
  • <fact 1>
  • <fact 2>

📈 Business — <business impact>
👷 Jobs     — <job impact>
🔬 Tech     — <technology impact>

─────────────────────────────────
🧵 PERSPECTIVES

1/5  💼  CAPITALIST MIND
<perspective text>
  ▸ <evidence bullet>

2/5  👷  WORKING PROFESSIONAL MIND
...

⚠️ DISCLAIMER: These are AI-simulated perspectives...
#AI #AgenticAI #ArtificialIntelligence ...
```

---

## Architecture Overview

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for full Mermaid diagrams.

```
┌─────────────────────────────────────────────────────────────────┐
│                      OpenShift / Kubernetes                      │
│  ┌──────────────────┐    ┌──────────────────────────────────┐   │
│  │   daily-news-api  │    │         CronJob (10:00 UTC)      │   │
│  │  (FastAPI + REST) │    │   daily_news.workflow_runner     │   │
│  └────────┬─────────┘    └──────────────┬───────────────────┘   │
│           │  LangGraph Workflow           │                       │
│           └──────────────┬───────────────┘                       │
│                          │                                        │
│          ┌───────────────▼────────────────┐                      │
│          │      daily_news_graph           │                      │
│          │  (9-node LangGraph state machine)│                     │
│          └───────────────┬────────────────┘                      │
│                          │                                        │
│  ┌───────────┐  ┌────────┴──────┐  ┌────────────┐  ┌─────────┐  │
│  │ news-mcp  │  │pageindex-mcp  │  │evaluation  │  │linkedin │  │
│  │ (GNews)   │  │(doc store)    │  │-mcp        │  │-mcp     │  │
│  └───────────┘  └───────────────┘  │(guardrails)│  │(OAuth)  │  │
│                                    └────────────┘  └─────────┘  │
└─────────────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   GNews API       LLM Gateway        LinkedIn API
  (news search)  (qwen2-5-72b)       (Posts REST)
```

---

## Agents & Their Roles

The LangGraph workflow executes these 9 nodes in sequence:

| Node | Agent / Code | What It Does |
|------|-------------|--------------|
| `discover_news` | `NewsMCPClient` | Queries GNews across 6 AI categories with a 48-hour window |
| `deduplicate` | inline (MD5 hash) | Removes articles with duplicate `title+url` hashes |
| `fetch_articles` | `NewsMCPClient.fetch_article` | Enriches articles with full content (capped at 30) |
| `index_pageindex` | `PageIndexMCPClient` | Indexes each article into in-memory section tree |
| `select_stories` | inline | Picks 1 article per run |
| `summarize` | `SummaryAgent` + LLM | Produces structured `NewsSummary` with 8 fields |
| `generate_personas` | `PersonaAgentFactory` | Runs 5 persona LLM prompts in parallel |
| `evaluate` | `EvaluationAgent` + MCP | Runs 6-layer guardrail pipeline; sets `publish_eligible` |
| `publish` | `PublisherAgent` + LinkedIn MCP | Composes post ≤3000 chars; calls `linkedin_create_post` |

**Routing after evaluate:**
- `PASS` → publish
- `REGENERATE` → back to summarize (max 2 retries)
- `BLOCK` / `HUMAN_REVIEW` → skip (item excluded from publish)

---

## MCP Servers

Four independent FastAPI + MCP servers, each at port 8000 inside the cluster:

### `news-mcp` — News Discovery
- **Source:** GNews API (`https://gnews.io/api/v4/search`)
- **Tools:** `news_search_latest`, `news_search_by_category`, `news_fetch_article`, `news_top_headlines_technology`
- **Free plan limits:** 100 requests/day, max 10 articles/request
- **Search window:** 48 hours (free plan returns sparse results for 24h)
- **Rate limiting:** 1100ms delay between requests (`GNEWS_REQUEST_DELAY_MS`)
- **Mock mode:** Returns 2 predictable mock articles when `GNEWS_API_KEY` is absent (safe for CI/local dev)

### `pageindex-mcp` — Document Store & Retrieval
- **Storage:** In-memory Python dict (replicas must stay at 1 or documents disappear on pod restart)
- **Retrieval:** Keyword-overlap section scoring — no embeddings, no vector DB, no external ML model
- **Tools:** `pageindex_index_document`, `pageindex_get_relevant_sections`, `pageindex_search_document`, `pageindex_get_document`
- **Note:** For production scale back with PostgreSQL or Redis

### `evaluation-mcp` — Content Guardrails
- **6 layers:** prompt_injection → factuality → hallucination → pii → policy → social_media_format
- **Tools:** `evaluation_evaluate_all` (all 6 in one call) + 6 individual standalone layer tools + `evaluation_policy_check` (backward-compat)
- **Decision:** Fully deterministic gate — LLM scores are advisory inputs; the Python gate decides PASS/REGENERATE/BLOCK/HUMAN_REVIEW
- **Thresholds (live):** factuality ≥ 0.50, groundedness ≥ 0.50, hallucination ≤ 0.85 (calibrated for qwen2-5-72b-instruct self-evaluation scores)
- **Stub mode:** Returns safe passing values when `LLM_API_KEY` is absent — so the pipeline still runs end-to-end in local dev

### `linkedin-mcp` — LinkedIn Publishing
- **Auth:** OAuth 2.0 3-legged flow; token stored in-memory (replicas must stay at 1)
- **Tools:** 12 tools — `linkedin_create_post`, `linkedin_create_comment`, `linkedin_validate_token`, `linkedin_get_profile`, `linkedin_get_profile_posts`, `linkedin_get_post`, `linkedin_get_publish_status`, `linkedin_get_comments`, `linkedin_create_comment_reply`, `linkedin_enable_comments`, `linkedin_disable_comments`, `linkedin_get_audit`
- **Idempotency:** `publication_key` (article_id + today's date + headline hash + run_id[-8:]) prevents duplicate posts; `comment_key` prevents duplicate comments
- **API version:** 202609 (env-configurable via `LINKEDIN_API_VERSION`)
- **Char limits:** posts ≤ 3000 (hard limit, enforced before API call), comments ≤ 1250 (soft limit, truncated with `...`)
- **Rate-limit safety:** 3-second `asyncio.sleep` at start of every `linkedin_create_comment` call
- **OAuth routes:** `/oauth/start` → `/oauth/callback` → `/oauth/status` + `/oauth/reauthorize`
- **Audit log:** Last 50 publish attempts at `GET /audit` — never logs the access token

---

## Guardrails Pipeline (6 Layers)

Every generated post passes through all 6 layers before being eligible for publication:

| Layer | Method | Blocks On |
|-------|--------|-----------|
| **1. Prompt Injection** | Regex (11 patterns) + LLM second-pass classifier | `HIGH` or `MEDIUM` risk → BLOCK |
| **2. Factuality** | LLM claim-level grounding check (source vs generated) | score < 0.50 → REGENERATE |
| **3. Hallucination** | LLM unsupported-statement detection | score > 0.85 → REGENERATE |
| **4. PII** | Regex only — no LLM (email, phone, SSN, credit card, API keys, tokens) | any PII found → BLOCK |
| **5. Policy** | LLM toxicity + political bias + brand safety + copyright markers | toxicity > 0.3 → BLOCK; bias → HUMAN_REVIEW |
| **6. Format** | Deterministic: char count ≤ 3000, hashtag count in comments ≤ 5, unsafe URLs, duplicate content hash | violations → REGENERATE |

**Decision priority (highest wins):** `BLOCK > HUMAN_REVIEW > REGENERATE > PASS`

> **Stub mode:** When `LLM_API_KEY` is absent the LLM-based layers (1, 2, 3, 5) return safe-passing stub values. This enables full local dev runs without a live LLM. Layer 4 (PII) and Layer 6 (format) always run — they are regex/deterministic and require no LLM.

---

## Project Structure

```
AINewsfeederLinkedin/
├── src/daily_news/              # Main application
│   ├── api/                     # FastAPI app + REST routes
│   │   └── main.py              # App factory, logging setup
│   ├── agents/                  # LangGraph node implementations
│   │   ├── summary_agent.py     # LLM summarization
│   │   ├── persona_agent.py     # 5-persona generation
│   │   ├── evaluation_agent.py  # Guardrail gate logic
│   │   └── publisher_agent.py   # LinkedIn post composition + publishing
│   ├── config/settings.py       # Pydantic settings (env-driven)
│   ├── mcp/                     # MCP client wrappers
│   │   ├── news.py              # → news-mcp
│   │   ├── pageindex.py         # → pageindex-mcp
│   │   ├── evaluation.py        # → evaluation-mcp
│   │   └── linkedin.py          # → linkedin-mcp
│   ├── models/                  # Pydantic data models
│   │   ├── news.py              # NewsArticle, NewsCategory
│   │   ├── summary.py           # NewsSummary
│   │   ├── persona.py           # PersonaOutput, PersonaSetOutput
│   │   └── evaluation.py        # EvaluationResult, EvaluationDecision
│   ├── observability/
│   │   ├── tracing.py           # OpenTelemetry + Langfuse setup
│   │   └── metrics.py           # Prometheus metrics
│   ├── workflows/
│   │   └── daily_news_graph.py  # LangGraph state machine (9 nodes)
│   └── workflow_runner.py       # CLI entry point for CronJob
│
├── mcp_servers/                 # Standalone MCP server processes
│   ├── news_mcp/server.py       # GNews integration
│   ├── pageindex_mcp/server.py  # In-memory document store
│   ├── evaluation_mcp/server.py # 6-layer guardrail pipeline
│   └── linkedin_mcp/server.py   # LinkedIn OAuth + Posts API
│
├── prompts/                     # LLM prompt templates
│   ├── summary.txt              # Structured news summary fields
│   ├── capitalist.txt           # Capitalist Mind persona
│   ├── labor.txt                # Working Professional Mind persona
│   ├── policy.txt               # Government Mind persona
│   ├── genz.txt                 # Young/Fresher Mind persona
│   └── linkedin.txt             # Techies Mind persona
│
├── openshift/                   # Kubernetes / OpenShift manifests
│   ├── namespace.yaml
│   ├── configmap.yaml           # Non-secret env vars
│   ├── secrets.yaml             # Template (never commit real values)
│   ├── cronjob.yaml             # 0 10 * * * UTC schedule
│   ├── networkpolicy.yaml       # Default-deny + selective allow
│   ├── api/                     # daily-news-api Deployment + Service + Route
│   ├── news-mcp/                # news-mcp Deployment + Service + Route
│   ├── pageindex-mcp/           # pageindex-mcp Deployment + Service + Route
│   ├── evaluation-mcp/          # evaluation-mcp Deployment + Service + Route
│   └── linkedin-mcp/            # linkedin-mcp Deployment + Service + Route
│
├── deploy/                      # Multi-platform Helm deployment
│   ├── deploy.sh                # 21-step deployment script
│   ├── helm/aifeeders/          # Helm chart (18 templates)
│   └── environments/            # Platform-specific values
│       ├── openshift/values.yaml
│       ├── eks/values.yaml
│       └── aks/values.yaml
│
├── Dockerfile                   # Main API + workflow runner image (UBI9/Python 3.11)
├── docker-compose.yaml          # Local dev: all 5 services
└── pyproject.toml               # Python deps + tool config
```

---

## Local Development Setup

### Prerequisites

- Python 3.11+
- Docker + Docker Compose
- GNews API key (free: https://gnews.io)
- LinkedIn Developer App (Client ID + Secret)
- IBM LLM Gateway access (or any OpenAI-compatible endpoint)

### 1. Clone and set up venv

```bash
git clone <repo>
cd AINewsfeederLinkedin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your real values — see Environment Variables section
```

### 3. Start all services

```bash
docker-compose up --build
```

Services start at:
- API: `http://localhost:8000`
- news-mcp: `http://localhost:8101`
- pageindex-mcp: `http://localhost:8102`
- evaluation-mcp: `http://localhost:8103`
- linkedin-mcp: `http://localhost:8104`

### 4. Run a smoke test (no publishing)

```bash
PUBLISHING_ENABLED=false python -m daily_news.workflow_runner
```

### 5. Run with publishing enabled

```bash
PUBLISHING_ENABLED=true python -m daily_news.workflow_runner
```

---

## Environment Variables

### ConfigMap (non-secret, `openshift/configmap.yaml`)

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `production` | Environment name |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `NEWS_MCP_URL` | `http://news-mcp:8000/mcp` | News MCP server URL |
| `PAGEINDEX_MCP_URL` | `http://pageindex-mcp:8000/mcp` | PageIndex MCP URL |
| `EVALUATION_MCP_URL` | `http://evaluation-mcp:8000/mcp` | Evaluation MCP URL |
| `LINKEDIN_MCP_URL` | `http://linkedin-mcp:8000/mcp` | LinkedIn MCP URL |
| `LLM_BASE_URL` | IBM model gateway URL | OpenAI-compatible LLM endpoint |
| `LLM_MODEL` | `qwen2-5-72b-instruct` | Model to use |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `""` | OTLP endpoint (empty = disabled) |
| `EVAL_FACTUALITY_THRESHOLD` | `0.50` | Min factuality score |
| `EVAL_GROUNDEDNESS_THRESHOLD` | `0.50` | Min groundedness score |
| `EVAL_HALLUCINATION_THRESHOLD` | `0.85` | Max hallucination score |
| `PUBLISHING_ENABLED` | `true` | Set `false` for smoke tests |
| `LINKEDIN_API_VERSION` | `202609` | LinkedIn REST API version |
| `LINKEDIN_COMMENT_DELAY_SECONDS` | `3` | Delay between comment publishes |

### Secret (`openshift/secrets.yaml` — never commit real values)

| Variable | Description |
|----------|-------------|
| `LLM_API_KEY` | Bearer token for LLM gateway |
| `GNEWS_API_KEY` | GNews API key |
| `LINKEDIN_CLIENT_ID` | LinkedIn app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn app client secret |
| `LINKEDIN_ACCESS_TOKEN` | Active OAuth access token |
| `LINKEDIN_REFRESH_TOKEN` | Refresh token (if enabled) |
| `LINKEDIN_REDIRECT_URI` | Must match LinkedIn app configuration |
| `LINKEDIN_SCOPES` | `openid profile email w_member_social` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse tracing public key |
| `LANGFUSE_SECRET_KEY` | Langfuse tracing secret key |
| `LANGFUSE_BASE_URL` | `https://us.cloud.langfuse.com` |

---

## OpenShift Deployment

### Prerequisites

- `oc` CLI logged in to `https://api.f80l034.fusion.tadn.ibm.com:6443`
- Namespace `aifeeders` created
- Image builds completed via `oc start-build`

### REST API endpoints (available from `daily-news-api`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/ready` | Readiness check |
| `POST` | `/workflow/daily-news` | Trigger workflow (runs in background, returns `run_id` immediately) |
| `GET` | `/workflow/{run_id}` | Poll status of a running/completed workflow |
| `POST` | `/approval/{run_id}/approve` | Approve a HUMAN_REVIEW item for publishing |
| `POST` | `/approval/{run_id}/reject` | Reject a HUMAN_REVIEW item |
| `GET` | `/news/search` | Search news via MCP |

> **Note:** `POST /workflow/daily-news` generates its own `run_id` — the request body is empty. Passing `run_id` in the body has no effect.

### Step-by-step

```bash
# 1. Create namespace
oc apply -f openshift/namespace.yaml

# 2. Create RBAC
oc apply -f openshift/rbac.yaml

# 3. Create ConfigMap (review values first)
oc apply -f openshift/configmap.yaml

# 4. Create Secrets (fill in real values first — NEVER commit)
oc apply -f openshift/secrets.yaml

# 5. Deploy MCP servers
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/

# 6. Deploy API
oc apply -f openshift/api/

# 7. Apply network policies
oc apply -f openshift/networkpolicy.yaml

# 8. Create CronJob
oc apply -f openshift/cronjob.yaml

# 9. Verify all pods are running
oc get pods -n aifeeders

# 10. Manually trigger a test run
oc create job --from=cronjob/daily-ai-news test-run-$(date +%s) -n aifeeders

# 11. Follow logs
oc logs -f job/test-run-<id> -n aifeeders
```

### Image builds

```bash
# Build main API/worker image (includes src/ and prompts/)
oc start-build daily-news --from-dir=. -n aifeeders --follow

# Build individual MCP server images
oc start-build linkedin-mcp --from-dir=mcp_servers/linkedin_mcp -n aifeeders --follow
oc start-build news-mcp --from-dir=mcp_servers/news_mcp -n aifeeders --follow
oc start-build evaluation-mcp --from-dir=mcp_servers/evaluation_mcp -n aifeeders --follow
oc start-build pageindex-mcp --from-dir=mcp_servers/pageindex_mcp -n aifeeders --follow
```

### Platform resources also deployed

| Resource | File | Purpose |
|----------|------|---------|
| HPA | `openshift/hpa.yaml` | `daily-news-api` autoscales 2–10 replicas on CPU/memory |
| PDB | `openshift/pdb.yaml` | Ensures ≥1 replica available for `daily-news-api`, `news-mcp`, `linkedin-mcp` during node drains |
| RBAC | `openshift/rbac.yaml` | ServiceAccount `daily-news` with minimal `get/list` on ConfigMaps, Secrets, Pods |

---

## Multi-Platform Deployment (Helm)

```bash
# OpenShift
./deploy/deploy.sh --platform openshift --environment prod --namespace aifeeders

# AWS EKS
./deploy/deploy.sh --platform eks --environment prod --namespace aifeeders \
  --image-registry 123456789.dkr.ecr.us-east-1.amazonaws.com/aifeeders

# Azure AKS
./deploy/deploy.sh --platform aks --environment prod --namespace aifeeders \
  --image-registry myacr.azurecr.io/aifeeders

# Dry run (no changes applied)
./deploy/deploy.sh --platform openshift --environment prod --dry-run

# Skip image builds (redeploy only)
./deploy/deploy.sh --platform openshift --environment prod --skip-build
```

---

## LinkedIn OAuth Setup

The LinkedIn MCP server exposes OAuth 2.0 3-legged flow endpoints:

### Initial setup

1. Go to: `https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/oauth/start`
2. Authorise the LinkedIn app in your browser
3. LinkedIn redirects to `/oauth/callback` — token is stored in the running pod
4. Verify: `https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/oauth/status`

### Required LinkedIn app settings

| Setting | Value |
|---------|-------|
| App Client ID | `77hy8ru946qrj4` |
| Products enabled | Share on LinkedIn + Sign In with LinkedIn (OpenID Connect) |
| Redirect URI | `https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/oauth/callback` |
| Scopes | `openid profile email w_member_social` |

### Token lifecycle

- Access tokens expire after **60 days**
- Re-run `/oauth/start` before expiry to obtain a fresh token
- Token is stored in the pod's in-memory store — it is lost on pod restart
- To persist across restarts: set `LINKEDIN_ACCESS_TOKEN` in the OpenShift secret

### Comments API note

The LinkedIn Comments API (`POST /rest/socialActions/{postUrn}/comments`) requires the **"Community Management API"** product on the LinkedIn Developer Portal. This product requires LinkedIn approval and is separate from "Share on LinkedIn". Until approved, all 5 persona perspectives are embedded directly in the post body (the current production behaviour).

---

## Monitoring & Observability

### Health checks

Every service exposes `/health`. Check from outside the cluster:

```bash
# API
curl https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health

# LinkedIn MCP (includes token status + audit log reference)
curl https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health

# Evaluation MCP (shows guardrail layer status)
curl https://evaluation-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health
```

### Audit log

The LinkedIn MCP server maintains the last 50 publish attempts:

```bash
curl https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/audit
```

### Langfuse tracing

Traces are sent to `https://us.cloud.langfuse.com` when both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are configured. The tracing layer uses three mechanisms:

- **`get_langfuse_callback(run_id=...)`** — LangChain `CallbackHandler` attached to every LLM chain invocation (summary + 5 persona agents). All traces share `trace_id = create_trace_id(seed=run_id)`.
- **`start_span(name, run_id=...)`** — Manual spans for MCP calls and non-LangChain steps (news discovery, evaluate node).
- **`@observe_node`** — Decorator available for entire LangGraph nodes (not currently applied by default but usable).

Every workflow run's traces are linked by `run_id` so you can filter the entire run as a session in Langfuse.

> **Note:** `langfuse_trace()` in `tracing.py` is a **no-op stub** retained for call-site compatibility. It always returns `None`. The real tracing goes through `get_langfuse_callback`.

### Log access

```bash
# CronJob logs (most recent run)
oc logs -l app=daily-news-worker --tail=200 -n aifeeders

# LinkedIn MCP logs
oc logs -l app=linkedin-mcp -n aifeeders --tail=100

# Follow live logs
oc logs -f deployment/linkedin-mcp -n aifeeders
```

---

## Troubleshooting

### GNews returns no articles

- **Free plan:** 100 requests/day. Check reset at midnight UTC.
- **`hours=48` required:** The free plan returns sparse results for `hours=24` on compound queries.
- **Verify key:** `curl "https://gnews.io/api/v4/search?q=AI&apikey=<key>&max=1"`

### LinkedIn post returns HTTP 201 but post appears blank on LinkedIn

- **Root cause:** Multi-codepoint emoji (e.g. `🏛️` = U+1F3DB U+FE0F) or separator lines can cause LinkedIn's renderer to blank the `commentary` field.
- **Current fix:** All personas use plain text + simple single-codepoint emoji. `─────` separators kept minimal.
- **Test:** POST a minimal text payload directly via the audit endpoint to isolate the character.

### Evaluation always returns REGENERATE

- Check `EVAL_FACTUALITY_THRESHOLD` and `EVAL_HALLUCINATION_THRESHOLD` in the ConfigMap.
- Live values: `0.50` / `0.50` / `0.85` — calibrated for qwen2-5-72b-instruct self-evaluation scores.
- If the LLM gateway is unreachable, evaluation-mcp returns stub pass values.

### LinkedIn token expired

1. Visit: `https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/oauth/start`
2. Re-authorise in browser
3. Update `LINKEDIN_ACCESS_TOKEN` in the OpenShift secret to persist across pod restarts

### CronJob pod can't reach MCP servers

- NetworkPolicy `allow-api-to-mcps` requires label `app: daily-news-worker` on CronJob pods
- Verify: `oc get pods -l app=daily-news-worker -n aifeeders`
- The label is in `openshift/cronjob.yaml` under `template.metadata.labels`

### `publication_key` collision — same post published twice

- Each run appends the last 8 chars of `run_id` to the publication key
- CronJob runs omit the suffix → same article published once per day
- Manual re-triggers always have a unique run_id suffix → fresh publish

---

## Key Constraints & Decisions

| Constraint | Reason |
|------------|--------|
| LinkedIn Posts API: **3000 char limit** | Hard API limit — HTTP 400 if exceeded. Post is truncated at 2990 chars in code |
| Comments API: **blocked** | Requires "Community Management API" product (needs LinkedIn approval). All personas in post body instead |
| **Never retry 400/401/403/422** | These are permanent errors — retrying wastes quota and may trigger abuse detection |
| **Comments sequential** — no `asyncio.gather` | LinkedIn rate-limits burst comment creates. 3s floor delay per comment |
| `pageindex-mcp` and `linkedin-mcp` **replicas: 1** | Both use in-memory state. Scale to >1 requires shared storage (Redis/Postgres) |
| **`OTEL_EXPORTER_OTLP_ENDPOINT=""`** | No OTEL collector deployed in this cluster. Empty string disables OTLP export cleanly |
| **`hours=48`** for GNews search | Free plan compound queries return sparse results for 24h windows |
| **1 article per run** | Keeps pipeline fast and debuggable. Increase `select_stories[:1]` for higher throughput |
| **Evaluation thresholds 0.50/0.50/0.85** | Calibrated from observed qwen2-5-72b-instruct self-evaluation score distributions |
| **`run_id` suffix in `publication_key`** | Prevents idempotency collisions on same-day manual re-triggers after format changes |
| **No markdown in LinkedIn posts** | `*bold*` renders as literal asterisks. Use ALL-CAPS + emoji for visual structure |
| **GNews free plan: 100 req/day** | Resets at midnight UTC. Avoid running multiple test workflows in one day |
| **MCP transport is `POST /call`** | `MCPHTTPClient` calls each server at `POST /call` with `{"tool": "...", "arguments": {...}}`. The MCP SDK streaming transport (`/mcp`) is mounted but not used by agents — REST is simpler and more reliable in this setup |
| **`verify=False` on all LLM + MCP calls** | IBM LLM gateway and internal MCP server calls use self-signed certs. httpx disables TLS verification for internal cluster traffic |
| **Persona LLM calls are parallel** (`asyncio.gather`) | All 5 persona agents run concurrently inside `PersonaAgentFactory.generate_all()`. This is intentional and safe — each is a stateless LLM call |
| **Workflow `POST /daily-news` takes no body** | The route auto-generates `run_id`. Do not pass a body — it is ignored |
