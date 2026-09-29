# RichContent.md — AIFeeders Content Strategy, Format Guide & Quality Standards

> **What this is:** The editorial constitution for AIFeeders output.
> Every post, every persona, every CTA is governed by the rules in this document.
> If it reads like a press release, something in the pipeline violated these rules.
> If it sounds like it was written by an AI — same problem.

---

## The One Sentence That Defines This Platform

**This is not a LinkedIn post generator. This is a live broadcast editorial round-table program delivered in text and a 6-panel comic strip.**

The reader should feel they are watching an insightful host moderate a live debate between four real practitioners with genuinely different views — not reading a summary with emoji decorations. Four voices. One conflict graph. One unresolved tension handed to the reader.

---

## Section 1 — What the Reader Sees (Post & Comic Strip Anatomy)

AIFeeders publishes content as a **Visual Comic Strip Post**. The full multi-persona debate lives inside a high-resolution 6-panel comic image rendered by [`ComicGenerator`](src/daily_news/agents/comic_generator.py), while the LinkedIn post text block is constructed by [`PublisherAgent._compose_main_post()`](src/daily_news/agents/publisher_agent.py) and published via the LinkedIn MCP.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  AIFEEDERS COMIC STRIP & POST ANATOMY                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  [ATTACHED VISUAL ASSET — 1440 × 888px COMIC STRIP IMAGE]                       │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │ 🧠 AIFEEDERS · ONE NEWS. MULTIPLE REAL-WORLD VOICES.     ● AI NEWS        │  │
│  │ HEADLINE: Major AI capability or strategic development                    │  │
│  │ SUBHEAD: Narrative context / tension line  SOURCE: Publisher Name         │  │
│  │──────────────── NEWS CARD ───── WHAT IT MEANS ↓ ───── DEBATE ────────────│  │
│  │ ┌──────────────────┬───────────────────┬───────────────────────┐          │  │
│  │ │ ① 🎙️ MEDIA HOST   │ ② 💼 FOUNDER      │ ③ 🧑‍💻 ENGINEER          │          │  │
│  │ │ News Brief       │ Opportunity Angle │ Production Reality    │          │  │
│  │ │ + TODAY'S VOICES │ → FOUNDER answers │ → ENGINEER contradicts│          │  │
│  │ │   roster card    │   MEDIA HOST      │   FOUNDER             │          │  │
│  │ ├──────────────────┼───────────────────┼───────────────────────┤          │  │
│  │ │ ④ 📊 ANALYST      │ ⑤ 🏛️ POLICY       │ ⑥ 🎙️ MEDIA HOST        │          │  │
│  │ │ Premise Challenge│ Governance Layer  │ SYNTHESIZING DEBATE   │          │  │
│  │ │ → SKEPTIC shifts │ → POLICY adds     │ The structural tension│          │  │
│  │ │   the frame      │   accountability  │ that survived debate  │          │  │
│  │ └──────────────────┴───────────────────┴───────────────────────┘          │  │
│  │                      WHERE DO YOU STAND? 👇                               │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  [OUTSIDE-IMAGE LINKEDIN POST TEXT BLOCK]                                       │
│  ───────────────────────────────────────────────────────────                    │
│  🧠 AI NEWS  |  <Headline bold>                                                 │
│  <Hook line — article-specific, event-typed>                                    │
│                                                                                 │
│  👇 See image — four voices, one story.                                         │
│                                                                                 │
│  💼 <Name> → <Founder teaser>                                                   │
│  🧑‍💻 <Name> → <Engineer teaser>                                                  │
│  📊 <Name> → <Analyst teaser>                                                   │
│  🏛️ <Name> → <Policy teaser>                                                    │
│                                                                                 │
│  Their answers don't completely agree. That's exactly the point.                │
│                                                                                 │
│  🎙️ <HostName> — THE AIFEEDERS QUESTION:                                        │
│  <Forced-choice CTA (numbered options A/B/C) anchored to THIS article>          │
│                                                                                 │
│  🗞️ Source: <Publisher>  🔗 <Article URL>                                       │
│                                                                                 │
│  ⚠️ Perspectives are AI-simulated for discussion — not professional advice.     │
│  🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev                      │
│                                                                                 │
│  #DynamicHashtag1 #NamedEntity #EventType #AI                                   │
│  ───────────────────────────────────────────────────────────                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Why this two-layer format?**
- The **comic strip** contains the full debate — it drives dwell time, saves, and shares
- The **post text** is the hook that gets people to stop scrolling and click "see more"
- LinkedIn's algorithm rewards the combination of image + substantive text + engagement CTA

