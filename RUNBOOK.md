# AIFeeders — Operations Runbook

**Audience:** Engineers, Platform Ops, or anyone who needs to understand, operate, or extend the AIFeeders platform end-to-end.

**Goal:** This runbook explains everything in plain language — from "what problem does this solve" through day-to-day operations and common failure scenarios.

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
2. Searches the internet for the latest AI news (via GNews, 48-hour window)
3. Picks one important story
4. Writes a structured summary using an LLM (qwen2-5-72b)
5. Generates five different "human perspectives" on that story (business, worker, government, student, tech engineer viewpoint)
6. Checks the content for safety issues (hallucination, PII, toxicity, injection attacks)
7. Posts everything to LinkedIn as a single rich-text post

**Think of it as:** a fully automated, AI-powered editorial team that writes and publishes a daily AI news digest — without anyone having to do anything manually.

---

## 2. POC Journey — How We Got Here

Understanding how we got here explains many design decisions.

### Phase 1 — POC: "Can we even run LLMs on OpenShift?"

**Goal:** Prove that an LLM workflow can run on OpenShift and publish to LinkedIn.

What we built:
- A simple LangGraph graph with 3 nodes: fetch news → summarise → publish
- Used GNews free tier for news (100 req/day limit discovered here)
- Used IBM's internal LLM gateway (`qwen2-5-72b-instruct`) — it uses an OpenAI-compatible API so LangChain works out of the box
- Discovered the LLM gateway has a self-signed cert — set `verify=False` on all httpx calls to it
- Got the first test post onto LinkedIn ✅

**Problems hit:**
- OTEL tracing crashed the container — no collector deployed. Fixed by setting `OTEL_EXPORTER_OTLP_ENDPOINT=""` to disable it
- Logs were invisible — uvicorn silenced the root logger. Fixed by adding `logging.basicConfig()` at module import time

### Phase 2 — MCP Architecture: "Split concerns into microservices"

**Goal:** Make each concern independently deployable and testable.

What we built:
- Extracted all external API calls into **4 MCP servers** (news, pageindex, evaluation, linkedin)
- Each MCP server is a FastAPI app that also exposes tools over HTTP at `/call`
- The main LangGraph workflow calls these via HTTP — it doesn't talk to external APIs directly
- Added evaluation as its own MCP server with 6 guardrail layers

**Problems hit:**
- GNews free plan: `hours=24` returned empty results for complex queries. Changed to `hours=48` ✅
- LinkedIn Comments API blocked — requires a separate LinkedIn product approval. Embedded all personas directly in the post body instead ✅

### Phase 3 — Production Hardening: "Make it reliable"

**Goal:** Fix all the subtle bugs and make it run stably as a CronJob.

Fixes made:
- **REGENERATE loop never ended** — `retry_count` was not being incremented. Fixed by incrementing in the `evaluate` node itself.
- **Same-day idempotency collision** — running the workflow twice on the same day produced the same `publication_key`, so the second run returned a cached (wrong) URN. Fixed by appending the last 8 characters of `run_id` to the key.
- **Blank LinkedIn posts** — Using `*bold*` markdown renders as literal asterisks. Changed to ALL-CAPS labels and emoji for visual structure.
- **Evaluation thresholds too strict** — qwen2-5-72b self-evaluation scores for news content cluster around 0.5–0.7, not 0.9+. Lowered thresholds: factuality ≥ 0.50, groundedness ≥ 0.50, hallucination ≤ 0.85.
- **Persona names too generic** — renamed: Business Mind → Capitalist Mind; Labor → Working Professional Mind; Policy → Government Mind; Gen Z → Young/Fresher Mind; Tech → Techies Mind.

### Phase 4 — Multi-Platform & Helm

**Goal:** Make deployment repeatable across OpenShift, EKS, AKS.

What we built:
- A Helm chart with 18 templates covering all services
- A 21-step `deploy.sh` script with environment-specific `values.yaml`
- Platform-abstracted image registry config

---

## 3. What Runs Where

The platform runs in the `aifeeders` namespace on the OpenShift cluster at:
`https://api.f80l034.fusion.tadn.ibm.com:6443`

### Services (always running)

| Service | Pod label | Port | Purpose |
|---------|-----------|------|---------|
| `daily-news-api` | `app=daily-news-api` | 8000 | REST API for manual triggers and status checks (HPA: 2–10 replicas) |
| `news-mcp` | `app=news-mcp` | 8000 | Fetches AI news from GNews API |
| `pageindex-mcp` | `app=pageindex-mcp` | 8000 | In-memory article document store and retrieval (**replicas: 1**) |
| `evaluation-mcp` | `app=evaluation-mcp` | 8000 | 6-layer guardrail pipeline |
| `linkedin-mcp` | `app=linkedin-mcp` | 8000 | LinkedIn OAuth + post/comment publishing (**replicas: 1**) |

