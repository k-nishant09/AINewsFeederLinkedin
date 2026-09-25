# AIFeeders Runbook

> **Build #83** — dialogue-delivery format · GrammarAgent · 3-5 sentence persona story passages  
> **Last verified:** Build #83 · 210 tests passing · Latest live run `RUN-A3ED494CC414` published `urn:li:share:7509262700972101632`

---

## Table of Contents

1. [What This System Does in Plain English](#1-what-this-system-does-in-plain-english)
2. [Normal Day — What to Check](#2-normal-day--what-to-check)
3. [First-Time Setup on a New Cluster](#3-first-time-setup-on-a-new-cluster)
4. [Build Flow → Launch Flow](#4-build-flow--launch-flow)
5. [Triggering a Run Manually](#5-triggering-a-run-manually)
6. [How to Read the Logs](#6-how-to-read-the-logs)
7. [Checking Service Health](#7-checking-service-health)
8. [Production Issues — Exact Symptoms, Root Causes, Fixes](#8-production-issues--exact-symptoms-root-causes-fixes)
9. [API Quota and Key Rotation](#9-api-quota-and-key-rotation)
10. [Networking — Understanding the NetworkPolicy](#10-networking--understanding-the-networkpolicy)
11. [Scaling and Resource Management](#11-scaling-and-resource-management)
12. [Monitoring and Observability](#12-monitoring-and-observability)
13. [Security Practices](#13-security-practices)
14. [Cleaning the Namespace](#14-cleaning-the-namespace)
15. [Deploying to EKS / AKS](#15-deploying-to-eks--aks)
16. [Glossary](#16-glossary)

---

## 1. What This System Does in Plain English

AIFeeders is a **fully automated AI news intelligence and LinkedIn publishing pipeline**. No human presses a button. Every day, twice a day, the system wakes up, reads the internet, picks the most important AI news story, thinks about it from four different human perspectives, writes a polished LinkedIn post, checks its own grammar, and publishes — all without you doing anything.

### The schedule

A Kubernetes CronJob fires at **08:00 UTC** and **16:00 UTC** every day.

### What happens in each run (8 stages)

**Stage 1 — Find the news**  
The pipeline fires 9 separate search queries at the GNews API, covering different angles of AI news from the last 24 hours. This produces a pool of candidate articles.

**Stage 2 — Deduplicate**  
Articles already published in previous runs are filtered out using a local published-article store. You will never see the same story twice.

**Stage 3 — Pick the best one**  
Every remaining article is scored by the **Jev System One** evaluator, which uses the `qwen2-5-72b-instruct` language model to assess relevance, novelty, and composite quality. The single highest-scoring article is selected.

**Stage 4 — Extract the story**  
The **MediaStorytellerAgent** reads the winning article and extracts a structured story arc: hook, analogy, perspective, second-order effect, and a future question. This is the "what is actually interesting here" layer that separates the post from a dry news summary.

**Stage 5 — Summarise the facts**  
The article facts are summarised, but calibrated against the extracted story so the summary supports the narrative rather than competing with it.

**Stage 6 — Generate four character voices**  
Four character personas each write a **3-5 sentence story passage** grounded in their own real-world experience and perspective:

| Character | Lens |
|-----------|------|
| Founder | How this affects building a startup today |
| Policy Analyst | Regulatory, societal, and governance implications |
| Generalist | How an informed non-specialist reads this |
| Engineer | Technical depth, implementation reality |

Each persona speaks in first person, as if they are on a live panel.

**Stage 7 — Proofread (build #83)**  
The **GrammarAgent** (new in build #83) reads the fully composed post and corrects spelling, grammar, punctuation, and capitalisation errors before publication. The log records how many characters were corrected.

**Stage 8 — Evaluate, score, and publish**  
The post is evaluated for quality, factual accuracy, PII, and safety. A reach score (0–100) is computed. If the verdict is `PUBLISH`, the post goes live on LinkedIn automatically.

### What the published post looks like

The post uses a **dialogue-delivery format** introduced in build #83. It reads like a news programme in text:

```
[MEDIA PERSON narrates the story and sets the scene]

FOUNDER: "..."
POLICY ANALYST: "..."
GENERALIST: "..."
ENGINEER: "..."

[MEDIA PERSON closes with the non-obvious take]

What's your take? [call to action]
```

This format is deliberately conversational. The reader feels like they are listening to four smart people think out loud, not reading a press release.

---

## 2. Normal Day — What to Check

If you are just checking whether today's run went well, these are the five commands to run in order.

### Step 1 — Are the pods running?

```bash
oc get pods -n aifeeders
```

Expected output — all pods should be in `Running` state, none in `CrashLoopBackOff` or `Error`:

```
NAME                               READY   STATUS    RESTARTS   AGE
daily-news-api-7d9f6b4c9-abcde    1/1     Running   0          3h
daily-news-api-7d9f6b4c9-fghij    1/1     Running   0          3h
linkedin-mcp-6c8b7d5f4-klmno      1/1     Running   0          2d
news-mcp-5f9c8e7b6-pqrst          1/1     Running   0          2d
evaluation-mcp-4e8d7c6a5-uvwxy    1/1     Running   0          2d
pageindex-mcp-3d7c6b5a4-z1234     1/1     Running   0          2d
```

### Step 2 — Is the API responding?

```bash
curl -sk https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health
```

Expected: `{"status":"healthy"}`

### Step 3 — Did today's CronJob run fire?

```bash
oc get cronjobs -n aifeeders
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -5
```

### Step 4 — Did the run publish successfully?

Get the name of the most recent job pod:

```bash
oc get pods -n aifeeders --sort-by=.metadata.creationTimestamp | grep daily-news | tail -3
```

Then read its logs (replace `<pod-name>`):

```bash
oc logs <pod-name> -n aifeeders | grep -E "post published|FAILED|ERROR"
```

Expected success line:

```
[RUN-xxxxxxxx] post published post_urn=urn:li:share:...
```

### Step 5 — Quick log health check

Key log patterns at a glance:

| Pattern to grep | What it means | Build #83 note |
|----------------|---------------|----------------|
| `jev_prefilter: #1` | Jev scored and selected article | New in #83: shows composite= |
| `story extracted` | MediaStorytellerAgent completed | Shows hook_len= |
| `grammar_agent` | GrammarAgent ran | Shows char count before→after |
| `eval article=` | Evaluator ran | Shows factuality=, decision= |
| `score_reach` | Reach scorer ran | Shows reach_score=X/100 |
| `post published` | LinkedIn post went live | Shows post_urn= |
| `PERMISSION_ERROR` | Comments API denied | **Expected — not an error** |
| `ReadTimeout` | Jev gateway timeout | Pipeline falls back gracefully |
| `AUTH_ERROR http=401` | LinkedIn token expired | Action required — re-authorise |
| `GNews API error: 403` | GNews quota exhausted | Pipeline auto-rotates key |

---

## 3. First-Time Setup on a New Cluster

Follow these 11 steps in order. Do not skip steps. Each step depends on the previous one.

### Prerequisites

Before you start, you need:
- `oc` CLI installed and available in your PATH
- `kubectl` available (optional but useful)
- Docker installed (for EKS/AKS variants; not needed for OpenShift)
- Access to the OpenShift web console to copy the login command
- All secret values ready (GNews API keys, LinkedIn credentials, LLM gateway URL)

---

### Step 1 — Log in to the cluster

Go to the OpenShift web console, click your username in the top-right corner, and select **"Copy login command"**. Run the copied command:

```bash
oc login --token=<your-token> --server=https://api-f80l034-fusion-tadn.ibm.com:6443
```

Verify you are in the right namespace:

```bash
oc project aifeeders
# If the project does not exist yet:
oc new-project aifeeders
```

---

### Step 2 — Apply the ConfigMap

The ConfigMap holds non-secret configuration values (URLs, feature flags, thresholds). It is safe to commit to git with placeholder values.

```bash
oc apply -f k8s/configmap.yaml -n aifeeders
```

Verify it was created:

```bash
oc get configmap daily-news-config -n aifeeders -o yaml
```

Check that `PUBLISHING_ENABLED` is `"false"` before your first run. You will enable it in Step 11.

---

### Step 3 — Apply Secrets

Secrets hold API keys and credentials. They are stored base64-encoded in Kubernetes. **Never commit real secret values to git.** Use the template file and fill in values locally.

```bash
# Edit the secrets template and fill in real values (never commit this file with real values)
cp k8s/secrets.yaml.template k8s/secrets-local.yaml
# Edit k8s/secrets-local.yaml — fill in all base64-encoded values

oc apply -f k8s/secrets-local.yaml -n aifeeders
```

Required secrets:

| Secret name | Key | Where to get it |
|-------------|-----|-----------------|
| `gnews-secret` | `GNEWS_API_KEY` | gnews.io dashboard |
| `gnews-secret` | `GNEWS_API_KEY_2` | gnews.io dashboard (second account) |
| `linkedin-secret` | `LINKEDIN_CLIENT_ID` | LinkedIn Developer Portal |
| `linkedin-secret` | `LINKEDIN_CLIENT_SECRET` | LinkedIn Developer Portal |
| `llm-secret` | `LLM_GATEWAY_API_KEY` | IBM internal model gateway |

Encode a value for a secret:

```bash
echo -n "your-actual-value" | base64
```

---

### Step 4 — Apply RBAC

RBAC (Role-Based Access Control) rules control what the pods are allowed to do inside the cluster (for example, the CronJob needs permission to create job pods).

```bash
oc apply -f k8s/rbac.yaml -n aifeeders
```

Verify:

```bash
oc get rolebindings -n aifeeders
```

---

### Step 5 — Create BuildConfigs (first time only)

BuildConfigs tell OpenShift how to build each container image from source code. This step is only needed once per cluster — after that, you use `oc start-build` to rebuild.

```bash
oc apply -f k8s/buildconfig-daily-news.yaml -n aifeeders
oc apply -f k8s/buildconfig-news-mcp.yaml -n aifeeders
oc apply -f k8s/buildconfig-evaluation-mcp.yaml -n aifeeders
oc apply -f k8s/buildconfig-linkedin-mcp.yaml -n aifeeders
oc apply -f k8s/buildconfig-pageindex-mcp.yaml -n aifeeders
```

---

### Step 6 — Set build history limits

Without this, completed builds accumulate and waste storage. Set history limits immediately after creating BuildConfigs.

```bash
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge \
    -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

---

### Step 7 — Build all images

**Critical:** Always rsync to a tmpdir first to exclude `.venv/` (270 MB). Including `.venv/` causes build uploads to time out.

```bash
# Create a clean upload directory
TMPDIR=$(mktemp -d)
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
  /Users/kumar/AINewsfeederLinkedin/ "$TMPDIR/"

# Verify the size — must be under 5 MB
echo "Upload size: $(du -sh $TMPDIR | cut -f1)"

# Build each service (these can run sequentially)
oc start-build daily-news      --from-dir="$TMPDIR" -n aifeeders --follow
oc start-build news-mcp        --from-dir="$TMPDIR" -n aifeeders --follow
oc start-build evaluation-mcp  --from-dir="$TMPDIR" -n aifeeders --follow
oc start-build linkedin-mcp    --from-dir="$TMPDIR" -n aifeeders --follow
oc start-build pageindex-mcp   --from-dir="$TMPDIR" -n aifeeders --follow
```

Each build will stream its log output. Wait for `Push successful` before moving on.

---

### Step 8 — Deploy all services

```bash
oc apply -f k8s/deployment-daily-news-api.yaml -n aifeeders
oc apply -f k8s/deployment-news-mcp.yaml -n aifeeders
oc apply -f k8s/deployment-evaluation-mcp.yaml -n aifeeders
oc apply -f k8s/deployment-linkedin-mcp.yaml -n aifeeders
oc apply -f k8s/deployment-pageindex-mcp.yaml -n aifeeders
oc apply -f k8s/cronjob.yaml -n aifeeders
oc apply -f k8s/networkpolicy.yaml -n aifeeders
oc apply -f k8s/service.yaml -n aifeeders
oc apply -f k8s/route.yaml -n aifeeders
```

---

### Step 9 — Verify all pods running

```bash
oc get pods -n aifeeders
```

Wait until all pods show `Running` and `READY` shows `1/1`. If any pod is in `CrashLoopBackOff`, read its logs:

```bash
oc logs <pod-name> -n aifeeders --previous
```

Health check all services:

```bash
curl -sk https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health
# Expected: {"status":"healthy"}

# linkedin-mcp health (check token status too)
curl -sk https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health
# Expected: {"status":"healthy","token_status":"missing"}  ← token missing is OK here, Step 10 fixes this
```

---

### Step 10 — Authorise LinkedIn (OAuth flow)

The LinkedIn MCP service needs an OAuth token to publish posts. This token is stored in memory only (not on disk, for security). You must authorise once after every linkedin-mcp pod restart.

```bash
# Port-forward the linkedin-mcp service to your local machine
oc port-forward svc/linkedin-mcp 8080:8080 -n aifeeders &

# Open your browser and go to:
open http://localhost:8080/auth/linkedin
# (or copy-paste the URL manually if 'open' does not work)
```

Complete the OAuth flow in the browser. You will be redirected back to a success page. After completion:

```bash
# Verify the token is now present
curl -sk http://localhost:8080/health
# Expected: {"status":"healthy","token_status":"present"}

# Kill the port-forward
kill %1
```

**Important:** Set a calendar reminder for **55 days from now** to re-authorise. LinkedIn tokens expire after 60 days, and you want a 5-day buffer before expiry.

---

### Step 11 — Smoke test, then live run

First, run with publishing disabled to verify the full pipeline works:

```bash
# Confirm publishing is disabled
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Trigger a dry run
curl -sk -X POST \
  https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/workflow/daily-news \
  -H "Content-Type: application/json" \
  -d '{}'

# Watch the logs of the workflow pod
oc logs -f deployment/daily-news-api -n aifeeders
```

Check the logs for `decision=PASS` and `verdict=PUBLISH`. If the dry run succeeds and generates a valid post, enable publishing and run live:

```bash
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'

curl -sk -X POST \
  https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/workflow/daily-news \
  -H "Content-Type: application/json" \
  -d '{"publishing_enabled": true}'
```

Confirm the post appeared on LinkedIn and the log shows `post published post_urn=urn:li:share:...`.

---

## 4. Build Flow → Launch Flow

### When to rebuild

You need to rebuild whenever you change **source code**. You do **not** need to rebuild if you only change a ConfigMap value (like `PUBLISHING_ENABLED`) — those take effect on pod restart.

| Change type | Action needed |
|-------------|---------------|
| Python source code change | Rebuild affected service(s), rollout |
| New dependency added to pyproject.toml | Rebuild affected service(s), rollout |
| ConfigMap value change | `oc rollout restart` only |
| Secret value change | Update secret, `oc rollout restart` |
| Kubernetes manifest change | `oc apply` + `oc rollout restart` |

### Exact build and launch sequence

Never skip steps. Skipping Step 1 (tests) risks deploying broken code. Skipping Step 2 (rsync) causes build timeouts.

```bash
# ─────────────────────────────────────────────
# STEP 1 — Run all tests locally
# ─────────────────────────────────────────────
/Users/kumar/.local/bin/uv run pytest tests/ --ignore=tests/evaluation -q
# Must show: 210 passed, 0 failed
# If any test fails, do NOT proceed to Step 2.

# ─────────────────────────────────────────────
# STEP 2 — rsync to a clean tmpdir
# ─────────────────────────────────────────────
# This is CRITICAL. The .venv/ directory is 270 MB.
# If you include it, oc start-build will hang and time out.
TMPDIR=$(mktemp -d)
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
  /Users/kumar/AINewsfeederLinkedin/ "$TMPDIR/"

echo "Upload size: $(du -sh $TMPDIR | cut -f1)"
# Must be < 5 MB. If it is larger, check what was not excluded.

# ─────────────────────────────────────────────
# STEP 3 — Build on OpenShift
# ─────────────────────────────────────────────
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
# Wait for: "Push successful"
# If other services changed too, build them as well (see Step 7 above)

# ─────────────────────────────────────────────
# STEP 4 — Roll out the new image
# ─────────────────────────────────────────────
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders --timeout=90s
# Wait for: "successfully rolled out"

# ─────────────────────────────────────────────
# STEP 5 — Verify pods are healthy
# ─────────────────────────────────────────────
oc get pods -n aifeeders -l app=daily-news-api --no-headers
# Expected: 2 pods Running

# ─────────────────────────────────────────────
# STEP 6 — Test the API health endpoint
# ─────────────────────────────────────────────
curl -sk https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health
# Expected: {"status":"healthy"}

# ─────────────────────────────────────────────
# STEP 7 — Optional: trigger a test run (dry run first)
# ─────────────────────────────────────────────
# See Section 5 for full dry run / live run instructions.
```

---

## 5. Triggering a Run Manually

The CronJob fires automatically at 08:00 UTC and 16:00 UTC. If you need to trigger a run outside that schedule — for testing, debugging, or re-running after a failure — use the following.

### Dry run (no LinkedIn publish)

A dry run executes the full pipeline but does not post to LinkedIn. Use this whenever you are testing a new build or investigating an issue.

```bash
# Step 1: Disable publishing in the ConfigMap
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Step 2: Trigger the workflow
curl -sk -X POST \
  https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/workflow/daily-news \
  -H "Content-Type: application/json" \
  -d '{}'

# Step 3: Watch the logs in real time
oc logs -f deployment/daily-news-api -n aifeeders
```

Look for `decision=PASS` and `verdict=PUBLISH` in the logs to confirm the pipeline would have published.

### Live run (publishes to LinkedIn)

Only use this after confirming the dry run succeeded and you actually want a post published now.

```bash
# Step 1: Enable publishing
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'

# Step 2: Trigger the workflow with publishing flag
curl -sk -X POST \
  https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/workflow/daily-news \
  -H "Content-Type: application/json" \
  -d '{"publishing_enabled": true}'

# Step 3: Confirm publication
oc logs -f deployment/daily-news-api -n aifeeders | grep "post published"
```

### Watching logs during a run

The pipeline takes 2–5 minutes end-to-end (longer if the Jev gateway is slow). Watch all stages in sequence:

```bash
oc logs -f deployment/daily-news-api -n aifeeders
```

Expected stage sequence in the logs:

```
[RUN-xxxxxxxx] Starting daily news workflow
[RUN-xxxxxxxx] gnews: fetching 9 queries...
[RUN-xxxxxxxx] jev_prefilter: #1 title="..." relevance=0.83 composite=0.68
[RUN-xxxxxxxx] story extracted style=unexpected_consequence hook_len=119
[RUN-xxxxxxxx] grammar_agent corrected 2898 → 2898 chars
[RUN-xxxxxxxx] eval article= ... decision=PASS factuality=0.74 hallucination=0.31
[RUN-xxxxxxxx] score_reach reach_score=63/100 verdict=PUBLISH
[RUN-xxxxxxxx] post published post_urn=urn:li:share:7509262700972101632
```

---

## 6. How to Read the Logs

This section documents every important log pattern with an example from build #83. Use these as your reference when investigating any run.

### Stage 1 — GNews fetch

```bash
oc logs <pod> -n aifeeders | grep "gnews"
```

**Normal:**
```
[RUN-A3ED494CC414] gnews: fetching 9 queries...
[RUN-A3ED494CC414] gnews: query 1/9 "AI news" → 10 articles
...
[RUN-A3ED494CC414] gnews: total 63 unique articles after deduplication
```

**Problem — quota exhausted:**
```
GNews API error: 403 for query "AI regulation"
```
→ See [Issue #3](#issue-3--gnews-quota-exhausted-http-403)

---

### Stage 3 — Jev article selection

```bash
grep "jev_prefilter"
```

**Normal:**
```
[RUN-A3ED494CC414] jev_prefilter: #1 title="OpenAI releases new reasoning model" relevance=0.83 composite=0.68
```

- `relevance` — how relevant the article is to AI (0–1)
- `composite` — combined quality score (0–1); anything above 0.55 is good

**Problem — gateway timeout:**
```
ReadTimeout: jev_prefilter exceeded 60s — falling back to articles[:1]
```
→ Pipeline continues. See [Issue #8](#issue-8--jev-gateway-timeout-readtimeout)

---

### Stage 4 — Story extraction

```bash
grep "story extracted"
```

**Normal:**
```
[RUN-A3ED494CC414] story extracted style=unexpected_consequence hook_len=119
```

- `style` — the story angle chosen (e.g. `unexpected_consequence`, `hidden_pattern`, `paradigm_shift`)
- `hook_len` — length of the opening hook in characters; aim for 80–130

---

### Stage 6 — Grammar agent (build #83)

```bash
grep "grammar_agent"
```

**Normal — no corrections needed:**
```
[RUN-A3ED494CC414] grammar_agent corrected 2898 → 2898 chars
```

**Normal — corrections made:**
```
[RUN-A3ED494CC414] grammar_agent corrected 2901 → 2898 chars
```
The second number is the character count after corrections. A decrease means text was tightened. A slight increase is also normal (e.g. added a missing article or comma).

---

### Stage 7 — Evaluation

```bash
grep "eval article="
```

**Normal — PASS:**
```
[RUN-A3ED494CC414] eval article="OpenAI releases..." decision=PASS factuality=0.74 hallucination=0.31
```

- `factuality` — confidence that claims in the post are factually accurate (0–1; above 0.6 is good)
- `hallucination` — risk score for invented facts (0–1; below 0.4 is good)
- `decision` — `PASS` or `FAIL`

**Problem — FAIL:**
```
[RUN-A3ED494CC414] eval article=... decision=FAIL reason="hallucination_risk_high"
```
→ Run will not publish. The pipeline aborts cleanly. Investigate the article and re-run.

---

### Reach scoring

```bash
grep "score_reach"
```

**Normal:**
```
[RUN-A3ED494CC414] score_reach reach_score=63/100 verdict=PUBLISH
```

- `reach_score` — predicted LinkedIn engagement (0–100)
- `verdict` — `PUBLISH` or `HOLD`; scores below threshold result in `HOLD` (post not published)

---

### Publication

```bash
grep "post published"
```

**Normal:**
```
[RUN-A3ED494CC414] post published post_urn=urn:li:share:7509262700972101632
```

**Problem — authentication failed:**
```
[RUN-A3ED494CC414] post FAILED error_class=AUTH_ERROR http=401
```
→ LinkedIn token expired. See [Issue #4](#issue-4--linkedin-token-expired-http-401)

---

### Comments API (expected — not an error)

```bash
grep "PERMISSION_ERROR"
```

**Always expected:**
```
Comments API not available (PERMISSION_ERROR) — personas embedded in post body. Skipping.
```

This is **not an error**. The LinkedIn "Community Management API" requires explicit approval from LinkedIn. Until that approval is granted, personas are included in the main post body instead of as comments. See [Issue #7](#issue-7--comments-api-permission_error-not-an-error) for full context.

---

### Any real error

```bash
grep "ERROR" | grep -v "PERMISSION_ERROR"
```

Any `ERROR` that is not `PERMISSION_ERROR` warrants investigation.

---

## 7. Checking Service Health

The system consists of 5 services. Here is how to check each one.

### 1 — daily-news-api (the main orchestrator)

```bash
# Pod status
oc get pods -n aifeeders -l app=daily-news-api

# Health endpoint
curl -sk https://daily-news-api-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health
# Expected: {"status":"healthy"}

# Logs
oc logs deployment/daily-news-api -n aifeeders --tail=50
```

### 2 — linkedin-mcp (LinkedIn publisher)

```bash
# Pod status
oc get pods -n aifeeders -l app=linkedin-mcp

# Health endpoint — also shows token status
curl -sk https://linkedin-mcp-aifeeders.apps.f80l034.fusion.tadn.ibm.com/health
# Expected: {"status":"healthy","token_status":"present"}
# If token_status is "missing", re-authorise (Section 3 Step 10)

# Logs
oc logs deployment/linkedin-mcp -n aifeeders --tail=50
```

### 3 — news-mcp (GNews fetcher)

```bash
oc get pods -n aifeeders -l app=news-mcp

# Internal health check via exec (news-mcp is not exposed externally)
oc exec deployment/daily-news-api -n aifeeders -- \
  curl --max-time 5 http://news-mcp:8000/health
# Expected: {"status":"healthy"}
```

### 4 — evaluation-mcp (quality evaluator)

```bash
oc get pods -n aifeeders -l app=evaluation-mcp

oc exec deployment/daily-news-api -n aifeeders -- \
  curl --max-time 5 http://evaluation-mcp:8000/health
# Expected: {"status":"healthy"}
```

### 5 — pageindex-mcp (published article store)

```bash
oc get pods -n aifeeders -l app=pageindex-mcp

oc exec deployment/daily-news-api -n aifeeders -- \
  curl --max-time 5 http://pageindex-mcp:8000/health
# Expected: {"status":"healthy"}
```

### Check all pods at once

```bash
oc get pods -n aifeeders
```

Any pod not in `Running` state or showing `CrashLoopBackOff` needs immediate attention. Read its logs:

```bash
oc logs <pod-name> -n aifeeders
oc logs <pod-name> -n aifeeders --previous   # logs from the previous (crashed) container
```

---

## 8. Production Issues — Exact Symptoms, Root Causes, Fixes

This section documents every production failure that has occurred, with exact symptoms, the root cause, and the exact fix. Read this before assuming something is broken.

---

### Issue #1 — CronJob pods silently timeout

**Symptom:**  
The CronJob completes and shows as `Completed` in `oc get jobs`, but all 9 GNews queries fail. The error message is an empty string `""`, not a real error message. No articles are fetched.

**Root cause:**  
The CronJob pod cannot reach `news-mcp` because the NetworkPolicy does not match it. The `app: daily-news-worker` label is placed under `template.spec.labels` (which is silently ignored by Kubernetes) instead of `template.metadata.labels` (where Kubernetes actually reads pod labels).

Because the pod has no matching label, the NetworkPolicy that allows traffic to `news-mcp` does not apply, and all outbound connections are silently dropped.

**Diagnose:**

```bash
# Get the name of a recent CronJob pod
oc get pods -n aifeeders | grep daily-news | grep -v api

# Exec into it and test connectivity
oc exec <cronjob-pod-name> -n aifeeders -- \
  curl --max-time 5 http://news-mcp:8000/health
# If this hangs or returns "Connection refused", the NetworkPolicy is not matching the pod.
```

**Fix:**  
In `cronjob.yaml`, move the `app: daily-news-worker` label from `spec.jobTemplate.spec.template.spec.labels` to `spec.jobTemplate.spec.template.metadata.labels`:

```yaml
# WRONG (silently ignored)
spec:
  jobTemplate:
    spec:
      template:
        spec:
          labels:           # ← This is NOT where pod labels go
            app: daily-news-worker

# CORRECT
spec:
  jobTemplate:
    spec:
      template:
        metadata:
          labels:           # ← Pod labels belong here
            app: daily-news-worker
```

After fixing, reapply and verify:

```bash
oc apply -f k8s/cronjob.yaml -n aifeeders
oc exec <new-cronjob-pod> -n aifeeders -- curl --max-time 5 http://news-mcp:8000/health
```

---

### Issue #2 — Build upload timeout

**Symptom:**  
`oc start-build` hangs for several minutes and then fails with `context deadline exceeded` or simply never completes.

**Root cause:**  
The `.venv/` directory (270 MB of Python packages) was included in the build context uploaded to OpenShift. The S2I builder does not need this — it installs dependencies fresh inside the container. Uploading 270 MB over a network connection almost always times out.

**Diagnose:**

```bash
du -sh /Users/kumar/AINewsfeederLinkedin/
# If this shows > 100 MB, .venv/ is likely being included.
du -sh $TMPDIR
# If this shows > 5 MB after rsync, check what was not excluded.
```

**Fix:**  
Always use the rsync-to-tmpdir pattern before every build:

```bash
TMPDIR=$(mktemp -d)
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
  /Users/kumar/AINewsfeederLinkedin/ "$TMPDIR/"
echo "Upload size: $(du -sh $TMPDIR | cut -f1)"
# Must be < 5 MB
```

---

### Issue #3 — GNews quota exhausted (HTTP 403)

**Symptom:**  
Some or all GNews search queries fail. Log shows:

```
GNews API error: 403 for query "AI regulation"
```

**Root cause:**  
The GNews free tier allows 100 API requests per day. With 9 search queries per run and 2 runs per day, the pipeline uses 18 requests per day normally, but quota resets at midnight UTC and edge conditions can cause faster exhaustion.

**Fix:**  
The pipeline automatically rotates to `GNEWS_API_KEY_2` when the primary key returns 403. If **both** keys are exhausted, the pipeline will fail to fetch articles. In that case:

- Wait 24 hours for the quota to reset at midnight UTC
- As a temporary workaround, you can trigger a manual run the next morning

To check which key is currently active:

```bash
oc logs deployment/daily-news-api -n aifeeders | grep "gnews.*key"
```

To check remaining quota, visit the gnews.io dashboard for each API key account.

---

### Issue #4 — LinkedIn token expired (HTTP 401)

**Symptom:**  
Post fails at the publish stage. Log shows:

```
[RUN-xxxxxxxx] post FAILED error_class=AUTH_ERROR http=401
```

**Root cause:**  
LinkedIn OAuth access tokens expire after **60 days**. If the token is not re-authorised before expiry, the linkedin-mcp service gets a 401 Unauthorized response from the LinkedIn API.

**Fix:**  
Re-authorise via the OAuth flow:

```bash
oc port-forward svc/linkedin-mcp 8080:8080 -n aifeeders &
open http://localhost:8080/auth/linkedin
# Complete the OAuth flow in the browser
curl -sk http://localhost:8080/health | grep token_status
# Expected: "token_status":"present"
kill %1
```

Set a calendar reminder for **55 days from the date of re-authorisation**.

---

### Issue #5 — linkedin-mcp pod restart wipes OAuth token

**Symptom:**  
After any deployment or restart of the `linkedin-mcp` pod, the health endpoint shows `"token_status":"missing"` and posts start failing with 401.

**Root cause:**  
By deliberate security design, the OAuth token is stored in **process memory only** — it is never written to disk or to a Kubernetes Secret. This means any pod restart clears the token. The trade-off is that the token cannot be leaked via a secret exfiltration attack.

**Fix:**  
Re-authorise immediately after any `linkedin-mcp` restart using the port-forward OAuth flow (same as Issue #4 above).

After every deployment of linkedin-mcp, set a new calendar reminder for 55 days out.

**Prevention:**  
Do not restart linkedin-mcp unnecessarily. If only the `daily-news-api` code changes, only rebuild and rollout `daily-news`. Rebuild `linkedin-mcp` only when its own code changes.

---

### Issue #6 — Post truncated mid-sentence on LinkedIn

**Symptom:**  
The published LinkedIn post ends abruptly mid-word or mid-sentence. The post looks cut off.

**Root cause:**  
LinkedIn measures post length in **UTF-16 code units**, not Python `len()` characters. Emoji outside the Basic Multilingual Plane (such as 💼 🎓 🧠 🤖 ⚠️) count as **2 LinkedIn units** but only 1 Python character. If the post template uses these emoji, Python's `len()` underestimates the true LinkedIn length, so posts that appear within the 3000-character limit actually exceed it.

**Status:**  
Fixed in build #61 with the `_linkedin_len()` helper function, which counts UTF-16 units correctly. This issue is **already resolved**.

**If it reappears:**  
Check whether new emoji were added to the post template. Any emoji with a Unicode code point above U+FFFF costs 2 LinkedIn units. Verify using `_linkedin_len()` from the codebase.

---

### Issue #7 — Comments API PERMISSION_ERROR (NOT an error)

**Symptom:**  
Log always shows:

```
Comments API not available (PERMISSION_ERROR) — personas embedded in post body. Skipping.
```

**Root cause:**  
The LinkedIn "Community Management API" (which allows posting comments programmatically) requires explicit approval from LinkedIn as a separate product permission. This has not been granted to the current LinkedIn Developer Application.

**This is not a bug.** The pipeline is correctly detecting the lack of permission and falling back gracefully. The four character persona voices (Founder, Policy Analyst, Generalist, Engineer) are included directly in the main post body instead of as comment-thread replies.

**Future note:**  
A fifth voice (`labor.txt`) is ready and waiting in the codebase for when the Community Management API permission is granted.

**Action required:**  
None. The system is working as designed.

---

### Issue #8 — Jev gateway timeout (ReadTimeout)

**Symptom:**  
The `jev_prefilter` stage takes longer than 60 seconds. Log shows:

```
ReadTimeout: jev_prefilter exceeded 60s — falling back to articles[:1]
```

**Root cause:**  
The IBM internal model gateway (`model-gateway-model-gateway.apps.f73l056.fusion.tadn.ibm.com`) is temporarily slow or overloaded. This can happen during peak usage hours of the shared model gateway.

**Impact:**  
The pipeline does **not** fail. It falls back gracefully to selecting the first article in the list (by recency) instead of the highest-scoring one. All four persona voices, the story extraction, grammar checking, and evaluation still run. The post is published.

**Fix:**  
No immediate action needed. If this happens frequently (more than once a week), consider:

- Running at a different time of day when the gateway is less loaded
- Reducing the Jev timeout threshold to fail faster and select the fallback sooner

---

### Issue #9 — Old builds accumulate

**Symptom:**  
`oc get builds -n aifeeders` shows 10+ old builds in `Complete` or `Failed` state, consuming storage.

**Fix:**

```bash
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge \
    -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

This caps retained build history to 1 successful and 1 failed build per BuildConfig. Old builds are garbage-collected automatically after this is applied.

---

### Issue #10 — oc whoami error / session token expired

**Symptom:**  
Any `oc` command returns `Unauthorized`:

```
Error from server (Unauthorized): the server has asked for the client to provide credentials
```

**Root cause:**  
OpenShift login tokens expire (typically after 24 hours or when a new token is issued).

**Fix:**  
Go to the OpenShift web console → click your username in the top-right corner → **"Copy login command"** → run the `oc login` command it gives you.

```bash
oc login --token=<new-token> --server=https://api-f80l034-fusion-tadn.ibm.com:6443
oc project aifeeders
oc whoami   # should show your username
```

---

## 9. API Quota and Key Rotation

### GNews API (2-key rotation)

| Key | Variable name | Quota |
|-----|--------------|-------|
| Primary | `GNEWS_API_KEY` | 100 req/day (free tier) |
| Secondary | `GNEWS_API_KEY_2` | 100 req/day (free tier) |

The pipeline uses **18 requests per day** in normal operation (9 queries × 2 runs). The pipeline auto-rotates to `GNEWS_API_KEY_2` on first 403 from the primary key. Quotas reset at midnight UTC.

If both keys are exhausted (unlikely under normal operation but possible if extra manual runs were triggered), wait until midnight UTC.

**To update a GNews key:**

```bash
# Encode the new value
echo -n "your-new-key" | base64

# Edit the secret
oc edit secret gnews-secret -n aifeeders
# Update the base64 value for GNEWS_API_KEY or GNEWS_API_KEY_2

# Restart to pick up the new secret
oc rollout restart deployment/daily-news-api -n aifeeders
```

---

### LinkedIn OAuth token (60-day expiry)

LinkedIn access tokens expire after **60 days**. This is a LinkedIn platform limitation and cannot be changed.

**Calendar reminder protocol:**  
After every re-authorisation, set a calendar reminder for **55 days** (5-day buffer before expiry).

**Re-authorisation procedure:**

```bash
oc port-forward svc/linkedin-mcp 8080:8080 -n aifeeders &
open http://localhost:8080/auth/linkedin
# Complete the OAuth browser flow
curl -sk http://localhost:8080/health | python3 -m json.tool
# Confirm: "token_status": "present"
kill %1
```

**Important:** The token is stored in memory only. It is wiped whenever the linkedin-mcp pod restarts (for any reason). After any deployment that touches linkedin-mcp, re-authorise immediately and reset the 55-day reminder.

---

### LLM gateway (qwen2-5-72b-instruct)

The LLM gateway at `https://model-gateway-model-gateway.apps.f73l056.fusion.tadn.ibm.com/v1` is shared IBM infrastructure. There is no per-day quota, but availability depends on IBM internal operations.

If the gateway is unreachable, the pipeline will fail at the story extraction stage. Check with IBM infrastructure team if the gateway is consistently unavailable.

To check the gateway manually:

```bash
curl -sk https://model-gateway-model-gateway.apps.f73l056.fusion.tadn.ibm.com/v1/models \
  -H "Authorization: Bearer $LLM_GATEWAY_API_KEY"
```

---

## 10. Networking — Understanding the NetworkPolicy

### Why NetworkPolicy exists

Without NetworkPolicy, any pod in the cluster can talk to any other pod. This means if one service is compromised, an attacker can reach all other services. NetworkPolicy implements a **zero-trust** model: every communication channel must be explicitly allowed.

In AIFeeders, NetworkPolicy means:
- `daily-news-api` can talk to `news-mcp`, `evaluation-mcp`, `linkedin-mcp`, and `pageindex-mcp`
- The CronJob pod can talk to `daily-news-api`
- No pod can reach services it has no business talking to
- External access comes only through the OpenShift Route (HTTPS)

### The policies

Five NetworkPolicies are applied:

| Policy name | Allows |
|-------------|--------|
| `allow-daily-news-to-mcp` | `daily-news-api` → `news-mcp`, `evaluation-mcp`, `linkedin-mcp`, `pageindex-mcp` |
| `allow-cronjob-to-api` | `daily-news-worker` → `daily-news-api` |
| `allow-route-ingress` | OpenShift router → `daily-news-api`, `linkedin-mcp` |
| `allow-dns` | All pods → kube-dns (UDP 53) |
| `deny-all-default` | Default deny — blocks everything not explicitly allowed |

### The CronJob label bug (Issue #1)

The CronJob pod must have the label `app: daily-news-worker` for the `allow-cronjob-to-api` NetworkPolicy to match it. This label must be in `spec.jobTemplate.spec.template.metadata.labels` — **not** in `spec.labels` (which labels the CronJob object itself, not its pods) and **not** in `spec.jobTemplate.spec.template.spec.labels` (which is not where Kubernetes reads pod labels, but is silently accepted by the API).

```yaml
# cronjob.yaml — correct label placement
apiVersion: batch/v1
kind: CronJob
metadata:
  name: daily-news-cronjob
  labels:              # ← This labels the CronJob object (not the pods)
    app: daily-news-cronjob
spec:
  schedule: "0 8,16 * * *"
  jobTemplate:
    spec:
      template:
        metadata:
          labels:      # ← This labels the pods created by the job
            app: daily-news-worker   # ← Must match NetworkPolicy selector
        spec:
          containers:
          - name: daily-news-worker
            # ...
```

### Diagnosing NetworkPolicy issues

If a pod cannot reach a service it should be able to reach:

```bash
# Exec into the source pod and try to reach the target
oc exec <source-pod> -n aifeeders -- \
  curl --max-time 5 http://<target-service>:8000/health

# Check what labels the pod actually has
oc get pod <pod-name> -n aifeeders -o jsonpath='{.metadata.labels}'

# Check what NetworkPolicies exist and what selectors they use
oc get networkpolicies -n aifeeders
oc describe networkpolicy <policy-name> -n aifeeders
```

---

## 11. Scaling and Resource Management

### Current replica counts

| Service | Replicas | Reason |
|---------|----------|--------|
| `daily-news-api` | 2 | HA — one pod can restart without downtime |
| `linkedin-mcp` | 1 | Token is stored in memory; 2 replicas would cause token split-brain |
| `news-mcp` | 1 | Stateless, low traffic |
| `evaluation-mcp` | 1 | Stateless, low traffic |
| `pageindex-mcp` | 1 | Has local file state; scaling requires shared PVC |

### Why linkedin-mcp must stay at 1 replica

The OAuth token is stored in process memory. If you scale to 2 replicas, one pod holds the token and one does not. Requests routed to the pod without the token will fail with 401. **Always keep linkedin-mcp at 1 replica.**

### Scaling daily-news-api

You can scale up during heavy testing periods:

```bash
oc scale deployment/daily-news-api --replicas=3 -n aifeeders
```

Scale back down when done. 2 is the standard production value.

### Resource requests and limits

Each pod should have resource requests and limits set in its deployment manifest. If a pod is being OOMKilled, check:

```bash
oc describe pod <pod-name> -n aifeeders | grep -A 5 "Limits\|Requests\|OOMKilled"
```

The `daily-news-api` pod does the heaviest work (LLM calls, long-running workflow). If it is memory-constrained, increase its limit in the deployment manifest and rebuild.

### CronJob job history

Completed CronJob pods remain visible in `oc get pods` until garbage-collected. To clean up manually:

```bash
oc get jobs -n aifeeders | grep Complete | awk '{print $1}' | \
  xargs -I{} oc delete job {} -n aifeeders
```

---

## 12. Monitoring and Observability

### What to monitor daily

| Check | Command | Alert if |
|-------|---------|----------|
| Pod health | `oc get pods -n aifeeders` | Any pod not `Running` |
| API health | `curl -sk .../health` | Not `{"status":"healthy"}` |
| CronJob last run | `oc get jobs -n aifeeders` | No job in last 12h |
| LinkedIn token | `curl -sk .../health` on linkedin-mcp | `token_status: missing` |
| Post published | `grep "post published"` in logs | No post in last 24h |

### Viewing logs

```bash
# All pods for a deployment (most recent logs)
oc logs deployment/daily-news-api -n aifeeders --tail=100

# Follow live logs
oc logs -f deployment/daily-news-api -n aifeeders

# Logs from a specific pod
oc logs <pod-name> -n aifeeders

# Logs from a crashed/previous container
oc logs <pod-name> -n aifeeders --previous

# Logs from a CronJob pod
oc logs <cronjob-pod-name> -n aifeeders
```

### Checking recent runs

```bash
# List all jobs (CronJob-triggered runs)
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp

# Get the most recent run's pod
oc get pods -n aifeeders --sort-by=.metadata.creationTimestamp | tail -5

# Check if the last run published
oc logs <last-run-pod> -n aifeeders | grep -E "post published|FAILED"
```

### Events (useful for diagnosing pod startup failures)

```bash
oc get events -n aifeeders --sort-by=.lastTimestamp | tail -20
```

### Build status

```bash
oc get builds -n aifeeders --sort-by=.metadata.creationTimestamp
```

---

## 13. Security Practices

### Secrets management

- **Never commit real secret values to git.** The `k8s/secrets.yaml.template` file contains only placeholder values with comments indicating what goes there.
- All secrets are stored in Kubernetes Secrets (base64-encoded, not plain text).
- Secret values are injected as environment variables into pods — they are never written to disk inside the container.
- To rotate a secret, edit it with `oc edit secret <name> -n aifeeders` and restart the affected deployment.

### OAuth token security

The LinkedIn OAuth token is stored **in memory only** (not in a Kubernetes Secret, not on disk). This is a deliberate trade-off: the token cannot be exfiltrated via a secret dump or a volume mount attack, but it is lost on every pod restart. Re-authorise after every linkedin-mcp restart.

### Publishing safeguard

The `PUBLISHING_ENABLED` flag in the ConfigMap is the main publishing guard. Set it to `"false"` during any debugging or testing:

```bash
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'
```

Confirm it is set before any non-production run.

### .env files

The `.env` and `.env.*` files in the local project directory contain API keys for local development. They are excluded from git via `.gitignore`. They are also excluded from build uploads via the rsync `--exclude='.env'` and `--exclude='.env.*'` flags.

Never run `oc start-build` from the raw project directory without the rsync step — it risks including `.env` in the build context.

### Network isolation

All inter-service communication is restricted by NetworkPolicy. External access is only available through the HTTPS OpenShift Route. Pod-to-pod traffic on arbitrary ports is denied by the default-deny policy.

---

## 14. Cleaning the Namespace

### Remove old builds

```bash
# Set history limits so this is automated going forward
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge \
    -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done

# Manually delete all but the most recent build for a BuildConfig
oc get builds -n aifeeders | grep daily-news | head -n -1 | awk '{print $1}' | \
  xargs -I{} oc delete build {} -n aifeeders
```

### Remove old CronJob pods

```bash
oc get jobs -n aifeeders | awk 'NR>1 {print $1}' | \
  xargs -I{} oc delete job {} -n aifeeders
```

### Restart a single service

```bash
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout restart deployment/news-mcp -n aifeeders
oc rollout restart deployment/evaluation-mcp -n aifeeders
oc rollout restart deployment/pageindex-mcp -n aifeeders

# ⚠️ linkedin-mcp: re-authorise immediately after restart
oc rollout restart deployment/linkedin-mcp -n aifeeders
# Then: oc port-forward svc/linkedin-mcp 8080:8080 -n aifeeders &
#       open http://localhost:8080/auth/linkedin
```

### Restart all services

```bash
for dep in daily-news-api news-mcp evaluation-mcp pageindex-mcp; do
  oc rollout restart deployment/$dep -n aifeeders
done
# Restart linkedin-mcp last and re-authorise immediately after
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

### Full namespace teardown (destructive — use with care)

Only do this if you are decommissioning this cluster entirely.

```bash
oc delete all --all -n aifeeders
oc delete configmap --all -n aifeeders
oc delete secret --all -n aifeeders
oc delete networkpolicy --all -n aifeeders
# oc delete project aifeeders  # uncomment only if decommissioning fully
```

---

## 15. Deploying to EKS / AKS

This section covers deploying AIFeeders to AWS EKS and Azure AKS. The pipeline logic is identical to OpenShift, but infrastructure setup differs.

---

### Deploying to AWS EKS

#### Prerequisites

- AWS CLI configured (`aws configure`)
- `kubectl` installed
- Docker installed and running
- `eksctl` installed (simplifies cluster management)
- An ECR (Elastic Container Registry) repository per service

#### Step 1 — Create the EKS cluster

```bash
eksctl create cluster \
  --name aifeeders \
  --region us-east-1 \
  --nodegroup-name standard \
  --node-type t3.medium \
  --nodes 3 \
  --nodes-min 2 \
  --nodes-max 4 \
  --managed
```

Wait ~15 minutes. Then verify:

```bash
kubectl get nodes
```

#### Step 2 — Install a NetworkPolicy engine (required)

EKS does not enforce NetworkPolicy by default. You must install Calico or Cilium.

```bash
# Install Calico
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.27.0/manifests/calico.yaml

# Wait for Calico pods to be running
kubectl get pods -n calico-system
```

#### Step 3 — Create ECR repositories

```bash
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=us-east-1

for svc in daily-news news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  aws ecr create-repository \
    --repository-name aifeeders/$svc \
    --region $AWS_REGION
done
```

#### Step 4 — Build and push images to ECR

```bash
# Log in to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS \
  --password-stdin $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Build and push each service (rsync to tmpdir first — same rule as OpenShift)
TMPDIR=$(mktemp -d)
rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  /Users/kumar/AINewsfeederLinkedin/ "$TMPDIR/"

for svc in daily-news news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  docker build -t $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/aifeeders/$svc:latest \
    -f "$TMPDIR/Dockerfile.$svc" "$TMPDIR"
  docker push $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/aifeeders/$svc:latest
done
```

#### Step 5 — Set up secrets with AWS Secrets Manager + External Secrets Operator

Use AWS Secrets Manager (not hardcoded Kubernetes Secrets):

```bash
# Install External Secrets Operator
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets-system --create-namespace

# Store secrets in AWS Secrets Manager
aws secretsmanager create-secret \
  --name aifeeders/gnews-api-key \
  --secret-string "your-gnews-api-key"

aws secretsmanager create-secret \
  --name aifeeders/linkedin-client-secret \
  --secret-string "your-linkedin-client-secret"

# etc. for each secret
```

Then apply an `ExternalSecret` manifest that pulls from Secrets Manager into Kubernetes Secrets.

#### Step 6 — Configure IRSA (IAM Roles for Service Accounts)

Instead of static AWS credentials in pods, use IRSA:

```bash
# Enable OIDC provider for the cluster
eksctl utils associate-iam-oidc-provider \
  --cluster aifeeders \
  --approve

# Create IAM role and bind to the service account
eksctl create iamserviceaccount \
  --cluster aifeeders \
  --namespace aifeeders \
  --name daily-news-api \
  --attach-policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite \
  --approve
```

#### Step 7 — Mount EFS PVC for PublishedStore persistence

The `pageindex-mcp` service stores previously published article URLs. This must persist across pod restarts. Use EFS:

```bash
# Install EFS CSI Driver
kubectl apply -k \
  "github.com/kubernetes-sigs/aws-efs-csi-driver/deploy/kubernetes/overlays/stable/?ref=master"

# Create EFS filesystem via AWS Console or CLI, then create a StorageClass
# and PersistentVolumeClaim pointing to the EFS filesystem ID.
```

Update `deployment-pageindex-mcp.yaml` to mount the EFS PVC at the published store path.

#### Step 8 — Apply manifests and deploy

```bash
# Update image references in manifests to ECR URLs first
kubectl create namespace aifeeders
kubectl apply -f k8s/ -n aifeeders
kubectl get pods -n aifeeders
```

#### Step 9 — Authorise LinkedIn

Same port-forward OAuth flow as OpenShift:

```bash
kubectl port-forward svc/linkedin-mcp 8080:8080 -n aifeeders &
open http://localhost:8080/auth/linkedin
```

---

### Deploying to Azure AKS

#### Prerequisites

- Azure CLI (`az`) installed and logged in (`az login`)
- `kubectl` installed
- Docker installed and running
- An Azure Container Registry (ACR) created

#### Step 1 — Create the AKS cluster

```bash
RESOURCE_GROUP=aifeeders-rg
CLUSTER_NAME=aifeeders-aks
ACR_NAME=aifeederscr   # Must be globally unique

az group create --name $RESOURCE_GROUP --location eastus

az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic

az aks create \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --node-count 3 \
  --node-vm-size Standard_D2s_v3 \
  --network-plugin azure \
  --network-policy azure \
  --attach-acr $ACR_NAME \
  --enable-managed-identity \
  --generate-ssh-keys
```

**Note:** `--network-policy azure` enables Azure CNI Overlay NetworkPolicy enforcement natively — no additional Calico/Cilium installation needed.

#### Step 2 — Connect kubectl to the cluster

```bash
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME
kubectl get nodes
```

#### Step 3 — Build and push images to ACR

```bash
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
az acr login --name $ACR_NAME

# Build and push
TMPDIR=$(mktemp -d)
rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  /Users/kumar/AINewsfeederLinkedin/ "$TMPDIR/"

for svc in daily-news news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  docker build \
    -t $ACR_LOGIN_SERVER/aifeeders/$svc:latest \
    -f "$TMPDIR/Dockerfile.$svc" "$TMPDIR"
  docker push $ACR_LOGIN_SERVER/aifeeders/$svc:latest
done
```

#### Step 4 — Set up secrets with Azure Key Vault + Secrets Store CSI Driver

```bash
# Install Secrets Store CSI Driver with Azure provider
helm repo add csi-secrets-store-provider-azure \
  https://azure.github.io/secrets-store-csi-driver-provider-azure/charts
helm install csi-secrets-store-provider-azure \
  csi-secrets-store-provider-azure/csi-secrets-store-provider-azure \
  -n kube-system

# Create Key Vault
az keyvault create \
  --name aifeeders-kv \
  --resource-group $RESOURCE_GROUP \
  --location eastus

# Store secrets
az keyvault secret set --vault-name aifeeders-kv \
  --name gnews-api-key --value "your-key"
az keyvault secret set --vault-name aifeeders-kv \
  --name linkedin-client-secret --value "your-secret"
```

Then apply a `SecretProviderClass` manifest that maps Key Vault secrets into Kubernetes Secrets.

#### Step 5 — Configure Managed Identity (no static credentials)

```bash
# Enable workload identity on the cluster
az aks update \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --enable-workload-identity \
  --enable-oidc-issuer

# Grant the managed identity access to Key Vault
IDENTITY_CLIENT_ID=$(az aks show \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --query identityProfile.kubeletidentity.clientId -o tsv)

az keyvault set-policy \
  --name aifeeders-kv \
  --object-id $IDENTITY_CLIENT_ID \
  --secret-permissions get list
```

#### Step 6 — Mount Azure Files PVC for PublishedStore

```bash
# Create Azure Files storage class (or use the built-in azurefile-csi)
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: published-store-pvc
  namespace: aifeeders
spec:
  accessModes:
  - ReadWriteMany
  storageClassName: azurefile-csi
  resources:
    requests:
      storage: 1Gi
EOF
```

Update `deployment-pageindex-mcp.yaml` to mount this PVC.

#### Step 7 — Apply manifests and deploy

```bash
# Update image references in manifests to ACR URLs first
kubectl create namespace aifeeders
kubectl apply -f k8s/ -n aifeeders
kubectl get pods -n aifeeders
```

#### Step 8 — Authorise LinkedIn

```bash
kubectl port-forward svc/linkedin-mcp 8080:8080 -n aifeeders &
open http://localhost:8080/auth/linkedin
```

---

## 16. Glossary

This glossary explains every technical term used in this runbook for someone who may be seeing this system for the first time.

---

**ACR (Azure Container Registry)**  
Azure's managed Docker image registry. Functions like ECR but for Azure. Used when deploying to AKS.

**AKS (Azure Kubernetes Service)**  
Microsoft Azure's managed Kubernetes offering. Similar to EKS but on Azure infrastructure.

**Article pool**  
The collection of candidate articles gathered by GNews searches before scoring. Typically 30–80 unique articles per run after deduplication.

**Base64**  
An encoding scheme that converts binary data (like a string or file) into printable ASCII characters. Kubernetes Secrets store values as base64-encoded strings. To encode: `echo -n "value" | base64`. To decode: `echo "dmFsdWU=" | base64 -d`.

**BMP (Basic Multilingual Plane)**  
The first 65,536 Unicode code points (U+0000 to U+FFFF). Characters outside the BMP (including many emoji like 💼 and 🎓) require 2 UTF-16 code units and count as 2 characters in LinkedIn's character limit.

**BuildConfig**  
An OpenShift resource that defines how to build a container image from source code. Think of it as the recipe for building a Docker image. Once created, you trigger builds with `oc start-build`.

**Calico / Cilium**  
Open-source Kubernetes networking plugins that enforce NetworkPolicy rules. EKS does not enforce NetworkPolicy natively — you must install one of these. AKS with Azure CNI enforces NetworkPolicy natively.

**CronJob**  
A Kubernetes resource that runs a job (pod) on a schedule, similar to a Unix `cron` job. AIFeeders uses a CronJob at `0 8,16 * * *` (08:00 and 16:00 UTC every day).

**ECR (Elastic Container Registry)**  
AWS's managed Docker image registry. Stores container images for EKS deployments.

**EFS (Elastic File System)**  
AWS's managed network filesystem. Used as a PVC (PersistentVolumeClaim) to give pods persistent storage that survives restarts. Required for the `pageindex-mcp` published article store on EKS.

**EKS (Elastic Kubernetes Service)**  
AWS's managed Kubernetes offering. Requires manual installation of a NetworkPolicy engine (Calico or Cilium).

**GrammarAgent**  
New in build #83. A language model agent that proofreads the fully composed post for spelling, grammar, punctuation, and capitalisation errors before publication. Logs the character count before and after corrections.

**GNews**  
The news search API used by AIFeeders to find AI news articles. Free tier allows 100 API requests per day. AIFeeders uses two API keys for redundancy.

**IRSA (IAM Roles for Service Accounts)**  
An AWS mechanism that allows Kubernetes pods to assume IAM roles without static credentials being placed in the pod. The pod's service account is annotated with the IAM role ARN, and AWS issues temporary credentials automatically.

**Jev System One**  
AIFeeders' internal article scoring system. Uses the LLM to evaluate each candidate article for relevance and composite quality. Returns a score between 0 and 1 for each dimension.

**Job**  
A Kubernetes resource that runs one or more pods to completion. CronJobs create Jobs on schedule, and Jobs create the actual pods.

**LLM (Large Language Model)**  
An AI model that generates text. AIFeeders uses `qwen2-5-72b-instruct` via an IBM internal model gateway for story extraction, persona generation, grammar checking, and evaluation.

**MediaStorytellerAgent**  
The agent responsible for Stage 4 (story extraction). It reads the winning article and extracts a structured narrative: hook, analogy, perspective, second-order effect, and future question.

**Namespace**  
A Kubernetes concept for isolating resources within a cluster. AIFeeders uses the `aifeeders` namespace. All commands in this runbook include `-n aifeeders` to target the correct namespace.

**NetworkPolicy**  
A Kubernetes resource that controls which pods can talk to which other pods (and on which ports). Without NetworkPolicy, all pods can reach all other pods. AIFeeders uses a default-deny policy and explicit allow rules.

**OAuth**  
An open standard for delegated authorisation. LinkedIn uses OAuth to grant the AIFeeders pipeline permission to publish posts. The flow involves redirecting to LinkedIn, logging in, and LinkedIn returning an access token.

**OpenShift**  
Red Hat's enterprise Kubernetes distribution. Adds features like BuildConfigs (build container images from source on the cluster), Routes (HTTPS ingress), and enhanced RBAC. AIFeeders runs on OpenShift in production.

**oc**  
The OpenShift command-line client. Works like `kubectl` but with additional OpenShift-specific subcommands (`oc start-build`, `oc new-project`, etc.).

**PageIndex / pageindex-mcp**  
AIFeeders' published article store. Remembers which articles have already been published so the same story is never covered twice. The `pageindex-mcp` service provides a REST API for querying and updating this store.

**Persona**  
One of the four character voices in the post: Founder, Policy Analyst, Generalist, Engineer. Each writes a 3-5 sentence story passage from their own lived experience and perspective.

**PII (Personally Identifiable Information)**  
Information that could identify a real person (names, email addresses, phone numbers). The evaluator checks for PII before publishing.

**Pod**  
The smallest deployable unit in Kubernetes. A pod wraps one or more containers and runs on a worker node. When you see `oc get pods`, you are listing pods.

**PVC (PersistentVolumeClaim)**  
A Kubernetes request for persistent storage. Unlike a pod's local filesystem (which is wiped on restart), a PVC persists data across restarts. Required for `pageindex-mcp`.

**RBAC (Role-Based Access Control)**  
A Kubernetes mechanism for controlling what operations service accounts and users are allowed to perform. AIFeeders uses RBAC to grant the CronJob's service account permission to create pods.

**Reach score**  
A 0–100 score predicting how much LinkedIn engagement (impressions, reactions, comments) a post is likely to receive. Computed before publishing. If below the threshold, verdict is `HOLD` and the post is not published.

**Route**  
An OpenShift resource that exposes a service at an HTTPS URL. Think of it as a managed ingress with a TLS certificate. OpenShift creates and manages the certificate automatically.

**rsync**  
A Unix command for efficiently copying files, with support for excluding specific files or directories. Used in the build flow to copy the project source to a clean tmpdir, excluding `.venv/` and other large directories.

**S2I (Source-to-Image)**  
OpenShift's mechanism for building container images from source code without writing a Dockerfile. An S2I builder image knows how to install Python packages and assemble a runnable container. AIFeeders uses S2I for all its services.

**Secret**  
A Kubernetes resource for storing sensitive data (API keys, passwords, tokens). Values are base64-encoded (not encrypted by default at the API level — Kubernetes encryption at rest requires additional configuration). Always use Secrets instead of ConfigMaps for sensitive values.

**Sidecar**  
A secondary container running in the same pod as the main application container. Not used in AIFeeders, but common in Kubernetes architectures.

**tmpdir**  
A temporary directory created by `mktemp -d`. Used as a clean staging area for the build upload, so that large directories like `.venv/` are never included in the build context.

**UTF-16**  
A character encoding used by LinkedIn to measure post length. Characters outside the BMP cost 2 UTF-16 code units. AIFeeders uses `_linkedin_len()` to count UTF-16 units accurately and avoid post truncation.

**uv**  
A fast Python package installer and runner (written in Rust). AIFeeders uses uv instead of pip/poetry. The uv binary is at `/Users/kumar/.local/bin/uv`. Run tests with `/Users/kumar/.local/bin/uv run pytest`.

---

*AIFeeders Runbook — Build #83 · 210 tests · OpenShift `aifeeders` namespace*
