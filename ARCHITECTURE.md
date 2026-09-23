# AIFeeders — Architecture

This document describes the production architecture of AIFeeders using Mermaid diagrams. It covers the complete system topology, the LangGraph agent workflow, all MCP servers and their tools, the 6-layer guardrail pipeline, data models, and deployment layout.

---

## 1. System Architecture — High Level

```mermaid
graph TB
    subgraph "External"
        GNews["GNews API<br/>(news search)"]
        LLM["IBM LLM Gateway<br/>qwen2-5-72b-instruct<br/>(OpenAI-compatible, self-signed cert)"]
        LinkedIn["LinkedIn REST API<br/>Posts API v202609<br/>(Comments API blocked — needs approval)"]
        Langfuse["Langfuse<br/>(optional LLM tracing)"]
    end

    subgraph "OpenShift — namespace: aifeeders"
        CronJob["CronJob: daily-ai-news<br/>0 10 * * * UTC<br/>label: app=daily-news-worker"]
        API["daily-news-api<br/>FastAPI (HPA: 2–10 replicas)"]

        subgraph "LangGraph Workflow (compiled StateGraph)"
            Graph["daily_news_graph<br/>9-node StateGraph"]
        end

        subgraph "MCP Servers — all port 8000, POST /call REST"
            NewsMCP["news-mcp<br/>(GNews adapter)"]
            PageMCP["pageindex-mcp<br/>(in-memory doc store)<br/>⚠️ replicas: 1"]
            EvalMCP["evaluation-mcp<br/>(6-layer guardrails)"]
            LIMCP["linkedin-mcp<br/>(OAuth + Posts API)<br/>⚠️ replicas: 1"]
        end
    end

    CronJob -->|"python -m daily_news.workflow_runner"| Graph
    API -->|"POST /workflow/daily-news<br/>(no body, auto run_id)"| Graph

    Graph -->|"MCPHTTPClient POST /call"| NewsMCP
    Graph -->|"MCPHTTPClient POST /call"| PageMCP
    Graph -->|"MCPHTTPClient POST /call"| EvalMCP
    Graph -->|"MCPHTTPClient POST /call"| LIMCP

    NewsMCP -->|"GET /search (HTTPS)"| GNews
    EvalMCP -->|"POST /chat/completions<br/>verify=False"| LLM
    Graph -->|"ChatOpenAI<br/>verify=False"| LLM
    LIMCP -->|"POST /rest/posts (HTTPS)"| LinkedIn
    Graph -.->|"get_langfuse_callback<br/>+ start_span (optional)"| Langfuse
```

---

## 2. LangGraph Workflow — State Machine

```mermaid
flowchart TD
    START([START]) --> A[discover_news]
    A --> B[deduplicate]
    B --> C[fetch_articles]
    C --> D[index_pageindex]
    D --> E[select_stories]
    E --> F[summarize]
    F --> G[generate_personas]
    G --> H{evaluate}

    H -->|"PASS"| I[publish]
    H -->|"REGENERATE<br/>retry_count < 2"| F
    H -->|"REGENERATE<br/>retry_count ≥ 2<br/>(max retries)"| I
    H -->|"BLOCK or HUMAN_REVIEW<br/>(no items in passed_ids)"| I

    I --> END([END])

    style START fill:#22c55e,color:#fff
    style END fill:#ef4444,color:#fff
    style H fill:#f59e0b,color:#fff
    style I fill:#3b82f6,color:#fff
```

### Workflow State (`NewsWorkflowState`)

```mermaid
classDiagram
    class NewsWorkflowState {
        +str run_id
        +list raw_articles
        +list deduplicated_articles
        +list selected_articles
        +list pageindex_documents
        +list summaries
        +list persona_outputs
        +list evaluation_results
        +int retry_count
        +str approval_status
        +list linkedin_results
        +str workflow_status
        +list errors
    }
```

---

## 3. Agent Architecture