### CronJob (runs once per day)

| Resource | Schedule | What It Runs |
|---------|---------|--------------|
| `daily-ai-news` | `0 10 * * *` UTC | `python -m daily_news.workflow_runner` |

The CronJob pod gets label `app=daily-news-worker` which the NetworkPolicy uses to grant access to MCP servers.

### External dependencies

| System | What We Use It For | Limit / Notes |
|--------|-------------------|---------------|
| GNews API | Search for AI news articles | 100 req/day free; resets midnight UTC |
| IBM LLM Gateway | qwen2-5-72b-instruct for summarisation, persona generation, and guardrail evaluation | Self-signed cert → `verify=False` on all httpx calls |
| LinkedIn API | Publishing posts | 3000 char limit; API version 202609 |
| Langfuse | LLM trace logging via `get_langfuse_callback` + `start_span` | Optional; disabled if keys absent |

---

## 4. Day-to-Day Operation

**Normal days:** Nothing to do. The CronJob fires at 10:00 AM UTC, runs for 3–10 minutes, and a post appears on LinkedIn.

**Things to monitor:**
- Check LinkedIn profile at ~10:15 AM UTC to confirm a new post appeared
- If no post: check CronJob logs (see [Section 9](#9-reading-the-logs))
- Token expiry: LinkedIn tokens last 60 days. Set a calendar reminder to re-authorise.

**GNews quota:** The free plan allows 100 requests/day and the workflow issues 6 GNews queries per run (one per AI category). That is 6 requests per CronJob invocation, leaving ~94 requests for manual test runs. Do not run more than ~15 manual test runs per day.

**Local development note:** `docker-compose.yaml` runs all 5 services plus two optional containers: `langflow` (visual workflow dev at port 7860) and `brave-search-mcp` (stdio Brave Search bridge, only starts with `--profile inspect`). Neither is deployed to OpenShift.

---

## 5. First-Time Setup (Fresh Cluster)

Follow this exactly if deploying to a new OpenShift cluster.

### 5.1 Prerequisites

```bash
# Check you are logged in
oc whoami
oc project aifeeders  # or create it: oc new-project aifeeders
```

### 5.2 Create ConfigMap and Secrets

Review values before applying:

```bash
# Edit configmap if your LLM gateway URL differs
nano openshift/configmap.yaml

# Fill in ALL real secret values (never commit real values)
# Key fields: LLM_API_KEY, GNEWS_API_KEY, LINKEDIN_CLIENT_ID,
# LINKEDIN_CLIENT_SECRET, LINKEDIN_ACCESS_TOKEN, LINKEDIN_REDIRECT_URI
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
# Create ImageStreams for all images
oc create imagestream daily-news -n aifeeders
oc create imagestream linkedin-mcp -n aifeeders
oc create imagestream news-mcp -n aifeeders
oc create imagestream evaluation-mcp -n aifeeders
oc create imagestream pageindex-mcp -n aifeeders
```

### 5.5 Build images

```bash
# Build the main API + CronJob worker image
oc new-build --name=daily-news --binary -n aifeeders
oc start-build daily-news --from-dir=. -n aifeeders --follow

# Build each MCP server
oc new-build --name=news-mcp --binary -n aifeeders
oc start-build news-mcp --from-dir=mcp_servers/news_mcp -n aifeeders --follow

oc new-build --name=pageindex-mcp --binary -n aifeeders
oc start-build pageindex-mcp --from-dir=mcp_servers/pageindex_mcp -n aifeeders --follow

oc new-build --name=evaluation-mcp --binary -n aifeeders
oc start-build evaluation-mcp --from-dir=mcp_servers/evaluation_mcp -n aifeeders --follow

oc new-build --name=linkedin-mcp --binary -n aifeeders
oc start-build linkedin-mcp --from-dir=mcp_servers/linkedin_mcp -n aifeeders --follow
```

### 5.6 Deploy services

```bash
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/
oc apply -f openshift/api/
```

### 5.7 Apply network policies

```bash
oc apply -f openshift/networkpolicy.yaml
```

### 5.8 Create the CronJob

```bash
oc apply -f openshift/cronjob.yaml
```

### 5.9 Authorise LinkedIn

```bash
# Open in browser — this starts the OAuth flow
open https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start
```

### 5.10 Verify everything is healthy

```bash
oc get pods -n aifeeders
# Expected: 5 pods Running (daily-news-api, news-mcp, pageindex-mcp, evaluation-mcp, linkedin-mcp)

curl https://daily-news-api-aifeeders.apps.<cluster>/health
curl https://linkedin-mcp-aifeeders.apps.<cluster>/health
```

### 5.11 Trigger a smoke test (no publishing)

```bash
# Temporarily disable publishing
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Trigger a test run
oc create job --from=cronjob/daily-ai-news smoke-test-1 -n aifeeders
oc logs -f job/smoke-test-1 -n aifeeders

# Re-enable publishing
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
- `openshift/configmap.yaml` (apply with `oc apply`)
- `openshift/secrets.yaml` (apply with `oc apply`, then restart pods)
- `openshift/cronjob.yaml` schedule (apply with `oc apply`)

### Rebuild and redeploy

```bash
# 1. Rebuild the main image (includes src/ and prompts/)
oc start-build daily-news --from-dir=. -n aifeeders --follow

# 2. Restart the API deployment to pick up the new image
oc rollout restart deployment/daily-news-api -n aifeeders

# 3. If you changed an MCP server, rebuild and restart it
oc start-build linkedin-mcp --from-dir=mcp_servers/linkedin_mcp -n aifeeders --follow
oc rollout restart deployment/linkedin-mcp -n aifeeders

# 4. Wait for rollout to complete
oc rollout status deployment/daily-news-api -n aifeeders
```

### Check what image is running

```bash
oc get deployment daily-news-api -n aifeeders \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
```

---

## 7. LinkedIn OAuth — Authorise & Reauthorise

### Why this is needed

LinkedIn access tokens expire after 60 days. The `LINKEDIN_ACCESS_TOKEN` secret holds the current token. When it expires, the LinkedIn MCP server will start returning `AUTH_ERROR` (HTTP 401) on every post attempt.

### How to reauthorise (step by step)

1. **Open the OAuth start URL in your browser:**
   ```
   https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/oauth/start
   ```

2. **LinkedIn shows a permission screen.** Sign in as the LinkedIn account owner and click "Allow".

3. **LinkedIn redirects to the callback URL.** The pod intercepts this, exchanges the code for a token, and stores it in memory. You will see a success page.

4. **Verify the token is active:**
   ```bash
   curl https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/oauth/status
   ```
   Expected: `"status": "ok"`, `"expired": false`, `"scopes_sufficient": true`

5. **Persist the token** so it survives pod restarts:
   ```bash
   # Get the current token value from the pod environment (only works immediately after OAuth)
   # Better: the OAuth callback page shows a "copy token" hint. Copy that value.
   
   oc patch secret daily-news-secrets -n aifeeders \
     --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<new-token>"}}'
   
   # Restart the pod so it picks up the new secret value
   oc rollout restart deployment/linkedin-mcp -n aifeeders
   ```

### What scopes are needed

| Scope | Required For |
|-------|-------------|
| `openid` | OAuth login identity |
| `profile` | Fetching your LinkedIn URN |
| `email` | Secondary identity verification |
| `w_member_social` | Creating posts and comments |

If `w_member_social` is missing from the granted scopes, posts will fail with `PERMISSION_ERROR` (HTTP 403).

### LinkedIn app details

- **Client ID:** `<your-linkedin-client-id>`
- **Redirect URI:** `https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/oauth/callback`
- **LinkedIn Developer Portal:** https://www.linkedin.com/developers/apps/

---

## 8. Triggering a Manual Run

### Via OpenShift CLI (recommended)

```bash
# Create a one-off job from the CronJob spec
oc create job --from=cronjob/daily-ai-news manual-$(date +%s) -n aifeeders

# Watch the pod come up
oc get pods -l job-name=manual-<id> -n aifeeders -w

# Follow the logs
oc logs -f job/manual-<id> -n aifeeders
```

### Via the REST API

```bash
# POST to trigger the workflow — no body required, run_id is auto-generated
curl -X POST \
  https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/workflow/daily-news

# Poll the run status using the returned run_id
curl https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/workflow/RUN-<id>
```

> **Important:** The `/workflow/daily-news` endpoint takes **no request body**. The `run_id` is generated server-side and returned in the response.

### With publishing disabled (safe test)

```bash
# Patch ConfigMap, run, restore
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

Every log line follows this format:
```
2025-09-22 10:03:14 INFO     daily_news.workflows.daily_news_graph — [RUN-ABC123] discover_news started
```

Fields: `timestamp level module — [run_id] message`

### What to look for in a successful run

```
2025-09-22 10:00:14 INFO  daily_news.workflows.daily_news_graph — [RUN-xxx] discover_news started
2025-09-22 10:00:28 INFO  daily_news.workflows.daily_news_graph — [RUN-xxx] discovered 45 raw articles
2025-09-22 10:00:29 INFO  daily_news.workflows.daily_news_graph — [RUN-xxx] deduplicated: 45 → 38
2025-09-22 10:00:35 INFO  daily_news.workflows.daily_news_graph — [RUN-xxx] selected 1 story for summarisation
2025-09-22 10:01:12 INFO  daily_news.workflows.daily_news_graph — [RUN-xxx] summarised 1 articles
2025-09-22 10:02:45 INFO  daily_news.workflows.daily_news_graph — [RUN-xxx] eval article=news-abc123 decision=PASS factuality=0.68 groundedness=0.71 hallucination=0.12
2025-09-22 10:03:01 INFO  daily_news.agents.publisher_agent     — [RUN-xxx] post published post_urn=urn:li:share:7508xxxxxx status=published
2025-09-22 10:03:02 INFO  daily_news.workflow_runner            — Workflow complete — status=PUBLISHED published=1 errors=0
```

### What a REGENERATE loop looks like

```
[RUN-xxx] eval article=news-abc123 decision=REGENERATE factuality=0.42 groundedness=0.48 hallucination=0.22
[RUN-xxx] REGENERATE decision — retry 1/2
[RUN-xxx] summarised 1 articles  ← second summarize run
[RUN-xxx] eval article=news-abc123 decision=PASS ...
```

### What a blocked post looks like

```
[RUN-xxx] eval article=news-abc123 decision=BLOCK ...
[RUN-xxx] publish_eligible=false (decision=BLOCK) — skipping article=news-abc123
Workflow complete — status=PUBLISHED published=0 errors=0
```

A blocked post is not an error — it is a guardrail doing its job.

### Common log commands

```bash
# Last 100 lines from the most recent CronJob pod
oc logs -l app=daily-news-worker --tail=100 -n aifeeders

# Live follow (for manual runs)
oc logs -f job/manual-<id> -n aifeeders

# All pods, last 50 lines
oc logs -l app=daily-news-api --tail=50 -n aifeeders
oc logs -l app=linkedin-mcp --tail=50 -n aifeeders
oc logs -l app=evaluation-mcp --tail=50 -n aifeeders
```

---

## 10. Checking Health of Each Service

Every service exposes a `/health` endpoint. All return `200 OK` with a JSON body when healthy.

```bash
CLUSTER="apps.f80l034.fusion.tadn.ibm.com"

curl -s https://daily-news-api-aifeeders.$CLUSTER/health | python3 -m json.tool
curl -s https://news-mcp-aifeeders.$CLUSTER/health | python3 -m json.tool
curl -s https://pageindex-mcp-aifeeders.$CLUSTER/health | python3 -m json.tool
curl -s https://evaluation-mcp-aifeeders.$CLUSTER/health | python3 -m json.tool
curl -s https://linkedin-mcp-aifeeders.$CLUSTER/health | python3 -m json.tool
```

### What each health response tells you

**news-mcp:**
```json
{
  "status": "healthy",
  "api_key_set": true,     ← false = GNEWS_API_KEY missing (mock mode)
  "max_per_request": 10
}
```

**evaluation-mcp:**
```json
{
  "status": "healthy",
  "llm_configured": true,  ← false = LLM_API_KEY missing (stub mode)
  "guardrail_layers": {
    "prompt_injection": "active",
    "factuality": "active",
    ...
  }
}
```

**linkedin-mcp:**
```json
{
  "status": "healthy",
  "token_configured": true,
  "token_expired": false,
  "scopes_sufficient": true,  ← false = w_member_social not in granted scope
  "api_version": "202609"
}
```

---

## 11. How the Workflow Flows (Step by Step)

Here is a complete walkthrough of what happens when the CronJob fires.

### Step 1: CronJob triggers `workflow_runner.py`

The pod runs:
```bash
python -m daily_news.workflow_runner
```

This calls `setup_tracing()` (initialises Langfuse if keys are present), creates a new `run_id` (e.g. `RUN-A3B4C5D6E7F8`), and calls `daily_news_graph.ainvoke(state)`.

---

### Step 2: `discover_news` — fetch articles from GNews

The node calls `news-mcp` at `http://news-mcp:8000/call` six times, once per category:

| Category | Example Query |
|----------|-------------|
| AI_TECHNOLOGY | "artificial intelligence LLM agentic AI model" |
| AI_BUSINESS | "AI enterprise automation business productivity" |
| AI_JOBS | "AI jobs employment automation workforce reskilling" |
| AI_POLICY | "AI regulation policy governance AI Act" |
| AI_PRODUCTS | "AI product launch release announcement" |

Each call fetches up to 10 articles from GNews with `from=now-48h`. Total raw articles: typically 40–60.

> **GNews free plan note:** Each of the 6 calls costs 1 API request. That is 6 of your 100 daily quota.

---

### Step 3: `deduplicate` — remove identical articles

Articles are deduplicated by MD5 hash of `title + url`. If the same story appears in multiple category searches, only the first copy is kept. Typically reduces from ~50 to ~35 unique articles.

---

### Step 4: `fetch_articles` — enrich with full content

The workflow fetches full article content (capped at 30 articles to control cost). On GNews free plan, content is already truncated to ~250 chars in the search result, so this step mainly adds any additional metadata from the article URL.

---

### Step 5: `index_pageindex` — store in document tree

Each article is indexed into `pageindex-mcp`. The server:
- Splits the article into paragraph-level sections
- Stores them in an in-memory dict keyed by `article_id`
- Returns a count of sections indexed

This enables the next nodes to retrieve the most relevant sections for a given question, without using embeddings or a vector database.

---

### Step 6: `select_stories` — pick 1 article

`selected_articles[:1]` — the first article from the enriched, deduplicated list becomes the one article for this run. This keeps the pipeline fast and the LinkedIn post focused.

---

### Step 7: `summarize` — LLM writes a structured summary

The `SummaryAgent` does:
1. Calls `pageindex-mcp.get_relevant_sections()` for "key business and technology facts"
2. Combines article metadata + top sections as context
3. Calls `qwen2-5-72b-instruct` with the `prompts/summary.txt` prompt
4. Gets back a structured JSON with: `headline`, `summary`, `key_points`, `business_impact`, `job_impact`, `technology_impact`, `policy_impact`, `source`, `source_url`

The output is a `NewsSummary` Pydantic model stored in workflow state.

---

### Step 8: `generate_personas` — LLM generates 5 perspectives (parallel)

The `PersonaAgentFactory` runs 5 LLM calls **concurrently** via `asyncio.gather`. This is intentional — each is a stateless, independent prompt:

| Persona | Prompt File | Viewpoint |
|---------|------------|-----------|
| Capitalist Mind | `prompts/capitalist.txt` | Revenue, investment, productivity, market disruption |
| Working Professional Mind | `prompts/labor.txt` | Jobs, reskilling, workforce effects |
| Government Mind | `prompts/policy.txt` | Regulation, governance, policy implications |
| Young/Fresher Mind | `prompts/genz.txt` | Education, career, future prospects |
| Techies Mind | `prompts/linkedin.txt` | Technical depth, architecture, developer impact |

Each persona produces a `perspective` (2-3 sentences) and `evidence` (1-3 bullet points with direct facts).

Each persona also loads its own prompt file at init time (`_load_prompt("capitalist")` etc.) and uses `temperature=0.4` to allow some variability across runs.

---

### Step 9: `evaluate` — run 6-layer guardrail pipeline

The `EvaluationAgent` composites all generated text (headline + summary + all 5 perspectives) into a single string, then calls `evaluation-mcp` via `POST /call` → tool `evaluation_evaluate_all`. The MCP server runs:

1. **Prompt Injection check** — regex + LLM classifier scans for jailbreak patterns
2. **Factuality check** — LLM verifies all claims in generated text against source text
3. **Hallucination check** — LLM identifies statements not traceable to source
4. **PII check** — regex finds emails, phone numbers, SSNs, credit cards, credentials
5. **Policy check** — LLM checks for toxicity, political bias, brand safety issues
6. **Format check** — deterministic: char count ≤ 3000, no unsafe URLs, no duplicate hash

The `EvaluationAgent` applies thresholds (from ConfigMap) to produce a `decision`:
- `PASS` → content is safe and accurate; proceed to publish
- `REGENERATE` → quality below threshold; go back to `summarize` (max 2 retries)
- `BLOCK` → PII or injection detected; skip this article entirely
- `HUMAN_REVIEW` → political bias or policy violations; exclude from auto-publish

---

### Step 10: `publish` — compose and post to LinkedIn

Only articles with `decision == PASS` reach this node. The `PublisherAgent`:

1. Composes the post text (max 2990 chars):
   - News block: headline, summary, key points, business/jobs/tech/policy impacts, source URL
   - Separator line
   - 5 persona perspectives with ALL-CAPS headers and evidence bullets
   - Disclaimer notice
   - 14 hashtags

2. Generates an idempotency key:
   ```
   {article_id}:{today's date}:{headline hash[:12]}:{run_id[-8:]}
   ```

3. Calls `linkedin-mcp.linkedin_create_post(text, publication_key)` — LinkedIn returns HTTP 201 + post URN in `x-restli-id` header

4. Logs the post URN and status

The workflow exits with `workflow_status=PUBLISHED`.

---

## 12. Guardrails — What Gets Blocked and Why

### When a post gets BLOCKED

A `BLOCK` decision means the content is **permanently excluded** — the workflow does not retry it.

Triggers for BLOCK:
- **PII detected:** Any email address, phone number, SSN, credit card number, or credential pattern in the generated text. News articles sometimes contain email addresses in author bios — these would be caught here.
- **Prompt injection detected (HIGH/MEDIUM):** The generated text contains patterns that attempt to override LLM instructions. This shouldn't happen from legitimate news, but is a safety net against adversarial articles.
- **Toxicity score > 0.3:** The LLM policy check rates the content as potentially offensive or harmful.

What to do: If a legitimate article is being blocked, check the `evaluation-mcp` logs to see which layer is triggering. You can call the evaluation MCP directly to test:

```bash
curl -X POST http://evaluation-mcp-aifeeders.apps.<cluster>/call \
  -H "Content-Type: application/json" \
  -d '{"tool": "guardrail_pii", "arguments": {"text": "<paste generated text here>"}}'
```

> **Why do we evaluate at all?** The LLM can hallucinate, misattribute quotes, or introduce political bias — especially when generating persona perspectives from truncated GNews content (250 chars on free plan). The evaluation layer is the only automated check between LLM output and a public LinkedIn post with thousands of followers.

### When a post gets REGENERATE

The LLM re-runs the `summarize` and `generate_personas` nodes with the hope that a second attempt produces higher-quality content. Maximum 2 retries.

Triggers for REGENERATE:
- Factuality score < 0.50 (LLM found claims not supported by the source text)
- Groundedness score < 0.50 (LLM found text not grounded in the source)
- Hallucination score > 0.85 (LLM found too many unsupported statements)
- Format violations (post exceeds 3000 chars, unsafe URL, duplicate post hash)

### When a post gets HUMAN_REVIEW

The article is excluded from auto-publish. Currently not wired to a human approval queue — items are simply skipped. Future work: expose a `/approval/{article_id}/approve` endpoint.

Triggers: political bias detected in the policy check, or other policy violations.

### Calibration note

The thresholds were calibrated empirically from qwen2-5-72b-instruct self-evaluation scores on real AI news articles. The LLM tends to score news summaries lower than other content types (because news contains specific numbers and names that are hard to verify against truncated source text). Thresholds set too high (e.g. 0.90) cause most articles to hit REGENERATE and never get published.

Current live thresholds: `factuality ≥ 0.50`, `groundedness ≥ 0.50`, `hallucination ≤ 0.85`

---

## 13. The LinkedIn Post Format

LinkedIn does not render Markdown. `*bold*` appears as literal asterisks. The post uses:

- **ALL-CAPS labels** for section headers (`KEY POINTS`, `PERSPECTIVES`, `CAPITALIST MIND`)
- **Emoji** for visual bullet markers (`📌`, `📈`, `👷`, `🔬`, `🏛️`, `🧠`, `▸`, `•`)
- **Plain text** for all body content
- **Line breaks** for spacing

### Character budget (≤ 3000 total)

| Section | Approximate chars |
|---------|------------------|
| News headline + summary + key points | ~400 |
| Business / jobs / tech / policy impacts | ~300 |
| Source URL | ~100 |
| Separator + PERSPECTIVES header | ~50 |
| 5 persona perspectives (5 × ~220) | ~1100 |
| Disclaimer | ~250 |
| Hashtags (14) | ~150 |
| **Total** | **~2350** |

The post is hard-truncated at 2990 chars if the LLM output is unexpectedly long.

### Hashtags used (14 total)

`#AI #AgenticAI #ArtificialIntelligence #LLM #GenerativeAI #AINews #TechNews #FutureOfWork #AIStrategy #MachineLearning #AIInnovation #DigitalTransformation #AILeadership #AIAgents`

---

## 14. Evaluating a Run

### Check if a post was published

```bash
# Get the LinkedIn audit log (last 50 publish attempts)
curl https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/audit | python3 -m json.tool
```

Look for entries with `"status": "published"` and note the `post_urn`.

### Check Langfuse traces

If Langfuse is configured (keys in Secret), every workflow run appears as a trace in https://us.cloud.langfuse.com. Filter by `run_id` (visible in the CronJob logs) to see all spans for a single run.

Trace hierarchy:
- `daily_news_workflow` (root)
  - `news.search_latest` × 6
  - `evaluate` with scores
  - `publish` with post_urn + comments_posted count

### Check post content without LinkedIn

The `PublisherAgent._compose_main_post()` is pure Python — no side effects. To preview what a post would look like, call the workflow with `PUBLISHING_ENABLED=false` and print `final["linkedin_results"]`.

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
# Edit the schedule (uses standard 5-field cron syntax, UTC)
oc patch cronjob daily-ai-news -n aifeeders --patch \
  '{"spec":{"schedule":"0 9 * * *"}}'  # ← 9:00 AM UTC instead of 10:00 AM
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
# Restart linkedin-mcp so it picks up the new value
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

---

## 16. Rotating Secrets

Secrets stored in the OpenShift Secret `daily-news-secrets`.

### Rotate LinkedIn access token

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<new token>"}}'
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

### Rotate GNews API key

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"GNEWS_API_KEY":"<new key>"}}'
oc rollout restart deployment/news-mcp -n aifeeders
```

### Rotate LLM API key

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LLM_API_KEY":"<new key>"}}'
# Restart all services that call the LLM
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout restart deployment/evaluation-mcp -n aifeeders
```

