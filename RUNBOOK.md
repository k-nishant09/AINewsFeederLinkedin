# AIFeeders Operational Runbook & Step-by-Step Production Guide

> **Enterprise Production Runbook** — Closed-Loop Enterprise Intelligence · Epistemological Judgment · Dynamic Live Roundtable · Multi-Cloud (OpenShift, EKS, AKS)  
> **Last verified:** Live Run `RUN-0C3B37F29E22` on OpenShift · 223 tests passing · Published `urn:li:share:7509289467577315328`

---

## Table of Contents

1. [System Overview & Execution Model](#1-system-overview--execution-model)
2. [Why Each Architectural Component is Used (FAQ & Deep-Dive Rationale)](#2-why-each-architectural-component-is-used-faq--deep-dive-rationale)
   - [Why Jev (System One AI Editorial Gateway)?](#why-jev-system-one-ai-editorial-gateway)
   - [Why Epistemological Judgment Analysis (JudgmentAgent)?](#why-epistemological-judgment-analysis-judgmentagent)
   - [Why Vectorless PageIndex Document Trees?](#why-vectorless-pageindex-document-trees)
   - [Why Dual Guardrails (Input & Output)?](#why-dual-guardrails-input--output)
   - [Why Multi-Persona Live Roundtable Debate?](#why-multi-persona-live-roundtable-debate)
   - [Why Deterministic Evaluation Gates & Back-Edge Retries?](#why-deterministic-evaluation-gates--back-edge-retries)
   - [Why ReachScoreAgent & Auto-Repair?](#why-reachscoreagent--auto-repair)
   - [Why Langfuse v4 Tracing & Observability?](#why-langfuse-v4-tracing--observability)
   - [Why ContentOptimizerAgent & Story Mutation Loops?](#why-contentoptimizeragent--story-mutation-loops)
3. [End-to-End Execution Flow (Step-by-Step Operator Guide)](#3-end-to-end-execution-flow-step-by-step-operator-guide)
4. [First-Time Cluster Setup (OpenShift, AWS EKS, Azure AKS)](#4-first-time-cluster-setup-openshift-aws-eks-azure-aks)
5. [Docker Container Build & Deployment Automation](#5-docker-container-build--deployment-automation)
6. [Daily Operations: Health Checks & Validation Checklist](#6-daily-operations-health-checks--validation-checklist)
7. [How to Trigger Manual Runs & Local Testing](#7-how-to-trigger-manual-runs--local-testing)
8. [Log Interpretation & Stage Trace Reference](#8-log-interpretation--stage-trace-reference)
9. [Production Incident Triage & Troubleshooting](#9-production-incident-triage--troubleshooting)
10. [Secret Management, API Key Rotation & Quotas](#10-secret-management-api-key-rotation--quotas)
11. [Enterprise Security & Network Isolation](#11-enterprise-security--network-isolation)

---

## 1. System Overview & Execution Model

AIFeeders is an autonomous, closed-loop media intelligence system engineered to transform raw, noisy AI technology announcements into high-signal, broadcast-quality editorial debate programs published to LinkedIn.

### Scheduled Execution
The pipeline runs automatically via Kubernetes CronJob at:
- **08:00 UTC** (Morning Asia/Europe edition)
- **16:00 UTC** (Morning US / Evening Europe edition)

---

## 2. Why Each Architectural Component is Used (FAQ & Deep-Dive Rationale)

### Why Jev (System One AI Editorial Gateway)?
- **Problem**: Standard LLMs are slow, costly, and lack calibrated journalistic instinct. When asked to evaluate 20 raw news stories, an LLM defaults to polite, obvious summaries of corporate press releases.
- **Why Jev**: Jev operates as an ultra-fast (70–500ms) System One editorial gateway. It evaluates multi-dimensional signals (novelty, trend velocity, emotional polarity, enterprise vs developer impact) to select the single highest-value story, determines the "Missing Angle" that mainstream media missed, and dynamically routes only the most relevant personas.
- **Fallback**: If Jev is disabled (`JEV_ENABLED=false`) or unreachable, the system gracefully falls back to deterministic heuristic ranking and Evaluation MCP without pipeline disruption.

### Why Epistemological Judgment Analysis (JudgmentAgent)?
- **Problem**: Large Language Models suffer from epistemic collapse—they treat unproven marketing hype ("Our chip is 10x faster") identically to verified empirical data.
- **Why JudgmentAgent**: Before storytelling begins, `JudgmentAgent` runs at low temperature (`0.2`) to partition the article into strict categories:
  1. `facts`: Verified launches, benchmark figures, confirmed dates.
  2. `reported_claims`: Statements and promises made by corporate actors.
  3. `analysis_implications`: Grounded technical/economic deductions.
  4. `uncertainties`: Pending benchmarks, unknown pricing, regulatory risks.
  5. `what_not_to_conclude`: Hard boundaries explicitly barring downstream models from fabricating certainty or attributing unproven claims as established facts.

### Why Vectorless PageIndex Document Trees?
- **Problem**: Traditional vector databases (RAG) suffer from semantic drift, token chunk truncation, cosine distance hallucinations, and external database infrastructure costs when handling fresh articles.
- **Why PageIndex**: Parses documents into an in-memory hierarchical structure (`Document → Sections → Headings → Evidence Items`). Section retrieval queries traverse the tree deterministically, guaranteeing 100% reproducible evidence extraction with zero vector database operational overhead.

### Why Dual Guardrails (Input & Output)?
- **Problem**: Public news feeds can contain adversarial prompt injection strings, and LLMs can hallucinate fake quotations attributed to real living individuals.
- **Why InputGuardrail**: Inspects raw incoming articles for prompt injections (`ignore previous instructions`, `<system>`, `bypass all filters`), scans for accidental PII (SSNs, credit card numbers), and cleans malformed control characters.
- **Why OutputGuardrail**: Inspects generated content before LinkedIn dispatch, flagging fake direct quotes not verified in the PageIndex evidence tree and enforcing persona disclaimers.

### Why Multi-Persona Live Roundtable Debate?
- **Problem**: Monolithic bullet-point summaries are boring and drive poor engagement on professional platforms.
- **Why Roundtable**: Models the story as a broadcast panel discussion featuring distinct industry archetypes:
  - **💼 Founder**: Evaluates unit economics, customer acquisition, and platform lock-in.
  - **🏛️ Policy Analyst**: Evaluates EU AI Act compliance, liability, and copyright.
  - **🧠 Engineer**: Evaluates architectural trade-offs, latency, observability, and integration debt.
  - **🎓 Generalist**: Evaluates workplace impact, usability, and workforce transitions.
  - **Host Opening & Synthesis**: Delivers an engaging human analogy and poses an open dilemma that sparks practitioner debate.

### Why Deterministic Evaluation Gates & Back-Edge Retries?
- **Problem**: LLMs cannot reliably self-govern their own factuality when given free rein.
- **Why Deterministic Gates**: While Jev or MCP supplies the numerical evaluation floats, deterministic Python code enforces hard threshold gates:
  - `PASS`: Factuality $\ge 0.75$, Groundedness $\ge 0.70$, Hallucination $\le 0.15$.
  - `REGENERATE`: Triggers a LangGraph back-edge (`evaluate ──► summarize`), passing failure reasons to retry generation up to `MAX_RETRIES=2`.
  - `BLOCK`: Unrecoverable policy or safety violation halts publication.

### Why ReachScoreAgent & Auto-Repair?
- **Problem**: Factually accurate posts may still fail to reach an audience if they suffer from poor structure, weak hooks, excessive length, or clickbait penalties.
- **Why Reach Scoring**: Evaluates the post across 6 dimensions (Hook Strength, Specificity Score, Question Quality, Length Fit, Clickbait Penalty, Topic Coherence). If the composite score is under 70, `ReachScoreAgent` executes surgical prompt auto-repairs prior to publication.

### Why Langfuse v4 Tracing & Observability?
- **Problem**: Multi-agent pipelines with dynamic routing and retries are impossible to monitor via standard terminal logs.
- **Why Langfuse**: Instruments every LLM call, tool execution, eval gate, and guardrail check with a unified `run_id`. Provides real-time visibility into token costs, latency bottlenecks, and error traces.

### Why ContentOptimizerAgent & Story Mutation Loops?
- **Problem**: Traditional bots publish blindly without learning from real-world performance.
- **Why ContentOptimizer**: Post-publication, it pulls real LinkedIn engagement metrics (reactions, reposts, comments), runs a structural diagnosis, and outputs **Story Mutations** that calibrate future Jev prompt recommendations.

---

## 3. End-to-End Execution Flow (Step-by-Step Operator Guide)

When a run is triggered, the LangGraph orchestrator executes the following 13 steps sequentially:

1. **`discover_news`**: Executes 9 targeted GNews search queries across AI domains (chips, LLMs, enterprise tools, policy, funding).
2. **`deduplicate`**: Performs 3-pass deduplication:
   - Pass 1: Canonical URL hash lookup against SQLite `PublishedStore`.
   - Pass 2: Exact normalized title matching.
   - Pass 3: Jaccard token overlap similarity ($\ge 0.85$).
3. **`input_guardrail`**: Scans surviving articles for prompt injection signatures and PII.
4. **`fetch_articles`**: Scrapes full article body text and metadata.
5. **`index_pageindex`**: Ingests articles into in-memory hierarchical PageIndex document trees.
6. **`jev_prefilter`**: Scores candidate articles on 7 multi-dimensional signals and selects the top-1 story.
7. **`summarize`**:
   - Step A: `JudgmentAgent` extracts facts, claims, uncertainties, and what NOT to conclude.
   - Step B: `MediaStorytellerAgent` designs the narrative hook, human analogy, and panel bridges.
   - Step C: `SummaryAgent` builds the structured `NewsSummary`.
8. **`find_angle`**: Jev identifies the "Missing Angle" and target audience.
9. **`jev_router`**: Jev selects the active panelist personas for this specific topic.
10. **`generate_personas`**: Executes selected personas in parallel to generate authentic roundtable commentary.
11. **`evaluate`**: Evaluates factuality and groundedness. If thresholds fail, LangGraph loops back to step 7.
12. **`score_reach`**: Measures organic reach potential (0–100) and executes auto-repairs if needed.
13. **`publish` & `optimize_content`**: Output guardrails inspect text $\rightarrow$ PublisherAgent extracts dynamic SEO/AEO hashtags $\rightarrow$ LinkedIn API creates post $\rightarrow$ ContentOptimizer logs structural mutations.

---

## 4. First-Time Cluster Setup (OpenShift, AWS EKS, Azure AKS)

### Step 1: Create Namespace / Project
```bash
# OpenShift
oc new-project aifeeders

# AWS EKS / Azure AKS
kubectl create namespace aifeeders
kubectl config set-context --current --namespace=aifeeders
```

### Step 2: Configure Secrets
```bash
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="your-llm-api-key" \
  --from-literal=GNEWS_API_KEY="your-primary-gnews-key" \
  --from-literal=GNEWS_API_KEY_2="your-backup-gnews-key" \
  --from-literal=JEV_API_KEY="your-jev-api-key" \
  --from-literal=LINKEDIN_CLIENT_ID="your-linkedin-client-id" \
  --from-literal=LINKEDIN_CLIENT_SECRET="your-linkedin-client-secret" \
  --from-literal=LANGFUSE_PUBLIC_KEY="pk-lf-..." \
  --from-literal=LANGFUSE_SECRET_KEY="sk-lf-..." \
  -n aifeeders
```

### Step 3: Deploy Microservices & API
```bash
kubectl apply -f openshift/configmap.yaml -n aifeeders
kubectl apply -f openshift/news-mcp.yaml -n aifeeders
kubectl apply -f openshift/pageindex-mcp.yaml -n aifeeders
kubectl apply -f openshift/evaluation-mcp.yaml -n aifeeders
kubectl apply -f openshift/linkedin-mcp.yaml -n aifeeders
kubectl apply -f openshift/daily-news-api.yaml -n aifeeders
kubectl apply -f openshift/cronjob.yaml -n aifeeders
```

---

## 5. Docker Container Build & Deployment Automation

### 5.1 Local Clean Docker Build
```bash
TMPDIR=$(mktemp -d) && rsync -a \
  --exclude='.venv/' --exclude='**/__pycache__/' --exclude='**/*.pyc' \
  --exclude='.git/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='.env' --exclude='.env.*' --exclude='dist/' --exclude='build/' \
  . "$TMPDIR/"

docker build -t aifeeders/daily-news:latest "$TMPDIR"
```

### 5.2 Push & Deploy to Red Hat OpenShift
```bash
oc project aifeeders
oc start-build daily-news --from-dir=. --follow
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders
```

### 5.3 Push & Deploy to AWS EKS (ECR)
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker tag aifeeders/daily-news:latest <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/aifeeders:latest
docker push <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/aifeeders:latest
kubectl rollout restart deployment/daily-news-api -n aifeeders
```

### 5.4 Push & Deploy to Azure AKS (ACR)
```bash
az acr login --name aifeedersregistry
docker tag aifeeders/daily-news:latest aifeedersregistry.azurecr.io/daily-news:latest
docker push aifeedersregistry.azurecr.io/daily-news:latest
kubectl rollout restart deployment/daily-news-api -n aifeeders
```

---

## 6. Daily Operations: Health Checks & Validation Checklist

Execute these verification checks to confirm cluster health:

```bash
# 1. Verify Pod Status (All pods should be 'Running' with 0 restarts)
kubectl get pods -n aifeeders -o wide

# 2. Check Service Endpoints
kubectl get svc -n aifeeders

# 3. Test API Health & MCP connectivity
kubectl exec deployment/daily-news-api -n aifeeders -- curl -s http://localhost:8080/health

# 4. Inspect latest CronJob execution
kubectl get cronjob -n aifeeders
kubectl get jobs -n aifeeders --sort-by='.metadata.creationTimestamp' | tail -n 5
```

---

## 7. How to Trigger Manual Runs & Local Testing

### Option A: Trigger Workflow via REST API Inside Cluster
```bash
kubectl exec deployment/daily-news-api -n aifeeders -- curl -s -X POST http://localhost:8080/workflow/run
```

### Option B: Trigger Workflow via CLI Runner
```bash
kubectl exec -it deployment/daily-news-api -n aifeeders -- python -m daily_news.workflow_runner
```

### Option C: Run Full Local Test Suite
```bash
uv run pytest tests/ -v
```

---

## 8. Log Interpretation & Stage Trace Reference

Each production run logs with a unique trace ID `[RUN-XXXXXXXX]`. Follow these log patterns:

```text
[RUN-0C3B37F29E22] discover_news: fetched 18 raw articles across 9 queries
[RUN-0C3B37F29E22] deduplicate: 18 -> 12 articles (3 filtered by store, 3 by Jaccard)
[RUN-0C3B37F29E22] input_guardrail: all 12 articles passed prompt injection/PII scan
[RUN-0C3B37F29E22] index_pageindex: 12 articles indexed into in-memory trees
[RUN-0C3B37F29E22] jev_prefilter: top article selected (composite_score=0.89, novelty=0.92)
[RUN-0C3B37F29E22] judgment_analysis: facts=4, claims=3, uncertainties=2, what_not_to_conclude=3
[RUN-0C3B37F29E22] story extracted: style=INVESTIGATIVE, hook_len=142
[RUN-0C3B37F29E22] jev_find_angle: missing_angle identified, target_audience=ENGINEERING_LEADS
[RUN-0C3B37F29E22] jev_router: routed active personas -> ['business', 'developer', 'policy']
[RUN-0C3B37F29E22] generate_personas: 3 personas generated in parallel (duration=1.4s)
[RUN-0C3B37F29E22] evaluate: decision=PASS (factuality=0.88, groundedness=0.84, hallucination=0.04)
[RUN-0C3B37F29E22] score_reach: reach_score=86 (specificity=0.92, hook=0.88, clickbait=0.0)
[RUN-0C3B37F29E22] publish: post published -> urn:li:share:7509289467577315328
[RUN-0C3B37F29E22] optimize_content: diagnosis recorded, 2 story mutations stored
[RUN-0C3B37F29E22] Workflow complete — status=OPTIMIZED published=1 errors=0
```

---

## 9. Production Incident Triage & Troubleshooting

### Incident 1: Comments API `PERMISSION_ERROR`
- **Symptom**: `Comments API not available (PERMISSION_ERROR) — personas embedded in post body`.
- **Root Cause**: The LinkedIn OAuth application has standard "Share on LinkedIn" permissions but lacks "Community Management API" access for threaded comments.
- **Resolution**: **No action required**. The `PublisherAgent` automatically embeds the entire multi-persona roundtable debate directly into the main post body with zero data loss.

### Incident 2: GNews API HTTP 403 Rate Limit
- **Symptom**: `news.search_latest failed: HTTP 403 Forbidden`.
- **Root Cause**: Primary GNews key reached its 100 req/day quota.
- **Resolution**: Automatic failover. `news-mcp` automatically rotates requests to `GNEWS_API_KEY_2`.

### Incident 3: Evaluation Decision `REGENERATE`
- **Symptom**: `eval decision=REGENERATE factuality=0.62`.
- **Root Cause**: Persona output contained claims not supported by the PageIndex evidence tree.
- **Resolution**: LangGraph automatically retries narrative generation up to `MAX_RETRIES=2`. If retries are exhausted, it publishes only strictly verified PASS elements.

### Incident 4: Langfuse Tracing Timeout
- **Symptom**: `Langfuse init/auth failed (LLM tracing disabled)`.
- **Root Cause**: Invalid Langfuse credentials or egress network policy blocking `cloud.langfuse.com`.
- **Resolution**: The system logs a warning and proceeds with local execution without failing the publication pipeline.

---

## 10. Secret Management, API Key Rotation & Quotas

To rotate API keys without downtime:
```bash
kubectl create secret generic daily-news-secrets \
  --from-literal=LLM_API_KEY="new-llm-key" \
  --from-literal=GNEWS_API_KEY="new-gnews-key-1" \
  --from-literal=GNEWS_API_KEY_2="new-gnews-key-2" \
  --from-literal=JEV_API_KEY="new-jev-key" \
  --from-literal=LINKEDIN_CLIENT_ID="your-id" \
  --from-literal=LINKEDIN_CLIENT_SECRET="your-secret" \
  --dry-run=client -o yaml | kubectl apply -f - -n aifeeders

# Trigger rolling restart
kubectl rollout restart deployment/daily-news-api -n aifeeders
```

---

## 11. Enterprise Security & Network Isolation

- **Non-Root Execution**: All containers run with `securityContext.runAsUser: 1001` and `allowPrivilegeEscalation: false`.
- **Network Isolation**: MCP servers (`news-mcp`, `pageindex-mcp`, `evaluation-mcp`, `linkedin-mcp`) listen on internal ClusterIP services and are not exposed to public ingress.
- **Data Invariant**: Raw news articles are treated as untrusted data inputs, strictly segregated from system prompts and reasoning templates.

---
*AIFeeders Enterprise Operational Runbook.*