```mermaid
graph LR
    subgraph "LangGraph Nodes"
        SUM["SummaryAgent<br/>LangChain chain<br/>temperature=0.2"]
        PER["PersonaAgentFactory<br/>asyncio.gather<br/>5× parallel<br/>temperature=0.4"]
        EVA["EvaluationAgent<br/>deterministic gate<br/>applies thresholds"]
        PUB["PublisherAgent<br/>no LLM calls<br/>composition + POST /call"]
    end

    subgraph "MCP Clients — MCPHTTPClient POST /call"
        NC["NewsMCPClient → news-mcp"]
        PC["PageIndexMCPClient → pageindex-mcp"]
        EC["EvaluationMCPClient → evaluation-mcp"]
        LC["LinkedInMCPClient → linkedin-mcp"]
    end

    subgraph "LLM gateway (qwen2-5-72b, verify=False)"
        SLLM["prompts/summary.txt<br/>→ NewsSummary (Pydantic)"]
        PLLM1["prompts/capitalist.txt"]
        PLLM2["prompts/labor.txt"]
        PLLM3["prompts/policy.txt"]
        PLLM4["prompts/genz.txt"]
        PLLM5["prompts/linkedin.txt"]
        ELLM["factuality + hallucination<br/>injection + policy prompts"]
    end

    subgraph "Observability"
        LF["Langfuse<br/>get_langfuse_callback<br/>+ start_span"]
    end

    SUM --> PC
    SUM --> SLLM
    SUM -.->|"traces"| LF
    PER --> PC
    PER --> PLLM1 & PLLM2 & PLLM3 & PLLM4 & PLLM5
    PER -.->|"5 traces"| LF
    EVA --> EC
    EC --> ELLM
    PUB --> LC

    style PUB fill:#3b82d4,color:#fff
    style EVA fill:#f59e0b,color:#fff
```

> **Key design point:** `PublisherAgent` makes **no LLM calls**. It is a deterministic composition layer — text is assembled from pre-evaluated model outputs then posted via `MCPHTTPClient`. The LLM never decides what to publish.

---

## 4. MCP Server Tools Map

```mermaid
graph TB
    subgraph "news-mcp (GNews adapter)"
        N1["news_search_latest(query, hours, limit)"]
        N2["news_search_by_category(category, hours)"]
        N3["news_search_ai_tech(hours)"]
        N4["news_search_ai_finance(hours)"]
        N5["news_top_headlines_technology(limit)"]
        N6["news_fetch_article(url)"]
    end

    subgraph "pageindex-mcp (document store)"
        P1["pageindex_index_document(id, title, content, url)"]
        P2["pageindex_get_relevant_sections(doc_id, question)"]
        P3["pageindex_search_document(doc_id, query)"]
        P4["pageindex_get_document(doc_id)"]
    end

    subgraph "evaluation-mcp (guardrail pipeline)"
        E0["evaluation_evaluate_all(article_id, source, generated, persona)"]
        E1["guardrail_prompt_injection(text)"]
        E2["guardrail_factuality(source, generated)"]
        E3["guardrail_hallucination(source, generated)"]
        E4["guardrail_pii(text)"]
        E5["guardrail_policy(generated, persona)"]
        E6["guardrail_social_media_format(text, platform)"]
        E0 --> E1 & E2 & E3 & E4 & E5 & E6
    end

    subgraph "linkedin-mcp (publishing)"
        L1["linkedin_create_post(text, publication_key)"]
        L2["linkedin_create_comment(post_urn, text, key)"]
        L3["linkedin_validate_token()"]
        L4["linkedin_get_profile()"]
        L5["linkedin_get_profile_posts(count)"]
        L6["linkedin_get_post(post_id)"]
        L7["linkedin_get_publish_status(post_id)"]
        L8["linkedin_get_comments(post_urn)"]
        L9["linkedin_create_comment_reply(post_urn, parent_urn, text)"]
        L10["linkedin_enable_comments(post_urn)"]
        L11["linkedin_disable_comments(post_urn)"]
        L12["linkedin_get_audit()"]
    end
```

---

## 5. Guardrail Pipeline — Evaluation MCP