---

## 17. Scaling Considerations

### What can scale horizontally today

| Service | Can Scale? | Notes |
|---------|-----------|-------|
| `daily-news-api` | ✅ Yes | Stateless REST API — scale to 2+ replicas freely |
| `evaluation-mcp` | ✅ Yes | Stateless per-request — scale freely |
| `news-mcp` | ✅ Yes | Stateless — scale freely (GNews quota is per-API-key, not per-pod) |
| `pageindex-mcp` | ⚠️ No | In-memory document store — multiple replicas lose each other's documents |
| `linkedin-mcp` | ⚠️ No | In-memory idempotency registry + token store — multiple replicas risk duplicate posts |

### What must stay at replicas: 1

- `pageindex-mcp` — articles indexed by one pod are invisible to other pods. Fix: back with Redis or PostgreSQL.
- `linkedin-mcp` — `_published_posts` dict is per-process. Fix: back with a shared cache or use LinkedIn's own idempotency key header.

### If you want to publish more articles per day

Currently: 1 article per CronJob run.

To increase: change `select_stories[:1]` to `select_stories[:3]` in [`src/daily_news/workflows/daily_news_graph.py`](src/daily_news/workflows/daily_news_graph.py). This increases GNews quota usage per run.

---

## 18. Common Failures & Fixes

