# AIFeeders Runbook

> **Build #84** — Closed-Loop Enterprise Intelligence · Epistemological Judgment · Dynamic Live Roundtable · Multi-Cloud (OpenShift, EKS, AKS)  
> **Last verified:** Live Run `RUN-0C3B37F29E22` on OpenShift · 223 tests passing · Published `urn:li:share:7509289467577315328`

---

## Table of Contents

1. [What This System Does in Plain English](#1-what-this-system-does-in-plain-english)
2. [Normal Day — What to Check](#2-normal-day--what-to-check)
3. [First-Time Setup on a New Cluster (OpenShift / EKS / AKS)](#3-first-time-setup-on-a-new-cluster-openshift--eks--aks)
4. [Docker Build & Multi-Cloud Deployment Guide](#4-docker-build--multi-cloud-deployment-guide)
5. [Triggering a Run Manually & Live Testing](#5-triggering-a-run-manually--live-testing)
6. [How to Read the Logs Across the 13 Stages](#6-how-to-read-the-logs-across-the-13-stages)
7. [Checking Service Health & Microservices](#7-checking-service-health--microservices)
8. [Production Issues — Exact Symptoms, Root Causes, Fixes](#8-production-issues--exact-symptoms-root-causes-fixes)
9. [API Quota, Guardrails & Key Rotation](#9-api-quota-guardrails--key-rotation)
10. [Networking & NetworkPolicy Security Model](#10-networking--networkpolicy-security-model)
11. [Scaling & Resource Management](#11-scaling--resource-management)
12. [Observability & Feedback Loop Operations](#12-observability--feedback-loop-operations)
13. [Glossary](#13-glossary)

---

## 1. What This System Does in Plain English

AIFeeders is a **fully automated AI news intelligence and media round-table platform**. Every day, on schedule, the system wakes up, discovers fresh AI news, inspects it for safety and prompt injection, builds a vectorless evidence tree, separates verified facts from corporate PR claims, determines why practitioners care via Jev, writes a live broadcast dialogue across four human personas, evaluates quality and reach, and publishes to LinkedIn with 100% dynamic SEO/AEO hashtags.

Post-publication, the system diagnoses structural engagement signals and logs learning mutations for future iterations.

### The Schedule
A Kubernetes CronJob fires at **08:00 UTC** and **16:00 UTC** every day.

---

## 2. Normal Day — What to Check

If you are checking whether today's run succeeded, execute these checks in order:

### Step 1 — Are the pods running?
```bash
# OpenShift
oc get pods -n aifeeders

# EKS / AKS
kubectl get pods -n aifeeders
```

Expected status: all pods `Running` with 0 restarts.

### Step 2 — Check the latest run logs
```bash
oc logs -n aifeeders deployment/daily-news-api --tail=100
```
Look for `Workflow complete — status=OPTIMIZED published=1 errors=0`.

---

## 3. First-Time Setup on a New Cluster (OpenShift / EKS / AKS)

### Step 1 — Create the namespace / project
```bash
oc new-project aifeeders || kubectl create namespace aifeeders
```

### Step 2 — Create Secrets
```bash
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY=<your-key> \
  --from-literal=GNEWS_API_KEY=<your-gnews-key-1> \
  --from-literal=GNEWS_API_KEY_2=<your-gnews-key-2> \
  --from-literal=JEV_API_KEY=<your-jev-key> \
  --from-literal=LINKEDIN_CLIENT_ID=<your-id> \
  --from-literal=LINKEDIN_CLIENT_SECRET=<your-secret> \
  -n aifeeders
```

### Step 3 — Apply ConfigMaps & Microservices
```bash
kubectl apply -f openshift/configmap.yaml -n aifeeders
kubectl apply -f openshift/news-mcp.yaml -n aifeeders
kubectl apply -f openshift/pageindex-mcp.yaml -n aifeeders
kubectl apply -f openshift/evaluation-mcp.yaml -n aifeeders
kubectl apply -f openshift/linkedin-mcp.yaml -n aifeeders
kubectl apply -f openshift/daily-news-api.yaml -n aifeeders
```

---

## 4. Docker Build & Multi-Cloud Deployment Guide

### 4.1 Local Docker Container Build
```bash
# Clean build context to avoid uploading unnecessary virtual environments
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

docker build -t aifeeders/daily-news:latest "$TMPDIR"
```

### 4.2 Push to OpenShift
```bash
oc project aifeeders
oc start-build daily-news --from-dir=. --follow
oc rollout restart deployment/daily-news-api -n aifeeders
```

### 4.3 Push to AWS EKS (ECR)
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker tag aifeeders/daily-news:latest <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/aifeeders:latest
docker push <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/aifeeders:latest
kubectl rollout restart deployment/daily-news-api -n aifeeders
```

### 4.4 Push to Azure AKS (ACR)
```bash
az acr login --name aifeedersregistry
docker tag aifeeders/daily-news:latest aifeedersregistry.azurecr.io/daily-news:latest
docker push aifeedersregistry.azurecr.io/daily-news:latest
kubectl rollout restart deployment/daily-news-api -n aifeeders
```

---

## 5. Triggering a Run Manually & Live Testing

### Run workflow inside the running pod:
```bash
oc rsh deployment/daily-news-api python -m daily_news.workflow_runner
```

---

## 6. How to Read the Logs Across the 13 Stages

Each run outputs traceable logs prefixed with `[RUN-XXXXXXXX]`:
1. `discover_news started` → Raw GNews articles fetched.
2. `deduplicated` → 3-pass deduplication filtering duplicate URLs and headlines.
3. `input guardrail` → Prompt injection & PII inspection passing status.
4. `jev_prefilter` → Composite scoring and article ranking.
5. `judgment analysis` → Number of verified facts, claims, and uncertainties.
6. `story extracted` → Narrative style, hook, and tension model.
7. `jev_find_angle` & `jev_route_personas` → Audience calibration and active persona routing.
8. `generate_personas` → Live round-table debate generation.
9. `eval` → Factuality, groundedness, and hallucination scores.
10. `score_reach` → Organic reach score (0–100) and auto-repair status.
11. `grammar_agent` → Punctuation and spelling corrections.
12. `post published` → LinkedIn post URN generated.
13. `ContentOptimizer` → Structural performance diagnosis and mutation recording.

---

## 7. Checking Service Health & Microservices

```bash
# Check all services
oc get svc -n aifeeders

# Test API health endpoint
oc exec deployment/daily-news-api -- curl -s http://localhost:8080/health
```

---

## 8. Production Issues — Exact Symptoms, Root Causes, Fixes

### Issue 1: Comments API `PERMISSION_ERROR`
- **Symptom**: `Comments API not available (PERMISSION_ERROR) — personas embedded in post body`.
- **Root Cause**: LinkedIn account requires "Community Management API" approval for programmatic comments.
- **Resolution**: Normal and expected. The `PublisherAgent` automatically embeds all round-table personas directly into the main post body with zero data loss.

### Issue 2: GNews API 403 Rate Limit
- **Symptom**: `news.search_latest failed: HTTP 403`.
- **Root Cause**: Daily request limit reached on key 1.
- **Resolution**: Automatic. The adapter switches automatically to `GNEWS_API_KEY_2`.

### Issue 3: Evaluation Decision `REGENERATE`
- **Symptom**: `eval decision=REGENERATE factuality=0.42`.
- **Root Cause**: Persona output contained ungrounded claims.
- **Resolution**: Automatic. LangGraph executes a back-edge to `summarize` with feedback up to `MAX_RETRIES=2`.

---

## 9. Security & NetworkPolicy

- **Non-Root Execution**: Runs as user `1001`.
- **Untrusted Input Isolation**: Raw news content is treated as data, never system instructions.
- **Fake Quotation Guardrails**: Prevents attributing unverified statements to real people.

---
*AIFeeders Operational Runbook.*
