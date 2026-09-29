# AGENTIC.md — AIFeeders System Flow

Complete agentic pipeline for the AIFeeders LinkedIn AI News platform.
Every stage, agent, tool call, decision gate, and retry path is documented here.

---

## Architecture Overview

AIFeeders is a **LangGraph state-machine** with 13 nodes running as a directed acyclic graph (with one conditional back-edge for retries). A single daily run produces one or more LinkedIn posts — each a structured multi-persona AI debate grounded in a verified news article.

```
GNews API ──► LangGraph Workflow ──► LinkedIn API
                    │
              Jev System One          (scoring, routing, evaluation)
              PageIndex MCP           (vectorless RAG evidence)
              Langfuse                (tracing every LLM call)
```

---

## LLM Gateway

All agents share a **single configuration-driven gateway** — no model names, URLs, or API keys are hardcoded anywhere.

| Setting | Current value | Purpose |
|---|---|---|
| `LLM_BASE_URL` | IBM OpenShift gateway `/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` | `qwen2-5-72b-instruct` | Generation model |
| `LLM_API_KEY` | Bearer token | Auth |
| `LLM_SSL_VERIFY` | `false` | IBM internal gateway uses self-signed cert |
| `LLM_TIMEOUT` | `120` | Request timeout (seconds) |

**To switch providers** — change `.env` only, zero code changes:
```
# Anthropic (future)
LLM_BASE_URL=https://api.anthropic.com/v1
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-3-5-sonnet-20241022
LLM_SSL_VERIFY=true
```

All LLM construction is centralised in [`src/daily_news/config/llm_factory.py`](src/daily_news/config/llm_factory.py).

---

## Workflow State

```python
NewsWorkflowState(TypedDict):
    run_id                 # unique UUID for this run (Langfuse session key)
    raw_articles           # GNews raw results
    deduplicated_articles  # after 3-pass dedup + InputGuardrail
    selected_articles      # after full content fetch
    pageindex_documents    # PageIndex RAG evidence tree
    jev_prefilter_scores   # dict[article_id → full Jev Stage 3 intelligence]
    jev_persona_hints      # warm persona signal from prefilter (list[str])
    jev_active_personas    # confirmed active personas from jev_router (list[str])
    summaries              # NewsSummary per article (serialised dicts)
    persona_outputs        # PersonaSetOutput per article (serialised dicts)
    evaluation_results     # EvaluationResult per article (serialised dicts)
    retry_count            # REGENERATE loop counter (max 3)
    approval_status        # "PENDING" | "APPROVED" | "REJECTED"
    reach_scores           # ReachScore summary_line() dict per article
    linkedin_results       # post URNs + comment URNs from LinkedIn MCP
    optimization_diagnosis # ContentOptimizer PerformanceDiagnosis per article
    story_mutations        # StoryMutation variants for learning loop
    workflow_status        # current stage label string
    errors                 # non-fatal error log (list[str])
```

---

## Pipeline Stages

### Stage 1 — DISCOVER
**Node:** `discover_news`
**Agent/Tool:** [`NewsMCPClient`](src/daily_news/mcp/news.py)
**What it does:** Fires 9 GNews search queries across topic buckets (AI technology, business, jobs, policy, products, chips, model releases, M&A). Serialised at 1.2 s/query to respect GNews free-tier rate limit.
**Output:** `raw_articles` — up to 90 candidate articles.

---

### Stage 2 — DEDUPLICATE
**Node:** `deduplicate`
**Agent/Tool:** [`InputGuardrail`](src/daily_news/agents/guardrails.py), [`PublishedStore`](src/daily_news/agents/published_store.py)
**What it does:**
- Pass 1: URL-hash exact dedup (normalises http/https, www, trailing slash)
- Pass 2: Cross-run dedup — drops articles already published today via `PublishedStore`
- Pass 3: Jaccard title-similarity dedup (threshold 0.55) — catches same story from different sources
- InputGuardrail: PII scan + prompt injection check on every remaining article
**Output:** `deduplicated_articles` — clean, novel articles only.

---

### Stage 3 — FETCH
**Node:** `fetch_articles`
**Agent/Tool:** [`NewsMCPClient.fetch_article()`](src/daily_news/mcp/news.py)
**What it does:** Fetches full article content for up to 30 deduplicated articles in parallel (Semaphore(10)).
**Output:** `selected_articles` — articles enriched with full content.

---