### "No articles discovered"

**Symptom:** `discovered 0 raw articles`  
**Cause:** GNews API key quota exhausted (100 req/day free plan)  
**Fix:** Wait until midnight UTC for quota reset. Check: `curl "https://gnews.io/api/v4/search?q=AI&apikey=<key>&max=1"`

---

### "Post FAILED AUTH_ERROR HTTP 401"

**Symptom:** LinkedIn API returns 401  
**Cause:** Access token expired (60-day lifetime)  
**Fix:** Re-run OAuth flow at `/oauth/start`. Update the secret with the new token.

---

### "Post FAILED PERMISSION_ERROR HTTP 403"

**Symptom:** LinkedIn API returns 403  
**Cause:** Token does not have `w_member_social` scope, OR LinkedIn app does not have "Share on LinkedIn" product enabled  
**Fix:** Re-run OAuth flow making sure `w_member_social` is in requested scopes. Check LinkedIn app at https://www.linkedin.com/developers/apps/

---

### "Post FAILED VALIDATION_ERROR HTTP 400"

**Symptom:** LinkedIn API returns 400  
**Cause:** Post text exceeds 3000 chars, or malformed payload  
**Fix:** Check `len(text)` in logs. The code caps at 2990 — if you see 400, check for special characters or invisible Unicode in the post. Never retry 400 — it will not succeed.

