# AIFeeders — AI Daily News Platform

> **Four personas. Jev System One AI decision engine. Publishes daily to LinkedIn automatically.**  
> Build #56 · OpenShift `aifeeders` · EKS-portable

---

## What It Does

AIFeeders runs every day at 10:00 UTC. It searches for AI news, scores articles with Jev System One, summarises the best two, generates four audience-specific perspectives, evaluates content quality, and publishes a structured LinkedIn post — automatically, with no human pressing a button.

**A published post looks like this:**

```
🤖  AI NEWS  ·  Agentic AI

AI Agents Reshape Enterprise Workflows Faster Than Predicted

A new cross-industry study finds agentic AI deployments are reducing
knowledge-work cycle times by 40%...

⚙️  Jev Decision
  💼  Primary audience : Business (market strategy & ROI)  — 93% Jev score
  📋  Story type       : Research Report
  🎯  AI relevance     : 91%   Market signal: [████░]
  ⚡  Engagement est.  : 74%   Controversy: Medium
  👥  Audience impact  : 💼 Business 93%  ›  🧠 Tech+Workforce 89%  ›  🎓 Generalist 76%
  🔥  AI market shift  : HIGH — potential to reshape the landscape.

📌  What you need to know
  • Agentic AI cut knowledge-work cycle times by 40% on average
  • Demand for AI orchestration engineers surged 380% year-on-year
  • Mid-level analyst roles saw 62% task automation exposure

📈  Business   —  Enterprises report 3x faster iteration and 28% lower headcount costs
👷  Workforce  —  Mid-level workers face 62% automation with reskilling 18 months behind
🔬  Tech       —  LangGraph and AutoGen are the de facto orchestration architecture
🏛️  Policy     —  EU AI Act transparency requirements apply to agentic systems now

🔗  Read more: https://...

🧵  Perspectives  ·  Jev-selected audience mindsets

💼  Capitalist Mind
A 40% cycle-time reduction is not incremental — it is a structural cost advantage.
  • 28% lower operational headcount costs reported by early adopters
  • 3x faster product iteration cited across 12 industries

🏛️  Government Mind
The EU AI Act's transparency obligations now cover agentic systems per March 2026 guidance.
  • EU AI Office guidance names agentic systems under Article 14
  • Enforcement begins Q1 2027 with no grace period

🎓  Generalist Mind
A 40% cut in cycle times is the kind of number used to justify headcount freezes.
  • LinkedIn postings for AI orchestration roles up 380% year-on-year
  • 62% of mid-level analyst tasks identified as automatable

🧠  Tech & Workforce Mind
The gains are real but come with significant prompt engineering and failure-mode overhead.
  • LangGraph and AutoGen cited as de facto enterprise frameworks
  • Observability tooling lagging deployment pace is a key risk

⚙️  Jev Decision  —  💼 Business Strategists  ›  🧠 Tech & Workforce  ›  🎓 Generalists

💬  Real Human Take — drop yours below. 👇

⚠️ AI-simulated perspectives — not verified opinions or professional advice.
🤖 AIFeeders  ·  Agentic AI  ·  Powered by Jev

#AI #AgenticAI #Jev #AINews #GenerativeAI #TechNews #MachineLearning
#AIStrategy #AIInnovation #DigitalTransformation #AILeadership
```

---

## How the Pipeline Works

```
CronJob (10:00 UTC)
  └─► discover_news        6 GNews queries → ~18 articles
        └─► deduplicate    SHA-256 hash dedup + PublishedStore filter (cross-run, 7-day TTL)
              └─► fetch_articles       Full HTML fetch + parse
                    └─► index_pageindex    RAG index for evidence retrieval
                          └─► jev_prefilter       [Jev Decision #1]
                                │  Scores all articles → picks top 2 by relevance + engagement
                                └─► summarise             LLM → structured summary
                                      └─► jev_route_personas  [Jev Decision #2]
                                            │  Picks which personas are relevant
                                            └─► generate_personas  (active subset only, parallel)
                                                  └─► evaluate     [Jev Decision #3]
                                                        │  Factuality / groundedness / hallucination
                                                        ├─► REGENERATE → summarise (max 2 retries)
                                                        └─► PASS → publish (3-gate dedup check)
                                                                    └─► LinkedIn post
```

**Jev System One** makes three decisions per run:
1. **Which articles matter** — scores all articles by relevance + engagement, picks top 2 (composite = relevance × 0.6 + engagement × 0.4)
2. **Which personas should respond** — routes only relevant personas, saves LLM cost (2–4 calls instead of always-4)
3. **Is the content safe to publish** — factuality ≥ 0.50, groundedness ≥ 0.50, hallucination ≤ 0.85; PII/injection → hard BLOCK