```mermaid
flowchart LR
    IN(["Generated Text<br/>+ Source Text"]) --> L1

    subgraph "6-Layer Guardrail Pipeline"
        L1["Layer 1<br/>Prompt Injection<br/>Regex + LLM classifier"]
        L2["Layer 2<br/>Factuality<br/>LLM claim grounding<br/>score 0→1"]
        L3["Layer 3<br/>Hallucination<br/>LLM unsupported statements<br/>score 0→1"]
        L4["Layer 4<br/>PII Detection<br/>Regex only<br/>(email, phone, SSN, card)"]
        L5["Layer 5<br/>Policy<br/>LLM toxicity + bias<br/>+ brand safety"]
        L6["Layer 6<br/>Format<br/>Char count, hashtags,<br/>unsafe URLs, duplicate hash"]

        L1 --> L2 --> L3 --> L4 --> L5 --> L6
    end

    L6 --> GATE{Deterministic<br/>Decision Gate}

    GATE -->|"PII found<br/>OR injection HIGH<br/>OR toxicity > 0.3"| BLOCK["BLOCK<br/>❌ Never published"]
    GATE -->|"political bias<br/>OR policy violations"| HR["HUMAN_REVIEW<br/>⚠️ Excluded from auto-publish"]
    GATE -->|"factuality < 0.50<br/>OR grounding < 0.50<br/>OR hallucination > 0.85"| REGEN["REGENERATE<br/>🔄 Retry summarize (max 2×)"]
    GATE -->|"all layers pass"| PASS["PASS<br/>✅ publish_eligible = True"]

    style BLOCK fill:#ef4444,color:#fff
    style HR fill:#f59e0b,color:#fff
    style REGEN fill:#8b5cf6,color:#fff
    style PASS fill:#22c55e,color:#fff
```

### Guardrail Thresholds (live production values)

| Layer | Metric | Threshold | Action on Fail |
|-------|--------|-----------|----------------|
| Prompt Injection | risk_level | `HIGH` or `MEDIUM` | BLOCK |
| Factuality | factuality_score | < 0.50 | REGENERATE |
| Factuality | grounding_score | < 0.50 | REGENERATE |
| Hallucination | hallucination_score | > 0.85 | REGENERATE |
| PII | pii_detected | `True` | BLOCK |
| Policy | toxicity_score | > 0.30 | BLOCK |
| Policy | political_bias_detected | `True` | HUMAN_REVIEW |
| Policy | policy_violations | non-empty | HUMAN_REVIEW |
| Format | char_count > 3000 | any violation | REGENERATE |

---

## 6. Data Models

```mermaid
classDiagram
    class NewsSummary {
        +str article_id
        +str headline
        +str summary
        +list~str~ key_points
        +str why_it_matters
        +str business_impact
        +str job_impact
        +str technology_impact
        +str policy_impact  "optional"
        +str source
        +str source_url
    }

    class PersonaType {
        <<enumeration>>
        BUSINESS
        LABOR
        POLICY
        GENZ
        LINKEDIN
    }

    class PersonaOutput {
        +PersonaType persona
        +str perspective
        +list~str~ evidence
        +str article_id
    }

    class PersonaSetOutput {
        +str article_id
        +PersonaOutput business
        +PersonaOutput labor
        +PersonaOutput policy
        +PersonaOutput genz
        +PersonaOutput linkedin
    }

    class EvaluationResult {
        +str article_id
        +EvaluationDecision decision
        +bool publish_eligible
        +float factuality
        +float groundedness
        +float hallucination
        +float relevance
        +float persona_adherence
        +float toxicity
        +str policy_check
        +list~str~ unsupported_claims
        +float overall_score
        +str notes
        +bool pii_detected
        +bool prompt_injection_detected
        +bool political_bias_detected
        +list~str~ guardrail_failures
        +dict layer_results
    }

    class EvaluationDecision {
        <<enumeration>>
        PASS
        FAIL
        REGENERATE
        BLOCK
        HUMAN_REVIEW
    }

    NewsSummary --> EvaluationResult : "source for evaluation"
    PersonaSetOutput --> EvaluationResult : "generated text for evaluation"
    EvaluationResult --> EvaluationDecision : "decision"
    PersonaSetOutput "1" *-- "5" PersonaOutput : "contains"
    PersonaOutput --> PersonaType : "persona type"
```