---

### "Persona generation fails / some personas missing"

**Symptom:** Workflow reaches `generate_personas` but fails or produces incomplete `PersonaSetOutput`
**Cause:** One of the 5 concurrent LLM calls in `asyncio.gather` raised an exception
**Fix:** Check LLM gateway reachability. Increase `httpx.Client` timeout in `persona_agent.py` if the gateway is slow. As a temporary workaround, the 5 parallel calls can be made sequential by replacing `asyncio.gather` with sequential `await` calls.

---

### "All evaluations return REGENERATE, nothing publishes"

**Symptom:** Workflow runs but `published=0`, evaluation always REGENERATE  
**Cause:** Factuality or hallucination thresholds too strict for current LLM output quality  
**Fix:**
```bash
oc patch configmap daily-news-config -n aifeeders --patch \
  '{"data":{"EVAL_FACTUALITY_THRESHOLD":"0.40","EVAL_HALLUCINATION_THRESHOLD":"0.90"}}'
```

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
**Fix:** Already fixed — `logging.basicConfig()` is called at top of `workflow_runner.py` and `main.py`. If still silent, check `LOG_LEVEL` in ConfigMap is set to `INFO`, not `WARNING`.

---

## 19. Known Limitations

| Limitation | Impact | Workaround / Plan |
|------------|--------|------------------|
| GNews free plan: 100 req/day | Can't run more than ~15 manual tests per day | Use mock mode for local dev |
| GNews free plan: content truncated at 250 chars | LLM summarises from limited context | Upgrade to paid GNews plan for full content |
| LinkedIn Comments API blocked | Personas embedded in post body, not as comments | Apply for "Community Management API" product at LinkedIn Developer Portal |
| `pageindex-mcp` in-memory only | Lost on pod restart; can't scale to >1 replica | Back with PostgreSQL |
| `linkedin-mcp` token in-memory | Lost on pod restart | Persist via Secret rotation after each OAuth |
| LLM self-signed cert | `verify=False` on all gateway calls | Install cert in pod trust store for stricter security |
| 1 article per CronJob run | Low content velocity | Change `[:1]` to `[:3]` in `select_stories` |

