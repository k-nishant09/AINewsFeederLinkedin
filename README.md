# AIFeeders — Vectorless AI News Intelligence Pipeline

> **Build #83** · Cluster `aifeeders` · LLM `qwen2-5-72b-instruct` · 210 tests passing · Latest live post `urn:li:share:7509262700972101632`

---

## Table of Contents

1. [What It Does — The Story](#1-what-it-does--the-story)
2. [Build #83 — What Changed](#2-build-83--what-changed)
3. [7-Stage Pipeline](#3-7-stage-pipeline)
4. [Services — What Runs Where](#4-services--what-runs-where)
5. [Project Structure](#5-project-structure)
6. [Prerequisites](#6-prerequisites)
7. [Local Development — POC to Working Code](#7-local-development--poc-to-working-code)
8. [Docker — Understanding the Container Build](#8-docker--understanding-the-container-build)
9. [OpenShift Deployment — End-to-End](#9-openshift-deployment--end-to-end)
10. [EKS Deployment — Docker + ECR](#10-eks-deployment--docker--ecr)
11. [AKS Deployment — Docker + ACR](#11-aks-deployment--docker--acr)
12. [Build Flow → Launch Flow](#12-build-flow--launch-flow)
13. [Production Issues We Have Actually Hit](#13-production-issues-we-have-actually-hit)
14. [Networking — How Pods Talk](#14-networking--how-pods-talk)
15. [Scaling](#15-scaling)
16. [Monitoring and Observability](#16-monitoring-and-observability)
17. [Security](#17-security)
18. [Health Checks](#18-health-checks)
19. [Build History](#19-build-history)

---

## 1. What It Does — The Story

Most AI news pipelines today work like this: scrape headlines, embed them into a vector store, run a similarity search, hand the top result to an LLM, publish. It works, but it is fragile — vectors drift, embeddings are expensive, and the model usually outputs a dry summary nobody shares.

AIFeeders does something different. It has **no vector database**. Instead it builds a lightweight in-memory document tree (PageIndex) that reasons over structure and meaning without ever calling an embedding endpoint. Then it runs a proper editorial workflow: a journalist agent extracts the real story, four distinct character voices (Founder, Policy Analyst, Engineer, Generalist) each write a short story passage from their perspective, and the whole thing is assembled into a dialogue-delivery post format that reads like a conversation rather than a press release.

The result is published to LinkedIn twice a day — 08:00 UTC and 16:00 UTC — with human approval gating the final publish step.

**One-line pitch:** Discover → Reason → Analyse → Storytell → Four Voices → Proofread → Publish. No vector DB required.

---

## 2. Build #83 — What Changed

Build #83 is the dialogue-delivery build. Three things changed:

### 2.1 Dialogue-delivery post format

Previous builds wrote a monolithic post. Build #83 restructures the output as a hosted dialogue — a Media Person (host) introduces the story, asks four people a question, each gives a 3–5 sentence story passage, and the host closes with a second-order observation. The separator `────────────────────` is a visual cue for LinkedIn's mobile renderer.

This format was chosen because LinkedIn's algorithm rewards posts that people read all the way through. A character-voice dialogue is skimmable but also pulls readers forward. The monolithic format was not getting that engagement.

### 2.2 GrammarAgent (new)

After the four persona passages are composed, a new `GrammarAgent` makes a single LLM call at `temperature=0` to fix spelling, grammar, punctuation, and capitalisation. It does **not** rewrite — the instruction in `prompts/grammar.txt` is explicit: fix errors, preserve voice. If the grammar pass fails for any reason (timeout, LLM error), the pipeline continues and publishes the uncorrected post. It is best-effort and never blocks publish.

### 2.3 Persona prompts rewritten

The persona prompts in `prompts/capitalist.txt`, `prompts/policy.txt`, `prompts/genz.txt`, and `prompts/linkedin.txt` were rewritten to produce multi-sentence story passages rather than bullet points. Each persona now receives the full `NewsStory` object and writes as if narrating a scene, not summarising a fact.

---

## 3. 7-Stage Pipeline

| # | Stage | Component | What it does | Why it exists |
|---|-------|-----------|--------------|---------------|
| 1 | **DISCOVER** | GNews adapter (`news-mcp`) | Runs 9 queries over a 24-hour window, rotates between `GNEWS_API_KEY` and `GNEWS_API_KEY_2` if quota is exhausted | GNews has a daily request cap per key. Two-key rotation doubles capacity without paying for a higher tier. |
| 2 | **UNDERSTAND** | PageIndex (`pageindex-mcp`) | Builds an in-memory document tree from raw article text — no embeddings, no vector DB | Embedding every article is slow and costs money. PageIndex uses structural reasoning (headings, paragraphs, named entities) to represent documents. Each run gets its own in-memory tree; nothing persists between runs. |
| 3 | **ANALYZE** | Jev System One (`evaluation-mcp`) | Scores every article across 11 questions (novelty, significance, actionability, …), selects the single best article | We cannot publish all discovered articles. Jev provides a principled scoring rubric so the selection is reproducible and explainable, not just "highest cosine similarity". |
| 4 | **EXPLAIN** | `MediaStorytellerAgent` → `SummaryAgent` | Two-pass: first pass extracts a `NewsStory` (full narrative arc), second pass condenses to `NewsSummary` (key facts only) | The personas need the full story; the host needs the summary. One pass cannot serve both. |
| 5 | **ANGLE** | `jev.find_angle()` | Returns the missing narrative angle and the recommended audience for today's story | Without this step, all four personas write about the same aspect of the story. `find_angle` seeds each persona with a different lens. |
| 6 | **CREATE** | `PersonaAgentFactory` (4 parallel) → `GrammarAgent` → `PublisherAgent` | Four agents run in parallel (Founder, Policy Analyst, Engineer, Generalist), grammar pass runs after, `PublisherAgent._compose_main_post()` assembles the dialogue | Parallel execution saves ~12 seconds per run. Grammar runs last because rewriting voices mid-composition would lose the character divergence. |
| 7 | **PUBLISH** | `linkedin-mcp` | Posts to LinkedIn via the Posts API, gated by `PUBLISHING_ENABLED=true` and human approval | The `PUBLISHING_ENABLED` flag lets you run the full pipeline in dry-run mode locally or in staging without accidentally posting to your real LinkedIn profile. |

### LangGraph state machine

The pipeline is a 12-node LangGraph graph defined in `src/daily_news/workflows/daily_news_graph.py`. Nodes are: `discover` → `deduplicate` → `prefilter` → `understand` → `analyze` → `score_reach` → `explain` → `angle` → `create_personas` → `grammar` → `compose` → `publish`. Each node writes into a shared state dict; later nodes can inspect what earlier nodes produced.

---

## 4. Services — What Runs Where

| Service | Purpose | Replicas | Notes |
|---------|---------|----------|-------|
| `daily-news-api` | Main FastAPI app + LangGraph orchestrator | min 2, max 4 (HPA) | CPU-based autoscaling. Two replicas minimum so a rolling restart never takes the API down. |
| `news-mcp` | GNews adapter — wraps the GNews HTTP API and enforces 2-key rotation | 2 | Stateless. Both replicas share the same API keys via Secret. |
| `evaluation-mcp` | LLM evaluation fallback — runs Jev scoring locally if the upstream gateway is slow | 2 | Stateless. If Jev gateway times out, `daily-news-api` calls this instead. |
| `linkedin-mcp` | LinkedIn OAuth 2.0 + Posts API | **1** | **Stateful singleton.** The OAuth token is stored in memory. If this pod restarts, the token is gone and you must re-authorise. Do not scale this to 2 — you will get split-brain OAuth state. |
| `pageindex-mcp` | Vectorless document tree — builds the PageIndex per run | **1** | Per-run in-memory state. All tree data is discarded between runs. 1 replica is intentional — concurrent runs would stomp on each other's trees if scaled. |

### Why MCP servers?

Each MCP server exposes a well-defined tool interface over HTTP. The main `daily-news-api` calls them via the MCP protocol rather than importing the code directly. This means each service can be deployed, scaled, and restarted independently. It also means you can test `daily-news-api` locally by pointing it at real or mock MCP servers without changing any application code.

---

## 5. Project Structure

```
AINewsfeederLinkedin/
├── src/
│   └── daily_news/
│       ├── agents/
│       │   ├── publisher_agent.py      # _compose_main_post() — dialogue format (build #83)
│       │   ├── grammar_agent.py        # NEW in build #83 — proofread pass, temperature=0
│       │   ├── summary_agent.py        # MediaStorytellerAgent + SummaryAgent
│       │   └── persona_agent.py        # PersonaAgentFactory (4 parallel agents)
│       ├── workflows/
│       │   └── daily_news_graph.py     # LangGraph 12-node state machine
│       └── models/
│           └── intelligence.py         # NewsStory, NewsIntelligence Pydantic models
├── prompts/
│   ├── storyteller.txt                 # MediaStorytellerAgent system prompt
│   ├── capitalist.txt                  # Founder persona
│   ├── policy.txt                      # Policy Analyst persona
│   ├── genz.txt                        # Engineer persona (dual-lens: technical + workforce)
│   ├── linkedin.txt                    # Generalist persona (zero jargon)
│   ├── labor.txt                       # Working Professional (5th voice — not yet embedded)
│   └── grammar.txt                     # GrammarAgent instruction (fix, do not rewrite)
├── openshift/                          # All Kubernetes manifests
│   ├── deployments/
│   ├── services/
│   ├── configmaps/
│   ├── cronjobs/
│   ├── networkpolicy/
│   └── buildconfigs/
├── tests/                              # 210 passing tests
│   └── evaluation/                     # Excluded from default test run (slow, LLM-dependent)
├── Dockerfile
├── pyproject.toml
└── .env.example
```

**Why `labor.txt` exists but is not in the post:**

The 5th voice — a Working Professional's perspective on what the story means for people's jobs — is fully written and wired through `PersonaAgentFactory`. It is not embedded in the post body because LinkedIn does not allow programmatic comment posting without the **Community Management API** product approval on your LinkedIn app. Once that permission is granted, the plan is to post the Founder/Analyst/Engineer/Generalist dialogue in the main post and add the Working Professional as the first comment, extending the conversation rather than crowding the post.

---

## 6. Prerequisites

### Accounts and credentials

| What | Where to get it | Required for |
|------|----------------|--------------|
| GNews API key(s) | [gnews.io](https://gnews.io) — free tier gives 100 requests/day | Article discovery |
| LinkedIn app (Client ID + Secret) | [LinkedIn Developer Portal](https://www.linkedin.com/developers/) — needs `w_member_social` scope | Publishing |
| LLM API key | Your LLM gateway — this project uses `qwen2-5-72b-instruct` on an internal IBM Fusion cluster | All LLM calls |
| Jev API key | Jev System One API — set `JEV_ENABLED=false` to run without it | Article scoring |
| OpenShift login | Web console → Copy login command | OpenShift deploys only |
| AWS account + ECR | AWS console | EKS deploys only |
| Azure subscription + ACR | Azure portal | AKS deploys only |

### Tools

```bash
# Required everywhere
python --version          # 3.11+
curl --version            # for smoke tests

# Python package manager (this project uses uv, not pip)
curl -LsSf https://astral.sh/uv/install.sh | sh
# uv installs to ~/.local/bin/uv

# OpenShift
oc version               # install from https://mirror.openshift.com/pub/openshift-v4/clients/ocp/

# EKS
aws --version            # AWS CLI v2
kubectl version          # kubectl 1.27+
docker --version

# AKS
az --version             # Azure CLI
kubectl version
docker --version
```

---

## 7. Local Development — POC to Working Code

### 7.1 Clone and install

```bash
git clone <repo-url> AINewsfeederLinkedin
cd AINewsfeederLinkedin

# uv creates a .venv and installs all deps from pyproject.toml
/Users/kumar/.local/bin/uv sync
```

### 7.2 Configure environment

Copy the example file and fill in real values:

```bash
cp .env.example .env
```

Minimum required for a dry run (no LLM calls, no LinkedIn):

```
# .env — dry run only
GNEWS_API_KEY=your_gnews_key_here
GNEWS_API_KEY_2=your_second_gnews_key_here   # optional, but recommended

LLM_BASE_URL=https://model-gateway-model-gateway.apps.f73l056.fusion.tadn.ibm.com/v1
LLM_API_KEY=your_llm_api_key

JEV_ENABLED=false          # skip Jev scoring locally — uses heuristic fallback
PUBLISHING_ENABLED=false   # NEVER set true unless you intend to post to LinkedIn

NEWS_MCP_URL=http://localhost:8001
EVALUATION_MCP_URL=http://localhost:8002
LINKEDIN_MCP_URL=http://localhost:8003
PAGEINDEX_MCP_URL=http://localhost:8004
```

> **PUBLISHING_ENABLED is the safety gate.** When it is `false`, the pipeline runs all 7 stages, composes the full post, logs it to stdout, and stops — nothing is sent to LinkedIn. Always start with `false`.

### 7.3 Run tests

```bash
/Users/kumar/.local/bin/uv run pytest tests/ --ignore=tests/evaluation -q
```

Expected output: `210 passed`. The `tests/evaluation` directory is excluded because those tests make real LLM calls — they are slow and cost quota.

### 7.4 Dry run (no LinkedIn post)

With `PUBLISHING_ENABLED=false`, run the pipeline end-to-end:

```bash
/Users/kumar/.local/bin/uv run python -m daily_news.main
```

The pipeline will:
1. Query GNews for today's AI news
2. Run PageIndex on each article
3. Score with Jev (or heuristic if `JEV_ENABLED=false`)
4. Extract story and summary
5. Generate four persona passages
6. Run grammar pass
7. Compose and **print** the full post — then stop

Read the printed post carefully before enabling live publishing.

### 7.5 Live run (publishes to LinkedIn)

Only do this when you have read the dry-run output and are happy with it:

```bash
PUBLISHING_ENABLED=true /Users/kumar/.local/bin/uv run python -m daily_news.main
```

The pipeline will pause at the publish step and ask for human confirmation before calling the LinkedIn API.

---

## 8. Docker — Understanding the Container Build

### 8.1 Why rsync to a tmpdir before building

The `.venv/` directory created by `uv sync` is ~270 MB. If you pass the repo root directly to `docker build` or `oc start-build`, the build client uploads everything — including `.venv/` — to the daemon or the OpenShift build server. This causes build uploads to time out and wastes bandwidth on every build.

The fix is to rsync only what you need into a clean tmpdir and build from there:

```bash
TMPDIR=$(mktemp -d) && rsync -a \
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
  . "$TMPDIR/"
```

The tmpdir now contains only your source code — typically 2–5 MB. Build from `$TMPDIR`, not `.`.

### 8.2 Dockerfile walkthrough

```dockerfile
FROM python:3.11-slim

# Install uv into the image
# We pin uv rather than using "latest" so builds are reproducible
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency declaration files first
# This layer is cached as long as pyproject.toml doesn't change
COPY pyproject.toml uv.lock ./

# Install dependencies into the image's system Python
# --no-dev excludes test/dev dependencies (pytest, etc.)
RUN uv sync --frozen --no-dev

# Copy application source — this layer changes on every code change
COPY src/ ./src/
COPY prompts/ ./prompts/

# Run as non-root (security)
RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["uv", "run", "uvicorn", "daily_news.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Why `--frozen`?** `uv sync --frozen` refuses to update `uv.lock` during the build. This means the image always installs exactly the versions that were tested locally. Without `--frozen`, a build on a different day might silently pick up a newer (potentially breaking) package version.

---

## 9. OpenShift Deployment — End-to-End

This section walks through a first-time deployment to the `aifeeders` namespace on `api-f80l034-fusion-tadn.ibm.com:6443`. If you are re-deploying after a code change, skip to [9.7 Trigger a build](#97-trigger-a-build).

### 9.1 Log in

```bash
# Get a fresh token from the OpenShift web console:
# Top-right menu → "Copy login command" → paste here
oc login --token=<your-token> --server=https://api-f80l034-fusion-tadn.ibm.com:6443

# Verify
oc whoami
oc project aifeeders   # switch to the right namespace
```

> If `oc whoami` errors with "session token expired", your token has expired. Go back to the web console and copy a new one. Tokens are short-lived by default on Fusion clusters.

### 9.2 Apply ConfigMap (safe to commit)

ConfigMap values are not secret — they are configuration that varies by environment:

```bash
oc apply -f openshift/configmaps/daily-news-config.yaml -n aifeeders
```

Key values in the ConfigMap:

```yaml
data:
  PUBLISHING_ENABLED: "false"          # flip to "true" only in production
  JEV_ENABLED: "true"
  LLM_BASE_URL: "https://model-gateway-model-gateway.apps.f73l056.fusion.tadn.ibm.com/v1"
  NEWS_MCP_URL: "http://news-mcp-service:8080"
  EVALUATION_MCP_URL: "http://evaluation-mcp-service:8080"
  LINKEDIN_MCP_URL: "http://linkedin-mcp-service:8080"
  PAGEINDEX_MCP_URL: "http://pageindex-mcp-service:8080"
```

### 9.3 Create Secrets (never commit real values)

```bash
# Create the secret with real values — do not put these in a file
oc create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY=<your-llm-api-key> \
  --from-literal=GNEWS_API_KEY=<your-gnews-key-1> \
  --from-literal=GNEWS_API_KEY_2=<your-gnews-key-2> \
  --from-literal=JEV_API_KEY=<your-jev-api-key> \
  --from-literal=LINKEDIN_CLIENT_ID=<your-linkedin-client-id> \
  --from-literal=LINKEDIN_CLIENT_SECRET=<your-linkedin-client-secret> \
  -n aifeeders

# If the secret already exists, use replace:
oc create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY=<your-llm-api-key> \
  ... \
  -n aifeeders \
  --dry-run=client -o yaml | oc apply -f -
```

### 9.4 Apply RBAC

The CronJob pods need a ServiceAccount with permission to read ConfigMaps and Secrets:

```bash
oc apply -f openshift/rbac/ -n aifeeders
```

### 9.5 Apply NetworkPolicy

The default-deny-all policy is applied first, then explicit allow rules for each service:

```bash
oc apply -f openshift/networkpolicy/ -n aifeeders
```

> **Read section 14 before touching NetworkPolicy.** There is a known CronJob label bug that will silently break all outbound calls if you are not careful.

### 9.6 Apply BuildConfigs and create ImageStreams

```bash
oc apply -f openshift/buildconfigs/ -n aifeeders
oc apply -f openshift/imagestreams/ -n aifeeders
```

Limit build history to avoid accumulating stale images (important — old builds pile up and fill the registry):

```yaml
# In each BuildConfig spec:
spec:
  successfulBuildsHistoryLimit: 1
  failedBuildsHistoryLimit: 2
```

### 9.7 Trigger a build

```bash
# Prepare clean build context (always do this — never build from repo root)
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

# Start the build
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
```

`--follow` streams build logs to your terminal. Wait for `Push successful` before continuing.

### 9.8 Deploy

```bash
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders
# Wait for: "successfully rolled out"
```

Repeat for each MCP server that changed:

```bash
for svc in news-mcp evaluation-mcp linkedin-mcp pageindex-mcp; do
  oc rollout restart deployment/$svc -n aifeeders
  oc rollout status deployment/$svc -n aifeeders
done
```

### 9.9 LinkedIn authorisation

The `linkedin-mcp` pod holds the OAuth token in memory. After every restart, you must re-authorise:

```bash
# Port-forward the linkedin-mcp service to your laptop
oc port-forward svc/linkedin-mcp-service 8003:8080 -n aifeeders

# Open in your browser (do not use curl — OAuth needs a redirect):
# http://localhost:8003/auth/linkedin
```

Complete the OAuth flow in your browser. The token is stored in the pod's memory. The port-forward can be closed once authorisation is complete.

> **Do not restart `linkedin-mcp` unnecessarily.** Every restart requires re-authorisation. If you are deploying all services, deploy `linkedin-mcp` last and authorise once at the end.

### 9.10 Smoke test

```bash
# Check all pods are Running
oc get pods -n aifeeders

# Hit the health endpoint
oc port-forward svc/daily-news-api-service 8080:8080 -n aifeeders &
curl http://localhost:8080/health
# Expected: {"status":"ok"}

# Dry run via API (PUBLISHING_ENABLED must be false in ConfigMap)
curl -X POST http://localhost:8080/run
# Watch logs:
oc logs -f deployment/daily-news-api -n aifeeders
```

### 9.11 Enable CronJobs

The CronJobs run at 08:00 UTC and 16:00 UTC daily:

```bash
oc apply -f openshift/cronjobs/ -n aifeeders

# Verify schedule
oc get cronjobs -n aifeeders
```

To trigger a manual run right now:

```bash
oc create job --from=cronjob/daily-news-cronjob manual-run-$(date +%s) -n aifeeders
oc logs -f job/manual-run-<suffix> -n aifeeders
```

### 9.12 Enable live publishing

Only after all smoke tests pass:

```bash
oc patch configmap daily-news-config \
  --patch '{"data":{"PUBLISHING_ENABLED":"true"}}' \
  -n aifeeders

# Restart daily-news-api to pick up the new value
oc rollout restart deployment/daily-news-api -n aifeeders
```

---

## 10. EKS Deployment — Docker + ECR

EKS does not have OpenShift BuildConfigs — you build the image locally and push to ECR.

**Important:** EKS with the default VPC CNI plugin does **not** enforce `NetworkPolicy` resources. You must install [Calico](https://docs.projectcalico.org/getting-started/kubernetes/managed-public-cloud/eks) or [Cilium](https://docs.cilium.io/en/stable/gettingstarted/k8s-install-default/) separately before applying NetworkPolicy manifests, or your policies will be silently ignored.

```bash
# --- Variables (set these for your environment) ---
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
IMAGE=$ECR_REGISTRY/aifeeders/daily-news:latest

# --- Create ECR repository (once only) ---
aws ecr create-repository \
  --repository-name aifeeders/daily-news \
  --region $AWS_REGION

# --- Prepare build context ---
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

# --- Build and push ---
docker build -t $IMAGE "$TMPDIR"
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ECR_REGISTRY
docker push $IMAGE

# --- Update your EKS kubeconfig ---
aws eks update-kubeconfig --region $AWS_REGION --name <your-cluster-name>

# --- Apply manifests (same files as OpenShift, minus BuildConfigs) ---
kubectl apply -f openshift/configmaps/ -n aifeeders
kubectl apply -f openshift/rbac/ -n aifeeders
kubectl apply -f openshift/networkpolicy/ -n aifeeders   # requires Calico/Cilium!

# Create secrets (same as OpenShift section 9.3, using kubectl)
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY=<key> \
  --from-literal=GNEWS_API_KEY=<key> \
  --from-literal=GNEWS_API_KEY_2=<key> \
  --from-literal=JEV_API_KEY=<key> \
  --from-literal=LINKEDIN_CLIENT_ID=<id> \
  --from-literal=LINKEDIN_CLIENT_SECRET=<secret> \
  -n aifeeders

# Update image reference in deployments to $IMAGE, then:
kubectl apply -f openshift/deployments/ -n aifeeders
kubectl apply -f openshift/services/ -n aifeeders
kubectl apply -f openshift/cronjobs/ -n aifeeders

# --- Rolling restart after image push ---
kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders

# --- LinkedIn auth (same port-forward approach) ---
kubectl port-forward svc/linkedin-mcp-service 8003:8080 -n aifeeders
# Open http://localhost:8003/auth/linkedin in browser
```

---

## 11. AKS Deployment — Docker + ACR

AKS with **Azure CNI Overlay** enforces NetworkPolicy natively — no Calico/Cilium needed. Standard CNI (kubenet) does not enforce NetworkPolicy; check which CNI your cluster uses before applying network policies.

```bash
# --- Variables ---
AZ_ACR_NAME=<your-acr-name>
ACR_REGISTRY=$AZ_ACR_NAME.azurecr.io
IMAGE=$ACR_REGISTRY/aifeeders/daily-news:latest
RESOURCE_GROUP=<your-resource-group>
CLUSTER_NAME=<your-aks-cluster-name>

# --- Create ACR (once only) ---
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $AZ_ACR_NAME \
  --sku Basic

# --- Attach ACR to AKS (grants pull permission) ---
az aks update \
  --name $CLUSTER_NAME \
  --resource-group $RESOURCE_GROUP \
  --attach-acr $AZ_ACR_NAME

# --- Prepare build context ---
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

# --- Build and push ---
az acr login --name $AZ_ACR_NAME
docker build -t $IMAGE "$TMPDIR"
docker push $IMAGE

# --- Get AKS credentials ---
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME

# --- Apply manifests ---
kubectl apply -f openshift/configmaps/ -n aifeeders
kubectl apply -f openshift/rbac/ -n aifeeders
kubectl apply -f openshift/networkpolicy/ -n aifeeders   # works natively on Azure CNI Overlay

kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY=<key> \
  --from-literal=GNEWS_API_KEY=<key> \
  --from-literal=GNEWS_API_KEY_2=<key> \
  --from-literal=JEV_API_KEY=<key> \
  --from-literal=LINKEDIN_CLIENT_ID=<id> \
  --from-literal=LINKEDIN_CLIENT_SECRET=<secret> \
  -n aifeeders

kubectl apply -f openshift/deployments/ -n aifeeders
kubectl apply -f openshift/services/ -n aifeeders
kubectl apply -f openshift/cronjobs/ -n aifeeders

# --- Rolling restart ---
kubectl rollout restart deployment/daily-news-api -n aifeeders
kubectl rollout status deployment/daily-news-api -n aifeeders

# --- LinkedIn auth ---
kubectl port-forward svc/linkedin-mcp-service 8003:8080 -n aifeeders
# Open http://localhost:8003/auth/linkedin in browser
```

---

## 12. Build Flow → Launch Flow

### When to rebuild vs when to just restart

| Changed | Do this |
|---------|---------|
| Python source code, prompts | Full build (sections 9.7 → 9.8) |
| ConfigMap values only | `oc apply -f configmap.yaml` + `rollout restart` (no rebuild) |
| Secret values only | `oc create secret ... --dry-run | oc apply` + `rollout restart` (no rebuild) |
| Kubernetes manifests only (replicas, limits, etc.) | `oc apply -f ...` (Kubernetes updates in place; restart only if env vars changed) |

### Never-skip steps (all platforms)

These steps are easy to forget and will cause subtle failures:

1. **Always rsync to tmpdir before building.** Building from repo root uploads `.venv/` (270 MB) and times out.
2. **Always wait for `rollout status` to confirm `successfully rolled out`** before testing. Pods can be in `Running` state but still on the old image during a rollout.
3. **Always re-authorise `linkedin-mcp` after restarting that pod.** The token is in-memory only.
4. **Confirm `PUBLISHING_ENABLED=false` in staging** before flipping to `true` in production.
5. **On EKS, install Calico or Cilium before applying NetworkPolicy.** Without it, the policies apply but are silently not enforced — your pods will appear to be communicating when they should be blocked.

### Exact sequence for a code change on OpenShift

```bash
# 1. Run tests locally first
/Users/kumar/.local/bin/uv run pytest tests/ --ignore=tests/evaluation -q

# 2. Prepare build context
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

# 3. Build
oc start-build daily-news --from-dir="$TMPDIR" -n aifeeders --follow
# Wait for "Push successful"

# 4. Deploy
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders
# Wait for "successfully rolled out"

# 5. Smoke test
oc port-forward svc/daily-news-api-service 8080:8080 -n aifeeders &
curl http://localhost:8080/health

# 6. If this was a linkedin-mcp change, re-authorise
oc port-forward svc/linkedin-mcp-service 8003:8080 -n aifeeders
# Open http://localhost:8003/auth/linkedin
```

---

## 13. Production Issues We Have Actually Hit

These are real failures from running this pipeline in production. Each entry has: what you see, why it happens, and exactly how to fix it.

---

### Issue 1 — CronJob pods silently timeout on all outbound calls

**Symptom:** CronJob runs appear to start (pod is `Running`), but the pipeline never returns any articles. All MCP calls time out. The same code works fine when run as a manual `kubectl create job`.

**Root cause:** Kubernetes CronJob pod labels must be under `template.metadata.labels`. If you accidentally put them under `template.spec.labels` (which is not a valid field), Kubernetes silently ignores the key. The CronJob pod then has no `app` label, so the NetworkPolicy `podSelector` does not match it, and all outbound traffic is denied by the default-deny-all policy.

**Fix:**

```yaml
# WRONG — spec.labels does not exist, silently ignored
spec:
  jobTemplate:
    spec:
      template:
        spec:
          labels:                      # ← WRONG PLACE
            app: daily-news-worker

# CORRECT — labels go under template.metadata
spec:
  jobTemplate:
    spec:
      template:
        metadata:
          labels:                      # ← CORRECT
            app: daily-news-worker
        spec:
          containers: ...
```

---

### Issue 2 — Build upload times out

**Symptom:** `oc start-build --from-dir=.` hangs or errors with a timeout before the build even starts.

**Root cause:** You are building from the repo root. The `.venv/` directory is ~270 MB. The build client is uploading your entire working directory to the OpenShift build server.

**Fix:** Always rsync to a tmpdir first (see section 9.7). The clean tmpdir is 2–5 MB and uploads in seconds.

---

### Issue 3 — GNews quota exhausted (HTTP 403)

**Symptom:** `news-mcp` logs show `403 Forbidden` from the GNews API. Pipeline returns zero articles.

**Root cause:** GNews free tier gives 100 requests per day per API key. The pipeline runs 9 queries per execution, twice a day — 18 requests per day. However, if you have been doing development runs locally, you may have used up the quota before the production CronJob runs.

**Fix:** The pipeline automatically rotates to `GNEWS_API_KEY_2` on a 403. If both keys are exhausted, the pipeline will return zero articles and exit cleanly (it does not error). Check your GNews dashboard to see when the quota resets (midnight UTC).

---

### Issue 4 — LinkedIn token expired (HTTP 401)

**Symptom:** `linkedin-mcp` logs show `401 Unauthorized` when attempting to post.

**Root cause:** LinkedIn OAuth access tokens expire. The expiry is visible in the token response — typically 60 days, but can vary.

**Fix:** Re-authorise:

```bash
oc port-forward svc/linkedin-mcp-service 8003:8080 -n aifeeders
# Open http://localhost:8003/auth/linkedin in browser
# Complete the OAuth flow
```

---

### Issue 5 — `linkedin-mcp` pod restart wipes OAuth token

**Symptom:** After deploying a new version of `linkedin-mcp`, or after the pod restarts due to an OOM kill or node eviction, posts fail with `401 Unauthorized` even though the token was recently set.

**Root cause:** The OAuth token is stored in the `linkedin-mcp` process memory, not in a database or a Kubernetes Secret. Pod restart = memory wipe = token gone.

**Fix:** Re-authorise after every `linkedin-mcp` restart (see Issue 4 fix). Long-term, the token should be persisted to a Kubernetes Secret or a small key-value store. This is a known architectural limitation.

---

### Issue 6 — Post truncated mid-sentence

**Symptom:** The LinkedIn post appears cut off. The last visible character is in the middle of a word or sentence.

**Root cause:** LinkedIn counts post length in **UTF-16 code units**, not Unicode code points. Standard Python `len()` counts code points. Emoji characters are outside the Basic Multilingual Plane and cost **2 UTF-16 units each**, not 1. A post full of emoji can appear to be 2800 characters but register as 3200 UTF-16 units, which exceeds LinkedIn's 3000-unit limit.

**Fix:** The pipeline now uses a `_linkedin_len()` helper in `publisher_agent.py` that counts UTF-16 units correctly:

```python
def _linkedin_len(text: str) -> int:
    """Count UTF-16 code units, matching LinkedIn's length check."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)
```

---

### Issue 7 — Comments API PERMISSION_ERROR

**Symptom:** Attempting to post to LinkedIn comments (for the `labor.txt` Working Professional voice) returns a permission error from the LinkedIn API.

**Root cause:** LinkedIn's Comments API requires the **Community Management API** product to be enabled on your LinkedIn Developer app. This product requires a separate approval process and is not available on all developer accounts.

**Current state:** The Working Professional persona is fully written in `prompts/labor.txt` and wired through `PersonaAgentFactory`. It is intentionally not embedded in the main post body. Publishing it as a first comment is blocked until the Community Management API permission is granted.

**Fix:** Apply for Community Management API access on your LinkedIn Developer app. Once granted, wire `labor.txt` to the Comments API in `linkedin-mcp`.

---

### Issue 8 — Jev gateway timeout (ReadTimeout)

**Symptom:** Pipeline logs show `ReadTimeout` when calling the Jev System One endpoint. The article scoring step fails.

**Root cause:** The Jev gateway at `https://model-gateway-model-gateway.apps.f73l056.fusion.tadn.ibm.com` sometimes takes longer than the configured HTTP timeout to respond, especially under load.

**Fix:** The pipeline automatically falls back to heuristic scoring when Jev times out — it does not fail the run. You will see `[JEV FALLBACK] using heuristic scoring` in the logs. This is expected behaviour. If Jev timeouts are frequent, check the gateway health and consider increasing the timeout in `evaluation-mcp`.

---

### Issue 9 — Old builds accumulate and fill the registry

**Symptom:** `oc get builds -n aifeeders` shows dozens of old builds. The internal registry starts returning storage errors.

**Root cause:** OpenShift BuildConfigs keep all historical builds by default. After 80+ builds, this adds up.

**Fix:** Set `successfulBuildsHistoryLimit` and `failedBuildsHistoryLimit` in every BuildConfig:

```yaml
spec:
  successfulBuildsHistoryLimit: 1
  failedBuildsHistoryLimit: 2
```

Apply this to all existing BuildConfigs:

```bash
for bc in $(oc get bc -n aifeeders -o name); do
  oc patch $bc -n aifeeders \
    --patch '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":2}}'
done
```

---

### Issue 10 — `oc whoami` error / session token expired

**Symptom:** `oc` commands fail with `Error from server (Unauthorized)` or `oc whoami` returns an error.

**Root cause:** OpenShift session tokens are short-lived. After expiry, all `oc` commands will fail until you re-authenticate.

**Fix:** Go to the OpenShift web console → top-right menu → **Copy login command** → paste the full `oc login --token=...` command in your terminal. There is no way to refresh the token from the CLI — you must go through the web console.

---

## 14. Networking — How Pods Talk

### NetworkPolicy model

All pods in the `aifeeders` namespace operate under a **default-deny-all** NetworkPolicy. This means:

- By default, a new pod cannot make any outbound calls and cannot receive any inbound calls.
- You must explicitly add allow rules for every communication path.

This is the right default for production — it forces you to be explicit about what talks to what, and prevents a compromised pod from reaching arbitrary internal services.

The allow rules in `openshift/networkpolicy/` cover:

| From | To | Port |
|------|-----|------|
| `daily-news-api` | `news-mcp` | 8080 |
| `daily-news-api` | `evaluation-mcp` | 8080 |
| `daily-news-api` | `linkedin-mcp` | 8080 |
| `daily-news-api` | `pageindex-mcp` | 8080 |
| `daily-news-worker` (CronJob) | all MCP services | 8080 |
| All pods | LLM gateway (egress) | 443 |
| All pods | GNews API (egress) | 443 |

### Pod DNS

Within the cluster, services are reachable at `<service-name>.<namespace>.svc.cluster.local`. The ConfigMap uses short-form DNS (`http://news-mcp-service:8080`) which works within the same namespace. If you ever move a service to a different namespace, you will need the fully qualified name.

### CronJob label bug (critical)

This is documented in Issue 1, but worth repeating here because it is a NetworkPolicy footgun:

**The `app` label on a CronJob pod must be under `template.metadata.labels`, not `template.spec.labels`.** If placed under `spec`, Kubernetes accepts the YAML without error, silently ignores the labels, and the pod starts with no matching `app` label. The NetworkPolicy `podSelector` for `app: daily-news-worker` will not match the pod. All outbound traffic will be silently dropped by the default-deny-all policy.

### EKS NetworkPolicy caveat

The default EKS CNI plugin (AWS VPC CNI) **does not enforce `NetworkPolicy` resources**. The policies will be created and applied without error, but they will have no effect. You must install Calico or Cilium to enforce them. This is a well-known EKS limitation.

### AKS NetworkPolicy

Azure CNI Overlay (the default for newer AKS clusters) enforces NetworkPolicy natively. If your AKS cluster uses the older kubenet CNI, you will have the same problem as EKS.

---

## 15. Scaling

### What scales automatically

- `daily-news-api`: HPA with `minReplicas: 2`, `maxReplicas: 4`, CPU-based. The FastAPI app is stateless — each request can be handled by any replica.

### What does not scale

- `linkedin-mcp`: **must remain at 1 replica**. The OAuth token is in-memory. If you run 2 replicas, each will hold a different token state (or no token), and LinkedIn API calls from the replica that did not handle the `/auth/linkedin` callback will fail with 401.
- `pageindex-mcp`: **must remain at 1 replica per run**. The document tree is built in memory during a run. If you scale to 2, the two replicas will have different trees and produce inconsistent results.

### Resource limits

```
daily-news-api:
  requests: { memory: 256Mi, cpu: 500m }
  limits:    { memory: 2Gi,  cpu: 2    }

news-mcp, evaluation-mcp, linkedin-mcp, pageindex-mcp:
  requests: { memory: 128Mi, cpu: 100m  }
  limits:   { memory: 256Mi, cpu: 500m  }
```

The `daily-news-api` memory limit is intentionally generous (2Gi) because LangGraph state machines accumulate node outputs in memory across the 12-node graph. If you reduce this below 512Mi you may see OOMKilled pods during long runs.

### PodDisruptionBudget

A PDB ensures at least 1 `daily-news-api` replica is always available during node maintenance:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: daily-news-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: daily-news-api
```

### CronJob concurrency

All CronJobs have `concurrencyPolicy: Forbid`. This means if the 08:00 UTC run is still in progress at 16:00 UTC, the 16:00 run is skipped rather than starting a second overlapping run. This prevents the pipeline from posting duplicate articles and avoids Jev API rate-limit issues from two concurrent scoring runs.

---

## 16. Monitoring and Observability

### Log patterns to watch

```bash
# Follow live logs
oc logs -f deployment/daily-news-api -n aifeeders

# Key log lines and what they mean:
[DISCOVER] Found 47 articles across 9 queries       # normal
[DISCOVER] GNews key 1 exhausted, rotating to key 2  # expected if quota is hit
[UNDERSTAND] PageIndex built: 47 nodes               # normal
[ANALYZE] Jev selected article_id=abc123 (score=8.7) # normal
[ANALYZE][JEV FALLBACK] using heuristic scoring      # Jev gateway timed out — ok
[CREATE] Running 4 persona agents in parallel         # normal
[GRAMMAR] pass complete — 3 corrections              # normal
[GRAMMAR] pass failed — publishing uncorrected       # grammar agent error, non-blocking
[PUBLISH] PUBLISHING_ENABLED=false — dry run only    # expected in staging
[PUBLISH] Posted urn:li:share:xxxx                   # successful live post
```

### Health checks

```bash
# All services expose /health
curl http://localhost:8080/health   # daily-news-api
# Expected: {"status":"ok","build":"daily-news-6"}
```

### LinkedIn audit log

All published post URNs are logged at the `[PUBLISH]` level. The latest live run URN is `urn:li:share:7509262700972101632`. Keep a record of these — you will need the URN if you need to delete a post via the LinkedIn API.

### Langfuse

If you have a Langfuse instance, set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in the ConfigMap. The LangGraph graph is instrumented with Langfuse tracing — every LLM call is logged with input, output, latency, and token count. This is the primary way to debug why a persona passage reads oddly or why Jev selected a particular article.

---

## 17. Security

### Secrets management

Never commit real secret values to the repository. The separation is:

| Type | File | Contains |
|------|------|---------|
| ConfigMap | `openshift/configmaps/daily-news-config.yaml` | `PUBLISHING_ENABLED`, `JEV_ENABLED`, `LLM_BASE_URL`, all service URLs — safe to commit |
| Secret | Created via `oc create secret` — never in files | `LLM_API_KEY`, `GNEWS_API_KEY`, `GNEWS_API_KEY_2`, `JEV_API_KEY`, `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` — never commit |

If you accidentally commit a secret, rotate it immediately (generate a new key at the provider) before treating the old key as revoked.

### NetworkPolicy

The default-deny-all NetworkPolicy limits blast radius if a pod is compromised. A compromised `pageindex-mcp` pod cannot reach the LinkedIn API directly — it can only receive calls from `daily-news-api` on port 8080.

### Content safety gates

The pipeline does not publish automatically. Two gates are in place:

1. **`PUBLISHING_ENABLED` flag** — must be explicitly set to `true`. Defaults to `false` in ConfigMap.
2. **Human approval step** — `PublisherAgent` prints the composed post and waits for confirmation before calling `linkedin-mcp`. This step is present even when `PUBLISHING_ENABLED=true`.

### OAuth

LinkedIn OAuth tokens are short-lived and stored in `linkedin-mcp` memory only. There is no persistent token store. This is a deliberate trade-off: storing tokens in a Kubernetes Secret would be more resilient but requires careful RBAC to ensure the Secret is not readable by other pods. The current approach means a pod restart requires re-authorisation — inconvenient but safe.

---

## 18. Health Checks

All deployments use the same probe pattern:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 30    # give uv/Python time to start
  periodSeconds: 10
  failureThreshold: 3        # restart after 3 consecutive failures

readinessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 15
  periodSeconds: 5
  failureThreshold: 3        # remove from load balancer after 3 failures
```

**Why different initial delays?** Liveness starts later (30s) because if the pod is killed too early during startup, Kubernetes enters a restart loop. Readiness starts sooner (15s) to begin accepting traffic as quickly as possible once the app is actually ready.

The `/health` endpoint returns HTTP 200 with `{"status":"ok","build":"<current-build-name>"}`. The `build` field is useful for confirming that a rollout has completed and the new code is serving.

---

## 19. Build History

| Build | Key changes |
|-------|------------|
| **#83** (current) | Dialogue-delivery post format (`_compose_main_post()`), GrammarAgent (new, `temperature=0`), persona prompts rewritten for multi-sentence story passages |
| #82 | `NewsStory` model, `MediaStorytellerAgent`, two-pass summarise (story → summary), story-driven post format replacing bullet-point format |
| #80–81 | Jaccard deduplication for discovered articles, per-article `jev_prefilter_scores` keyed by `article_id`, `score_reach` graph node |
| #61 | UTF-16 length fix (`_linkedin_len()`), MCP nested error detection, `jev_prefilter_scores` refactored to use `article_id` as key |
| <#61 | NewsDataIO removed; GNews became the sole article discovery provider |

---

## Contributing

1. Branch from `main`.
2. Run `uv sync` to get a fresh environment.
3. Make your change.
4. Run `uv run pytest tests/ --ignore=tests/evaluation -q` — all 210 tests must pass.
5. Do a dry run (`PUBLISHING_ENABLED=false`) and read the output.
6. Open a pull request.

Do not commit `.env`, `.venv/`, or any file containing real API keys.

---

*Current build: `daily-news-6` · LLM: `qwen2-5-72b-instruct` · Latest post: `urn:li:share:7509262700972101632`*