> **Note on `FAIL`:** The `EvaluationDecision.FAIL` enum value exists in `models/evaluation.py` but the current decision gate in `evaluation_agent.py` never emits it. The active set is PASS / REGENERATE / BLOCK / HUMAN_REVIEW.

---

## 7. LinkedIn Publishing Flow

```mermaid
sequenceDiagram
    participant WF as LangGraph Workflow
    participant PA as PublisherAgent
    participant LIMCP as linkedin-mcp
    participant LI as LinkedIn API

    WF ->> PA: publish(summary, personas, run_id, evaluation)

    alt evaluation.publish_eligible == False
        PA -->> WF: {status: "skipped", reason: "guardrail_block"}
    end

    PA ->> PA: _compose_main_post() → text (≤2990 chars)
    PA ->> PA: _make_publication_key() → idempotency key

    PA ->> LIMCP: linkedin_create_post(text, publication_key)

    alt publication_key already in _published_posts
        LIMCP -->> PA: {post_urn: cached_urn, idempotent: true}
    else new post
        LIMCP ->> LI: POST /rest/posts {author, commentary, visibility...}
        LI -->> LIMCP: HTTP 201 + x-restli-id: urn:li:share:xxxxx
        LIMCP -->> PA: {post_urn: "urn:li:share:xxxxx", status: "published"}
    end

    Note over PA,LIMCP: Comments API currently blocked (needs LinkedIn approval)<br/>All 5 personas are embedded in the main post body

    WF -->> WF: linkedin_results = [{post_urn, publication_key, status}]
```

---

## 8. OpenShift Deployment Layout

```mermaid
graph TB
    subgraph "OpenShift Cluster (aifeeders namespace)"

        subgraph "Ingress"
            Router["OpenShift Router<br/>(HAProxy)"]
        end

        subgraph "Application Layer"
            API["Deployment: daily-news-api<br/>replicas: 2<br/>image: daily-news:latest"]
        end

        subgraph "CronJob"
            CJ["CronJob: daily-ai-news<br/>schedule: 0 10 * * *<br/>label: app=daily-news-worker"]
        end

        subgraph "MCP Servers"
            NM["Deployment: news-mcp<br/>replicas: 2<br/>image: news-mcp:latest"]
            PM["Deployment: pageindex-mcp<br/>replicas: 1 ⚠️<br/>image: pageindex-mcp:latest"]
            EM["Deployment: evaluation-mcp<br/>replicas: 2<br/>image: evaluation-mcp:latest"]
            LM["Deployment: linkedin-mcp<br/>replicas: 1 ⚠️<br/>image: linkedin-mcp:latest"]
        end

        subgraph "Config"
            CM["ConfigMap: daily-news-config<br/>(thresholds, URLs, API versions)"]
            SEC["Secret: daily-news-secrets<br/>(tokens, API keys)"]
        end

        subgraph "Networking"
            NP1["NetworkPolicy: default-deny-all"]
            NP2["NetworkPolicy: allow-api-to-mcps<br/>(app=daily-news-api<br/>OR app=daily-news-worker)"]
            NP3["NetworkPolicy: allow-router-to-api"]
            NP4["NetworkPolicy: allow-router-to-linkedin-mcp<br/>(OAuth browser flow)"]
            NP5["NetworkPolicy: allow-egress-internet"]
        end

        subgraph "Platform"
            HPA["HPA: daily-news-api<br/>min:2 max:10<br/>CPU:70% / Mem:80%"]
            PDB["PodDisruptionBudget<br/>minAvailable:1<br/>(api, news-mcp, linkedin-mcp)"]
            RBAC2["ServiceAccount: daily-news<br/>Role: get/list ConfigMaps, Secrets, Pods"]
        end
    end

    Router -->|"HTTPS"| API
    Router -->|"HTTPS (OAuth)"| LM
    API --> CM & SEC
    CJ --> CM & SEC
    NM --> CM & SEC
    PM --> CM & SEC
    EM --> CM & SEC
    LM --> CM & SEC
    API & CJ & NM & PM & EM & LM --> RBAC

    style PM fill:#fef3c7
    style LM fill:#fef3c7
```