### Stage 4 — INDEX
**Node:** `index_pageindex`
**Agent/Tool:** [`PageIndexMCPClient.index_document()`](src/daily_news/mcp/pageindex.py)
**What it does:** Indexes each article into PageIndex (vectorless RAG). Runs in parallel (Semaphore(10)). Produces a structured evidence tree — key facts, business claims, technical details — retrieved later by summary and persona agents.
**Output:** `pageindex_documents`

---

### Stage 5 — ANALYZE (Jev Prefilter)
**Node:** `jev_prefilter`
**Agent/Tool:** [`jev_prefilter_articles()`](src/daily_news/agents/jev_agents.py) → [`JevClient`](src/daily_news/mcp/jev_client.py)
**What it does:** Jev System One scores every article on 9 intelligence dimensions:
- `sentiment_polarity` · `emotion` (curiosity/excitement/concern/urgency)
- `impact` (enterprise/developers/infrastructure/business/policy/general_public)
- `novelty` · `trend_velocity` · `audience_relevance`
- `event_type` · `significance` · `controversy_level`
- `estimated_engagement`

Selects the highest-scoring article as today's post. Falls back to first article when `JEV_ENABLED=false`.
**Output:** `jev_prefilter_scores` (dict keyed by article_id), `jev_persona_hints`

---

### Stage 6 — EXPLAIN (Summarize)
**Node:** `summarize`
**Agents:** [`JudgmentAgent`](src/daily_news/agents/judgment_agent.py), [`MediaStorytellerAgent`](src/daily_news/agents/summary_agent.py), [`SummaryAgent`](src/daily_news/agents/summary_agent.py)
**Tool:** [`PageIndexMCPClient.get_relevant_sections()`](src/daily_news/mcp/pageindex.py)

Three-phase pipeline per article (all articles processed in parallel):

```
Phase 1 (parallel):
    PageIndex.get_relevant_sections()   ← structured evidence
    JudgmentAgent.analyze()             ← facts / claims / uncertainties / what-not-to-conclude

Phase 2 (sequential, needs Phase 1):
    MediaStorytellerAgent.extract_story()
        ← hook, analogy, perspective, second_order_effect, narrative_style
        ← calibrated by Jev emotion/novelty/controversy signals
        ← bounded by JudgmentAgent facts (never asserts beyond verified facts)

Phase 3 (sequential, needs Phase 2):
    SummaryAgent.summarize()
        ← headline, key_points, business/job/technology/policy impacts
        ← story context sharpens framing
        ← builds NewsIntelligence backbone
```

**Output:** `summaries` — list of `NewsSummary` (serialised dicts)

---

### Stage 7 — ANGLE (Jev Find Angle)
**Node:** `find_angle`
**Agent/Tool:** [`jev_find_angle()`](src/daily_news/agents/jev_agents.py) → [`JevClient`](src/daily_news/mcp/jev_client.py)
**What it does:** Jev analyses the structured `NewsSummary` to identify:
- `common_narrative` — what everyone else is covering
- `missing_angle` — the perspective that's absent in current coverage
- `recommended_audience` — who will most engage with this article
- `discussion_question` — the sharpest CTA question Jev can derive

Writes `content_opportunity` into `jev_prefilter_scores` for the publisher to use.
**Output:** Updated `jev_prefilter_scores`

---

### Stage 8 — ROUTE (Jev Router)
**Node:** `jev_router`
**Agent/Tool:** [`jev_route_personas()`](src/daily_news/agents/jev_agents.py) → [`JevClient`](src/daily_news/mcp/jev_client.py)
**What it does:** Selects which of the 4 personas are most relevant for this article's content DNA. Returns `jev_active_personas` — only these run in the next stage, reducing unnecessary LLM calls.
**Output:** `jev_active_personas` (list of PersonaType values)

---

### Stage 9 — CREATE (Generate Personas)
**Node:** `generate_personas`
**Agent/Tool:** [`PersonaAgentFactory`](src/daily_news/agents/persona_agent.py) → [`PersonaAgent`](src/daily_news/agents/persona_agent.py) × 4
**Tool:** [`PageIndexMCPClient.get_relevant_sections()`](src/daily_news/mcp/pageindex.py)

Runs only the Jev-selected active personas in parallel (`asyncio.gather`). Each `PersonaAgent` generates one perspective grounded in:
- Story context (hook, analogy, second-order effect, narrative style)
- Intelligence signals (novelty, emotion, impact, controversy)
- Judgment boundaries (verified facts only — no hallucinated certainty)
- `avoid_phrases` — banned phrases from the previous evaluation cycle (retry path)

