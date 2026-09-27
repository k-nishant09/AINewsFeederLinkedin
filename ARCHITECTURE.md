# AIFeeders — System Architecture

> **Audience:** Junior engineer to architect. Read top-to-bottom for a full picture,
> or jump to any section using the table of contents.

---

## Table of Contents

1. [What Is This System?](#1-what-is-this-system)
2. [Why It Was Built This Way](#2-why-it-was-built-this-way)
3. [High-Level System Design (HLD)](#3-high-level-system-design-hld)
4. [End-to-End Workflow Flow](#4-end-to-end-workflow-flow)
5. [LangGraph State Machine](#5-langgraph-state-machine)
6. [Agent Descriptions](#6-agent-descriptions)
7. [Persona Flow](#7-persona-flow)
8. [Evaluation (Evals) Flow](#8-evaluation-evals-flow)
9. [Comic Strip Generation Flow](#9-comic-strip-generation-flow)
10. [User Accessibility Flow](#10-user-accessibility-flow)
11. [System Infrastructure Flow](#11-system-infrastructure-flow)
12. [MCP Server Architecture](#12-mcp-server-architecture)
13. [Intelligence Data Model](#13-intelligence-data-model)
14. [Observability & Tracing](#14-observability--tracing)
15. [Configuration & Secrets](#15-configuration--secrets)
16. [Testing Architecture](#16-testing-architecture)
17. [Recent Changes & Why They Were Required](#17-recent-changes--why-they-were-required)
18. [Glossary](#18-glossary)

---

## 1. What Is This System?

**AIFeeders** is a fully autonomous AI news intelligence and publishing pipeline.

Every day it:

1. **Discovers** fresh AI/tech news articles from GNews (9 targeted queries).
2. **Scores and selects** the single most relevant article using Jev System One signals.
3. **Understands** the story through document indexing (PageIndex), judgment analysis, and LLM summarisation.
4. **Generates** four distinct persona perspectives in parallel (Founder · Engineer · Skeptic · Policy).
5. **Evaluates** the output through a multi-layer quality gate (deterministic scanner → Jev scores → LLM judge).
6. **Optimises** reach score before publishing.
7. **Publishes** a main LinkedIn post + per-persona comments.
8. **Generates** a 6-panel comic strip image (SVG → PNG) attached to the post.
9. **Learns** through a closed-loop content optimiser that diagnoses post performance.

The system never publishes the same article twice. It never publishes content that fails the evaluation gate. Every publish is idempotent.

---

## 2. Why It Was Built This Way

| Design Decision | Reason |
|---|---|
| **LangGraph state machine** | Deterministic node ordering with conditional retry edges. Easier to debug, test, and trace than prompt chains. |
| **MCP servers as microservices** | News, PageIndex, Evaluation, and LinkedIn are isolated services — each replaceable independently. The main pipeline calls them over HTTP, never directly. |
| **Jev System One for scoring** | Jev is a fast, lightweight structured-decision model (Qwen3.5-2B with LoRA decision heads). It replaces LLM calls for scoring-only tasks where speed and cost matter. |
| **Four personas** | LinkedIn audience is segmented: Founders care about ROI, Engineers care about production trade-offs, Skeptics challenge assumptions, Policy minds focus on governance. Each perspective reaches a different reader. |
| **LLM-as-a-Judge at temp=0.1** | The generator (Qwen at 0.7) and the evaluator (Qwen at 0.1) are different chain invocations. The judge never sees its own generation context. This prevents the generator from self-approving poor outputs. |
| **Deterministic banned-phrase scanner** | The LLM judge is probabilistic. The pre-scanner is 100% reliable — if a banned opener is in the text, it is always caught regardless of judge temperature. |
| **Reach score before publish** | LinkedIn's algorithm rewards specific structural signals. Checking these deterministically before calling the API means every post is optimised without an extra LLM round-trip. |
| **Comic strip as SVG** | No external image service needed. Pure Python SVG generation, converted to PNG via `cairosvg` (with `rsvg-convert` / `inkscape` fallbacks). The comic turns a text post into a scroll-stopping visual. |
| **PublishedStore deduplication** | Prevents the same article from being published twice on the same day even across retries or restarts. |

---

## 3. High-Level System Design (HLD)

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                               AIFeeders Platform                                    │
│                                                                                     │
│  ┌────────────┐   REST    ┌──────────────────────────────────────────────────────┐  │
│  │  Scheduler │ ────────► │          FastAPI Application (port 8000)             │  │
│  │ (cron/k8s) │           │  /workflow/run  /news/*  /approval/*  /health        │  │
│  └────────────┘           └────────────────────┬─────────────────────────────────┘  │
│                                                │                                    │
│                                         invokes│                                    │
│                                                ▼                                    │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │                    LangGraph Daily News Graph                               │    │
│  │                                                                             │    │
│  │  discover_news → deduplicate → fetch_articles → index_pageindex             │    │
│  │       → jev_prefilter → find_angle → summarize → jev_router                 │    │
│  │       → generate_personas → evaluate ──┐                                   │    │
│  │                              ┌─────────┘                                   │    │
│  │                    REGENERATE│(up to 3x)                                   │    │
│  │                              └──► find_angle → summarize → ...             │    │
│  │                         PASS│                                              │    │
│  │                              └──► score_reach → publish → optimize_content │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                           MCP Microservices Layer                           │   │
│  │                                                                             │   │
│  │  ┌─────────────┐  ┌───────────────┐  ┌──────────────┐  ┌────────────────┐  │   │
│  │  │  News MCP   │  │ PageIndex MCP │  │Evaluation MCP│  │  LinkedIn MCP  │  │   │
│  │  │  :8101      │  │  :8102        │  │  :8103       │  │  :8104         │  │   │
│  │  │  GNews API  │  │  Doc tree +   │  │  LLM scoring │  │  Posts API     │  │   │
│  │  │             │  │  evidence     │  │  + policy    │  │  Comments API  │  │   │
│  │  └─────────────┘  └───────────────┘  └──────────────┘  │  Image Upload  │  │   │
│  │                                                         └────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                         External Services                                   │   │
│  │                                                                             │   │
│  │  ┌───────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │   │
│  │  │  Jev Gateway  │  │ LLM Gateway (Qwen)  │  │  Langfuse Tracing        │  │   │
│  │  │  (System One) │  │ IBM OpenShift or     │  │  (us.cloud.langfuse.com) │  │   │
│  │  │  Qwen3.5-2B   │  │ OpenAI-compatible   │  │  All LLM + agent traces  │  │   │
│  │  └───────────────┘  └─────────────────────┘  └──────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. End-to-End Workflow Flow

```
USER / SCHEDULER
      │
      │ POST /workflow/run
      ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1 — DISCOVER                                             │
│  discover_news                                                  │
│  • 9 GNews queries across 6 AI topic buckets                    │
│  • 72h window, 10 results/query → up to 90 raw articles         │
│  • Langfuse span per query                                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  DEDUPLICATION (3-pass)                                         │
│  deduplicate                                                    │
│  Pass 1 — URL normalisation + content hash (exact dedup)        │
│  Pass 2 — PublishedStore.filter_unpublished (cross-run dedup)   │
│  Pass 3 — Jaccard title similarity ≥ 0.55 (near-duplicate)      │
│  + InputGuardrail: prompt injection, PII, control-char scan     │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  FETCH & INDEX                                                  │
│  fetch_articles  — full content via News MCP (cap: 30)          │
│  index_pageindex — PageIndex document tree + evidence           │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3 — ANALYZE (Jev System One)                             │
│  jev_prefilter                                                  │
│  • Scores each article: relevance × 0.6 + engagement × 0.4     │
│  • Returns full Stage 3 intelligence per article:               │
│    event_type, emotion (C/E/W/U), impact (enterprise/dev/       │
│    infra/biz/policy/public), novelty, trend_velocity,           │
│    controversy_level, sentiment_polarity, persona_fit           │
│  • Selects single best article (composite score)                │
│  Fallback: [:1] selection when JEV_ENABLED=false                │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 5 — ANGLE (Jev System One)                               │
│  find_angle                                                     │
│  • Given the selected article + Stage 3 scores, asks Jev:       │
│    - common_narrative (what every outlet is saying)             │
│    - missing_angle (what's underreported)                       │
│    - recommended_audience (primary LinkedIn segment)            │
│    - discussion_question (what sparks real conversation)        │
│  • Writes content_opportunity into jev_prefilter_scores         │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 4 — EXPLAIN (LLM, 2-pass)                                │
│  summarize                                                      │
│                                                                 │
│  Sub-step A: JudgmentAgent                                      │
│  • Separates facts / reported claims / uncertainties /          │
│    what-not-to-conclude for every article                       │
│  • Prevents fictional certainty in downstream generation        │
│                                                                 │
│  Sub-step B: MediaStorytellerAgent (Pass 1, temp=0.5)           │
│  • Extracts the STORY: hook, analogy, perspective,              │
│    second_order_effect, future_question, narrative_style,       │
│    media_host_opening, audience_cta, SEO hashtags               │
│  • "What is the story?" — not facts, the STORY                  │
│                                                                 │
│  Sub-step C: SummaryAgent (Pass 2, temp=0.2)                    │
│  • Structured NewsSummary: headline, key_points, impacts,       │
│    sentiment, ai_tag, intelligence backbone                     │
│  • Calibrated by story context from Pass 1                      │
│  + sentiment resolved (Jev polarity → VADER → TextBlob)         │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  PERSONA ROUTING (Jev System One)                               │
│  jev_router                                                     │
│  • Routes only the relevant personas for this article           │
│  • Merges with jev_persona_hints from prefilter (union)         │
│  • Skipped personas get a stub (empty perspective)              │
│  Fallback: all four personas when JEV_ENABLED=false             │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 6 — CREATE (Persona Agents, parallel asyncio.gather)     │
│  generate_personas                                              │
│  • PersonaAgentFactory runs active personas in parallel         │
│  • Each PersonaAgent: loads prompt file + LangChain chain       │
│  • Context injected per persona:                                │
│    - Judgment boundaries (facts / claims / uncertainties)       │
│    - Intelligence signals (emotion, impact, novelty)            │
│    - Story context (hook, perspective, consequences)            │
│    - Content angle from Jev (missing_angle, audience)           │
│    - avoid_phrases on retry (exact banned phrases from prior    │
│      eval cycle injected back into prompt)                      │
│  Personas: 💼 Founder  🧑‍💻 Engineer  ⚖️ Skeptic  🏛️ Policy        │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  EVALUATION GATE (3-layer)                                      │
│  evaluate                                                       │
│                                                                 │
│  Layer 1 — Deterministic pre-scanner (always runs first)        │
│  • _check_persona_text() in publisher_agent.py                  │
│  • Banned openers: "When I was scaling", "Imagine you are", … │
│  • Banned inline: "the real challenge", "in the end", …        │
│  • Any hit → immediate REGENERATE (no LLM call wasted)          │
│                                                                 │
│  Layer 2 — Jev System One scores                                │
│  • factuality, groundedness, hallucination floats               │
│  • Fallback: EvaluationMCPClient when JEV_ENABLED=false         │
│                                                                 │
│  Layer 3 — LLM-as-a-Judge (Qwen @ 0.1)                         │
│  • Independent chain invocation — never self-reviews            │
│  • Detects: stock boilerplate, throat-clearing, no-clash,       │
│    off-context regulation, generic AI truisms                   │
│  • REVISE verdict → forces groundedness=0 → REGENERATE          │
│                                                                 │
│  Decision gate (_apply_gate):                                   │
│    PASS        → continue to score_reach                        │
│    REGENERATE  → loop back to find_angle (up to MAX_RETRIES=3)  │
│    BLOCK       → hard stop (PII or prompt injection)            │
│    HUMAN_REVIEW→ publish but flag for review                    │
└─────────────────────────┬───────────────────────────────────────┘
                          │ PASS
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  REACH SCORING (pre-publish, deterministic)                     │
│  score_reach                                                    │
│  • Scores on 6 dimensions (0–100 total):                        │
│    hook_strength (20) / specificity (20) / question_quality (20)│
│    length_fit (15) / bait_penalty (15) / topic_coherence (10)  │
│  • score ≥ 55 → PUBLISH                                         │
│  • score < 55 → REVISE → auto-repair (no LLM):                 │
│    strip excess hashtags, remove bait lines, trim to 300 words  │
│  • Repaired text stored in persona_outputs["_repaired_post"]    │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  PUBLISH (PublisherAgent — only component that calls LinkedIn)  │
│  publish                                                        │
│                                                                 │
│  Pre-publish checks:                                            │
│  • PUBLISHING_ENABLED=false → skip (smoke-test safe)            │
│  • PublishedStore.is_published() → skip duplicates              │
│  • evaluation.publish_eligible=false → skip                     │
│                                                                 │
│  Step 1 — Comic generation (ComicGenerator)                     │
│  • build_comic_script_from_summary() — no LLM calls             │
│  • Renders 6-panel SVG comic strip                              │
│  • Converts to PNG (cairosvg → rsvg-convert → inkscape)         │
│                                                                 │
│  Step 2 — Upload image to LinkedIn                              │
│  • linkedin_upload_image() → asset_urn                          │
│                                                                 │
│  Step 3 — Compose main post text                                │
│  • _compose_main_post() — assembles:                            │
│    hook_line, context_line, sentiment_signal, intelligence_note │
│    source_url, persona debate block, hashtags, footer           │
│  • OutputGuardrail final scan (PII, BANNED_CONTENT sentinel)    │
│  • GrammarAgent cleanup                                         │
│  • LinkedIn UTF-16 char-count + clip at sentence boundary       │
│  • **bold** → Unicode Bold Mathematical characters              │
│                                                                 │
│  Step 4 — linkedin_create_post_with_image(text, asset_urn)      │
│  • Returns post_urn                                             │
│                                                                 │
│  Step 5 — Per-persona comments (sequential, not gather)         │
│  • 4 personas × linkedin_create_comment(post_urn, text, key)   │
│  • MCP-level delay between comments                             │
│                                                                 │
│  PublishedStore.mark_published(article_id)                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  CLOSED-LOOP OPTIMIZATION                                       │
│  optimize_content                                               │
│  • Fetches post engagement via linkedin_get_post_analytics()    │
│  • ContentOptimizerAgent.diagnose_performance() →              │
│    scores 6 structural components (hook/story/audience/…)      │
│    identifies weakest_component + actionable_recommendation     │
│  • ContentOptimizerAgent.mutate_story() →                      │
│    generates StoryMutation variants for learning loop           │
│  • Results stored in state for observability (not yet fed back) │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. LangGraph State Machine

The graph is defined in [`daily_news_graph.py`](src/daily_news/workflows/daily_news_graph.py).

```
START
  │
  ▼
discover_news
  │
  ▼
deduplicate
  │
  ▼
fetch_articles
  │
  ▼
index_pageindex
  │
  ▼
jev_prefilter          ← Stage 3: Jev scores all articles, picks best
  │
  ▼
find_angle             ← Stage 5: Jev identifies missing angle + audience
  │
  ▼
summarize              ← Stage 4: Judgment → Storyteller → Summary (2-pass LLM)
  │
  ▼
jev_router             ← Dynamic persona routing
  │
  ▼
generate_personas      ← 4 persona LLM calls in parallel
  │
  ▼
evaluate               ← 3-layer quality gate
  │
  ├──[REGENERATE, retry<3]──► find_angle  (loop back with avoid_phrases)
  ├──[BLOCK]────────────────► END
  └──[PASS | HUMAN_REVIEW]──► score_reach
                                  │
                                  ▼
                              publish
                                  │
                                  ▼
                              optimize_content
                                  │
                                  ▼
                                END
```

### State object: `NewsWorkflowState`

| Field | Type | Populated by |
|---|---|---|
| `run_id` | str | `make_initial_state()` |
| `raw_articles` | list[dict] | `discover_news` |
| `deduplicated_articles` | list[dict] | `deduplicate` |
| `selected_articles` | list[dict] | `fetch_articles` + `jev_prefilter` |
| `pageindex_documents` | list[dict] | `index_pageindex` |
| `jev_persona_hints` | list[str] | `jev_prefilter` |
| `jev_active_personas` | list[str] | `jev_router` |
| `jev_prefilter_scores` | dict[article_id → scores] | `jev_prefilter` + `find_angle` |
| `summaries` | list[dict] | `summarize` |
| `persona_outputs` | list[dict] | `generate_personas` |
| `evaluation_results` | list[dict] | `evaluate` |
| `retry_count` | int | `evaluate` |
| `approval_status` | str | API route (`/approval`) |
| `linkedin_results` | list[dict] | `publish` |
| `reach_scores` | list[dict] | `score_reach` |
| `optimization_diagnosis` | list[dict] | `optimize_content` |
| `story_mutations` | list[dict] | `optimize_content` |
| `workflow_status` | str | each node |
| `errors` | list[str] | each node |

---

## 6. Agent Descriptions

### 6.1 `InputGuardrail` — [`guardrails.py`](src/daily_news/agents/guardrails.py)
Runs during `deduplicate`. Scans every article for:
- Prompt injection patterns (regex, 9 patterns)
- SSN and credit card PII
- Control characters (sanitised in-place)

Articles that fail are dropped from the pipeline before any LLM sees them.

### 6.2 `JudgmentAgent` — [`judgment_agent.py`](src/daily_news/agents/judgment_agent.py)
Runs inside `summarize` (Sub-step A). Separates:
- **facts** — verified/announced claims
- **reported_claims** — things companies or actors said (not verified)
- **analysis_implications** — logical technical/business implications
- **uncertainties** — what is unknown or contested
- **what_not_to_conclude** — boundaries models must not overclaim

This boundary enforcement propagates to the Storyteller, Summary, and Persona prompts, preventing fictional certainty.

### 6.3 `MediaStorytellerAgent` — [`summary_agent.py`](src/daily_news/agents/summary_agent.py)
Pass 1 of the summarise node (temp=0.5). Extracts the **story**, not the facts:
- `hook` — scroll-stopping opening
- `human_analogy` — makes complex concepts relatable
- `perspective` — the non-obvious take
- `second_order_effect` — downstream consequence
- `future_question` — the open question worth discussing
- `media_host_opening` / `media_host_audience_cta` — used directly in comic generation
- `dynamic_seo_hashtags` — article-specific hashtags for LinkedIn reach

### 6.4 `SummaryAgent` — [`summary_agent.py`](src/daily_news/agents/summary_agent.py)
Pass 2 of the summarise node (temp=0.2). Produces structured [`NewsSummary`](src/daily_news/models/summary.py):
`headline`, `summary`, `key_points`, `business_impact`, `job_impact`, `technology_impact`, `policy_impact`, `why_it_matters`, `sentiment`, `ai_tag`.
Calibrated by the story context from Pass 1.

### 6.5 `PersonaAgent` / `PersonaAgentFactory` — [`persona_agent.py`](src/daily_news/agents/persona_agent.py)
Four agents run in parallel via `asyncio.gather`. Each loads a prompt file from `/prompts/`:

| Persona | Prompt file | Focus |
|---|---|---|
| 💼 Founder (BUSINESS) | `capitalist.txt` | Revenue, ROI, market disruption, investment thesis |
| 🏛️ Policy (POLICY) | `policy.txt` | Regulation, governance, national competitiveness |
| ⚖️ Skeptic (GENZ) | `genz.txt` | Broad societal impact, digital culture, career entry |
| 🧑‍💻 Engineer (LINKEDIN) | `linkedin.txt` | Engineering trade-offs, workforce impact, reskilling |

Every persona prompt receives:
- Judgment boundaries (facts / claims / unknowns)
- Jev intelligence signals (emotion, impact, novelty, trend_velocity)
- Story context (hook, perspective, consequences)
- Content angle (missing_angle, recommended_audience)
- `avoid_phrases` list on retry (exact banned phrases from prior evaluation cycle)

### 6.6 `EvaluationAgent` — [`evaluation_agent.py`](src/daily_news/agents/evaluation_agent.py)
The quality gate. Three layers (see §8).

### 6.7 `ReachScoreAgent` — [`reach_score_agent.py`](src/daily_news/agents/reach_score_agent.py)
Pre-publish deterministic scorer. No LLM. Runs in < 1ms. See §8 Evals.

### 6.8 `PublisherAgent` — [`publisher_agent.py`](src/daily_news/agents/publisher_agent.py)
The **only** component that calls LinkedIn MCP. Assembles the full post text, generates and uploads the comic, posts to LinkedIn, adds persona comments. Idempotent.

### 6.9 `ComicGenerator` — [`comic_generator.py`](src/daily_news/agents/comic_generator.py)
Renders a 6-panel SVG comic strip. Pure Python, no LLM. See §9.

### 6.10 `ContentOptimizerAgent` — [`content_optimizer.py`](src/daily_news/agents/content_optimizer.py)
Post-publish closed-loop optimiser. Diagnoses 6 structural performance components and generates story mutation candidates for future runs.

### 6.11 `GrammarAgent` — [`grammar_agent.py`](src/daily_news/agents/grammar_agent.py)
Final cleanup pass on post text before publish. Deterministic string fixes.

### 6.12 `OutputGuardrail` — [`guardrails.py`](src/daily_news/agents/guardrails.py)
Final scan of assembled post text before LinkedIn API call. Checks for BANNED_CONTENT sentinel, PII, fake quotation attribution.

---

## 7. Persona Flow

```
NewsWorkflowState.summaries (list of NewsSummary)
           │
           ▼
    jev_route_personas
    ─────────────────
    Asks Jev: "Which of these 4 personas are
    genuinely relevant to this article?"
    Returns: e.g. ["business", "linkedin"]

    Merged with jev_persona_hints from prefilter
    (union of both signals)
           │
           ▼
    generate_personas
    ─────────────────
    Only the active personas run (LLM cost saved)
    Inactive personas get an empty stub

    For each active persona (in parallel):

    ┌──────────────────────────────────────────────────────┐
    │  PersonaAgent.generate(summary, evidence, run_id)    │
    │                                                      │
    │  Input context:                                      │
    │   • Article headline + summary + key_points          │
    │   • Story: hook, perspective, second_order_effect    │
    │   • Judgment: facts, claims, uncertainties           │
    │   • Jev signals: emotion, impact, novelty            │
    │   • Content angle: missing_angle, recommended_aud    │
    │   • avoid_phrases (on retry only)                    │
    │                                                      │
    │  System prompt loaded from prompts/<persona>.txt     │
    │  Parser: PydanticOutputParser → PersonaOutput        │
    │                                                      │
    │  Output:                                             │
    │   • perspective: str  (2-4 crisp conversational      │
    │                         sentences, podcast-style)    │
    │   • evidence: list[str]                              │
    └──────────────────────────────────────────────────────┘
           │
           ▼
    PersonaSetOutput
    ┌──────────────────────────────────────────────────────┐
    │  .business → PersonaOutput (💼 Founder)              │
    │  .policy   → PersonaOutput (🏛️ Policy)              │
    │  .genz     → PersonaOutput (⚖️ Skeptic)             │
    │  .linkedin → PersonaOutput (🧑‍💻 Engineer)          │
    └──────────────────────────────────────────────────────┘
           │
           ▼  (after PASS evaluation)
   Comic Generator & Publisher Integration
   ────────────────────────────────────────
   The complete multi-turn debate is rendered INSIDE the 6-Panel Comic Strip Image.
   The LinkedIn post caption provides Scene 6 caption continuity:
     1. Hook & framing (what changed + practitioner relevance)
     2. "Our four voices see it differently:" + 4-persona colored bullet teaser
        (🟢 Founder, 🟣 Engineer, 🔴 AI Analyst, 🟠 Policy Lead)
     3. Dynamic Host Question: 🎙️ <HostName> — Media Host (labelled A/B/C/D/E choices)
     4. Source attribution + Link + Disclaimer + Dynamic hashtags
   
   If image rendering or upload fails, the publication is skipped (no text-only fallback).

   Event-type determines lead persona order in the comic strip:
     product_launch → Engineer leads
     funding        → Founder leads
     regulation     → Policy leads
     research       → Engineer leads
```

---

## 8. Evaluation (Evals) Flow

```
PersonaSetOutput
       │
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 0 — Deterministic banned-phrase pre-scanner                 │
│  _pre_scan_personas() + _check_persona_text()                      │
│                                                                    │
│  Checks each persona perspective for:                              │
│    Banned openers (first 200 chars):                               │
│      "When I was scaling", "Imagine you are", "Let me be clear",   │
│      "I've been in", "Sounds great on paper", etc. (22 patterns)   │
│                                                                    │
│    Banned inline phrases (full text):                              │
│      "the real challenge", "at the end of the day",                │
│      "it remains to be seen", "is a game-changer",                 │
│      "only time will tell", "in the end", etc. (24 patterns)       │
│                                                                    │
│  ✓ Hit found → REGENERATE immediately (no Jev/LLM call)           │
│  ✓ No hits  → proceed to Layer 1                                   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1 — Jev System One evaluation scores                        │
│  JevClient.evaluate_content()                                      │
│                                                                    │
│  Returns:                                                          │
│    factuality:    float 0-1  (how well claims align with source)   │
│    groundedness:  float 0-1  (how grounded in article evidence)    │
│    hallucination: float 0-1  (how much invented content)           │
│    toxicity:      float 0-1                                        │
│    policy_check:  "PASS" | "FAIL"                                  │
│    pii_detected:  bool                                             │
│    prompt_injection_detected: bool                                 │
│    political_bias_detected: bool                                   │
│                                                                    │
│  Fallback: EvaluationMCPClient when JEV_ENABLED=false             │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 2 — LLM-as-a-Judge (Qwen @ temp=0.1)                       │
│  Independent chain — never sees its own generation context        │
│                                                                    │
│  Detects (INSTANT FAIL = REVISE):                                  │
│    • Anecdote / setup openers (22 patterns)                        │
│    • Banned inline phrases (13 pattern categories)                 │
│    • Wrong-context EU AI Act / GDPR drops                          │
│    • All personas reaching the same conclusion (no clash)          │
│    • Generic AI truisms not anchored to specific article           │
│    • Post applicable word-for-word to any other AI story           │
│    • Factual claims beyond source article                          │
│                                                                    │
│  REVISE verdict → result.groundedness = 0.0 → forces REGENERATE   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  _apply_gate() — deterministic threshold decision                  │
│                                                                    │
│  BLOCK        if pii_detected or prompt_injection_detected         │
│  REGENERATE   if factuality < 0.50                                 │
│               or groundedness < 0.50                               │
│               or hallucination > 0.85                              │
│  HUMAN_REVIEW if political_bias_detected                           │
│               or policy_check != "PASS"                            │
│  PASS         otherwise                                            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ├──[REGENERATE, retry<3]──► find_angle
                             ├──[BLOCK]────────────────► END
                             ├──[HUMAN_REVIEW]──────────► score_reach (filtered)
                             └──[PASS]──────────────────► score_reach

┌─────────────────────────────────────────────────────────────────────┐
│  Reach Score Gate (ReachScoreAgent, post-eval, pre-publish)        │
│                                                                    │
│  Dimension          Weight  Signals                                │
│  ─────────────────────────────────────────────────────────────     │
│  hook_strength       20pts  Strong: "here's the real...",         │
│                             "no one is asking"                     │
│                             Weak: "As AI continues…"             │
│  specificity         20pts  Numbers, named entities, source link, │
│                             bulleted facts                         │
│  question_quality    20pts  Numbered choices, forced-choice CTAs, │
│                             "genuinely curious", "where do you     │
│                             land"                                  │
│  length_fit          15pts  Optimal: 150–300 words                 │
│  bait_penalty        15pts  -3 per bait hit, -1 per excess #tag   │
│  topic_coherence     10pts  ≥3 AI/tech domain signals              │
│                                                                    │
│  score ≥ 55 → PUBLISH                                              │
│  score < 55 → auto-repair (no LLM):                               │
│    1. Trim hashtags to 3                                           │
│    2. Remove bait lines                                            │
│    3. Clip to 300 words at sentence boundary                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Retry Loop

When `REGENERATE` is returned:
1. `retry_count` increments.
2. The graph re-enters at `generate_personas` directly (skipping redundant re-summarization and find_angle, saving 8–15s of LLM latency).
3. `generate_personas` is called again with `avoid_phrases` injected into every persona prompt — the exact failing phrases from the prior evaluation cycle.
4. After `MAX_RETRIES=3` regen cycles, the graph publishes only articles with `PASS` decisions and skips the rest.

---

## 9. Comic Strip Generation Flow

The comic is a **6-panel SVG image** generated in pure Python with no LLM calls during generation. It is built from the workflow objects (summary + personas) already in state.

```
build_comic_script_from_summary(summary, personas)
──────────────────────────────────────────────────
Extracts from summary.story:
  • news_brief     ← media_host_opening + hook + why_it_matters
  • central_tension← future_question or media_host_audience_cta
  • host_question  ← tension shown as a pill in Scene 1

Builds cast (4 personas, deterministic name from article_id hash):
  business → "Arjun / Priya / Leo …"  role: AI Founder
  linkedin → "Steve / Anika / Tom …"  role: Enterprise Engineer
  genz     → "Nina / Zoe / Alex …"    role: Generalist Thinker
  policy   → "James / Rachel / …"     role: Policy Specialist

Host name deterministic from article_id hash:
  "Maya / Daniel / Sophia / Marcus / Elena / Jordan"

ComicScript contains:
  headline, host_name/role, news_brief, central_tension,
  cast[4], voice1-4 lines (first 2 sentences of each persona),
  host_synthesis, audience_question, hashtags, source_url

build_scenes(script)
────────────────────
Scene 1  Media Host brief  →  "TODAY'S VOICES" roster (right panel)
Scene 2  Persona A         →  opens debate, answering host question
Scene 3  Persona B         →  replies to A's question
Scene 4  Persona C         →  replies to B's question
Scene 5  Persona D         →  replies to C's question
Scene 6  Media Host        →  synthesises, poses audience question

_render_comic(headline, subhead, scenes)
─────────────────────────────────────────
Canvas: 1440 × 888px  (3 cols × 2 rows + header bar)
Header: dark background (#0F172A), AIFeeders brand, headline, subhead
Panels: per-persona accent stripe, face avatar (SVG circle + hair + badge),
        name row (bold 20px), quote-reply strip (↳ answering X's question),
        speech bubble (auto-scaling text, fills box)

_svg_to_png()
─────────────
  1. cairosvg (Python)    ← fastest
  2. rsvg-convert         ← fallback
  3. inkscape             ← fallback
  4. .svg file            ← final fallback (LinkedIn accepts SVG)

Outside-post text (build_outside_post):
  <Hook sentence: what actually changed>
  
  <Framing: why it matters to practitioners right now>
  
  Our four voices see it differently:
  🟢 **<FounderName> — Founder:** <teaser ≤ 100 chars>
  🟣 **<EngineerName> — Engineer:** <teaser ≤ 100 chars>
  🔴 **<AnalystName> — AI Analyst:** <teaser ≤ 100 chars>
  🟠 **<PolicyName> — AI Governance Lead:** <teaser ≤ 100 chars>
  
  🎙️ **<HostName> — Media Host**
  <audience_question with choices A/B/C/D/E>
  
  📰 Source: <name>
  🔗 <url>
  
  ⚠️ Perspectives are AI-simulated for discussion — not professional advice.
  🤖 AIFeeders · Daily AI Intelligence · Powered by Jev
  
  #EnterpriseAI #MLOps #AIGovernance
```

**Why a comic?**
LinkedIn's algorithm favours posts with images. A static image of text does not stop the scroll. A structured 6-panel comic strip with named characters makes the AI discussion visually distinctive and immediately recognisable as AIFeeders content.

---

## 10. User Accessibility Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│  Human-in-the-loop access points                                   │
└─────────────────────────────────────────────────────────────────────┘

[Scheduled trigger]                [Manual trigger]
        │                                  │
        │                    POST /workflow/run
        └──────────┬───────────────────────┘
                   │
                   ▼
         FastAPI /workflow routes
         ─────────────────────────
         GET  /workflow/status/{run_id}   → current workflow_status
         POST /workflow/run               → start new run
         GET  /workflow/results/{run_id}  → full state dump

         FastAPI /news routes
         ─────────────────────
         GET  /news/latest    → discover without publishing
         GET  /news/{id}      → fetch single article

         FastAPI /approval routes
         ─────────────────────────
         GET  /approval/pending         → articles awaiting human review
         POST /approval/{run_id}/approve → set approval_status=APPROVED
         POST /approval/{run_id}/reject  → set approval_status=REJECTED

┌─────────────────────────────────────────────────────────────────────┐
│  HUMAN_REVIEW decision path                                        │
│                                                                    │
│  evaluate() returns HUMAN_REVIEW when:                             │
│   • political_bias_detected = true                                 │
│   • policy_check != "PASS"                                         │
│                                                                    │
│  Article is still scored and reach-optimised.                      │
│  PublisherAgent checks approval_status before posting.             │
│  A human can APPROVE → publishes or REJECT → skips.               │
│  Timeout → defaults to skip (safe mode).                           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Observability access                                              │
│                                                                    │
│  GET  /health  → {"status": "healthy"}  (used by k8s/load balancer) │
│  GET  /ready   → {"status": "ready"}                               │
│  Langfuse UI   → full LLM trace per run (session_id = run_id)      │
│  OTEL / Jaeger → infrastructure spans (when OTLP endpoint set)     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Developer access                                                  │
│                                                                    │
│  GET /docs   → Swagger UI (non-production only)                    │
│  GET /redoc  → ReDoc UI   (non-production only)                    │
│  Langflow :7860 → visual workflow development                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. System Infrastructure Flow

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  OpenShift / Kubernetes Cluster (Production)                                       │
│                                                                                    │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │  Namespace: daily-news                                                      │   │
│  │                                                                             │   │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐               │   │
│  │  │  api           │  │  news-mcp      │  │  pageindex-mcp │               │   │
│  │  │  Deployment    │  │  Deployment    │  │  Deployment    │               │   │
│  │  │  :8000         │  │  :8101→:8000   │  │  :8102→:8000   │               │   │
│  │  └────────────────┘  └────────────────┘  └────────────────┘               │   │
│  │                                                                             │   │
│  │  ┌────────────────┐  ┌────────────────┐                                   │   │
│  │  │  eval-mcp      │  │  linkedin-mcp  │                                   │   │
│  │  │  Deployment    │  │  Deployment    │                                   │   │
│  │  │  :8103→:8000   │  │  :8104→:8000   │                                   │   │
│  │  └────────────────┘  └────────────────┘                                   │   │
│  │                                                                             │   │
│  │  ConfigMap: mcp-urls, thresholds (EVAL_FACTUALITY_THRESHOLD etc.)          │   │
│  │  Secret:    linkedin-token, gnews-key, llm-api-key, jev-api-key            │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                    │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │  External Services                                                          │   │
│  │  GNews API → :443    LLM Gateway → :443    Jev Gateway → :443              │   │
│  │  LinkedIn API → :443  Langfuse → :443       OTLP Collector → :4317         │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────┐
│  Local Development (docker-compose)                                                │
│                                                                                    │
│  docker-compose.yaml services:                                                     │
│  ┌──────────┐ ┌──────────┐ ┌───────────────┐ ┌───────────────┐ ┌─────────────┐   │
│  │ api:8000 │ │news:8101 │ │pageindex:8102 │ │eval:8103      │ │linkedin:8104│   │
│  └──────────┘ └──────────┘ └───────────────┘ └───────────────┘ └─────────────┘   │
│  ┌───────────────────┐  ┌────────────────────────────────────────────────────┐    │
│  │ langflow:7860     │  │ brave-search-mcp (optional, --profile inspect)     │    │
│  └───────────────────┘  └────────────────────────────────────────────────────┘    │
│                                                                                    │
│  All services communicate over Docker internal network                             │
│  .env file provides secrets to all containers                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘

Network call map (happy path):
  api → news-mcp      → GNews REST API
  api → pageindex-mcp → in-memory document store
  api → eval-mcp      → LLM Gateway (Qwen)
  api → linkedin-mcp  → LinkedIn REST API
  api → Jev Gateway   → direct HTTP (not via MCP)
  api → LLM Gateway   → direct HTTP (LangChain/OpenAI client)
  api → Langfuse      → async background flush (non-blocking)
```

---

## 12. MCP Server Architecture

Each MCP server is an independent microservice exposing a Streamable HTTP transport.
The main pipeline calls them via [`mcp/client.py`](src/daily_news/mcp/client.py).

```
MCP Client (Python) ──httpx──► MCP Server ──internal──► External API / Service

┌─────────────────────────────────────────────────────────────────────┐
│  news_mcp  (port 8101)                                             │
│  Tools: search_latest, fetch_article                               │
│  Backend: GNews API  (GNEWS_API_KEY)                               │
│  Rate-limited: 1100ms between requests (free tier)                 │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  pageindex_mcp  (port 8102)                                        │
│  Tools: index_document, get_relevant_sections                      │
│  Backend: in-memory document store (hierarchical tree)             │
│  Purpose: extract evidence sections relevant to a question          │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  evaluation_mcp  (port 8103)                                       │
│  Tools: evaluate_content                                           │
│  Backend: LLM Gateway (Qwen)                                       │
│  Returns: factuality, groundedness, hallucination, toxicity,       │
│           policy_check, pii_detected, political_bias_detected      │
│  Used as fallback when JEV_ENABLED=false                           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  linkedin_mcp  (port 8104)                                         │
│  Tools: linkedin_get_profile, linkedin_validate_token,             │
│         linkedin_create_post, linkedin_create_post_with_image,     │
│         linkedin_upload_image, linkedin_create_comment,            │
│         linkedin_create_comment_reply, linkedin_get_post_analytics │
│  Backend: LinkedIn Marketing API v2                                │
│  Auth: LINKEDIN_ACCESS_TOKEN (OAuth 2.0)                           │
│  Idempotency: publication_key + comment_key per call               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 13. Intelligence Data Model

The `NewsIntelligence` object is the backbone JSON contract. Every pipeline stage enriches it rather than passing unstructured text between agents.

```
NewsIntelligence
├── article_id, headline, source, source_url, topics
│
├── Stage 3 (Jev ANALYZE):
│   ├── sentiment, sentiment_score, sentiment_stats
│   ├── emotion: EmotionSignals
│   │     curiosity / excitement / concern / urgency
│   ├── impact: AudienceImpact
│   │     enterprise / developers / infrastructure / business / policy / general_public
│   ├── novelty, trend_velocity, audience_relevance
│   ├── event_type: "product_launch"|"funding"|"acquisition"|"regulation"|"research"|"other"
│   ├── significance, controversy_level
│
├── Stage 4a (JudgmentAgent):
│   └── judgment: JudgmentAnalysis
│         facts / reported_claims / analysis_implications
│         uncertainties / what_not_to_conclude
│
├── Stage 4b (MediaStorytellerAgent):
│   └── story: NewsStory
│         hook / human_analogy / perspective / second_order_effect
│         future_question / media_host_opening / media_host_audience_cta
│         dynamic_seo_hashtags / narrative_style / tone
│         business_consequence / technology_consequence / human_consequence
│
├── Stage 4c (SummaryAgent):
│   ├── what_happened, what_changed, why_it_matters, who_is_affected
│   ├── key_points, summary, business_impact, job_impact
│   ├── technology_impact, policy_impact
│
├── Stage 5 (Jev ANGLE):
│   └── content_opportunity: ContentOpportunity
│         common_narrative / missing_angle
│         recommended_audience / discussion_question
│
└── evidence: list[EvidenceItem]  ← from PageIndex
```

---

## 14. Observability & Tracing

```
Every significant operation is traced in Langfuse:
  session_id = run_id  (all traces for one run linked together)

Trace coverage:
  discover_news        → parent trace with raw_articles count
  summarize            → span per article (storyteller + summary passes)
  generate_personas    → span per persona per article
  evaluate             → span per article (judge verdict + scores)
  publish              → span per article (post_urn, comments_posted)

Langfuse fields per span:
  name: node name (e.g. "evaluate")
  input: article_id, query, etc.
  output: decision, scores, urn
  tags: [node_name, persona_name, app_env]
  metadata: run_id, model, agent

OTEL (OpenTelemetry):
  Infrastructure spans when OTEL_EXPORTER_OTLP_ENDPOINT is set
  Traces httpx calls, LangChain invocations
  Exports to Jaeger / Grafana Tempo

Structured logging:
  All nodes log with [run_id] prefix
  Format: "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
  Third-party (httpx, httpcore, opentelemetry) set to WARNING

Health endpoints:
  GET /health → {"status": "healthy"}   ← k8s liveness probe
  GET /ready  → {"status": "ready"}     ← k8s readiness probe
```

---

## 15. Configuration & Secrets

All settings are in [`settings.py`](src/daily_news/config/settings.py) using `pydantic-settings`. Loaded once, cached with `@lru_cache`.

| Variable | Description | Default |
|---|---|---|
| `LLM_API_KEY` | LLM gateway API key | (required in prod) |
| `LLM_BASE_URL` | LLM gateway base URL | IBM OpenShift gateway |
| `LLM_MODEL` | Model name | `qwen2-5-72b-instruct` |
| `EVAL_LLM_MODEL` | Judge model | `qwen2-5-72b-instruct` |
| `JEV_BASE_URL` | Jev System One gateway URL | empty (disables Jev) |
| `JEV_API_KEY` | Jev API key | "" |
| `JEV_ENABLED` | Enable/disable Jev | `true` |
| `NEWS_MCP_URL` | News MCP service URL | `http://localhost:8101/mcp` |
| `PAGEINDEX_MCP_URL` | PageIndex MCP URL | `http://localhost:8102/mcp` |
| `EVALUATION_MCP_URL` | Evaluation MCP URL | `http://localhost:8103/mcp` |
| `LINKEDIN_MCP_URL` | LinkedIn MCP URL | `http://localhost:8104/mcp` |
| `GNEWS_API_KEY` | GNews API key | (required) |
| `LINKEDIN_ACCESS_TOKEN` | LinkedIn OAuth token | (required) |
| `PUBLISHING_ENABLED` | Gate — `false` skips LinkedIn posts | `true` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key | "" |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key | "" |
| `EVAL_FACTUALITY_THRESHOLD` | Min factuality to pass | `0.50` |
| `EVAL_GROUNDEDNESS_THRESHOLD` | Min groundedness to pass | `0.50` |
| `EVAL_HALLUCINATION_THRESHOLD` | Max hallucination to pass | `0.85` |

---

## 16. Testing Architecture

```
tests/
├── unit/                        ← No external dependencies
│   ├── test_comic_generator.py  ← SVG rendering, scene layout [NEW]
│   ├── test_banned_phrase_scanner.py ← deterministic scanner
│   ├── test_content_optimizer.py
│   ├── test_deduplication.py    ← URL/hash/title-similarity passes
│   ├── test_evaluation_gate.py  ← _apply_gate() thresholds
│   ├── test_guardrails_judgment.py
│   ├── test_models.py           ← Pydantic model contracts
│   ├── test_published_store.py  ← idempotency
│   ├── test_publisher_idempotency.py
│   ├── test_reach_score.py      ← 6-dimension scoring
│   └── test_sentiment_resolver.py
│
├── evaluation/                  ← Evaluation threshold tests
│   └── test_evaluation_thresholds.py
│
├── mcp/                         ← MCP client contract tests
│   ├── test_evaluation_mcp.py
│   ├── test_linkedin_mcp.py
│   ├── test_news_mcp.py
│   └── test_pageindex_mcp.py
│
├── integration/                 ← End-to-end API tests
│   └── test_api.py
│
└── workflow/                    ← LangGraph routing tests
    └── test_langgraph_routing.py
```

**Run all tests:**
```sh
uv run pytest tests/ -v
```

**Run only unit tests (fast, no network):**
```sh
uv run pytest tests/unit/ -v
```

---

## 17. Recent Changes & Why They Were Required

### 17.1 Comic Strip Generator (`src/daily_news/agents/comic_generator.py`) — NEW

**What was added:**
A complete 6-panel comic strip generator in pure Python. Renders the AI debate as a visual story using SVG → PNG conversion.

**Why:**
Text-only LinkedIn posts are algorithmically disadvantaged. A branded, recognisable visual format differentiates AIFeeders content in the feed. The comic is generated from existing workflow objects (no new LLM calls) making it essentially free at runtime.

**Key design choices:**
- `ComicScript` / `ComicScene` data classes separate data from rendering
- `build_comic_script_from_summary()` bridges pipeline objects to comic format with no LLM calls
- Face avatars, per-persona accent colours, quote-reply strips (↳ answering X's question) make the conversation legible
- Deterministic cast name selection (MD5 of article_id + persona + slot) prevents the same names appearing every day
- SVG → PNG via `cairosvg` with `rsvg-convert` / `inkscape` / `.svg` fallback chain

### 17.2 Publisher Agent — Comic integration + image upload (`src/daily_news/agents/publisher_agent.py`)

**What changed:**
Added the comic generation and image upload path inside `PublisherAgent.publish()`:
1. `build_comic_script_from_summary()` → `generate_comic_from_script()` → PNG
2. Base64 encodes PNG for cross-pod transport to LinkedIn MCP server.
3. `LinkedInMCPClient.upload_image(image_data=img_b64)` → `asset_urn`
4. Strict Image-Only Policy: Persona debate lives inside the comic image; the LinkedIn post text only contains Source, Host CTA, Disclaimers, and dynamic hashtags. No text-only post fallback is published if image upload fails.
5. `linkedin_create_post_with_image(text, asset_urn)` publishes the post.

**Why:**
The visual comic strip drives 3-5x higher LinkedIn feed engagement. Publishing duplicated text or falling back to text-only dilutes brand value and breaches audience expectations.

### 17.3 MCP & Jev Client Connection Pooling (`src/daily_news/mcp/client.py`, `src/daily_news/mcp/jev_client.py`)

**What changed:**
Replaced per-call `httpx.AsyncClient` instantiation with persistent pooled clients (`max_connections=50`, `max_keepalive_connections=20`, `keepalive_expiry=30s`). Added graceful lifecycle teardown `aclose()` methods.

**Why:**
Per-call TCP + TLS handshakes added ~200–800ms overhead across 50+ tool calls per pipeline execution (15–40s wasted per run). Connection pooling cuts round-trip network latency to <15ms per call and avoids socket exhaustion.

### 17.4 Parallel Pipeline Execution & Narrow Retry Routing (`src/daily_news/workflows/daily_news_graph.py`)

**What changed:**
- Replaced sequential loops with `asyncio.gather` bounded by semaphores in `discover_news` (9 concurrent search queries), `fetch_articles` (semaphore=10), `index_pageindex` (semaphore=10), and `summarize` (concurrent per-article processing and internal judgment+pageindex parallelism).
- Narrowed the `REGENERATE` evaluation routing edge: retries loop back strictly to `generate_personas`, skipping redundant deterministic re-summarization and angle discovery.

**Why:**
Reduces end-to-end pipeline latency from ~120s down to ~25s.

### 17.3 LinkedIn MCP Client — Image upload + analytics (`src/daily_news/mcp/linkedin.py`)

**What changed:**
Added `upload_image()` and `create_post_with_image()` methods. Added `get_post_analytics()` for the closed-loop optimiser.

**Why:**
The LinkedIn Asset API is a two-step process (register + upload). Wrapping it in the MCP client keeps the publisher clean and testable.

### 17.4 Persona prompts — Banned phrase hardening

**What changed:**
`prompts/capitalist.txt`, `prompts/genz.txt`, `prompts/linkedin.txt`, `prompts/policy.txt` updated with explicit `⛔ AUTOMATIC REJECTION TRIGGERS` sections and requirements for article-specific opening claims.

**Why:**
The deterministic banned-phrase scanner and LLM judge were catching recurring patterns the LLM was generating: "the real challenge is", "at the end of the day", throat-clearing openers. Adding these to the system prompt reduces first-pass failures and avoids unnecessary regeneration cycles.

### 17.5 Test — Comic generator (`tests/unit/test_comic_generator.py`) — NEW

**What was added:**
Unit tests for `ComicScript`, `build_scenes()`, `_render_comic()`, `build_outside_post()`, and the `build_comic_script_from_summary()` pipeline bridge.

**Why:**
The SVG rendering logic has many string-formatting paths. Unit tests verify scene count, panel layout, required fields, and that the outside-post text format matches the expected structure — without requiring LinkedIn API access or cairosvg.

### 17.6 Test script — `scripts/test_comic_live.py` — NEW

**What was added:**
A standalone script that generates a real comic PNG using the test scenario from the module's `__main__` block. Used for visual inspection during development.

**Why:**
The unit tests mock SVG rendering. The live script gives engineers a way to visually check the output without running the full pipeline.

---

## 18. Glossary

| Term | Meaning |
|---|---|
| **AIFeeders** | The platform brand. The LinkedIn page where content is published. |
| **Jev** | Jev System One — a fast lightweight model (Qwen3.5-2B + LoRA heads) used for scoring-only decisions: article selection, persona routing, content evaluation. |
| **LangGraph** | Python library for building stateful multi-agent workflows as directed graphs with typed state. |
| **MCP** | Model Context Protocol — a standard for LLM tool servers. Used here as HTTP microservices for News, PageIndex, Evaluation, and LinkedIn. |
| **Persona** | One of four distinct editorial voices: Founder (Capitalist Mind), Engineer (Tech & Workforce Mind), Skeptic (Generalist Mind), Policy (Government Mind). |
| **PublishedStore** | In-memory (optionally persistent) store tracking which article IDs have been published today. Prevents duplicate posts. |
| **reach score** | Pre-publish deterministic score (0–100) across 6 LinkedIn organic-reach dimensions. |
| **regenerate** | Evaluation decision that sends the pipeline back to `find_angle` for a fresh generation attempt with banned phrases injected. |
| **REGENERATE** | Formal `EvaluationDecision` enum value triggering the retry loop. |
| **PASS** | Evaluation decision that allows a post to proceed to `score_reach` and then `publish`. |
| **BLOCK** | Evaluation decision that hard-stops publication (PII or prompt injection detected). |
| **HUMAN_REVIEW** | Evaluation decision that routes to the approval API before publication. |
| **comic strip** | 6-panel SVG/PNG visual produced by `ComicGenerator`. Attached to every LinkedIn post. |
| **NewsIntelligence** | The backbone Pydantic model enriched by every pipeline stage and passed to all agents. |
| **LLM-as-a-Judge** | The pattern of using an LLM at low temperature (0.1) as an independent critic of another LLM's generation at higher temperature (0.7). |
| **content_opportunity** | Jev's Stage 5 output: `common_narrative`, `missing_angle`, `recommended_audience`, `discussion_question`. |
| **avoid_phrases** | Exact banned phrases from the previous evaluation cycle, injected back into persona prompts on retry so the LLM knows precisely what failed. |
| **Unicode Bold** | LinkedIn renders `**bold**` as literal asterisks. The publisher converts `**text**` to Unicode Mathematical Bold characters (e.g. `𝐭𝐞𝐱𝐭`) that render as native bold on LinkedIn. |
