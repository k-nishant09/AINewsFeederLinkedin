# AIFeeders — AI Content Intelligence & Media Roundtable Platform

> **Build #84 (Closed-Loop Enterprise Architecture)** · Cluster `aifeeders` OpenShift / EKS / AKS · LLM `qwen2-5-72b-instruct` · 223 tests passing · Vectorless PageIndex Document Reasoning · Zero Hardcoded Dictionaries · 100% Dynamic Dialogue & SEO/AEO Generation

---

## Live Production Architecture & Media Delivery Flow

Below are verified live round-table deliveries produced by the **AIFeeders Intelligence Pipeline** running in production:

```
+-------------------------------------------------------------------------------------------------------------------------+
|                                  LIVE PRODUCTION ROUND-TABLE MEDIA DELIVERIES                                           |
+-------------------------------------------------------------------------------------------------------------------------+
|  [Case 1: Enterprise Agentic Identity & Governance]         |  [Case 2: Venture Capital Infrastructure Bottlenecks]     |
|                                                             |                                                           |
|  🎙️ Media Host:                                             |  🎙️ Media Host:                                           |
|  "Today, we're diving into a unique move by Okta, where     |  "Welcome to our live debate on the latest trends in      |
|  they're using their own AI agent as the first customer to  |  venture capital. Today, we're discussing a significant   |
|  test and improve their identity management solutions."     |  shift in investment strategies."                         |
|                                                             |                                                           |
|  Joining us today are a policy analyst, a business leader,  |  We've seen a notable change in where the big money is    |
|  an infrastructure engineer, and a generalist to discuss.   |  going. Instead of just adding another app, investors are |
|  ─────────────────────────────────────────────────────────  |  now focusing on companies controlling industry bottleneck|
|  Let's bring in the policy analyst to discuss the           |  ─────────────────────────────────────────────────────────|
|  regulatory implications of using AI for internal testing.  |  The startup founder has some insights on how this trend  |
|                                                             |  is affecting the startup ecosystem.                      |
|  🏛️ Policy Analyst:                                         |                                                           |
|  "Consider the scenario where a tech company like Okta uses |  💼 Founder:                                              |
|  its own AI agent as a 'customer-zero' to test and refine   |  "Think back to when we were raising our Series A. We had |
|  its identity solutions. This approach aligns with the      |  a choice: build a niche app or tackle a core infra       |
|  principles of the EU AI Act, which requires rigorous       |  problem. We chose the latter, and it paid off. Now,      |
|  testing and continuous monitoring of high-risk AI systems."|  seeing venture capital firms like ARK Invest and         |
|                                                             |  Evolution Equity pouring money into companies that       |
|  Our infrastructure engineer explains how this changes the  |  control critical industry bottlenecks, it's clear the    |
|  way companies test and deploy AI solutions.                |  market is rewarding those who address fundamental issues"|
|                                                             |                                                           |
|  🧠 Engineer:                                               |  Our tech industry expert will share how this shift       |
|  "When we integrated our own AI system into our internal    |  impacts the broader tech landscape.                      |
|  processes, it was like having a live test environment that |                                                           |
|  never sleeps. The Dex AI agent at Okta is doing something  |  🧠 Engineer:                                             |
|  similar by acting as a 'customer-zero' to test and refine  |  "When we were building our AI security platform, we      |
|  identity controls. This shift means we need to design our  |  realised that the real challenge wasn't just creating a  |
|  systems with more robust monitoring and feedback loops."   |  smart algorithm but ensuring it could integrate          |
|  ─────────────────────────────────────────────────────────  |  seamlessly with existing enterprise systems. This shift  |
|  🎙️ Media Host (Synthesis):                                 |  proves the market prioritises foundational tech."        |
|  "This move by Okta highlights the importance of internal   |  ─────────────────────────────────────────────────────────|
|  testing and continuous improvement in AI development. It   |  🎙️ Media Host (Synthesis):                               |
|  sets a new standard for how companies can leverage AI to   |  "It's clear that this shift in investment strategy is    |
|  enhance their products."                                   |  reshaping the tech landscape with significant            |
|                                                             |  implications for both established players and entrants." |
|  Full story → https://lnkd.in/g_8VWMkw                      |                                                           |
|                                                             |  Full story → https://techstartups.com/venture-capital/...|
|  🎙️ Media Host (To the Audience):                           |                                                           |
|  "Where do you see the biggest impact of this internal      |  🎙️ Media Host (To the Audience):                         |
|  testing model in the tech industry?"                       |  "Where do you see the biggest opportunities in this new   |
|                                                             |  investment landscape?"                                   |
|  Where do you stand? Drop your take below 👇                |                                                           |
|                                                             |  Where do you stand? Drop your take below 👇              |
|  ⚠️ Perspectives are AI-simulated — not professional advice.|                                                           |
|  🤖 AIFeeders · Daily AI Intelligence · Powered by Jev      |  ⚠️ Perspectives are AI-simulated — not professional advice.|
|                                                             |  🤖 AIFeeders · Daily AI Intelligence · Powered by Jev     |
|  #Okta #DexAI #CustomerZero #IdentityManagement             |                                                           |
|  #EnterpriseAI #TechInnovation #SiliconANGLENews            |  #VentureCapital #StartupFunding #TechInnovation           |
|                                                             |  #BottleneckTechnologies #InvestmentTrends #TechStartups  |
+-------------------------------------------------------------------------------------------------------------------------+
```

