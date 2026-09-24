# AIFeeders — Operations Runbook

> Build #62 · OpenShift `aifeeders` · Last updated: 2026-09  
> For architecture details: [`ARCHITECTURE.md`](ARCHITECTURE.md)  
> For project overview: [`README.md`](README.md)

---

## Table of Contents

1. [What AIFeeders Does](#1-what-aifeeders-does)
2. [Normal Day](#2-normal-day)
3. [First-Time Setup on a New Cluster](#3-first-time-setup-on-a-new-cluster)
4. [Build Flow → Launch Flow](#4-build-flow--launch-flow)
5. [Triggering a Run Manually](#5-triggering-a-run-manually)
6. [How to Read the Logs](#6-how-to-read-the-logs)
7. [Checking Service Health](#7-checking-service-health)
8. [API Quota and Key Rotation](#8-api-quota-and-key-rotation)
9. [Cleaning the Namespace](#9-cleaning-the-namespace)
10. [Deploying to EKS](#10-deploying-to-eks)
11. [What Can Go Wrong](#11-what-can-go-wrong)
12. [Glossary](#12-glossary)

---

## 1. What AIFeeders Does

Every day at 10:00 UTC, AIFeeders:

1. Queries GNews for AI news (9 queries × 24-hour window = ~27 articles)
2. Deduplicates: MD5 hash within-run + PublishedStore cross-run (7-day TTL)
3. Fetches full article content
4. Indexes articles in PageIndex (in-memory RAG)
5. **Jev Decision #1** — scores all articles, picks top 2 by relevance + engagement
6. Summarises each selected article with an LLM
7. **Jev Decision #2** — routes only relevant personas (saves LLM calls)
8. Generates 4-persona perspectives (only active personas, in parallel)
9. **Jev Decision #3** — evaluates content quality (factuality, hallucination, PII, injection)
10. Publishes LinkedIn post + persona comments (3-gate dedup: PublishedStore × 2 + LinkedIn MCP key)

Expected result: **2 LinkedIn posts per day** (~10:30–10:45 UTC depending on Jev + LLM latency).

---

## 2. Normal Day

**No action needed.** The CronJob runs automatically. To verify:

```bash
# 1. Check today's run completed
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -3

# 2. Check the logs
oc logs job/<latest-job-name> -n aifeeders | grep -E "status=|published|ERROR"
# Expected: "Workflow complete — status=PUBLISHED published=2 errors=0"

# 3. Check the published store (confirms what was published)
oc exec deployment/daily-news-api -n aifeeders -- cat /tmp/aifeeders_published.json
```

If `published=0` and `errors=0`: check `PUBLISHING_ENABLED` in the ConfigMap.  
If `published=0` and `errors>0`: check the full logs for the error.

---

## 3. First-Time Setup on a New Cluster

### Tools required

```bash
python3 --version   # 3.11+
oc version          # OpenShift CLI 4.x
jq --version        # JSON parsing in health checks
```

### Step 1 — Log in

```bash
oc login https://api.<cluster>:6443 -u <user> -p <pass>
# OR using a token:
oc login --token=<token> --server=https://api.<cluster>:6443

oc new-project aifeeders     # creates namespace; skip if it already exists
oc project aifeeders         # set as current project
```

### Step 2 — Apply ConfigMap

```bash
oc apply -f openshift/configmap.yaml -n aifeeders

# Verify — check key values
oc get configmap daily-news-config -n aifeeders -o yaml | grep -A5 data:
```

The ConfigMap contains non-sensitive settings:
- `PUBLISHING_ENABLED: "true"` — set `"false"` for smoke tests
- `JEV_ENABLED: "true"` — set `"false"` to fall back to heuristics
- `LLM_BASE_URL`, `JEV_BASE_URL` — service endpoint URLs
- `EVAL_FACTUALITY_THRESHOLD: "0.50"`, `EVAL_GROUNDEDNESS_THRESHOLD: "0.50"`, `EVAL_HALLUCINATION_THRESHOLD: "0.85"`

### Step 3 — Apply Secrets

```bash
# NEVER commit real secret values to git.
# The secrets.yaml in the repo is a template with placeholder values.
# You must create a local copy with real values before applying.
cp openshift/secrets.yaml openshift/secrets.yaml.real
# Edit secrets.yaml.real — replace placeholder base64 values with real ones:
#   echo -n "your-real-value" | base64
# Then apply:
oc apply -f openshift/secrets.yaml.real -n aifeeders

# Verify secrets exist (values are hidden):
oc get secret daily-news-secrets -n aifeeders
```

Required secret keys:
- `LLM_API_KEY` — LLM bearer token
- `GNEWS_API_KEY` — GNews primary key
- `GNEWS_API_KEY_2` — GNews secondary key (auto-rotation on quota exhaustion)
- `JEV_API_KEY` — Jev gateway bearer token
- `LINKEDIN_CLIENT_ID` — LinkedIn app client ID
- `LINKEDIN_CLIENT_SECRET` — LinkedIn app secret

### Step 4 — RBAC

```bash
oc apply -f openshift/rbac.yaml -n aifeeders

# Verify the service account exists:
oc get serviceaccount aifeeders-sa -n aifeeders
```

### Step 5 — Create BuildConfigs (first time only)

```bash
oc apply -f openshift/buildconfigs/ -n aifeeders

# Set build history limits — old builds are deleted automatically
# This is the key setting that keeps the namespace clean
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done

# Verify BuildConfigs created:
oc get buildconfigs -n aifeeders
```

### Step 6 — Build all images

```bash
# Create a clean tmpdir with .venv/ excluded (it's 270 MB — causes upload timeout)
TMPDIR=$(mktemp -d)
rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  --exclude='htmlcov/' --exclude='.coverage' --exclude='.tox/' --exclude='.DS_Store' \
  . "$TMPDIR/"

echo "Upload size: $(du -sh $TMPDIR | cut -f1)"  # should be < 5 MB

# Build each service (sequential — one at a time)
for svc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  echo "=== Building $svc ==="
  oc start-build $svc --from-dir="$TMPDIR" -n aifeeders --follow
  echo "=== $svc done ==="
done
```

Each build takes ~2 minutes. `--follow` streams the build log and exits when done.

### Step 7 — Deploy all services

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
```

### Step 8 — Check all pods are running

```bash
oc get pods -n aifeeders
# Expected (all Running, none CrashLoopBackOff):
# daily-news-api-xxx-yyy         1/1  Running   0  2m
# daily-news-api-xxx-zzz         1/1  Running   0  2m
# news-mcp-xxx-yyy               1/1  Running   0  2m
# news-mcp-xxx-zzz               1/1  Running   0  2m
# evaluation-mcp-xxx-yyy         1/1  Running   0  2m
# evaluation-mcp-xxx-zzz         1/1  Running   0  2m
# linkedin-mcp-xxx-yyy           1/1  Running   0  2m
# pageindex-mcp-xxx-yyy          1/1  Running   0  2m

# Check all service /health endpoints
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health | jq -r .status
done
# Expected: "healthy" or "ready" for each
```

### Step 9 — Authorise LinkedIn (first time)

LinkedIn requires a browser-based OAuth flow. The token is stored in `linkedin-mcp` memory.

```bash
# Open a port-forward tunnel
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
PF_PID=$!

# Open in browser: http://localhost:8080/auth/linkedin
# Complete the OAuth flow — grant w_member_social + r_liteprofile permissions
# You'll see a success page or be redirected back

# Verify the token was stored
curl -s http://localhost:8080/health | jq .
# Should show {"status": "ready", "token_status": "valid"} or similar

# Stop the port-forward
kill $PF_PID
```

> **Important:** The token lives in `linkedin-mcp` pod memory. It is lost if the pod restarts. You must re-authorise after any pod restart. LinkedIn tokens expire after **60 days** — set a calendar reminder.

### Step 10 — Smoke test (no publishing)

```bash
# Disable publishing
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Trigger a run
SMOKE_JOB=$(oc create job smoke-$(date +%s) --from=cronjob/daily-ai-news \
  -n aifeeders --output=name | sed 's|job.batch/||')
echo "Smoke job: $SMOKE_JOB"

# Wait and tail the logs
sleep 5
oc logs -f job/$SMOKE_JOB -n aifeeders | tail -20

# Expected last line:
# "Workflow complete — status=EVALUATED published=0 errors=0"

# Re-enable publishing
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### Step 11 — First live run

```bash
LIVE_JOB=$(oc create job live-$(date +%s) --from=cronjob/daily-ai-news \
  -n aifeeders --output=name | sed 's|job.batch/||')
echo "Live job: $LIVE_JOB"

# Wait for it to start
sleep 10

# Follow the logs
oc logs -f job/$LIVE_JOB -n aifeeders | grep -E "status=|published|article_id|post_urn|ERROR"

# Expected success output:
# [RUN-xxx] post composed article=news-abc123 python_len=2847 linkedin_utf16_len=2873
# [RUN-xxx] post published post_urn=urn:li:share:750847xxx status=published
# Workflow complete — status=PUBLISHED published=2 errors=0
```

---

## 4. Build Flow → Launch Flow

### Pre-flight

Every build starts with tests. **Never build a codebase that fails tests.**

```bash
source .venv/bin/activate
python -m pytest tests/unit tests/workflow -q --tb=short
# Expected: 52 passed (no failures, no errors)
```

### Build

```bash
# Create a clean rsync tmpdir (excludes .venv/ and other junk)
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  --exclude='htmlcov/' --exclude='.coverage' --exclude='.tox/' --exclude='.DS_Store' \
  . "$TMPDIR/"

# Start the build — prints the build name (e.g. build/daily-news-62)
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --output=name
```

Save the build name for the next step.

If you also changed an MCP server (e.g. `linkedin-mcp`), run its build too:
```bash
oc start-build linkedin-mcp --from-dir="$TMPDIR" -n aifeeders --output=name
```

### Monitor build

```bash
# Replace 62 with the actual build number from the previous step
oc logs -f build/daily-news-62 -n aifeeders

# Milestones to watch for:
# "STEP 4/6: RUN pip install -e ."      ← pip install running
# "STEP 5/6: COPY . /app/src"           ← copying source
# "STEP 6/6: CMD ..."                   ← final layer
# "Push successful"                     ← image pushed to registry ✅

# If the build fails:
oc describe build/daily-news-62 -n aifeeders | grep -A5 "Status:"
```

**What happens to the old build:**  
`successfulBuildsHistoryLimit: 1` — OpenShift automatically deletes `daily-news-61` when `daily-news-62` succeeds. No manual cleanup needed.

### Roll out

```bash
# Restart the deployment — pods pull the new :latest image
oc rollout restart deployment/daily-news-api -n aifeeders

# Wait for completion (zero-downtime: new pod passes probes before old pod stops)
oc rollout status deployment/daily-news-api -n aifeeders --timeout=90s
# Expected: "deployment 'daily-news-api' successfully rolled out"

# Verify the new pods are running
oc get pods -n aifeeders -l app=daily-news-api
```

If you rebuilt `linkedin-mcp`, roll it out too and **re-authorise LinkedIn** (token is in-memory):
```bash
oc rollout restart deployment/linkedin-mcp -n aifeeders
oc rollout status deployment/linkedin-mcp -n aifeeders --timeout=60s
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# Browser: http://localhost:8080/auth/linkedin
```

### Launch (live run)

```bash
LIVE_JOB=$(oc create job live-$(date +%s) --from=cronjob/daily-ai-news \
  -n aifeeders --output=name | sed 's|job.batch/||')
echo "Job: $LIVE_JOB"
```

### Verify result

```bash
# Summary line
oc logs job/$LIVE_JOB -n aifeeders | grep "Workflow complete"
# Expected: "Workflow complete — status=PUBLISHED published=2 errors=0"

# Jev scores (what was selected and why)
oc logs job/$LIVE_JOB -n aifeeders | grep "jev_prefilter:"

# Published store (confirms articles recorded)
oc exec deployment/daily-news-api -n aifeeders -- cat /tmp/aifeeders_published.json | jq .

# LinkedIn audit (post URNs)
oc exec deployment/linkedin-mcp -n aifeeders -- curl -s http://localhost:8000/audit | jq .

# Full log (for troubleshooting)
oc logs job/$LIVE_JOB -n aifeeders
```

### Self-pruning builds — how upgrading works

```
Before upgrade:
  Builds:  daily-news-61 (complete, image in registry)
  Pods:    daily-news-api-old-1, daily-news-api-old-2 (running :latest = #61)

oc start-build daily-news-62:
  BuildPod daily-news-62 created
  pip install runs in BuildPod
  New image pushed to registry as :latest (replaces #61's :latest tag)
  BuildPod daily-news-62 terminates (self-cleaning)
  Build record daily-news-61 deleted (successfulBuildsHistoryLimit: 1)
  Builds now: daily-news-62 only

oc rollout restart:
  New pods scheduled: daily-news-api-new-1 (pulls :latest = #62 image)
  new-1 starts, passes liveness probe, becomes Ready
  Old pod old-1 terminated (RollingUpdate maxUnavailable: 1)
  New pod new-2 scheduled and starts
  Old pod old-2 terminated
  Deployment complete: 2 pods running #62

Result:
  - Zero downtime (RollingUpdate with at least 1 pod always Ready)
  - No manual image cleanup ever needed
  - oc get builds -n aifeeders shows exactly 1 build record
  - oc get pods -n aifeeders shows only the new pods
```

---

## 5. Triggering a Run Manually

### Via CLI

```bash
# Trigger from the CronJob template (most common)
oc create job manual-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# With a custom run label for easy filtering
oc create job test-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders \
  --output=name
```

### Via the REST API

```bash
# Port-forward the API
oc port-forward svc/daily-news-api 9090:8000 -n aifeeders &

# Trigger a run
curl -X POST http://localhost:9090/run \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}' | jq .

# Check run status (using the run_id from the response)
curl http://localhost:9090/status/<run_id> | jq .

# Health check
curl http://localhost:9090/health | jq .
```

### Dry run (no LinkedIn post)

```bash
# Method 1: patch ConfigMap (affects all future runs until reverted)
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'
oc create job dry-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders
# Remember to re-enable after the test

# Method 2: API dry_run flag (if implemented)
curl -X POST http://localhost:9090/run -d '{"dry_run": true}'

# Method 3: local dry run (no cluster needed)
PUBLISHING_ENABLED=false python -m daily_news.workflow_runner
```

---

## 6. How to Read the Logs

### A healthy run

```
[RUN-A3F91C2B4E6D] discover_news started
[RUN-A3F91C2B4E6D] discovered 24 raw articles
[RUN-A3F91C2B4E6D] deduplicated: 24 raw → 21 unique → 19 unpublished-today
[RUN-A3F91C2B4E6D] jev_prefilter: #1 article_id=news-abc123 relevance=0.92 engagement=0.78 composite=0.864 personas=['policy','business','genz']
[RUN-A3F91C2B4E6D] jev_prefilter: #2 article_id=news-def456 relevance=0.84 engagement=0.71 composite=0.788 personas=['business','linkedin']
[RUN-A3F91C2B4E6D] summarised 2 articles
[RUN-A3F91C2B4E6D] jev_route_personas: article=news-abc123 active_personas=['policy','business','genz']
[RUN-A3F91C2B4E6D] generate_personas: running ['policy', 'business', 'genz']
[RUN-A3F91C2B4E6D] eval article=news-abc123 decision=PASS factuality=0.90 groundedness=0.88 hallucination=0.05
[RUN-A3F91C2B4E6D] post composed article=news-abc123 python_len=2847 linkedin_utf16_len=2873
[RUN-A3F91C2B4E6D] post published post_urn=urn:li:share:7508479385549697024 status=published
[RUN-A3F91C2B4E6D] comment OK persona=policy urn=urn:li:comment:...
[RUN-A3F91C2B4E6D] comment OK persona=business urn=urn:li:comment:...
Workflow complete — status=PUBLISHED published=2 errors=0
```

### What each line means

| Log pattern | Meaning |
|---|---|
| `discovered N raw articles` | GNews returned N articles across all 9 queries |
| `deduplicated: X raw → Y unique → Z unpublished-today` | X total, Y after MD5 dedup, Z after PublishedStore filter |
| `jev_prefilter: #1 ... composite=0.864` | Jev scored all articles; this one ranked #1 |
| `jev_route_personas: ... active_personas=['policy','business']` | Only these 2 LLM persona calls will run |
| `eval decision=PASS factuality=0.90` | Jev evaluation passed; quality looks good |
| `post composed ... linkedin_utf16_len=2873` | Post is 2873 LinkedIn units (safely under 2900 limit) |
| `post published post_urn=urn:li:share:...` | LinkedIn returned HTTP 201, post is live |
| `comment OK persona=policy` | Persona comment posted successfully |
| `Comments API not available (PERMISSION_ERROR)` | Comments API not approved — personas are in the post body; this is expected |
| `Workflow complete — status=PUBLISHED published=2 errors=0` | ✅ Normal successful run |

### Problem indicators

| Log pattern | Meaning | Action |
|---|---|---|
| `discovered 0 raw articles` | GNews quota exhausted | Wait for midnight UTC reset |
| `all articles already published today` | Re-run on same day | Expected — no action needed |
| `eval decision=REGENERATE` | LLM output quality too low | Usually transient — will retry automatically (max 2) |
| `eval decision=BLOCK` | PII or injection detected | Check source article; may need to skip it |
| `post FAILED http=401` | LinkedIn token expired | Re-authorise via port-forward |
| `post FAILED http=429` | LinkedIn rate limit hit | Wait ~10 minutes; retry |
| `jev_prefilter: ReadTimeout` | Jev gateway slow | Automatic fallback to `[:2]` selection |
| `publish failed for ... MCPError` | MCP server returned an error | Check linkedin-mcp logs for details |
| `Workflow complete ... errors=N` | Non-fatal errors during the run | Review the full log; articles may still have published |

---

## 7. Checking Service Health

```bash
# All pods at a glance
oc get pods -n aifeeders

# All services — running pod count
oc get deployments -n aifeeders

# Individual health check endpoints (all should return "healthy" or "ready")
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp daily-news-api; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health \
    | jq -r '.status // .health // "no status field"' 2>/dev/null || echo "exec failed"
done

# CronJob schedule
oc get cronjob daily-ai-news -n aifeeders -o yaml | grep schedule:
# Expected: "schedule: 0 10 * * *"  (10:00 UTC daily)

# Recent jobs (today's CronJob triggers)
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp
```

### Verify Jev is working (5 live checks)

```bash
# Jev health endpoint (no authentication required)
curl -s https://<your-jev-gateway>/health | jq .
# Expected: {"status": "ready", "model": "Qwen/Qwen3.5-2B", "method": "lora_decision_head"}

# From inside the cluster (test from a pod)
oc exec deployment/daily-news-api -n aifeeders -- \
  curl -s https://<your-jev-gateway>/health | jq .
```

If Jev is unreachable, the pipeline automatically falls back:
- `jev_prefilter` → takes first 2 articles (no Jev scoring)
- `jev_router` → runs all 4 personas
- `evaluate` → uses `EvaluationMCPClient` (LLM-based evaluation)

### Published store inspection

```bash
# What was published in the last 7 days
oc exec deployment/daily-news-api -n aifeeders -- \
  cat /tmp/aifeeders_published.json | python3 -m json.tool

# Example output:
# {
#   "news-abc123:2026-09-24": "2026-09-24T10:31:22+00:00",
#   "news-def456:2026-09-24": "2026-09-24T10:31:45+00:00",
#   "news-ghi789:2026-09-23": "2026-09-23T10:29:11+00:00"
# }

# LinkedIn audit (URNs published since pod start)
oc exec deployment/linkedin-mcp -n aifeeders -- \
  curl -s http://localhost:8000/audit | jq .
```

---

## 8. API Quota and Key Rotation

### GNews quota exhausted (HTTP 403 from news-mcp)

GNews free tier: 100 requests/day. 9 queries × 1 run/day = 9 requests — well within the limit.  
If you run multiple tests on the same day, you may exhaust the quota.

```bash
# Symptom: "discovered 0 raw articles" in logs
# Check news-mcp logs for the specific error:
oc logs deployment/news-mcp -n aifeeders | grep -E "403|quota|exceeded"

# Fix: wait until midnight UTC for quota reset
# OR: configure GNEWS_API_KEY_2 for auto-rotation

# To add a second key (auto-rotation on 403):
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"GNEWS_API_KEY_2\":\"$(echo -n 'your-key-2' | base64)\"}}"
# Restart news-mcp to pick up the new secret:
oc rollout restart deployment/news-mcp -n aifeeders
```

### LinkedIn token expired (60-day rotation)

```bash
# Symptom: "post FAILED http=401" in logs
# The token is stored in linkedin-mcp memory — it expires after 60 days

# Re-authorise:
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# Browser: http://localhost:8080/auth/linkedin
# Complete the OAuth flow

# Verify the token was accepted:
curl -s http://localhost:8080/health | jq .
```

Set a calendar reminder 55 days after each authorisation.

### Jev API key

```bash
# Symptom: "jev_prefilter failed" with 401 in logs
# Rotate the key:
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"JEV_API_KEY\":\"$(echo -n 'new-jev-key' | base64)\"}}"

# Restart the API to pick up the new secret:
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders
```

### LLM API key

```bash
# Symptom: "summarize failed" or "persona generation failed" with 401/403
oc patch secret daily-news-secrets -n aifeeders \
  --type=merge -p "{\"data\":{\"LLM_API_KEY\":\"$(echo -n 'new-llm-key' | base64)\"}}"
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders
```

### Disable Jev (emergency fallback)

If the Jev gateway is having an outage and the pipeline is failing:

```bash
# Disable Jev — pipeline falls back to heuristics
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"false"}}'

# Fallback behaviour:
# - jev_prefilter: takes first 2 articles (no scoring)
# - jev_router: runs all 4 personas
# - evaluate: uses evaluation-mcp (LLM-based)

# Restart daily-news-api to pick up the ConfigMap change
oc rollout restart deployment/daily-news-api -n aifeeders

# Re-enable when Jev is back:
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"true"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
```

---

## 9. Cleaning the Namespace

### Remove stale completed jobs

Completed and failed Jobs accumulate over time. Clean them periodically:

```bash
# Delete all completed (Succeeded) jobs
oc delete jobs -n aifeeders --field-selector status.successful=1

# Delete all failed jobs
oc delete jobs -n aifeeders --field-selector status.failed=1

# Or delete all jobs older than 7 days (requires GNU date):
oc get jobs -n aifeeders -o json | \
  jq -r '.items[] | select(.status.completionTime < (now - 604800 | todate)) | .metadata.name' | \
  xargs -I{} oc delete job {} -n aifeeders
```

### Remove old builds (should be automatic with limit=1)

```bash
# Check current builds — should be 1 per service
oc get builds -n aifeeders

# If old builds accumulated (successfulBuildsHistoryLimit was not set):
oc delete builds -n aifeeders -l buildconfig=daily-news \
  --field-selector status.phase=Complete

# Re-apply the limit to prevent recurrence:
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

### Remove stale imagestreams

```bash
# List current imagestream tags
oc get imagestreams -n aifeeders

# Prune unused image layers (removes unreferenced layers, not :latest)
oc adm prune images --confirm
```

### Full namespace audit

```bash
# Everything in the namespace
oc get all -n aifeeders

# Resources by type
echo "=== Pods ===" && oc get pods -n aifeeders
echo "=== Deployments ===" && oc get deployments -n aifeeders
echo "=== Services ===" && oc get services -n aifeeders
echo "=== Jobs ===" && oc get jobs -n aifeeders
echo "=== CronJobs ===" && oc get cronjobs -n aifeeders
echo "=== BuildConfigs ===" && oc get buildconfigs -n aifeeders
echo "=== Builds ===" && oc get builds -n aifeeders
echo "=== ConfigMaps ===" && oc get configmaps -n aifeeders
echo "=== Secrets ===" && oc get secrets -n aifeeders
```

### Reset the published store

Use when you want to re-publish articles that were already published today (e.g. after a bugfix deploy):

```bash
# Option A: Clear today's entries only (keeps historical data)
oc exec deployment/daily-news-api -n aifeeders -- python3 -c "
from daily_news.agents.published_store import published_store
published_store.clear_today()
print('Cleared today\\'s entries')
print('Remaining:', list(published_store._cache.keys()))
"

# Option B: Delete the file entirely (clears all 7 days)
oc exec deployment/daily-news-api -n aifeeders -- rm /tmp/aifeeders_published.json
echo "Published store deleted — will be recreated fresh on next run"

# Option C: View then selectively delete one article_id
oc exec deployment/daily-news-api -n aifeeders -- python3 -c "
import json
from pathlib import Path
data = json.loads(Path('/tmp/aifeeders_published.json').read_text())
for k, v in sorted(data.items()):
    print(k, '->', v)
"
```

### Pre-mark stale articles (force Jev to pick fresh ones)

If the pipeline keeps selecting the same articles because the fresh pool is small, you can pre-mark known stale IDs so the deduplication step skips them:

```bash
oc exec deployment/daily-news-api -n aifeeders -- python3 -c "
from daily_news.agents.published_store import published_store
# Pre-mark the stale article IDs (article_id is MD5 of title+url)
stale_ids = ['news-abc123', 'news-def456']
for aid in stale_ids:
    published_store.mark_published(aid)
    print(f'Pre-marked: {aid}')
"
```

---

## 10. Deploying to EKS

### What is the same on EKS

Everything except the image build and image registry URL is identical:
- All Kubernetes YAML manifests (Deployments, Services, CronJob, ConfigMap, HPA, PDB, NetworkPolicy, RBAC)
- The application code and configuration
- The PublishedStore, Jev integration, and deduplication logic
- The build → launch flow (test → build → push → rollout → run)

### What changes on EKS

| Aspect | OpenShift | EKS |
|---|---|---|
| Image build | `oc start-build` (S2I in-cluster) | `docker build` + `docker push` to ECR |
| Image registry URL | `image-registry.openshift-image-registry.svc:5000/aifeeders/<svc>:latest` | `<account>.dkr.ecr.<region>.amazonaws.com/aifeeders/<svc>:latest` |
| Secrets management | `oc create secret` | External Secrets Operator (ESO) + AWS Secrets Manager |
| LinkedIn OAuth route | OpenShift Route | ALB Ingress or `kubectl port-forward` |
| NetworkPolicy enforcement | Built-in | Requires CNI: Calico or Cilium (default VPC CNI doesn't enforce) |
| PublishedStore persistence | `/tmp` (ephemeral) | PVC on EFS (recommended for production) |

### EKS First-Time Setup

```bash
# ── Prerequisites ─────────────────────────────────────────────────────────────
export AWS_ACCOUNT=123456789012
export AWS_REGION=us-east-1
export ECR_REGISTRY=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
export EKS_CLUSTER=aifeeders-prod

# Configure kubectl
aws eks update-kubeconfig --region $AWS_REGION --name $EKS_CLUSTER

# Create namespace
kubectl apply -f openshift/namespace.yaml

# ── Create ECR repositories (once) ───────────────────────────────────────────
for svc in daily-news linkedin-mcp news-mcp evaluation-mcp pageindex-mcp; do
  aws ecr create-repository --repository-name aifeeders/$svc --region $AWS_REGION \
    --image-scanning-configuration scanOnPush=true
done

# ── Install External Secrets Operator ────────────────────────────────────────
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets-operator --create-namespace

# ── Create IAM Role for Service Account (IRSA) ───────────────────────────────
# See AWS docs for eksctl:
eksctl create iamserviceaccount \
  --name aifeeders-sa \
  --namespace aifeeders \
  --cluster $EKS_CLUSTER \
  --attach-policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite \
  --approve

# ── Store secrets in AWS Secrets Manager ─────────────────────────────────────
aws secretsmanager create-secret \
  --name aifeeders/prod \
  --secret-string '{
    "LLM_API_KEY": "...",
    "GNEWS_API_KEY": "...",
    "GNEWS_API_KEY_2": "...",
    "JEV_API_KEY": "...",
    "LINKEDIN_CLIENT_ID": "...",
    "LINKEDIN_CLIENT_SECRET": "...",
    "LANGFUSE_SECRET_KEY": "..."
  }'

# ── Apply ExternalSecret + SecretStore ───────────────────────────────────────
# (See ARCHITECTURE.md §10 for the full ExternalSecret YAML)
kubectl apply -f openshift/eks/external-secrets.yaml -n aifeeders

# ── Apply ConfigMap and RBAC ─────────────────────────────────────────────────
kubectl apply -f openshift/configmap.yaml -n aifeeders
kubectl apply -f openshift/rbac.yaml -n aifeeders
kubectl apply -f openshift/networkpolicy.yaml -n aifeeders

# ── Build and push images ─────────────────────────────────────────────────────
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' . "$TMPDIR/"

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

for svc in daily-news linkedin-mcp news-mcp evaluation-mcp pageindex-mcp; do
  docker build -t $ECR_REGISTRY/aifeeders/$svc:latest -f Dockerfile.$svc "$TMPDIR" 2>/dev/null \
    || docker build -t $ECR_REGISTRY/aifeeders/$svc:latest -f Dockerfile "$TMPDIR"
  docker push $ECR_REGISTRY/aifeeders/$svc:latest
  echo "Pushed $svc"
done

# ── Deploy services ───────────────────────────────────────────────────────────
# Update image: field in each deployment YAML from OpenShift registry to ECR
# (sed example for daily-news-api):
sed "s|image-registry.openshift-image-registry.svc:5000/aifeeders/daily-news:latest|$ECR_REGISTRY/aifeeders/daily-news:latest|g" \
  openshift/api/deployment.yaml | kubectl apply -f - -n aifeeders

# Repeat for each service, then:
kubectl apply -f openshift/cronjob.yaml -n aifeeders
kubectl apply -f openshift/hpa.yaml -n aifeeders
kubectl apply -f openshift/pdb.yaml -n aifeeders

# ── Authorise LinkedIn ────────────────────────────────────────────────────────
kubectl port-forward svc/linkedin-mcp 8080:8000 -n aifeeders &
# Browser: http://localhost:8080/auth/linkedin
```

### EKS Build Flow

```bash
# ── Per-change build cycle (same test → build → push → rollout → run pattern) ─

# 1. Test
python -m pytest tests/unit tests/workflow -q --tb=short

# 2. Build (rsync pattern — same as OpenShift, same reason)
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' . "$TMPDIR/"

docker build -t aifeeders/daily-news:latest -f Dockerfile "$TMPDIR"

# 3. Push to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY
docker tag aifeeders/daily-news:latest $ECR_REGISTRY/aifeeders/daily-news:latest
docker push $ECR_REGISTRY/aifeeders/daily-news:latest

# 4. Update + rollout
kubectl set image deployment/daily-news-api \
  daily-news=$ECR_REGISTRY/aifeeders/daily-news:latest -n aifeeders
kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders --timeout=90s
```

### EKS Launch Flow

```bash
# Trigger a run
kubectl create job live-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# Follow the logs
kubectl logs -f job/live-<id> -n aifeeders | grep -E "status=|published|ERROR"

# Verify
kubectl logs job/live-<id> -n aifeeders | grep "Workflow complete"
```

### EKS Differences to Address

| Item | Action |
|---|---|
| NetworkPolicy enforcement | Install Calico: `kubectl apply -f https://docs.projectcalico.org/manifests/calico.yaml` |
| PublishedStore persistence | Create EFS PVC and mount at `AIFEEDERS_STORE_PATH=/data/aifeeders_published.json` |
| LinkedIn token persistence | linkedin-mcp must be single replica; re-auth after every pod restart |
| Image build history | ECR: enable `imageTagMutability: MUTABLE` so `:latest` is always overwritten; old images auto-expire via ECR lifecycle policy |
| Build cleanup (ECR lifecycle) | Add lifecycle policy: `{"rules":[{"rulePriority":1,"selection":{"tagStatus":"untagged","countType":"imageCountMoreThan","countNumber":1},"action":{"type":"expire"}}]}` |

---

## 11. What Can Go Wrong

### No post today

```
Symptom: LinkedIn feed shows no new post
         Logs: "Workflow complete — status=EVALUATED published=0 errors=0"
```

**Check in order:**

1. `PUBLISHING_ENABLED=false` in ConfigMap → set to `true`
2. All articles already published today → check `published_store` and wait until tomorrow
3. Eval threshold too strict → check `EVAL_FACTUALITY_THRESHOLD`, try lowering to `0.40`
4. GNews quota → check `news-mcp` logs for 403
5. LinkedIn token expired → re-authorise

### Personas not generating

```
Symptom: "Workflow complete — status=PUBLISHED published=2"
         Post shows only the main article, no Perspectives section
         Logs: "persona generation failed for ..."
```

**Check:**
```bash
oc logs deployment/daily-news-api -n aifeeders | grep -E "persona|LLM|OutputParser"
```

- `OutputParserException` → LLM returned malformed JSON; usually transient; will fix on retry
- `401` → LLM API key expired; rotate `LLM_API_KEY`
- `jev_active_personas=[]` → Jev returned no active personas; check Jev logs

### Jev gateway timeouts

```
Symptom: "jev_prefilter failed (ReadTimeout) — falling back to [:2]"
         Logs show articles selected without scoring
```

**Check:**
```bash
# Jev health
curl -s https://<your-jev-gateway>/health
# Should be {"status": "ready", ...}

# From inside cluster
oc exec deployment/daily-news-api -n aifeeders -- \
  curl -s -m 5 https://<your-jev-gateway>/health
```

If Jev is down:
- The pipeline **still runs** (graceful fallback to `[:2]` + all personas + LLM eval)
- Disable Jev temporarily: `JEV_ENABLED=false` in ConfigMap
- Post will publish without Jev scores in the body

### Build upload timeout

```
Symptom: "oc start-build" hangs for > 5 minutes then times out
         Error: "error: build/daily-news-XX failed — error streaming build..."
```

**Root cause:** `.venv/` directory (~270 MB) included in the build context.

**Fix:**
```bash
# Verify the rsync exclude is working BEFORE starting the build:
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' \
  --exclude='.git/' --exclude='.pytest_cache/' \
  . "$TMPDIR/"
echo "Upload size: $(du -sh $TMPDIR | cut -f1)"
# Must show < 5 MB — if you see > 100 MB, .venv/ is being included

# Then start the build from the tmpdir:
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
```

### Eval thresholds too strict

```
Symptom: "eval decision=REGENERATE" every run
         "max retries (2) exceeded — publishing PASS items only"
         Some days: published=0 (no articles pass)
```

**Fix (loosen thresholds temporarily):**
```bash
oc patch configmap daily-news-config -n aifeeders --type=merge -p '{
  "data": {
    "EVAL_FACTUALITY_THRESHOLD": "0.40",
    "EVAL_GROUNDEDNESS_THRESHOLD": "0.40",
    "EVAL_HALLUCINATION_THRESHOLD": "0.90"
  }
}'
```

### Post truncated mid-sentence (pre-build #61)

```
Symptom: LinkedIn post cuts off at "🏛️  Primary audience : Policy"
         Post appears complete in logs but is truncated on LinkedIn
```

This was caused by LinkedIn counting UTF-16 units while Python counted code points.

**Status:** Fixed in build #61 via `_linkedin_len()`. If you see this on build #62+:
```bash
# Check if the truncation is consistent at the same position
oc logs job/<latest-job> -n aifeeders | grep "linkedin_utf16_len"
# If linkedin_utf16_len > 2900, there's a budget calculation regression
```

### Article #2 shows wrong Jev scores in post (pre-build #61)

```
Symptom: Second LinkedIn post of the day shows "💼 Business 93%" in Jev block
         But the article is clearly about regulation, not business
```

**Status:** Fixed in build #61. `jev_prefilter_scores` is now a `dict[article_id → scores]`.  
If you still see this, check that the build #61+ image is actually running:

```bash
oc describe pod $(oc get pods -n aifeeders -l app=daily-news-api -o name | head -1) \
  | grep "Image:"
```

---

## 12. Glossary

| Term | Meaning |
|---|---|
| `article_id` | MD5 hash of `(title + url)` — deterministic, reproducible across runs |
| `publication_key` | `"{article_id}:{date}:{body_hash[:12]}"` — Gate 3 idempotency key for LinkedIn MCP |
| `run_id` | `"RUN-{12 hex chars}"` — unique identifier for a single pipeline execution |
| `jev_prefilter_scores` | `dict[article_id → scores]` — per-article Jev scores from Decision #1; keyed by article_id so article #2 always gets its own scores |
| `jev_persona_hints` | Persona fit suggestions from Decision #1 (raw article text analysis); merged with Decision #2 output |
| `jev_active_personas` | Final list of persona types to run LLM calls for; union of Decision #1 hints and Decision #2 answer |
| `PublishedStore` | File-backed `dict["{article_id}:{date}" → ISO-timestamp]` with 7-day TTL and threading.Lock |
| `Gate 1` | `PublishedStore.filter_unpublished()` in `deduplicate` node — before LLM work |
| `Gate 2` | `PublishedStore.is_published()` in `publish` node — before LinkedIn call |
| `Gate 3` | LinkedIn MCP `publication_key` — last-resort idempotency in MCP server |
| `_linkedin_len()` | `sum(2 if ord(c) > 0xFFFF else 1 for c in text)` — counts UTF-16 units (not Python code points) |
| `POST_LIMIT` | 2900 LinkedIn UTF-16 units (100-unit safety margin below LinkedIn's 3000 hard limit) |
| `BODY_LIMIT` | `POST_LIMIT - FOOTER_TEXT_COST` — budget for body sections; footer is always appended unconditionally |
| `noul` | Jev question type: numeric probability (float 0–1); >0.5 = "yes" |
| `choice` | Jev question type: pick one named label from a dict |
| `score` | Jev question type: rate on a descriptive scale (0..N-1 float) |
| `composite score` | Jev article selection score: `relevance_score × 0.6 + estimated_engagement × 0.4` |
| `S2I` | Source-to-Image — OpenShift's in-cluster build mechanism; no local Docker daemon needed |
| `successfulBuildsHistoryLimit: 1` | BuildConfig setting that auto-deletes the previous build after each new one succeeds |
| `lora_decision_head` | Jev System One's inference method (LoRA fine-tuned decision head on Qwen/Qwen3.5-2B) |
| `EvaluationMCPClient` | LLM-based evaluation fallback used when `JEV_ENABLED=false` |
| `REGENERATE` | Evaluation decision: content quality below threshold; retry from `summarize` (max 2 retries) |
| `BLOCK` | Evaluation decision: PII, prompt injection, or policy=FAIL detected; article not published |
| `HUMAN_REVIEW` | Evaluation decision: borderline content; currently treated as publish-with-flag |
| `daily-news-config` | ConfigMap with non-sensitive env vars (URLs, thresholds, feature flags) |
| `daily-news-secrets` | Kubernetes Secret with API keys (never commit real values) |
| `ECR` | Amazon Elastic Container Registry — container image registry for EKS deployments |
| `ESO` | External Secrets Operator — syncs AWS Secrets Manager → Kubernetes Secrets on EKS |
| `IRSA` | IAM Roles for Service Accounts — AWS-native way to give EKS pods AWS permissions without static credentials |
