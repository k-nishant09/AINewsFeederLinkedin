# AIFeeders — AI Daily News LinkedIn Platform

> **Fully automated AI news pipeline. Jev System One AI scoring. Publishes to LinkedIn twice daily.**
> Build #76 · OpenShift `aifeeders` · EKS-portable · LangGraph state machine

---

## Table of Contents

1. [What It Does](#1-what-it-does)
2. [How the Pipeline Works](#2-how-the-pipeline-works)
3. [Services — What Runs Where](#3-services--what-runs-where)
4. [Project Structure](#4-project-structure)
5. [Prerequisites](#5-prerequisites)
6. [Local Development](#6-local-development)
7. [OpenShift Deployment — End-to-End](#7-openshift-deployment--end-to-end)
8. [Build Flow → Launch Flow](#8-build-flow--launch-flow)
9. [Production Issues We Have Actually Hit](#9-production-issues-we-have-actually-hit)
10. [Networking — How Pods Talk to Each Other](#10-networking--how-pods-talk-to-each-other)
11. [Scaling](#11-scaling)
12. [Monitoring and Observability](#12-monitoring-and-observability)
13. [Security](#13-security)
14. [EKS Deployment](#14-eks-deployment)
15. [Health Checks](#15-health-checks)
16. [Build History](#16-build-history)

---

## 1. What It Does

AIFeeders runs twice a day — **08:00 UTC** (morning) and **16:00 UTC** (afternoon). Each run:

1. Searches GNews for AI news across 9 query categories (24-hour window).
2. Scores every article with **Jev System One** — picks the single best one by relevance + engagement.
3. Summarises the article with an LLM.
4. Generates four audience-specific perspectives (Capitalist / Policy / Generalist / Tech).
5. Evaluates content for factuality, hallucination, PII, and injection safety.
6. Publishes a structured LinkedIn post — **automatically, no human button-press needed**.

**A published post looks like this:**

```
📡  AI Intelligence  ·  CNBC  ·  AIFeeders

Palo Alto CEO Rejects AI Slowdown, Cites Low Extinction Risk

This perspective highlights the ongoing debate within the tech industry about the pace
and direction of AI development.

🔍  Why this matters
  📡  Signal strength  : 89% relevance   [██░░░] significance
  ⚡  Engagement pulse : 60%   Controversy: Medium
  👥  Relevant to      : 🏛️ Policy 87%  ·  🧠 Tech+Workforce 79%  ·  🎓 Generalist 65%

📌  3 things to know
  • Nikesh Arora, CEO of Palo Alto Networks, opposes calls to slow AI development.
  • Arora's stance aligns with Nvidia CEO Jensen Huang, contrasting with Anthropic and OpenAI.
  • Arora considers the risk of AI-induced human extinction to be 'extremely small.'

📈  Business   —  Arora's stance could influence corporate AI strategies and investment pace.
👷  Workforce  —  Rapid AI development may accelerate automation and workforce reskilling needs.
🔬  Tech       —  The debate underscores balancing technological advancement with safety measures.

🔗  https://www.cnbc.com/2026/09/24/palo-alto-networks-nikesh-arora-ai-slowdown.html

💬  4 angles on this story

💼  Capitalist Mind
Arora's stance on AI development aligns with the need for rapid innovation, but the real
test is whether it drives revenue and defensible moats, not just existential debates.

🏛️  Government Mind
Palo Alto CEO Nikesh Arora's stance against slowing AI development highlights a divide in
the tech industry between safety-first and innovation-first philosophies.

🎓  Generalist Mind
Palo Alto CEO Nikesh Arora thinks slowing down AI is unrealistic and the risk of
AI-induced human extinction is extremely small, which could speed up AI adoption.

🧠  Tech & Workforce Mind
While Arora's stance on minimal AI extinction risk may ease fears, it's crucial to focus
on near-term impacts like job displacement and skill obsolescence.

🗣️  What guardrails should exist around rapid AI development? All angles welcome. 👇

⚠️ Perspectives are AI-simulated — not professional advice.
🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev

#AI #AINews #GenerativeAI #MachineLearning #AIStrategy #OpenAI #Anthropic #NVIDIA
```

---

## 2. How the Pipeline Works

```
CronJob (08:00 UTC + 16:00 UTC)
  └─► discover_news        9 GNews queries (hours=24) → ~20-30 articles
        └─► deduplicate    MD5 hash dedup + PublishedStore cross-run filter
              └─► fetch_articles       Full HTML fetch per article
                    └─► index_pageindex    RAG index for evidence retrieval
                          └─► jev_prefilter       [Jev Decision #1]
                                │  Scores all articles, picks top 1
                                │  11 questions per article:
                                │  is_ai_topic, relevance_score, event_type,
                                │  significance, controversy_level,
                                │  persona_fit × 4, estimated_engagement
                                │  Composite = relevance × 0.6 + engagement × 0.4
                                └─► summarize             LLM → structured NewsSummary
                                      └─► jev_router         [Jev Decision #2]
                                            │  Routes only relevant personas
                                            └─► generate_personas  (parallel)
                                                  └─► evaluate     [Jev Decision #3]
                                                        │  10 quality checks
                                                        ├─► REGENERATE → retry (max 2)
                                                        ├─► BLOCK      → hard stop
                                                        └─► PASS → publish
                                                                    └─► LinkedIn post
```

---

## 3. Services — What Runs Where

| Service | What it does | Pods | State |
|---|---|---|---|
| `daily-news-api` | FastAPI — runs the LangGraph workflow | 2 (HPA 2–4) | Stateless |
| `news-mcp` | GNews adapter — 2-key auto-rotation on quota | 2 | Stateless |
| `evaluation-mcp` | LLM content quality fallback (when Jev disabled) | 2 | Stateless |
| `linkedin-mcp` | LinkedIn OAuth + post publishing | **1** | **Stateful** (token in memory) |
| `pageindex-mcp` | Article RAG / evidence retrieval | **1** | **Stateful** (index in memory) |
| Jev gateway | IBM Jev System One — AI scoring engine | External | — |
| CronJob | `daily-ai-news-morning` (08:00) + `daily-ai-news-afternoon` (16:00) | 0–1 | Job (transient pod) |

> **Why are `linkedin-mcp` and `pageindex-mcp` single-replica?**
> `linkedin-mcp` holds the LinkedIn OAuth token in memory — two pods would each need their own token and there's no shared session store. `pageindex-mcp` holds the RAG index in memory — it is rebuilt per-run anyway, so there's no benefit to two replicas.

---

## 4. Project Structure

```
src/daily_news/
  agents/
    jev_agents.py          Jev decisions #1 (prefilter) and #2 (persona routing)
    evaluation_agent.py    Jev decision #3; quality gate; PASS/BLOCK/REGENERATE
    persona_agent.py       4-persona LLM factory
    published_store.py     File-backed dedup store (7-day TTL, thread-safe)
    publisher_agent.py     Post composition + LinkedIn publish (no LLM calls here)
    summary_agent.py       LLM summarisation → NewsSummary model
  config/
    settings.py            Pydantic settings; all env var names
  mcp/
    client.py              MCPHTTPClient base (timeout, error detection, retry)
    jev_client.py          JevClient: prefilter, route_personas, evaluate_content
    linkedin.py            LinkedInMCPClient
    news.py                NewsMCPClient
    pageindex.py           PageIndexMCPClient
    evaluation.py          EvaluationMCPClient (LLM fallback)
  models/
    persona.py             PersonaType (4 values), PersonaOutput, PersonaSetOutput
    summary.py             NewsSummary
    evaluation.py          EvaluationResult, EvaluationDecision
  workflows/
    daily_news_graph.py    LangGraph 10-node state machine

mcp_servers/
  linkedin_mcp/server.py   LinkedIn OAuth + post/comment tools
  news_mcp/server.py       GNews adapter; 2-key rotation on 403
  evaluation_mcp/server.py LLM evaluation fallback
  pageindex_mcp/server.py  In-memory RAG index

prompts/
  capitalist.txt  policy.txt  genz.txt  linkedin.txt

openshift/
  api/             deployment.yaml  service.yaml  route.yaml
  news-mcp/        deployment.yaml  service.yaml
  evaluation-mcp/  deployment.yaml  service.yaml
  linkedin-mcp/    deployment.yaml  service.yaml
  pageindex-mcp/   deployment.yaml  service.yaml
  configmap.yaml   cronjob.yaml  hpa.yaml  pdb.yaml
  rbac.yaml  networkpolicy.yaml  namespace.yaml  secrets.yaml

tests/
  unit/    test_jev_client.py  test_published_store.py  test_evaluation_gate.py
  workflow/ test_langgraph_routing.py

ARCHITECTURE.md   Full technical reference
RUNBOOK.md        Operations guide — every production scenario
skills.md         Persona voice guide
```

---

## 5. Prerequisites

### Accounts needed

| Account | Where to get it | Free tier limits |
|---|---|---|
| GNews API key (×2) | https://gnews.io | 100 req/day per key |
| OpenAI-compatible LLM | IBM watsonx / Azure OpenAI / OpenAI | Varies |
| Jev System One | IBM internal gateway | Bearer token required |
| LinkedIn Developer App | https://developer.linkedin.com | Scopes: `w_member_social`, `r_liteprofile` |
| Langfuse (optional) | https://us.cloud.langfuse.com | Free tier available |

### Tools required

```bash
python3 --version   # 3.11+ required
oc version          # OpenShift CLI (for OpenShift deployment)
kubectl version     # for EKS deployment
docker --version    # only for EKS builds
```

---

## 6. Local Development

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

# 4. Run tests (all must pass before any build)
python -m pytest tests/unit tests/workflow -q
# Expected: all tests pass

# 5. Dry run (no LinkedIn post)
PUBLISHING_ENABLED=false python -m daily_news.workflow_runner

# 6. Live run
python -m daily_news.workflow_runner
```

### Key environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | ✅ | — | OpenAI-compatible endpoint |
| `LLM_API_KEY` | ✅ | — | LLM bearer token |
| `GNEWS_API_KEY` | ✅ | — | GNews primary key |
| `GNEWS_API_KEY_2` | — | — | GNews key #2 (auto-rotates on 403) |
| `JEV_BASE_URL` | ✅ | — | Jev gateway URL |
| `JEV_API_KEY` | ✅ | — | Jev bearer token |
| `JEV_ENABLED` | — | `true` | Set `false` to skip Jev (uses heuristics) |
| `LINKEDIN_CLIENT_ID` | ✅ | — | LinkedIn app client ID |
| `LINKEDIN_CLIENT_SECRET` | ✅ | — | LinkedIn app secret |
| `PUBLISHING_ENABLED` | — | `true` | Set `false` for smoke/dry runs |
| `EVAL_FACTUALITY_THRESHOLD` | — | `0.50` | Jev eval gate |
| `EVAL_GROUNDEDNESS_THRESHOLD` | — | `0.50` | Jev eval gate |
| `EVAL_HALLUCINATION_THRESHOLD` | — | `0.85` | Jev eval gate |
| `AIFEEDERS_STORE_PATH` | — | `/tmp/aifeeders_published.json` | PublishedStore file path |
| `LANGFUSE_SECRET_KEY` | — | — | Optional LLM tracing |

---

## 7. OpenShift Deployment — End-to-End

### Step 1 — Log in

```bash
# Get your token from the OpenShift web console: top-right → "Copy login command"
oc login --token=<token> --server=https://api.f80l034.fusion.tadn.ibm.com:6443
oc new-project aifeeders   # skip if already exists
oc project aifeeders
```

### Step 2 — Apply ConfigMap and Secrets

```bash
oc apply -f openshift/configmap.yaml -n aifeeders
oc apply -f openshift/rbac.yaml -n aifeeders

# Secrets — never commit real values to git
# Replace placeholder base64 values: echo -n "your-value" | base64
oc apply -f openshift/secrets.yaml -n aifeeders
```

### Step 3 — Create BuildConfigs (first time only)

```bash
oc apply -f openshift/buildconfigs/ -n aifeeders

# Self-pruning: keep only 1 build per service (old builds auto-deleted)
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

### Step 4 — Build all images

```bash
# IMPORTANT: exclude .venv/ — it's 270 MB and causes upload timeout
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' \
  . "$TMPDIR/"

for svc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc start-build $svc --from-dir="$TMPDIR" -n aifeeders --follow
done
```

### Step 5 — Deploy services

```bash
oc apply -f openshift/api/ -n aifeeders
oc apply -f openshift/news-mcp/ -n aifeeders
oc apply -f openshift/evaluation-mcp/ -n aifeeders
oc apply -f openshift/linkedin-mcp/ -n aifeeders
oc apply -f openshift/pageindex-mcp/ -n aifeeders
oc apply -f openshift/cronjob.yaml -n aifeeders
oc apply -f openshift/hpa.yaml -n aifeeders
oc apply -f openshift/pdb.yaml -n aifeeders
oc apply -f openshift/networkpolicy.yaml -n aifeeders

oc get pods -n aifeeders   # all should be Running
```

### Step 6 — Authorise LinkedIn (first time)

```bash
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# Browser: http://localhost:8080/auth/linkedin
# Grant: w_member_social + r_liteprofile
# Token is stored in-memory — re-authorise after any pod restart
# Token expires after 60 days — set a calendar reminder
```

### Step 7 — Smoke test and first live run

```bash
# Disable publishing, run the whole pipeline without posting
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'
oc create job smoke-$(date +%s) --from=cronjob/daily-ai-news-morning -n aifeeders
# Wait and tail: expected "status=EVALUATED published=0 errors=0"

# Re-enable and fire a live run
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
oc create job live-$(date +%s) --from=cronjob/daily-ai-news-morning -n aifeeders
```

---

## 8. Build Flow → Launch Flow

Every code change follows this exact sequence. **Never skip the test step.**

```bash
# 1. Test
source .venv/bin/activate
python -m pytest tests/unit tests/workflow -q --tb=short

# 2. Build (exclude .venv/ — upload must be < 5 MB)
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' \
  . "$TMPDIR/"
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow

# 3. Roll out (zero-downtime rolling restart)
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders --timeout=90s

# 4. Run
LIVE_JOB=$(oc create job live-$(date +%s) --from=cronjob/daily-ai-news-morning \
  -n aifeeders --output=name | sed 's|job.batch/||')
oc logs -f job/$LIVE_JOB -n aifeeders | grep -E "status=|published|ERROR"
```

---

## 9. Production Issues We Have Actually Hit

This section documents every real failure we encountered on this system — what broke, why, and exactly how it was fixed. Read this before debugging any issue.

---

### Issue #1 — CronJob pods silently timeout on every GNews search

**Symptom:**
```
2026-09-25 03:47:16 WARNING news search failed for artificial intelligence LLM agentic AI model:
2026-09-25 03:48:16 WARNING news search failed for artificial intelligence finance investment funding:
```
The error message is **empty** (nothing after the colon). All 9 queries fail. Pipeline produces 0 articles.

**Root cause:**
The CronJob pod template had **no labels**. OpenShift's `default-deny-all` NetworkPolicy blocked all outbound traffic from the CronJob pod to the MCP services. The 60-second httpx timeout fired before any GNews response arrived. The exception was a `TimeoutError` with empty string representation, which is why the log showed nothing after the colon.

```
CronJob pod labels: {batch.kubernetes.io/job-name: "...", controller-uid: "..."}
NetworkPolicy allow-api-to-mcps: allows only pods with app=daily-news-api OR app=daily-news-worker
Result: CronJob pod → news-mcp:8000 → CONNECTION TIMED OUT (60s)
```

The label was in the wrong YAML location:

```yaml
# WRONG — labels under spec (does not exist in Kubernetes)
template:
  spec:
    labels:                       ← This field doesn't exist
      app: daily-news-worker

# CORRECT — labels under metadata
template:
  metadata:
    labels:
      app: daily-news-worker      ← Network policy now allows this pod
  spec:
    ...
```

**Fix:**
```bash
oc patch cronjob daily-ai-news-morning --type='json' \
  -p='[{"op":"add","path":"/spec/jobTemplate/spec/template/metadata/labels","value":{"app":"daily-news-worker"}}]'
oc patch cronjob daily-ai-news-afternoon --type='json' \
  -p='[{"op":"add","path":"/spec/jobTemplate/spec/template/metadata/labels","value":{"app":"daily-news-worker"}}]'
```

**How to verify the fix worked:**
```bash
# Confirm the new pod has the label
oc get pod <cronjob-pod-name> -o jsonpath='{.metadata.labels}'
# Must show: {"app":"daily-news-worker", ...}

# Test connectivity from inside the pod
oc exec <cronjob-pod-name> -- curl -s --max-time 5 http://news-mcp:8000/health
# Must return: {"status":"healthy", ...}
```

**Lesson:** Always verify that NetworkPolicy `podSelector.matchLabels` values match exactly what your pod template `metadata.labels` contains. A missing label = silent network block.

---

### Issue #2 — Build upload timeout — `.venv/` included in context

**Symptom:**
```
oc start-build daily-news --from-dir=.
Uploading directory "." as binary input...
[hangs for 5+ minutes, then]
error: build/daily-news-XX failed — error streaming build logs
```

**Root cause:**
`.venv/` is ~270 MB. `oc start-build --from-dir=.` tarballs and streams the entire directory to the OpenShift build pod. At typical cluster upload speeds (~1 MB/s), this takes 4–5 minutes and exceeds the streaming timeout.

**Fix:**
Always rsync to a tmpdir first, excluding `.venv/`:
```bash
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' \
  . "$TMPDIR/"
echo "Upload size: $(du -sh $TMPDIR | cut -f1)"  # Must be < 5 MB
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
```

**Never run:**
```bash
oc start-build daily-news --from-dir=.   # ← Do NOT do this
```

---

### Issue #3 — GNews API key quota exhausted

**Symptom:**
```
WARNING news search failed for ...: 403 Client Error: Forbidden
discovered 0 raw articles
Workflow complete — status=PUBLISHED published=0 errors=9
```

**Root cause:**
GNews free tier allows 100 requests/day per key. Our pipeline uses 9 queries per run × 2 runs/day = 18 requests/day. Running manual test jobs during development exhausted the daily quota.

**Fix (immediate):**
Wait until midnight UTC for quota reset.

**Fix (permanent — key rotation):**
We set up two GNews keys. The `news-mcp` server automatically switches to key 2 on any HTTP 403:
```bash
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"GNEWS_API_KEY_2\":\"$(echo -n 'your-key-2' | base64)\"}}"
oc rollout restart deployment/news-mcp -n aifeeders
```

Verify both keys are loaded:
```bash
oc exec deployment/news-mcp -n aifeeders -- curl -s http://localhost:8000/health | jq .
# "keys_configured": 2  ← must show 2
# "active_key_prefix": "e6f0db13..."
```

---

### Issue #4 — LinkedIn token expired, posts silently fail

**Symptom:**
```
post FAILED http=401 article=news-abc123
Workflow complete — status=PUBLISHED published=0 errors=1
```

**Root cause:**
LinkedIn OAuth tokens expire after **60 days**. The token is stored in-memory in `linkedin-mcp`. After expiry, every post attempt returns HTTP 401 Unauthorized.

**Fix:**
```bash
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# Browser: http://localhost:8080/auth/linkedin
# Complete OAuth flow (takes ~30 seconds)
kill %1  # stop port-forward
```

**Prevention:**
Set a repeating calendar reminder every 55 days titled "Renew LinkedIn OAuth token".

---

### Issue #5 — linkedin-mcp pod restart loses the OAuth token

**Symptom:**
Posts were working fine, then suddenly all start failing with `401` — even though the token was just renewed.

**Root cause:**
The LinkedIn OAuth token is stored only in-memory in the `linkedin-mcp` process. Any pod restart (OOM kill, node eviction, deploy rollout) wipes the token.

**Fix:**
Re-authorise via OAuth after every `linkedin-mcp` pod restart:
```bash
# Always check token status after any restart
oc exec deployment/linkedin-mcp -n aifeeders -- curl -s http://localhost:8000/health | jq .token_status
# "valid" = OK;  "missing" or "expired" = need to re-authorise

oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# http://localhost:8080/auth/linkedin
```

**Lesson:** Single-replica stateful pods lose their state on restart. Do not deploy new `linkedin-mcp` images carelessly — always re-authorise after.

---

### Issue #6 — Post truncated mid-sentence on LinkedIn

**Symptom:**
The post in logs looks complete and under the limit. On LinkedIn the post cuts off in the middle of a sentence, usually somewhere in the Jev decision block.

**Root cause:**
LinkedIn's API counts characters as **UTF-16 code units** (like JavaScript's `String.length`). Python's `len()` counts Unicode code points. Most emoji — like `💼 🎓 🧠 🔥 📌 📈 👷 🔬 💬 🤖 ⚠️` — are outside the Basic Multilingual Plane (U+10000+), so each emoji costs **2 UTF-16 units** but only **1 Python code point**. A post with 30 such emoji that Python reports as 2980 chars is actually ~3010 LinkedIn units → silent mid-sentence truncation.

**Fix (in `publisher_agent.py`):**
```python
def _linkedin_len(text: str) -> int:
    """Count as LinkedIn does: UTF-16 code units, not Unicode code points."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)
```

All character budget calculations now use `_linkedin_len()` with a safety limit of **2900** (not 3000).

---

### Issue #7 — `PERMISSION_ERROR` on LinkedIn Comments API

**Symptom:**
```
INFO Comments API not available (PERMISSION_ERROR) — personas embedded in post body.
```

**Root cause:**
LinkedIn's Comments API requires the "Community Management API" product to be added to the LinkedIn Developer App. This requires separate approval from LinkedIn and is not available on basic developer apps.

**This is expected behaviour, not a bug.** All 4 persona perspectives are embedded in the post body itself, so nothing is lost.

**If you get LinkedIn Comments API approved:**
No code change is needed. The publisher agent already attempts persona comments after each post. Remove the `PERMISSION_ERROR` check to let them post as threaded comments.

---

### Issue #8 — Jev gateway timeout — pipeline falls back silently

**Symptom:**
```
WARNING jev_prefilter failed (ReadTimeout) — falling back to [:1] selection
```
The post is published, but without Jev scoring — the first article is taken regardless of quality.

**Root cause:**
The Jev gateway had high load or was being restarted. The httpx timeout (30s) fired before a response arrived.

**Behaviour:**
The pipeline does NOT stop. It falls back gracefully:
- `jev_prefilter` → picks first article (`[:1]`) with no scoring
- `jev_router` → runs all 4 personas (no routing optimisation)
- `evaluate` → uses LLM-based `EvaluationMCPClient`

**If Jev is down repeatedly:**
```bash
# Disable Jev entirely until it recovers
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"false"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
# Re-enable when Jev is back
```

---

### Issue #9 — Old builds accumulate, namespace gets cluttered

**Symptom:**
```
oc get builds -n aifeeders
daily-news-68  Complete
daily-news-69  Complete
daily-news-70  Complete
daily-news-71  Complete
daily-news-72  Complete
daily-news-73  Complete
daily-news-74  Complete
daily-news-75  Complete
```

**Root cause:**
`successfulBuildsHistoryLimit` was not set on the BuildConfig. OpenShift keeps every completed build indefinitely by default.

**Fix:**
```bash
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
# Delete existing old builds manually
oc delete builds -n aifeeders -l buildconfig=daily-news \
  --field-selector status.phase=Complete
```

With the limit set to 1, OpenShift deletes the previous build automatically when the next one succeeds.

---

### Issue #10 — `oc whoami` shows error / session token expired

**Symptom:**
```
oc get pods -n aifeeders
error: You must be logged in to the server (Unauthorized)
```

**Root cause:**
OpenShift session tokens expire. The token in `~/.kube/config` is no longer valid.

**Fix:**
1. Open the OpenShift web console
2. Top-right → your username → "Copy login command"
3. Paste the `oc login --token=... --server=...` command into your terminal

This is normal and expected. Tokens expire for security reasons.

---

## 10. Networking — How Pods Talk to Each Other

This is one of the most important things to understand about this system. **If networking is wrong, everything silently fails with timeouts.**

### The NetworkPolicy model

The namespace uses a `default-deny-all` policy. **All traffic is blocked by default.** Only explicitly allowed paths work.

```
default-deny-all  ──────  blocks all ingress and egress between pods

allow-api-to-mcps  ────  allows pods with label app=daily-news-api
                          OR  app=daily-news-worker
                         to reach pods with label role=mcp-server
                         on port 8000

allow-router-to-api  ──  allows the OpenShift router to reach daily-news-api
allow-egress-internet  ─  allows all pods to reach external internet (GNews, LinkedIn, Jev)
allow-router-to-linkedin-mcp  ──  allows the router to reach linkedin-mcp for OAuth
```

### What label each pod needs

| Pod | Required label | Why |
|---|---|---|
| `daily-news-api` pods | `app: daily-news-api` | Can call all MCP services |
| CronJob pods | `app: daily-news-worker` | Can call all MCP services |
| MCP server pods | `role: mcp-server` | Accepts calls from the above |

### How to diagnose a networking problem

```bash
# Step 1: Check what labels a pod actually has
oc get pod <pod-name> -o jsonpath='{.metadata.labels}' | python3 -m json.tool

# Step 2: Test connectivity from inside the pod
oc exec <pod-name> -- curl -s --max-time 5 http://news-mcp:8000/health
# "Connection timed out" = NetworkPolicy blocking
# "Connection refused" = pod not listening on that port
# JSON response = working correctly

# Step 3: Check what NetworkPolicies exist
oc get networkpolicy -n aifeeders

# Step 4: Describe a specific policy to see its selectors
oc describe networkpolicy allow-api-to-mcps -n aifeeders
```

### Service DNS names

Inside the cluster, every Kubernetes Service is reachable by its name:

| Service | Internal DNS name | Port |
|---|---|---|
| `news-mcp` | `http://news-mcp:8000` | 8000 |
| `evaluation-mcp` | `http://evaluation-mcp:8000` | 8000 |
| `linkedin-mcp` | `http://linkedin-mcp:8000` | 8000 |
| `pageindex-mcp` | `http://pageindex-mcp:8000` | 8000 |
| `daily-news-api` | `http://daily-news-api:8000` | 8000 |

The MCP URL settings in the ConfigMap end with `/mcp` (e.g. `http://news-mcp:8000/mcp`). The `MCPHTTPClient` automatically strips `/mcp` off the base URL and appends `/call` for tool calls.

---

## 11. Scaling

### What scales automatically

| Component | Mechanism | Trigger |
|---|---|---|
| `daily-news-api` | HPA (Horizontal Pod Autoscaler) | CPU > 70% → scales up to 4 pods |
| `daily-news-api` | HPA | CPU < 30% for 5 min → scales down to 2 pods |

### What does NOT scale

| Component | Reason | Impact |
|---|---|---|
| `linkedin-mcp` | Stateful OAuth token — can't share state between replicas | Only 1 pod; pod failure = need to re-authorise |
| `pageindex-mcp` | In-memory RAG index per run — no value in scaling | Only 1 pod; no data loss on restart |
| CronJob pods | Each run is independent — 1 pod per run | `concurrencyPolicy: Forbid` prevents two runs at once |

### PodDisruptionBudget

`pdb.yaml` sets `minAvailable: 1` for `daily-news-api`. This means:
- During node maintenance or cluster upgrades, Kubernetes will always keep at least 1 `daily-news-api` pod running.
- Without this, an upgrade could terminate all pods simultaneously, causing API downtime.

### Resource limits

| Service | Request | Limit | Why |
|---|---|---|---|
| `daily-news-api` | 256Mi / 500m | 2Gi / 2 CPU | LangGraph + multiple LLM calls in parallel |
| `news-mcp` | 128Mi / 100m | 256Mi / 500m | Lightweight HTTP adapter |
| `evaluation-mcp` | 256Mi / 200m | 512Mi / 1 CPU | LLM inference calls |
| `linkedin-mcp` | 128Mi / 100m | 256Mi / 500m | OAuth + HTTP |
| `pageindex-mcp` | 128Mi / 200m | 512Mi / 1 CPU | In-memory index |

Setting limits prevents one runaway pod from consuming all node resources and evicting neighbours.

---

## 12. Monitoring and Observability

### Checking if today's run worked

```bash
# 1. See all jobs from today
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -5

# 2. Check the summary line of the last job
oc logs job/<latest-job-name> -n aifeeders | grep "Workflow complete"
# Expected: "Workflow complete — status=PUBLISHED published=1 errors=0"

# 3. What article was selected (and why)
oc logs job/<latest-job-name> -n aifeeders | grep "jev_prefilter:"
# Shows: relevance score, engagement score, composite, which personas

# 4. What was published in the last 7 days
oc exec deployment/daily-news-api -n aifeeders -- \
  cat /tmp/aifeeders_published.json | python3 -m json.tool
```

### Langfuse traces (LLM observability)

Every run creates a Langfuse session keyed by `run_id`. Each LLM call (summarise, persona, evaluate) is a span with:
- Input: article text / summary
- Output: structured result + decision
- Tokens used and latency

Access: https://us.cloud.langfuse.com → filter by `run_id` from the logs.

### LinkedIn audit log

```bash
oc exec deployment/linkedin-mcp -n aifeeders -- \
  curl -s http://localhost:8000/audit | jq .
```

Returns all post URNs published since the current pod started, with timestamps and character counts.

### Prometheus metrics

`daily-news-api` exposes metrics at `:8000/metrics`. Key metrics:
- `workflow_runs_total` — total runs since pod start
- `workflow_errors_total` — total error counts by type
- `articles_published_total` — total LinkedIn posts published

### Health check all services at once

```bash
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp daily-news-api; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- \
    curl -s http://localhost:8000/health | jq -r '.status // "unknown"' 2>/dev/null \
    || echo "EXEC FAILED"
done
```

### Log patterns to watch for

| Pattern | Meaning | Action needed? |
|---|---|---|
| `discovered 0 raw articles` | GNews quota hit or network error | Check quota; wait for reset |
| `news search failed for ...: ` (empty error) | NetworkPolicy blocking pod | Check pod labels (Issue #1) |
| `all articles already published today` | Dedup store blocking re-runs | Normal — wait until tomorrow |
| `eval decision=BLOCK` | PII or injection detected in article | Review source article |
| `eval decision=REGENERATE` | Quality below threshold | Auto-retries up to 2× |
| `post FAILED http=401` | LinkedIn token expired | Re-authorise (Issue #4) |
| `post FAILED http=429` | LinkedIn rate limit | Wait ~10 min |
| `Comments API not available (PERMISSION_ERROR)` | Normal — app not approved for Comments | No action needed |
| `status=PUBLISHED published=1 errors=0` | ✅ Perfect run | None |

---

## 13. Security

### Secrets — never in code or ConfigMap

All API keys and credentials live in the Kubernetes Secret `daily-news-secrets`. They are injected as environment variables at runtime. They are:
- Never written to logs
- Never included in the ConfigMap (which can be read by anyone with namespace access)
- Never committed to git

```bash
# To rotate a key without restarting pods:
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"LLM_API_KEY\":\"$(echo -n 'new-key' | base64)\"}}"
# Then restart the pods that use it:
oc rollout restart deployment/daily-news-api -n aifeeders
```

### NetworkPolicy — default deny

The namespace uses `default-deny-all`. No pod can talk to any other pod unless there is an explicit NetworkPolicy `allow` rule covering both the source label and the destination port. See [Section 10](#10-networking--how-pods-talk-to-each-other) for the full model.

### Jev content safety gates

Before any article is published, Jev evaluates:
- `pii_detected > 0.5` → **hard BLOCK** — post never published
- `prompt_injection_detected > 0.5` → **hard BLOCK**
- `policy_check = FAIL` → **hard BLOCK**

These gates cannot be overridden by any configuration flag — they are enforced in code regardless of `JEV_ENABLED`.

### LinkedIn OAuth

The LinkedIn OAuth access token is:
- Stored only in-memory in the `linkedin-mcp` process
- Never written to disk, logs, or a database
- Lost on pod restart (requires re-authorisation)
- Valid for 60 days — rotation is the operator's responsibility

### TLS

All external API calls use HTTPS. The Jev gateway uses an IBM-internal TLS certificate; `httpx` is called with `verify=False` for that specific client only. All other external calls (GNews, LinkedIn, LLM endpoint, Langfuse) use standard TLS verification.

### RBAC — minimum permissions

The CronJob ServiceAccount (`daily-news-sa`) has only the permissions it needs:
- `create` Jobs in the `aifeeders` namespace
- `get`/`list` Pods (for health checks)
- No cluster-level permissions
- No access to Secrets (secrets are injected by the pod spec, not read by the app)

---

## 14. EKS Deployment

Everything except the image build and registry URL is identical between OpenShift and EKS.

| Aspect | OpenShift | EKS |
|---|---|---|
| Image build | `oc start-build` (in-cluster S2I) | `docker build` + ECR push |
| Image registry | `image-registry.openshift-image-registry.svc:5000/...` | `<account>.dkr.ecr.<region>.amazonaws.com/...` |
| Secrets | `oc create secret generic` | External Secrets Operator + AWS Secrets Manager |
| LinkedIn OAuth route | OpenShift Route | ALB Ingress or `kubectl port-forward` |
| NetworkPolicy enforcement | Built-in | Requires Calico or Cilium CNI |
| PublishedStore persistence | `/tmp` (ephemeral) | EFS PVC (recommended) |
| All other YAML | **Identical** | **Identical** |

See [`ARCHITECTURE.md`](ARCHITECTURE.md) §10 for the full EKS setup.

---

## 15. Health Checks

```bash
# All pods
oc get pods -n aifeeders

# Per-service health
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp daily-news-api; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health \
    | jq -r '.status // "unknown"' 2>/dev/null || echo "EXEC FAILED"
done

# GNews key status
oc exec deployment/news-mcp -n aifeeders -- curl -s http://localhost:8000/health \
  | jq '{keys_configured,active_key_index,active_key_prefix}'

# Jev gateway (external — no auth needed)
curl -s https://<your-jev-gateway>/health | jq .
# Expected: {"status": "ready", "model": "...", "method": "lora_decision_head"}

# CronJob schedule
oc get cronjobs -n aifeeders
# Expected: daily-ai-news-morning (0 8 * * *) and daily-ai-news-afternoon (0 16 * * *)

# Today's run
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -3
oc logs job/<latest-job> -n aifeeders | grep "Workflow complete"
```

---

## 16. Build History

| Build | Key changes |
|---|---|
| #76 | Clean rebuild after old build deletion; confirmed post-publish to LinkedIn `urn:li:share:7509097557713833984` |
| #75 | All visible text clips at sentence boundary — no more mid-sentence cuts from `_hard_clip` |
| #74 | Full post format redesign — 10-section humanised engagement-first layout, all dynamic |
| #73 | Single article per run (top-1 Jev), dual CronJob 08:00/16:00 UTC, all persona prompts rewritten |
| #62 | Documentation rewrite. EKS portability guide. `IMPACT_LINE_CAP` 88→120. |
| #61 | **LinkedIn truncation fix** — `_linkedin_len()` UTF-16 counting; safety limit 2900. Per-article `jev_prefilter_scores` keyed by `article_id`. LinkedIn MCP nested error detection. `publication_key` includes body hash. |
| #60 | Post composition rewrite: hook category from Jev `event_type`, Jev decision block, footer guarantee. `AI_SEARCH_QUERIES` 6→9; `hours=48→24`; `limit=20→10`. |
| #59 | Initial documentation. |
| #58 | `evaluation_agent.py` fix for `personas.labor` AttributeError. |