> ⚠️ `pageindex-mcp` and `linkedin-mcp` **must stay at replicas: 1** until backed by shared storage (Redis/PostgreSQL). Yellow = in-memory state constraint.
>
> **HPA applies only to `daily-news-api`** (stateless, CPU/memory-driven). MCP servers are not HPA'd — each has a fixed replica count.

---

## 9. Network Policy — Who Can Talk to Whom

```mermaid
graph LR
    Internet(["Internet / Browser"])
    Router["OpenShift Router"]

    API["daily-news-api<br/>app=daily-news-api"]
    Worker["CronJob Pod<br/>app=daily-news-worker"]

    NM["news-mcp<br/>role=mcp-server"]
    PM["pageindex-mcp<br/>role=mcp-server"]
    EM["evaluation-mcp<br/>role=mcp-server"]
    LM["linkedin-mcp<br/>role=mcp-server"]

    GNews(["GNews API"])
    LLM(["LLM Gateway"])
    LinkedInExt(["LinkedIn API"])

    Internet -->|"HTTPS"| Router
    Router -->|"HTTP :8000"| API
    Router -->|"HTTP :8000<br/>(OAuth endpoints)"| LM

    API -->|"HTTP :8000"| NM & PM & EM & LM
    Worker -->|"HTTP :8000"| NM & PM & EM & LM

    NM -->|"HTTPS egress"| GNews
    EM -->|"HTTPS egress"| LLM
    API -->|"HTTPS egress"| LLM
    LM -->|"HTTPS egress"| LinkedInExt

    style Internet fill:#e5e7eb
    style GNews fill:#e5e7eb
    style LLM fill:#e5e7eb
    style LinkedInExt fill:#e5e7eb
```

**Default-deny-all** is in place. Only the edges shown above are permitted.

---

## 10. LinkedIn Post Anatomy

> **Feed card rendering fix:** The headline is on its own line (line 2) so LinkedIn mobile always shows it in the feed card preview. Previously `🤖 AI NEWS  |  <headline>` was on one line — LinkedIn clipped at the emoji glyph, hiding the headline.

```mermaid
graph TB
    POST["LinkedIn Post (≤ 3000 chars)"]

    POST --> B0["🤖  AI NEWS (own line — feed card badge)"]
    POST --> B1["Headline (own line — always visible in feed card)"]
    POST --> B2["2-3 sentence summary"]
    POST --> B3["📌  Key Points\n  • fact 1\n  • fact 2\n  • fact 3"]
    POST --> B4["📈 Business Impact — ...\n👷 Workforce Impact — ...\n🔬 Tech Impact — ...\n🏛️ Policy Impact — ... (optional)"]
    POST --> B5["🔗  Source: url"]
    POST --> B6["──────────────────\n🧵  Perspectives"]
    POST --> B7["💼  Capitalist Mind\nperspective\n    ↳ evidence"]
    POST --> B8["👷  Working Professional Mind\n..."]
    POST --> B9["🏛️  Government Mind\n..."]
    POST --> B10["🎓  Young / Fresher Mind\n..."]
    POST --> B11["──────────────────\n🧠  Tech Strategist Mind\n(standalone practitioner section)"]
    POST --> B12["──────────────────\n⚠️ AI-simulated perspectives notice\n🤖 Built with AIFeeders"]
    POST --> B13["#AI #AgenticAI #LLM ... (11 hashtags)"]

    style B0 fill:#dbeafe
    style B1 fill:#dbeafe
    style B6 fill:#fef3c7
    style B11 fill:#ede9fe
    style B12 fill:#fee2e2
    style B13 fill:#f0fdf4
```

