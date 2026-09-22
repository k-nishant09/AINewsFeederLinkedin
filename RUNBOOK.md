# AIFeeders — Operations Runbook

**Audience:** Engineers, Platform Ops, or anyone who needs to understand, operate, or extend the AIFeeders platform end-to-end.

**Goal:** This runbook explains everything in plain language — from "what problem does this solve" through day-to-day operations and all common failure scenarios.

---

## Table of Contents

1. [Plain-Language Overview](#1-plain-language-overview)
2. [POC Journey — How We Got Here](#2-poc-journey--how-we-got-here)
3. [What Runs Where](#3-what-runs-where)
4. [Day-to-Day Operation](#4-day-to-day-operation)
5. [First-Time Setup (Fresh Cluster)](#5-first-time-setup-fresh-cluster)
6. [Building & Deploying New Code](#6-building--deploying-new-code)
7. [LinkedIn OAuth — Authorise & Reauthorise](#7-linkedin-oauth--authorise--reauthorise)
8. [Triggering a Manual Run](#8-triggering-a-manual-run)
9. [Reading the Logs](#9-reading-the-logs)
10. [Checking Health of Each Service](#10-checking-health-of-each-service)
11. [How the Workflow Flows (Step by Step)](#11-how-the-workflow-flows-step-by-step)
12. [Guardrails — What Gets Blocked and Why](#12-guardrails--what-gets-blocked-and-why)
13. [The LinkedIn Post Format](#13-the-linkedin-post-format)
14. [Evaluating a Run](#14-evaluating-a-run)
15. [Changing Configuration (No Rebuild)](#15-changing-configuration-no-rebuild)
16. [Rotating Secrets](#16-rotating-secrets)
17. [Scaling Considerations](#17-scaling-considerations)
18. [Common Failures & Fixes](#18-common-failures--fixes)
19. [Known Limitations](#19-known-limitations)
20. [Glossary](#20-glossary)

---

## 1. Plain-Language Overview

AIFeeders is an automated system that:

1. Wakes up every day at **10:00 AM UTC (3:30 PM IST)**
2. Searches the internet for the latest AI news (via GNews, 48-hour window, 2 API keys with auto-rotation)
3. Picks one important story
4. Writes a structured summary using an LLM (`qwen2-5-72b-instruct`)
5. Generates five different "human perspectives" on that story: Capitalist Mind, Working Professional Mind, Government Mind, Young/Fresher Mind, Techies Mind
6. Checks the content for safety issues (hallucination, PII, toxicity, injection attacks) — using an **enriched source** so short GNews content doesn't produce false hallucination scores
7. Posts everything to LinkedIn as a single rich-text post with the headline on its own line so LinkedIn's feed card preview always shows it

**Think of it as:** a fully automated, AI-powered editorial team that writes and publishes a daily AI news digest — without anyone doing anything manually.

---

## 2. POC Journey — How We Got Here

Understanding how we got here explains many design decisions.

### Phase 1 — POC: "Can we even run LLMs on OpenShift?"

**Goal:** Prove that an LLM workflow can run on OpenShift and publish to LinkedIn.

What we built:
- A simple LangGraph graph with 3 nodes: fetch news → summarise → publish
- Used GNews free tier for news (100 req/day limit discovered here)
- Used IBM's internal LLM gateway (`qwen2-5-72b-instruct`) — OpenAI-compatible API so LangChain works out of the box
- Discovered the LLM gateway has a self-signed cert — set `verify=False` on all httpx calls to it
- Got the first test post onto LinkedIn ✅

**Problems hit:**
- OTEL tracing crashed the container — no collector deployed. Fixed by setting `OTEL_EXPORTER_OTLP_ENDPOINT=""` to disable it
- Logs were invisible — uvicorn silenced the root logger. Fixed by adding `logging.basicConfig()` at module import time

### Phase 2 — MCP Architecture: "Split concerns into microservices"

**Goal:** Make each concern independently deployable and testable.

What we built:
- Extracted all external API calls into **4 MCP servers** (news, pageindex, evaluation, linkedin)
- Each MCP server is a FastAPI app exposing tools over HTTP at `POST /call`
- The main LangGraph workflow calls these via HTTP — it doesn't talk to external APIs directly
- Added evaluation as its own MCP server with 6 guardrail layers

**Problems hit:**
- GNews free plan: `hours=24` returned empty results for complex queries. Changed to `hours=48` ✅
- LinkedIn Comments API blocked — requires a separate LinkedIn product approval. Embedded all personas directly in the post body instead ✅

### Phase 3 — Production Hardening: "Make it reliable"

**Goal:** Fix all the subtle bugs and make it run stably as a CronJob.

Fixes made in this phase:
- **REGENERATE loop never ended** — `retry_count` was not being incremented. Fixed by incrementing in the `evaluate` node itself.
- **Same-day idempotency collision** — running the workflow twice on the same day produced the same `publication_key`, so the second run returned a cached wrong URN. Fixed by appending the last 8 characters of `run_id` to the key.
- **Blank LinkedIn posts (markdown)** — Using `*bold*` markdown renders as literal asterisks. Changed to ALL-CAPS labels and emoji for visual structure.
- **Evaluation thresholds too strict** — qwen2-5-72b self-evaluation scores for news content cluster around 0.5–0.7, not 0.9+. Lowered thresholds: factuality ≥ 0.50, groundedness ≥ 0.50, hallucination ≤ 0.85.
- **Persona names too generic** — renamed: Business Mind → Capitalist Mind; Labor → Working Professional Mind; Policy → Government Mind; Gen Z → Young/Fresher Mind; Tech → Techies Mind.
- **GNews key quota exhausted** — added automatic key rotation from `GNEWS_API_KEY` to `GNEWS_API_KEY_2` on HTTP 403.
- **Evaluation hallucination=1.0** — GNews free plan truncates content to ~250 chars, so the evaluator scored all persona content as hallucinated. Fixed by enriching the source text with all `NewsSummary` fields before evaluation (`_enrich_source()` in `evaluation_agent.py`).
- **Blank LinkedIn feed card** — First line `🤖 AI NEWS | <long headline>` was clipped by LinkedIn mobile at the emoji. Fixed by putting the headline on its own line (line 2).
- **Comment PERMISSION_ERROR spam** — 5 ERROR logs per run for a known-permanent 403. Changed to one INFO log + `_comments_blocked=True` flag to skip remaining calls.

### Phase 4 — Multi-Platform & Helm

**Goal:** Make deployment repeatable across OpenShift, EKS, AKS.

What we built:
- A Helm chart with 18 templates covering all services
- A 21-step `deploy.sh` script with environment-specific `values.yaml`
- Platform-abstracted image registry configuration

---

## 3. What Runs Where

The platform runs in the `aifeeders` namespace.

### Services (always running)

| Service | Pod label | Port | Purpose |
|---------|-----------|------|---------|
| `daily-news-api` | `app=daily-news-api` | 8000 | REST API for manual triggers and status checks (HPA: 2–10 replicas) |
| `news-mcp` | `app=news-mcp` | 8000 | Fetches AI news from GNews API (2 keys, auto-rotation) |
| `pageindex-mcp` | `app=pageindex-mcp` | 8000 | In-memory article document store and retrieval (**replicas: 1**) |
| `evaluation-mcp` | `app=evaluation-mcp` | 8000 | 6-layer guardrail pipeline |
| `linkedin-mcp` | `app=linkedin-mcp` | 8000 | LinkedIn OAuth + post publishing (**replicas: 1**) |

### CronJob (runs once per day)

| Resource | Schedule | What It Runs |
|----------|---------|--------------|
| `daily-ai-news` | `0 10 * * *` UTC | `python -m daily_news.workflow_runner` |

The CronJob pod gets label `app=daily-news-worker` which the NetworkPolicy uses to grant access to MCP servers.

### External dependencies

| System | Used For | Limit / Notes |
|--------|----------|---------------|
| GNews API | Search for AI news articles | 100 req/day free per key; resets midnight UTC. 2 keys supported. |
| IBM LLM Gateway | `qwen2-5-72b-instruct` for summarisation, personas, and guardrail evaluation | Self-signed cert → `verify=False` on all httpx calls |
| LinkedIn API | Publishing posts | 3000 char limit; API version `202609` |
| Langfuse | LLM trace logging via `get_langfuse_callback` + `start_span` | Optional; disabled if keys absent |

---

## 4. Day-to-Day Operation

**Normal days:** Nothing to do. The CronJob fires at 10:00 AM UTC, runs for 3–10 minutes, and a post appears on LinkedIn.

**Things to monitor:**
- Check LinkedIn profile at ~10:15 AM UTC to confirm a new post appeared
- If no post: check CronJob logs (see [Section 9](#9-reading-the-logs))
- Token expiry: LinkedIn tokens last 60 days. Set a calendar reminder to re-authorise.

**GNews quota:** The free plan allows 100 requests/day. The workflow issues 6 GNews queries per run (one per AI category). That is 6 requests per CronJob invocation. With 2 keys (`GNEWS_API_KEY` + `GNEWS_API_KEY_2`) effective quota is 200/day. If key 1 is exhausted, `news-mcp` automatically switches to key 2 on the next 403 response.

**Local development note:** `docker-compose.yaml` runs all 5 services plus two optional containers: `langflow` (visual workflow dev at port 7860) and `brave-search-mcp` (stdio Brave Search bridge, only starts with `--profile inspect`). Neither is deployed to OpenShift.

---

## 5. First-Time Setup (Fresh Cluster)

Follow this exactly if deploying to a new OpenShift cluster.

### 5.1 Prerequisites

```bash
oc whoami
oc project aifeeders  # or: oc new-project aifeeders
```

### 5.2 Create ConfigMap and Secrets

```bash
# Review and edit if your LLM gateway URL differs
nano openshift/configmap.yaml

# Fill in ALL real secret values (NEVER commit real values)
# Required fields: LLM_API_KEY, GNEWS_API_KEY, GNEWS_API_KEY_2 (optional),
# LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET,
# LINKEDIN_ACCESS_TOKEN, LINKEDIN_REDIRECT_URI
nano openshift/secrets.yaml

oc apply -f openshift/configmap.yaml
oc apply -f openshift/secrets.yaml
```

### 5.3 Create RBAC

```bash
oc apply -f openshift/rbac.yaml
```

### 5.4 Create ImageStream build configs (first time only)

```bash
for svc in daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  oc create imagestream $svc -n aifeeders 2>/dev/null || true
  oc new-build --name=$svc --binary -n aifeeders 2>/dev/null || true
done
```

### 5.5 Build images

```bash
oc start-build daily-news     --from-dir=. -n aifeeders --follow
oc start-build news-mcp       --from-dir=mcp_servers/news_mcp -n aifeeders --follow
oc start-build pageindex-mcp  --from-dir=mcp_servers/pageindex_mcp -n aifeeders --follow
oc start-build evaluation-mcp --from-dir=mcp_servers/evaluation_mcp -n aifeeders --follow
oc start-build linkedin-mcp   --from-dir=mcp_servers/linkedin_mcp -n aifeeders --follow
```

### 5.6 Deploy services

```bash
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/
oc apply -f openshift/api/
```

### 5.7 Apply network policies, HPA, PDB

```bash
oc apply -f openshift/networkpolicy.yaml
oc apply -f openshift/hpa.yaml
oc apply -f openshift/pdb.yaml
```

### 5.8 Create the CronJob

```bash
oc apply -f openshift/cronjob.yaml
```

### 5.9 Authorise LinkedIn

```bash
open https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start
# Complete the consent screen in the browser
```

### 5.10 Verify everything is healthy

```bash
oc get pods -n aifeeders
# Expected: 7 pods Running
# (2× daily-news-api, 2× news-mcp, 1× pageindex-mcp, 2× evaluation-mcp, 1× linkedin-mcp)

CLUSTER="apps.<your-cluster>"
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  echo "=== $svc ===" && curl -s https://${svc}-aifeeders.$CLUSTER/health | python3 -m json.tool
done
```

### 5.11 Trigger a smoke test (no publishing)

```bash
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"false"}}'

oc create job --from=cronjob/daily-ai-news smoke-test-1 -n aifeeders
oc logs -f job/smoke-test-1 -n aifeeders

oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

---

## 6. Building & Deploying New Code

### When to rebuild

Rebuild is needed when you change:
- Any Python source file in `src/`
- Any MCP server in `mcp_servers/`
- Any prompt in `prompts/`
- `Dockerfile` or `pyproject.toml`

**No rebuild needed** when you only change:
- `openshift/configmap.yaml` — apply with `oc apply`
- `openshift/secrets.yaml` — apply with `oc apply`, then restart affected pods
- `openshift/cronjob.yaml` schedule — apply with `oc apply`

### Rebuild and redeploy

```bash
# 1. Rebuild the main image (includes src/ and prompts/)
oc start-build daily-news --from-dir=. -n aifeeders --follow

# 2. Restart the API deployment to pick up the new image
oc rollout restart deployment/daily-news-api -n aifeeders

# 3. Wait for rollout
oc rollout status deployment/daily-news-api -n aifeeders

# 4. If you changed an MCP server, rebuild and restart it
oc start-build news-mcp --from-dir=mcp_servers/news_mcp -n aifeeders --follow
oc rollout restart deployment/news-mcp -n aifeeders
```

### Check what image is running

```bash
oc get deployment daily-news-api -n aifeeders \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
```

---

## 7. LinkedIn OAuth — Authorise & Reauthorise

### Why this is needed

LinkedIn access tokens expire after 60 days. When expired, the LinkedIn MCP server returns `AUTH_ERROR` (HTTP 401) on every post attempt.

### How to reauthorise (step by step)

1. **Open the OAuth start URL in your browser:**
   ```
   https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start
   ```

2. **LinkedIn shows a permission screen.** Sign in as the LinkedIn account owner and click "Allow".

3. **LinkedIn redirects to the callback URL.** The pod intercepts this, exchanges the code for a token, and stores it in memory. You will see a success page.

4. **Verify the token is active:**
   ```bash
   curl https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/status
   ```
   Expected: `"status": "ok"`, `"expired": false`, `"scopes_sufficient": true`

5. **Persist the token** so it survives pod restarts:
   ```bash
   oc patch secret daily-news-secrets -n aifeeders \
     --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<new-token>"}}'

   oc rollout restart deployment/linkedin-mcp -n aifeeders
   ```

### What scopes are needed

| Scope | Required For |
|-------|-------------|
| `openid` | OAuth login identity |
| `profile` | Fetching your LinkedIn person URN |
| `email` | Secondary identity verification |
| `w_member_social` | Creating posts |

If `w_member_social` is missing, posts fail with `PERMISSION_ERROR` (HTTP 403).

### LinkedIn app details

- **Client ID:** _(see LinkedIn Developer Portal → your app)_
- **Redirect URI:** `https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/callback`
- **LinkedIn Developer Portal:** https://www.linkedin.com/developers/apps/

---

## 8. Triggering a Manual Run

### Via OpenShift CLI (recommended)

```bash
oc create job --from=cronjob/daily-ai-news manual-$(date +%s) -n aifeeders
oc get pods -l job-name=manual-<id> -n aifeeders -w
oc logs -f job/manual-<id> -n aifeeders
```

### Via the REST API

```bash
# POST with no body — run_id is auto-generated server-side
curl -X POST \
  https://daily-news-api-aifeeders.apps.<cluster>/workflow/daily-news

# Returns: {"run_id": "RUN-XXXX", "status": "STARTED"}
# Poll status:
curl https://daily-news-api-aifeeders.apps.<cluster>/workflow/RUN-XXXX
```

> **Important:** The `/workflow/daily-news` endpoint takes **no request body**. `run_id` is generated server-side and returned in the response.

### With publishing disabled (safe test)

```bash
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"false"}}'

oc create job --from=cronjob/daily-ai-news nopost-$(date +%s) -n aifeeders
oc logs -f job/nopost-<id> -n aifeeders

oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

---

## 9. Reading the Logs

### Log format

```
2026-09-22 10:03:14 INFO  daily_news.workflows.daily_news_graph — [RUN-ABC123] discover_news started
```

Fields: `timestamp level module — [run_id] message`

### What to look for in a successful run

```
[RUN-xxx] discover_news started
[RUN-xxx] discovered 45 raw articles
[RUN-xxx] deduplicated: 45 → 38
[RUN-xxx] selected 1 story for summarisation
[RUN-xxx] summarised 1 articles
[RUN-xxx] eval article=news-abc decision=PASS factuality=0.86 groundedness=0.86 hallucination=0.40
[RUN-xxx] publishing main post article=news-abc key=news-abc:2026-09-22:78173e...:4FC7A6A1
[RUN-xxx] post published post_urn=urn:li:share:7508xxxxxx status=published
[RUN-xxx] Comments API not available (PERMISSION_ERROR) — personas are embedded in post body. Skipping remaining comment attempts.
Workflow complete — status=PUBLISHED published=1 errors=0
```

### What a REGENERATE loop looks like

```
[RUN-xxx] eval article=news-abc decision=REGENERATE factuality=0.42 groundedness=0.48 hallucination=0.22
[RUN-xxx] REGENERATE decision — retry 1/2
[RUN-xxx] summarised 1 articles       ← second summarize run
[RUN-xxx] eval article=news-abc decision=PASS factuality=0.71 ...
```

If you see `max retries (2) exceeded`, the workflow publishes any PASS items and skips the rest.

### What a blocked post looks like

```
[RUN-xxx] eval article=news-abc decision=BLOCK ...
[RUN-xxx] publish_eligible=false (decision=BLOCK) — skipping article=news-abc
Workflow complete — status=PUBLISHED published=0 errors=0
```

A blocked post is **not an error** — it is a guardrail doing its job.

### What a GNews key rotation looks like

```
WARNING news-mcp: GNews key #1 quota exhausted (403) — rotating to key #2
[RUN-xxx] discovered 5 raw articles   ← successful after rotation
```

### Common log commands

```bash
# Latest CronJob run
oc logs -l app=daily-news-worker --tail=100 -n aifeeders

# Live follow for manual runs
oc logs -f job/manual-<id> -n aifeeders

# All API pods
oc logs -l app=daily-news-api --tail=50 -n aifeeders

# LinkedIn MCP (publishing errors)
oc logs -l app=linkedin-mcp --tail=50 -n aifeeders

# Evaluation MCP (guardrail scores)
oc logs -l app=evaluation-mcp --tail=50 -n aifeeders

# news-mcp (key rotation events)
oc logs -l app=news-mcp --tail=50 -n aifeeders
```

---

## 10. Checking Health of Each Service

Every service exposes `/health` (liveness) and `/ready` (readiness):

```bash
CLUSTER="apps.<your-cluster>"
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  echo "=== $svc ===" && curl -s https://${svc}-aifeeders.$CLUSTER/health | python3 -m json.tool
done
```

### What each health response tells you

**news-mcp:**
```json
{
  "status": "healthy",
  "keys_configured": 2,        ← number of GNews keys in pool
  "active_key_index": 2,       ← 1 = primary active, 2 = rotated to secondary
  "active_key_prefix": "e6f0db13...",
  "max_per_request": 10
}
```

**evaluation-mcp:**
```json
{
  "status": "healthy",
  "llm_configured": true,      ← false = LLM_API_KEY missing (stub mode)
  "guardrail_layers": {
    "prompt_injection": "active",
    "factuality": "active",
    "hallucination": "active",
    "pii": "active",
    "policy": "active",
    "social_media_format": "active"
  }
}
```

**linkedin-mcp:**
```json
{
  "status": "healthy",
  "token_configured": true,
  "token_expired": false,
  "scopes_sufficient": true,    ← false = w_member_social missing
  "api_version": "202609",
  "last_post_status": {
    "post_urn": "urn:li:share:...",
    "status": "published",
    "http_status": 201
  }
}
```

---

## 11. How the Workflow Flows (Step by Step)

### Step 1: CronJob triggers `workflow_runner.py`

The pod runs `python -m daily_news.workflow_runner`. This calls `setup_tracing()` (initialises Langfuse if keys are present), creates a new `run_id` (e.g. `RUN-A3B4C5D6E7F8`), and calls `daily_news_graph.ainvoke(state)`.

### Step 2: `discover_news` — fetch articles from GNews

Calls `news-mcp` at `http://news-mcp:8000/call` six times, once per category:

| Category | Query |
|----------|-------|
| AI_TECHNOLOGY | "artificial intelligence LLM agentic AI model" |
| AI_BUSINESS | "artificial intelligence finance investment funding fintech" |
| AI_BUSINESS | "AI enterprise automation business productivity" |
| AI_JOBS | "AI jobs employment automation workforce reskilling" |
| AI_POLICY | "AI regulation policy governance AI Act" |
| AI_PRODUCTS | "AI product launch release announcement" |

Each call fetches up to 10 articles from GNews with `from=now-48h`. If the primary key returns 403, `news-mcp` automatically rotates to the secondary key and retries.

### Step 3: `deduplicate` — remove identical articles

Articles are deduplicated by MD5 hash of `title + url`. Typically reduces from ~50 to ~35 unique articles.

### Step 4: `fetch_articles` — enrich with full content

Fetches content for up to 30 articles. On GNews free plan, content is already truncated to ~250 chars in the search result.

### Step 5: `index_pageindex` — store in document tree

Each article is indexed into `pageindex-mcp`. The server splits the article into sections and stores them in-memory keyed by `article_id`. This enables context retrieval for summary and persona generation.

### Step 6: `select_stories` — pick 1 article

`selected_articles[:1]` — the first article from the enriched list. Keeps the pipeline fast and the LinkedIn post focused.

### Step 7: `summarize` — LLM writes a structured summary

1. Calls `pageindex-mcp.get_relevant_sections()` for "key business and technology facts"
2. Combines article metadata + top sections as context
3. Calls `qwen2-5-72b-instruct` with `prompts/summary.txt`
4. Gets back a structured JSON: `headline`, `summary`, `key_points`, `business_impact`, `job_impact`, `technology_impact`, `policy_impact`, `source`, `source_url`

### Step 8: `generate_personas` — LLM generates 5 perspectives (parallel)

`PersonaAgentFactory` runs 5 LLM calls **concurrently** via `asyncio.gather` (reduces latency from ~50s to ~12s):

| Persona | Prompt File | Viewpoint |
|---------|------------|-----------|
| Capitalist Mind | `prompts/capitalist.txt` | Revenue, investment, productivity, market disruption |
| Working Professional Mind | `prompts/labor.txt` | Jobs, reskilling, workforce effects |
| Government Mind | `prompts/policy.txt` | Regulation, governance, policy implications |
| Young/Fresher Mind | `prompts/genz.txt` | Education, career, future prospects |
| Techies Mind | `prompts/linkedin.txt` | Technical depth, architecture, developer impact |

Each persona produces a `perspective` (2-3 sentences) and `evidence` (1-3 bullet points).

### Step 9: `evaluate` — run 6-layer guardrail pipeline

The `EvaluationAgent`:

1. Composes generated text: `headline + summary + 5 perspectives`
2. **Enriches source text** using `_enrich_source()`: combines raw article content (~250 chars) with all `NewsSummary` fields (headline, summary, key_points, business/job/tech/why impacts). This prevents false hallucination scores from near-empty source text.
3. Calls `evaluation-mcp` via `POST /call` → tool `evaluation_evaluate_all`
4. The MCP server runs 6 layers and returns scores
5. `EvaluationAgent._apply_gate()` applies ConfigMap thresholds to produce a final `decision`

### Step 10: `publish` — compose and post to LinkedIn

Only articles with `decision == PASS` reach this node. The `PublisherAgent`:

1. Composes the post text (max 2990 chars) with headline on its own line:
   ```
   Line 0: 🤖 AI NEWS
   Line 1: <headline>
   Line 2: (blank)
   Line 3: <summary paragraph>
   ...
   ```
2. Generates an idempotency key: `{article_id}:{date}:{headline_hash[:12]}:{run_id[-8:]}`
3. Calls `linkedin-mcp.linkedin_create_post(text, publication_key)` — LinkedIn returns HTTP 201 + post URN
4. Attempts comments (5 personas) — on first `PERMISSION_ERROR` (403), sets `_comments_blocked=True`, logs INFO once, and skips remaining calls silently

---

## 12. Guardrails — What Gets Blocked and Why

### When a post gets BLOCKED

A `BLOCK` decision means the content is **permanently excluded** — no retry.

Triggers:
- **PII detected:** Any email, phone, SSN, credit card, credential pattern in the generated text
- **Prompt injection detected (HIGH/MEDIUM):** Text contains patterns attempting to override LLM instructions
- **Toxicity score > 0.3:** LLM policy check rates content as offensive or harmful

### When a post gets REGENERATE

The LLM re-runs `summarize` and `generate_personas` (maximum 2 retries).

Triggers:
- Factuality score < 0.50
- Groundedness score < 0.50
- Hallucination score > 0.85
- Format violations (post > 3000 chars, unsafe URL, duplicate post hash)

> **Note:** With source enrichment in place, hallucination=1.0 false positives no longer occur for real GNews articles. If you still see persistent REGENERATE, lower the thresholds (see [Section 15](#15-changing-configuration-no-rebuild)).

### When a post gets HUMAN_REVIEW

Excluded from auto-publish. Currently not wired to a human approval queue — items are skipped. Future work: expose `/approval/{article_id}/approve`.

Triggers: Political bias detected, or policy violations flagged.

### Calibration note

Current live thresholds (set in ConfigMap): `factuality ≥ 0.50`, `groundedness ≥ 0.50`, `hallucination ≤ 0.85`

These were calibrated empirically from qwen2-5-72b-instruct self-evaluation scores on real AI news. The LLM scores news conservatively (specific numbers and names are hard to verify against truncated source text). With source enrichment in place, typical scores on real articles: `factuality≈0.85`, `groundedness≈0.85`, `hallucination≈0.40`.

---

## 13. The LinkedIn Post Format

LinkedIn does not render Markdown. `*bold*` appears as literal asterisks. The post uses:

- **ALL-CAPS labels** for section headers (`KEY POINTS`, `PERSPECTIVES`, `CAPITALIST MIND`)
- **Emoji** for visual bullet markers (`📌`, `📈`, `👷`, `🔬`, `🏛️`, `🧠`, `▸`, `•`)
- **Plain text** for all body content
- **Line breaks** for spacing

### Post structure (line by line)

```
Line 0:  🤖 AI NEWS                          ← short, always visible in feed card
Line 1:  <headline>                           ← on its own line — visible in preview
Line 2:  (blank)
Line 3:  <2-3 sentence summary>
Line 4:  (blank)
Line 5:  📌 KEY POINTS
Line 6+: • fact 1 / fact 2 / fact 3
         (blank)
         📈 Business — ...
         👷 Jobs     — ...
         🔬 Tech     — ...
         🏛️ Policy   — ... (only if article discusses policy)
         (blank)
         🔗 <source URL>
         ─────────────────────────────────
         🧵 PERSPECTIVES
         (blank)
         1/5  💼  CAPITALIST MIND
         <perspective> + ▸ evidence
         ...
         5/5  🧠  TECHIES MIND
         ─────────────────────────────────
         ⚠️ DISCLAIMER: AI-simulated perspectives...
         🤖 Built with AIFeeders · Powered by Agentic AI
         (blank)
         #AI #AgenticAI ... (14 hashtags)
```

> **Why headline on its own line?** LinkedIn mobile clips the first line at the emoji glyph when it is long. Previously `🤖 AI NEWS  |  <headline>` showed as just `🤖 AI NEWS` with blank content below. With headline on line 2, the feed card preview shows both lines.

### Character budget (≤ 3000 total)

| Section | Approximate chars |
|---------|------------------|
| `🤖 AI NEWS` + headline | ~80 |
| Summary paragraph | ~250 |
| Key points | ~200 |
| Impact lines (business/jobs/tech) | ~300 |
| Source URL | ~100 |
| Separator + PERSPECTIVES header | ~50 |
| 5 persona perspectives (5 × ~220) | ~1100 |
| Disclaimer | ~250 |
| Hashtags (14) | ~150 |
| **Total typical** | **~2480** |

Hard-truncated at 2990 chars if LLM output is unexpectedly long.

### Hashtags used (14 total)

`#AI #AgenticAI #ArtificialIntelligence #LLM #GenerativeAI #AINews #TechNews #FutureOfWork #AIStrategy #MachineLearning #AIInnovation #DigitalTransformation #AILeadership #AIAgents`

---

## 14. Evaluating a Run

### Check if a post was published

```bash
curl https://linkedin-mcp-aifeeders.apps.<cluster>/audit | python3 -m json.tool
```

Look for entries with `"status": "published"` and note the `post_urn`.

### Check Langfuse traces

Every workflow run appears as a trace in https://us.cloud.langfuse.com. Filter by `run_id` (visible in CronJob logs):

- `daily_news_workflow` (root)
  - `news.search_latest` × 6
  - `evaluate` with factuality / groundedness / hallucination scores
  - `publish` with `post_urn` + comments count

### Check post content without LinkedIn

Run workflow with `PUBLISHING_ENABLED=false` then read `final["linkedin_results"]`. Or run `publisher._compose_main_post(summary, personas)` directly in a pod:

```bash
oc exec deploy/daily-news-api -n aifeeders -- python3 -c "
import asyncio, sys; sys.path.insert(0,'/app/src')
# ... (see ARCHITECTURE.md Section 12 for full snippet)
"
```

---

## 15. Changing Configuration (No Rebuild)

These changes take effect without rebuilding images:

### Change evaluation thresholds

```bash
oc patch configmap daily-news-config -n aifeeders --patch \
  '{"data":{"EVAL_FACTUALITY_THRESHOLD":"0.60","EVAL_HALLUCINATION_THRESHOLD":"0.80"}}'
```

The CronJob picks up ConfigMap values when the pod starts — no restart needed for the next scheduled run.

### Change CronJob schedule

```bash
oc patch cronjob daily-ai-news -n aifeeders --patch \
  '{"spec":{"schedule":"0 9 * * *"}}'   # 9:00 AM UTC instead of 10:00 AM
```

### Disable publishing temporarily

```bash
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"false"}}'
```

### Change LinkedIn API version

```bash
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"LINKEDIN_API_VERSION":"202612"}}'
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

---

## 16. Rotating Secrets

### Rotate GNews API key (primary)

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"GNEWS_API_KEY":"<new-key>"}}'
oc rollout restart deployment/news-mcp -n aifeeders
```

### Add/rotate GNews API key (secondary — auto-failover)

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"GNEWS_API_KEY_2":"<new-key>"}}'
oc rollout restart deployment/news-mcp -n aifeeders
```

After restart, `news-mcp` health will show `keys_configured: 2`.

### Rotate LinkedIn access token

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<new-token>"}}'
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

### Rotate LLM API key

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LLM_API_KEY":"<new-key>"}}'
# Restart all services that call the LLM
oc rollout restart deployment/daily-news-api deployment/evaluation-mcp -n aifeeders
```

---

## 17. Scaling Considerations

### What can scale horizontally today

| Service | Can Scale? | Notes |
|---------|-----------|-------|
| `daily-news-api` | ✅ Yes (HPA) | Stateless REST API — scales 2–10 replicas automatically |
| `evaluation-mcp` | ✅ Yes | Stateless per-request |
| `news-mcp` | ✅ Yes | Stateless — GNews quota is per-API-key, not per-pod |
| `pageindex-mcp` | ⚠️ No | In-memory document store — multiple replicas lose each other's documents |
| `linkedin-mcp` | ⚠️ No | In-memory idempotency registry + token store — multiple replicas risk duplicate posts |

### What must stay at replicas: 1

- `pageindex-mcp` — Fix: back with Redis or PostgreSQL
- `linkedin-mcp` — Fix: back with a shared cache or use LinkedIn's own idempotency key header

### To publish more articles per day

Change `select_stories[:1]` to `select_stories[:3]` in [`src/daily_news/workflows/daily_news_graph.py`](src/daily_news/workflows/daily_news_graph.py). Increases GNews quota usage from 6 to 6 requests per run (articles are already fetched — selection only affects summarisation).

---

## 18. Common Failures & Fixes

### "No articles discovered"

**Symptom:** `discovered 0 raw articles`
**Cause:** GNews API key quota exhausted (100 req/day free plan)
**Fix:** Wait until midnight UTC for quota reset. If `GNEWS_API_KEY_2` is configured, check why rotation didn't fire: `curl https://news-mcp-aifeeders.apps.<cluster>/health | python3 -m json.tool` — look at `active_key_index`.

---

### "news-mcp returns 500 on /call"

**Symptom:** `Server error '500 Internal Server Error' for url 'http://news-mcp:8000/call'`
**Cause:** Tool name mismatch or unhandled exception in `_gnews_search`
**Fix:** Rebuild `news-mcp` image if the server code changed. The tool registry in `_TOOLS` supports both `news_search_latest` and `news.search_latest` (alias). Verify with: `curl http://news-mcp-aifeeders.apps.<cluster>/health`

---

### "Post FAILED AUTH_ERROR HTTP 401"

**Symptom:** LinkedIn API returns 401
**Cause:** Access token expired (60-day lifetime)
**Fix:** Re-run OAuth flow at `/oauth/start`. Update the secret with the new token.

---

### "Post FAILED PERMISSION_ERROR HTTP 403"

**Symptom:** LinkedIn API returns 403 on post creation
**Cause:** Token does not have `w_member_social` scope, OR LinkedIn app does not have "Share on LinkedIn" product enabled
**Fix:** Re-run OAuth flow making sure `w_member_social` is in requested scopes. Check LinkedIn app at https://www.linkedin.com/developers/apps/

> **Note:** PERMISSION_ERROR on **comments** (not posts) is expected and normal. The Comments API requires "Community Management API" product approval. The pipeline soft-skips this with a single INFO log — it is not a failure.

---

### "Post FAILED VALIDATION_ERROR HTTP 400"

**Symptom:** LinkedIn API returns 400 or 422
**Cause (400):** Post text exceeds 3000 chars, or malformed payload
**Cause (422):** Duplicate content — LinkedIn detects the post text matches a previously published post
**Fix:** Never retry 400 or 422 — they will not succeed. For 422 duplicate: change the article or wait until a new article is selected tomorrow.

---

### "Evaluation always returns REGENERATE"

**Symptom:** `decision=REGENERATE` on every run, `published=0`
**Root cause (now fixed):** Was caused by GNews content truncation (~250 chars) making hallucination scores appear as 1.0. `_enrich_source()` in `EvaluationAgent` now passes full `NewsSummary` fields as grounded context.
**If still occurring:** Check thresholds in ConfigMap:
```bash
oc get configmap daily-news-config -n aifeeders -o jsonpath='{.data}' | python3 -m json.tool | grep EVAL
```
Lower thresholds if needed:
```bash
oc patch configmap daily-news-config -n aifeeders --patch \
  '{"data":{"EVAL_FACTUALITY_THRESHOLD":"0.40","EVAL_HALLUCINATION_THRESHOLD":"0.90"}}'
```

---

### "LinkedIn post appears blank in feed card"

**Symptom:** LinkedIn feed shows `🤖 AI NEWS` and nothing else
**Root cause (now fixed):** Long first line `🤖 AI NEWS  |  <headline>` was clipped at the emoji by LinkedIn mobile renderer. The `|` separator and headline were hidden.
**Fix applied:** Headline is now on its own line (line 2). Current format:
```
Line 0: 🤖 AI NEWS
Line 1: <headline text>
```
**If you see this again:** Verify the composed post with `publisher._compose_main_post(summary, personas)` in the pod. Line 0 should be just `🤖 AI NEWS` and line 1 should be the bare headline.

---

### "CronJob pod shows ImagePullBackOff"

**Symptom:** Pod fails to start, `oc describe pod` shows `ImagePullBackOff`
**Cause:** Image not built or wrong tag
**Fix:** Rebuild the image:
```bash
oc start-build daily-news --from-dir=. -n aifeeders --follow
```

---

### "LinkedIn MCP pod restarts, token lost"

**Symptom:** Post returns mock URNs or AUTH_ERROR after pod restart
**Cause:** Token was obtained via OAuth but not persisted to the Secret
**Fix:** After every OAuth reauth, update the secret:
```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<token>"}}'
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

---

### "Workflow logs are empty / silent"

**Symptom:** CronJob pod completes but logs show nothing
**Cause:** `logging.basicConfig()` was not called before loggers were created
**Fix:** Already fixed — `logging.basicConfig()` is called at top of `workflow_runner.py` and `main.py`. If still silent, check `LOG_LEVEL` in ConfigMap is `INFO`, not `WARNING`.

---

## 19. Known Limitations

| Limitation | Impact | Workaround / Plan |
|------------|--------|------------------|
| GNews free plan: 100 req/day per key | Can't run more than ~15 manual tests per day (each uses 6 requests) | 2-key pool in place; use mock mode for local dev |
| GNews free plan: content truncated at ~250 chars | LLM summarises from limited context | `_enrich_source()` mitigates for evaluation; upgrade to paid GNews for full summary content |
| LinkedIn Comments API blocked | Personas embedded in post body, not as comments | Apply for "Community Management API" product at LinkedIn Developer Portal |
| `pageindex-mcp` in-memory only | Lost on pod restart; can't scale to >1 replica | Back with PostgreSQL |
| `linkedin-mcp` token in-memory | Lost on pod restart if not persisted | Persist via Secret rotation after each OAuth; pod reads it on restart |
| LLM self-signed cert | `verify=False` on all gateway calls | Install cert in pod trust store for stricter security |
| 1 article per CronJob run | Low content velocity | Change `[:1]` to `[:3]` in `select_stories` |
| Comments API permanently soft-skipped | No persona comments on LinkedIn post | Platform limitation; personas are fully in post body |

---

## 20. Glossary

| Term | Meaning |
|------|---------|
| **MCP** | Model Context Protocol — a standard for exposing tools to AI agents. In this project each MCP server is a FastAPI app; agents call tools via `POST /call` REST |
| **`POST /call`** | The REST dispatcher on every MCP server. Body: `{"tool": "name", "arguments": {...}}`. Returns `{"result": ...}`. Used by `MCPHTTPClient` |
| **LangGraph** | Library for building stateful multi-agent workflows as directed graphs. The 9-node workflow is a `StateGraph` compiled once at module load |
| **LangChain** | Library for building LLM chains. Used for `ChatPromptTemplate`, `ChatOpenAI`, `PydanticOutputParser` inside `SummaryAgent` and `PersonaAgent` |
| **run_id** | Unique identifier for one workflow execution. Format: `RUN-<12 hex chars>` |
| **publication_key** | Idempotency key. Format: `{article_id}:{date}:{headline_sha256[:12]}:{run_id[-8:]}`. Prevents duplicate posts |
| **post_urn** | LinkedIn's identifier for a published post (e.g. `urn:li:share:7508111836219797506`). From `x-restli-id` header |
| **guardrail** | One of 6 automated checks that must pass before content is allowed to be published |
| **PASS / REGENERATE / BLOCK / HUMAN_REVIEW** | The four active outcomes of the evaluation pipeline |
| **`_enrich_source()`** | Function in `evaluation_agent.py` that combines raw article content with all `NewsSummary` fields before evaluation, preventing false hallucination scores from GNews content truncation |
| **`_comments_blocked`** | Flag in `PublisherAgent.publish()` — set True on first PERMISSION_ERROR (403) from Comments API, causing remaining personas to be silently skipped |
| **key rotation** | Automatic failover in `news-mcp` from `GNEWS_API_KEY` to `GNEWS_API_KEY_2` when the primary key returns HTTP 403 (quota exhausted) |
| **evaluation-mcp** | The MCP server running 6 guardrail layers. Returns stub passing values when `LLM_API_KEY` is absent |
| **Langfuse** | LLM observability. Tracing via `get_langfuse_callback` (LangChain handler) and `start_span`. `langfuse_trace()` is a no-op stub |
| **OTEL** | OpenTelemetry. Disabled (`OTEL_EXPORTER_OTLP_ENDPOINT=""`). No OTEL collector in cluster |
| **qwen2-5-72b-instruct** | The LLM for summarisation, persona generation, and guardrail evaluation. IBM model gateway. OpenAI-compatible API, self-signed cert |
| **UBI9** | Red Hat Universal Base Image 9 — base OS for all container images |
| **`asyncio.gather`** | Python concurrency: 5 persona LLM calls run in parallel. Intentional and safe — each is stateless |
| **`verify=False`** | httpx TLS verification disabled for LLM gateway (self-signed cert) and intra-cluster MCP calls |
| **Langflow** | Visual workflow IDE. In `docker-compose.yaml` (port 7860) for local prototyping only. Not deployed to OpenShift |