---

## Section 2 — The Four Personas and Their Roles in the Conflict Graph

Every article generates a **CONFLICT GRAPH** before persona generation runs. This graph defines the two opposing forces in the story and ensures each persona speaks from a distinct position within that conflict — not four parallel opinions.

```
CONFLICT GRAPH (story spine — built by _build_conflict_graph()):
  NEWS:        <headline>
  OPPORTUNITY: <what the event enables — business/tech gain>
  RISK:        <what the event creates — governance/dependency problem>
  → FOUNDER   sees the OPPORTUNITY  (cost reduction, market timing)
  → ENGINEER  sees the RISK         (architecture, operational debt)
  → ANALYST   challenges both       (who wins at platform scale)
  → POLICY    adds governance layer (accountability, concentration)
  → HOST      names the unresolved tension that survives the debate
```

### Persona Routing

Not every article activates all four personas. `jev_router` determines which are relevant:

```
┌─────────────────┬────────────────────────────────────────────────────┐
│ Event Type      │ Natural persona fit (Jev routes from content)       │
├─────────────────┼────────────────────────────────────────────────────┤
│ product_launch  │ ENGINEER · FOUNDER · ANALYST                       │
│ funding         │ FOUNDER · ANALYST · ENGINEER                       │
│ acquisition     │ FOUNDER · ANALYST · POLICY                         │
│ regulation      │ POLICY · ENGINEER · FOUNDER · ANALYST (all 4)      │
│ research        │ ENGINEER · ANALYST · FOUNDER · POLICY (all 4)      │
│ other           │ FOUNDER · ANALYST · ENGINEER                       │
└─────────────────┴────────────────────────────────────────────────────┘

Fallback: all 4 personas when JEV_ENABLED=false or routing fails.
Skipped personas receive a stub (empty perspective) — publisher omits them.
```

**Why selective routing?** Policy voice adds no value to a semiconductor chip release. Forcing it produces generic regulatory throat-clearing that the LLM judge catches and REGENERATEs. Jev routes only voices that have substantively different things to say about this specific article.

---

## Section 3 — The Four Personas: What They Are and Are NOT

### 💼 FOUNDER — AI Infrastructure Founder (prompts/capitalist.txt)

**Mental model:** Unit economics, market timing, competitive position, tool consolidation, platform dependency.

**Voice register:** OPPORTUNITY VOICE. Genuinely optimistic. Makes a concrete, arguable commercial claim that the Engineer will directly contradict.

**What they must say:**
- A specific business constraint or cost structure implication from THIS article
- A concrete market timing or competitive position argument
- A claim the Engineer can directly challenge

**What they must NEVER say:**
- `"Sounds great on paper"` / `"This sounds promising"` → banned
- `"When I was scaling my last startup..."` → banned anecdote opener
- Agreement with the engineer's point → kills the debate
- Any hedge: `"on the other hand"`, `"but we should also consider"` → banned

**Opens like:** `"At $X per query, the unit economics only work if..."` or `"The first enterprise that adopts this platform effectively can gain..."`

---

### 🧑‍💻 ENGINEER — ML Platform Engineer (prompts/linkedin.txt)

**Mental model:** Architecture, reliability, identity/permissions/observability, operational debt. Simplification promises move complexity underneath the platform — it does not disappear.

**Voice register:** OPERATIONAL REALITY VOICE. Directly contradicts the Founder's commercial premise with a specific operational constraint.