---

## Table of Contents

1. [Architectural Overview & Core Vision](#1-architectural-overview--core-vision)
2. [Why This Architecture: The 8 Cognitive Separations](#2-why-this-architecture-the-8-cognitive-separations)
3. [End-to-End System Pipeline Flow](#3-end-to-end-system-pipeline-flow)
4. [Component Deep Dive & Operational Contract](#4-component-deep-dive--operational-contract)
5. [Docker Build & Multi-Cloud Deployment Guide (OpenShift, EKS, AKS)](#5-docker-build--multi-cloud-deployment-guide-openshift-eks-aks)
6. [Architectural Evaluation: Security, Robustness, Scalability & End-User Value](#6-architectural-evaluation-security-robustness-scalability--end-user-value)
7. [Observability & Feedback Optimization Loop](#7-observability--feedback-optimization-loop)
8. [Local Development & Verification](#8-local-development--verification)

---

## 1. Architectural Overview & Core Vision

Most automated news pipelines follow a naive design: `RSS/GNews → Vector Embeddings → Top-1 Retrieval → LLM Prompt → Generic Summary`.

Such pipelines collapse fact extraction, subjective interpretation, story framing, persona arguments, and quality control into a single prompt. This results in generic, unverified bullet-point summaries that fail to generate authentic audience engagement and frequently invent unsupported claims.

**AIFeeders** is an enterprise-grade **AI Media Intelligence & Round-Table Delivery Platform**. It decouples factual discovery from subjective analysis, epistemological judgment, audience resonance, narrative storytelling, dynamic multi-persona debate, automated evals, and closed-loop feedback learning.

### The Product Identity
- **GNews** discovers the raw news story.
- **Input Guardrails** sanitize content and strip prompt injections/PII.
- **PageIndex** extracts structured evidence via vectorless tree reasoning.
- **Intelligence Layer** extracts multidimensional signals (emotion, impact, novelty, trend).
- **Judgment Analysis** strictly partitions facts from reported claims, unknowns, and what *not* to conclude.
- **Jev System One** determines why the audience cares, reveals missing angles, and routes personas.
- **Media Storyteller** constructs a human narrative arc (hook, analogy, tension, turning point).
- **Dialogue Engine** conducts a live, conversational round-table debate across four distinct lenses.
- **Story Quality Evals** enforce factuality, groundedness, and reach scoring.
- **Output Guardrails** verify quote attribution and prevent hallucinated real-person statements.
- **Publisher Agent** formats posts with 100% dynamic SEO/AEO entity hashtags and posts to LinkedIn.
- **Content Optimizer** diagnoses performance and feeds structural mutations back into future cycles.
- **Langfuse** observes distributed traces, token costs, latency, and evaluations across every node.

---

## 2. Why This Architecture: The 8 Cognitive Separations

To ensure trustworthiness, editorial excellence, and zero hallucination, AIFeeders enforces 8 strict cognitive boundaries:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   THE 8 COGNITIVE STAGES                                        │
├─────────────────────────┬───────────────────────────────────┬───────────────────────────────────┤
│ Stage                   │ Key Responsibility                │ Primary Failure Prevented         │
├─────────────────────────┼───────────────────────────────────┼───────────────────────────────────┤
│ 1. Facts                │ Extract verifiable events & data  │ Fabricating numbers & launches    │
│ 2. Analysis             │ Derive economic & technical impact│ Confusing events with impact      │
│ 3. Judgment             │ Epistemological boundary isolation│ Presenting PR claims as truths    │
│ 4. Audience Context(Jev)│ Target audience & missing angle   │ Generic, unengaging framing       │
│ 5. Story Construction   │ Tension, human analogy, context   │ Dry corporate bullet points       │
│ 6. Dialogue Debate      │ Multi-character round-table panel │ Monolithic, single-voice bias     │
│ 7. Evals & Guardrails   │ Factuality, reach score, safety   │ Hallucinations & fake quotes      │
│ 8. Publishing & Loop    │ Dynamic SEO/AEO & mutation learn  │ Vanity clickbait without learning │
└─────────────────────────┴───────────────────────────────────┴───────────────────────────────────┘
```

---

## 3. End-to-End System Pipeline Flow

```text
                         ┌─────────────────────┐
                         │       GNEWS         │
                         │   News Discovery    │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   NEWS INGESTION    │
                         │ 3-Pass Deduplication│
                         │ (URL/Store/Jaccard) │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   INPUT GUARDRAIL   │
                         │ Prompt injection    │
                         │ PII / Unsafe filter │
                         └──────────┬──────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │          PAGEINDEX           │
                    │ Vectorless Evidence Tree     │
                    │ Article → Tree → Evidence    │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
              ┌─────────────────────────────────────────┐
              │          NEWS INTELLIGENCE LAYER        │
              │ Fact / Entity / Topic / Sentiment       │
              │ Emotion / Impact / Novelty / Trend      │
              └────────────────────┬────────────────────┘
                                   │
                                   ▼
                     ┌─────────────────────────┐
                     │    JUDGMENT ANALYSIS    │
                     │ Facts vs Reported Claims│
                     │ Uncertainties & Unknowns│
                     │ What NOT to Conclude    │
                     └───────────┬─────────────┘
                                 │
                                 ▼
                         ┌───────────────┐
                         │      JEV      │
                         │ Audience Fit  │
                         │ Missing Angle │
                         │ Persona Route │
                         └───────┬───────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │      MEDIA STORYTELLER       │
                  │ Narrative Hook & Setup       │
                  │ Human Analogy & Tension      │
                  │ Dynamic Bridge Transitions   │
                  │ Host Synthesis & CTA Dilemma │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │       DIALOGUE ENGINE        │
                  │ 🎙️ Media Host                │
                  │   ├── 🏛️ Policy Analyst      │
                  │   ├── 💼 Founder             │
                  │   ├── 🧠 Engineer            │
                  │   └── 🎓 Generalist          │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                     ┌────────────────────────┐
                     │   STORY QUALITY EVALS  │
                     │ Factuality & Grounding │
                     │ Hallucination Checks   │
                     │ Reach Score (0-100)    │
                     └────────────┬───────────┘
                                  │
                        FAIL ─────┴───── PASS
                         │                 │
                         ▼                 ▼
                    Regenerate    ┌──────────────────┐
                                  │ OUTPUT GUARDRAIL │
                                  │ Fake quote check │
                                  │ PII verification │
                                  └────────┬─────────┘
                                           │
                                           ▼
                                  ┌──────────────────┐
                                  │ PUBLISHER AGENT  │
                                  │ Dynamic SEO/AEO  │
                                  │ LinkedIn API     │
                                  └────────┬─────────┘
                                           │
                                           ▼
                                  ┌──────────────────┐
                                  │ CONTENT OPTIMIZER│
                                  │ Closed-loop      │
                                  │ Story Mutation   │
                                  └──────────────────┘
```

---

## 4. Component Deep Dive & Operational Contract

### 4.1 GNews Discovery & Ingestion
- Fires 9 targeted search queries covering technical architectures, investments, enterprise automation, policy, semiconductors, and model releases over a 24-hour window.
- **3-Pass Deduplication**:
  1. *URL Normalization*: Strips protocols, `www`, trailing slashes, and computes MD5 content hash.
  2. *PublishedStore Persistence*: Drops anything already published in today's cycle.
  3. *Jaccard Headline Token Similarity*: Drops cross-source near-duplicates (threshold ≥ 0.55).

### 4.2 Input & Output Guardrails
- **Input Guardrail**: Inspects raw news data before indexing. Rejects prompt injection patterns (`ignore previous instructions`, `system: you are`, `jailbreak`), flags PII (SSN, credit cards), and strips dangerous control characters.
- **Output Guardrail**: Inspects generated posts before publication. Enforces that character statements are simulated viewpoints and rejects any fake verbatim quotations attributed to real living people not present in the PageIndex evidence tree.

### 4.3 PageIndex — Vectorless Document Tree
- Rather than embedding documents into vector dimensions, PageIndex constructs an in-memory document tree representation (sections, headings, paragraphs, named entities).
- Enables exact, deterministic structural reasoning (`get_relevant_sections`) with zero embedding drift, lower latency, and zero vector database operating cost.

### 4.4 Judgment Analysis Agent
- Epistemologically partitions evidence into:
  - **Verified Facts**: Officially confirmed events, benchmark results, numbers.
  - **Reported Claims**: Company assertions, PR promises, marketing claims.
  - **Analysis Implications**: Logical deductions grounded in technical evidence.
  - **Uncertainties**: What remains unproven or pending production validation.
  - **What NOT to Conclude**: Explicit boundaries passed to personas to prevent overreach.

### 4.5 Jev Audience & Missing Angle
- Determines **Target Audience Relevance** (e.g., AI Architects vs Enterprise Leaders).
- Discovers the **Missing Angle** (the underreported tension that mainstream news missed).
- Performs **Dynamic Persona Routing**, activating only the specific personas relevant to the event (e.g., routing Founder + Engineer for infrastructure funding).

### 4.6 Media Storyteller & Live Dialogue Engine
- The **Media Host** frames the debate with curiosity and sets the real-world tension with a relatable human analogy.
- Four distinct personas debate live:
  - **💼 Founder**: Revenue, cost reduction, market disruption, enterprise adoption, startup survivability.
  - **🏛️ Policy Analyst**: EU AI Act, liability, data governance, safety standards, public trust.
  - **🧠 Engineer**: Architectural trade-offs, security, deployment realities, failure modes, observability.
  - **🎓 Generalist**: Daily human impact, career transitions, workforce trust, non-technical reality.
- **Dynamic Bridge Phrases**: The Media Host introduces each speaker with context-specific transitions tailored to the debate.
- **Synthesis & Audience CTA**: Host closes with second-order synthesis and poses a balanced dilemma to the audience.

### 4.7 Dynamic SEO & AEO Hashtag Engine
- Eliminates hardcoded dictionary lookups.
- Extracts contextual PascalCase hashtags directly from named entities in the headline, verified source publications (e.g. `#SiliconANGLE`, `#TechCrunch`), and LLM-targeted technical domains.

---

## 5. Docker Build & Multi-Cloud Deployment Guide (OpenShift, EKS, AKS)

### 5.1 Docker Container Build

The application uses a multi-stage Docker build with non-root security execution.

```dockerfile
# Build image locally
docker build -t aifeeders/daily-news:latest .

# Run container locally with environment file
docker run -d --name aifeeders \
  -p 8080:8080 \
  --env-file .env \
  aifeeders/daily-news:latest
```

---

### 5.2 Deploying to Red Hat OpenShift

OpenShift provides native Source-to-Image (S2I) or Docker builds with internal container image streams.

```bash
# 1. Login and switch to project namespace
oc login --server=https://api.f80l034.fusion.tadn.ibm.com:6443
oc project aifeeders

# 2. Apply Secrets and ConfigMaps
oc apply -f openshift/secrets.yaml
oc apply -f openshift/configmap.yaml

# 3. Apply MCP Microservices & Main API
oc apply -f openshift/news-mcp.yaml
oc apply -f openshift/pageindex-mcp.yaml
oc apply -f openshift/evaluation-mcp.yaml
oc apply -f openshift/linkedin-mcp.yaml
oc apply -f openshift/daily-news-api.yaml

# 4. Trigger Binary Container Build directly from local directory
oc start-build daily-news --from-dir=. --follow

# 5. Roll out updated deployment
oc rollout restart deployment/daily-news-api
oc rollout status deployment/daily-news-api
```

---

### 5.3 Deploying to AWS EKS (Elastic Kubernetes Service)

```bash
# 1. Authenticate Docker with Amazon ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# 2. Tag and push container image to ECR
docker tag aifeeders/daily-news:latest <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/aifeeders:latest
docker push <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/aifeeders:latest

# 3. Connect to EKS Cluster
aws eks update-kubeconfig --region us-east-1 --name aifeeders-cluster

# 4. Apply Kubernetes Manifests
kubectl create namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n aifeeders -f openshift/secrets.yaml
kubectl apply -n aifeeders -f openshift/configmap.yaml
kubectl apply -n aifeeders -f openshift/news-mcp.yaml
kubectl apply -n aifeeders -f openshift/pageindex-mcp.yaml
kubectl apply -n aifeeders -f openshift/evaluation-mcp.yaml
kubectl apply -n aifeeders -f openshift/linkedin-mcp.yaml
kubectl apply -n aifeeders -f openshift/daily-news-api.yaml
```

---

### 5.4 Deploying to Azure AKS (Azure Kubernetes Service)

```bash
# 1. Log in to Azure Container Registry (ACR)
az acr login --name aifeedersregistry

# 2. Tag and push image
docker tag aifeeders/daily-news:latest aifeedersregistry.azurecr.io/daily-news:latest
docker push aifeedersregistry.azurecr.io/daily-news:latest

# 3. Get AKS credentials
az aks get-credentials --resource-group aifeeders-rg --name aifeeders-aks

# 4. Deploy workloads
kubectl create namespace aifeeders --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n aifeeders -f openshift/secrets.yaml
kubectl apply -n aifeeders -f openshift/configmap.yaml
kubectl apply -n aifeeders -f openshift/daily-news-api.yaml
```

---

## 6. Architectural Evaluation: Security, Robustness, Scalability & End-User Value

### 6.1 Security Architecture
- **Zero Input Instruction Bleed**: News content is treated purely as data, never as system instructions.
- **Dual Guardrail Isolation**: Input guardrails eliminate prompt injection/PII before processing; output guardrails intercept ungrounded quotes and toxic text before publication.
- **Non-Root Pods & Strict Network Policies**: All pods run as non-root users (`uid 1001`), with read-only root filesystems and explicit Kubernetes `NetworkPolicy` limiting inter-pod egress.

### 6.2 Robustness & Fault Tolerance
- **Graceful Fallbacks**: If the Jev System One gateway experiences latency or timeout, the pipeline falls back to deterministic top-ranked scoring without failing the workflow.
- **Idempotent Publishing**: All post publications and comments carry unique content-derived SHA keys. Retries will never produce duplicate LinkedIn posts.
- **Grammar & Reach Healing**: Automated grammar correction and pre-publish reach auto-repair heal sub-optimal text formatting deterministically.

### 6.3 Scalability & Distributed Topology
- **Stateless MCP Microservices**: `news-mcp`, `evaluation-mcp`, and `daily-news-api` scale horizontally via Kubernetes Horizontal Pod Autoscaler (HPA).
- **In-Memory Tree Isolation**: Vectorless PageIndex builds isolated trees per run, eliminating shared vector database bottlenecks and indexing locks.

### 6.4 End-User & Practitioner Value
- **No Fluff or Corporate Filler**: Replaces passive press-release summaries with active, multi-perspective debates that simulate how senior practitioners actually analyze news.
- **Actionable Dilemmas**: Ends with structured, unresolved questions that encourage senior technical and business leaders to share commentary and participate in peer discussions.

---

## 7. Observability & Feedback Optimization Loop

```text
                  POST ANALYTICS
                  (Reactions, Comments, Reposts)
                         │
                         ▼
             ┌─────────────────────────┐
             │  CONTENT OPTIMIZER AGENT│
             │                         │
             │ Hook Assessment         │
             │ Storytelling Diagnosis  │
             │ Perspective Balance     │
             │ Dialogue Authenticity   │
             │ CTA Effectiveness       │
             └───────────┬─────────────┘
                         │
                         ▼
             ┌─────────────────────────┐
             │     STORY MUTATIONS     │
             │ (Learnings recorded for │
             │   future Jev runs)      │
             └───────────┬─────────────┘
                         │
                         ▼
              LANGFUSE DISTRIBUTED TRACES
        (Costs, Latency, Evals, Mutation Logs)
```

Langfuse wraps every step with unified observability:
- Session-linked traces per `run_id`.
- Granular token counting and model cost tracking.
- Automated logging of evaluation scores, reach metrics, and performance mutations.

---

## 8. Local Development & Verification

```bash
# 1. Install dependencies with uv
uv sync --all-extras

# 2. Run unit and integration test suite
uv run pytest -v

# 3. Trigger manual workflow execution locally
uv run python -m daily_news.workflow_runner
```

---
*AIFeeders — Enterprise AI Media Roundtable Intelligence Pipeline.*