> **Why Tech Strategist Mind is separate:** The technical practitioner voice is qualitatively different from the four "societal impact" personas. Giving it its own section after the `──────────────────` divider signals to readers that they're getting a deeper technical take, not just another stakeholder view.

> **Why no numbers (1/4…4/4):** Removed. The emoji headers are visually distinct enough. Numbers added visual clutter without helping navigation in a single scrollable post.

---

## 11. Persona Voice Architecture — How `skills.md` Works

The five personas are not just different system prompts. They are defined in [`skills.md`](skills.md) with a full description of each persona's worldview, evidence style, and conversational voice. The prompt files load from `prompts/*.txt` at runtime.

```mermaid
graph TB
    subgraph "Persona Definition Layer"
        SK["skills.md\n(voice reference, anti-patterns,\nensemble design)"]
    end

    subgraph "Prompt Files (loaded at PersonaAgent init)"
        PC["prompts/capitalist.txt\nFounder/operator voice\nP&L lens + anti-patterns"]
        PL["prompts/labor.txt\nSlack-message energy\nNo HR-speak + anti-patterns"]
        PP["prompts/policy.txt\nPolicy memo precision\nNamed regulations + anti-patterns"]
        PG["prompts/genz.txt\nGroup chat energy\nSay WHY + anti-patterns"]
        PLI["prompts/linkedin.txt\nRFC comment style\nName the trade-off + anti-patterns"]
    end

    subgraph "PersonaAgent (one per persona)"
        PA["PersonaAgent.generate()\n→ LLM call\n→ persona enum injected\n  from agent context\n  (never from LLM output)"]
    end

    subgraph "Output"
        PO["PersonaOutput\n.persona (injected)\n.perspective (LLM)\n.evidence (LLM)"]
    end

    SK -.->|"reference when\ntuning prompts"| PC & PL & PP & PG & PLI
    PC & PL & PP & PG & PLI --> PA
    PA --> PO
```

### Why `persona` is always injected from the agent context

The `PydanticOutputParser` asks the LLM to fill in the `persona` field as an enum value (`"business"`, `"linkedin"`, etc.). When a prompt says `Persona: Tech Strategist Mind`, some LLMs fill the field with the display name instead of the internal key. This causes a `KeyError` when building `PersonaSetOutput`.

**Fix (applied in `persona_agent.py`):**
```python
# After LLM parse, always override persona + article_id from agent context:
return PersonaOutput(
    persona=self._persona,        # ← from PersonaAgent.__init__, never from LLM
    article_id=summary.article_id,
    perspective=raw.perspective,
    evidence=raw.evidence,
)
```

### Anti-pattern architecture

Each prompt file contains **two sections**:
1. **✗ Anti-patterns** — exact bad outputs taken from real live runs. These are the sentences the LLM actually produced before the prompts were strengthened.
2. **✓ Good examples** — the target voice, taken from `skills.md` sample monologues.

When a persona voice drifts back to corporate-speak, add the bad output as a new `✗` line in the relevant `.txt` file, then rebuild.

### Persona ensemble design

The five personas are designed to create productive tension:

| Tension | What it illuminates |
|---------|-------------------|
| Capitalist vs. Working Professional | Value capture vs. value creation |
| Government vs. Capitalist | Regulation cost vs. opportunity |
| Young/Fresher vs. Tech Strategist | Entry-level perspective vs. practitioner depth |
| Tech Strategist vs. Capitalist | Build-vs-buy decision vs. unit economics |

No single persona has the full picture. The value is in the ensemble.

---

## 12. Technology Stack

