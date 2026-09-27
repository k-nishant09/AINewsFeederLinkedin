# AIFeeders — Operational Runbook

> **From zero to published post** — step-by-step build, deploy, execute, monitor, triage.
> Covers: Podman local · OpenShift · AWS EKS · Azure AKS · day-two operations · incident response.
> Last verified: `RUN-AB68D6F3A20E` published `urn:li:share:7510052603121012736` · OpenShift production · comic attached.
> Last clean rebuild: cluster `api.your-cluster.example.com` · namespace `aifeeders` · all 5 services rebuilt and verified running.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Build — Container Images](#2-build--container-images)
3. [Deploy — OpenShift](#3-deploy--openshift)
4. [Deploy — AWS EKS](#4-deploy--aws-eks)
5. [Deploy — Azure AKS](#5-deploy--azure-aks)
6. [First-Time Secret Setup](#6-first-time-secret-setup)
7. [Post-Deployment Validation](#7-post-deployment-validation)
8. [Executing the Pipeline](#8-executing-the-pipeline)
9. [Reading Logs & Traces](#9-reading-logs--traces)
10. [Daily Operations Checklist](#10-daily-operations-checklist)
11. [Incident Triage](#11-incident-triage)
12. [API Key Rotation](#12-api-key-rotation)
13. [Environment Migration (Cloud Switching)](#13-environment-migration-cloud-switching)
14. [Architecture FAQ — Why Each Component Exists](#14-architecture-faq--why-each-component-exists)
15. [Full Clean Rebuild — OpenShift](#15-full-clean-rebuild--openshift)

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         EXECUTION FLOW (ONE RUN)                             │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  TRIGGER                                                                     │
│  CronJob 08:00 UTC + 16:00 UTC                                               │
│  OR: POST http://<api>/workflow/daily-news                                   │
│                          │                                                   │
│                          ▼                                                   │
│  STAGE 1 · DISCOVERY     discover_news → deduplicate → fetch_articles        │
│  STAGE 2 · INDEXING      index_pageindex (vectorless document tree)          │
│  STAGE 3 · INTELLIGENCE  jev_prefilter → find_angle → summarize → jev_router│
│  STAGE 4 · GENERATION    generate_personas (Qwen @ 0.7, asyncio.gather)      │
│  STAGE 5 · EVALUATION    evaluate: Jev floats + Judge Qwen @ 0.1             │
│                          ├── PASS → score_reach → publish                    │
│                          └── REGENERATE (retry < 2) → find_angle             │
│  STAGE 6 · PUBLISH       OutputGuardrail → Unicode bold → LinkedIn MCP       │
│  STAGE 7 · LEARN         optimize_content → story mutations                  │
│                                                                              │
│  STATUS at END: workflow_status = OPTIMIZED                                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Five containers — one bridge network:**

| Container | Port | Role |
|---|---|---|
| `daily-news-api` | 8000 | FastAPI + LangGraph engine + all 13 agents |
| `news-mcp` | 8101 | GNews REST client + article scraper |
| `pageindex-mcp` | 8102 | In-memory document tree (resets on restart) |
| `evaluation-mcp` | 8103 | LLM-backed eval engine (Jev fallback) |
| `linkedin-mcp` | 8104 | LinkedIn Posts + Comments API + analytics |

**MCP services must be healthy before starting `daily-news-api`.** The API calls all four at startup.

---

## 2. Build — Container Images

> **Docker or Podman?** Commands below use `docker`. Swap to `podman` everywhere if you prefer rootless builds — the CLI is fully compatible. Podman is required for OpenShift-local builds.

### 2.1 Prerequisites

```bash
docker --version     # Docker 24+ recommended (or Podman 4.x+)
python3 --version    # 3.11+
cp .env.example .env
# Edit .env — set at minimum: LLM_BASE_URL, LLM_API_KEY, GNEWS_API_KEY

# Set a version tag — use git SHA in CI for traceability
export IMAGE_TAG=$(git rev-parse --short HEAD)
# Or a semantic version: export IMAGE_TAG=1.0.0
```

### 2.2 Build all 5 images

```bash
# Main API — all LangGraph nodes, agents, prompts + CairoSVG for comic generation
docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t ainewsfeederlinkedin/daily-news-api:${IMAGE_TAG} \
  -t ainewsfeederlinkedin/daily-news-api:latest \
  .

# MCP microservices
docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t ainewsfeederlinkedin/news-mcp:${IMAGE_TAG} \
  -t ainewsfeederlinkedin/news-mcp:latest \
  mcp_servers/news_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t ainewsfeederlinkedin/pageindex-mcp:${IMAGE_TAG} \
  -t ainewsfeederlinkedin/pageindex-mcp:latest \
  mcp_servers/pageindex_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t ainewsfeederlinkedin/evaluation-mcp:${IMAGE_TAG} \
  -t ainewsfeederlinkedin/evaluation-mcp:latest \
  mcp_servers/evaluation_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t ainewsfeederlinkedin/linkedin-mcp:${IMAGE_TAG} \
  -t ainewsfeederlinkedin/linkedin-mcp:latest \
  mcp_servers/linkedin_mcp/
```

### 2.2.1 Tag & Push to Target Registry (OpenShift / EKS / AKS)

Authenticate and push images to your cloud container registry:

```bash
# ── Define Target Registry Variable ──────────────────────────────────────────
# OpenShift internal registry (via default route):
# export REGISTRY=$(oc get route default-route -n openshift-image-registry --template='{{ .spec.host }}')/aifeeders

# AWS EKS (Amazon ECR):
# export REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/aifeeders"

# Azure AKS (Azure Container Registry):
# export REGISTRY="${ACR_NAME}.azurecr.io/aifeeders"

# ── Tag and Push All 5 Images ────────────────────────────────────────────────
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  docker tag ainewsfeederlinkedin/${svc}:${IMAGE_TAG} ${REGISTRY}/${svc}:${IMAGE_TAG}
  docker tag ainewsfeederlinkedin/${svc}:${IMAGE_TAG} ${REGISTRY}/${svc}:latest
  docker push ${REGISTRY}/${svc}:${IMAGE_TAG}
  docker push ${REGISTRY}/${svc}:latest
done
```

> **Why 5 images?** Independent scaling + independent failure domains.
> A GNews API quota spike forces `news-mcp` to restart — it never touches `linkedin-mcp`.
> Prompts are baked at build time — immutable per release version.

> **Multi-platform builds (CI/CD):** For `linux/amd64` + `linux/arm64` in a single push:
> ```bash
> docker buildx create --use --name aifeeders-builder
> docker buildx build --platform linux/amd64,linux/arm64 \
>   --build-arg APP_VERSION=${IMAGE_TAG} \
>   -t <REGISTRY>/aifeeders/daily-news-api:${IMAGE_TAG} --push .
> # Repeat for each MCP service with its context path.
> ```

### 2.3 Run locally (Docker)

```bash
docker network create ainews-net

# MCP services — start FIRST
docker run -d --name news-mcp       --network ainews-net -p 8101:8101 --env-file .env \
  ainewsfeederlinkedin/news-mcp:latest

docker run -d --name pageindex-mcp  --network ainews-net -p 8102:8102 --env-file .env \
  ainewsfeederlinkedin/pageindex-mcp:latest

docker run -d --name evaluation-mcp --network ainews-net -p 8103:8103 --env-file .env \
  ainewsfeederlinkedin/evaluation-mcp:latest

docker run -d --name linkedin-mcp   --network ainews-net -p 8104:8104 --env-file .env \
  ainewsfeederlinkedin/linkedin-mcp:latest

# Wait for MCP health
sleep 5

# Main API — start LAST
docker run -d --name daily-news-api --network ainews-net -p 8000:8000 --env-file .env \
  ainewsfeederlinkedin/daily-news-api:latest
```

> **Docker Compose shortcut:** `docker compose up -d` starts all 5 services with the correct network and `--env-file` automatically. Use `docker compose logs -f` to tail all logs and `docker compose down` to stop.

### 2.4 Verify all 5 healthy

```bash
for port in 8101 8102 8103 8104 8000; do
  status=$(curl -sf http://localhost:$port/health \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" \
    2>/dev/null || echo "UNREACHABLE")
  echo "  :$port → $status"
done
# Expected: all → ok (or ready)
```

---

## 3. Deploy — OpenShift

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  OPENSHIFT DEPLOYMENT FLOW                                                     │
│                                                                                │
│  Developer           OpenShift Cluster              External APIs              │
│  ─────────           ─────────────────              ──────────────             │
│  docker build        BuildConfig (S2I)              IBM Qwen Gateway           │
│  docker push    ──►  Internal ImageStream      ◄──► IBM Jev System One         │
│  apply YAMLs    ──►  Deployment (2–10 pods)    ◄──► GNews API                 │
│                      CronJob (08:00+16:00 UTC)  ◄──► LinkedIn Platform API     │
│                      ClusterIP (MCP services)        Langfuse Tracing          │
│                      NetworkPolicy (zero trust)                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

```bash
# ── STEP 1: Connect ───────────────────────────────────────────────────────────
oc login --server=https://api.your-cluster.ibm.com:6443 --token=<your-token>
oc new-project aifeeders 2>/dev/null || oc project aifeeders
export IMAGE_TAG=$(git rev-parse --short HEAD)

# ── STEP 2: Create namespace-level resources ──────────────────────────────────
oc apply -f openshift/namespace.yaml     # namespace labels + annotations
oc apply -f openshift/rbac.yaml          # ServiceAccount + least-privilege Role

# ── STEP 3: Secrets (NEVER commit these) ─────────────────────────────────────
# See Section 6 for full secret setup
oc apply -f openshift/secrets.yaml       # or create imperatively (Section 6)
oc apply -f openshift/configmap.yaml     # non-secret config (ports, log level)

# ── STEP 4: Network policy (zero-trust) ──────────────────────────────────────
oc apply -f openshift/networkpolicy.yaml
# Policies applied:
#   default-deny-all        — blocks all ingress + egress by default
#   allow-api-to-mcps       — API pod → MCP pods on their ports
#   allow-router-to-api     — OpenShift router → API pod only
#   allow-egress-internet   — all pods → HTTPS 443 to external APIs

# ── STEP 5: Deploy MCP services (order matters — they must be healthy first) ──
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/

# ── STEP 6: Deploy main API ───────────────────────────────────────────────────
oc apply -f openshift/api/

# ── STEP 7: Auto-scaling + disruption budget ──────────────────────────────────
oc apply -f openshift/hpa.yaml           # HPA: 2–10 replicas, CPU 70% / mem 80%
oc apply -f openshift/pdb.yaml           # PodDisruptionBudget: min 1 always available

# ── STEP 8: Scheduled runs ────────────────────────────────────────────────────
oc apply -f openshift/cronjob.yaml       # 08:00 UTC + 16:00 UTC

# ── STEP 9: Build images and push to OpenShift internal registry ───────────────
# Option A: S2I binary build (no local Docker needed)
oc apply -f openshift/buildconfigs.yaml
oc start-build daily-news-api --from-dir=. --follow
oc start-build news-mcp       --from-dir=mcp_servers/news_mcp/ --follow
oc start-build pageindex-mcp  --from-dir=mcp_servers/pageindex_mcp/ --follow
oc start-build evaluation-mcp --from-dir=mcp_servers/evaluation_mcp/ --follow
oc start-build linkedin-mcp   --from-dir=mcp_servers/linkedin_mcp/ --follow

# Option B: Build locally with Docker and push to internal registry
oc registry login
INTERNAL_REGISTRY=$(oc get route default-route -n openshift-image-registry \
  --template='{{ .spec.host }}')

for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  docker build --build-arg APP_VERSION=${IMAGE_TAG} \
    -t ainewsfeederlinkedin/${svc}:${IMAGE_TAG} \
    $([ "$svc" = "daily-news-api" ] && echo "." || echo "mcp_servers/${svc/-/_}/")
  docker tag ainewsfeederlinkedin/${svc}:${IMAGE_TAG} \
    ${INTERNAL_REGISTRY}/aifeeders/${svc}:${IMAGE_TAG}
  docker push ${INTERNAL_REGISTRY}/aifeeders/${svc}:${IMAGE_TAG}
done
# ImageStream triggers rolling rollout automatically on new push

# ── STEP 10: Verify ───────────────────────────────────────────────────────────
oc rollout status deployment/daily-news-api -n aifeeders
oc get pods -n aifeeders -o wide
oc exec deployment/daily-news-api -n aifeeders -- curl -s http://localhost:8000/health
```

**Update an existing deployment (code change):**
```bash
export IMAGE_TAG=$(git rev-parse --short HEAD)

# Option A: re-run binary build
oc start-build daily-news-api --from-dir=. --follow

# Option B: rebuild locally and push
docker build --build-arg APP_VERSION=${IMAGE_TAG} \
  -t ainewsfeederlinkedin/daily-news-api:${IMAGE_TAG} .
docker tag ainewsfeederlinkedin/daily-news-api:${IMAGE_TAG} \
  ${INTERNAL_REGISTRY}/aifeeders/daily-news-api:${IMAGE_TAG}
docker push ${INTERNAL_REGISTRY}/aifeeders/daily-news-api:${IMAGE_TAG}

oc rollout status deployment/daily-news-api -n aifeeders
```

---

## 4. Deploy — AWS EKS

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  AWS EKS DEPLOYMENT FLOW                                                       │
│                                                                                │
│  Developer        ECR                EKS Cluster          External APIs        │
│  ─────────        ───                ───────────          ──────────           │
│  docker build     Amazon ECR         aifeeders ns         IBM Qwen             │
│  docker push ──►  Repository    ──►  Deployments (HPA)    GNews API            │
│  kubectl apply    scanOnPush=true    CronJob              LinkedIn API          │
│                   versioned tags     ALB Ingress          Langfuse              │
│                                      NetworkPolicy                             │
│                                      Secrets Manager ESO  ◄── secrets          │
└────────────────────────────────────────────────────────────────────────────────┘
```

```bash
# ── STEP 1: Set variables ─────────────────────────────────────────────────────
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1
export ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
export CLUSTER_NAME=aifeeders-cluster
export IMAGE_TAG=$(git rev-parse --short HEAD)

# ── STEP 2: Login to ECR ─────────────────────────────────────────────────────
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ECR_REGISTRY

# ── STEP 3: Create ECR repos (idempotent, scan-on-push enabled) ───────────────
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  aws ecr create-repository --repository-name aifeeders/$svc \
    --image-scanning-configuration scanOnPush=true \
    --region $AWS_REGION 2>/dev/null || echo "  $svc: repo exists"
done

# ── STEP 4: Build all images with versioned + latest tags ─────────────────────
docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/daily-news-api:${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/daily-news-api:latest .

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/news-mcp:${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/news-mcp:latest \
  mcp_servers/news_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/pageindex-mcp:${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/pageindex-mcp:latest \
  mcp_servers/pageindex_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/evaluation-mcp:${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/evaluation-mcp:latest \
  mcp_servers/evaluation_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/linkedin-mcp:${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/linkedin-mcp:latest \
  mcp_servers/linkedin_mcp/

# Push both versioned and latest to ECR
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  docker push $ECR_REGISTRY/aifeeders/$svc:${IMAGE_TAG}
  docker push $ECR_REGISTRY/aifeeders/$svc:latest
done

# ── STEP 5: Connect kubectl ───────────────────────────────────────────────────
aws eks update-kubeconfig --region $AWS_REGION --name $CLUSTER_NAME

# ── STEP 6: Namespace + RBAC ─────────────────────────────────────────────────
kubectl create namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n aifeeders -f openshift/rbac.yaml 2>/dev/null \
  || echo "  RBAC: apply manually if OpenShift CRDs missing"

# ── STEP 7: Secrets ───────────────────────────────────────────────────────────
# See Section 6. Recommended: AWS Secrets Manager + External Secrets Operator
# Quick path (dev/staging only — not for production):
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="..." \
  --from-literal=LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=GNEWS_API_KEY="..." \
  --from-literal=GNEWS_BACKUP_API_KEY="..." \
  --from-literal=LINKEDIN_ACCESS_TOKEN="..." \
  --from-literal=LINKEDIN_PERSON_URN="urn:li:person:XXXXX" \
  --from-literal=LANGFUSE_PUBLIC_KEY="pk-lf-..." \
  --from-literal=LANGFUSE_SECRET_KEY="sk-lf-..." \
  --namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -

# ── STEP 8: Deploy all services (swap image refs to ECR) ──────────────────────
for f in openshift/{news-mcp,pageindex-mcp,evaluation-mcp,linkedin-mcp,api}/*.yaml; do
  sed "s|ainewsfeederlinkedin/|${ECR_REGISTRY}/aifeeders/|g" "$f" \
  | kubectl apply -n aifeeders -f -
done

kubectl apply -n aifeeders -f openshift/configmap.yaml
kubectl apply -n aifeeders -f openshift/networkpolicy.yaml
kubectl apply -n aifeeders -f openshift/hpa.yaml
kubectl apply -n aifeeders -f openshift/cronjob.yaml

# ── STEP 9: Verify ────────────────────────────────────────────────────────────
kubectl rollout status deployment/daily-news-api -n aifeeders
kubectl get pods -n aifeeders -o wide
kubectl exec deploy/daily-news-api -n aifeeders -- curl -s http://localhost:8000/health
```

**Update (code change):**
```bash
export IMAGE_TAG=$(git rev-parse --short HEAD)
docker build --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/daily-news-api:${IMAGE_TAG} \
  -t $ECR_REGISTRY/aifeeders/daily-news-api:latest . && \
docker push $ECR_REGISTRY/aifeeders/daily-news-api:${IMAGE_TAG} && \
docker push $ECR_REGISTRY/aifeeders/daily-news-api:latest && \
kubectl rollout restart deployment/daily-news-api -n aifeeders && \
kubectl rollout status  deployment/daily-news-api -n aifeeders
```

---

## 5. Deploy — Azure AKS

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  AZURE AKS DEPLOYMENT FLOW                                                     │
│                                                                                │
│  Developer        ACR                AKS Cluster          External APIs        │
│  ─────────        ───                ───────────          ──────────           │
│  docker build     Azure ACR          aifeeders ns         IBM Qwen             │
│  docker push ──►  aifeedersregistry  Deployments (HPA)    GNews API            │
│  kubectl apply    Managed Identity   CronJob              LinkedIn API          │
│                   versioned tags     AGIC (HTTPS + WAF)   Langfuse              │
│                                      Key Vault CSI        ◄── secrets          │
└────────────────────────────────────────────────────────────────────────────────┘
```

```bash
# ── STEP 1: Set variables ─────────────────────────────────────────────────────
export RESOURCE_GROUP=aifeeders-rg
export ACR_NAME=aifeedersregistry
export AKS_NAME=aifeeders-aks
export ACR_REGISTRY="${ACR_NAME}.azurecr.io"
export IMAGE_TAG=$(git rev-parse --short HEAD)

# ── STEP 2: Attach ACR to AKS (one-time — enables pull via Managed Identity) ──
az aks update --resource-group $RESOURCE_GROUP --name $AKS_NAME --attach-acr $ACR_NAME

# ── STEP 3: Login to ACR + build + push all images ────────────────────────────
az acr login --name $ACR_NAME

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/daily-news-api:${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/daily-news-api:latest .

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/news-mcp:${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/news-mcp:latest \
  mcp_servers/news_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/pageindex-mcp:${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/pageindex-mcp:latest \
  mcp_servers/pageindex_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/evaluation-mcp:${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/evaluation-mcp:latest \
  mcp_servers/evaluation_mcp/

docker build \
  --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/linkedin-mcp:${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/linkedin-mcp:latest \
  mcp_servers/linkedin_mcp/

# Push versioned + latest tags to ACR
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  docker push $ACR_REGISTRY/aifeeders/$svc:${IMAGE_TAG}
  docker push $ACR_REGISTRY/aifeeders/$svc:latest
done

# Alternative: in-cloud build via ACR Tasks (no local Docker required)
# az acr build --registry $ACR_NAME \
#   --image aifeeders/daily-news-api:${IMAGE_TAG} \
#   --build-arg APP_VERSION=${IMAGE_TAG} .

# ── STEP 4: Connect kubectl ───────────────────────────────────────────────────
az aks get-credentials --resource-group $RESOURCE_GROUP --name $AKS_NAME

# ── STEP 5: Namespace + secrets ───────────────────────────────────────────────
kubectl create namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -
# See Section 6 for full secret creation. Recommended: Azure Key Vault CSI Driver
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="..." \
  --from-literal=LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=GNEWS_API_KEY="..." \
  --from-literal=GNEWS_BACKUP_API_KEY="..." \
  --from-literal=LINKEDIN_ACCESS_TOKEN="..." \
  --from-literal=LINKEDIN_PERSON_URN="urn:li:person:XXXXX" \
  --from-literal=LANGFUSE_PUBLIC_KEY="pk-lf-..." \
  --from-literal=LANGFUSE_SECRET_KEY="sk-lf-..." \
  --namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -

# ── STEP 6: Deploy all services (swap image refs to ACR) ──────────────────────
for f in openshift/{news-mcp,pageindex-mcp,evaluation-mcp,linkedin-mcp,api}/*.yaml; do
  sed "s|ainewsfeederlinkedin/|${ACR_REGISTRY}/aifeeders/|g" "$f" \
  | kubectl apply -n aifeeders -f -
done

kubectl apply -n aifeeders -f openshift/configmap.yaml
kubectl apply -n aifeeders -f openshift/networkpolicy.yaml
kubectl apply -n aifeeders -f openshift/hpa.yaml
kubectl apply -n aifeeders -f openshift/cronjob.yaml

# ── STEP 7: Verify ────────────────────────────────────────────────────────────
kubectl rollout status deployment/daily-news-api -n aifeeders
kubectl get pods -n aifeeders
kubectl exec deploy/daily-news-api -n aifeeders -- curl -s http://localhost:8000/health

# ── Rolling update (code change) ─────────────────────────────────────────────
export IMAGE_TAG=$(git rev-parse --short HEAD)
az acr login --name $ACR_NAME && \
docker build --build-arg APP_VERSION=${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/daily-news-api:${IMAGE_TAG} \
  -t $ACR_REGISTRY/aifeeders/daily-news-api:latest . && \
docker push $ACR_REGISTRY/aifeeders/daily-news-api:${IMAGE_TAG} && \
docker push $ACR_REGISTRY/aifeeders/daily-news-api:latest && \
kubectl rollout restart deployment/daily-news-api -n aifeeders && \
kubectl rollout status  deployment/daily-news-api -n aifeeders
```

---

## 6. First-Time Secret Setup

**Never commit secrets to git.** Use Kubernetes Secrets for dev/staging, cloud-native vault for production.

> **Complete secret list** — the same keys are required on all three platforms. Create them once per environment using the platform-specific method below.

### OpenShift
```bash
oc create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="your-key" \
  --from-literal=LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=LLM_MODEL="qwen2-5-72b-instruct" \
  --from-literal=EVAL_LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=EVAL_LLM_API_KEY="your-key" \
  --from-literal=EVAL_LLM_MODEL="qwen2-5-72b-instruct" \
  --from-literal=GNEWS_API_KEY="primary-gnews-key" \
  --from-literal=GNEWS_BACKUP_API_KEY="backup-gnews-key" \
  --from-literal=JEV_BASE_URL="https://your-jev-gateway" \
  --from-literal=JEV_API_KEY="your-jev-key" \
  --from-literal=LINKEDIN_ACCESS_TOKEN="your-token" \
  --from-literal=LINKEDIN_PERSON_URN="urn:li:person:XXXXXXXX" \
  --from-literal=LANGFUSE_PUBLIC_KEY="pk-lf-..." \
  --from-literal=LANGFUSE_SECRET_KEY="sk-lf-..." \
  --from-literal=PUBLISHING_ENABLED="true" \
  --from-literal=APP_ENV="production" \
  -n aifeeders
```

### EKS — plain K8s secret (dev/staging) or AWS Secrets Manager (production)
```bash
# ── Option A: Plain K8s secret (dev / staging) ────────────────────────────────
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="your-key" \
  --from-literal=LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=LLM_MODEL="qwen2-5-72b-instruct" \
  --from-literal=EVAL_LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=EVAL_LLM_API_KEY="your-key" \
  --from-literal=EVAL_LLM_MODEL="qwen2-5-72b-instruct" \
  --from-literal=GNEWS_API_KEY="primary-gnews-key" \
  --from-literal=GNEWS_BACKUP_API_KEY="backup-gnews-key" \
  --from-literal=JEV_BASE_URL="https://your-jev-gateway" \
  --from-literal=JEV_API_KEY="your-jev-key" \
  --from-literal=LINKEDIN_ACCESS_TOKEN="your-token" \
  --from-literal=LINKEDIN_PERSON_URN="urn:li:person:XXXXXXXX" \
  --from-literal=LANGFUSE_PUBLIC_KEY="pk-lf-..." \
  --from-literal=LANGFUSE_SECRET_KEY="sk-lf-..." \
  --from-literal=PUBLISHING_ENABLED="true" \
  --from-literal=APP_ENV="production" \
  --namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -

# ── Option B: AWS Secrets Manager + External Secrets Operator (production) ────
# 1. Store all secrets in AWS Secrets Manager as a single JSON object
aws secretsmanager create-secret --name aifeeders/prod \
  --secret-string '{
    "LLM_API_KEY":"...","LLM_BASE_URL":"...","LLM_MODEL":"qwen2-5-72b-instruct",
    "GNEWS_API_KEY":"...","GNEWS_BACKUP_API_KEY":"...",
    "JEV_BASE_URL":"...","JEV_API_KEY":"...",
    "LINKEDIN_ACCESS_TOKEN":"...","LINKEDIN_PERSON_URN":"urn:li:person:XXXXXXXX",
    "LANGFUSE_PUBLIC_KEY":"pk-lf-...","LANGFUSE_SECRET_KEY":"sk-lf-..."
  }'

# 2. Install External Secrets Operator via Helm
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets --create-namespace

# 3. Apply ExternalSecret manifest — reads the SM ARN and creates the K8s secret
kubectl apply -n aifeeders -f eks/external-secret.yaml
```

### AKS — plain K8s secret (dev/staging) or Azure Key Vault (production)
```bash
# ── Option A: Plain K8s secret (dev / staging) ────────────────────────────────
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="your-key" \
  --from-literal=LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=LLM_MODEL="qwen2-5-72b-instruct" \
  --from-literal=EVAL_LLM_BASE_URL="https://your-gateway/v1" \
  --from-literal=EVAL_LLM_API_KEY="your-key" \
  --from-literal=EVAL_LLM_MODEL="qwen2-5-72b-instruct" \
  --from-literal=GNEWS_API_KEY="primary-gnews-key" \
  --from-literal=GNEWS_BACKUP_API_KEY="backup-gnews-key" \
  --from-literal=JEV_BASE_URL="https://your-jev-gateway" \
  --from-literal=JEV_API_KEY="your-jev-key" \
  --from-literal=LINKEDIN_ACCESS_TOKEN="your-token" \
  --from-literal=LINKEDIN_PERSON_URN="urn:li:person:XXXXXXXX" \
  --from-literal=LANGFUSE_PUBLIC_KEY="pk-lf-..." \
  --from-literal=LANGFUSE_SECRET_KEY="sk-lf-..." \
  --from-literal=PUBLISHING_ENABLED="true" \
  --from-literal=APP_ENV="production" \
  --namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -

# ── Option B: Azure Key Vault + CSI Driver (production) ───────────────────────
# 1. Enable Key Vault CSI driver addon on the AKS cluster
az aks enable-addons --addons azure-keyvault-secrets-provider \
  --resource-group $RESOURCE_GROUP --name $AKS_NAME

# 2. Grant AKS Managed Identity GET + LIST on Key Vault
az keyvault set-policy --name aifeeders-kv \
  --object-id $(az aks show --resource-group $RESOURCE_GROUP --name $AKS_NAME \
    --query "identityProfile.kubeletidentity.objectId" -o tsv) \
  --secret-permissions get list

# 3. Apply SecretProviderClass manifest — mounts secrets as files in pods
kubectl apply -n aifeeders -f aks/keyvault-secret-provider.yaml
# Secrets are auto-rotated by the CSI driver without requiring a pod restart
```

---

## 7. Post-Deployment Validation

Run this checklist after every deploy to any environment:

```bash
# ── 1. Pod health ─────────────────────────────────────────────────────────────
kubectl get pods -n aifeeders -o wide
# Expected: all 5 pods Running, 0 restarts

# ── 2. Service endpoints ──────────────────────────────────────────────────────
kubectl get svc -n aifeeders
# Expected: 4 ClusterIP MCP services + 1 LoadBalancer/Route for daily-news-api

# ── 3. API health ─────────────────────────────────────────────────────────────
kubectl exec deployment/daily-news-api -n aifeeders -- \
  curl -s http://localhost:8000/health
# Expected: {"status":"ok"}

# ── 4. MCP connectivity from API ─────────────────────────────────────────────
kubectl exec deployment/daily-news-api -n aifeeders -- bash -c "
  for port in 8101 8102 8103 8104; do
    echo -n ':'\$port': '
    curl -sf http://localhost:\$port/health 2>/dev/null | python3 -c \"import sys,json; print(json.load(sys.stdin).get('status','?'))\" || echo UNREACHABLE
  done"
# Note: MCP ports are accessed via K8s service DNS inside cluster

# ── 5. CronJob scheduled ─────────────────────────────────────────────────────
kubectl get cronjob -n aifeeders
# Expected: daily-news-cron with nextSchedule shown

# ── 6. HPA active ─────────────────────────────────────────────────────────────
kubectl get hpa -n aifeeders
# Expected: daily-news-api-hpa targets showing metrics

# ── 7. Dry-run pipeline (PUBLISHING_ENABLED=false) ────────────────────────────
kubectl exec deployment/daily-news-api -n aifeeders -- \
  curl -s -X POST http://localhost:8000/workflow/daily-news \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('run_id:', d.get('run_id'), 'status:', d.get('workflow_status'))"
# Expected: run_id=RUN-XXXXXXXX  status=OPTIMIZED (or PUBLISHED if PUBLISHING_ENABLED=true)
```

---

## 8. Executing the Pipeline

### 8.1 Automatic (CronJob)
Runs at **08:00 UTC** and **16:00 UTC** daily. No action required after deploy.

```bash
# Check next scheduled run
kubectl get cronjob daily-news-cron -n aifeeders

# View last 5 job executions
kubectl get jobs -n aifeeders --sort-by='.metadata.creationTimestamp' | tail -6

# Watch logs of last job
kubectl logs job/$(kubectl get jobs -n aifeeders \
  --sort-by='.metadata.creationTimestamp' -o name | tail -1 | cut -d/ -f2) \
  -n aifeeders --follow
```

### 8.2 Manual trigger via REST API
```bash
# Inside cluster
kubectl exec deployment/daily-news-api -n aifeeders -- \
  curl -s -X POST http://localhost:8000/workflow/daily-news \
  -H "Content-Type: application/json" -d '{}'

# From outside cluster (OpenShift Route)
curl -X POST https://daily-news-api-aifeeders.apps.your-cluster.com/workflow/daily-news \
  -H "Content-Type: application/json" -d '{}'
```

### 8.3 Check run status
```bash
curl http://localhost:8000/workflow/<run_id>
# Returns: {"run_id":"...", "workflow_status":"OPTIMIZED", "linkedin_results":[...], "errors":[...]}
```

### 8.4 Trigger in dry-run mode (no LinkedIn publish)
```bash
# Temporarily override PUBLISHING_ENABLED in a debug pod
kubectl run debug-run --image=curlimages/curl --rm -it --restart=Never \
  -n aifeeders -- curl -s -X POST http://daily-news-api:8000/workflow/daily-news \
  -H "Content-Type: application/json" -d '{}'
# The PUBLISHING_ENABLED=false env var in .env/secret controls publication
```

---

## 9. Reading Logs & Traces

### 9.1 Log pattern per run
Every log line is prefixed `[RUN-XXXXXXXX]` — the unique run ID. All 13 stages emit structured INFO logs:

```text
[RUN-0C3B37F29E22] discover_news started
[RUN-0C3B37F29E22] discovered 87 raw articles
[RUN-0C3B37F29E22] deduplicated: 87 raw → 61 url-unique → 58 unpublished-today → 42 title-unique
[RUN-0C3B37F29E22] input guardrail: 42 passed / 42 inspected
[RUN-0C3B37F29E22] jev_prefilter: JEV_BASE_URL not set — skipping  ← INFO not WARNING
[RUN-0C3B37F29E22] sentiment resolved article=abc123 provider=jev label=positive
[RUN-0C3B37F29E22] judgment analysis article=abc123 facts=4 claims=3 uncertainties=2
[RUN-0C3B37F29E22] story extracted article=abc123 style=AI_DEBATE hook_len=142
[RUN-0C3B37F29E22] generate_personas: running ['business', 'linkedin', 'genz']
[RUN-0C3B37F29E22] eval article=abc123 decision=PASS factuality=0.88 groundedness=0.84 hallucination=0.04
[RUN-0C3B37F29E22] llm_judge review article=abc123 verdict=PASS quality=0.91 boilerplate=False critique=''
[RUN-0C3B37F29E22] score_reach article=abc123 reach_score=86/100 verdict=PUBLISH
[RUN-0C3B37F29E22] post composed article=abc123 python_len=1823 linkedin_utf16_len=2104
# If banned phrase detected (REGENERATE triggered):
[RUN-0C3B37F29E22] WARNING banned_phrase_detected persona=business phrase='when i was scaling' — injecting sentinel to force REGENERATE
[RUN-0C3B37F29E22] WARNING OutputGuardrail: BANNED_CONTENT sentinel detected — persona=business phrase='when i was scaling'
[RUN-0C3B37F29E22] WARNING OutputGuardrail BLOCKED post article=abc123 violations=['banned_phrase_in_persona_output: ...']
[RUN-0C3B37F29E22] post published post_urn=urn:li:share:7509289467577315328 status=published
[RUN-0C3B37F29E22] Workflow complete — status=OPTIMIZED published=1 errors=0
```

### 9.2 Log levels guide

| Level | Meaning | Action needed? |
|---|---|---|
| `INFO` | Normal progress, Jev skip notices | None |
| `WARNING` | Non-fatal: article fetch fail, dedup drop, max retries | Monitor trends |
| `ERROR` | Agent failure — article skipped, not published | Investigate if persistent |

### 9.3 Langfuse distributed traces
All spans tagged with `run_id` — search in Langfuse dashboard:
- Filter by tag `run_id=RUN-XXXXXXXX` to see the complete trace
- Compare persona generation quality across runs
- Token cost and latency per node

### 9.4 Fetch logs

```bash
# OpenShift
oc logs deployment/daily-news-api -n aifeeders --tail=200 --follow

# EKS / AKS
kubectl logs deployment/daily-news-api -n aifeeders --tail=200 --follow

# Grep for a specific run
kubectl logs deployment/daily-news-api -n aifeeders | grep "RUN-0C3B37F29E22"

# Grep for errors only
kubectl logs deployment/daily-news-api -n aifeeders | grep -E "ERROR|REGENERATE|BLOCK"
```

---

## 10. Daily Operations Checklist

```bash
# Run every morning before the 08:00 UTC CronJob fires:

# 1. All pods running (0 restarts)
kubectl get pods -n aifeeders

# 2. Last CronJob succeeded
kubectl get jobs -n aifeeders --sort-by='.metadata.creationTimestamp' | tail -3

# 3. GNews API key status — watch for 403 in logs
kubectl logs deployment/daily-news-api -n aifeeders | grep "403\|quota\|rate limit" | tail -5

# 4. LinkedIn token valid — watch for 401
kubectl logs deployment/daily-news-api -n aifeeders | grep "401\|INVALID_ACCESS_TOKEN" | tail -5

# 5. HPA not stuck at max replicas (memory leak signal)
kubectl get hpa -n aifeeders

# 6. No persistent REGENERATE loops (prompt quality signal)
kubectl logs deployment/daily-news-api -n aifeeders | grep REGENERATE | tail -10

# 7. No persistent banned-phrase blocks (persona prompt quality signal)
kubectl logs deployment/daily-news-api -n aifeeders | grep "BANNED_CONTENT\|banned_phrase_detected" | tail -10
```

---

## 11. Incident Triage

### ❌ GNews HTTP 403 — rate limit hit

```
news.search_latest failed for '...': HTTP 403 Forbidden
```
**Cause:** Primary GNews key reached 100 req/day limit (resets 00:00 UTC = 05:30 IST).
**Auto-handled:** `news-mcp` rotates to `GNEWS_BACKUP_API_KEY` automatically.
**Manual action:** If both keys exhausted, rotate keys (Section 12). Wait until 00:00 UTC.

---

### ❌ LinkedIn 401 — token expired

```
post FAILED ... http=401 li_code=INVALID_ACCESS_TOKEN
```
**Cause:** LinkedIn 3-legged OAuth tokens expire after 60 days.
**Fix:** Generate a new token from LinkedIn Developer Portal with `w_member_social` scope.
```bash
kubectl create secret generic daily-news-secrets \
  --from-literal=LINKEDIN_ACCESS_TOKEN="new-token" \
  --dry-run=client -o yaml | kubectl apply -n aifeeders -f -
kubectl rollout restart deployment/daily-news-api -n aifeeders
```

---

### ❌ Evaluation REGENERATE loop

```
REGENERATE decision — retry 1/3
llm_judge REGENERATE article=abc123 boilerplate=False throat=True
  critique='The post starts with a banned opener: Microsoft is refocusing...'
```
**Cause (A) — false-positive throat-clearing flag:** The LLM judge at `temperature=0.1` was
over-applying the `"Any persona text starting with 'I'"` rule and flagging openers that begin
with a company name, statistic, or news fact. Fixed in `evaluation_agent.py` — removed the
broad `I`-opener rule from the judge prompt; replaced with:
> `"NOTE: An opener starting with a company name, statistic, or news fact is NOT banned."`

**Cause (B) — `avoid_phrases` not reaching the generator:** `failure_reasons` contained full
judge critiques like `"llm_judge: The post contains the banned phrase 'the real question' in
the third paragraph."` The generator received the verbose string instead of the bare phrase.
Fixed in `daily_news_graph.py` — the retry path now extracts all single-quoted substrings from
each failure reason before injecting, so the generator receives `"the real question"` not the
full critique.

**Cause (C) — hallucination threshold too tight:** `EVAL_HALLUCINATION_THRESHOLD=0.85` in
configmap was below the ~0.95 scores the evaluation-mcp returns for this model. Every run
triggered REGENERATE regardless of actual content quality.
**Fix:** Patched `EVAL_HALLUCINATION_THRESHOLD` to `0.97` in configmap:
```bash
oc patch configmap daily-news-config -n aifeeders \
  --type=merge -p '{"data":{"EVAL_HALLUCINATION_THRESHOLD":"0.97"}}'
oc rollout restart deployment/daily-news-api -n aifeeders
```

**Auto-handled:** LangGraph loops back to `generate_personas` with extracted avoid_phrases
injected. Max 3 retries. With the three fixes above, runs pass on first or second attempt.
**If still looping:** Check `oc logs deployment/daily-news-api | grep "llm_judge"` for the
offending critique, verify the phrase is in `_BANNED_INLINE_PHRASES` in `publisher_agent.py`.

---

### ❌ OutputGuardrail BANNED_CONTENT block

```
OutputGuardrail: BANNED_CONTENT sentinel detected — persona=business phrase='when i was scaling'
OutputGuardrail BLOCKED post article=abc123 violations=['banned_phrase_in_persona_output: persona=business phrase=...']
```
**Cause:** A persona's generated text contained a banned opener (e.g. `"When I was scaling"`, `"Consider a scenario"`) or banned inline phrase. The `_check_persona_text()` deterministic scanner caught it and injected a `[BANNED_CONTENT:]` sentinel into the post body. `OutputGuardrail.inspect_output()` detected the sentinel and blocked publication.
**Auto-handled:** Publisher returns `output_guardrail_block:banned_phrase_in_persona_output` — graph routes to REGENERATE. No manual action needed for isolated occurrences.
**If persistent (every run):** Inspect the persona prompt for the offending persona. Verify that `_BANNED_OPENERS` and `_BANNED_INLINE_PHRASES` in `publisher_agent.py` include the recurring phrase — add it if missing.

```bash
# See which persona and phrase triggered it
kubectl logs deployment/daily-news-api -n aifeeders | grep "banned_phrase_detected\|BANNED_CONTENT"
```

---

### ❌ Jev UnsupportedProtocol

```
JevClient() UnsupportedProtocol: ''
```
**Cause:** `JEV_BASE_URL` is set to a non-empty but malformed value (e.g. just a hostname without `https://`).
**Fix:** Either leave `JEV_BASE_URL` blank (skips Jev gracefully) or set a valid `https://...` URL.

---

### ❌ pageindex-mcp returns empty sections

```
get_relevant_sections returned sections_text=''
```
**Cause:** `pageindex-mcp` resets in-memory state on pod restart. The document was indexed before the restart.
**Fix:** Run the pipeline again — `index_pageindex` re-indexes each article at the start of every run.

---

### ❌ LLM_BASE_URL missing — Connection error on all LLM calls

```
WARNING  daily_news.agents.judgment_agent — JudgmentAgent failed for <id>: Connection error.
WARNING  daily_news.agents.summary_agent  — MediaStorytellerAgent failed for <id>: Connection error.
ERROR    daily_news.workflows.daily_news_graph — summarize failed for <id>: Connection error.
```
**Cause:** `LLM_BASE_URL` not set in configmap. All LangChain/OpenAI client calls fail immediately
with `Connection error` because the base URL is empty.
**Fix:**
```bash
# Set the IBM Model Gateway endpoint
oc patch configmap daily-news-config -n aifeeders \
  --type=merge \
  -p '{"data":{"LLM_BASE_URL":"https://model-gateway-model-gateway.apps.<cluster>/v1"}}'
oc rollout restart deployment/daily-news-api deployment/evaluation-mcp -n aifeeders
```
**Verify from inside the cluster:**
```bash
oc exec deployment/daily-news-api -n aifeeders -- curl -sk \
  "$LLM_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2-5-72b-instruct","messages":[{"role":"user","content":"Reply: READY"}],"max_tokens":5}' \
  | python3 -m json.tool
```

---

### ❌ Degenerate evaluation scores (0.0 / 0.0 / 1.0)

```
WARNING  Degenerate scores detected for <id> (factuality=0.0, groundedness=0.0, hallucination=1.0)
         — likely backend failure, applying neutral scores
```
**Cause:** `evaluation-mcp` pod was running without `LLM_BASE_URL` set (started before the
configmap patch, or the secret/configmap wasn't picked up). The MCP backend silently fails and
returns all-zero scores.
**Fix:** Restart `evaluation-mcp` after patching `LLM_BASE_URL`:
```bash
oc rollout restart deployment/evaluation-mcp -n aifeeders
# Verify it picked up the URL:
oc exec deployment/evaluation-mcp -n aifeeders -- printenv LLM_BASE_URL
```
**Auto-handled:** `_neutral_result()` applies `(0.6, 0.6, 0.3)` fallback scores so the gate
continues to the LLM judge rather than hard-blocking.

---

### ❌ news_search_latest returns 0 articles after key rotation

```
✅ PASS  news_search_latest returned 0 articles
```
**Cause:** The `news-mcp` pod was not restarted after patching `GNEWS_API_KEY` in the secret.
The old pod continues using the previously env-injected (now-exhausted) key.
**Fix:**
```bash
oc rollout restart deployment/news-mcp -n aifeeders
oc rollout status deployment/news-mcp -n aifeeders --timeout=60s
```

---

### ❌ verify_mcp.py tool calls return 307 / 404

```
Tool call failed: Redirect response '307 Temporary Redirect' for url 'http://news-mcp:8000/mcp'
Tool call failed: Client error '404 Not Found' for url 'http://news-mcp:8000/mcp/'
```
**Cause:** `verify_mcp.py` was using JSON-RPC `POST /mcp` (wrong protocol). The MCP servers
expose a simple REST endpoint at `POST /call` (not JSON-RPC). Fixed in `scripts/verify_mcp.py`
— all tool calls now use `base_url.rstrip("/").removesuffix("/mcp") + "/call"`.
**Note:** This does not affect the actual workflow — `MCPHTTPClient` in `src/daily_news/mcp/client.py`
has always used `POST /call` correctly.

---

### ❌ OTLP connection refused spam

```
Failed to export spans: ConnectionRefusedError: [Errno 111] Connection refused
```
**Cause:** `OTEL_EXPORTER_OTLP_ENDPOINT` is set in `.env` but OTLP collector is not running.
**Fix:** Leave `OTEL_EXPORTER_OTLP_ENDPOINT=` (empty) in production if not using OTLP. The code installs a no-op TracerProvider automatically when the env var is blank.

---

### ❌ Langfuse auth failed

```
Langfuse init/auth failed (LLM tracing disabled)
```
**Cause:** Invalid `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` or network egress to Langfuse blocked.
**Impact:** Non-blocking — pipeline continues, tracing disabled.
**Fix:** Verify keys at `cloud.langfuse.com`. Check `NetworkPolicy` allows egress to Langfuse endpoint.

---

## 12. API Key Rotation

```bash
# ── Rotate any secret without downtime ────────────────────────────────────────
# Uses --dry-run=client -o yaml | kubectl apply pattern — updates in-place

kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="new-llm-key" \
  --from-literal=GNEWS_API_KEY="new-gnews-primary" \
  --from-literal=GNEWS_BACKUP_API_KEY="new-gnews-backup" \
  --from-literal=JEV_API_KEY="new-jev-key" \
  --from-literal=LINKEDIN_ACCESS_TOKEN="new-li-token" \
  --from-literal=LINKEDIN_PERSON_URN="urn:li:person:XXXXXX" \
  -n aifeeders --dry-run=client -o yaml | kubectl apply -f -

# Trigger rolling restart (pods pick up new env vars)
kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders
```

**GNews quota facts:**
- Free tier: 100 requests/day **total across all keys if same email**
- Reset time: 00:00 UTC (05:30 IST)
- Two keys under different accounts = genuine 200 req/day

**LinkedIn token facts:**
- 3-legged OAuth tokens last **60 days**
- Renew at: `https://www.linkedin.com/developers/apps` → OAuth 2.0 Tools → Generate Access Token
- Required scope: `w_member_social`

---

## 13. Environment Migration (Cloud Switching)

### What changes between clouds

```
┌──────────────────────┬──────────────────────────┬──────────────────────────────┐
│ Item                 │ Changes?                  │ Notes                        │
├──────────────────────┼──────────────────────────┼──────────────────────────────┤
│ Application code     │ NO                        │ Zero changes, ever           │
│ Prompts              │ NO                        │ Baked into image              │
│ LangGraph graph      │ NO                        │ Pure Python, cloud-agnostic  │
│ Kubernetes YAML      │ MINIMAL                   │ Only image registry URL      │
│ NetworkPolicy        │ NO                        │ Standard K8s, works anywhere │
│ HPA                  │ NO                        │ Standard K8s autoscaling/v2  │
│ CronJob              │ NO                        │ Standard K8s batch/v1        │
│ Secret values        │ YES                       │ Re-create in new cloud       │
│ Image registry       │ YES                       │ ECR / ACR / OpenShift        │
│ Ingress / Route      │ YES                       │ Cloud-specific controller    │
│ Secret backend       │ YES                       │ SM / Key Vault / oc secrets  │
└──────────────────────┴──────────────────────────┴──────────────────────────────┘
```

### OpenShift → EKS migration script

```bash
# 1. Export current state
oc get deployment,service,configmap,cronjob -n aifeeders -o yaml > exported.yaml

# 2. Clean OpenShift-specific fields
sed -i 's|image-registry.openshift-image-registry.svc[^"]*|'$ECR_REGISTRY'/aifeeders/daily-news-api:latest|g' exported.yaml
# Also remove: routes, buildconfigs, imagestreams, SCCs

# 3. Re-push images to ECR (Section 4)
# 4. Apply to EKS — kubectl apply -n aifeeders -f exported.yaml
# 5. Re-create secrets in EKS (Section 6)
```

---

## 14. Architecture FAQ — Why Each Component Exists

### Why LangGraph instead of a script?
A script cannot express the `evaluate → find_angle` regeneration back-edge without while-loop scaffolding across 5 functions. LangGraph makes it one conditional edge. All state flows through `NewsWorkflowState` TypedDict — no hidden globals.

### Why qwen2-5-72b-instruct for both generator and judge?
The IBM OpenShift AI gateway only serves this model. `meta-llama-3-1-70b-instruct` returns HTTP 400 `model not found`. Temperature split achieves separation: generator at 0.7 (creative), judge at 0.1 (deterministic pattern-matching). Separate chain invocations — judge never sees generator context.

### Why PageIndex instead of a vector DB?
PageIndex traverses a document tree deterministically. Identical input → identical output. No embedding model, no ANN search latency (50–200ms saved per query), no chunk boundary truncation, no external service dependency. For single-article fact extraction, deterministic > approximate.

### Why 5 containers instead of 1?
Independent failure domains. GNews quota exhaustion → `news-mcp` restarts alone. LinkedIn 429 → `linkedin-mcp` backs off without touching the LLM pipeline. Independent HPA scaling per service.

### Why hashtags in `parts[]` not the footer?
`_clip_at_sentence(post, 2800)` clips from the end. Footer is decorative — acceptable to clip. Hashtags drive LinkedIn algorithmic reach distribution — they must survive. Hashtags are last item inside `parts[]`; footer is outside `parts[]`.

### Why `JEV_BASE_URL` empty = INFO not ERROR?
Local development has no access to the IBM Jev gateway. Emitting `WARNING/ERROR` for a missing optional service would make every local dev run look broken. The `if not s.jev_base_url: return state` guard in all 3 Jev nodes means the pipeline runs end-to-end with deterministic fallbacks, not failures.

### Why is `MAX_RETRIES = 2` not higher?
Each regeneration loop costs ~10 LLM calls (find_angle + 3 summary agents + 3-4 persona calls + 2 eval calls). At `MAX_RETRIES=3` a failed run costs 40+ calls. The root cause (boilerplate opener) is solved in 1 retry once failure_reasons are injected. A third retry is wasted spend.

### Why non-root containers?
`runAsUser: 1001` + `readOnlyRootFilesystem: true` + `capabilities.drop: [ALL]` means a container escape does not yield host root. Required by OpenShift SCCs and AWS/Azure security benchmarks. Zero functional impact on the application.

---

## 15. Full Clean Rebuild — OpenShift

> Use this procedure when you need to tear down **everything** in the namespace and rebuild
> from source — after a config drift, a broken image, a namespace reset, or a fresh cluster.
> Preserves the `daily-news-secrets` secret (real credentials) and leaves it untouched.
> Verified against cluster `api.your-cluster.example.com`, namespace `aifeeders`.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CLEAN REBUILD SEQUENCE                                                      │
│                                                                              │
│  1. oc login          →  authenticate to cluster                            │
│  2. Hard delete       →  pods, builds, buildconfigs, imagestreams,          │
│                          deployments, services, routes, cronjobs,           │
│                          hpa, pdb, configmap, networkpolicies               │
│  3. Re-apply config   →  namespace → configmap → rbac → networkpolicy      │
│  4. Re-apply builds   →  buildconfigs + imagestreams (fresh)                │
│  5. Start builds      →  oc start-build --from-dir=. for all 5 services    │
│  6. Wait              →  oc wait --for=condition=Complete on all builds     │
│  7. Deploy            →  MCPs → API → cronjob → HPA → PDB                  │
│  8. Verify            →  oc wait --for=condition=Available on deployments   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 15.1 Login

```bash
# Get a fresh token from the OpenShift web console → "Copy Login Command"
# ⚠️  Never share or commit tokens — revoke immediately after use.
oc login --token=<your-token> --server=https://api.<cluster-domain>:6443
# Confirm you are in the right project
oc project aifeeders
```

### 15.2 Hard delete all resources (preserve secret)

```bash
# Pods (immediate, no graceful shutdown)
oc delete pods --all -n aifeeders --force --grace-period=0

# Builds + BuildConfigs + ImageStreams
oc delete builds --all -n aifeeders
oc delete buildconfig --all -n aifeeders
oc delete imagestream --all -n aifeeders

# Workloads + networking
oc delete deployment --all -n aifeeders
oc delete service --all -n aifeeders
oc delete route --all -n aifeeders

# Schedulers + autoscaling + availability
oc delete cronjob --all -n aifeeders
oc delete hpa --all -n aifeeders
oc delete pdb --all -n aifeeders

# Config (NOT the secret — it holds real credentials)
oc delete configmap daily-news-config -n aifeeders
oc delete networkpolicy --all -n aifeeders
```

> **Why preserve the secret?** `daily-news-secrets` holds API keys for LinkedIn, GNews, LLM,
> and Langfuse. These are not stored in the repo. If you delete it you must re-enter all values.
> To check it still exists: `oc get secret daily-news-secrets -n aifeeders`

### 15.3 Re-apply config layer

```bash
oc apply -f openshift/namespace.yaml
oc apply -f openshift/configmap.yaml
oc apply -f openshift/rbac.yaml
oc apply -f openshift/networkpolicy.yaml
```

Expected output — all `created` or `unchanged`:
```
namespace/aifeeders configured
configmap/daily-news-config created
serviceaccount/daily-news unchanged
role.rbac.authorization.k8s.io/daily-news-role unchanged
rolebinding.rbac.authorization.k8s.io/daily-news-rolebinding unchanged
networkpolicy.networking.k8s.io/default-deny-all created
networkpolicy.networking.k8s.io/allow-api-to-mcps created
networkpolicy.networking.k8s.io/allow-router-to-api created
networkpolicy.networking.k8s.io/allow-router-to-linkedin-mcp created
networkpolicy.networking.k8s.io/allow-egress-internet created
```

### 15.4 Re-apply BuildConfigs + ImageStreams

```bash
oc apply -f openshift/buildconfigs.yaml
```

Expected — all 5 imagestreams + 5 buildconfigs `created`:
```
imagestream.image.openshift.io/daily-news created
imagestream.image.openshift.io/news-mcp created
imagestream.image.openshift.io/pageindex-mcp created
imagestream.image.openshift.io/evaluation-mcp created
imagestream.image.openshift.io/linkedin-mcp created
buildconfig.build.openshift.io/daily-news created
buildconfig.build.openshift.io/news-mcp created
buildconfig.build.openshift.io/pageindex-mcp created
buildconfig.build.openshift.io/evaluation-mcp created
buildconfig.build.openshift.io/linkedin-mcp created
```

### 15.5 Start all 5 builds from local source

The BuildConfigs use **binary strategy** — source is uploaded directly from your working directory.
Run builds sequentially (each upload takes ~30 s on a typical connection):

```bash
oc start-build daily-news     --from-dir=. --follow=false -n aifeeders
oc start-build news-mcp       --from-dir=. --follow=false -n aifeeders
oc start-build pageindex-mcp  --from-dir=. --follow=false -n aifeeders
oc start-build evaluation-mcp --from-dir=. --follow=false -n aifeeders
oc start-build linkedin-mcp   --from-dir=. --follow=false -n aifeeders
```

> **Why `--from-dir=.` for every build?** All five Dockerfiles (`Dockerfile`,
> `mcp_servers/*/Dockerfile`) reference paths relative to the repo root, so the full repo
> context must be uploaded each time. The `.dockerignore` keeps the tarball small.

### 15.6 Wait for all builds to complete

```bash
# Poll status — builds typically take 6–16 minutes each (pip install is the bottleneck)
oc get builds -n aifeeders

# Or block until all complete (adjust build names to match your run numbers)
oc wait build/daily-news-1 build/news-mcp-1 build/pageindex-mcp-1 \
         build/evaluation-mcp-1 build/linkedin-mcp-1 \
  --for=condition=Complete -n aifeeders --timeout=600s
```

**Build duration reference (observed on `api.your-cluster.example.com`):**

| Service | Typical duration |
|---|---|
| `daily-news` | ~15 min |
| `news-mcp` | ~12 min |
| `pageindex-mcp` | ~11 min |
| `evaluation-mcp` | ~6 min |
| `linkedin-mcp` | ~9 min |

**If a build fails with `Network is unreachable` during `pip install`:**
This is a transient build-node network blip — not a code issue. Simply re-run:
```bash
oc start-build pageindex-mcp --from-dir=. --follow=false -n aifeeders
# Wait for the new build number (e.g. pageindex-mcp-2)
oc wait build/pageindex-mcp-2 --for=condition=Complete -n aifeeders --timeout=600s
```

### 15.7 Apply deployments, services, routes

```bash
# MCPs first — API depends on them being available
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/
oc apply -f openshift/api/
```

### 15.8 Apply cronjob, HPA, PDB

```bash
oc apply -f openshift/cronjob.yaml   # daily-ai-news-morning (08:00 UTC) + afternoon (16:00 UTC)
oc apply -f openshift/hpa.yaml       # 2–10 replicas, CPU 70% / memory 80%
oc apply -f openshift/pdb.yaml       # min 1 always available for api, news-mcp, linkedin-mcp
```

### 15.9 Verify all deployments available

```bash
oc wait deployment/daily-news-api deployment/news-mcp deployment/pageindex-mcp \
         deployment/evaluation-mcp deployment/linkedin-mcp \
  --for=condition=Available -n aifeeders --timeout=120s

# Full status overview
oc get pods -n aifeeders
oc get routes -n aifeeders
oc get hpa -n aifeeders
oc get cronjobs -n aifeeders
```

Expected final pod state — all `1/1 Running`:
```
NAME                              READY   STATUS      RESTARTS   AGE
daily-news-api-<hash>-xxxxx       1/1     Running     0          Xm
daily-news-api-<hash>-xxxxx       1/1     Running     0          Xm
evaluation-mcp-<hash>-xxxxx       1/1     Running     0          Xm
evaluation-mcp-<hash>-xxxxx       1/1     Running     0          Xm
linkedin-mcp-<hash>-xxxxx         1/1     Running     0          Xm
news-mcp-<hash>-xxxxx             1/1     Running     0          Xm
news-mcp-<hash>-xxxxx             1/1     Running     0          Xm
pageindex-mcp-<hash>-xxxxx        1/1     Running     0          Xm
```

Build pods (`*-build`) in `Completed` state are normal and expected — ignore them.

### 15.10 One-liner full clean rebuild script

```bash
#!/usr/bin/env bash
# Usage: ./scripts/clean_rebuild_openshift.sh
# Requires: oc logged in to the correct project (aifeeders)
set -euo pipefail

NS=aifeeders

echo "==> Deleting all resources (preserving secret)..."
oc delete pods --all -n $NS --force --grace-period=0 2>/dev/null || true
oc delete builds buildconfig imagestream deployment service route \
   cronjob hpa pdb --all -n $NS 2>/dev/null || true
oc delete configmap daily-news-config -n $NS 2>/dev/null || true
oc delete networkpolicy --all -n $NS 2>/dev/null || true

echo "==> Re-applying config layer..."
oc apply -f openshift/namespace.yaml
oc apply -f openshift/configmap.yaml
oc apply -f openshift/rbac.yaml
oc apply -f openshift/networkpolicy.yaml

echo "==> Re-applying buildconfigs + imagestreams..."
oc apply -f openshift/buildconfigs.yaml

echo "==> Starting all 5 builds from local source..."
for svc in daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  oc start-build $svc --from-dir=. --follow=false -n $NS
done

echo "==> Waiting for builds to complete (up to 15 min each)..."
# Collect build names dynamically
BUILDS=$(oc get builds -n $NS --no-headers \
  | grep -v Error | awk '{print "build/" $1}' | tr '\n' ' ')
oc wait $BUILDS --for=condition=Complete -n $NS --timeout=900s

echo "==> Deploying workloads..."
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/
oc apply -f openshift/api/
oc apply -f openshift/cronjob.yaml
oc apply -f openshift/hpa.yaml
oc apply -f openshift/pdb.yaml

echo "==> Waiting for deployments to become Available..."
oc wait deployment/daily-news-api deployment/news-mcp deployment/pageindex-mcp \
         deployment/evaluation-mcp deployment/linkedin-mcp \
  --for=condition=Available -n $NS --timeout=120s

echo ""
echo "✅  Clean rebuild complete."
oc get pods -n $NS
oc get routes -n $NS
```

---

*AIFeeders Operational Runbook — from `podman build` to published LinkedIn post.*