---

## Why Jev (Not Just Another LLM Call)

| Decision point | Before Jev | With Jev |
|---|---|---|
| Article selection | Blind `[:2]` — first 2 articles in GNews order | 11-dimension score per article in < 2s, picks the genuinely best ones |
| Persona routing | Always 4 LLM calls regardless of relevance | 1 Jev call → activates only relevant personas (e.g., 2 of 4 for a regulation story) |
| Content evaluation | LLM evaluating LLM output — expensive + hallucination risk | Jev scores factuality/groundedness/hallucination with calibrated floats, no free text |

Jev answers structured multi-question queries (`noul`, `choice`, `score` types) in one HTTP call to `POST /v1/systemone`. It returns float scores and named labels — never generated text. This makes every decision **fast** (< 2s), **cheap**, and **deterministic** (threshold comparisons on calibrated floats).

---

## The Four Personas

| Persona | Voice | Focus |
|---|---|---|
| 💼 **Capitalist Mind** | Founder/operator. P&L lens. Ends with a pointed decision question. | ROI, margins, competitive moat, build-vs-buy |
| 🏛️ **Government Mind** | Policy memo precision. Names specific regulations. | Compliance, cross-jurisdiction governance, enforcement |
| 🎓 **Generalist Mind** | Plain English. For people outside the AI bubble. | Everyday impact, what it means for non-specialists |
| 🧠 **Tech & Workforce Mind** | Dual lens: architecture decisions + career reality. | Tech strategy, engineering trade-offs, workforce/skills |

Each persona always produces: **3 sentences of perspective + 2 evidence bullets** from the article.

Personas are Jev-routed — only the relevant ones run. On a regulation story, only `policy` + `genz` may activate. On a product launch, all 4 may run. Prompt files: `prompts/*.txt`. Voice guide: [`skills.md`](skills.md).

---

## Deduplication — How Duplicate Posts Are Prevented

The CronJob can be retried, manually re-triggered, or run twice on the same day. Three independent gates prevent duplicates:

```
Gate 1 — deduplicate node (before any LLM work)
    PublishedStore.filter_unpublished(articles)
    → Removes articles already published today BEFORE summarising/generating
    → Saves cost: no LLM calls for known articles

Gate 2 — publish node (right before LinkedIn API call)
    PublishedStore.is_published(article_id)
    → Catches race conditions if two runs reach publish simultaneously

Gate 3 — LinkedIn MCP idempotency key
    key = "{article_id}:{YYYY-MM-DD}:{headline_hash[:12]}"
    → Stable across retries (no run_id in key)
    → Even if Gates 1 and 2 both fail, LinkedIn MCP rejects the duplicate
```

**PublishedStore**: file-backed at `/tmp/aifeeders_published.json`. Keys: `"{article_id}:{YYYY-MM-DD}"`. Values: ISO timestamp. 7-day TTL auto-purge. Thread-safe. File is < 2 KB. Failure-safe: if the file cannot be read/written, publishing is not blocked — worst case is a duplicate post.

---

## Architecture

### Services

| Service | Purpose | Replicas |
|---|---|---|
| `daily-news-api` | FastAPI — trigger runs, check status | 2 (HPA) |
| `news-mcp` | GNews adapter | 2 |
| `evaluation-mcp` | LLM content quality fallback | 2 |
| `linkedin-mcp` | LinkedIn OAuth + post publishing | **1** (stateful) |
| `pageindex-mcp` | Article RAG / evidence retrieval | **1** (in-memory) |
| Jev gateway | AI decision engine (external, IBM) | — |

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for full technical detail.

---

## Project Structure

```
src/daily_news/
  agents/
    jev_agents.py          jev_prefilter_articles, jev_route_personas graph nodes
    evaluation_agent.py    Jev primary / EvaluationMCP fallback; deterministic _apply_gate()
    persona_agent.py       4-persona factory, _PersonaOutputRaw lenient parser
    published_store.py     File-backed dedup store, 7-day TTL, thread-safe
    publisher_agent.py     3-gate publish + post composition (deterministic)
    summary_agent.py       LLM summarisation
  config/
    settings.py            Pydantic settings, env var aliases, lru_cache singleton
  mcp/
    jev_client.py          JevClient: prefilter_article, route_personas, evaluate_content
    evaluation.py          EvaluationMCPClient (fallback)
    linkedin.py            LinkedInMCPClient
    news.py                NewsMCPClient
    pageindex.py           PageIndexMCPClient
  models/
    persona.py             PersonaType (4), PersonaOutput, PersonaSetOutput
    summary.py             NewsSummary
    evaluation.py          EvaluationResult, EvaluationDecision
  workflows/
    daily_news_graph.py    LangGraph state machine — 10 nodes, conditional routing

prompts/
  capitalist.txt   policy.txt   genz.txt   linkedin.txt

tests/
  unit/            test_jev_client.py, test_published_store.py, test_evaluation_gate.py
  workflow/        test_langgraph_routing.py

skills.md          Persona voice guide
ARCHITECTURE.md    Full technical architecture (rearchitecture with Jev)
RUNBOOK.md         Operations guide (build flow, launch flow, EKS)
```