```mermaid
graph LR
    subgraph "Language & Runtime"
        PY["Python 3.11"]
        UBI["Red Hat UBI9"]
    end

    subgraph "AI / Agents"
        LGR["LangGraph 1.x<br/>(state machine)"]
        LCH["LangChain 1.x<br/>(LLM chains)"]
        OAI["langchain-openai<br/>(OpenAI-compatible client)"]
        LFU["Langfuse<br/>(LLM tracing)"]
    end

    subgraph "APIs & Protocols"
        FA["FastAPI + Uvicorn<br/>(REST + health)"]
        MCP["MCP (Streamable HTTP)<br/>(tool protocol)"]
        HX["httpx<br/>(async HTTP client)"]
    end

    subgraph "Config & Models"
        PD["Pydantic v2<br/>(data models)"]
        PS["pydantic-settings<br/>(env config)"]
    end

    subgraph "Observability"
        OT["OpenTelemetry<br/>(disabled — no collector)"]
        PM["Prometheus<br/>(metrics endpoint)"]
    end

    subgraph "Platform"
        OS["OpenShift 4.x"]
        K8S["Kubernetes batch/v1<br/>(CronJob)"]
        HELM["Helm 3<br/>(multi-cloud chart)"]
    end
```

---

## 13. CI/CD and Image Build Flow

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant OC as oc CLI
    participant IS as ImageStream (OpenShift)
    participant Build as BuildConfig
    participant Deploy as Deployment

    Dev ->> OC: oc start-build daily-news --from-dir=. --follow
    OC ->> Build: Stream source code to BuildConfig
    Build ->> Build: docker build (UBI9/Python 3.11)
    Build ->> IS: Push image → image-registry.../aifeeders/daily-news:latest
    Build -->> OC: Build complete

    Dev ->> OC: oc rollout restart deployment/daily-news-api
    OC ->> Deploy: Trigger rolling update
    Deploy ->> IS: Pull latest image
    Deploy -->> OC: Rollout complete (new pods healthy)

    Note over Dev,Deploy: CronJob always uses :latest tag<br/>No explicit restart needed for CronJob
