# RichContent.md — AIFeeders Content Strategy, Format Guide & Quality Standards

> **What this is:** The editorial constitution for AIFeeders output.
> Every post, every persona, every CTA is governed by the rules in this document.
> If it reads like a press release, something in the pipeline violated these rules.

---

## The One Sentence That Defines This Platform

**This is not a LinkedIn post generator. This is a live broadcast editorial round-table program delivered in text.**

The reader should feel they are watching an insightful host moderate a live debate between four real practitioners with genuinely different views — not reading a summary with emoji decorations.

---

## Section 1 — What the Reader Sees (Post & Comic Strip Anatomy)

AIFeeders publishes content as a **Visual Comic Strip Post**. The full multi-persona debate lives inside the high-resolution 6-panel comic image asset rendered by [`ComicGenerator`](src/daily_news/agents/comic_generator.py), while the LinkedIn post text block is constructed strictly by [`build_outside_post()`](src/daily_news/agents/comic_generator.py:794) and published via [`PublisherAgent.publish()`](src/daily_news/agents/publisher_agent.py).

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  AIFEEDERS COMIC STRIP & POST ANATOMY                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  [ATTACHED VISUAL ASSET — 1440 × 888px COMIC STRIP IMAGE]                       │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │ 🧠 AIFEEDERS · ONE NEWS. MULTIPLE REAL-WORLD VOICES.                      │  │
│  │ HEADLINE: Major AI capability or strategic development                    │  │
│  │ SUBHEAD: Narrative context / tension line                                 │  │
│  │ ┌───────────────────┬───────────────────┬───────────────────┐             │  │
│  │ │ ① 🎙️ MEDIA HOST    │ ② 💼 FOUNDER      │ ③ 🧑‍💻 ENGINEER    │             │  │
│  │ │ News Brief +      │ Opportunity Angle │ Production Reality│             │  │
│  │ │ Hand-off Question │ Answering Host    │ Answering Founder │             │  │
│  │ ├───────────────────┼───────────────────┼───────────────────┤             │  │
│  │ │ ④ ⚖️ SKEPTIC       │ ⑤ 🏛️ POLICY       │ ⑥ 🎙️ MEDIA HOST    │             │  │
│  │ │ Premise Challenge │ Governance / Risk │ Synthesis & Poll  │             │  │
│  │ │ Answering Eng.    │ Answering Skeptic │ Question to Reader│             │  │
│  │ └───────────────────┴───────────────────┴───────────────────┘             │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  [OUTSIDE-IMAGE LINKEDIN POST TEXT BLOCK]                                       │
│  ───────────────────────────────────────────────────────────                    │
│  📰 Source: <Publisher Name>                                                    │
│  🔗 https://... (Article Canonical URL)                                         │
│                                                                                 │
│  🎙️ <HostName> — To the Audience:                                               │
│                                                                                 │
│  <Audience Discussion Question & Multi-choice Options>                          │
│                                                                                 │
│  Where do you stand? Drop your take below 👇                                    │
│                                                                                 │
│  ⚠️ Perspectives are AI-simulated — not professional advice.                    │
│  🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev                      │
│                                                                                 │
│  #Dynamic #Hashtags #EntityBased #AIFeeders                                     │
│  ───────────────────────────────────────────────────────────                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Section 2 — Dynamic 3 vs 4 Voice Composition

Not every story needs 4 voices. Voice count is determined by `event_type` and `controversy_level` from IBM Jev:

```
┌─────────────────┬─────────────────────────────────────────────────────────┐
│ Event Type      │ Voice Order (left = opens, right = optional 4th)        │
├─────────────────┼─────────────────────────────────────────────────────────┤
│ product_launch  │ 🧑‍💻 ENGINEER → ⚖️ SKEPTIC → 💼 FOUNDER         (3)     │
│ funding         │ 💼 FOUNDER → ⚖️ SKEPTIC → 🧑‍💻 ENGINEER           (3)     │
│ acquisition     │ 💼 FOUNDER → ⚖️ SKEPTIC → 🏛️ POLICY              (3)     │
│ regulation      │ 🏛️ POLICY → 🧑‍💻 ENGINEER → ⚖️ SKEPTIC → 💼 FOUNDER  (4) │
│ research        │ 🧑‍💻 ENGINEER → ⚖️ SKEPTIC → 💼 FOUNDER → 🏛️ POLICY  (4) │
│ other           │ 💼 FOUNDER → ⚖️ SKEPTIC → 🧑‍💻 ENGINEER           (3)     │
└─────────────────┴─────────────────────────────────────────────────────────┘

Why 3 voices for product launches and funding?
  → Policy voice is not credibly relevant to a chip launch
  → Forces each voice to speak with more precision (no filler fourth view)
  → Reduces token cost ~25% per run
  → Judge detects genuine clash more reliably with 3 focused voices

Why 4 voices for regulation?
  → Regulation stories have genuine legal, technical, business AND societal dimensions
  → All 4 archetypes have substantively different things to say
  → High controversy = more voices = more reader alignment opportunities
```