---

## Prerequisites

### Accounts needed

| Account | What for |
|---|---|
| GNews API key | `https://gnews.io` — free tier (100 req/day) |
| OpenAI-compatible LLM | Any endpoint (IBM, Azure, OpenAI) |
| Jev System One | Gateway URL + Bearer token |
| LinkedIn Developer App | Client ID + Secret + `w_member_social` scope |
| Langfuse | Optional — LLM tracing at `https://us.cloud.langfuse.com` |

### Tools

```bash
python3 --version   # 3.11+
oc version          # OpenShift CLI (or kubectl for EKS)
docker --version    # Only needed for EKS builds
```

---

## Local Development

```bash
# 1. Clone
git clone https://github.com/<you>/AINewsFeederLinkedin.git
cd AINewsFeederLinkedin

# 2. Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Environment variables
cp .env.example .env
# Edit .env — fill in LLM_API_KEY, GNEWS_API_KEY, JEV_API_KEY etc.

# 4. Run tests
python -m pytest tests/unit tests/workflow -q

# 5. Dry run (no LinkedIn post)
PUBLISHING_ENABLED=false python -m daily_news.workflow_runner

# 6. Live run
python -m daily_news.workflow_runner
```

### Key environment variables

| Variable | Required | Description |
|---|---|---|
| `LLM_BASE_URL` | ✅ | OpenAI-compatible endpoint |
| `LLM_API_KEY` | ✅ | LLM bearer token |
| `GNEWS_API_KEY` | ✅ | GNews API key |
| `JEV_BASE_URL` | ✅ | Jev gateway URL |
| `JEV_API_KEY` | ✅ | Jev bearer token |
| `LINKEDIN_CLIENT_ID` | ✅ | LinkedIn app client ID |
| `LINKEDIN_CLIENT_SECRET` | ✅ | LinkedIn app secret |
| `PUBLISHING_ENABLED` | — | `true` / `false` (default: `true`) |
| `JEV_ENABLED` | — | `true` / `false` (default: `true`) — set false to disable Jev, fall back to heuristics |
| `EVAL_FACTUALITY_THRESHOLD` | — | Default `0.50` |
| `EVAL_GROUNDEDNESS_THRESHOLD` | — | Default `0.50` |
| `EVAL_HALLUCINATION_THRESHOLD` | — | Default `0.85` |
| `LANGFUSE_SECRET_KEY` | — | Optional tracing |

---

## OpenShift Deployment

### Step 1 — Login and namespace

```bash
oc login https://api.<cluster>:6443 -u <user> -p <pass>
oc new-project aifeeders  # skip if exists
```

### Step 2 — ConfigMap and Secrets

```bash
oc apply -f openshift/configmap.yaml -n aifeeders
cp openshift/secrets.yaml.example openshift/secrets.yaml
# Edit secrets.yaml with real values (never commit this file)
oc apply -f openshift/secrets.yaml -n aifeeders
oc apply -f openshift/rbac.yaml -n aifeeders
```

### Step 3 — Create BuildConfigs (first time only)

```bash
oc apply -f openshift/buildconfigs/ -n aifeeders

# Self-pruning: keep only 1 build per service — old builds are deleted automatically
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

### Step 4 — Build images

```bash
# Rsync to a clean tmpdir first — .venv/ is 269 MB and causes build timeouts if included
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  --exclude='htmlcov/' --exclude='.coverage' --exclude='.tox/' --exclude='.DS_Store' \
  . "$TMPDIR/" && \
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
```

> **Why rsync to a tmpdir?** The `.venv/` directory is 269 MB. Including it causes the build upload to time out. The rsync excludes it, keeping the upload under 1 MB. `pip install` inside the BuildPod is layer-cached and runs in ~15 seconds.

### Step 5 — Deploy

```bash
oc apply -f openshift/deployments/ -n aifeeders
oc apply -f openshift/services/ -n aifeeders
oc apply -f openshift/cronjob.yaml -n aifeeders
```

### Step 6 — Authorise LinkedIn

```bash
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders
# Browser: http://localhost:8080/auth/linkedin
# Grant: w_member_social, r_liteprofile
```

### Step 7 — Smoke test

```bash
oc patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'
oc create job smoke-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders
# Expected last line: Workflow complete — status=EVALUATED published=0 errors=0
oc patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### Step 8 — First live run