**What they must say:**
- A specific failure mode in production (not demo conditions)
- The team or system that actually absorbs the complexity
- A concrete integration challenge with existing enterprise infrastructure

**What they must NEVER say:**
- `"The real challenge lies in..."` → banned throat-clearing
- Agreement with the Founder's framing → kills the debate
- `"My advice to colleagues would be..."` → banned
- Generic praise followed by a concern

**Opens like:** `"The first enterprise that adopts this platform effectively will have to redesign their IAM stack, not just add a new integration layer."` or `"The retry storm hits at 3 AM when the agent burns through OAuth token limits."`

---

### 📊 ANALYST — AI Industry Analyst (prompts/genz.txt)

**Mental model:** Who wins at platform scale, second-order displacement, switching cost. Challenges the FRAMING of both Founder and Engineer — shifts from "does this work" to "who does this benefit at scale".

**Voice register:** MARKET DYNAMICS VOICE. Never both-sides the debate. Asks: who becomes the default workspace and what does that mean for everyone else?

**What they must say:**
- A challenge to the assumption embedded in the headline
- A second-order consequence that Founder and Engineer both missed
- A harder question about platform power and downstream displacement

**What they must NEVER say:**
- `"Both sides have valid points"` → kills the debate
- The same concern the Engineer already raised → redundant
- A balanced hedge in both directions → forbidden

**Opens like:** `"The competition was never about who had the best model."` or `"When you make building 10× easier, you don't get less friction — you get 10× more software to monitor."`

---

### 🏛️ POLICY — AI Policy Lead (prompts/policy.txt)

**Mental model:** Specific regulatory gap, liability chain, auditability requirement, accountability concentration.

**Voice register:** GOVERNANCE VOICE. Adds the accountability layer that Engineer and Analyst both missed. Names the specific governance gap — not a generic regulation checkbox.

**What they must say:**
- A named regulation that is actually relevant to THIS article's topic
- A specific accountability or liability gap created by THIS announcement
- A compliance implication for enterprise deployers of THIS technology

**What they must NEVER say:**
- `"Under the EU AI Act..."` when the article is NOT about EU regulation → hard ban
- `"Consider a scenario where..."` → banned hypothetical setup
- `"Imagine a company that..."` → banned hypothetical
- Policy commentary on non-policy stories (chip launches, product releases)

**Opens like:** `"When one platform becomes the default workspace, governance becomes part of the product — not a checkbox."` or `"GDPR Article 22 applies the moment this agent makes automated decisions on customer data..."`

---

## Section 4 — Banned Phrases (Automatic REGENERATE)

The pipeline enforces two distinct scanners:

### Layer 0 — Deterministic Pre-Scanner (runs BEFORE any LLM call)

[`_check_persona_text()`](src/daily_news/agents/publisher_agent.py) in `publisher_agent.py` — 100% reliable, zero false negatives. Any hit triggers immediate `REGENERATE` without calling Jev or the LLM judge.

```
BANNED OPENERS (first 200 chars — automatic REGENERATE):
  "when i was scaling..."         "when i was building..."
  "when we deployed..."           "when we rolled out..."
  "when we launched..."           "when we were..."
  "imagine you are..."            "imagine you're..."
  "imagine a startup..."          "imagine running..."
  "consider a scenario..."        "let me paint a picture..."
  "let me be clear..."            "i've been in..."
  "i recently..."                 "sounds great on paper..."
  "sounds promising on paper..."

BANNED INLINE PHRASES (full text — automatic REGENERATE):
  "the real challenge lies in"    "the real question is"
  "at the end of the day"         "it remains to be seen"
  "only time will tell"           "sounds great, but"
  "sounds promising, but"         "is a game-changer"
  "game-changing"                 "in the end,"
  "the real test is"              "marks a significant"
  "marks a major milestone"       "this is a significant step"
  "this changes everything"       "will fundamentally change"
  "the future of ai"              "ai is transforming"
  "has the potential to revolutionize"
  "this is a strategic move"      "this is a bold move"

REGEX CATCH-ALLS (automatic REGENERATE):
  "the real <challenge|question|test|issue|risk|concern|...> is/lies/isn't/becomes"
  "marks? a significant"
  "the first major <issue|challenge|concern> will be"
  "in the end"   (any form)
```