---

## Section 3 — The Four Personas: What They Are and Are NOT

### 💼 FOUNDER (prompts/capitalist.txt)

**Mental model:** Unit economics, customer acquisition cost, moat defensibility, operational burn.

**What they must say:**
- A specific business constraint or cost structure implication
- A question about moat durability or commoditisation risk
- A capital efficiency or margin observation

**What they must NEVER say:**
- `"Sounds great on paper"` / `"This sounds promising"`
- `"When I was scaling my last startup..."` (anecdote opener)
- Agreement with the engineer's point

**Opens like:** `"At $X per query, the unit economics only work if..."` or `"Enterprise lock-in here runs two years at minimum based on..."`

---

### 🧑‍💻 ENGINEER (prompts/linkedin.txt)

**Mental model:** Integration debt, latency SLAs, data pipeline complexity, security perimeters.

**What they must say:**
- A specific failure mode in production (not demo)
- A hidden integration challenge with existing enterprise systems
- A security or observability concern at scale

**What they must NEVER say:**
- `"The real challenge lies in..."` (banned throat-clearing)
- Agreement with the founder's framing
- `"My advice to colleagues would be..."`

**Opens like:** `"At 3 AM when the agent burns through OAuth token limits, the retry storm hits..."` or `"The demo runs on a clean dataset. Production has 12 years of schema drift."`

---

### ⚖️ SKEPTIC (prompts/genz.txt)

**Mental model:** Challenges the fundamental premise, not just the implementation details.

**What they must say:**
- A challenge to the assumption embedded in the headline
- A second-order consequence everyone else missed
- A harder question than anyone else is asking

**What they must NEVER say:**
- A balanced view that hedges in both directions
- `"Both sides have valid points"` (kills the debate)
- The same concern the engineer already raised

**Opens like:** `"Generating software is becoming easier. Operating software isn't moving at all."` or `"The benchmark measures training speed. Nobody measures the cost of the engineer-hours spent debugging what it generates."`

---

### 🏛️ POLICY (prompts/policy.txt)

**Mental model:** Specific regulatory gap, liability chain, auditability requirement.

**What they must say:**
- A named regulation that is actually relevant (not generic "EU AI Act" for a chip launch)
- A specific liability or accountability gap in THIS announcement
- A compliance implication for enterprise deployers

**What they must NEVER say:**
- `"Under the EU AI Act..."` when the article is NOT about regulation
- `"Consider a scenario where..."` (banned setup)
- `"Imagine a company that..."` (banned hypothetical)

**Opens like:** `"GDPR Article 22 applies the moment this agent makes automated decisions on customer data without..."` or `"The FTC's draft AI rule specifically names this capability as requiring human review at..."`

---

## Section 4 — Banned Phrases (Automatic REGENERATE)

The following phrases in any persona voice trigger `verdict=REVISE` from the Judge (Qwen @ temp=0.1), which forces `groundedness=0.0` and loops back to `find_angle`:

```
ANECDOTE OPENERS (automatic fail):
  "When I was scaling..."
  "When we deployed..."
  "When we rolled out..."
  "Imagine you are..."
  "Imagine a startup..."
  "Consider a scenario..."
  "Let me paint a picture..."

THROAT-CLEARING (automatic fail):
  "sounds great on paper"
  "sounds great, but"
  "sounds promising, but"
  "the real challenge lies in"
  "the real question is"
  "let me be clear"
  "at the end of the day"
  "let us dive in"

WRONG-CONTEXT REGULATION (automatic fail):
  "Under the EU AI Act" when article is not about regulation
  "Under GDPR" when article is not about data/privacy
  "Under the AI Act" when article is not about regulation

FIRST-WORD FAIL:
  Any persona opening with "I" as the first word
```

---

## Section 5 — The 7 Signature Content Formats

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

---