**Personas:**
| Persona | Voice | Temperature |
|---|---|---|
| 💼 Business (Founder) | Opportunity · commercial premise · market timing | 0.4 |
| 🧑‍💻 LinkedIn (Engineer) | Operational reality · architecture · complexity trade-offs | 0.4 |
| ⚖️ GenZ (Analyst) | Market dynamics · who benefits at scale · switching cost | 0.4 |
| 🏛️ Policy (Lead) | Governance · accountability · concentration risk | 0.4 |

**Output:** `persona_outputs` — list of `PersonaSetOutput`

---

### Stage 10 — OPTIMISE (LinkedIn Skills)
**Node:** `linkedin_optimize`
**Agent:** [`LinkedInSkillsOptimizer`](src/daily_news/agents/linkedin_skills_optimizer.py)

Three deterministic + LLM passes applied to the assembled post:

```
Pass 1 — Hook Selector
    Scores current hook against 2026 heuristics (number-first > contrarian > false-binary)
    If score < 0.72: generates up to 3 alternatives via LLM, replaces if stronger

Pass 2 — Humanizer (deterministic, no LLM)
    Removes reveal bridges ("The result?", "Here's what")
    Reduces AI-vocabulary density (leverage, streamline, fundamentally…)
    Caps staccato fragment runs (≤ 2 standalone short lines)
    Strips engagement-bait closers ("What do you think?", "Tag someone")

Pass 3 — Audit (LLM)
    Scores: hook_strength · commentability · ai_style_density · cta_quality · algorithm_compliance
    Returns AuditResult → stored as linkedin_audit in persona_dict for the judge
```

**Output:** Updated `persona_outputs` with `linkedin_audit` scores attached

---

### Stage 11 — EVALUATE (Quality Gate)
**Node:** `evaluate`
**Agent:** [`EvaluationAgent`](src/daily_news/agents/evaluation_agent.py)
**Tools:** [`JevClient.evaluate_content()`](src/daily_news/mcp/jev_client.py) or [`EvaluationMCPClient`](src/daily_news/mcp/evaluation.py)

Three-layer evaluation:

```
Layer 0 — Deterministic Pre-scan (Python, no LLM)
    Scans all persona texts for banned openers + banned inline phrases
    Any hit → immediate REGENERATE (injecting the offending phrase as avoid_phrase)

Layer 1 — Jev / MCP Quality Scoring
    factuality · groundedness · hallucination (0–1 floats)
    Falls back: Jev → MCP → neutral scores (0.6/0.6/0.3) when both fail

Layer 2 — LLM-as-a-Judge (independent model, temperature=0.1)
    Checks: genuine intellectual clash · article-specific claims · grounded facts
    Also checks EU AI Act / GDPR off-scope drops
    REVISE only when ALL THREE quality conditions fail simultaneously
```

**Gate decisions:**

| Decision | Condition | Next |
|---|---|---|
| `PASS` | All thresholds met, judge approves | → `score_reach` |
| `REGENERATE` | Below threshold or judge flags boilerplate | → `generate_personas` (retry, max 3) |
| `BLOCK` | PII or prompt injection detected | → END (never published) |
| `HUMAN_REVIEW` | Political bias or policy check failed | → `approval` queue |

**Output:** `evaluation_results`

---

### Stage 12 — REACH SCORE (Pre-publish Gate)
**Node:** `score_reach`
**Agent:** [`ReachScoreAgent`](src/daily_news/agents/reach_score_agent.py) (deterministic, no LLM)

Scores post on 6 LinkedIn organic-reach dimensions (0–100):

| Dimension | Weight | What it checks |
|---|---|---|
| Hook strength | 20 pts | First 220 chars — number-first, named entity, structural tension |
| Specificity | 20 pts | Stats, named entities, source link, bulleted facts |
| Question quality | 20 pts | Forced-choice CTA, numbered options |
| Length fit | 15 pts | 150–300 words optimal |
| Bait penalty | 15 pts | Engagement-bait patterns, excess hashtags |
| Topic coherence | 10 pts | AI/tech domain signal presence |

If `score < 55` → auto-repair applied (hashtag trim, bait removal, word-count clip) — no LLM call.

**Output:** `reach_scores`, updated `persona_outputs` (repaired posts)

---

### Stage 13 — PUBLISH
**Node:** `publish`
**Agent:** [`PublisherAgent`](src/daily_news/agents/publisher_agent.py)
**Tool:** [`LinkedInMCPClient`](src/daily_news/mcp/linkedin.py)
**Sub-agent:** [`GrammarAgent`](src/daily_news/agents/grammar_agent.py), [`ComicGenerator`](src/daily_news/agents/comic_generator.py)