### Layer 2 — LLM Judge (Qwen @ temp=0.1)

The judge enforces story-level quality only — phrase-level scanning is done upstream. The judge sets `verdict=REVISE` **only if ALL THREE** are simultaneously true:

1. No genuine intellectual clash — every persona reaches the same conclusion
2. Completely generic — could apply word-for-word to any AI news story
3. Zero grounded facts — not one claim traceable to the source article

The judge also forces `REGENERATE` if:
- EU AI Act / GDPR mentioned when source article is not about EU regulation

**Why two separate layers?** The deterministic scanner is instant and infallible for known patterns. The LLM judge catches story-level failures (no clash, generic content) that no regex can detect. Running both eliminates the failure mode of each.

---

## Section 5 — The LinkedIn Skills Optimization Layer

After persona generation, `LinkedInSkillsOptimizer` runs three passes **before** the evaluation gate:

```
generate_personas → [linkedin_optimize] → evaluate → publish

Pass 1 — HOOK SELECTOR
  Scores the current hook against 2026 LinkedIn formula heuristics.
  Optionally generates up to 3 alternative hook candidates.
  Selects the strongest opening.

  2026 winning hook formulas (used by AIFeeders):
    F7  — Number-first (odd-precision stat or cost figure in line 1)
          "+34% median likes vs vague openers"
    F10 — Contrarian + historical receipt (challenge a sacred cow with a dated fact)
          "comments-optimised"
    F18 — False-binary dissolve (EXACTLY ONE contrast — dissolves the false choice)
          "comments/reposts"

Pass 2 — HUMANIZER
  Deterministic scrub — no LLM call:
  • AI-vocabulary density (leverage, streamline, fundamentally, game-changer...)
  • Reveal bridges ("The result?", "Here's what", "Plot twist:")
  • Stacked triads (X, Y, and Z for all three)
  • Staccato fragment runs (≥3 consecutive 1-sentence paragraphs)
  • Performed-sincerity patterns (let that sink in, quietly, built different)
  NEVER changes: persona identities, factual claims, source URLs, debate structure

Pass 3 — AUDIT
  LLM-backed scoring on 5 dimensions:
  • hook_strength    (0–1) — first 2 visible lines before "see more"
  • commentability   (0–1) — does the CTA invite substantive reply?
  • ai_style_density (0–1) — fraction of AI-generated vocabulary
  • cta_quality      (0–1) — forced-choice vs open-ended
  • algorithm_compliance (0–1) — 2026 LinkedIn reach signals

  Scores stored in persona_outputs["linkedin_audit"] for the evaluation gate.
  Low hook_strength or commentability → failure_reason injected for next retry.
  Does NOT hard-block — advisory signals only.
```

---

## Section 6 — The 7 Signature Content Formats

`MediaStorytellerAgent` selects one narrative format per article based on the story's tension model:

| Format | Best for | What makes it work |
|---|---|---|
| **The AI Debate** | Product launches, capability announcements | Contrarian hook + asymmetric clash (builder vs operator vs deployer) |
| **You Are the Investor** | Funding rounds, acquisitions | $100M capital allocation dilemma — reader forced to put skin in the game |
| **The Uncomfortable AI Truth** | Hype cycle stories | Exposes gap between benchmark and production survival |
| **AI Architecture Battle** | Infrastructure, model releases | Technical showdown: agentic graphs vs RAG vs MCP tools |
| **The AI Postmortem** | Enterprise AI failures, cautionary stories | Systems analysis: why a working demo failed in Monday production |
| **Prediction Without Predicting** | Regulation, long-term trends | Strategic 3-year bet — reader defends their pick against challengers |
| **One Diagram → One Question** | Architecture papers, research | Clean system flow + single architectural vulnerability question |

