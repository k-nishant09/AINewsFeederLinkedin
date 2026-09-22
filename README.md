# AIFeeders — AI News Platform

**Autonomous AI Daily News Multi-Agent Platform** that discovers AI news, summarises it with an LLM, generates five distinct human-perspective commentaries, runs a six-layer content guardrail pipeline, and publishes the result to LinkedIn — every day at 10:00 AM UTC (3:30 PM IST) via an OpenShift CronJob.

> **Customer value:** AIFeeders saves professionals 30–60 minutes of daily news curation. Instead of manually scanning AI blogs and newsletters, a fully automated, LLM-powered editorial team discovers, filters, and publishes a daily AI digest to your LinkedIn profile — with no human intervention required after initial setup.

---

## Table of Contents

1. [What This Does](#what-this-does)
2. [Who Is This For](#who-is-this-for)
3. [Architecture Overview](#architecture-overview)
4. [Agents & Their Roles](#agents--their-roles)
5. [MCP Servers](#mcp-servers)
6. [Guardrails Pipeline (6 Layers)](#guardrails-pipeline-6-layers)
7. [Project Structure](#project-structure)
8. [Local Development Setup](#local-development-setup)
9. [Environment Variables](#environment-variables)
10. [OpenShift Deployment](#openshift-deployment)
11. [Multi-Platform Deployment (Helm)](#multi-platform-deployment-helm)
12. [LinkedIn OAuth Setup](#linkedin-oauth-setup)
13. [Monitoring & Observability](#monitoring--observability)
14. [Troubleshooting](#troubleshooting)
15. [Key Constraints & Decisions](#key-constraints--decisions)
16. [Changelog](#changelog)

---

## What This Does

Every day at 10:00 AM UTC (3:30 PM IST), AIFeeders:

1. **Discovers** the latest AI news across 6 categories using GNews API (48-hour window)
2. **Deduplicates** articles by URL+title hash to prevent repetition
3. **Indexes** article content in an in-memory document store (PageIndex MCP)
4. **Selects** 1 article per run for focused, high-quality processing
5. **Summarises** the article with structured fields using `qwen2-5-72b-instruct` LLM
6. **Generates** 5 human persona perspectives: Capitalist Mind, Working Professional Mind, Government Mind, Young/Fresher Mind, Techies Mind
7. **Evaluates** content through a 6-layer guardrail pipeline (factuality, hallucination, PII, injection, policy, format)
8. **Publishes** a fully-formatted plain-text post to LinkedIn (≤ 3000 chars) with all 5 perspectives embedded

The published post looks like:

```
🤖 AI NEWS
Xiaomi's MiMo-V2.6-Pro Outperforms DeepSeek as Top Open-Weights Model

Xiaomi has launched MiMo-V2.6-Pro, which surpasses DeepSeek as the leading
open-weights language model, along with a more affordable V2.6-Flash version.

📌 KEY POINTS
  • MiMo-V2.6-Pro is now the top-performing open-weights model globally.
  • Xiaomi also released a cheaper V2.6-Flash variant.
  • The model handles text, images, and video.

📈 Business — Could enhance Xiaomi's market position in AI and consumer electronics.
👷 Jobs     — May lead to increased demand for AI specialists and data scientists.
🔬 Tech     — Demonstrates significant advances in natural language processing.

🔗 https://venturebeat.com/...
─────────────────────────────────
🧵 PERSPECTIVES

1/5  💼  CAPITALIST MIND
<2-3 sentence business perspective + evidence bullets>

2/5  👷  WORKING PROFESSIONAL MIND
...
3/5  🏛️  GOVERNMENT MIND  ...
4/5  🎓  YOUNG / FRESHER MIND  ...
5/5  🧠  TECHIES MIND  ...

─────────────────────────────────
⚠️ DISCLAIMER: These are AI-simulated perspectives — not verified opinions.
🤖 Built with AIFeeders · Powered by Agentic AI

#AI #AgenticAI #ArtificialIntelligence #LLM #GenerativeAI #AINews #TechNews
#FutureOfWork #AIStrategy #MachineLearning #AIInnovation #DigitalTransformation
#AILeadership #AIAgents
```

> **Format note:** The headline is on its own line (line 2) so LinkedIn's mobile feed card preview always shows it. A long headline on the same line as the emoji gets clipped by LinkedIn's card renderer.

---

## Who Is This For

| Persona | Value |
|---------|-------|
| **AI professionals** | A daily LinkedIn post showing your thought leadership on AI — without writing anything manually |
| **Platform engineers** | A reference implementation of a production LangGraph + MCP agentic pipeline on OpenShift/Kubernetes |
| **AI/ML architects** | Demonstrates LLM guardrails, multi-persona generation, and deterministic publishing gates |
| **Enterprises** | White-label it for your team's LinkedIn page, internal newsletter, or Slack channel |

---

## Architecture Overview

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for full Mermaid diagrams and the architect's Q&A.

```
┌─────────────────────────────────────────────────────────────────┐
│                      OpenShift / Kubernetes                      │
│  ┌──────────────────┐    ┌──────────────────────────────────┐   │
│  │   daily-news-api  │    │     CronJob (0 10 * * * UTC)     │   │
│  │  (FastAPI + REST) │    │   daily_news.workflow_runner     │   │
│  └────────┬─────────┘    └──────────────┬───────────────────┘   │
│           │  LangGraph Workflow (9 nodes) │                       │
│           └──────────────┬───────────────┘                       │
│                          │                                        │
│          ┌───────────────▼────────────────┐                      │
│          │      daily_news_graph           │                      │
│          │  (LangGraph StateGraph)         │                      │
│          └───────────────┬────────────────┘                      │
│                          │  MCPHTTPClient POST /call              │
│  ┌───────────┐  ┌────────┴──────┐  ┌────────────┐  ┌─────────┐  │
│  │ news-mcp  │  │pageindex-mcp  │  │evaluation  │  │linkedin │  │
│  │ (GNews)   │  │(doc store)    │  │-mcp        │  │-mcp     │  │
│  │ replicas:2│  │ replicas: 1   │  │(guardrails)│  │replicas:│  │
│  │ key rotate│  │ ⚠️ in-memory  │  │ replicas:2 │  │  1 ⚠️   │  │
│  └───────────┘  └───────────────┘  └────────────┘  └─────────┘  │
└─────────────────────────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    GNews API       LLM Gateway        LinkedIn API
   (news search)  (qwen2-5-72b)       (Posts REST v202609)
   key rotation    verify=False        3000 char limit
```

---

## Agents & Their Roles

| Agent | LangGraph Node | LLM? | Responsibility |
|-------|---------------|------|----------------|
| `SummaryAgent` | `summarize` | ✅ Yes (temp=0.2) | Produces structured `NewsSummary` from article + PageIndex context |
| `PersonaAgentFactory` | `generate_personas` | ✅ Yes × 5 (temp=0.4, parallel) | Generates 5 distinct persona perspectives concurrently |
| `EvaluationAgent` | `evaluate` | ❌ No (calls evaluation-mcp which uses LLM internally) | Applies deterministic threshold gate; never lets the LLM decide whether to publish |
| `PublisherAgent` | `publish` | ❌ No | Composes post text, applies character budget, calls linkedin-mcp |

> **PublisherAgent has zero LLM calls.** Post composition is purely deterministic string assembly. This ensures the published content is exactly what was evaluated.

---

## MCP Servers

All servers expose `POST /call` with body `{"tool": "<name>", "arguments": {...}}` and return `{"result": ...}`.

### `news-mcp` — News Discovery

- **Source:** GNews API (`/v4/search`, `/v4/top-headlines`)
- **Key rotation:** Automatic failover from primary key (`GNEWS_API_KEY`) to secondary (`GNEWS_API_KEY_2`) on HTTP 403 (quota exhausted). Both search and headlines endpoints retry once with the fallback key. Health endpoint reports `active_key_index` and `active_key_prefix` for operator visibility.
- **Tools:** `news_search_latest`, `news_search_by_category`, `news_search_ai_tech`, `news_search_ai_finance`, `news_top_headlines_technology`, `news_fetch_article`
- **Replicas:** 2 (stateless)

### `pageindex-mcp` — Document Store & Retrieval

- **Storage:** In-memory dict keyed by `article_id`
- **Retrieval:** Keyword-overlap section scoring (no embeddings)
- **Tools:** `pageindex_index_document`, `pageindex_get_relevant_sections`, `pageindex_search_document`, `pageindex_get_document`
- **Replicas:** **1** (in-memory state — cannot scale without shared storage)

### `evaluation-mcp` — Content Guardrails

- **Layers:** 6 (prompt injection → factuality → hallucination → PII → policy → format)
- **Decision gate:** Fully deterministic (`_determine_decision()`) — the LLM never decides to publish; it only scores
- **Stub mode:** When `LLM_API_KEY` is absent, returns passing stub values (for CI/local dev)
- **Tools:** `evaluation_evaluate_all`, plus 6 individual layer tools
- **Replicas:** 2 (stateless)

### `linkedin-mcp` — LinkedIn Publishing

- **Auth:** OAuth 2.0 PKCE flow. Token stored in-memory; persisted via Secret after reauth.
- **Posts API:** `POST /rest/posts` (v202609). Returns URN from `x-restli-id` header.
- **Comments API:** 403 PERMISSION_ERROR (requires "Community Management API" product — not yet approved). Comments are **soft-skipped** with an INFO log; personas are embedded in the post body instead.
- **Idempotency:** `_published_posts` in-memory dict; keyed by `publication_key`
- **Audit log:** `/audit` endpoint returns last 50 publish/comment attempts with status + request IDs
- **Replicas:** **1** (in-memory token + idempotency registry)

---

## Guardrails Pipeline (6 Layers)

```
Generated Text + Enriched Source Text
         │
         ▼
Layer 1: Prompt Injection  — regex patterns + LLM classifier
Layer 2: Factuality        — LLM claim-level grounding score (0→1)
Layer 3: Hallucination     — LLM unsupported-statement score (0→1)
Layer 4: PII               — regex: email, phone, SSN, credit card, credentials
Layer 5: Policy            — LLM: toxicity, political bias, brand safety
Layer 6: Format            — deterministic: char count, hashtags, URLs, duplicate hash
         │
         ▼
   Deterministic Gate → PASS / REGENERATE / BLOCK / HUMAN_REVIEW
```

**Source enrichment (fix applied):** Because GNews free plan truncates article content to ~250 chars, the evaluation source text is enriched with all `NewsSummary` fields (headline, summary, key_points, business/job/tech impacts) before being sent to the evaluator. This prevents false hallucination scores caused by near-empty source text.

| Decision | Trigger | Action |
|----------|---------|--------|
| `PASS` | All layers within threshold | Publish to LinkedIn |
| `REGENERATE` | factuality < 0.50 OR groundedness < 0.50 OR hallucination > 0.85 | Re-run summarize + personas (max 2 retries) |
| `BLOCK` | PII found OR injection HIGH OR toxicity > 0.30 | Skip article permanently |
| `HUMAN_REVIEW` | Political bias OR policy violations | Exclude from auto-publish |

---

## Project Structure

```
AIFeeders/
├── src/
│   └── daily_news/
│       ├── agents/
│       │   ├── evaluation_agent.py   # Deterministic threshold gate + _enrich_source()
│       │   ├── persona_agent.py      # 5 parallel LLM personas (asyncio.gather)
│       │   ├── publisher_agent.py    # Post composition + LinkedIn MCP calls
│       │   └── summary_agent.py      # LangChain chain → NewsSummary Pydantic
│       ├── api/
│       │   └── main.py               # FastAPI app (POST /workflow/daily-news)
│       ├── config/
│       │   └── settings.py           # pydantic-settings from env vars
│       ├── mcp/
│       │   ├── client.py             # MCPHTTPClient (POST /call REST)
│       │   ├── news.py               # NewsMCPClient
│       │   ├── pageindex.py          # PageIndexMCPClient
│       │   ├── evaluation.py         # EvaluationMCPClient
│       │   └── linkedin.py           # LinkedInMCPClient
│       ├── models/
│       │   ├── evaluation.py         # EvaluationResult, EvaluationDecision
│       │   ├── news.py               # NewsCategory
│       │   ├── persona.py            # PersonaOutput, PersonaSetOutput, PersonaType
│       │   └── summary.py            # NewsSummary
│       ├── observability/
│       │   └── tracing.py            # Langfuse + OTLP (OTLP disabled if endpoint="")
│       ├── workflows/
│       │   └── daily_news_graph.py   # LangGraph StateGraph (9 nodes)
│       └── workflow_runner.py        # CronJob entry point (python -m ...)
├── mcp_servers/
│   ├── news_mcp/server.py            # GNews adapter + key rotation
│   ├── pageindex_mcp/server.py       # In-memory doc store
│   ├── evaluation_mcp/server.py      # 6-layer guardrail pipeline
│   └── linkedin_mcp/server.py        # OAuth + Posts API
├── prompts/
│   ├── summary.txt                   # Structured summary prompt
│   ├── capitalist.txt                # Capitalist Mind persona
│   ├── labor.txt                     # Working Professional Mind
│   ├── policy.txt                    # Government Mind
│   ├── genz.txt                      # Young/Fresher Mind
│   └── linkedin.txt                  # Techies Mind
├── openshift/                        # OpenShift manifests (configmap, secrets, deployments...)
├── deploy/
│   ├── deploy.sh                     # Multi-platform deploy script
│   ├── helm/aifeeders/               # Helm chart (18 templates)
│   └── environments/                 # openshift / eks / aks values.yaml
├── Dockerfile                        # UBI9 / Python 3.11 image
├── pyproject.toml
├── .env.example                      # Template — copy to .env, never commit .env
├── README.md
├── RUNBOOK.md
└── ARCHITECTURE.md
```

---

## Local Development Setup

### Prerequisites

- Python 3.11+
- Docker or Podman (for running MCP servers locally)
- GNews API key (free at https://gnews.io)
- LinkedIn Developer App credentials (see [LinkedIn OAuth Setup](#linkedin-oauth-setup))
- IBM LLM Gateway access (or any OpenAI-compatible endpoint)

### 1. Clone and set up venv

```bash
git clone https://github.com/k-nishant09/AINewsFeederLinkedin.git
cd AINewsFeederLinkedin
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your real values:
# LLM_API_KEY, GNEWS_API_KEY, LINKEDIN_CLIENT_ID,
# LINKEDIN_CLIENT_SECRET, LINKEDIN_ACCESS_TOKEN
```

### 3. Start all services

```bash
# Start all 5 MCP servers + the daily-news-api
docker compose up -d

# Optional: Langflow visual workflow IDE (port 7860)
docker compose --profile inspect up langflow -d
```

### 4. Run a smoke test (no publishing)

```bash
PUBLISHING_ENABLED=false python -m daily_news.workflow_runner
```

### 5. Run with publishing enabled

```bash
PUBLISHING_ENABLED=true python -m daily_news.workflow_runner
# Or via the API:
curl -X POST http://localhost:8000/workflow/daily-news
```

---

## Environment Variables

### ConfigMap (non-secret, `openshift/configmap.yaml`)

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `production` | `development` / `staging` / `production` |
| `LOG_LEVEL` | `INFO` | Python log level |
| `NEWS_MCP_URL` | `http://news-mcp:8000` | Internal service URL |
| `PAGEINDEX_MCP_URL` | `http://pageindex-mcp:8000` | Internal service URL |
| `EVALUATION_MCP_URL` | `http://evaluation-mcp:8000` | Internal service URL |
| `LINKEDIN_MCP_URL` | `http://linkedin-mcp:8000` | Internal service URL |
| `LLM_BASE_URL` | _(model gateway URL)_ | OpenAI-compatible endpoint |
| `LLM_MODEL` | `qwen2-5-72b-instruct` | Model name |
| `PUBLISHING_ENABLED` | `true` | Set `false` to run without publishing |
| `EVAL_FACTUALITY_THRESHOLD` | `0.50` | Min factuality score to pass |
| `EVAL_GROUNDEDNESS_THRESHOLD` | `0.50` | Min groundedness score to pass |
| `EVAL_HALLUCINATION_THRESHOLD` | `0.85` | Max hallucination score to pass |
| `LINKEDIN_API_VERSION` | `202609` | LinkedIn API version header |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `""` | Empty = OTLP disabled (no collector) |
| `LANGFUSE_BASE_URL` | `https://us.cloud.langfuse.com` | Langfuse endpoint |

### Secret (`openshift/secrets.yaml` — never commit real values)

| Variable | Description |
|----------|-------------|
| `LLM_API_KEY` | Bearer token for LLM gateway |
| `GNEWS_API_KEY` | GNews primary API key |
| `GNEWS_API_KEY_2` | GNews secondary key (auto-failover on 403) |
| `MCP_AUTH_TOKEN` | Shared Bearer token for MCP server auth |
| `LINKEDIN_CLIENT_ID` | LinkedIn OAuth app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn OAuth app client secret |
| `LINKEDIN_ACCESS_TOKEN` | LinkedIn member access token (60-day expiry) |
| `LINKEDIN_REFRESH_TOKEN` | Optional programmatic refresh token |
| `LINKEDIN_REDIRECT_URI` | Must match LinkedIn Developer Portal exactly |
| `LINKEDIN_SCOPES` | `openid profile email w_member_social` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key |

---

## OpenShift Deployment

### Prerequisites

```bash
oc login --token=<token> --server=https://<cluster-api>:6443
oc new-project aifeeders  # or: oc project aifeeders
```

### REST API endpoints (available from `daily-news-api`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health check |
| `GET` | `/ready` | Readiness probe |
| `POST` | `/workflow/daily-news` | Trigger workflow (no body — `run_id` auto-generated) |
| `GET` | `/workflow/{run_id}` | Get workflow run status |
| `GET` | `/metrics` | Prometheus metrics |

### Step-by-step

```bash
# 1. Apply ConfigMap and Secrets (fill in secrets.yaml first)
oc apply -f openshift/configmap.yaml
oc apply -f openshift/secrets.yaml

# 2. Apply RBAC
oc apply -f openshift/rbac.yaml

# 3. Create ImageStreams (first time only)
for svc in daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  oc create imagestream $svc -n aifeeders 2>/dev/null || true
done

# 4. Build all images
oc start-build daily-news     --from-dir=. -n aifeeders --follow
oc start-build news-mcp       --from-dir=mcp_servers/news_mcp -n aifeeders --follow
oc start-build pageindex-mcp  --from-dir=mcp_servers/pageindex_mcp -n aifeeders --follow
oc start-build evaluation-mcp --from-dir=mcp_servers/evaluation_mcp -n aifeeders --follow
oc start-build linkedin-mcp   --from-dir=mcp_servers/linkedin_mcp -n aifeeders --follow

# 5. Deploy services
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/
oc apply -f openshift/api/

# 6. Apply network policies, HPA, PDB, CronJob
oc apply -f openshift/networkpolicy.yaml
oc apply -f openshift/hpa.yaml
oc apply -f openshift/pdb.yaml
oc apply -f openshift/cronjob.yaml

# 7. Authorise LinkedIn (open in browser)
open https://linkedin-mcp-aifeeders.apps.<cluster>/oauth/start

# 8. Verify
oc get pods -n aifeeders
curl https://daily-news-api-aifeeders.apps.<cluster>/health
```

### Image builds

```bash
# Rebuild main image after code changes
oc start-build daily-news --from-dir=. -n aifeeders --follow
oc rollout restart deployment/daily-news-api -n aifeeders

# Rebuild a specific MCP server
oc start-build news-mcp --from-dir=mcp_servers/news_mcp -n aifeeders --follow
oc rollout restart deployment/news-mcp -n aifeeders
```

### Platform resources also deployed

| Resource | Details |
|----------|---------|
| HPA | `daily-news-api` — min:2, max:10, CPU:70%, Mem:80% |
| PDB | `daily-news-api`, `news-mcp`, `linkedin-mcp` — minAvailable:1 |
| NetworkPolicy | default-deny-all + 4 allow rules |
| RBAC | ServiceAccount `daily-news` — get/list ConfigMaps, Secrets, Pods |
| CronJob | `daily-ai-news` — `0 10 * * *` UTC, label `app: daily-news-worker` |

---

## Multi-Platform Deployment (Helm)

The Helm chart at `deploy/helm/aifeeders/` supports OpenShift, Amazon EKS, and Azure AKS.

See [`deploy/README.md`](deploy/README.md) for full per-platform instructions including:
- Prerequisites per platform (oc / aws / az CLI)
- Environment variable reference
- Quick start for each platform
- Secret management (ESO / AWS Secrets Manager / Azure Key Vault)
- LinkedIn token rotation
- Troubleshooting

**Quick start:**

```bash
# Export secrets (all platforms)
export LLM_API_KEY="..."
export GNEWS_API_KEY="..."
export GNEWS_API_KEY_2="..."          # optional secondary key for auto-rotation
export LINKEDIN_CLIENT_ID="..."
export LINKEDIN_CLIENT_SECRET="..."
export LINKEDIN_ACCESS_TOKEN="..."
export LANGFUSE_PUBLIC_KEY="..."
export LANGFUSE_SECRET_KEY="..."

# OpenShift
./deploy/deploy.sh --platform openshift --environment prod --namespace aifeeders

# EKS
./deploy/deploy.sh --platform eks --environment prod --namespace aifeeders \
  --image-registry 123456789.dkr.ecr.us-east-1.amazonaws.com/aifeeders --image-tag v1.0.0

# AKS
./deploy/deploy.sh --platform aks --environment prod --namespace aifeeders \
  --image-registry myacr.azurecr.io/aifeeders --image-tag v1.0.0
```

---

## LinkedIn OAuth Setup

### Initial setup

1. Go to https://www.linkedin.com/developers/apps/new and create an app
2. Under **Products** tab, request: **Share on LinkedIn** + **Sign In with LinkedIn using OpenID Connect**
3. Under **Auth** → OAuth 2.0 settings, add redirect URI:
   ```
   https://linkedin-mcp-<namespace>.apps.<cluster-domain>/oauth/callback
   ```
4. Note your **Client ID** and **Client Secret** — add to `openshift/secrets.yaml`
5. Trigger the OAuth flow: `open https://linkedin-mcp-<namespace>.apps.<cluster>/oauth/start`
6. Complete the consent screen — the pod stores the token automatically

### Required LinkedIn app settings

| Setting | Value |
|---------|-------|
| App Client ID | _(your app's client ID from LinkedIn Developer Portal)_ |
| Products enabled | Share on LinkedIn + Sign In with LinkedIn (OpenID Connect) |
| Redirect URI | `https://linkedin-mcp-aifeeders.apps.<your-cluster-domain>/oauth/callback` |
| Scopes | `openid profile email w_member_social` |

### Token lifecycle

LinkedIn access tokens expire after **60 days**. Set a calendar reminder.

To reauthorise:
```bash
# 1. Open OAuth flow in browser
open https://linkedin-mcp-aifeeders.apps.<cluster>/oauth/start

# 2. Complete consent

# 3. Persist the new token to survive pod restarts
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<new-token>"}}'
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

### Comments API note

The LinkedIn Comments API (`partnerApiSocialActions.CREATE`) requires the **"Community Management API"** product — a separate LinkedIn product requiring manual approval. Until approved, comment calls return `403 PERMISSION_ERROR`. This is handled as a **soft skip** — one `INFO` log is emitted and all 5 persona perspectives remain in the main post body. No pipeline errors result.

---

## Monitoring & Observability

### Health checks

Every service exposes `/health` (liveness) and `/ready` (readiness):

```bash
CLUSTER="apps.<your-cluster>"
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  echo "=== $svc ===" && curl -s https://${svc}-aifeeders.$CLUSTER/health | python3 -m json.tool
done
```

Key fields to check:

| Service | Field | Healthy value |
|---------|-------|---------------|
| `news-mcp` | `keys_configured` | `2` (both keys present) |
| `news-mcp` | `active_key_index` | `1` or `2` |
| `evaluation-mcp` | `llm_configured` | `true` |
| `linkedin-mcp` | `token_expired` | `false` |
| `linkedin-mcp` | `scopes_sufficient` | `true` |

### Audit log

The linkedin-mcp maintains a rolling in-memory audit log of all publish and comment attempts:

```bash
curl https://linkedin-mcp-aifeeders.apps.<cluster>/audit | python3 -m json.tool
```

Each entry includes: `timestamp`, `tool`, `actor_urn`, `post_urn`, `status`, `http_status`, `li_request_id`, `attempt`.

### Langfuse tracing

If `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, every workflow run is traced at https://us.cloud.langfuse.com. Each run generates:
- One root trace (`daily_news_workflow`)
- 6 spans for GNews searches
- 5 parallel spans for persona generation
- 1 span for evaluation with scores
- 1 span for publish with URN

Filter by `run_id` (visible in CronJob logs) to see all spans for a single run.

### Log access

```bash
# Latest CronJob run
oc logs -l app=daily-news-worker --tail=100 -n aifeeders

# API (manual trigger)
oc logs -l app=daily-news-api --tail=50 -n aifeeders

# LinkedIn MCP (publishing errors)
oc logs -l app=linkedin-mcp --tail=50 -n aifeeders

# Evaluation MCP (guardrail scores)
oc logs -l app=evaluation-mcp --tail=50 -n aifeeders
```

A successful run produces:
```
[RUN-xxx] discover_news started
[RUN-xxx] discovered 45 raw articles
[RUN-xxx] deduplicated: 45 → 38
[RUN-xxx] selected 1 story for summarisation
[RUN-xxx] summarised 1 articles
[RUN-xxx] eval article=news-abc decision=PASS factuality=0.86 groundedness=0.86 hallucination=0.40
[RUN-xxx] post published post_urn=urn:li:share:75081... status=published
[RUN-xxx] Comments API not available (PERMISSION_ERROR) — personas in post body. Skipping.
Workflow complete — status=PUBLISHED published=1 errors=0
```

---

## Troubleshooting

### GNews returns no articles

- **Quota exhausted:** Free plan allows 100 req/day, resets midnight UTC
- **Auto-rotation:** If primary key is exhausted, `news-mcp` automatically switches to `GNEWS_API_KEY_2`. Check `active_key_index` in `/health`
- **`hours=48` required:** Free plan returns sparse results for `hours=24` on compound queries
- **Verify key:** `curl "https://gnews.io/api/v4/search?q=AI&apikey=<key>&max=1"`

### LinkedIn post appears blank in feed card

- **Root cause (fixed):** The first line of the post was `🤖 AI NEWS  |  <long headline>`. LinkedIn mobile clips long first lines at the emoji glyph. The headline after `|` was hidden.
- **Fix applied:** Headline is now on its own line (line 2). Line 0: `🤖 AI NEWS`. Line 1: `<headline>`.
- **Verification:** Check the first 3 lines of the composed post; line 1 should be the bare headline with no emoji prefix.

### Evaluation always returns REGENERATE

- **Root cause (fixed):** GNews free plan truncates article content to ~250 chars. The evaluator was comparing 500-char persona output against ~250 chars of source, scoring everything as hallucinated.
- **Fix applied:** `_enrich_source()` in `EvaluationAgent` combines raw content with all `NewsSummary` fields before evaluation. Live thresholds: `factuality ≥ 0.50`, `groundedness ≥ 0.50`, `hallucination ≤ 0.85`.
- **Further tuning:** `oc patch configmap daily-news-config -n aifeeders --patch '{"data":{"EVAL_HALLUCINATION_THRESHOLD":"0.90"}}'`

### LinkedIn token expired

1. Open: `https://linkedin-mcp-aifeeders.apps.<cluster>/oauth/start`
2. Complete consent in browser
3. Persist the new token: `oc patch secret daily-news-secrets -n aifeeders --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<token>"}}'`
4. Restart: `oc rollout restart deployment/linkedin-mcp -n aifeeders`

### CronJob pod can't reach MCP servers

- NetworkPolicy `allow-api-to-mcps` requires label `app: daily-news-worker` on CronJob pods
- Verify: `oc get pods -l app=daily-news-worker -n aifeeders`
- The label is in `openshift/cronjob.yaml` under `spec.jobTemplate.spec.template.metadata.labels`

### `publication_key` collision — same post published twice

- Each manual trigger appends the last 8 chars of `run_id` to the key → unique per invocation
- CronJob (scheduled runs) uses the same key for the same article on the same day → idempotent
- The `linkedin-mcp` returns a cached result on duplicate keys (no duplicate LinkedIn post)

---

## Key Constraints & Decisions

| Constraint | Reason |
|------------|--------|
| LinkedIn Posts API: **3000 char limit** | Hard API limit — HTTP 400 if exceeded. Post is capped at 2990 chars |
| **Comments API: soft-skipped** | Requires "Community Management API" product. 403 PERMISSION_ERROR → INFO log, personas in post body |
| **Headline on own line** | LinkedIn mobile clips long first lines at the emoji. Headline on line 2 is always visible in the feed card preview |
| **`_enrich_source()` in evaluation** | GNews free plan content is ~250 chars. Enriching with `NewsSummary` fields gives the evaluator enough grounded context to score fairly |
| **Never retry 400/401/403/422** | These are permanent errors — retrying wastes quota and may trigger LinkedIn abuse detection |
| **Comments sequential** | LinkedIn rate-limits burst comment creates. 3s floor delay per comment (enforced at MCP level) |
| `pageindex-mcp` and `linkedin-mcp` **replicas: 1** | Both use in-memory state. Scale to >1 requires shared storage (Redis/Postgres) |
| **`OTEL_EXPORTER_OTLP_ENDPOINT=""`** | No OTEL collector deployed. Empty string disables OTLP export cleanly |
| **`hours=48`** for GNews search | Free plan compound queries return sparse results for 24h windows |
| **1 article per run** | Keeps pipeline fast and the LinkedIn post focused. Change `[:1]` in `select_stories` to increase |
| **Evaluation thresholds 0.50/0.50/0.85** | Calibrated from observed qwen2-5-72b-instruct self-evaluation score distributions on AI news |
| **`run_id` suffix in `publication_key`** | Prevents idempotency collisions on same-day manual re-triggers after format changes |
| **No markdown in LinkedIn posts** | `*bold*` renders as literal asterisks. Use ALL-CAPS + emoji for visual structure |
| **GNews key rotation** | Primary key (GNEWS_API_KEY) auto-rotates to secondary (GNEWS_API_KEY_2) on 403. Health shows `active_key_index` |
| **MCP transport is `POST /call`** | `MCPHTTPClient` sends `{"tool": "...", "arguments": {...}}`. The MCP SDK streaming transport (`/mcp`) is mounted but not used — REST is simpler and more reliable |
| **`verify=False` on LLM + MCP calls** | IBM LLM gateway uses self-signed cert. Internal MCP calls are within the cluster |
| **Persona LLM calls are parallel** | `asyncio.gather` — each is stateless and independent. Reduces `generate_personas` from ~50s to ~12s |
| **`POST /workflow/daily-news` takes no body** | `run_id` is auto-generated server-side. Any body sent is ignored |

---

## Changelog

| Version | Date | Change |
|---------|------|--------|
| `1.3.0` | 2026-09-22 | **Fix: headline on own line.** LinkedIn mobile was clipping `🤖 AI NEWS \| <headline>` at the emoji. Headline moved to line 2. |
| `1.2.0` | 2026-09-22 | **Fix: evaluation source enrichment.** GNews free plan content (~250 chars) was causing hallucination=1.0. `_enrich_source()` now passes all `NewsSummary` fields as grounded reference. |
| `1.2.0` | 2026-09-22 | **Fix: comment PERMISSION_ERROR soft-skip.** `publisher_agent.py` now treats LinkedIn Comments API 403 as a soft skip (INFO log, no pipeline error). First occurrence sets `_comments_blocked=True` to avoid 5 unnecessary API calls. |
| `1.1.0` | 2026-09-22 | **Fix: GNews key rotation.** `news-mcp` automatically rotates from `GNEWS_API_KEY` to `GNEWS_API_KEY_2` on HTTP 403. Health endpoint reports `active_key_index` and `active_key_prefix`. |
| `1.0.0` | 2026-09-21 | Initial production release. LangGraph workflow, 4 MCP servers, 6-layer guardrails, OpenShift CronJob, Helm chart for OpenShift/EKS/AKS. |