```

---

## 14. Summary: All Components at a Glance

| Component | Type | Replicas | State | Calls |
|-----------|------|----------|-------|-------|
| `daily-news-api` | Deployment | 2 (HPA: 2–10) | Stateless | LangGraph graph (BackgroundTask) |
| `daily-ai-news` | CronJob | 1 per run | Stateless | LangGraph graph (ainvoke) |
| `news-mcp` | Deployment | 2 | Stateless | GNews API |
| `pageindex-mcp` | Deployment | **1** | In-memory doc store | — |
| `evaluation-mcp` | Deployment | 2 | Stateless | LLM Gateway (verify=False) |
| `linkedin-mcp` | Deployment | **1** | In-memory token + idempotency registry | LinkedIn Posts API |
| `SummaryAgent` | LangGraph node | — | Stateless | pageindex-mcp (context), LLM via ChatOpenAI |
| `PersonaAgentFactory` | LangGraph node | — | Stateless | pageindex-mcp (evidence), LLM × 5 **parallel** |
| `EvaluationAgent` | LangGraph node | — | Stateless | evaluation-mcp (POST /call), deterministic gate |
| `PublisherAgent` | LangGraph node | — | Stateless | linkedin-mcp (POST /call), **no LLM** |

**Bold replicas = must stay at 1** due to in-memory state.

### Why this architecture? — Architect's Q&A

**Q: Why 4 separate MCP servers instead of one monolith?**
A: Each server owns one integration boundary and its own credentials. `linkedin-mcp` holds the LinkedIn token; no agent ever sees it. `evaluation-mcp` can be replaced or upgraded independently without touching the news pipeline. Each server is testable in isolation with a single `curl POST /call`.

**Q: Why MCP at all — why not direct Python function calls?**
A: The MCP boundary makes each service independently deployable, independently scalable (within its replica constraints), and independently testable. It also enforces the security principle: agents never hold external API keys.

**Q: Why `POST /call` REST instead of MCP streaming transport?**
A: The MCP SDK streaming transport (`/mcp` endpoint) is complex to debug and adds overhead for synchronous request-response tool calls. `MCPHTTPClient.call()` in `src/daily_news/mcp/client.py` is 15 lines of plain httpx — trivial to debug, trace, and test.

**Q: Why is `PublisherAgent` an agent at all if it makes no LLM calls?**
A: Because it is a workflow node that makes decisions (eligibility gate, character budgeting, composition logic) and has a side effect (POST to LinkedIn). "Agent" here means "autonomous unit with a specific responsibility" — not "LLM-calling agent". Keeping it separate from the LangGraph graph makes the publishing logic testable without running the full graph.

**Q: Why `pageindex-mcp` instead of a real vector database?**
A: Vector databases add operational complexity (deployment, embeddings model, indexing latency). For a single-article-per-run pipeline, keyword-overlap section scoring gives sufficient evidence retrieval. The MCP boundary means swapping to a vector DB later requires changing only `pageindex-mcp/server.py`.

**Q: Why run 5 persona LLM calls in parallel?**
A: Each persona call is stateless and independent. Parallelism reduces `generate_personas` latency from ~50s sequential to ~12s. The 5 traces all share the same `run_id`-seeded trace in Langfuse so they appear as siblings in the session view.

**Q: Why is the evaluation threshold calibration important?**
A: `qwen2-5-72b-instruct` self-evaluates news summaries conservatively — factuality scores cluster at 0.5–0.7, not 0.9+. Setting thresholds at 0.90 means every article fails and nothing publishes. Setting them at 0.50/0.50/0.85 allows content to pass while still blocking genuinely low-quality output.

**Q: Why enrich the evaluation source text with `NewsSummary` fields?**
A: GNews free plan truncates article content to ~250 chars. The `evaluation-mcp` was comparing 500-char persona output against ~250 chars of source text, causing `hallucination_score=1.0` and permanent REGENERATE loops. `_enrich_source()` in `EvaluationAgent` combines the raw content with all structured `NewsSummary` fields (headline, summary, key_points, business/job/tech impacts) giving the evaluator enough grounded context. Typical post-fix scores: `factuality≈0.85`, `hallucination≈0.40`.

**Q: Why is the LinkedIn Comments API soft-skipped instead of raising an error?**
A: The `partnerApiSocialActions.CREATE` 403 is a **permanent, known** error — the "Community Management API" product is not yet approved. Raising an ERROR log 5 times per run created false alarm fatigue and obscured real failures. The fix: first PERMISSION_ERROR sets `_comments_blocked=True`, logs INFO once (`"personas are embedded in post body"`), and all subsequent comment calls are skipped without any API round-trip. The pipeline completes cleanly.

**Q: Why put the headline on its own line (line 2) instead of the same line as the emoji?**
A: LinkedIn mobile's feed card renderer clips the first line of a post at the emoji glyph when the line is long. `🤖 AI NEWS  |  <headline>` was being displayed as just `🤖 AI NEWS` with everything after the emoji gap appearing blank. Separating them guarantees the feed card preview always shows both `🤖 AI NEWS` (line 0) and the full headline (line 1).

**Q: Why support two GNews API keys (`GNEWS_API_KEY` + `GNEWS_API_KEY_2`)?**
A: The GNews free plan allows 100 requests/day per key. A single key is exhausted after ~16 manual test runs (6 requests/run). Key rotation is transparent to the workflow — `news-mcp` detects HTTP 403, switches to the secondary key in-process, and retries the same call. Health endpoint shows `active_key_index` so operators can see which key is active without checking logs.

**Q: What is `skills.md` and why does it exist alongside the prompt files?**
A: `skills.md` is the human-readable reference document for each persona's voice, worldview, and evidence style. The `prompts/*.txt` files are the machine-readable instructions derived from it. `skills.md` exists because LLM personas drift over time — when a persona starts sounding generic, you need a clear description of what "good" looks like to recalibrate the prompt. The anti-pattern examples in each `.txt` file are taken directly from real bad outputs recorded in `skills.md`. Without this separation, there's no authoritative source to return to when quality degrades.

**Q: Why were the persona numbers (1/4…4/4) removed from post headers?**
A: They were added originally to give readers a sense of progress through the perspectives section. In practice, the emoji headers (`💼`, `👷`, `🏛️`, `🎓`) are visually distinct enough to separate sections. The numbers added clutter and made the headers harder to scan quickly. Removed in v1.4.