**Why these 7 and not more?** Every format maps to a specific LinkedIn engagement mechanic. A format not on this list either lacks a clear conflict structure (no genuine disagreement possible) or produces a CTA no practitioner will answer (too abstract or too broad).

---

## Section 7 — What Good Looks Like vs What Fails

### ✅ PASS — what a good post looks like

```
🧠 AI NEWS | Anthropic Ships 200K-Token Context Window

The benchmark is clean. The Monday morning reality for enterprise teams is more complicated.

👇 See image — four voices, one story.

💼 Elena → 200K context at $15/MTok means a single enterprise legal review workflow
   costs $0.94 per document. At 10K documents per week that's $9,400...
🧑‍💻 Marco → 200K tokens sounds infinite until you hit the retrieval latency wall.
   At 150K tokens the P99 latency crosses 8 seconds on Claude 3.5 Sonnet...
📊 Priya → The token window grew 4× in 18 months. The ability to verify what the
   model attended to inside that window didn't grow at all...
🏛️ James → When a single vendor controls the context window AND the embedding
   model AND the retrieval layer, audit rights become a contract negotiation...

Their answers don't completely agree. That's exactly the point.

🎙️ Tara — THE AIFEEDERS QUESTION:
The context window is no longer the constraint. The cost, latency, and audit surface are.

For teams already deploying document AI:
A. The cost curve kills most use cases before latency does
B. Latency kills synchronous workflows before cost does
C. Hallucination audit complexity kills both
D. None — 200K context genuinely unlocks something new

Where do you stand? 👇

🗞️ Source: VentureBeat  🔗 https://venturebeat.com/...
#Anthropic #Claude #EnterpriseAI #AIProductLaunch #AI
⚠️ Perspectives are AI-simulated — not professional advice.
🤖 AIFeeders · Daily AI Intelligence · Powered by Jev
```

**Why this passes:**
- Every voice opens with a specific number or named constraint — zero generic framing
- Genuine disagreement: FOUNDER (cost), ENGINEER (latency), ANALYST (verification), POLICY (vendor lock) — four different blockers, none overlapping
- CTA is article-specific and numbered — forces a one-character reply
- No banned phrases anywhere
- Hook is before the fold and contains a specific claim

---

### ❌ REGENERATE — what a bad post looks like

```
💼 Founder
"When I was scaling my last startup, we were always looking for ways to reduce
costs and improve productivity. The Anthropic update sounds great on paper, but
the real challenge lies in integrating it into existing workflows..."

🧑‍💻 Engineer
"This is an exciting development! As someone who has worked with large language
models extensively, longer context windows can be very useful. However, we need
to be careful about the implications for our systems. The key question here is
whether it will be adopted..."

[HOST]
"Marks a significant milestone for enterprise AI..."
```

**Why this fails (and which layer catches it):**
- Layer 0 — `"When I was scaling my last startup"` → banned opener → immediate REGENERATE (no LLM call wasted)
- Layer 0 — `"sounds great on paper"` → banned phrase → REGENERATE
- Layer 0 — `"the real challenge lies in"` → banned throat-clearing → REGENERATE
- Layer 0 — `"The key question here is"` → banned phrase → REGENERATE
- Layer 0 — `"Marks a significant milestone"` → regex catch-all → REGENERATE
- Layer 2 — Even if Layer 0 missed everything, the judge would catch: both voices basically agree (no genuine clash), completely generic (could apply to any AI announcement), zero grounded facts

---

## Section 8 — The Reach Score: What the System Optimises For

Before publishing, `ReachScoreAgent` scores the assembled post on 6 dimensions. Score < 55 triggers deterministic auto-repair (no LLM call):