```bash
oc create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders --output=name
```

---

## Build Flow → Launch Flow

Every code change follows this exact sequence. **Never skip the test step.**

```bash
# 1. Test locally — never build a failing codebase
source .venv/bin/activate
python -m pytest tests/unit tests/workflow -q --tb=short
# Expected: all pass

# 2. Build (clean context — excludes .venv/, sends < 1 MB)
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  --exclude='htmlcov/' --exclude='.coverage' --exclude='.tox/' --exclude='.DS_Store' \
  . "$TMPDIR/" && \
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --output=name
# Prints: build.build.openshift.io/daily-news-57

# 3. Watch build — ends with "Push successful"
oc logs -f build/daily-news-57 -n aifeeders | grep -E "STEP|Push"

# 4. Roll out — new pods pull the :latest image
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders --timeout=60s

# 5. Run
oc create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders --output=name

# 6. Verify
oc logs job/live-run-<id> -n aifeeders | grep -E "status=|published|ERROR"
# Expected: Workflow complete — status=PUBLISHED published=2 errors=0
```

**Build history is self-cleaning.** `successfulBuildsHistoryLimit: 1` means OpenShift deletes the previous build automatically after each new one. No manual cleanup ever needed.

---

## EKS Deployment

Moving from OpenShift to EKS requires **changing only the image build and registry** steps. All Kubernetes YAMLs (Deployments, Services, CronJob, ConfigMap, Secrets, RBAC, NetworkPolicy) are **identical**.

### EKS Build Flow

```bash
# Set your variables
export AWS_ACCOUNT=123456789012
export AWS_REGION=us-east-1
export ECR_REGISTRY=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Build
docker build -t aifeeders/daily-news:latest .

# Push to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY
docker tag aifeeders/daily-news:latest $ECR_REGISTRY/aifeeders/daily-news:latest
docker push $ECR_REGISTRY/aifeeders/daily-news:latest
```

### EKS Launch Flow

```bash
# Update image in deployment
kubectl set image deployment/daily-news-api \
  daily-news=$ECR_REGISTRY/aifeeders/daily-news:latest -n aifeeders
kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders

# Trigger a run
kubectl create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# Watch
kubectl logs -f job/live-run-<id> -n aifeeders | grep -E "status=|published"
```

The only YAML change is `image:` field in each Deployment — from `image-registry.openshift-image-registry.svc/.../daily-news:latest` to `<ecr-registry>/aifeeders/daily-news:latest`.

For production EKS: use **External Secrets Operator** (not raw Secrets), **IRSA** for AWS resource access, and a CNI with NetworkPolicy support (Calico or Cilium). See [`ARCHITECTURE.md`](ARCHITECTURE.md) §10 for the complete EKS setup guide.

---

## Health Checks

```bash
# All pods
oc get pods -n aifeeders

# Service health
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health | jq -r .status
done

# Jev gateway
curl -s https://<your-jev-gateway-host>/health | jq .

# Today's run status
oc logs -l app=daily-news-worker -n aifeeders --since=4h | grep "status="
```

---

## Troubleshooting

| Problem | Most likely cause | Fix |
|---|---|---|
| No post today, `discovered 0 raw articles` | GNews 100 req/day quota exhausted | Wait until midnight UTC |
| No post today, `published=0 errors=0` | `PUBLISHING_ENABLED=false` | Check ConfigMap |
| Post failed, `http=401` | LinkedIn token expired | Re-authorise via port-forward |
| `OutputParserException` | LLM output schema mismatch | Usually transient — retry |
| Build timeout | `.venv/` included in context (269 MB) | Use rsync with exclude pattern above |
| `eval decision=REGENERATE` every run | Eval thresholds too strict | Check `EVAL_FACTUALITY_THRESHOLD` in ConfigMap |
| Old build pods accumulating | `successfulBuildsHistoryLimit` not set | Patch all BuildConfigs to `limit: 1` |
| `jev_prefilter: ReadTimeout` | Jev gateway busy | Handled gracefully — affected articles skipped, run continues |
| `all articles already published today` | Re-run same day | Clear published store or wait until tomorrow |

Full operations guide: [`RUNBOOK.md`](RUNBOOK.md)

---

## Monitoring

- **Langfuse** — LLM traces at `https://us.cloud.langfuse.com`. Each run is a session keyed by `run_id`. Every persona call, evaluation, and publish is a separate span.
- **LinkedIn audit** — `oc exec deployment/linkedin-mcp -- curl -s http://localhost:8000/audit` returns all posts published since pod start.
- **Prometheus** — metrics endpoint at `:8000/metrics` on `daily-news-api`.