Publishing flow:
```
1. GrammarAgent.correct(post_text)          ← final proofread (temperature=0.0)
2. ComicGenerator.generate_comic()          ← SVG → PNG debate strip (6-panel)
3. LinkedInMCPClient.create_post(text, image)
   └─ Returns post_urn from x-restli-id header (HTTP 201)
4. LinkedInMCPClient.create_comment(post_urn, persona_text) × 4
   └─ Sequential, never parallel — MCP-level delay between each comment
5. PublishedStore.mark_published(article_id) ← prevents re-posting same article
```

**Gate:** `PUBLISHING_ENABLED=false` skips LinkedIn calls entirely (smoke-test mode).

**Output:** `linkedin_results` — post URNs, comment URNs, image path

---

### Stage 14 — LEARN (Optimize Content)
**Node:** `optimize_content`
**Agent:** [`ContentOptimizerAgent`](src/daily_news/agents/content_optimizer.py)
**Tool:** [`LinkedInMCPClient.get_post_analytics()`](src/daily_news/mcp/linkedin.py)

Closed-loop performance loop (runs after publish):
```
1. Fetch engagement analytics (impressions/reactions/comments/reposts)
2. ContentOptimizerAgent.diagnose_performance()
   ← identifies weakest component: hook | storytelling | audience | perspective | dialogue | question
3. ContentOptimizerAgent.mutate_story()
   ← generates 3 alternative narrative approaches for the learning loop
   ← stored as story_mutations for future Jev angle calibration
```

**Output:** `optimization_diagnosis`, `story_mutations`

---

## Retry Loop

```
evaluate
  └─ REGENERATE?
       ├─ retry_count < 3 → generate_personas (avoid_phrases injected)
       │    └─ linkedin_optimize → evaluate
       └─ retry_count ≥ 3 → publish PASS articles only, drop the rest
```

Only `generate_personas` is re-run on retry — **not** `find_angle` or `summarize`. Those are deterministic for a given article and re-running them wastes 8–15 s of LLM budget.

---

## Observability

Every LLM call is traced in **Langfuse** with:
- `session_id = run_id` — all traces from a single run are linked
- `tags = [agent_name, app_env]`
- `metadata = {article_id, model, agent}`

Configure: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`

---

## File Map

```
src/daily_news/
├── config/
│   ├── settings.py          ← all env-var definitions (single source of truth)
│   └── llm_factory.py       ← make_llm() / make_eval_llm() (single place for ChatOpenAI)
│
├── agents/                  ← one file per agent, skills.md per agent
│   ├── summary_agent.py         MediaStorytellerAgent + SummaryAgent
│   ├── judgment_agent.py        JudgmentAgent
│   ├── persona_agent.py         PersonaAgent + PersonaAgentFactory
│   ├── evaluation_agent.py      EvaluationAgent (generator-evaluator separation)
│   ├── grammar_agent.py         GrammarAgent
│   ├── publisher_agent.py       PublisherAgent (deterministic, no LLM)
│   ├── reach_score_agent.py     ReachScoreAgent (deterministic, no LLM)
│   ├── linkedin_skills_optimizer.py  LinkedInSkillsOptimizer
│   ├── content_optimizer.py     ContentOptimizerAgent
│   ├── comic_generator.py       ComicGenerator (SVG/PNG, no LLM)
│   ├── sentiment_resolver.py    resolve_sentiment() (deterministic)
│   ├── jev_agents.py            jev_prefilter / jev_find_angle / jev_route_personas
│   └── guardrails.py            InputGuardrail + OutputGuardrail
│
├── mcp/                     ← one file per external tool/MCP server
│   ├── news.py              NewsMCPClient    (GNews)
│   ├── pageindex.py         PageIndexMCPClient
│   ├── evaluation.py        EvaluationMCPClient
│   ├── linkedin.py          LinkedInMCPClient
│   ├── jev_client.py        JevClient
│   └── client.py            jev_singleton (shared connection pool)
│
├── models/                  ← Pydantic data models
│   ├── news.py, summary.py, persona.py, evaluation.py, intelligence.py
│
├── workflows/
│   └── daily_news_graph.py  ← LangGraph state machine (13 nodes)
│
├── api/                     ← FastAPI routes (trigger, approval, status)
│   └── routes/workflow.py, approval.py, news.py
│
└── observability/
    ├── tracing.py           Langfuse callbacks
    └── metrics.py           OTEL metrics
```