```
┌─────────────────────────────────────────────────────────────────┐
│  REACH SCORE DIMENSIONS (0–100 total)                           │
├───────────────────┬──────────┬──────────────────────────────────┤
│ Dimension         │ Max pts  │ What earns full score            │
├───────────────────┼──────────┼──────────────────────────────────┤
│ Hook Strength     │ 20       │ Strong opener in 220-char window │
│ Specificity       │ 20       │ Numbers + named entities + source│
│ Question Quality  │ 20       │ Numbered-choice forced CTA       │
│ Length Fit        │ 15       │ 150–300 words (LinkedIn optimum) │
│ Bait Penalty      │ 15       │ Zero engagement-bait patterns    │
│ Topic Coherence   │ 10       │ ≥ 3 AI/tech domain signals       │
├───────────────────┼──────────┼──────────────────────────────────┤
│ TOTAL             │ 100      │ ≥ 55 = PUBLISH  < 55 = auto-repair│
└───────────────────┴──────────┴──────────────────────────────────┘
```

**What auto-repair does (no LLM — pure deterministic text cleanup):**
1. Strips hashtags to 3 maximum (keeps topical over generic)
2. Removes lines containing engagement-bait patterns (`comment YES if`, `like if you agree`)
3. Trims to 300-word ceiling at the last sentence boundary

**Pre-publish reach score stored in `state["reach_scores"]`** for observability. If repair is applied, both before/after scores are logged.

---