## Section 6 — What Good Looks Like vs What Fails

### ✅ PASS — what a good post looks like

```
🧠 𝐀𝐈𝐅𝐄𝐄𝐃𝐄𝐑𝐒 | 𝐓𝐇𝐄 𝐃𝐀𝐈𝐋𝐘 𝐀𝐈 𝐃𝐄𝐁𝐀𝐓𝐄

🚨 Anthropic just shipped a 200K-token context window and called it
production-ready. The benchmark is clean. The Monday morning reality
for enterprise teams is more complicated.

💼 𝐅𝐎𝐔𝐍𝐃𝐄𝐑
200K context at $15/MTok means a single enterprise legal review
workflow costs $0.94 per document. At 10K documents per week that's
$9,400 — before error handling, retries, and hallucination audits.
The unit economics only work if it replaces labour costing $50+ per
document today.

🧑‍💻 𝐄𝐍𝐆𝐈𝐍𝐸𝐄𝐑
200K tokens sounds infinite until you hit the retrieval latency wall.
At 150K tokens the P99 latency crosses 8 seconds on Claude 3.5 Sonnet.
That breaks every synchronous API contract in every enterprise stack
I've touched. "Production-ready" needs a latency SLA, not just a
token count.

⚖️ 𝐒𝐊𝐄𝐏𝐓𝐈𝐂
The token window grew 4× in 18 months. The ability to verify what the
model actually attended to inside that window didn't grow at all.
Longer context doesn't reduce hallucination risk — it distributes it
across more surface area you can't inspect.

🎙️ 𝐓𝐇𝐄 𝐀𝐈𝐅𝐄𝐄𝐃𝐄𝐑𝐒 𝐐𝐔𝐄𝐒𝐓𝐈𝐎𝐍
The context window is no longer the constraint. The cost, latency, and
audit surface are.

💬 𝐘𝐎𝐔𝐑 𝐓𝐔𝐑𝐍
For teams already deploying document AI:
1️⃣ The cost curve kills most use cases before latency does
2️⃣ Latency kills synchronous workflows before cost does
3️⃣ Hallucination audit complexity kills both
4️⃣ None of the above — 200K context genuinely unlocks something new

Drop your number + one sentence. 👇

Source → https://venturebeat.com/...
#Anthropic #Claude #EnterpriseAI #AIProductLaunch #AI

🤖 AIFeeders · Daily AI Intelligence · Powered by Jev
*AI-simulated perspectives for discussion — not professional advice.*
```

**Why this passes:**
- Every voice opens with a specific number or named constraint
- Genuine disagreement: FOUNDER (cost), ENGINEER (latency), SKEPTIC (verification) — three different blockers
- CTA is article-specific (200K context → cost/latency/hallucination tradeoffs)
- No banned phrases

---

### ❌ REGENERATE — what a bad post looks like

```
💼 FOUNDER
"When I was scaling my last startup, we were always looking for ways to
reduce costs and improve productivity. The Anthropic update sounds
great on paper, but the real challenge lies in integrating it into
existing workflows. The key is to ensure the ROI is clear..."

🧑‍💻 ENGINEER
"This is an exciting development! As someone who has worked with large
language models extensively, I can say that longer context windows can
be very useful. However, we need to be careful about the implications
for our systems..."
```

**Why this fails (and the judge catches it):**
- FOUNDER opens with `"When I was scaling my last startup"` → banned anecdote opener → automatic REGENERATE
- `"sounds great on paper"` → banned phrase → automatic REGENERATE
- `"the real challenge lies in"` → banned throat-clearing → automatic REGENERATE
- ENGINEER opens with `"This is an exciting development!"` → generic affirmation, no concrete claim
- Both voices basically agree — no genuine clash detected

---

## Section 7 — The Reach Score: What the System Optimises For

Before publishing, `ReachScoreAgent` scores the assembled post on 6 dimensions. If total < 55, auto-repair runs:

```
┌─────────────────────────────────────────────────────────────────┐
│  REACH SCORE DIMENSIONS (0–100 total)                           │
├───────────────────┬──────────┬──────────────────────────────────┤
│ Dimension         │ Max pts  │ What earns full score            │
├───────────────────┼──────────┼──────────────────────────────────┤
│ Hook Strength     │ 20       │ Strong opener signal in 220 chars│
│ Specificity       │ 20       │ Numbers + named entities + source│
│ Question Quality  │ 20       │ Numbered-choice forced CTA       │
│ Length Fit        │ 15       │ 150–300 words (LinkedIn optimum) │
│ Bait Penalty      │ 15       │ Zero engagement-bait patterns    │
│ Topic Coherence   │ 10       │ ≥ 3 AI/tech domain signals       │
├───────────────────┼──────────┼──────────────────────────────────┤
│ TOTAL             │ 100      │ ≥ 55 = PUBLISH  < 55 = auto-repair│
└───────────────────┴──────────┴──────────────────────────────────┘
```

