# AIFeeders — Architecture Reference

h> Build #83 · OpenShift `aifeeders` · LangGraph · Jev System One · GNews only · GrammarAgent · EKS/AKS-portable

---

## Table of Contents

1. [Overview — Why This Architecture](#1-overview--why-this-architecture)
2. [7-Stage Pipeline — Design Intent](#2-7-stage-pipeline--design-intent)
3. [Build #83 — Dialogue Delivery Format](#3-build-83--dialogue-delivery-format)
4. [System Diagram](#4-system-diagram)
5. [The NewsIntelligence Backbone Contract](#5-the-newsintelligence-backbone-contract)
6. [The NewsStory Object — Storytelling Layer](#6-the-newsstory-object--storytelling-layer)
7. [The GrammarAgent — Why a Proofreading Pass](#7-the-grammagent--why-a-proofreading-pass)
8. [LangGraph State Machine — 12 Nodes](#8-langgraph-state-machine--12-nodes)
9. [Jev System One Integration](#9-jev-system-one-integration)
10. [Deduplication — Three Independent Gates](#10-deduplication--three-independent-gates)
11. [Post Composition — Dialogue Format + LinkedIn UTF-16 Budget](#11-post-composition--dialogue-format--linkedin-utf-16-budget)
12. [MCP Service Architecture](#12-mcp-service-architecture)
13. [Networking — NetworkPolicy Model](#13-networking--networkpolicy-model)
14. [Scaling Architecture](#14-scaling-architecture)
15. [Build Flow — Docker, OpenShift, EKS, AKS](#15-build-flow--docker-openshift-eks-aks)
16. [Memory and State Management](#16-memory-and-state-management)
17. [Observability](#17-observability)
18. [Security Architecture](#18-security-architecture)
19. [Is This Secure, Robust, Scalable, Distributed, Useful?](#19-is-this-secure-robust-scalable-distributed-useful)
20. [Change Log](#20-change-log)

---

## 1. Overview — Why This Architecture

### The problem

A naive pipeline — `GNews → LLM → LinkedIn` — produces generic article summaries. Every AI news account already does this. The output is competent but forgettable: a list of facts the reader already saw in their feed three hours ago.

The real work of journalism is not summarisation. It is five distinct cognitive tasks that a naive pipeline collapses into one:

| Cognitive task | Question answered | Where solved in AIFeeders |
|---|---|---|
| Discovery | What happened? | Stage 1 — GNews |
| Evidence retrieval | What do the sources actually say? | Stage 2 — PageIndex |
| Prioritisation | Is this worth saying? | Stage 3 — Jev prefilter |
| Storytelling | What is the real story? | Stage 4 — MediaStorytellerAgent |
| Angle finding | What is everyone missing? | Stage 5 — Jev find_angle |
| Voice | Who should say what, and how? | Stage 6 — PersonaAgentFactory |
| Publication | Did it go out correctly? | Stage 7 — LinkedIn MCP |

Collapsing these into one LLM call forces the model to be simultaneously creative and factual, simultaneously a journalist and a copy-editor — tasks that require different temperatures, different prompts, and different trust models.

### Four pillars

**Vectorless retrieval.** PageIndex builds a document tree from each article. Section retrieval is structural, not semantic. No embedding model. No vector database. No cosine similarity. The tree is the retrieval mechanism. This is not a compromise — for a single-article evidence pass, it is faster, more deterministic, and cheaper than a vector pipeline.

**Score before spend.** Jev System One runs 11 lightweight questions per article before any expensive LLM work begins. A 0.6/0.4 weighted composite score selects the single most interesting article per run. Cost is paid only for content worth publishing.

**Separation of storytelling and factual extraction.** The two-pass summarize node runs MediaStorytellerAgent at temperature=0.5 first (creative framing) then SummaryAgent at temperature=0.2 second (factual accuracy, calibrated by the story). These are not the same task and they should not share a temperature.

**Dialogue over bullet lists.** Build #83 replaced a structured bullet list with a four-character dialogue format. Each character speaks in 3–5 sentence story passages, not one-liners. The Media Person opens and closes. The result reads like a news programme, not a content calendar post.

### Design principles

| Principle | How it is enforced |
|---|---|
| Fail loudly in logs, fail gracefully in pipeline | Every LLM/Jev/MCP error is caught, logged with `run_id`, pipeline continues |
| Hard blocks are truly hard | PII, injection, policy_fail cannot be overridden — not even by `JEV_ENABLED=false` |
| Stateful singletons are explicitly documented | linkedin-mcp and pageindex-mcp are intentionally 1-replica — this is a design constraint, not an oversight |
| No secrets in code, logs, or ConfigMaps | All credentials injected as K8s Secrets or cloud-native secret managers |
| Platform portability | The same YAML manifests run on OpenShift, EKS, and AKS — only the image registry URL changes |

---

## 2. 7-Stage Pipeline — Design Intent

```
DISCOVER → UNDERSTAND → ANALYZE → EXPLAIN → ANGLE → CREATE → PUBLISH
  Stage 1     Stage 2     Stage 3    Stage 4   Stage 5   Stage 6   Stage 7
```

### Stage 1 — DISCOVER

**Component:** GNews REST API  
**Why it exists:** One well-integrated source beats two poorly integrated ones. Previous builds included NewsDataIO, which added auth surface area, a second rate-limit budget, and a second error path with no meaningful content improvement. GNews was retained; NewsDataIO was removed in build #82.  
**Configuration:** 9 curated queries, 24-hour window, 2-key auto-rotation on HTTP 403  
**Output:** Raw article list — `{title, url, source, content[:2000]}`

The 2000-character content slice is deliberate. Full article text is not needed at discovery; it is retrieved structurally in Stage 2. The slice prevents oversized payloads from polluting the LangGraph state dict in the first pass.

### Stage 2 — UNDERSTAND

**Component:** PageIndex MCP (1 replica — see §12)  
**Why it exists:** LLMs hallucinate less with structured evidence. PageIndex builds a tree from each article, enabling specific section retrieval without embedding models, vector databases, or cosine search. The tree IS the retrieval mechanism.  
**Output:** Evidence sections per article — `[{source, section, text}]` stored in `NewsIntelligence.evidence`

The key insight: retrieval by tree traversal is fully deterministic. The same article always returns the same sections. Semantic search would return different sections depending on query phrasing — introducing variability before the LLM has even started.

### Stage 3 — ANALYZE

**Component:** Jev System One prefilter  
**Why it exists:** Not every AI article is worth a LinkedIn post. Running full LLM pipeline on all articles burns cost and degrades quality (averaging over a bad article pollutes a good one). Jev scores before any expensive LLM work.  
**Scoring:** 11 questions per article. Composite = `relevance × 0.6 + engagement × 0.4`  
**Output:** `NewsIntelligence` — `{sentiment, emotion, impact, novelty, trend_velocity, event_type}`  
**Decision:** Top-1 article selected per run.

### Stage 4 — EXPLAIN (Two passes)

**Why two passes?** Storytelling and factual extraction are different cognitive tasks. Forcing one LLM call to be both creative and factual produces writing that is neither.

**Pass 1 — MediaStorytellerAgent**  
- Temperature: `0.5` (creative framing, narrative construction)  
- Prompt: `prompts/storyteller.txt`  
- Question answered: "What is the STORY?"  
- Output: `NewsStory` — `{hook, human_analogy, perspective, second_order_effect, future_question, what_actually_happened, what_changed, why_now, narrative_style, ...}`

**Pass 2 — SummaryAgent**  
- Temperature: `0.2` (factual accuracy, low variance)  
- Prompt: `prompts/summary.txt`  
- Question answered: "What are the FACTS?"  
- Calibrated by: the `NewsStory` from Pass 1  
- Output: `NewsSummary` — `{headline, key_points, why_it_matters, impacts}`

The story object from Pass 1 is passed to Pass 2 as context, so the factual extraction is grounded in the same narrative frame the persona agents will use.

### Stage 5 — ANGLE

**Component:** Jev find_angle  
**Question answered:** "What is everyone MISSING?"  
**Why it exists:** The most obvious story angle is the one every other account will use. Jev identifies the gap between the common narrative and what the evidence actually supports.  
**Output:** `ContentOpportunity` — `{common_narrative, missing_angle, recommended_audience}`

### Stage 6 — CREATE

**Component:** PersonaAgentFactory + GrammarAgent + PublisherAgent

**PersonaAgentFactory** runs 4 parallel LLM agents. Each character receives full story context (all `NewsStory` fields, `NewsIntelligence` signals, `ContentOpportunity`). Each produces a 3–5 sentence story passage, not a one-liner.

| Character | Lens | Prompt file |
|---|---|---|
| Founder | Capital allocation, business risk, competitive moat | `prompts/founder.txt` |
| Policy Analyst | Governance frameworks, accountability gaps, enforcement | `prompts/policy.txt` |
| Engineer | Technical trade-offs and workforce implications (dual lens) | `prompts/engineer.txt` |
| Generalist | Zero jargon, human scale, plain English | `prompts/generalist.txt` |

A 5th character — Working Professional — exists in `prompts/labor.txt` but is gated on LinkedIn Comments API "Community Management" permission, not yet granted.

**GrammarAgent** (new in build #83) — see §7.

**PublisherAgent** is deterministic — zero LLM calls. It assembles the dialogue format described in §3 from the structured outputs of all previous stages.

**Output:** LinkedIn post (dialogue format) + grammar-corrected version

### Stage 7 — PUBLISH

**Component:** LinkedIn API via linkedin-mcp  
**Why MCP:** Decouples the OAuth lifecycle from the main pipeline. The MCP server is the single place that holds the token; the pipeline never touches credentials.  
**Deduplication:** 3-gate model — see §10  
**Gate:** `PUBLISHING_ENABLED` flag enables human review before final publish  
**Output:** LinkedIn URN + audit record (logged to Langfuse and structured log)

---

## 3. Build #83 — Dialogue Delivery Format

### Why dialogue beats bullet lists

A list of expert opinions is a listicle. A conversation between four characters with distinct voices is a programme. The LinkedIn audience — senior AI practitioners — reads enough listicles. The dialogue format signals editorial intent: someone actually thought about this.

Mechanically: bullet-list posts allow readers to skim and conclude they've understood. Dialogue posts require following a thread. Engagement duration increases. LinkedIn's algorithm rewards time-on-post.

### Full post anatomy

```
🎙️ HOOK
  (story.hook from MediaStorytellerAgent)

SITUATION
  (story.what_actually_happened + why_now + what_changed)

HUMAN ANALOGY
  (story.human_analogy)

"So I asked four people what [subject] means for them."
────────────────────

"Let's start with the business question."
💼 Founder
[3-5 sentence story passage — real business scenario, risk/capital lens]

"Now let's look at the governance question."
🏛️ Policy Analyst
[3-5 sentence story passage — named framework, accountability gap]

"And then there's the engineering question."
🧠 Engineer
[3-5 sentence story passage — technical trade-off + workforce implication]

"But there's someone we haven't heard from yet..."
🎓 Generalist
[3-5 sentence story passage — zero jargon, human scale]

────────────────────

MEDIA CLOSE
  (story.perspective + story.second_order_effect)

SOURCE LINK

FUTURE QUESTION
  (story.future_question — fallback to _build_cta())

FOOTER
  ⚠️ disclaimer + dynamic hashtags from article content
```

### The Media Person role

The Media Person is not a fifth character. It is the structural frame. The Media Person owns the hook, the situation setup, the bridge questions between characters, and the close. This is the `PublisherAgent`'s responsibility — deterministic, template-driven, zero LLM calls.

The bridge questions (`"Let's start with the business question."` etc.) are not generic placeholders. They are positioned to create narrative flow: business → governance → engineering → human scale — the same order a policy-aware journalist would use.

### Character passage format

Each persona receives a prompt instruction to produce 3–5 complete sentences forming a coherent passage. The passage must:
- Include a concrete scenario or named example (not a vague principle)
- Commit to a specific claim (not hedge with "might")
- End with a consequence, not a summary

One-liner responses are rejected at the persona prompt level. The instruction is explicit: "Do not produce a one-sentence opinion. Write a story passage."

---

## 4. System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OpenShift / EKS / AKS Cluster                       │
│                                                                               │
│  ┌──────────────┐     ┌──────────────────────────────────────────────────┐  │
│  │  CronJob     │     │              daily-news-api (HPA 2-4)             │  │
│  │  (2×/day)    │────▶│                                                    │  │
│  └──────────────┘     │  LangGraph State Machine (12 nodes)               │  │
│                        │                                                    │  │
│                        │  discover → deduplicate → fetch_articles          │  │
│                        │  → index_pageindex → jev_prefilter                │  │
│                        │  → summarize ◀──────────────────────┐            │  │
│                        │      │                               │            │  │
│                        │  find_angle → jev_router            │            │  │
│                        │  → generate_personas → evaluate ────┘ (REGEN)   │  │
│                        │  → score_reach → publish                          │  │
│                        └───────┬───────────────────┬──────────────────────┘  │
│                                │                   │                          │
│         ┌──────────────────────┼───────────────────┼──────────────────────┐  │
│         │      MCP Services    │                   │                       │  │
│         │                      │                   │                       │  │
│  ┌──────┴──────┐  ┌────────────┴──┐  ┌────────────┴──┐  ┌─────────────┐  │  │
│  │ pageindex-  │  │ evaluation-   │  │  news-mcp     │  │ linkedin-   │  │  │
│  │ mcp (×1)   │  │ mcp (×2)     │  │  (×2)         │  │ mcp (×1)   │  │  │
│  │ [Jev + PI] │  │ [Jev eval]   │  │  [GNews]      │  │ [OAuth tok] │  │  │
│  └─────────────┘  └───────────────┘  └───────────────┘  └──────┬──────┘  │  │
│         │                                                        │          │
└─────────┼────────────────────────────────────────────────────────┼──────────┘
          │                                                        │
          ▼                                                        ▼
   ┌─────────────┐                                         ┌──────────────┐
   │  Jev System │                                         │  LinkedIn    │
   │  One (ext)  │                                         │  REST API    │
   └─────────────┘                                         └──────────────┘

External: GNews REST API, OpenAI API, Langfuse (observability)
Storage:  PublishedStore (/tmp or PVC), Langfuse (permanent audit)
```

---

## 5. The NewsIntelligence Backbone Contract

### Why a single model carries the full run

Every stage writes to `NewsIntelligence`. No stage overwrites another stage's fields. The object travels through the entire LangGraph state machine as a single coherent record. This design has three consequences:

1. **Debuggability.** A Langfuse trace showing the full `NewsIntelligence` object at publish time is a complete audit of every decision made in that run.
2. **Downstream calibration.** Persona agents receive Jev signals (novelty, trend_velocity, controversy_level) alongside the story object. They are not generating opinions in a vacuum — they are reacting to scored evidence.
3. **Immutability contract.** LangGraph nodes return `{**state, key: new_value}`. Existing keys are never mutated in place. This prevents the score-leak bug fixed in build #61 (jev_prefilter_scores was a flat dict; all articles shared the same key).

### Full field list

```python
class NewsIntelligence(BaseModel):
    # Identity
    article_id: str
    headline: str
    source: str
    source_url: str
    topics: list[str]

    # Stage 3 — Jev prefilter outputs
    sentiment: str
    sentiment_score: float
    emotion: EmotionSignals         # {curiosity, excitement, concern, urgency}
    impact: AudienceImpact          # {enterprise, developers, infrastructure,
                                    #  business, policy}
    novelty: float
    trend_velocity: float
    audience_relevance: float
    event_type: str
    significance: float
    controversy_level: str

    # Stage 4a — Storytelling (Pass 1)
    story: Optional[NewsStory]      # full storytelling object — see §6

    # Stage 4b — Facts (Pass 2)
    what_happened: str
    what_changed: str
    why_it_matters: str
    key_points: list[str]

    # Stage 5 — Content angle
    content_opportunity: ContentOpportunity  # {common_narrative, missing_angle,
                                             #  recommended_audience}

    # Stage 2 — PageIndex evidence
    evidence: list[EvidenceItem]    # {source, section, text}
```

---

## 6. The NewsStory Object — Storytelling Layer

### Why separate storytelling from summarisation

A summary answers: "What happened?" A story answers: "Why does this matter, and to whom, and what comes next?"

These are different questions with different answers. The SummaryAgent produces accurate bullet points. The MediaStorytellerAgent produces a narrative frame that makes those bullet points land with an audience that has already read the headline.

The story object is not decorative. It is structural: every `story.*` field maps to a specific slot in the dialogue post (hook → opening, human_analogy → bridge, perspective + second_order_effect → close, future_question → CTA). If MediaStorytellerAgent fails, the entire downstream composition degrades to a lower-quality fallback.

### Two-pass rationale

| Pass | Agent | Temperature | Why |
|---|---|---|---|
| 1 | MediaStorytellerAgent | 0.5 | Framing is a creative act. Too low and the writing is flat; too high and it hallucinates. 0.5 is the empirically validated sweet spot for this task. |
| 2 | SummaryAgent | 0.2 | Factual extraction must be low-variance. The story frame is already set; now we need accurate attribution, not invention. |

### Full NewsStory field list

```python
class NewsStory(BaseModel):
    # What happened
    what_actually_happened: str
    who: list[str]
    what_changed: str
    why_now: str
    what_came_before: str
    what_problem_it_solves: str

    # Story frame
    hook: str                   # → opening line of post
    human_analogy: str          # → bridge paragraph
    turning_point: str
    why_reader_should_care: str
    perspective: str            # → Media Person close
    second_order_effect: str    # → Media Person close
    future_question: str        # → CTA (fallback: _build_cta())

    # Consequences by audience
    business_consequence: str
    technology_consequence: str
    human_consequence: str

    # Risk/opportunity
    risks: list[str]
    opportunities: list[str]

    # Style
    narrative_style: str        # one of 15 styles — see below
    tone: str
```

### 15 narrative styles

| Style | When used |
|---|---|
| `imagine_if` | Speculative future scenario, near-term |
| `human_story` | A named individual or team is the protagonist |
| `behind_the_scenes` | Process or decision not visible in the headline |
| `problem_solution` | Classical structure; works for product launches |
| `unexpected_consequence` | Second-order effect the headline missed |
| `nobody_talking_about` | Contrarian, counter-narrative angle |
| `before_after` | State change is the story |
| `what_happens_next` | Forward-looking; audience is waiting for resolution |
| `simple_explanation` | Complex topic requires demystification |
| `business_impact` | P&L and competitive moat are the lens |
| `contrarian` | Disagrees with the consensus narrative explicitly |
| `future_scenario` | Longer-horizon speculation, 3–5 year frame |
| `developer_lens` | API surface, runtime cost, developer experience |
| `architect_lens` | System design, scale, tradeoffs |
| `executive_lens` | Board-level implications, risk/reward framing |

MediaStorytellerAgent selects the style. The selection is included in `NewsStory.narrative_style` and logged. Persona agents receive the style as context — the Founder speaks differently when the style is `contrarian` vs. `human_story`.

---

## 7. The GrammarAgent — Why a Proofreading Pass

### What it fixes

GrammarAgent is a single-purpose LLM call at `temperature=0`. It receives the fully composed post and returns a corrected version. It fixes:

- Spelling errors
- Grammar errors
- Punctuation errors (comma splices, missing full stops, misused apostrophes)
- Capitalisation errors

### What it never changes

- Post structure (section order, paragraph breaks, line breaks)
- Tone, voice, or meaning
- Emoji (including emoji that appear mid-sentence)
- Hashtags (including casing, e.g. `#AIPolicy` stays `#AIPolicy`)
- Line breaks (the dialogue format relies on specific line break positions)

The prompt instruction is explicit: "Correct only spelling, grammar, punctuation, and capitalisation. Do not rephrase, restructure, or alter tone. Return the full corrected text. Do not add commentary."

### Best-effort design

GrammarAgent **never blocks publish**. If the call fails (timeout, model error, malformed response), the pipeline logs `"grammar_agent: fallback to original"` and continues with the unmodified post. This is the correct trade-off: a post with a minor grammar error is better than a blocked publish.

### Why temperature=0

Grammar correction is not a creative task. Every correct answer is the same answer. `temperature=0` maximises determinism — the same input produces the same correction every time. This also makes the before/after diff in the log (`"grammar_agent: corrected"`) reproducible and auditable.

---

## 8. LangGraph State Machine — 12 Nodes

### Why LangGraph

LangGraph provides three things this pipeline needs:

1. **Typed state with immutable node contracts.** Each node receives the full state dict and returns a partial update. No node can accidentally overwrite another node's output.
2. **Conditional edges.** The evaluate → summarize back-edge (REGENERATE loop) and the jev_router → generate_personas conditional dispatch are expressible as first-class graph edges, not nested if-else blocks.
3. **Native Langfuse integration.** Each node maps to a span. The full run is a single traceable session.

### State schema (abridged)

```python
class AIFeedersState(TypedDict):
    run_id: str
    articles: list[dict]                      # raw GNews output
    deduplicated_articles: list[dict]         # after gate 1
    fetched_articles: list[dict]              # after content fetch
    indexed_articles: list[dict]              # after PageIndex
    intelligence: Optional[NewsIntelligence]  # selected article + all signals
    personas: dict[str, str]                  # {founder, policy, engineer, generalist}
    composed_post: Optional[str]              # before grammar check
    final_post: Optional[str]                 # after GrammarAgent
    evaluation_result: Optional[dict]         # Jev eval output
    score_reach_result: Optional[dict]        # reach scoring output
    publish_result: Optional[dict]            # LinkedIn URN + audit
    retry_count: int                          # REGENERATE loop counter
    jev_prefilter_scores: dict[str, float]    # keyed by article_id (bug fix #61)
```

### Node descriptions

| Node | Responsibility | Key outputs |
|---|---|---|
| `discover_news` | GNews API — 9 queries, 24h, 2-key rotation | `articles` |
| `deduplicate` | Gate 1: URL hash + Jaccard title similarity | `deduplicated_articles` |
| `fetch_articles` | Full content fetch per deduplicated URL | `fetched_articles` |
| `index_pageindex` | PageIndex MCP — build document tree per article | `indexed_articles`, `evidence` |
| `jev_prefilter` | Jev 11-question scoring, top-1 selection | `intelligence` (partial) |
| `summarize` | Two-pass: MediaStorytellerAgent + SummaryAgent | `intelligence.story`, facts fields |
| `find_angle` | Jev find_angle — missing angle + audience | `intelligence.content_opportunity` |
| `jev_router` | Jev 4-question — which personas are relevant | routing signal |
| `generate_personas` | PersonaAgentFactory × 4 parallel + GrammarAgent | `personas`, `final_post` |
| `evaluate` | EvaluationAgent — Jev 10-question, PASS/BLOCK/REGEN | `evaluation_result` |
| `score_reach` | Text scoring 0–100, PUBLISH/REVISE verdict, auto-repair | `score_reach_result` |
| `publish` | Gate 2 dedup + LinkedIn MCP publish | `publish_result` |

### Graph wiring (ASCII)

```
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
jev_prefilter
     │
     ▼
summarize ◀──────────────────────────────────────────────┐
     │                                                     │ REGENERATE
     ▼                                                     │ (max 2 retries)
find_angle                                                 │
     │                                                     │
     ▼                                                     │
jev_router                                                 │
     │                                                     │
     ▼                                                     │
generate_personas                                          │
     │                                                     │
     ▼                                                     │
evaluate ──────────────────────────────────────────────────┘
     │
     │ PASS
     ▼
score_reach
     │
     ▼
publish
     │
     ▼
  [END]
```

The only back-edge is `evaluate → summarize`. Max retries = 2. After 2 REGENERATE cycles, if evaluate still fires REGENERATE, the pipeline moves to BLOCK evaluation instead.

`score_reach` is pre-publish, not pre-evaluate. It runs on the post that already passed evaluation. REVISE triggers auto-repair (trimming, restructuring) without an LLM call — pure text scoring and manipulation. PUBLISH proceeds to Stage 7.

---

## 9. Jev System One Integration

### Why Jev instead of a prompt-based LLM judge

| Dimension | LLM-as-judge | Jev System One |
|---|---|---|
| Reproducibility | Low — same input, different scores across runs | High — deterministic scoring model |
| Cost | Full LLM call per evaluation | Lightweight inference, significantly cheaper |
| Calibration | Requires prompt tuning per criterion | Pre-calibrated question bank |
| Hard blocks | Cannot enforce — LLM can be prompted around them | Hard gates enforced at system level |
| Audit trail | Prompt + response (difficult to diff) | Structured score object with per-question breakdown |

The critical advantage is the hard-block enforcement. PII detection, prompt injection detection, and policy_fail are enforced by Jev at the gate level — they cannot be overridden by changing prompts or feature flags.

### Decision #1 — jev_prefilter (Stage 3)

11 questions per article. Composite score = `relevance × 0.6 + engagement × 0.4`.

The 0.6/0.4 weighting reflects editorial judgment: relevance to the AI practitioner audience is the primary criterion. Engagement potential (novelty, emotional signal) is a secondary multiplier. A highly relevant but dry story beats a highly engaging but off-topic one.

Output fields: `sentiment`, `sentiment_score`, `emotion`, `impact`, `novelty`, `trend_velocity`, `audience_relevance`, `event_type`, `significance`, `controversy_level`

Top-1 selection: only the highest-composite article proceeds. All others are discarded after scoring.

### Decision #2 — jev_route_personas (Stage 6)

4 questions determine which personas are most relevant given the article's topic and audience signals. Routes to a subset of the 4 personas or all 4. In practice, all 4 are almost always relevant — the router exists to handle edge cases where, for example, a purely infrastructure article has no meaningful Policy Analyst angle.

### Decision #3 — EvaluationAgent (Stage 8)

10 questions. Three verdict outcomes:

**PASS** — post proceeds to score_reach and publish.

**BLOCK (hard, cannot be overridden):**

```
pii_detected > 0.5
prompt_injection_detected > 0.5
policy_check = FAIL
```

These conditions cannot be disabled. `JEV_ENABLED=false` disables Jev prefilter and routing — it does NOT disable the hard block gates.

**REGENERATE:**

```
factuality < 0.50
OR groundedness < 0.50
OR hallucination > 0.85
```

Triggers a loop back to the `summarize` node. Max 2 retries. After 2 failed regenerations, the pipeline blocks the article and logs the failure.

---

## 10. Deduplication — Three Independent Gates

The problem with single-gate deduplication: one failure mode (race condition, hash collision, store corruption) results in a duplicate LinkedIn post. Three independent gates eliminate this risk.

### Gate 1 — `deduplicate` node

Runs before any expensive work (no LLM calls consumed on duplicates).

1. **URL normalisation + hash.** Strip query params, normalise scheme. SHA-256 hash stored in `PublishedStore`. Exact duplicates are caught here.
2. **PublishedStore cross-run lookup.** The store persists across runs (7-day TTL). An article that was published 6 hours ago is caught here.
3. **Jaccard title similarity.** Threshold: 0.55. Two articles about the same announcement with slightly different headlines are caught here. Formula: `|title_tokens_A ∩ title_tokens_B| / |title_tokens_A ∪ title_tokens_B|`

### Gate 2 — `publish` node

A second `published_store.is_published()` check immediately before the LinkedIn API call. This is the race-condition guard. If two pipeline runs processed the same article concurrently (possible on restart), gate 1 may have passed for both. Gate 2 uses a file lock on the store.

### Gate 3 — linkedin-mcp idempotency

The linkedin-mcp server maintains an in-process idempotency dict. Key format:

```
{article_id}:{YYYY-MM-DD}:{body_hash[:12]}
```

If the same article ID is submitted twice on the same calendar day with the same post body, linkedin-mcp returns the existing URN without making a second LinkedIn API call. This gate survives application-layer failures above it.

### PublishedStore

```
Path (local):  /tmp/aifeeders_published.json
Path (EKS/AKS): $AIFEEDERS_STORE_PATH (must point to a mounted PVC)
TTL:           7 days (entries older than 7 days are pruned on load)
Format:        {url_hash: {published_at: ISO8601, headline: str, urn: str}}
```

On EKS/AKS: the store MUST be on a PVC, not the pod's ephemeral filesystem. Pod restarts should not reset the deduplication state. Set `AIFEEDERS_STORE_PATH` to the PVC mount path.

---

## 11. Post Composition — Dialogue Format + LinkedIn UTF-16 Budget

### Composition flow

```
MediaStorytellerAgent output (NewsStory)
         │
         ├── story.hook             → Opening hook line
         ├── story.what_actually_happened + why_now + what_changed → Situation block
         ├── story.human_analogy    → Bridge paragraph
         ├── story.perspective      → Media Person close
         ├── story.second_order_effect → Media Person close
         └── story.future_question  → CTA (fallback: _build_cta())

PersonaAgentFactory outputs (4 passages)
         │
         ├── personas["founder"]    → 💼 Founder block
         ├── personas["policy"]     → 🏛️ Policy Analyst block
         ├── personas["engineer"]   → 🧠 Engineer block
         └── personas["generalist"] → 🎓 Generalist block

PublisherAgent (zero LLM calls)
         │
         └── assembles all blocks + bridge phrases + dividers + source + footer
                   │
                   ▼
         GrammarAgent (temperature=0, best-effort)
                   │
                   ▼
         final_post (≤ 3000 LinkedIn units)
```

### The UTF-16 character counting bug (fixed in build #61)

**Problem:** LinkedIn counts characters as UTF-16 code units. Python's `len()` counts Unicode code points. Most emoji are in the Supplementary Multilingual Plane (code points > U+FFFF). They cost 2 UTF-16 units but only 1 Python code point.

A post with 30 emoji that Python reports as 2,980 characters is 3,010+ LinkedIn units — above the 3,000 limit. LinkedIn silently truncates the post mid-sentence, not at a paragraph boundary.

**Fix:**

```python
def _linkedin_len(text: str) -> int:
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)
```

All length checks in `PublisherAgent` and `score_reach` use `_linkedin_len()`, not `len()`.

**POST_LIMIT history:**

| Build | POST_LIMIT | Reason |
|---|---|---|
| #61–#82 | 2900 | Conservative buffer for unknown emoji counts |
| #83 | 3000 | Dialogue format has better per-paragraph budget management; each character block is now bounded at generation time |

---

## 12. MCP Service Architecture

### What MCP is (and why it is used here)

MCP (Model Context Protocol) servers are small FastAPI services that expose a standard `POST /call` tool-dispatch interface. The pipeline calls tools over HTTP rather than importing libraries directly. This provides:

- **Isolation.** A bug in evaluation-mcp cannot crash the main LangGraph process.
- **Independent restartability.** Each MCP server has its own Deployment and health check.
- **Stable interface.** `news-mcp` and `linkedin-mcp` could be replaced with different implementations without changing `daily-news-api`.

### MCPHTTPClient design

All MCP calls go through a shared `MCPHTTPClient` that:
- Sets a per-call timeout
- Detects nested errors (LinkedIn MCP returns `{result: {error: ...}}` — the outer call succeeds but the inner result is an error; naive clients miss this)
- Logs every call with `run_id` for Langfuse correlation

### Service inventory

| Service | Replicas | Stateful | Notes |
|---|---|---|---|
| `news-mcp` | 2 | No | GNews wrapper, 2-key rotation |
| `evaluation-mcp` | 2 | No | Jev System One gateway |
| `pageindex-mcp` | **1** | **Yes (per-run index)** | Must be 1 replica — see below |
| `linkedin-mcp` | **1** | **Yes (OAuth token)** | Must be 1 replica — see below |
| `daily-news-api` | 2–4 (HPA) | No | Main LangGraph process |

### linkedin-mcp — stateful singleton

**Why 1 replica:** linkedin-mcp holds three things in process memory: the OAuth token, the idempotency dictionary, and the audit log. These cannot be shared across replicas without a shared store.

**The deliberate security trade-off:** The token is held in-process memory only, never written to disk. A storage breach (compromised PVC, leaked ConfigMap) cannot expose the LinkedIn OAuth token. The price: any pod restart (OOMKill, rolling deploy, node eviction) requires a human to re-authenticate via the LinkedIn PKCE OAuth flow.

This is the correct trade-off for an editorial pipeline that publishes 2× per day. It would be wrong for a high-availability consumer-facing service.

**Path to horizontal scaling:** Store the OAuth token in a K8s Secret (encrypted at rest). Add a refresh token flow. Replicas read from the shared Secret. This is not yet implemented.

### pageindex-mcp — per-run in-memory index

**Why 1 replica:** PageIndex builds an in-memory document tree per run. Replica 1 and Replica 2 would each build their own separate index from the articles they received. Any subsequent retrieval call might hit a different replica that has a different (or no) index for that article.

**Fix:** `replicas: 1` in the Deployment manifest. No architectural change needed — the constraint is correctly matched to the workload.

### Each MCP server exposes

```
POST /call     — tool dispatcher (JSON body: {tool: str, args: dict})
GET  /health   — liveness check (returns {"status": "ok"})
GET  /metrics  — Prometheus endpoint
```

---

## 13. Networking — NetworkPolicy Model

### Why default-deny-all

Without a default-deny policy, any pod in the namespace can call any other pod. A compromised `news-mcp` could call `linkedin-mcp` and attempt to publish arbitrary content. Default-deny-all means every allowed communication path is an explicit whitelist entry.

### Full policy set

```
default-deny-all
  → All ingress and egress blocked unless explicitly permitted.

allow-api-to-mcps
  → daily-news-api and daily-news-worker pods may call news-mcp,
    evaluation-mcp, pageindex-mcp, linkedin-mcp.
  → No other pod may call MCP services.

allow-egress-internet
  → daily-news-api and news-mcp may reach external APIs
    (GNews, OpenAI, Langfuse, LinkedIn REST).
  → MCP services that do not need internet (pageindex-mcp) are NOT
    in this policy.

allow-router-to-api
  → OpenShift router / ingress controller → daily-news-api (CronJob trigger).

allow-router-to-linkedin-mcp
  → OAuth callback from LinkedIn to linkedin-mcp.
  → Scope: router → linkedin-mcp only. Not a blanket ingress grant.
```

### The CronJob label bug — the most important production lesson

**Symptom:** All outbound calls from the CronJob pod timeout with an empty error message. No firewall log. No connection refused. Just silence and timeout.

**Root cause:** Pod labels were placed at `template.spec.labels` in the CronJob manifest. Kubernetes silently ignores labels at this path. The correct path is `template.metadata.labels`. Because NetworkPolicy label selectors matched against `metadata.labels`, the CronJob pod matched no allow rule — and the default-deny rule applied.

**Why it is silent:** Kubernetes does not validate `template.spec.labels` as an error. It accepts the manifest. The labels are simply never applied to the pod. `kubectl describe pod <cronjob-pod>` shows no labels. Without looking at the pod description, the failure looks like a network connectivity problem, not a manifest bug.

**The fix:**

```yaml
# WRONG — Kubernetes ignores this silently
spec:
  jobTemplate:
    spec:
      template:
        spec:
          labels:          # ← this path does not exist and is ignored
            app: daily-news-worker

# CORRECT
spec:
  jobTemplate:
    spec:
      template:
        metadata:
          labels:          # ← this is where pod labels must be
            app: daily-news-worker
```

### Platform-specific NetworkPolicy requirements

| Platform | CNI | NetworkPolicy enforcement |
|---|---|---|
| OpenShift | OVN-Kubernetes (default) | Native — enforced by default |
| EKS | VPC CNI (default) | **NOT enforced** — must install Calico or Cilium explicitly |
| AKS | Azure CNI Overlay | Native — enforced by default |

**EKS note:** This is a common production surprise. Default EKS clusters with VPC CNI silently accept NetworkPolicy manifests without enforcing them. A cluster that appears locked down is completely open. Install Calico (`kubectl apply -f calico.yaml`) or use EKS with Cilium before relying on NetworkPolicy.

---

## 14. Scaling Architecture

### What scales and what doesn't

| Component | Scalable | Why / Why not |
|---|---|---|
| `daily-news-api` | ✅ HPA 2–4 | Stateless LangGraph process |
| `news-mcp` | ✅ 2 replicas | Stateless GNews wrapper |
| `evaluation-mcp` | ✅ 2 replicas | Stateless Jev gateway |
| `pageindex-mcp` | ❌ 1 replica max | Per-run in-memory index |
| `linkedin-mcp` | ❌ 1 replica max | In-memory OAuth token + idempotency dict |

### HPA configuration

```yaml
# daily-news-api
minReplicas: 2
maxReplicas: 4
scaleUp:   CPU > 70%
scaleDown: CPU < 30%
```

### PodDisruptionBudget

```yaml
# daily-news-api
minAvailable: 1
```

Ensures at least one replica remains available during node drains, rolling upgrades, or voluntary disruptions.

### CronJob concurrency

```yaml
concurrencyPolicy: Forbid
```

Prevents parallel CronJob runs. The pipeline is designed for editorial cadence (2×/day), not real-time throughput. A second run starting before the first finishes would create a race condition on the `PublishedStore`.

### Resource limits

| Service | CPU request | Memory request | CPU limit | Memory limit |
|---|---|---|---|---|
| `daily-news-api` | 500m | 256Mi | 2 CPU | 2Gi |
| MCP servers | 100m | 128Mi | 500m | 256Mi |

The `daily-news-api` limit of 2 CPU / 2Gi is sized for concurrent LLM API calls (which are I/O-bound, not CPU-bound) plus the LangGraph state dict in memory across 12 nodes.

MCP server limits of 500m / 256Mi are sized for FastAPI request handling. They do not run LLMs locally; all heavy compute is delegated to external APIs or Jev.

---

## 15. Build Flow — Docker, OpenShift, EKS, AKS

### The rsync rule

All platforms start with:

```bash
rsync -av --exclude='.venv/' --exclude='__pycache__/' \
         --exclude='.env' --exclude='.git/' \
         . "$TMPDIR/"
```

Build from `$TMPDIR`, not from the working directory. This prevents `.env` files (containing API keys), virtual environment binaries (500MB+), and Python bytecode caches from entering the build context. On a slow connection, this alone reduces build time by 60%.

### OpenShift — S2I (no Docker daemon required)

```bash
oc start-build aifeeders --from-dir="$TMPDIR" --follow
```

Source-to-Image builds run on the OpenShift cluster. No local Docker daemon. No local image push. The build runs in a BuildConfig pod, produces an ImageStream tag, and triggers a rolling deployment automatically.

```
Local source (rsync'd tmpdir)
         │
         ▼
OpenShift BuildConfig (S2I)
         │
         ▼
ImageStream (aifeeders:latest)
         │
         ▼
Deployment rollout
```

### EKS

```bash
docker build -t $ECR_REPO/aifeeders:$TAG "$TMPDIR"
docker push $ECR_REPO/aifeeders:$TAG
kubectl rollout restart deployment/daily-news-api
```

Requires:
- Local Docker daemon
- `aws ecr get-login-password` credentials configured
- ECR repository created
- `IRSA` configured for the pod ServiceAccount (for Secrets Manager access)

### AKS

```bash
# Option A: local build + push
docker build -t $ACR_REPO/aifeeders:$TAG "$TMPDIR"
docker push $ACR_REPO/aifeeders:$TAG
kubectl rollout restart deployment/daily-news-api

# Option B: cloud build (no local Docker daemon)
az acr build --registry $ACR_NAME --image aifeeders:$TAG "$TMPDIR"
kubectl rollout restart deployment/daily-news-api
```

### What changes in manifests between platforms

| Element | OpenShift | EKS | AKS |
|---|---|---|---|
| Image registry | `image-registry.openshift-image-registry.svc:5000/...` | `<account>.dkr.ecr.<region>.amazonaws.com/...` | `<name>.azurecr.io/...` |
| Secret backend | K8s Secrets | AWS Secrets Manager + IRSA | Azure Key Vault + Managed Identity |
| NetworkPolicy enforcement | Native (OVN-K) | Requires Calico/Cilium | Native (Azure CNI Overlay) |
| Storage class for PVC | `managed-csi` or default | `gp3` | `managed-premium` |

All other YAML is identical across platforms. The platform abstraction is entirely at the image registry URL and the secret injection mechanism.

---

## 16. Memory and State Management

### Within a run — LangGraph immutability

Each LangGraph node receives the full state dict and returns a partial update:

```python
# Correct pattern — immutable update
return {**state, "intelligence": updated_intelligence}

# Wrong pattern — mutation in place (breaks LangGraph's state tracking)
state["intelligence"].novelty = 0.9
return state
```

The immutability contract is what makes the `evaluate → summarize` back-edge safe. The evaluate node does not modify the post it is evaluating — it appends `evaluation_result`. If REGENERATE fires, the summarize node has access to both the original inputs and the evaluation result, and can produce a different output.

### The jev_prefilter_scores keying bug (fixed in #61)

**Before #61:** `jev_prefilter_scores` was a flat dict with scores overwriting each other:

```python
state["jev_prefilter_scores"]["relevance"] = 0.8  # article 1
# ... later ...
state["jev_prefilter_scores"]["relevance"] = 0.6  # article 2, overwrites article 1
```

**After #61:** Keyed by `article_id`:

```python
state["jev_prefilter_scores"][article_id] = {"relevance": 0.8, "composite": 0.72}
```

This is why the `article_id` field exists on `NewsIntelligence`.

### Across runs

| Layer | Storage | Lifetime | Notes |
|---|---|---|---|
| PublishedStore | `/tmp/aifeeders_published.json` or PVC | 7-day TTL | Must be PVC on EKS/AKS |
| linkedin-mcp idempotency dict | In-process memory | Pod lifetime | Lost on restart |
| linkedin-mcp OAuth token | In-process memory | 60-day TTL | Lost on restart — requires re-auth |
| Langfuse traces | External (Langfuse cloud) | Permanent | Full audit trail, never lost |

---

## 17. Observability

### Three layers

**Layer 1 — Structured logs (always available)**

Every log line is prefixed with `run_id`. Stdout. No dependency on external services. If Langfuse is down or the Prometheus scraper is misconfigured, structured logs remain.

Key patterns to watch:

| Pattern | Meaning |
|---|---|
| `"story extracted" style= hook_len=` | MediaStorytellerAgent succeeded, story quality indicator |
| `"jev_prefilter: #1" relevance= composite=` | Top article selected, with scores |
| `"grammar_agent: corrected" before= after=` | GrammarAgent ran; char delta shows correction magnitude |
| `"grammar_agent: fallback to original"` | GrammarAgent failed; original post used (non-blocking) |
| `"post published" post_urn=` | Successful LinkedIn publish with URN |
| `"eval article=" factuality= hallucination=` | EvaluationAgent scores for audit |
| `"BLOCK: pii_detected"` | Hard block fired — post was not published |
| `"REGENERATE: factuality below threshold"` | Quality loop triggered |
| `"score_reach: REVISE, auto-repair applied"` | Reach scoring triggered post repair |

**Layer 2 — Langfuse**

Session key: `run_id`. Per-span tracking for every LLM call, every Jev call, every MCP call, and the publish event. Enables:
- End-to-end trace per article
- LLM cost breakdown per run
- Before/after comparison of REGENERATE cycles

**Layer 3 — Prometheus**

Each service exposes `GET :8000/metrics`. Standard FastAPI instrumentation: request count, latency histogram, error rate per endpoint.

---

## 18. Security Architecture

### Secrets management

| Platform | Backend | Mechanism |
|---|---|---|
| OpenShift | K8s Secrets | Injected as env vars via Deployment |
| EKS | AWS Secrets Manager | IRSA (pod ServiceAccount → IAM role → Secrets Manager) |
| AKS | Azure Key Vault | Managed Identity (pod identity → Key Vault policy) |

Secrets are **never** in code, logs, ConfigMaps, or container images. `OPENAI_API_KEY`, `GNEWS_API_KEY_1/2`, `LINKEDIN_CLIENT_ID/SECRET` are all env var injected.

One exception: Jev gateway uses `verify=False` on HTTPS calls. The Jev gateway runs on IBM internal infrastructure with a self-signed certificate. This is an intentional, documented exception — not a general disable of TLS verification.

### Network security

- `default-deny-all` NetworkPolicy at pod level
- TLS required for all external API calls
- Compromised `news-mcp` cannot reach `linkedin-mcp` (enforced by NetworkPolicy, not application logic)

### Content safety

Three hard-block Jev gates enforced at system level:

```
pii_detected > 0.5         → BLOCK (no PII on LinkedIn)
prompt_injection_detected > 0.5 → BLOCK (no prompt injection in posts)
policy_check = FAIL         → BLOCK (policy compliance enforced)
```

These cannot be disabled. `JEV_ENABLED=false` disables prefilter and routing. It does NOT disable these three gates.

### LinkedIn OAuth

- PKCE flow — no client secret in browser
- Token held in linkedin-mcp process memory only
- 60-day TTL
- Token lost on pod restart — requires human re-authentication
- This is a deliberate security trade-off: storage breach cannot expose the token

### RBAC

The pipeline's ServiceAccount has minimum permissions:

```yaml
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
```

No cluster-level permissions. No cross-namespace access. No secret read access (secrets are injected by the platform before the pod starts).

---

## 19. Is This Secure, Robust, Scalable, Distributed, Useful?

### SECURE — Yes, with documented limitations

**What works:**
- Secrets injected via platform-native secret managers — never in code or manifests
- `default-deny-all` NetworkPolicy — compromised news-mcp cannot pivot to linkedin-mcp
- Three hard-block content safety gates (PII, injection, policy) — cannot be overridden
- Non-root container, minimal base image
- Least-privilege ServiceAccount

**Known limitations:**
- LinkedIn OAuth requires human browser interaction for initial auth and after any linkedin-mcp pod restart. No fully automated token rotation is possible with the current LinkedIn OAuth model (no server-side token exchange without user interaction).
- EKS clusters silently ignore NetworkPolicy with default VPC CNI. Must install Calico or Cilium explicitly before trusting the network model.

### ROBUST — Yes, with documented failure modes

**What works:**
- Every LLM/Jev/MCP error is caught, logged with `run_id`, pipeline continues
- Jev gateway down → graceful fallback: first article, all 4 personas, LLM evaluator
- GNews key 1 exhausted → auto-rotation to key 2
- LLM timeout → article skipped, run continues
- Duplicate run → 3-gate deduplication prevents duplicate LinkedIn post
- Content quality failure → REGENERATE loop (max 2 retries), then BLOCK if safety gates fire

**Known fragility:**
- linkedin-mcp is a single-replica stateful pod. Any restart requires manual OAuth re-authentication. This is a deliberate security trade-off, but it is a real operational burden at 2× daily publish cadence.

### SCALABLE — Yes for stateless layers. No for stateful singletons.

**What scales:**
- `daily-news-api`: HPA 2–4, stateless — scales horizontally with load
- `news-mcp`, `evaluation-mcp`: 2 replicas each, stateless

**What doesn't scale:**
- `linkedin-mcp`: 1 replica max — OAuth token in process memory. Path to horizontal scaling: store token in K8s Secret + implement refresh token flow.
- `pageindex-mcp`: 1 replica max — per-run in-memory index. Path to horizontal scaling: Redis or shared PVC for the document tree.
- CronJob: `concurrencyPolicy=Forbid` — designed for editorial cadence, not real-time throughput.

### DISTRIBUTED — Yes

Five independent microservices over HTTP, each with:
- Own Dockerfile and container image
- Own Kubernetes Deployment
- Own health checks (`GET /health`)
- Own resource limits
- Own Prometheus metrics endpoint
- Independent restartability

Loose coupling: the MCP tool interface is stable. `news-mcp` and `linkedin-mcp` could be replaced with completely different implementations without changing any code in `daily-news-api`.

Cross-platform: all K8s manifests deploy unchanged on OpenShift, EKS, and AKS. Only the image registry URL and secret injection mechanism differ.

### USEFUL FOR END USERS — Yes, with honest scope boundaries

**Who benefits and how:**
- LinkedIn audience (Senior/Director/VP AI practitioners): daily post with the non-obvious angle, four expert character voices — a news programme in text, not a news summary.
- Content creator running the pipeline: fully automated pipeline producing posts worth reading, not generic summaries.
- Junior engineer learning production AI: working end-to-end example of LangGraph + MCP + Jev + LLM with real error handling.
- AI architect evaluating vectorless RAG: production demonstration of PageIndex replacing a vector database for structured retrieval.

**Known limitations:**
- Publishes to one LinkedIn account. No per-follower personalisation. No A/B content testing.
- Stage 7 feedback loop (impressions → audience learning) is not yet implemented. The pipeline does not learn from post performance.
- The 5th character (Working Professional, `prompts/labor.txt`) is fully built but blocked on LinkedIn Comments API "Community Management" permission, which has not yet been granted.

---

## 20. Change Log

### Build #83
- `_compose_main_post` rewritten — dialogue-delivery format replaces structured bullet list
- GrammarAgent added (`prompts/grammar.txt`, `temperature=0`, best-effort, never blocks)
- All 4 persona prompts rewritten — 3–5 sentence story passages instead of one-liners
- `labor.txt` documented as 5th voice; Comments API permission pending
- `POST_LIMIT` raised from 2900 to 3000 (dialogue format has better per-paragraph budget management)
- All 210 tests pass

### Build #82
- `NewsStory` model added (`models/intelligence.py`)
- `MediaStorytellerAgent` added (`summary_agent.py`, `prompts/storyteller.txt`)
- Two-pass `summarize` node (Pass 1: storytelling at `temperature=0.5`; Pass 2: facts at `temperature=0.2`)
- `story` field added to `NewsSummary` and `NewsIntelligence`
- All 13 story fields passed to persona agents as context
- 15 narrative styles defined and implemented
- `score_reach` node added (reach scoring 0–100, PUBLISH/REVISE verdict)
- GNews sole provider (NewsDataIO removed)

### Build #81
- `score_reach` node scaffolded
- Reach scoring algorithm (pure text, 0–100, no LLM)
- Auto-repair on REVISE verdict

### Build #80
- Jaccard title similarity deduplication added (Gate 1, threshold 0.55)
- `jev_prefilter_scores` keyed by `article_id` (cross-article score leak fix from #61 finalised)

### Build #61
- `_linkedin_len()` UTF-16 character counting fix
- MCP nested error detection in `MCPHTTPClient`
- `POST_LIMIT` set to 2900
- `jev_prefilter_scores` flat-dict cross-article score leak identified and patched

---

*AIFeeders Architecture Reference — Build #83*  
*A vectorless AI News Intelligence pipeline: GNews → PageIndex → Jev → MediaStorytellerAgent → PersonaAgentFactory → GrammarAgent → LinkedIn*
