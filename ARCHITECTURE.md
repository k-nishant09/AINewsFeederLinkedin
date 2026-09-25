# AIFeeders — Architecture Reference

> **Build #84 (Enterprise Closed-Loop Media Architecture)** · OpenShift `aifeeders` · LangGraph · Jev System One · Vectorless PageIndex · Epistemological Judgment Layer · EKS/AKS Portable

---

## Table of Contents

1. [Architectural Overview & Design Intent](#1-architectural-overview--design-intent)
2. [Why This Architecture: The 8 Cognitive Separations](#2-why-this-architecture-the-8-cognitive-separations)
3. [The 13-Stage Closed-Loop Pipeline](#3-the-13-stage-closed-loop-pipeline)
4. [System Topology & Data Flow](#4-system-topology--data-flow)
5. [Vectorless Document Tree: PageIndex Protocol](#5-vectorless-document-tree-pageindex-protocol)
6. [The NewsIntelligence & Epistemological Judgment Contract](#6-the-newsintelligence--epistemological-judgment-contract)
7. [Jev System One: Audience & Angle Optimization](#7-jev-system-one-audience--angle-optimization)
8. [Media Storyteller & Dynamic Round-Table Dialogue Engine](#8-media-storyteller--dynamic-round-table-dialogue-engine)
9. [Story Quality Evals, Reach Scoring & Guardrails](#9-story-quality-evals-reach-scoring--guardrails)
10. [Dynamic SEO & AEO Entity Hashtag Extraction](#10-dynamic-seo--aeo-entity-hashtag-extraction)
11. [Closed-Loop Feedback & Story Mutation Engine](#11-closed-loop-feedback--story-mutation-engine)
12. [Langfuse Observability & Distributed Tracing](#12-langfuse-observability--distributed-tracing)
13. [Containerization & Multi-Cloud Deployment (OpenShift, EKS, AKS)](#13-containerization--multi-cloud-deployment-openshift-eks-aks)
14. [Architectural Evaluation: Security, Robustness, Scalability & Utility](#14-architectural-evaluation-security-robustness-scalability--utility)

---

## 1. Architectural Overview & Design Intent

### The Problem with Naive Summarization
A naive news processing architecture (`GNews → Vector DB → LLM → LinkedIn`) collapses fact extraction, opinion synthesis, audience calibration, and content formatting into a single prompt. This produces:
1. **Generic, Dry Bullet Points**: Content reads like a corporate press release rather than an engaging media program.
2. **Hallucinated Factuality**: The LLM conflates corporate marketing claims with verified technical facts.
3. **No Perspective Diversity**: The output represents a monolithic point of view rather than capturing the real-world trade-offs between executives, policymakers, engineers, and end-users.
4. **Zero Continuous Learning**: The system publishes blindly without diagnosing why previous posts underperformed or mutating future story formulations based on audience reception.

### The AIFeeders Architectural Solution
AIFeeders implements a **multi-agent, closed-loop media intelligence system** that treats news as untrusted data, extracts structured evidence without vector drift, performs epistemological boundary analysis, seeds distinct mental models for simulated roundtable personas, evaluates story quality and reach, and learns from audience feedback.

---

## 2. Why This Architecture: The 8 Cognitive Separations

To ensure trustworthiness and broadcast quality, the architecture enforces 8 discrete cognitive boundaries:

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

## 3. The 13-Stage Closed-Loop Pipeline

```text
  START
    │
    ▼
  1. discover_news       ──► 9 GNews queries across tech, business, policy, chips, models
    │
    ▼
  2. deduplicate         ──► 3-pass dedup (URL hash + PublishedStore + Jaccard token overlap)
    │
    ▼
  3. input_guardrail     ──► Reject prompt injection patterns, strip control chars, detect PII
    │
    ▼
  4. fetch_articles      ──► Full article text extraction & metadata enrichment
    │
    ▼
  5. index_pageindex     ──► Build vectorless in-memory document tree representation
    │
    ▼
  6. jev_prefilter       ──► Score articles (emotion, impact, novelty, trend) & rank top-1
    │
    ▼
  7. judgment_analysis   ──► Partition facts, claims, analysis, uncertainties, & what NOT to say
    │
    ▼
  8. summarize           ──► MediaStorytellerAgent (Pass 1: Story Arc) + SummaryAgent (Pass 2)
    │
    ▼
  9. find_angle & route  ──► Jev determines missing angle, primary audience, routes active personas
    │
    ▼
 10. generate_personas   ──► Founder, Policy Analyst, Engineer, Generalist live debate (Parallel)
    │
    ▼
 11. evaluate            ──► Jev & LLM quality gate (Factuality, Groundedness, Hallucination)
    │                         ├─► REGENERATE ──► summarize (up to MAX_RETRIES)
    │                         └─► PASS
    ▼
 12. score_reach         ──► 6-dimension reach scoring (0-100) & auto-repair
    │
    ▼
 13. publish & optimize  ──► Output Guardrails ──► LinkedIn API ──► Content Optimizer Feedback Loop
    │
   END
```

---

## 4. System Topology & Data Flow

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    LANGFUSE OBSERVABILITY                                       │
│          (Traces · Prompts · Models · Token Latency · Guardrails · Evals · Feedback)             │
└────────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │       1. GNews Discovery      │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   2. Ingestion & Dedup        │
                                 │  (3-pass URL/Store/Jaccard)   │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   3. Input Guardrails         │
                                 │  (PII · Injections · Escapes) │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   4. PageIndex Evidence Tree  │
                                 │  (Vectorless section parsing) │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   5. News Intelligence        │
                                 │  (Sentiment · Emotion · Impact│
                                 │   Novelty · Trend Velocity)   │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   6. Judgment Analysis        │
                                 │  (Facts vs Claims vs Unknowns │
                                 │   Boundaries: What NOT to say)│
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   7. Jev Audience & Angle     │
                                 │  (Missing angle · Audience fit│
                                 │   Persona Routing)            │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   8. Media Storyteller        │
                                 │  (Hook · Analogy · Dilemma    │
                                 │   Transitions · Synthesis)    │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │   9. Dialogue Engine          │
                                 │  (Policy · Founder · Engineer │
                                 │   Generalist live exchange)   │
                                 └───────────────┬───────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │  10. Story Quality Evals      │
                                 │  (Factuality · Groundedness   │
                                 │   Hallucination · Reach score)│
                                 └───────┬───────────────┬───────┘
                                         │ FAIL          │ PASS
                                         ▼               ▼
                                 [ Regenerate ]  ┌───────────────┴───────────────┐
                                                 │  11. Output Guardrails        │
                                                 │  (Fake quotes · Attribution)  │
                                                 └───────────────┬───────────────┘
                                                                 │
                                                 ┌───────────────┴───────────────┐
                                                 │  12. Publisher & Distribution │
                                                 │  (Dynamic SEO/AEO · LinkedIn) │
                                                 └───────────────┬───────────────┘
                                                                 │
                                                 ┌───────────────┴───────────────┐
                                                 │  13. Feedback & Optimization  │
                                                 │  (Structural diagnosis · Loop)│
                                                 └───────────────────────────────┘
```

---

## 5. Vectorless Document Tree: PageIndex Protocol

Vector databases incur embedding latency, token expenses, and semantic drift. When reasoning over a single fresh article, similarity search often pulls irrelevant paragraphs that happen to share cosine distance.

**PageIndex** eliminates vector embeddings entirely:
- Parses the document into an in-memory structural tree (`Document → Sections → Headings → Paragraphs → Evidence Items`).
- When an agent requests evidence via `get_relevant_sections(document_id, query_intent)`, PageIndex navigates the structural hierarchy deterministically.
- Guarantees 100% reproducible retrieval: identical document text always returns the exact same evidence nodes.

---

## 6. The NewsIntelligence & Epistemological Judgment Contract

The `JudgmentAgent` runs before narrative storytelling to establish strict epistemic boundaries:

```python
class JudgmentAnalysis(BaseModel):
    facts: list[str]                  # Officially confirmed data, launches, numbers
    reported_claims: list[str]        # Corporate statements, PR promises, executive claims
    analysis_implications: list[str]  # Grounded deductions on architecture/economics
    uncertainties: list[str]          # Unproven metrics, pending real-world benchmarks
    what_not_to_conclude: list[str]   # Explicit guardrails: What personas must NOT assert
```

By providing `what_not_to_conclude` directly to the `PersonaAgentFactory`, personas are strictly prevented from fabricating certainty or attributing hypothetical outcomes as confirmed facts.

---

## 7. Jev System One: Audience & Angle Optimization

Jev acts as the audience and editorial judgment layer:
1. **Pre-filter & Ranking**: Scores candidate articles across multi-dimensional criteria (novelty, controversy, market impact, trend velocity).
2. **Missing Angle Discovery**: Discovers the underreported tension or non-obvious operational bottleneck that generic coverage overlooked.
3. **Dynamic Persona Routing**: Rather than running all personas unconditionally, Jev selects the relevant voices (e.g., routing `['business', 'linkedin']` for an infrastructure funding story).

---

## 8. Media Storyteller & Dynamic Round-Table Dialogue Engine

### The Live Media Roundtable
The post is formatted not as bullet points, but as a broadcast roundtable program:
- **Media Host Opening**: Hooks the audience with immediate curiosity and situation framing.
- **Human Analogy**: Translates abstract technical mechanics into an intuitive real-world mental model.
- **Dynamic Bridge Transitions**: Contextual transitions introducing each panelist based on the flow of arguments.
- **Distinct Persona Lenses**:
  - **💼 Founder**: Customer acquisition, unit economics, platform lock-in, infrastructure vs application risk.
  - **🏛️ Policy Analyst**: Regulatory compliance, EU AI Act risk tiers, liability, cross-border governance.
  - **🧠 Engineer**: Architectural trade-offs, security perimeters, observability, integration friction.
  - **🎓 Generalist**: End-user experience, everyday usability, workplace transitions, digital literacy.
- **Media Host Synthesis & Audience Dilemma**: Summarizes second-order consequences and poses a balanced dilemma to the audience.

---

## 9. Story Quality Evals, Reach Scoring & Guardrails

### Dual Guardrail Protection
- **Input Guardrail**: Inspects untrusted news inputs for prompt injection signatures (`ignore previous instructions`, `system: you are`), PII, and malformed control characters.
- **Output Guardrail**: Validates generated posts prior to publication. Detects hallucinated quotes attributed to real living individuals not verified in the PageIndex evidence tree.

### Quality Evals & Reach Optimization
- **EvaluationAgent**: Measures `factuality`, `groundedness`, `hallucination`, and policy compliance. Triggers `REGENERATE` if thresholds are unmet.
- **ReachScoreAgent**: Deterministically analyzes the post across 6 dimensions (Hook Strength, Specificity, Question Quality, Length Fit, Clickbait Penalty, Topic Coherence) to ensure maximum organic engagement.

---

## 10. Dynamic SEO & AEO Entity Hashtag Extraction

Static hashtag dictionaries are completely eliminated. The dynamic SEO/AEO engine extracts hashtags directly from:
1. **Named Entities**: PascalCase extraction of proper nouns, model names, and products from the headline and evidence tree (e.g., `#Okta`, `#DexAI`, `#VentureCapital`).
2. **Source Publications**: Verified source hashtagging (e.g., `#SiliconANGLE`, `#TechCrunch`, `#Reuters`).
3. **Domain Subjects**: High-relevance search topic tags derived from the underlying story.

---

## 11. Closed-Loop Feedback & Story Mutation Engine

After publication, the `ContentOptimizerAgent` fetches engagement analytics (`reactions`, `comments`, `reposts`, `impressions`) and executes an automated structural diagnosis:
- Evaluates individual component performance (Hook, Storytelling, Perspective Balance, Dialogue Authenticity, Audience CTA).
- Identifies the weakest component and generates actionable recommendations.
- Produces **Story Mutations** stored for calibration in subsequent Jev angle recommendations and prompt optimizations.

---

## 12. Langfuse Observability & Distributed Tracing

Every stage of the LangGraph state graph is traced in Langfuse:
- Unified `run_id` session linking across all microservices and agents.
- Token consumption, cost accounting, and latency breakdown per LLM call.
- Direct logging of evaluation decisions, reach scores, guardrail violations, and optimization diagnoses.

---

## 13. Containerization & Multi-Cloud Deployment (OpenShift, EKS, AKS)

### Docker Build
```bash
docker build -t aifeeders/daily-news:latest .
```

### OpenShift Deployment
```bash
oc project aifeeders
oc apply -f openshift/secrets.yaml
oc apply -f openshift/configmap.yaml
oc apply -f openshift/news-mcp.yaml
oc apply -f openshift/pageindex-mcp.yaml
oc apply -f openshift/evaluation-mcp.yaml
oc apply -f openshift/linkedin-mcp.yaml
oc apply -f openshift/daily-news-api.yaml
oc start-build daily-news --from-dir=. --follow
oc rollout status deployment/daily-news-api
```

### AWS EKS & Azure AKS Portability
Standard Kubernetes manifests located in `openshift/` deploy across AWS EKS and Azure AKS by pushing images to Amazon ECR or Azure ACR and applying `kubectl apply -f openshift/`.

---

## 14. Architectural Evaluation: Security, Robustness, Scalability & Utility

| Dimension | Architectural Implementation | Practical Guarantee |
|---|---|---|
| **Security** | Untrusted data isolation, dual guardrails, non-root containers (`uid 1001`), strict `NetworkPolicy` | Zero prompt injection bleed, zero PII leakage, zero unauthorized container privileges |
| **Robustness** | Epistemological judgment boundaries, graceful Jev fallbacks, idempotent publication keys | Zero fake quote attribution, zero duplicate posts, resilient to external API outages |
| **Scalability** | Stateless MCP microservices, horizontal pod autoscaling (HPA), vectorless in-memory tree parsing | Scales horizontally without vector DB indexing locks or shared cache bottlenecks |
| **End-User Utility**| Multi-perspective debate format, dynamic SEO tags, closed-loop structural optimization | Delivers authentic, high-signal editorial intelligence that sparks practitioner discussion |

---
*AIFeeders — Enterprise AI Media Intelligence Architecture.*