## Section 9 — Why This Format Drives LinkedIn Engagement

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ENGAGEMENT MECHANICS                                                    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. HOOK BEFORE THE FOLD  (220-char window = what's visible pre-click)  │
│     Strong hook forces reader to expand → increases dwell time          │
│     LinkedIn algorithm rewards dwell time in feed ranking               │
│     2026 formula: number-first (+34% median likes) wins all formats     │
│                                                                          │
│  2. GENUINE CONFLICT BETWEEN VOICES                                     │
│     Judge enforces that at least 2 personas genuinely disagree          │
│     Reader takes a side → comment friction drops dramatically            │
│     "Who do you agree with?" is the unasked question driving replies    │
│                                                                          │
│  3. FORCED-CHOICE CTA (NOT OPEN-ENDED)                                  │
│     A/B/C/D numbered options lower barrier to comment by 10×            │
│     Reader types a letter + one sentence → zero writing friction        │
│     Produces 10× more comments than "What do you think?"               │
│                                                                          │
│  4. COMIC STRIP IMAGE  (the real engagement driver)                     │
│     Full debate visible at a glance — drives saves and shares           │
│     Image + text = dwell time signal the algorithm rewards              │
│     Branded panel layout builds recognition across posts                │
│                                                                          │
│  5. DYNAMIC HASHTAGS (named entity + AEO, not static)                  │
│     Company hashtags reach that company's followers (#MicrosoftCopilot) │
│     Static #AI #Tech get zero algorithmic amplification today           │
│     Entity-based hashtags distribute to the right professional feed     │
│                                                                          │
│  6. SIMULATED PERSONA DISCLAIMER                                        │
│     "AI-simulated perspectives — not professional advice"               │
│     OutputGuardrail enforces its presence before every publish          │
│     Protects against fake-quote allegations and platform policy risk    │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Section 10 — Closed-Loop Content Optimization

After each post is published, `ContentOptimizerAgent` runs to feed the learning loop:

```
LinkedIn Engagement Signals
(impressions · reactions · comments · reposts · engagement_rate)
               │
               ▼
    ┌────────────────────────────────┐
    │   ContentOptimizerAgent        │
    │                                │
    │   diagnose_performance()       │
    │   → scores 6 structural        │
    │     components:                │
    │     hook / story / audience /  │
    │     format / specificity /     │
    │     question                   │
    │   → identifies weakest_component│
    │   → actionable_recommendation  │
    └───────────┬────────────────────┘
               │
               ▼
    ┌────────────────────────────────┐
    │     Story Mutations            │
    │   mutate_story()               │
    │   → generates variants         │
    │   → stored as calibration for  │
    │     future Jev editorial angle │
    │     recommendations            │
    └────────────────────────────────┘
```

**What gets stored (structural mutations, not raw metrics):**
```
"Hook rotation slot 3 consistently underperforms on regulation stories.
 Prefer slot 0 (direct policy consequence opener) for governance events."

"Funding stories with 3-voice composition see higher CTA reply rates
 than 4-voice compositions for the same article type."

"ANALYST voice generates 40% more comment replies when it challenges
 the Founder's specific claim rather than reframing the market generally."
```

These mutations calibrate future `find_angle` Jev calls — the system learns which editorial angles drive real practitioner engagement, not just impressions.

---

## Section 11 — Content Standards Checklist (Pre-Publish Mental Model)

The pipeline enforces all of these automatically. This checklist is for human review:

| Check | Standard | Enforced by |
|---|---|---|
| Every persona opens with a concrete claim | ✅ No anecdote, no setup, no compliment | `_check_persona_text()` Layer 0 |
| Genuine disagreement exists | ✅ At least 2 voices in real conflict | LLM judge Layer 2 |
| All claims traceable to source article | ✅ Nothing invented | `EvaluationAgent` factuality score |
| No EU AI Act cited for non-regulation article | ✅ Guardrail hard-ban | LLM judge prompt |
| CTA is article-specific | ✅ Not a reusable generic question | `_build_cta()` deterministic builder |
| Hashtags are named entities or AEO event types | ✅ No generic #AI #Tech only | `_extract_dynamic_tags()` |
| No banned phrases detected | ✅ Scanner and judge both clear | Layer 0 + Layer 2 |
| Post length 150–300 words | ✅ LinkedIn sweet spot | `ReachScoreAgent` length_fit |
| Reach score ≥ 55 | ✅ Or auto-repair applied | `ReachScoreAgent.repair()` |
| OutputGuardrail passed | ✅ No fake quotes, disclaimer present | `OutputGuardrail.inspect_output()` |
| LinkedIn Audit scores logged | ✅ hook_strength, commentability visible | `LinkedInSkillsOptimizer` |
| Comic strip image attached | ✅ Post never published text-only | `ComicGenerator` + LinkedIn MCP |

---

## Section 12 — Reading the Content Quality Signal in Logs

Every run emits structured quality signals. Here is what to look for:

```
# Successful quality flow:
[RUN-XX] eval article=abc123 decision=PASS factuality=0.88 groundedness=0.84 hallucination=0.04
[RUN-XX] llm_judge article=abc123 verdict=PASS quality=0.91 conflict=True narrative=True
[RUN-XX] linkedin_optimize article=abc123 hook=0.85 comment=0.79 ai_density=0.12 overall=0.82 formula=F7
[RUN-XX] score_reach article=abc123 total=82 verdict=PUBLISH hook_strength=8 specificity=9

# Banned phrase — Layer 0 catch (fastest path to regenerate):
[RUN-XX] WARNING pre_scan persona=business article=abc123 banned_phrase='when i was scaling'
[RUN-XX] WARNING deterministic_scanner REGENERATE article=abc123

# LLM judge regenerate — Layer 2 story quality:
[RUN-XX] WARNING llm_judge REGENERATE article=abc123 boilerplate=False throat=True critique='...'

# LinkedIn audit advisory (does not block, informs next retry):
[RUN-XX] linkedin_audit article=abc123 hook=0.32 commentability=0.41 → failure_reasons injected

# Reach repair:
[RUN-XX] score_reach: score 48 below threshold 55 — applying auto-repair
[RUN-XX] score_reach: after repair total=71 verdict=PUBLISH
```

**What each signal means for content quality:**
- `factuality < 0.50` → persona made claims not in source article → REGENERATE
- `groundedness < 0.50` → post not grounded in article evidence → REGENERATE
- `verdict=PASS quality > 0.80` → strong, specific, in-conflict post
- `hook_strength < 0.50` → opener too weak for pre-fold window → retry hint
- `ai_density > 0.60` → too many AI-vocabulary words → humanizer pass needed
- `reach_score < 55` → structural reach issues → auto-repair applied

---

*AIFeeders Content Strategy & Editorial Standards — the system enforces this automatically. Read this document when adding new personas, changing prompt files, or debugging persistent REGENERATE loops.*