**What auto-repair does (no LLM):**
1. Strips hashtags down to 3 maximum (keeps most topical ones)
2. Removes lines containing engagement-bait patterns (`comment YES if`, `like if you agree`)
3. Trims word count to 300-word ceiling at last sentence boundary

---

## Section 8 — Why This Format Drives LinkedIn Engagement

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ENGAGEMENT MECHANICS                                                    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. HOOK BEFORE THE FOLD                                                 │
│     First 220 chars are visible before "see more"                       │
│     → Strong hook forces reader to expand → increases dwell time        │
│     → LinkedIn algorithm rewards dwell time in feed ranking             │
│                                                                          │
│  2. GENUINE CONFLICT BETWEEN VOICES                                      │
│     Judge enforces that at least 2 personas genuinely disagree          │
│     → Reader takes a side → comment friction drops dramatically         │
│     → "Who do you agree with?" is the unasked question driving replies  │
│                                                                          │
│  3. FORCED-CHOICE CTA (NOT OPEN-ENDED)                                  │
│     "1️⃣ 2️⃣ 3️⃣ 4️⃣" numbered options lower barrier to comment by 10×     │
│     → Reader just types a number + one sentence → zero writing friction │
│     → Produces 10× more comments than "What do you think?"              │
│                                                                          │
│  4. DYNAMIC HASHTAGS (NOT STATIC)                                        │
│     Proper nouns + source + AEO event tags = distributed to right feed  │
│     → Static #AI #Tech get zero algorithmic amplification                │
│     → Named company hashtags reach that company's followers             │
│                                                                          │
│  5. SIMULATED PERSONA DISCLAIMER                                         │
│     "AI-simulated perspectives for discussion — not professional advice" │
│     → OutputGuardrail enforces its presence before every publish        │
│     → Protects against fake-quote allegations and platform policy risk  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Section 9 — Closed-Loop Content Optimization

After each post is published, `ContentOptimizerAgent` runs:

```
LinkedIn Engagement Signals
(impressions · reactions · comments · reposts)
               │
               ▼
    ┌────────────────────────────┐
    │   ContentOptimizerAgent    │
    │                            │
    │  Diagnoses weakest         │
    │  structural component:     │
    │  hook / CTA / clash /      │
    │  format / specificity      │
    └───────────┬────────────────┘
               │
               ▼
    ┌────────────────────────────┐
    │     Story Mutations        │
    │  Stored as calibration     │
    │  for future Jev editorial  │
    │  angle recommendations     │
    └────────────────────────────┘
```

**What gets stored:** Not raw performance numbers — structural mutations. Example mutation output:
```
"Hook rotation slot 3 consistently underperforms on regulation stories.
 Prefer slot 0 (direct policy consequence opener) for governance events."

"Funding stories with 3-voice composition see higher CTA reply rates
 than 4-voice compositions for the same article type."
```

These mutations are passed upstream to calibrate future `find_angle` Jev calls — the system learns which editorial angles drive real practitioner engagement.

---

## Section 10 — Content Standards Checklist (Pre-Publish Mental Model)

Before a post leaves the pipeline, verify mentally:

| Check | Standard |
|---|---|
| Every persona opens with a concrete claim | ✅ No anecdote, no setup, no compliment |
| Genuine disagreement exists | ✅ At least 2 voices in real conflict |
| All claims traceable to source article | ✅ Nothing invented or extrapolated beyond facts |
| CTA is article-specific | ✅ Not a reusable generic question |
| Hashtags are named entities or AEO | ✅ No generic #AI #Tech filler only |
| No banned phrases detected | ✅ Judge ran and returned verdict=PASS |
| Post length 150–300 words | ✅ LinkedIn sweet spot |
| Reach score ≥ 55 | ✅ Or auto-repair was applied |
| OutputGuardrail passed | ✅ No fake quotes, disclaimer present |

---

*AIFeeders Content Strategy & Editorial Standards — the system enforces this automatically.*