---

## 20. Glossary

| Term | Meaning |
|------|---------|
| **MCP** | Model Context Protocol — a standard for exposing tools to AI agents. In this project each MCP server is a FastAPI app; agents call tools via `POST /call` REST, not the MCP streaming transport |
| **`POST /call`** | The REST dispatcher on every MCP server. Body: `{"tool": "tool_name", "arguments": {...}}`. Returns `{"result": ...}`. Used by `MCPHTTPClient` in `src/daily_news/mcp/client.py` |
| **LangGraph** | Library for building stateful multi-agent workflows as directed graphs. The 9-node workflow is a LangGraph `StateGraph` compiled once at module load |
| **LangChain** | Library for building LLM chains. Used here for `ChatPromptTemplate`, `ChatOpenAI`, and `PydanticOutputParser` inside `SummaryAgent` and `PersonaAgent` |
| **run_id** | Unique identifier for one CronJob execution. Format: `RUN-<12 hex chars>` (e.g. `RUN-A3B4C5D6E7F8`) |
| **publication_key** | Idempotency key. Format: `{article_id}:{date}:{headline_sha256[:12]}:{run_id[-8:]}`. Prevents duplicate posts |
| **post_urn** | LinkedIn's identifier for a published post (e.g. `urn:li:share:7508111836219797506`). From `x-restli-id` header |
| **guardrail** | One of 6 automated checks that must pass before content is allowed to be published |
| **PASS / REGENERATE / BLOCK / HUMAN_REVIEW** | The four active outcomes of the evaluation pipeline. `FAIL` also exists as an enum value but is unused in the current gate |
| **evaluation-mcp** | The MCP server running 6 guardrail layers. Returns stub passing values when `LLM_API_KEY` is absent |
| **Langfuse** | LLM observability. Tracing via `get_langfuse_callback` (LangChain handler) and `start_span`. `langfuse_trace()` is a no-op stub |
| **OTEL** | OpenTelemetry. Disabled (`OTEL_EXPORTER_OTLP_ENDPOINT=""`). No OTEL collector in cluster |
| **qwen2-5-72b-instruct** | The LLM for summarisation, persona generation, and guardrail evaluation. IBM model gateway. OpenAI-compatible API, self-signed cert |
| **UBI9** | Red Hat Universal Base Image 9 — base OS for all container images |
| **CronJob** | Kubernetes resource that runs a container on a schedule |
| **`asyncio.gather`** | Python concurrency: 5 persona LLM calls run in parallel. LinkedIn comments would be sequential (but Comments API is currently blocked) |
| **`verify=False`** | httpx TLS verification disabled for LLM gateway (self-signed cert) and all intra-cluster MCP calls |
| **Langflow** | Visual workflow IDE. In `docker-compose.yaml` (port 7860) for local prototyping only. Not deployed to OpenShift |
