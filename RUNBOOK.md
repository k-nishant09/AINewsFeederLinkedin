# AIFeeders — Operations Runbook

> Build #76 · OpenShift `aifeeders` · Last updated: 2026-09
> For architecture details: [`ARCHITECTURE.md`](ARCHITECTURE.md)
> For project overview: [`README.md`](README.md)

This runbook is written for someone who has **never seen this system before**. Every section explains the *why*, not just the *what*. Real production failures are documented with their exact symptoms, root causes, and fixes.

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
15. [Deploying to EKS](#15-deploying-to-eks)
16. [Glossary](#16-glossary)

---

## 1. What This System Does in Plain English

AIFeeders is a **fully automated LinkedIn posting bot** for AI news. Every day at **08:00 UTC** and **16:00 UTC**, a Kubernetes CronJob fires. It:

1. Searches GNews for recent AI news (last 24 hours, 9 different search terms).
2. Scores every article using an AI model called **Jev System One** — picks the best one.
3. Sends the article to a language model (LLM) that writes a structured summary.
4. Generates four different audience perspectives (business / policy / generalist / tech).
5. Checks the content for quality, factual accuracy, PII, and safety using Jev.
6. Posts to LinkedIn — automatically.

**Nobody presses a button.** The post appears on LinkedIn by itself.

The system runs inside an OpenShift Kubernetes cluster in the `aifeeders` namespace. It is made up of 5 microservices (called MCP servers) that talk to each other over HTTP.

---

## 2. Normal Day — What to Check

**No action is needed on a normal day.** The CronJob runs automatically.

To verify a run completed successfully:

```bash
# Step 1 — Are there recent jobs?
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -5

# Step 2 — Did the last job succeed?
oc logs job/<latest-job-name> -n aifeeders | grep "Workflow complete"
# GOOD: "Workflow complete — status=PUBLISHED published=1 errors=0"
# BAD:  "Workflow complete — status=PUBLISHED published=0 errors=0"  ← no post
# BAD:  "Workflow complete — status=EVALUATED published=0 errors=0"  ← not published

# Step 3 — What was published today?
oc exec deployment/daily-news-api -n aifeeders -- \
  cat /tmp/aifeeders_published.json | python3 -m json.tool
```

If `published=0` and `errors=0`: `PUBLISHING_ENABLED` is probably `false` in the ConfigMap.
If `published=0` and `errors>0`: check the full logs for the specific error.

---

## 3. First-Time Setup on a New Cluster

### What you need installed

```bash
python3 --version   # 3.11+
oc version          # OpenShift CLI 4.x
jq --version        # JSON parsing in health checks
```

### Step 1 — Log in

```bash
# Get a token from the OpenShift web console:
# Top-right corner → your username → "Copy login command"
oc login --token=<token> --server=https://api.f80l034.fusion.tadn.ibm.com:6443

oc new-project aifeeders     # creates the namespace; skip if it already exists
oc project aifeeders         # make it the active project
```

> **Why does the token expire?** OpenShift session tokens are time-limited for security. When you get "Unauthorized" errors, your token has expired. Go back to the web console to get a new one.

### Step 2 — Apply ConfigMap

```bash
oc apply -f openshift/configmap.yaml -n aifeeders

# Verify it was created
oc get configmap daily-news-config -n aifeeders -o yaml | grep -A 20 "data:"
```

The ConfigMap holds **non-sensitive settings** (URLs, thresholds, feature flags). It is safe to version-control because it contains no secrets.

| Key | What it controls |
|---|---|
| `PUBLISHING_ENABLED` | Set to `"false"` to run the whole pipeline without posting to LinkedIn |
| `JEV_ENABLED` | Set to `"false"` to skip Jev scoring and use heuristics instead |
| `EVAL_FACTUALITY_THRESHOLD` | Minimum factuality score to allow publishing (default: 0.50) |
| `NEWS_MCP_URL` | Where the main app finds the news-mcp service |
| `LINKEDIN_MCP_URL` | Where the main app finds the linkedin-mcp service |

### Step 3 — Apply Secrets

> **Critical:** Never commit real secret values to git. The `secrets.yaml` in the repo contains placeholder base64 values. Always create your own copy with real values.

```bash
# How to base64-encode a value:
echo -n "your-actual-api-key" | base64

# Apply the secret (edit the file first with real values)
oc apply -f openshift/secrets.yaml -n aifeeders

# Verify the secret exists (values are hidden)
oc get secret daily-news-secrets -n aifeeders
```

Required secret keys:

| Key | What it is |
|---|---|
| `LLM_API_KEY` | Bearer token for the LLM endpoint |
| `GNEWS_API_KEY` | GNews primary API key |
| `GNEWS_API_KEY_2` | GNews secondary key (auto-rotates when primary hits 403) |
| `JEV_API_KEY` | Jev System One gateway bearer token |
| `LINKEDIN_CLIENT_ID` | LinkedIn OAuth app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn OAuth app secret |

### Step 4 — Apply RBAC

```bash
oc apply -f openshift/rbac.yaml -n aifeeders

# Verify the ServiceAccount was created
oc get serviceaccount daily-news -n aifeeders
```

> **What is RBAC?** Role-Based Access Control. The CronJob pod needs permission to create Job objects in the namespace. Without RBAC, the CronJob cannot launch itself. The ServiceAccount is given only the minimum permissions needed — nothing more.

### Step 5 — Create BuildConfigs (first time only)

```bash
oc apply -f openshift/buildconfigs/ -n aifeeders

# Set build history limits — this prevents old builds from accumulating
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done

oc get buildconfigs -n aifeeders   # verify all 5 exist
```

> **What is a BuildConfig?** It defines how to build a container image from source code. With `successfulBuildsHistoryLimit: 1`, OpenShift automatically deletes the previous build after each new successful one — keeping the namespace tidy.

### Step 6 — Build all images

```bash
# IMPORTANT: Never build from the raw project directory
# .venv/ is 270 MB — it will cause the build upload to time out
# Always rsync to a clean tmpdir first

TMPDIR=$(mktemp -d)
rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

echo "Upload size: $(du -sh $TMPDIR | cut -f1)"
# Must be < 5 MB. If it shows 200+ MB, .venv/ was not excluded.

# Build each service (one at a time, each takes ~2 minutes)
for svc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  echo "=== Building $svc ==="
  oc start-build $svc --from-dir="$TMPDIR" -n aifeeders --follow
done
```

### Step 7 — Deploy services

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
```

### Step 8 — Verify all pods are running

```bash
oc get pods -n aifeeders
# Expected — all pods in "Running" state, none in "CrashLoopBackOff" or "Pending":
# daily-news-api-xxx-yyy         1/1   Running   0   2m
# daily-news-api-xxx-zzz         1/1   Running   0   2m
# news-mcp-xxx-yyy               1/1   Running   0   2m
# news-mcp-xxx-zzz               1/1   Running   0   2m
# evaluation-mcp-xxx-yyy         1/1   Running   0   2m
# evaluation-mcp-xxx-zzz         1/1   Running   0   2m
# linkedin-mcp-xxx-yyy           1/1   Running   0   2m   ← ONLY ONE
# pageindex-mcp-xxx-yyy          1/1   Running   0   2m   ← ONLY ONE

# Health check all services
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health \
    | jq -r '.status // "unknown"' 2>/dev/null || echo "EXEC FAILED"
done
```

### Step 9 — Authorise LinkedIn (first time)

LinkedIn requires an interactive browser OAuth flow. This only needs to happen once (until the token expires in 60 days or the `linkedin-mcp` pod restarts).

```bash
# Open a tunnel from your laptop to the linkedin-mcp service
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
PF_PID=$!

# Open this URL in your browser:
# http://localhost:8080/auth/linkedin
# Click "Allow" to grant LinkedIn permissions
# You'll see a success message

# Verify the token was stored
curl -s http://localhost:8080/health | jq .

# Stop the tunnel
kill $PF_PID
```

> **Important:** The OAuth token is stored only in `linkedin-mcp` pod memory. It is lost if the pod restarts. Any time you redeploy `linkedin-mcp`, you must re-authorise. Set a calendar reminder for 55 days from now — LinkedIn tokens expire after 60 days.

### Step 10 — Smoke test (run without posting)

```bash
# Disable publishing (won't post to LinkedIn)
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Trigger a test run
SMOKE_JOB=$(oc create job smoke-$(date +%s) \
  --from=cronjob/daily-ai-news-morning -n aifeeders \
  --output=name | sed 's|job.batch/||')

# Wait for it to finish, then check the result
sleep 30
oc logs job/$SMOKE_JOB -n aifeeders | tail -5
# Expected: "Workflow complete — status=EVALUATED published=0 errors=0"

# Re-enable publishing
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### Step 11 — First live run

```bash
LIVE_JOB=$(oc create job live-$(date +%s) \
  --from=cronjob/daily-ai-news-morning -n aifeeders \
  --output=name | sed 's|job.batch/||')
echo "Job name: $LIVE_JOB"

sleep 10
oc logs -f job/$LIVE_JOB -n aifeeders | \
  grep -E "jev_prefilter|post published|Workflow complete|ERROR"

# Expected success output:
# [RUN-xxx] jev_prefilter: #1 article_id=news-abc composite=0.78 ...
# [RUN-xxx] post published post_urn=urn:li:share:... status=published
# Workflow complete — status=PUBLISHED published=1 errors=0
```

---

## 4. Build Flow → Launch Flow

### When to rebuild

Rebuild the `daily-news` image any time you change:
- `src/daily_news/` — application code
- `prompts/` — persona prompt files
- `openshift/configmap.yaml` — no rebuild needed, just re-apply
- `openshift/secrets.yaml` — no rebuild needed, just re-apply + rollout restart

### The build sequence

```bash
# ── 1. Test — never skip this ────────────────────────────────────────────────
source .venv/bin/activate
python -m pytest tests/unit tests/workflow -q --tb=short
# All tests must pass before proceeding

# ── 2. Build ─────────────────────────────────────────────────────────────────
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
# Watch for "Push successful" at the end — that means the image is ready

# ── 3. Roll out ───────────────────────────────────────────────────────────────
# Rolling restart — new pods start before old ones stop (zero downtime)
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders --timeout=90s
# Expected: "deployment 'daily-news-api' successfully rolled out"

# ── 4. Run ────────────────────────────────────────────────────────────────────
LIVE_JOB=$(oc create job live-$(date +%s) \
  --from=cronjob/daily-ai-news-morning -n aifeeders \
  --output=name | sed 's|job.batch/||')
oc logs -f job/$LIVE_JOB -n aifeeders | grep -E "status=|published|ERROR"
```

### What happens to the old build automatically

```
Before:  daily-news-75 (image in registry, running)

oc start-build → daily-news-76
  1. BuildPod created, pip install runs, source copied
  2. New :latest image pushed to internal registry
  3. Build record daily-news-75 deleted  ← automatic (successfulBuildsHistoryLimit: 1)
  4. BuildPod terminates (self-cleaning)

oc rollout restart
  1. New pod starts, pulls :latest (= build #76), passes health probe
  2. Old pod terminates
  3. Process repeats for each replica
  4. Zero downtime: at least 1 pod always running
```

### If you changed an MCP server

If you modified `mcp_servers/news_mcp/`, `mcp_servers/linkedin_mcp/`, etc., rebuild that service too:

```bash
oc start-build news-mcp --from-dir="$TMPDIR" -n aifeeders --follow
oc rollout restart deployment/news-mcp -n aifeeders

# If you rebuilt linkedin-mcp: you MUST re-authorise LinkedIn
oc rollout restart deployment/linkedin-mcp -n aifeeders
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# http://localhost:8080/auth/linkedin
```

---

## 5. Triggering a Run Manually

### From the CLI (most common)

```bash
# Trigger from the morning CronJob template
oc create job manual-$(date +%s) --from=cronjob/daily-ai-news-morning -n aifeeders

# Follow logs in real time
oc logs -f job/manual-<timestamp> -n aifeeders
```

### Dry run (no LinkedIn post)

```bash
# Disable publishing
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

oc create job dry-$(date +%s) --from=cronjob/daily-ai-news-morning -n aifeeders

# Remember to re-enable when done
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### Via the REST API

```bash
# Open a tunnel to the API service
oc port-forward svc/daily-news-api 9090:8000 -n aifeeders &

# Trigger a run
curl -X POST http://localhost:9090/run \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}' | jq .

# Check run status
curl http://localhost:9090/status/<run_id> | jq .

kill %1   # stop the tunnel
```

---

## 6. How to Read the Logs

### A healthy run — what you should see

```
INFO Starting daily news workflow — run_id=RUN-6FFC7CF7862F

INFO [RUN-6FFC7CF7862F] discover_news started
INFO [RUN-6FFC7CF7862F] discovered 5 raw articles

INFO [RUN-6FFC7CF7862F] deduplicated: 5 raw → 4 unique → 4 unpublished-today

INFO [RUN-6FFC7CF7862F] jev_prefilter: #1 article_id=news-4cf396b3c7e1
     relevance=0.89 engagement=0.60 composite=0.78 personas=['business','policy','genz','linkedin']

INFO [RUN-6FFC7CF7862F] summarised 1 articles

INFO [RUN-6FFC7CF7862F] jev_route_personas: merged → ['genz','business','policy','linkedin']
INFO [RUN-6FFC7CF7862F] generate_personas: running ['genz','business','policy','linkedin']

INFO [RUN-6FFC7CF7862F] eval article=news-4cf396b3c7e1 decision=PASS
     factuality=0.69 groundedness=0.60 hallucination=0.38

INFO [RUN-6FFC7CF7862F] post composed article=news-4cf396b3c7e1
     python_len=2557 linkedin_utf16_len=2579

INFO [RUN-6FFC7CF7862F] post published post_urn=urn:li:share:7509097557713833984 status=published

INFO [RUN-6FFC7CF7862F] Comments API not available (PERMISSION_ERROR) — personas embedded in post body.

INFO Workflow complete — status=PUBLISHED published=1 errors=0
```

### What each line means

| Log pattern | What it means |
|---|---|
| `discovered N raw articles` | GNews returned N articles across all 9 queries |
| `deduplicated: X raw → Y unique → Z unpublished-today` | X total → Y after removing duplicates → Z after removing already-published |
| `jev_prefilter: #1 ... composite=0.78` | Jev scored all articles; this one ranked highest |
| `jev_route_personas: merged → [...]` | These LLM persona calls will run (others are skipped) |
| `eval decision=PASS factuality=0.69` | Article passed all quality checks; safe to publish |
| `post composed ... linkedin_utf16_len=2579` | Post is 2579 LinkedIn characters (limit is 2900) |
| `post published post_urn=urn:li:share:...` | LinkedIn accepted the post — it's live |
| `Comments API not available (PERMISSION_ERROR)` | **Normal** — LinkedIn Comments API not approved for this app |
| `Workflow complete — status=PUBLISHED published=1 errors=0` | ✅ Successful run |

### Warning signs in the logs

| Log pattern | What is wrong | What to do |
|---|---|---|
| `news search failed for ...: ` *(empty error)* | NetworkPolicy blocking the pod | Check pod labels — see Issue #1 |
| `discovered 0 raw articles` | GNews quota hit | Wait for midnight UTC reset |
| `all articles already published today` | Dedup store blocking re-run | Normal — wait until tomorrow |
| `jev_prefilter failed (ReadTimeout)` | Jev gateway slow | Auto-fallback — posts still work |
| `eval decision=BLOCK` | PII or injection detected in article | Review source article |
| `eval decision=REGENERATE` | Quality below threshold | Auto-retries up to 2× |
| `post FAILED http=401` | LinkedIn token expired | Re-authorise (see §8 Issue #4) |
| `post FAILED http=429` | LinkedIn rate limit | Wait ~10 min |
| `Workflow complete ... errors=N` | Non-fatal errors | Read full logs for details |

---

## 7. Checking Service Health

### Quick health check

```bash
# All pods at a glance
oc get pods -n aifeeders

# Per-service health endpoints
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp daily-news-api; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health \
    | jq -r '.status // "unknown"' 2>/dev/null || echo "EXEC FAILED"
done
# Expected: "healthy" or "ready" for each service

# CronJob schedule
oc get cronjobs -n aifeeders
# Expected: daily-ai-news-morning (0 8 * * *) and daily-ai-news-afternoon (0 16 * * *)
```

### Check GNews key status

```bash
oc exec deployment/news-mcp -n aifeeders -- \
  curl -s http://localhost:8000/health | jq '{keys_configured, active_key_index, active_key_prefix}'
# Good: {"keys_configured": 2, "active_key_index": 1, "active_key_prefix": "e6f0db13..."}
# Bad:  {"keys_configured": 1, ...}  ← only one key configured; no fallback available
```

### Check Jev gateway

```bash
curl -s https://<your-jev-gateway>/health | jq .
# Expected: {"status": "ready", "model": "Qwen/Qwen3.5-2B", "method": "lora_decision_head"}
# If unreachable: pipeline still runs using fallback heuristics
```

### Check what was published today

```bash
# Published store (articles in last 7 days)
oc exec deployment/daily-news-api -n aifeeders -- \
  cat /tmp/aifeeders_published.json | python3 -m json.tool
# Example: {"news-4cf396b3c7e1:2026-09-25": "2026-09-25T03:52:54+00:00"}

# LinkedIn audit (post URNs since pod start)
oc exec deployment/linkedin-mcp -n aifeeders -- \
  curl -s http://localhost:8000/audit | jq .
```

---

## 8. Production Issues — Exact Symptoms, Root Causes, Fixes

Every real failure we encountered on this system is documented here.

---

### P1 — CronJob pods can't reach MCP services (NetworkPolicy label bug)

**Date first seen:** 2026-09-25
**Build affected:** All builds before the cronjob.yaml was fixed

**Exact symptom in logs:**
```
2026-09-25 03:47:16 WARNING news search failed for artificial intelligence LLM agentic AI model:
2026-09-25 03:48:16 WARNING news search failed for artificial intelligence finance investment funding:
[... 7 more warnings, all with empty error message after the colon ...]
discovered 0 raw articles
```

**Why the error message is empty:** The exception is a `TimeoutError` whose `str()` representation is an empty string. Python's `f"... {exc}"` produces nothing after the colon.

**Root cause (detailed):**

The `default-deny-all` NetworkPolicy blocks all pod-to-pod traffic unless an explicit allow rule covers it. The `allow-api-to-mcps` NetworkPolicy allows traffic from pods labelled `app=daily-news-api` or `app=daily-news-worker` to pods labelled `role=mcp-server`.

CronJob pods get automatic Kubernetes labels like `batch.kubernetes.io/job-name`. They do **not** get custom labels unless you set them explicitly under `template.metadata.labels`.

The bug was that our `cronjob.yaml` had `labels` under `spec` instead of `metadata`:

```yaml
# BUG: labels under template.spec — this field does not exist in Kubernetes
template:
  spec:
    labels:               ← Kubernetes ignores this completely
      app: daily-news-worker

# FIX: labels under template.metadata
template:
  metadata:
    labels:
      app: daily-news-worker   ← NetworkPolicy now sees this label
  spec:
    ...
```

Because the label was silently ignored, CronJob pods had no `app` label. The NetworkPolicy blocked all their outbound calls to MCP services. Every `search_latest` call timed out after 60 seconds.

**How to diagnose any networking issue:**
```bash
# 1. Check what labels the pod actually has
oc get pod <pod-name> -o jsonpath='{.metadata.labels}' | python3 -m json.tool

# 2. Test connectivity from inside the pod
oc exec <pod-name> -- curl -s --max-time 5 http://news-mcp:8000/health
# Timeout = NetworkPolicy blocking
# Refused = service not running
# JSON = working

# 3. List all NetworkPolicies
oc get networkpolicy -n aifeeders

# 4. Check which pods a policy allows
oc describe networkpolicy allow-api-to-mcps -n aifeeders
```

**Fix:**
```bash
oc patch cronjob daily-ai-news-morning --type='json' \
  -p='[{"op":"add","path":"/spec/jobTemplate/spec/template/metadata/labels",
       "value":{"app":"daily-news-worker"}}]'
oc patch cronjob daily-ai-news-afternoon --type='json' \
  -p='[{"op":"add","path":"/spec/jobTemplate/spec/template/metadata/labels",
       "value":{"app":"daily-news-worker"}}]'
```

---

### P2 — Build upload times out (`.venv/` included in context)

**Exact symptom:**
```
oc start-build daily-news --from-dir=.
Uploading directory "." as binary input for the build...
[no output for 5 minutes]
error: build/daily-news-XX failed — error streaming build logs: unexpected EOF
```

**Root cause:**
`oc start-build --from-dir=.` tarballs the entire current directory and streams it to the OpenShift build pod over the API. `.venv/` contains ~270 MB of Python packages. The upload takes 4–5 minutes and exceeds the client-side streaming timeout.

**Fix: always rsync to a tmpdir first**
```bash
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"
echo "Upload size: $(du -sh $TMPDIR | cut -f1)"   # Must be < 5 MB
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
```

**Rule:** Never run `oc start-build --from-dir=.` directly from the project root.

---

### P3 — GNews quota exhausted (403 Forbidden)

**Exact symptom:**
```
WARNING news search failed for artificial intelligence LLM agentic AI model:
         403 Client Error: Forbidden for url: https://gnews.io/api/v4/search?...
discovered 0 raw articles
Workflow complete — status=PUBLISHED published=0 errors=9
```

**Root cause:**
GNews free plan: 100 requests/day per API key. Pipeline uses 9 requests per run. Running 10+ manual test jobs in one day exhausts the daily quota.

**Immediate fix:** Wait for midnight UTC (quota resets daily at midnight UTC).

**Permanent fix:** Configure `GNEWS_API_KEY_2` so the server auto-rotates on 403:
```bash
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"GNEWS_API_KEY_2\":\"$(echo -n 'your-key-2' | base64)\"}}"
oc rollout restart deployment/news-mcp -n aifeeders

# Verify both keys loaded
oc exec deployment/news-mcp -n aifeeders -- \
  curl -s http://localhost:8000/health | jq '{keys_configured,active_key_prefix}'
# Expected: {"keys_configured": 2, "active_key_prefix": "e6f0db13..."}
```

How rotation works: when `news-mcp` receives HTTP 403 from GNews, it automatically switches to the second key for that call and all subsequent calls in the process lifetime.

---

### P4 — LinkedIn token expired (HTTP 401)

**Exact symptom:**
```
post FAILED http=401 article=news-4cf396b3c7e1
Workflow complete — status=PUBLISHED published=0 errors=1
```

**Root cause:**
LinkedIn OAuth access tokens expire after 60 days. The token is stored only in `linkedin-mcp` pod memory and is not persisted anywhere.

**Fix:**
```bash
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# Open in browser: http://localhost:8080/auth/linkedin
# Click through the OAuth flow (~30 seconds)
# Verify:
curl -s http://localhost:8080/health | jq .token_status
# Should show "valid"
kill %1
```

**Prevention:** Set a calendar reminder every 55 days: "Renew LinkedIn OAuth token (expires in 5 days)".

---

### P5 — Token lost after linkedin-mcp pod restart

**Exact symptom:**
Posts worked yesterday, now failing with `401` even though the token was recently renewed.

**Root cause:**
The OAuth token is **in-memory only**. Any event that restarts the `linkedin-mcp` pod loses the token:
- Manual `oc rollout restart`
- Node eviction
- OOM kill
- Cluster maintenance

**Fix:** Same as P4 — re-authorise via OAuth after any restart.

**How to detect a restart without checking logs:**
```bash
oc get pod -l app=linkedin-mcp -n aifeeders
# Check the "RESTARTS" column and the "AGE" — if age is very recent, it restarted
```

---

### P6 — Post truncated mid-sentence on LinkedIn

**Exact symptom:**
Post in logs looks complete and reports `linkedin_utf16_len=2870`. On LinkedIn the post ends abruptly in the middle of a sentence.

**Root cause:**
LinkedIn's API counts characters as **UTF-16 code units** (matching JavaScript's `String.length`). Python's `len()` counts Unicode **code points**. Characters outside Unicode Basic Multilingual Plane (U+10000+) — which includes most emoji — each cost:
- Python `len()`: 1
- LinkedIn API: 2 (they are encoded as surrogate pairs in UTF-16)

A post with 30 emoji that Python reports as 2970 characters is actually ~3000 LinkedIn units → truncated.

**Fix (in [`publisher_agent.py`](src/daily_news/agents/publisher_agent.py)):**
```python
def _linkedin_len(text: str) -> int:
    """Count characters as LinkedIn does: UTF-16 code units."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)
```

**Safe limit:** 2900 (not 3000) — 100-unit safety margin.

If you ever see this again, check:
```bash
oc logs job/<latest-job> -n aifeeders | grep "linkedin_utf16_len"
# If linkedin_utf16_len > 2900, there is a budget regression in publisher_agent.py
```

---

### P7 — `PERMISSION_ERROR` on LinkedIn Comments API

**Exact symptom:**
```
INFO Comments API not available (PERMISSION_ERROR) — personas embedded in post body. Skipping remaining.
```

**Root cause:**
LinkedIn's Comments API requires a "Community Management API" product approval from LinkedIn. Basic developer apps do not have this. Calling the Comments API without the approval always returns `PERMISSION_ERROR`.

**Is this a problem?** No. All 4 persona perspectives are embedded in the post body. The post is complete. No content is lost.

**If you get LinkedIn Comments API approved:** No code change needed. The publisher agent already attempts persona comments after every post and will use them if the API allows it.

---

### P8 — Jev gateway timeout — silent fallback

**Exact symptom:**
```
WARNING jev_prefilter failed (ReadTimeout) — falling back to [:1] selection
```
The post still gets published, but Jev scores are missing from the post body.

**Root cause:**
The Jev gateway was busy (high load, restart, or maintenance). The httpx request timeout (30s) fired before a response arrived.

**Behaviour:** The pipeline **does not stop**. It falls back:
- Article selection: takes the first article without scoring
- Persona routing: runs all 4 personas
- Evaluation: uses LLM-based `EvaluationMCPClient` instead of Jev

**If Jev stays down for more than a day:**
```bash
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"false"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
# Posts will continue without Jev scoring
# Re-enable when Jev is back:
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"true"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
```

---

### P9 — Old builds accumulating in the namespace

**Exact symptom:**
```
oc get builds -n aifeeders
daily-news-68  Complete
daily-news-69  Complete
daily-news-70  Complete
... (8 entries)
```

**Root cause:**
`successfulBuildsHistoryLimit` was not set on the BuildConfig. OpenShift keeps every completed build indefinitely by default.

**Fix:**
```bash
# Set the limit on all BuildConfigs
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done

# Delete existing old builds manually
oc get builds -n aifeeders -l buildconfig=daily-news \
  --field-selector status.phase=Complete \
  -o name | head -n -1 | xargs oc delete -n aifeeders
```

With the limit set to 1, the cleanup is automatic on every future build.

---

### P10 — OpenShift session token expired

**Exact symptom:**
```
oc get pods -n aifeeders
error: You must be logged in to the server (Unauthorized)
```

**Root cause:** OpenShift session tokens are time-limited. The token in `~/.kube/config` expired.

**Fix:**
1. Open the OpenShift web console: `https://console-openshift-console.apps.f80l034.fusion.tadn.ibm.com`
2. Top-right → your username → "Copy login command"
3. Paste the `oc login --token=... --server=...` command in your terminal

This is normal security behaviour — tokens expire to limit the window of exposure if a token is stolen.

---

### P11 — `PUBLISHING_ENABLED=false` — pipeline runs but never posts

**Exact symptom:**
```
Workflow complete — status=EVALUATED published=0 errors=0
```
No errors, but also no LinkedIn post. This happens silently — the logs don't explicitly say "publishing is disabled".

**Fix:**
```bash
oc get configmap daily-news-config -n aifeeders -o jsonpath='{.data.PUBLISHING_ENABLED}'
# If this shows "false", re-enable:
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
# Note: no pod restart needed — the ConfigMap value is read at runtime
```

---

## 9. API Quota and Key Rotation

### GNews — rotating to a second key

GNews free tier: 100 requests/day. Pipeline uses 18/day (9 queries × 2 runs). Manual test runs consume quota quickly.

```bash
# Add or update the secondary key
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"GNEWS_API_KEY_2\":\"$(echo -n 'key2value' | base64)\"}}"

# Restart news-mcp to load the new key
oc rollout restart deployment/news-mcp -n aifeeders
oc rollout status deployment/news-mcp -n aifeeders

# Verify it loaded
oc exec deployment/news-mcp -n aifeeders -- \
  curl -s http://localhost:8000/health | jq '{keys_configured,active_key_index}'
```

Key rotation is automatic in the `news-mcp` server code. On HTTP 403, it switches to the next key in the pool without any manual intervention.

### Rotating the primary GNews key

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"GNEWS_API_KEY\":\"$(echo -n 'new-key-1' | base64)\"}}"
oc rollout restart deployment/news-mcp -n aifeeders
```

### LinkedIn OAuth token (60-day rotation)

```bash
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# http://localhost:8080/auth/linkedin
# Complete the OAuth flow
kill %1
```

### Jev API key rotation

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"JEV_API_KEY\":\"$(echo -n 'new-jev-key' | base64)\"}}"
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders
```

### LLM API key rotation

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"LLM_API_KEY\":\"$(echo -n 'new-llm-key' | base64)\"}}"
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders
```

### Emergency: disable Jev when its gateway is down

```bash
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"false"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
# Pipeline runs with heuristics; posts still publish
# Re-enable when Jev is back:
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"true"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
```

---

## 10. Networking — Understanding the NetworkPolicy

**This is the most common source of mysterious failures in this system.** Read this section carefully.

### The fundamental rule: default-deny-all

The namespace uses a `default-deny-all` NetworkPolicy. This means:
- **All pod-to-pod traffic is blocked by default**
- Traffic is only allowed when there is an explicit `allow` rule
- If a pod is missing a label, it is silently blocked — no error message, just connection timeout

### The allow rules

```
NetworkPolicy: default-deny-all
  → blocks ALL pod-to-pod traffic in the namespace

NetworkPolicy: allow-api-to-mcps
  → allows: pods with label app=daily-news-api
         OR pods with label app=daily-news-worker
    to reach: pods with label role=mcp-server
    on port: 8000

NetworkPolicy: allow-egress-internet
  → allows ALL pods to reach the external internet
    (needed for GNews, LinkedIn API, Jev gateway, LLM endpoint)

NetworkPolicy: allow-router-to-api
  → allows the OpenShift router to reach daily-news-api
    (needed for the external-facing API route)

NetworkPolicy: allow-router-to-linkedin-mcp
  → allows the OpenShift router to reach linkedin-mcp
    (needed for the LinkedIn OAuth callback URL)
```

### Required labels for each pod type

| Pod type | Label needed | Why |
|---|---|---|
| `daily-news-api` Deployment pods | `app: daily-news-api` | Can call all 4 MCP services |
| CronJob pods | `app: daily-news-worker` | Can call all 4 MCP services |
| MCP server pods | `role: mcp-server` | Can accept calls from the above |

### Diagnosing a network failure

```bash
# Step 1: What labels does the affected pod have?
oc get pod <pod-name> -o jsonpath='{.metadata.labels}' | python3 -m json.tool

# Step 2: Can it reach the target service?
oc exec <pod-name> -- curl -v --max-time 5 http://news-mcp:8000/health 2>&1
# "Trying ... Connection timed out" = blocked by NetworkPolicy
# "Connection refused" = service not listening
# 200 OK = working

# Step 3: Does the target pod have the right label?
oc get pods -l role=mcp-server -n aifeeders
# If empty: MCP deployments don't have the role=mcp-server label
```

### Service DNS names (how pods find each other)

Inside the cluster, Kubernetes Services are reachable by their name:

```
http://news-mcp:8000        → news-mcp service
http://evaluation-mcp:8000  → evaluation-mcp service
http://linkedin-mcp:8000    → linkedin-mcp service
http://pageindex-mcp:8000   → pageindex-mcp service
```

These DNS names only resolve from within the cluster. They do not work from your laptop.

---

## 11. Scaling and Resource Management

### What scales automatically

`daily-news-api` has a Horizontal Pod Autoscaler (HPA):
- Minimum replicas: 2
- Maximum replicas: 4
- Scale-up trigger: CPU > 70% averaged over 60 seconds
- Scale-down trigger: CPU < 30% for 5 minutes

```bash
# Check current HPA status
oc get hpa -n aifeeders
oc describe hpa daily-news-api -n aifeeders
```

### What does NOT scale (and why)

| Service | Why single replica only |
|---|---|
| `linkedin-mcp` | OAuth token in memory — two replicas would each need separate OAuth flows |
| `pageindex-mcp` | RAG index rebuilt per-run — no benefit to multiple replicas; each would have a different index |

### PodDisruptionBudget

`daily-news-api` has a PodDisruptionBudget: `minAvailable: 1`. This means:
- During Kubernetes node maintenance, at least 1 `daily-news-api` pod stays running
- Without this, an upgrade could briefly kill all pods simultaneously

```bash
oc get pdb -n aifeeders
```

### Resource requests and limits

Resources are defined in each deployment manifest. Approximate values:

| Service | Memory request | Memory limit | CPU request | CPU limit |
|---|---|---|---|---|
| `daily-news-api` | 256Mi | 2Gi | 500m | 2 |
| `news-mcp` | 128Mi | 256Mi | 100m | 500m |
| `evaluation-mcp` | 256Mi | 512Mi | 200m | 1 |
| `linkedin-mcp` | 128Mi | 256Mi | 100m | 500m |
| `pageindex-mcp` | 128Mi | 512Mi | 200m | 1 |

> **What are requests and limits?** Kubernetes *schedules* pods based on requests (the pod is guaranteed this much). It *kills* pods that exceed limits. Setting appropriate limits prevents one runaway pod from starving the whole node.

### Checking resource usage

```bash
oc top pods -n aifeeders   # real-time CPU + memory per pod
oc top nodes               # node-level resource usage
```

---

## 12. Monitoring and Observability

### Three layers of observability

**Layer 1 — Structured logs** (always available)

Every log line includes `[run_id]` and context:
```bash
# All logs from today's runs
oc logs -l app=daily-news-api -n aifeeders --since=12h | grep "Workflow complete"

# Jev decision data
oc logs job/<job-name> -n aifeeders | grep "jev_prefilter:"

# All errors across all runs today
oc logs -l app=daily-news-api -n aifeeders --since=12h | grep -E "ERROR|FAILED"
```

**Layer 2 — Langfuse traces** (requires `LANGFUSE_SECRET_KEY` to be set)

Every LLM call, Jev call, and publish attempt creates a Langfuse span. Access at https://us.cloud.langfuse.com. Filter by `run_id` from the logs.

Useful for:
- How long did each LLM call take?
- What did the model receive as input?
- Why did an evaluation fail?

**Layer 3 — Prometheus metrics** (at `:8000/metrics` on `daily-news-api`)

```bash
oc port-forward svc/daily-news-api 9090:8000 -n aifeeders &
curl http://localhost:9090/metrics | grep aifeeders
kill %1
```

### Published store — what ran and when

```bash
oc exec deployment/daily-news-api -n aifeeders -- \
  cat /tmp/aifeeders_published.json | python3 -m json.tool
```

This file shows every article published in the last 7 days. If it is empty, either no articles have been published yet, or the pod restarted (the file is at `/tmp` and is ephemeral).

### LinkedIn audit log

```bash
oc exec deployment/linkedin-mcp -n aifeeders -- \
  curl -s http://localhost:8000/audit | jq .
```

Returns all post URNs published since the current `linkedin-mcp` pod started, with timestamps, character counts, and publication keys.

### Setting up alerts (recommended)

Set a daily check reminder if Langfuse or Prometheus alerting isn't configured:
1. Check `oc get jobs -n aifeeders` — at least 2 jobs should have run today
2. Check `grep "Workflow complete" <logs>` — should show `published=1 errors=0`
3. Check LinkedIn directly — open your profile and verify the post appeared

---

## 13. Security Practices

### API keys — never in code or ConfigMap

All API keys live in the Kubernetes Secret `daily-news-secrets`. They are:
- Injected as environment variables at pod start
- **Never written to logs** — the code explicitly avoids logging secret values
- **Never in ConfigMap** — ConfigMap values are visible to anyone with namespace read access
- **Never committed to git** — `secrets.yaml` in the repo contains only base64-encoded placeholder values

```bash
# To check what is in the secret (values are hidden):
oc get secret daily-news-secrets -n aifeeders -o yaml | grep -v "  [A-Za-z0-9+/=]\{20\}"

# To rotate any key (no downtime needed for most):
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"KEY_NAME\":\"$(echo -n 'new-value' | base64)\"}}"
# Then restart the pods that use it:
oc rollout restart deployment/<service-name> -n aifeeders
```

### NetworkPolicy — zero trust between pods

See §10. The `default-deny-all` policy means no pod can communicate with any other pod unless explicitly allowed. This limits the blast radius of a compromised pod — a compromised `news-mcp` pod cannot directly call the LinkedIn API.

### Content safety gates (cannot be disabled)

Before any article is published, Jev evaluates three hard gates in `evaluation_agent.py`:
- `pii_detected > 0.5` → **BLOCK** — article contains personal identifiable information
- `prompt_injection_detected > 0.5` → **BLOCK** — article contains adversarial AI prompts
- `policy_check = FAIL` → **BLOCK** — article violates content policy

These gates are **not controlled by `JEV_ENABLED`**. Even if Jev is disabled, the LLM-based evaluation fallback (`EvaluationMCPClient`) runs equivalent checks.

### LinkedIn OAuth token security

- The token is stored only in `linkedin-mcp` process memory
- It is never written to disk, logs, a database, or a ConfigMap
- If `linkedin-mcp` is compromised, the attacker can post to LinkedIn — but cannot access other services (NetworkPolicy restricts what `linkedin-mcp` can reach)
- Token rotation every 60 days limits the exposure window

### Build security

- S2I builds run in isolated BuildPods with no access to the host
- The `pip install` inside the build uses a layer cache — only packages declared in `pyproject.toml` are installed
- `.env` files are excluded from the build context (rsync excludes `.env` and `.env.*`)

### RBAC — minimum permissions

The CronJob ServiceAccount (`daily-news`) has only:
- `create` Jobs in the `aifeeders` namespace
- `get`/`list` Pods for health checks
- No cluster-level permissions
- No access to Secrets (secrets are injected by the pod spec at the Kubernetes level)

---

## 14. Cleaning the Namespace

### Remove completed jobs

```bash
# Delete all succeeded jobs
oc delete jobs -n aifeeders --field-selector status.successful=1

# Delete all failed jobs
oc delete jobs -n aifeeders --field-selector status.failed=1
```

### Remove old builds

With `successfulBuildsHistoryLimit: 1` set, this should be automatic. If it gets out of hand:

```bash
oc get builds -n aifeeders   # see all builds
# Delete all but the latest for each service:
oc delete builds -n aifeeders -l buildconfig=daily-news \
  --field-selector status.phase=Complete
```

### Reset the published store

Use when you want to re-publish an article that was already published today (e.g. after a bug fix):

```bash
# Option A: Clear only today's entries (keeps historical data)
oc exec deployment/daily-news-api -n aifeeders -- python3 -c "
from daily_news.agents.published_store import published_store
published_store.clear_today()
print('Cleared. Remaining:', list(published_store._cache.keys()))
"

# Option B: Delete the file entirely (resets all 7 days)
oc exec deployment/daily-news-api -n aifeeders -- rm /tmp/aifeeders_published.json
echo "Store deleted — will recreate fresh on next run"
```

### Full namespace audit

```bash
echo "=== Pods ===" && oc get pods -n aifeeders
echo "=== Deployments ===" && oc get deployments -n aifeeders
echo "=== Services ===" && oc get services -n aifeeders
echo "=== CronJobs ===" && oc get cronjobs -n aifeeders
echo "=== Jobs ===" && oc get jobs -n aifeeders
echo "=== Builds ===" && oc get builds -n aifeeders
echo "=== ConfigMaps ===" && oc get configmaps -n aifeeders
echo "=== Secrets ===" && oc get secrets -n aifeeders
echo "=== NetworkPolicies ===" && oc get networkpolicies -n aifeeders
echo "=== HPA ===" && oc get hpa -n aifeeders
echo "=== PDB ===" && oc get pdb -n aifeeders
```

---

## 15. Deploying to EKS

Everything about the application logic, YAML manifests, and pipeline is identical between OpenShift and EKS. Only the image build and registry URL change.

### What changes

| Aspect | OpenShift | EKS |
|---|---|---|
| Image build | `oc start-build` (in-cluster, no Docker daemon) | `docker build` + `docker push` to ECR |
| Image registry | `image-registry.openshift-image-registry.svc:5000/aifeeders/<svc>:latest` | `<account>.dkr.ecr.<region>.amazonaws.com/aifeeders/<svc>:latest` |
| Secrets management | `oc create secret generic` | External Secrets Operator + AWS Secrets Manager |
| LinkedIn OAuth route | OpenShift Route (auto-generated) | AWS ALB Ingress or `kubectl port-forward` |
| NetworkPolicy enforcement | Built-in | Must install Calico or Cilium CNI |
| PublishedStore | `/tmp` (ephemeral) | EFS PVC for persistence |

### EKS build and deploy

```bash
export AWS_ACCOUNT=123456789012
export AWS_REGION=us-east-1
export ECR_REGISTRY=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Create ECR repos (once)
for svc in daily-news linkedin-mcp news-mcp evaluation-mcp pageindex-mcp; do
  aws ecr create-repository --repository-name aifeeders/$svc --region $AWS_REGION
done

# Build and push (same rsync pattern as OpenShift)
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' . "$TMPDIR/"

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

for svc in daily-news linkedin-mcp news-mcp evaluation-mcp pageindex-mcp; do
  docker build -t $ECR_REGISTRY/aifeeders/$svc:latest -f Dockerfile "$TMPDIR"
  docker push $ECR_REGISTRY/aifeeders/$svc:latest
done

# Deploy (update image: field in each deployment YAML, then apply)
kubectl apply -f openshift/ -n aifeeders
kubectl rollout restart deployment/daily-news-api -n aifeeders
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) §10 for the complete EKS setup including External Secrets Operator and PVC configuration.

---

## 16. Glossary

| Term | Meaning |
|---|---|
| `article_id` | MD5 hash of (title + URL) — deterministic, reproducible across runs |
| `run_id` | `"RUN-{12 hex chars}"` — unique ID for one pipeline execution |
| `publication_key` | `"{article_id}:{date}:{body_hash[:12]}"` — idempotency key for LinkedIn |
| `PublishedStore` | File at `/tmp/aifeeders_published.json` — prevents republishing same article |
| Gate 1 | `PublishedStore.filter_unpublished()` in `deduplicate` — before LLM work |
| Gate 2 | `PublishedStore.is_published()` in `publish` — before LinkedIn call |
| Gate 3 | LinkedIn MCP `publication_key` — last-resort idempotency |
| `_linkedin_len()` | `sum(2 if ord(c) > 0xFFFF else 1 for c in text)` — counts UTF-16 units |
| `POST_LIMIT` | 2900 LinkedIn UTF-16 units (100-unit margin below LinkedIn's 3000 hard limit) |
| Jev Decision #1 | `jev_prefilter_articles` — scores all articles, picks best 1 by composite score |
| Jev Decision #2 | `jev_route_personas` — decides which personas to run (saves LLM cost) |
| Jev Decision #3 | `EvaluationAgent` — checks factuality, hallucination, PII, injection |
| `noul` | Jev question type: returns float 0–1 probability; >0.5 = "yes" |
| `choice` | Jev question type: picks one named label |
| `score` | Jev question type: rates on a descriptive scale (0..N-1) |
| composite score | `relevance_score × 0.6 + estimated_engagement × 0.4` — Jev article ranking |
| `REGENERATE` | Eval decision: quality miss; retry from `summarize` (max 2 retries) |
| `BLOCK` | Eval decision: PII, injection, or policy=FAIL — article not published |
| S2I | Source-to-Image — OpenShift's in-cluster build; no local Docker daemon needed |
| `successfulBuildsHistoryLimit: 1` | Auto-deletes the previous build when a new one succeeds |
| `default-deny-all` | NetworkPolicy that blocks all pod traffic unless explicitly allowed |
| `allow-api-to-mcps` | NetworkPolicy that allows `daily-news-api`/`daily-news-worker` → MCP services |
| `daily-news-config` | ConfigMap — non-sensitive env vars (URLs, thresholds, feature flags) |
| `daily-news-secrets` | Kubernetes Secret — API keys (never commit real values) |
| HPA | Horizontal Pod Autoscaler — scales `daily-news-api` from 2 to 4 pods on CPU load |
| PDB | PodDisruptionBudget — ensures at least 1 `daily-news-api` pod stays running during maintenance |
| ECR | Amazon Elastic Container Registry — image registry for EKS |
| ESO | External Secrets Operator — syncs AWS Secrets Manager → Kubernetes Secrets on EKS |
| IRSA | IAM Roles for Service Accounts — AWS way to give EKS pods AWS permissions without static keys |
