# AIFeeders — Operations Runbook

> **Current as of build #56.** Four personas. Jev System One AI decision engine. Three deduplication gates. Self-pruning builds (limit=1). EKS-portable. GNews daily quota resets at midnight UTC.

---

## Table of Contents

1. [What AIFeeders Does](#1-what-aifeeders-does)
2. [Normal Day — What You Actually Do](#2-normal-day)
3. [First-Time Setup on a New Cluster](#3-first-time-setup)
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

A CronJob runs every day at 10:00 UTC. It:

1. Searches GNews for ~18 AI articles across 6 query categories
2. Deduplicates articles (SHA-256 hash + PublishedStore cross-run filter)
3. Sends all articles to **Jev System One** for scoring — picks the top 2 by composite score (relevance × 0.6 + engagement × 0.4)
4. Summarises the top 2 articles with an LLM (reads article + RAG context from PageIndex)
5. Asks Jev which of the 4 personas are relevant to each summary
6. Generates parallel LLM perspectives for active personas only (Capitalist · Government · Generalist · Tech & Workforce)
7. Evaluates the generated content via Jev (factuality / groundedness / hallucination / PII / policy check)
8. Publishes one structured LinkedIn post per article if evaluation passes all gates

**Output**: 1–2 LinkedIn posts per day, fully automated. No human in the loop unless evaluation forces `HUMAN_REVIEW`.

---

## 2. Normal Day

```
10:00 UTC    CronJob fires automatically
10:05 UTC    2 posts published to LinkedIn (check your feed)
             Nothing else to do.
```

Check if the post went out:

```bash
# Quick status from logs
oc logs -l app=daily-news-worker -n aifeeders --since=2h | grep "status="
# Expected: status=PUBLISHED published=2 errors=0

# Or from the API deployment
oc logs deployment/daily-news-api -n aifeeders --since=2h | tail -5
```

If `published=0` and no error — GNews quota is likely exhausted (100 req/day free tier). Resets at midnight UTC.

---

## 3. First-Time Setup on a New Cluster

### Tools required

```bash
oc version          # OpenShift CLI 4.x
python3 --version   # 3.11+
```

### Step 1 — Log in

```bash
oc login https://api.<cluster>:6443 -u <user> -p <password>
oc project aifeeders
# If namespace doesn't exist:
oc new-project aifeeders
```

### Step 2 — Apply ConfigMap

Edit `openshift/configmap.yaml` with your values, then:

```bash
oc apply -f openshift/configmap.yaml -n aifeeders
```

Key values to set:

| Key | Description | Example |
|---|---|---|
| `LLM_BASE_URL` | OpenAI-compatible LLM endpoint | `https://your-llm-gateway/v1` |
| `JEV_BASE_URL` | Jev gateway URL | `https://open-jev-gateway-jev-model.apps.xxx.ibm.com` |
| `NEWS_MCP_URL` | Internal service URL | `http://news-mcp:8000` |
| `EVALUATION_MCP_URL` | Internal service URL | `http://evaluation-mcp:8000` |
| `LINKEDIN_MCP_URL` | Internal service URL | `http://linkedin-mcp:8000` |
| `PAGEINDEX_MCP_URL` | Internal service URL | `http://pageindex-mcp:8000` |
| `PUBLISHING_ENABLED` | `true` to publish, `false` for dry run | `true` |
| `JEV_ENABLED` | `true` to use Jev, `false` to fall back to heuristics | `true` |
| `EVAL_FACTUALITY_THRESHOLD` | Minimum factuality score to pass | `0.50` |
| `EVAL_GROUNDEDNESS_THRESHOLD` | Minimum groundedness score to pass | `0.50` |
| `EVAL_HALLUCINATION_THRESHOLD` | Maximum hallucination score to pass | `0.85` |

### Step 3 — Apply Secrets

```bash
# Copy the example and fill in real values
cp openshift/secrets.yaml.example openshift/secrets.yaml
# Edit: GNEWS_API_KEY, LLM_API_KEY, JEV_API_KEY, LANGFUSE_SECRET_KEY, etc.

oc apply -f openshift/secrets.yaml -n aifeeders
```

**Never commit `secrets.yaml` to git.** It is in `.gitignore`.

### Step 4 — RBAC

```bash
oc apply -f openshift/rbac.yaml -n aifeeders
```

### Step 5 — Create BuildConfigs (first time only)

```bash
oc apply -f openshift/buildconfigs/ -n aifeeders

# Patch all to keep only 1 build in history — old builds are deleted automatically
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge \
    -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

### Step 6 — Build all images

See [Section 4](#4-build-flow--launch-flow) for the canonical build command. Build all five services:

```bash
for svc in daily-news news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  TMPDIR=$(mktemp -d) && rsync -a \
    --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
    --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
    --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
    --exclude='htmlcov/' --exclude='.coverage' --exclude='.tox/' --exclude='.DS_Store' \
    . "$TMPDIR/" && \
  oc start-build $svc --from-dir="$TMPDIR" -n aifeeders --follow
  echo "$svc build complete"
done
```

### Step 7 — Deploy all services

```bash
oc apply -f openshift/deployments/ -n aifeeders
oc apply -f openshift/services/ -n aifeeders
oc apply -f openshift/cronjob.yaml -n aifeeders
```

### Step 8 — Check all pods are running

```bash
oc get pods -n aifeeders
```

Expected:

```
daily-news-api-xxxx     1/1     Running
evaluation-mcp-xxxx     1/1     Running
linkedin-mcp-xxxx       1/1     Running
news-mcp-xxxx           1/1     Running
pageindex-mcp-xxxx      1/1     Running
```

### Step 9 — Authorise LinkedIn (first time)

```bash
# Open a port-forward to the linkedin-mcp service
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders

# In your browser: http://localhost:8080/auth/linkedin
# Complete the OAuth flow, grant: w_member_social, r_liteprofile
# The token is stored in the linkedin-mcp pod memory
```

### Step 10 — Smoke test (no publishing)

```bash
# Temporarily set PUBLISHING_ENABLED=false in ConfigMap
oc patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Trigger a manual run
oc create job smoke-test-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# Watch logs
oc logs -f job/smoke-test-<id> -n aifeeders | grep -E "status=|prefilter|persona|eval"

# Expected last line:
# Workflow complete — status=EVALUATED published=0 errors=0

# Re-enable publishing
oc patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### Step 11 — First live run

```bash
oc create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders --output=name
```

---

## 4. Build Flow → Launch Flow

This is the **complete sequence** every time you change code and need to deploy. **Never skip Step 1.**

### Pre-flight

```bash
# Run all tests locally first — never build a failing codebase
source .venv/bin/activate
python -m pytest tests/unit tests/workflow -q --tb=short
# Expected: all tests pass
```

### Build

```bash
# 1. Create a clean temp directory
#    NEVER build from repo root — .venv is 269 MB and causes build upload timeout
TMPDIR=$(mktemp -d)

# 2. Rsync only the required files (< 1 MB upload)
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
  --exclude='htmlcov/' \
  --exclude='.coverage' \
  --exclude='.tox/' \
  --exclude='.DS_Store' \
  . "$TMPDIR/"

# 3. Start build (returns build name immediately)
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --output=name
# Prints: build.build.openshift.io/daily-news-57
```

### Monitor build

```bash
# Follow logs until push succeeds
oc logs -f build/daily-news-57 -n aifeeders | grep -E "STEP|Push successful|error"
```

A successful build ends with:
```
Push successful
```

> **Why the build is fast (<60s):** pip dependencies are layer-cached by the BuildPod. Only `src/` and `prompts/` are re-copied. The cache is warm on repeated builds.

### Roll out

```bash
# Restart pods to pull the new :latest image
oc rollout restart deployment/daily-news-api -n aifeeders

# Wait for completion (typically <30s)
oc rollout status deployment/daily-news-api -n aifeeders --timeout=60s
```

### Launch (live run)

```bash
# Optional: clear today's published store if you want to re-run with same articles
POD=$(oc get pods -n aifeeders -l app=daily-news-api --no-headers | head -1 | awk '{print $1}')
oc exec $POD -n aifeeders -- rm -f /tmp/aifeeders_published.json

# Trigger the run
oc create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders --output=name
```

### Verify result

```bash
oc wait --for=condition=complete job/live-run-<id> -n aifeeders --timeout=180s
oc logs job/live-run-<id> -n aifeeders | grep -E "status=|published|ERROR"
# Expected: Workflow complete — status=PUBLISHED published=2 errors=0
```

### Self-pruning builds

All BuildConfigs have `successfulBuildsHistoryLimit: 1`. After each new build completes, OpenShift **automatically deletes** the previous build object and pod. The namespace stays clean with exactly 1 build at all times — no manual cleanup needed.

```
Build #55 complete → Build #56 starts → OpenShift auto-deletes Build #55
Build #56 complete → Build #57 starts → OpenShift auto-deletes Build #56
```

There is never more than 1 successful build per service in the namespace.

---

## 5. Triggering a Run Manually

### Via CLI

```bash
oc create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders
```

### Via the REST API

```bash
curl -s -X POST http://$(oc get svc daily-news-api -n aifeeders -o jsonpath='{.spec.clusterIP}'):8000/workflow/daily-news \
  -H "Content-Type: application/json" | jq .
```

### Dry run (no LinkedIn post)

```bash
# Temporarily disable publishing
oc patch configmap aifeeders-config -n aifeeders --type=merge \
  -p '{"data":{"PUBLISHING_ENABLED":"false"}}'

oc create job dryrun-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# Watch it complete
oc logs -f job/dryrun-<id> -n aifeeders | grep -E "status=|prefilter|eval"

# Re-enable when done
oc patch configmap aifeeders-config -n aifeeders --type=merge \
  -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

---

## 6. How to Read the Logs

### A healthy run

```
INFO  [RUN-8D26D6C665BA] discover_news started
INFO  [RUN-8D26D6C665BA] discovered 18 raw articles
INFO  [RUN-8D26D6C665BA] deduplicated: 18 raw → 18 unique → 18 unpublished-today
INFO  [RUN-8D26D6C665BA] jev_prefilter: #1 article_id=news-abc relevance=0.87 engagement=0.62 composite=0.77 personas=['business','policy','genz','linkedin']
INFO  [RUN-8D26D6C665BA] jev_prefilter: #2 article_id=news-xyz relevance=0.85 engagement=0.60 composite=0.75 personas=['policy','genz','linkedin']
INFO  [RUN-8D26D6C665BA] summarised 2 articles
INFO  [RUN-8D26D6C665BA] jev_route_personas: article=news-abc active_personas=['business','genz']
INFO  [RUN-8D26D6C665BA] jev_route_personas: merged with prefilter hints → ['linkedin','policy','business','genz']
INFO  [RUN-8D26D6C665BA] generate_personas: running ['business','policy','genz','linkedin']
INFO  [RUN-8D26D6C665BA] eval article=news-abc decision=PASS factuality=0.70 groundedness=0.54 hallucination=0.43
INFO  [RUN-8D26D6C665BA] post published post_urn=urn:li:share:abc status=published
INFO  Workflow complete — status=PUBLISHED published=2 errors=0
```

### What each line means

| Log line | Meaning |
|---|---|
| `discovered 18 raw articles` | GNews returned results (quota OK) |
| `deduplicated: 18 → 18 → 18 unpublished-today` | No dedup hits today (fresh run) |
| `jev_prefilter: #1 ... composite=0.77` | Jev scored and ranked articles — top 2 selected by relevance + engagement |
| `jev_prefilter: skipping non-AI article` | Jev flagged `is_ai_topic < 0.5` — correctly filtered |
| `ReadTimeout` WARNING on prefilter | Jev gateway busy for that article — handled gracefully, run continues |
| `generate_personas: running ['business','policy','genz','linkedin']` | All 4 active — full set |
| `generate_personas: running ['policy','genz']` | Only 2 active — regulation story, 50% LLM savings |
| `eval ... decision=PASS` | Content quality passed all Jev thresholds |
| `eval ... decision=REGENERATE` | Below threshold — will retry up to 2 times |
| `eval ... decision=BLOCK` | PII or prompt injection detected — will not publish |
| `post published post_urn=urn:li:share:xxx` | LinkedIn accepted the post |
| `already published today — skipping` | PublishedStore Gate 2 blocked a duplicate |
| `status=PUBLISHED published=2 errors=0` | 🟢 Perfect run |

### Problem indicators

| Log line | Problem | Fix |
|---|---|---|
| `discovered 0 raw articles` | GNews 403 quota exhausted | Wait for midnight UTC reset |
| `all articles already published today` | Already ran today | Clear published store or wait until tomorrow |
| `post FAILED ... http=401` | LinkedIn token expired | Re-authorise (see Section 8) |
| `post FAILED ... http=403` | Wrong scope or token mismatch | Re-authorise with correct scopes |
| `eval ... decision=REGENERATE` | Content below quality threshold | Usually resolves on retry (max 2) |
| `OutputParserException` | LLM output didn't match schema | Usually transient — retry run |
| `status=EVALUATED published=0` | Everything ran but not published | Check `PUBLISHING_ENABLED=true` in ConfigMap |
| `pii_detected=True` or `prompt_injection_detected=True` | Hard BLOCK from Jev evaluation | Article content unusable — will not publish |

---

## 7. Checking Service Health

```bash
# All pods at a glance
oc get pods -n aifeeders

# Individual health checks
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  echo -n "$svc: "
  oc exec deployment/$svc -n aifeeders -- curl -s http://localhost:8000/health | jq -r .status
done

# Jev gateway (external — no auth required)
curl -s https://<your-jev-gateway-host>/health | jq .
# Expected: {"status":"ready","model":"Qwen/Qwen3.5-2B","method":"lora_decision_head"}

# Main API
curl -s http://$(oc get svc daily-news-api -n aifeeders -o jsonpath='{.spec.clusterIP}'):8000/health | jq .
```

Expected response from each service: `{"status": "healthy"}` or `{"status": "ok"}`.

### Verify Jev is working (5 live checks)

```bash
python scripts/verify_jev.py
# Runs 5 question types against the live Jev gateway
# Expected: all 5 pass with calibrated scores
```

---

## 8. API Quota and Key Rotation

### GNews quota exhausted (HTTP 403 from news-mcp)

The free tier allows **100 requests per day**. 6 search queries per run = 6 requests. After ~16 runs, the quota is exhausted.

```bash
# Check
oc logs deployment/news-mcp -n aifeeders | tail -5 | grep 403

# Fix: wait for midnight UTC reset (quota resets automatically)
# Or: rotate to a different GNews API key
oc patch secret aifeeders-secrets -n aifeeders \
  --type=merge -p '{"stringData":{"GNEWS_API_KEY":"<new-key>"}}'
# Restart news-mcp to pick up new key
oc rollout restart deployment/news-mcp -n aifeeders
```

### LinkedIn token expired (60-day rotation)

```bash
# Signs: post FAILED http=401 from linkedin-mcp logs
oc logs deployment/linkedin-mcp -n aifeeders | grep "401\|expired"

# Fix: re-authorise
oc port-forward svc/linkedin-mcp 8080:8000 -n aifeeders
# Browser: http://localhost:8080/auth/linkedin
# Complete OAuth flow — token is stored in pod memory
# Note: token resets on pod restart — store it in the secret if you need persistence
```

### Jev API key

```bash
oc patch secret aifeeders-secrets -n aifeeders \
  --type=merge -p '{"stringData":{"JEV_API_KEY":"<new-key>"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
```

### LLM API key

```bash
oc patch secret aifeeders-secrets -n aifeeders \
  --type=merge -p '{"stringData":{"LLM_API_KEY":"<new-key>"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout restart deployment/evaluation-mcp -n aifeeders
```

### Disable Jev (emergency fallback)

If the Jev gateway is down, set `JEV_ENABLED=false` — the pipeline falls back to heuristic article selection and LLM-based evaluation:

```bash
oc patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"false"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
```

Re-enable when Jev is back:
```bash
oc patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"JEV_ENABLED":"true"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
```

---

## 9. Cleaning the Namespace

### Remove stale completed jobs

```bash
# List completed jobs
oc get jobs -n aifeeders

# Delete all completed jobs at once
oc delete jobs -n aifeeders $(oc get jobs -n aifeeders --no-headers | awk '{print $1}')
```

### Remove old builds (should be automatic with limit=1)

```bash
# If stale builds accumulate (e.g., after manual limit override)
oc get builds -n aifeeders
oc delete build/<old-build-name> -n aifeeders

# Re-patch limits if they were reset
for bc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  oc patch buildconfig/$bc -n aifeeders \
    --type=merge -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

### Remove stale imagestreams

```bash
oc delete imagestream evaluation-mcpatest linkedin-mcpatest news-mcpatest pageindex-mcpatest \
  -n aifeeders --ignore-not-found
```

### Full namespace audit

```bash
oc get builds,buildconfigs,imagestreams,jobs,cronjobs,pods -n aifeeders
```

Expected clean state:
- **builds**: exactly 1 per BuildConfig (the latest)
- **imagestreams**: exactly 1 per service (no `*atest`)
- **jobs**: 0 (or only currently running)
- **pods**: 1–2 per Deployment + CronJob pod if running

### Reset the published store

```bash
# Use only if you want to re-publish today's articles (testing / debugging)
POD=$(oc get pods -n aifeeders -l app=daily-news-api --no-headers | head -1 | awk '{print $1}')
oc exec $POD -n aifeeders -- rm -f /tmp/aifeeders_published.json
```

---

## 10. Deploying to EKS

The application Kubernetes YAMLs (Deployments, Services, CronJob, ConfigMap, Secrets, NetworkPolicies, RBAC) are **identical** for EKS. Only the image build and push process changes.

### What is the same on EKS

| Component | Status |
|---|---|
| Deployment YAMLs | ✅ Copy-paste — only `image:` field changes |
| Service YAMLs | ✅ Identical |
| CronJob YAML | ✅ Identical (same schedule, same trigger) |
| ConfigMap YAML | ✅ Identical |
| Secret YAML | ✅ Identical (or use External Secrets Operator) |
| RBAC (Role/RoleBinding) | ✅ Identical |
| NetworkPolicy | ✅ Identical (requires Calico or Cilium CNI) |
| Application code | ✅ Identical — no OpenShift SDK |
| Port-forward for LinkedIn OAuth | `kubectl port-forward` (same syntax) |

### EKS First-Time Setup

```bash
# 1. Create namespace
kubectl create namespace aifeeders

# 2. Create ECR repositories (once per repo)
export AWS_ACCOUNT=123456789012
export AWS_REGION=us-east-1
export ECR_REGISTRY=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

for repo in daily-news news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  aws ecr create-repository --repository-name aifeeders/$repo --region $AWS_REGION
done

# Set ECR lifecycle policy (replaces OpenShift's successfulBuildsHistoryLimit)
for repo in daily-news news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  aws ecr put-lifecycle-policy \
    --repository-name aifeeders/$repo \
    --lifecycle-policy-text '{"rules":[{"rulePriority":1,"description":"Keep last 3 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}'
done

# 3. Create imagePullSecret for ECR
kubectl create secret docker-registry ecr-credentials \
  --docker-server=$ECR_REGISTRY \
  --docker-username=AWS \
  --docker-password=$(aws ecr get-login-password --region $AWS_REGION) \
  -n aifeeders

# 4. Apply RBAC, ConfigMap, Secrets
kubectl apply -f openshift/rbac.yaml -n aifeeders
kubectl apply -f openshift/configmap.yaml -n aifeeders
kubectl apply -f openshift/secrets.yaml -n aifeeders

# 5. Build and push all images (see EKS Build Flow below)

# 6. Deploy all services
kubectl apply -f openshift/deployments/ -n aifeeders
kubectl apply -f openshift/services/ -n aifeeders
kubectl apply -f openshift/cronjob.yaml -n aifeeders

# 7. Authorise LinkedIn
kubectl port-forward svc/linkedin-mcp 8080:8000 -n aifeeders
# Browser: http://localhost:8080/auth/linkedin

# 8. Smoke test
kubectl patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"false"}}'
kubectl create job smoke-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders
# Expected: status=EVALUATED published=0 errors=0
kubectl patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

### EKS Build Flow

```bash
# Run tests first — always
python -m pytest tests/unit tests/workflow -q --tb=short

# Build image
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
  daily-news=$ECR_REGISTRY/aifeeders/daily-news:latest \
  -n aifeeders

kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders --timeout=60s

# Trigger a run
kubectl create job live-run-$(date +%s) --from=cronjob/daily-ai-news -n aifeeders

# Watch
kubectl logs -f job/live-run-<id> -n aifeeders | grep -E "status=|published"
# Expected: Workflow complete — status=PUBLISHED published=2 errors=0
```

### EKS Differences to Address

| Topic | Action |
|---|---|
| Image cleanup | ECR lifecycle policy (keep last 3) — replaces OpenShift's `successfulBuildsHistoryLimit: 1` |
| Secrets | Replace raw K8s Secrets with **External Secrets Operator** + AWS Secrets Manager |
| Service accounts | Add **IRSA** annotation if any service uses AWS SDK |
| NetworkPolicy | Requires CNI with NetworkPolicy support (Calico or Cilium) |
| Image pull | Add `imagePullSecrets` referencing ECR credentials to each Deployment |
| Ingress | Replace OpenShift Route with ALB Ingress Controller or nginx-ingress |
| Node sizing | `t3.medium` minimum; `t3.large` recommended for LLM-calling pods |

---

## 11. What Can Go Wrong

### No post today

```bash
oc logs -l app=daily-news-worker -n aifeeders --since=4h | grep -E "status=|ERROR"
```

| Symptom | Cause | Fix |
|---|---|---|
| `discovered 0 raw articles` | GNews quota | Wait until midnight UTC |
| `all articles already published today` | Re-run same day | Clear published store (`rm /tmp/aifeeders_published.json`) |
| `status=EVALUATED published=0` | `PUBLISHING_ENABLED=false` | Check ConfigMap |
| `post FAILED http=401` | LinkedIn token expired | Re-authorise (see Section 8) |
| `OutputParserException` | LLM schema mismatch | Retry run |
| `eval decision=BLOCK` | PII or prompt injection in content | Article blocked by Jev — not publishable |

### Personas not generating

```bash
oc logs -l app=daily-news-worker -n aifeeders --since=1h | grep -E "persona|OutputParser"
```

The `_PersonaOutputRaw` lenient parser handles all LLM display-name mismatches. If you still see `ValidationError`, check that the LLM is outputting valid JSON.

If Jev routing is selecting 0 personas, check that `JEV_ENABLED=true` and the Jev gateway is healthy. Fallback is all 4 personas.

### Jev gateway timeouts

```bash
# Check concurrency setting (should be 5)
grep "Semaphore" src/daily_news/agents/jev_agents.py
# Should show: asyncio.Semaphore(5)

# Check Jev health
curl -s https://<your-jev-gateway-host>/health | jq .
```

With `asyncio.Semaphore(5)`, at most 5 articles score in parallel. Individual timeouts are caught and logged as WARNING — they do not fail the run. If the entire gateway is down, set `JEV_ENABLED=false` temporarily (see Section 8).

### Build upload timeout

```bash
# Verify .venv is excluded (it's 269 MB)
du -sh .venv/

# Dry-run the rsync to see what would be uploaded
rsync --dry-run -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . /tmp/check/ | head -20
```

If the build hangs on "Uploading directory", the rsync exclude is wrong. Check that `.venv/` is listed in the `--exclude` options.

### Eval thresholds too strict

If every run returns `eval decision=REGENERATE` or articles never publish:

```bash
# Check current thresholds
oc get configmap aifeeders-config -n aifeeders -o yaml | grep EVAL

# Loosen thresholds temporarily
oc patch configmap aifeeders-config -n aifeeders \
  --type=merge -p '{
    "data": {
      "EVAL_FACTUALITY_THRESHOLD": "0.40",
      "EVAL_GROUNDEDNESS_THRESHOLD": "0.40",
      "EVAL_HALLUCINATION_THRESHOLD": "0.90"
    }
  }'
oc rollout restart deployment/daily-news-api -n aifeeders
```

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **Jev System One** | External AI decision engine. Scores articles, routes personas, evaluates content. REST gateway at `/v1/systemone`. Model: Qwen/Qwen3.5-2B with lora_decision_head. |
| **JEV_ENABLED** | ConfigMap flag. Set `false` to run without Jev (falls back to heuristic selection and LLM eval). |
| **noul / choice / score** | Jev question answer types. `noul` = 0–1 float (>0.5 means yes), `choice` = named label, `score` = float 0..N-1. |
| **Composite score** | `relevance × 0.6 + engagement × 0.4`. Used to rank articles in the prefilter step. |
| **PublishedStore** | File-backed deduplication store at `/tmp/aifeeders_published.json`. Prevents re-publishing the same article on the same day. 7-day TTL. Thread-safe. |
| **publication_key** | `{article_id}:{YYYY-MM-DD}:{headline_hash[:12]}`. Stable idempotency key passed to LinkedIn MCP — no run_id so it is identical across retries. |
| **run_id** | `RUN-{UUID8}`. Unique per workflow execution. Used as Langfuse trace session seed. |
| **Binary build** | OpenShift build triggered by uploading a local directory. No Git webhook. No Docker Hub pull. |
| **BuildConfig** | OpenShift resource that defines how to build an image from source. One per service. |
| **successfulBuildsHistoryLimit: 1** | OpenShift auto-deletes all but the last successful build. Keeps namespace clean. |
| **MCP** | Model Context Protocol. `POST /call { "tool": "...", "arguments": {...} }` REST convention used for inter-service calls. |
| **Persona stub** | Empty `PersonaOutput` created for personas not selected by Jev routing. Publisher skips stubs. |
| **_PersonaOutputRaw** | Lenient Pydantic model with `persona: str` used as the LLM parse target. Prevents `ValidationError` when the LLM outputs display names instead of enum keys. |
| **PUBLISHING_ENABLED** | ConfigMap flag. `false` = full workflow runs but no LinkedIn post is made. Safe for testing. |
| **GNews quota** | 100 requests/day on the free tier. Resets at midnight UTC. 6 queries per run = ~16 runs max per day. |
| **Gate 1 / 2 / 3** | The three independent deduplication gates: deduplicate node / publisher pre-check / LinkedIn MCP idempotency key. |
| **IRSA** | IAM Roles for Service Accounts. AWS mechanism for pod-level IAM permissions without storing credentials. |
| **ECR** | Elastic Container Registry. AWS-managed Docker registry used for EKS image storage. |
