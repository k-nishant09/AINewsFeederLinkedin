# RichContent.md — AIFeeders Content Strategy & Quality Guide

> **Build #83 · Dialogue-Delivery Format · Last updated: 2025**

---

## The One Sentence That Matters Most

**This is not a LinkedIn post generator. This is a mini news programme in text.**

The reader should feel like they are watching a conversation between a journalist and four people with genuinely different real-world perspectives. If it reads like a bullet list with emoji headers, the format has failed.

---

## Analytics — What the Data Says

These are real numbers from production posts. They tell you who reads, what they do, and what the algorithm rewards.

| Metric | Range | What it means |
|---|---|---|
| Impressions | 90–220 | Algorithm is waiting for early engagement signal |
| Members reached | 60–180 | Organic only — no paid amplification |
| Reactions | 6–10 | ~10% reaction rate (LinkedIn considers 2–3% good) |
| Comments | 0–2 | Even one comment triggers algorithmic amplification |
| Saves | tracked | Strong quality signal — marks you as reference content |

**Audience profile (real seniority breakdown):**
- Senior: 23%
- Director: 20%
- VP: 13%

This is a high-value practitioner audience, not a general consumer feed.

**Industry breakdown:**
- IT Services: 38%
- Software Development: 25%

**Location concentration:** Seattle, Bay Area, Bengaluru — AI practitioners, not casual observers.

**The insight these numbers reveal:** Reactions are already strong. The gap is comments. A single comment from a Director or VP can 3–5x reach. Everything about the format — the future question, the numbered choices, the audience framing — exists to trigger that first comment.

---

## The LinkedIn Algorithm Reality

LinkedIn's algorithm does not treat all engagement equally. Here is the multiplier reality:

| Signal | Reach boost | Why it matters |
|---|---|---|
| Reaction | ~1.2× | Minimal. Nice but not the target. |
| Comment | ~3–5× | Medium. Triggers algorithmic recirculation. |
| Save | ~5× | Strong. Marks the post as reference content. LinkedIn treats saves as a quality vote. |
| Repost | Strong amplification | Reaches the reposter's 2nd-degree network directly. |
| Comment from 2nd-degree connection | Viral trigger | The signal that pushes a post into wider feed distribution. |

**What this means for format decisions:**

- The future question must be genuinely open — not rhetorical — because a practitioner has to feel the pull to answer it.
- The numbered choices (1–6) must map to real experiences the audience has actually lived. Generic options ("all of the above") produce no comments.
- The Media Person close must leave something unresolved — a post that fully wraps up has nothing left to argue with.

---

## Part 1 — How to Think Before You Write

### The so-what question vs the story question

Most AI news fails the so-what test. A product launch is not a story. A funding round is not a story. A benchmark result is not a story.

A story is what happens to real people when the technology lands.

Before writing anything, ask three questions:

1. **Who changes their behaviour because of this?** If the answer is "nobody yet," the story is not ready.
2. **What did people believe before this, and what do they have to believe now?** The gap between those two beliefs is the story.
3. **Which of the four characters is most surprised by this?** The most surprised character is usually the one who gives the post its edge.

### Who is reading this post

The AIFeeders audience is not the general public. They are:

- Engineers who have deployed or evaluated AI systems
- Founders who have made or are about to make AI infrastructure decisions
- Policy people who follow regulatory developments but are not lawyers
- Generalists in tech-adjacent roles who want to understand what AI means for their job

They read LinkedIn at 7am or during lunch. They have 45–60 seconds. They will stop reading the moment they feel lectured at.

Write for the Director who already knows what an LLM is. Do not explain what AI is. Explain what *this particular development* means for someone who already works with it.

### Three qualifying questions before you start

Before a story earns a post:

1. **Is there a genuine tension?** Not just a development — an actual disagreement, trade-off, or decision someone now has to make?
2. **Do the four characters have genuinely different answers?** If Founder and Engineer would say the same thing, the story is too thin.
3. **Is the future question one that a VP would still be wrestling with in six months?** If it has a clean answer today, it is not a future question — it is a FAQ.

---

## Part 2 — The Virality Framework

### Dwell time

LinkedIn's algorithm measures how long someone pauses on a post. Short paragraphs and blank lines between ideas increase dwell time. A wall of text gets scrolled past in one second. Five short paragraphs with breathing room get read.

Target reading time: **45–60 seconds**. Less than 45 seconds and there is not enough to engage with. More than 60 seconds and most people do not finish.

### The comment flywheel

The first comment is the most valuable thing that can happen to a post. It does not matter if the comment agrees or disagrees — it signals to the algorithm that this post is generating conversation.

The future question and numbered choices exist specifically to pull that first comment. The choices must be recognisable from real experience:

Bad: "1. AI will transform everything 2. AI is overhyped 3. It depends"

Good: "1. I'm actively deploying this and the compliance question is real 2. I work in a regulated sector — my legal team stopped this before it started 3. I'm watching from the outside and still don't know what to do about it"

The second version makes a Director say "that's number 2 for me" and type a comment before they think about it.

### The save signal

A post gets saved when it feels like reference material — something the reader will want to find again. The post anatomy is designed for this: the pipeline explanation, the evidence anchors, the clear character frameworks. A practitioner who saves the post is telling LinkedIn it is worth distributing to similar professionals.

### The repost trigger

A repost happens when someone feels the post represents something they want to say to their own network. This is usually triggered by the Media Person close — the non-obvious perspective that a reader feels is under-discussed. If the close is just a summary of what was already said, it will not be reposted. If it reframes the entire story in a way the reader had not considered, it earns a repost.

---

## Part 3 — The Post Anatomy

Every element has a job. If an element is not doing its job, it is wasting the reader's attention.

---

### 🎙️ The Hook (Media Person opens)

**Job:** Create curiosity in the first 10 words. The reader must want to know what comes next.

**Rule:** Never open with a company name, a product name, or "AI is changing..." The reader has seen those openings a hundred times.

**What works:** An observation that is slightly off-centre. A fact that does not fit the expected narrative. A question the reader thought they knew the answer to.

**Example:**
> "AI is getting access to something much more valuable than data."

The reader's first question is: *what is more valuable than data?* They keep reading to find out.

**Source in pipeline:** Written by MediaStorytellerAgent in Pass 1. The `hook` field in the NewsStory object. Temperature 0.5 — creative, not deterministic.

---

### The Situation (what actually happened)

**Job:** Tell the reader what happened in plain English, without press release language, in 2–3 short paragraphs.

**Rule:** No jargon. No "leveraging synergies." No "paradigm shifts." A reader who has never worked in tech should be able to follow the situation paragraph.

**What works:** Subject, verb, consequence. What happened. Why it matters. What changed.

**Example:**
> "Imagine an AI system sitting inside a national health portal. It isn't just answering questions. It is reading millions of patient records, looking for patterns humans might miss."

Three sentences. The reader knows exactly what happened. No technical knowledge required.

**Source in pipeline:** `what_actually_happened`, `what_changed`, `why_now` fields from MediaStorytellerAgent. Shaped by SummaryAgent in Pass 2 for factual accuracy.

---

### The Human Analogy

**Job:** Make the technology concrete. Someone with zero technical background should say "oh I get it now."

**Rule:** The analogy must be from everyday life — not from another tech context. "It is like a better neural network" is not an analogy. "It is like hiring a new employee who can read every email in the company's history" is.

**Example:**
> "Think of it like hiring a new employee who can read every email in the company's history — except this employee never sleeps and never forgets."

**Test:** Read this sentence to someone who has never worked in tech. Do they nod? If yes, it works.

**Source in pipeline:** `human_analogy` field from MediaStorytellerAgent. One of the most important outputs of Pass 1.

---

### The Transition ("So I asked four people...")

**Job:** Signal the shift from journalist to voices. Make it feel like a real editorial choice, not a format wrapper.

**Fixed text:**
> "So I asked four people what [subject] means for them."

This line is structural. It sets up the dialogue format and tells the reader they are about to hear four genuinely different perspectives.

---

### The Media Bridges

**Job:** Introduce each character in a way that reframes the question, not just labels the next voice.

**What fails:**
> "🏛️ Policy Analyst:"

That is a label. Labels do not build dialogue.

**What works:**
> "Now let's look at the governance question."
> "And then there's the engineering question."
> "But there's someone we haven't heard from yet — the person who isn't building AI or regulating it. Just using technology and wondering what any of this means."

Each bridge tells the reader *which lens they are about to look through*. The Generalist bridge is deliberately longer because it earns the character's inclusion — this is the voice that least obviously belongs in an AI conversation, so the bridge has to justify it.

**Source in pipeline:** Written by PublisherAgent (deterministic composition). Fixed text with minimal variation.

---

### 💼 Founder (business perspective)

**Job:** Business scenario — risk, cost, timing, capital. The Founder is not a cheerleader for AI. They are someone who has made or is about to make a real financial decision, and this development changes the terms of that decision.

**Length:** 3–5 sentences.

**What fails:** "This AI development creates exciting opportunities for founders to innovate..." — that is a press release, not a voice.

**What works:** A specific scenario the reader can picture. A risk that has a dollar value attached to it. A decision point that did not exist before this news.

**Example:**
> "Imagine you're running a healthcare startup and your AI just got flagged by a regulator before a single customer saw it. That is not a hypothetical any more — it is the deployment reality in sectors where the EU AI Act's high-risk tier now applies. The question I keep asking is not whether the regulation is right. It is whether the compliance cost becomes the new barrier to entry that keeps smaller founders out and protects incumbents who can afford the legal teams. That is the business risk that does not appear in the press release."

**Source in pipeline:** PersonaAgentFactory, parallel, receives full story context + NewsStory + SummaryAgent output. Does NOT receive raw analysis scores.

---

### 🏛️ Policy Analyst (governance perspective)

**Job:** Named framework, jurisdiction, or enforcement mechanism. The Policy Analyst is not an abstract voice about "regulation." They operate in a specific legal or institutional context and they know the accountability gap by name.

**Length:** 3–5 sentences.

**What fails:** "Regulators will need to consider the implications of this technology for existing frameworks..."

**What works:** A specific regulation. A specific accountability mechanism. A specific gap between what the law requires and what the technology actually does.

**Example:**
> "Think about a hospital. A doctor makes a decision — there is a name attached to it, there is a process, there is accountability. Now put an AI system in that same hospital, analysing patient records and flagging risk. The EU AI Act's high-risk classification requires documented incident response plans and human oversight mechanisms before that system goes live — something most enterprise deployments currently lack. The technology may move at the speed of software, but accountability still has to operate at the speed of institutions, and the gap between those two speeds is where the real governance problem lives."

**Source in pipeline:** PersonaAgentFactory. Character is given their role and the full story context. They speak from that situation — not from a list of pre-assigned conclusions.

---

### 🧠 Engineer (technical perspective)

**Job:** Technical trade-off AND workforce implication. Both lenses are required. An Engineer passage that only covers the technical side misses half its job.

**Length:** 3–5 sentences.

**What fails:** "Engineers will need to implement robust security measures to protect AI systems..."

**What works:** The specific engineering problem that this development creates. And then: what this means for which engineers become more valuable.

**Example:**
> "Giving an AI system access to a database is not the hard part — we already know how to connect systems. The hard part is controlling what happens after the AI gets access: can it read everything, modify records, call another service, make a decision without approval? That shift is moving engineering work from 'how do I build an intelligent model' to 'how do I operate an intelligent system safely' — and those are genuinely different skill sets. The engineers who understand access control, audit logging, and decision tracing are the ones whose value is going up right now."

**Source in pipeline:** PersonaAgentFactory. Prompted with both technical and workforce lenses explicitly.

---

### 🎓 Generalist (human scale perspective)

**Job:** Zero jargon. Human scale — jobs, daily life, access. This is the voice for every reader who is affected by AI without being in a position to build or regulate it. They ask the question the others never quite get to.

**Length:** 3–5 sentences.

**What fails:** "This AI development will impact many people across various sectors of society..."

**What works:** The question a non-technical person would genuinely ask. The human experience of what happens when AI enters a system they depend on.

**Example:**
> "Honestly, every time I read about AI going into healthcare or finance, my first question is not 'is it accurate' — it is 'who is responsible when it is wrong?' Because right now the answer seems to be nobody in particular, and that is genuinely worrying. People experience AI not as a model in a lab — they experience it as a denied transaction, a medical recommendation, a security check. That is when AI stops being a technology story and starts being a story about trust."

**Source in pipeline:** PersonaAgentFactory. Explicitly prompted to avoid technical vocabulary and speak at human scale.

---

### The Media Person Close

**Job:** The non-obvious perspective. The second-order effect. Something the reader had not considered before reading the post.

**Rule:** The close must NOT summarise what was already said. It must add a new frame. A reader who skips straight to the close should discover something that makes them want to read the whole post.

**Example:**
> "And suddenly the story becomes much bigger than AI. The technology may be about models, data, and compute. But the real story is about trust — and once AI enters critical infrastructure, we are no longer asking 'how intelligent is the AI?' We are asking 'how much responsibility are we willing to give it?'"

**Source in pipeline:** `second_order_effect` and `perspective` fields from MediaStorytellerAgent Pass 1. This is why Pass 1 exists — to extract the non-obvious frame before the characters speak.

---

### Source link

**Job:** Attribution and credibility. Every post links to the original article.

**Format:** Plain URL on its own line after the close.

---

### Future question

**Job:** Open the conversation for comments. Must be genuinely unresolved — not rhetorical, not yes/no, not something with a clean answer.

**What fails:** "AI is changing everything, isn't it?"
That is a statement wearing a question mark.

**What fails:** "Will AI replace all jobs?"
That is a yes/no question with a tribal answer. It generates heat, not insight.

**What works:** A specific tension that a practitioner is genuinely wrestling with, phrased as an invitation.

**Example:**
> "If accountability still has to operate at the speed of institutions — but AI systems deploy at the speed of software — who decides when the gap becomes too dangerous?"

**Source in pipeline:** `future_question` field from MediaStorytellerAgent Pass 1.

---

### Numbered choices

**Job:** Lower the activation energy for a comment. Give the reader a ready-made response that maps to their real experience. They read the list, feel recognised, and type "number 3 for me" without thinking about it.

**Rule:** The options must be mutually exclusive, recognisable from real experience, and span the range of likely audience positions. Four to six choices.

**What fails:** "1. Very concerned 2. Somewhat concerned 3. Not concerned 4. Not sure"

**What works:**
> "1. I work in a regulated sector — compliance cost is already the deciding factor
> 2. I'm at a startup — I'd rather move fast and deal with regulation reactively
> 3. I'm on the engineering side — access control is already the conversation we're having
> 4. I work in policy — the accountability gap is exactly what I'm trying to close
> 5. I'm watching from the outside — I just want to know my data is safe
> 6. I think we're all worrying about the wrong thing entirely — tell me why below"

Note: option 6 always invites dissent. Dissent generates comments.

---

### Drop your take below 👇

**Job:** Direct call to action. Simple. Not optional.

---

### ⚠️ Disclaimer

Fixed text. Non-negotiable. Every post ends with:

> ⚠️ Perspectives are AI-simulated — not professional advice.

---

### Branding and hashtags

Fixed elements:

> 🤖 AIFeeders · Daily AI Intelligence · Powered by Jev

Core hashtags (every post):
`#AI #AINews #GenerativeAI #MachineLearning #AIStrategy #AIInnovation #DigitalTransformation`

Dynamic hashtags: generated by PublisherAgent based on article company, sector, and topics.

---

## Part 4 — The AI Pipeline Behind the Post

The post is produced by a 7-stage pipeline. Each stage exists for a specific reason. Removing any stage degrades post quality in a predictable way.

---

### Stage 1 — DISCOVER

**What it does:** Queries GNews across 9 search queries with a 24-hour window and 2-key rotation.

**Why it exists:** AI news moves in 24-hour cycles. A story that is 48 hours old has already been covered by every major tech newsletter. The discovery window is tight by design.

**Output:** Raw articles with metadata.

---

### Stage 2 — UNDERSTAND

**What it does:** Builds a PageIndex — a vectorless document tree from article content. No vector database. No embeddings.

**Why it exists:** The pipeline does not need semantic search across a corpus. It needs structured access to one article's content — paragraphs, claims, evidence, quotes. The document tree gives the downstream agents structured context without the latency and cost of embedding pipelines.

**Output:** Structured document tree per article.

---

### Stage 3 — ANALYZE

**What it does:** Jev System One runs 11 scoring questions against each article and selects the best one using a weighted formula:

> Score = relevance × 0.6 + engagement × 0.4

**Why it exists:** Not every article that appears in discovery is a post. Many are press releases, vendor announcements, or incremental updates with no genuine story tension. The scoring function filters for articles that have both relevance (is this AI-significant?) and engagement potential (does this have story tension a practitioner would care about?).

**Output:** One selected article with scores and signals.

---

### Stage 4 — EXPLAIN (two passes)

This is the most important stage. It is the reason the new format works and the old format did not.

#### Pass 1 — MediaStorytellerAgent (temperature=0.5)

**What it does:** A journalist reads the article and extracts the NewsStory — a rich narrative object with these fields:

- `hook` — curiosity in the first 10 words
- `human_analogy` — makes the technology concrete without jargon
- `perspective` — the non-obvious angle
- `second_order_effect` — what happens after the obvious thing happens
- `future_question` — the genuinely open question
- `what_actually_happened` — plain-English situation
- `what_changed` — the delta from before
- `why_now` — why this matters today
- `narrative_style` — which of the 15 styles fits this article

**Temperature 0.5:** Enough creativity to find a genuine angle, enough control to stay grounded in the article.

**Why it exists:** Without Pass 1, Pass 2 has nothing to build on. The characters would receive a compressed article summary and speak in compressed-article language. Pass 1 extracts the *story frame* — the interpretive layer that gives the characters something to react to.

#### Pass 2 — SummaryAgent (temperature=0.2)

**What it does:** Takes the NewsStory from Pass 1 and produces structured facts calibrated by the narrative frame. Lower temperature means tighter factual grounding — names, numbers, dates, institutional references.

**Temperature 0.2:** Deterministic enough to be trustworthy, not so rigid that it misses context.

**Why it exists:** Characters need both narrative richness (from Pass 1) and factual accuracy (from Pass 2). Either alone produces a flawed post. Narrative without facts is impressionistic. Facts without narrative context produces four voices that all sound like Wikipedia.

**The two-pass rationale in one sentence:** Pass 1 is the journalist reading the article. Pass 2 is the fact-checker confirming what the journalist found.

---

### Stage 5 — ANGLE

**What it does:** Jev `find_angle` evaluates the common narrative for this story type and identifies the missing angle — the perspective most AI coverage is not taking.

**Output:** Recommended angle label and recommended audience framing.

**Why it exists:** Most AI stories get covered the same way — the technology announcement, the capability claim, the expert quote. The angle stage explicitly asks: what is everyone *not* saying? This feeds into the Media Person close and the future question.

---

### Stage 6 — CREATE

Three agents run at this stage:

#### PersonaAgentFactory (4 parallel agents)

Each agent receives:
- Their role and real-world concerns
- The full article
- The NewsStory from Pass 1 (narrative frame)
- The structured facts from Pass 2
- The recommended angle from Stage 5

They do **not** receive: raw analysis scores (impact=0.91, risk=0.72). These numbers belong to the scoring function, not the characters.

**Why this matters:** Characters given only analysis scores produce output like: "The high impact score of this development suggests significant transformative potential." That is not a voice. Characters given full story context produce output like: "The question I keep asking is not whether the regulation is right. It is whether the compliance cost becomes the new barrier to entry." That is a voice.

#### GrammarAgent (NEW — build #83, temperature=0)

**What it does:** Proofreads the fully composed post before LinkedIn publish.

**Fixes:**
- Spelling errors
- Grammar errors
- Punctuation errors
- Awkward phrasing
- Capitalisation (e.g. "linkedin" → "LinkedIn", "ai" → "AI")
- Repeated words

**Does NOT change:**
- Structure
- Tone
- Meaning
- Character labels
- Emoji
- Hashtags
- Line breaks
- URLs

**Temperature=0:** Deterministic. No creative variation. This agent has one job and it is a proofreading job.

**Failure mode:** If the LLM call fails, the original post is published unchanged. The GrammarAgent is best-effort. It never blocks publishing.

**Why it exists:** Even a single capitalisation error ("linkedin" in a LinkedIn post) signals low production quality and reduces save/repost rates. The grammar pass is the cheapest quality improvement in the pipeline.

#### PublisherAgent (deterministic composition)

**What it does:** Assembles all elements into the final post in the correct structure. Zero LLM calls. No generative variation.

**Why it exists:** The post structure is fixed. The Media bridges are fixed text. The disclaimer is fixed. The branding is fixed. Deterministic assembly means the structure never drifts and A/B testing a single variable (e.g. the future question) is possible.

---

### Stage 7 — PUBLISH

**What it does:** Submits the final post to the LinkedIn API.

**Gate:** Human approval via `PUBLISHING_ENABLED` flag. No post is published without a human reviewing it.

---

### The 5th Voice

`labor.txt` — Working Professional — is fully written and ready as a 5th character for the LinkedIn Comments API. Until LinkedIn grants "Community Management API" permission, this voice is **not** in the post. All four characters are embedded in the post body. This is the intended design — not a bug.

---

### 15 Narrative Styles

MediaStorytellerAgent selects the style that best fits the article. The style is never imposed — it is discovered by reading the article.

| Style | Best for |
|---|---|
| `imagine_if` | Near-future speculation with clear stakes |
| `human_story` | Individual impact, identity, or access |
| `behind_the_scenes` | Process or decision that is usually invisible |
| `problem_solution` | Clear engineering or business problem + response |
| `unexpected_consequence` | High novelty, second-order effects |
| `nobody_talking_about` | Under-covered angle, contrarian |
| `before_after` | Product launch, capability shift, regulatory change |
| `what_happens_next` | Developing story, unresolved tension |
| `simple_explanation` | Complex concept, broad audience |
| `business_impact` | Financial, competitive, or market consequence |
| `contrarian` | High controversy, dominant narrative challenged |
| `future_scenario` | Speculation grounded in current trajectory |
| `developer_lens` | Technical implementation, tooling, platform |
| `architect_lens` | System design, infrastructure, scalability |
| `executive_lens` | Strategy, risk, governance, board-level framing |

**Selection signals:** High novelty → `unexpected_consequence`, `nobody_talking_about`. High controversy → `contrarian`, `human_story`. Product launch → `before_after`, `developer_lens`. Regulatory development → `problem_solution`, `executive_lens`.

---

## Part 5 — The Voice Principles

### Organic voice vs generated voice

The single biggest quality failure in AI-generated content is that it sounds like AI-generated content. Here is how to tell the difference.

**Generated voice:**
- No contractions, or mechanical overuse ("it's important that we understand that it's...")
- All sentences the same length
- Hedges everything ("it remains to be seen whether this will have significant implications")
- Never stakes a position
- Default vocabulary: "landscape", "rapidly evolving", "highlights the ongoing debate", "underscores the importance of"

**Organic voice:**
- Contractions used naturally
- Short sentence. Then a longer one that gives the reader room to think.
- Makes one clear claim
- Occasionally contrarian — disagrees with the obvious reading
- Acknowledges uncertainty without hedging everything

**The read-aloud test:** Say the sentence in a stand-up meeting. Does it sound like something a real person said? If you would feel embarrassed saying it out loud, cut it.

### Evidence rules

Every factual claim needs one of:
1. Article attribution ("according to the EU AI Act's official text...")
2. A specific number ("at €30M the fine is larger than most Series A rounds...")
3. A named source or institution

Claims without evidence are opinions. Label them as opinions.

### The one-thing rule

Each character makes one main claim. Not three. Not a list of considerations. One claim, supported by 2–4 sentences of context, leading to one implication.

If a character passage contains two different main points, it is two different voices collapsed into one. Split them, or choose the stronger one.

### What this content is NOT

- A press release. Product announcements are not stories.
- A tutorial. How-to content does not produce the future question or the comment.
- A hot take. An unsupported contrarian claim with no evidence is noise, not dialogue.
- A summary. Summaries compress information. This format expands it — from event to human consequence to open question.

### Banned phrases

These phrases signal generated voice and are not permitted in any element of the post:

- "highlights the ongoing debate"
- "underscores the importance of"
- "rapidly evolving landscape"
- "it remains to be seen"
- "significant implications for society"
- "it is worth noting that"
- "in conclusion"
- "as we navigate"
- "it is important to consider"
- "the intersection of X and Y"

---

## Part 6 — Pre-Publish Quality Check (Build #83)

Run this checklist before approving any post for publishing. Every item is a binary pass/fail.

### Hook
- [ ] Curiosity in the first 10 words
- [ ] Does not open with a company name, product name, or "AI is changing..."
- [ ] A reader who stops here still wants to know what comes next

### Situation
- [ ] Plain English — no jargon, no press release language
- [ ] 2–3 short paragraphs
- [ ] Someone with zero tech background can follow it

### Human analogy
- [ ] From everyday life, not from another tech context
- [ ] A non-technical reader says "oh I get it now"
- [ ] Zero technical knowledge required

### Media bridges
- [ ] Each bridge reframes the question, not just labels the next voice
- [ ] The Generalist bridge justifies why this voice belongs here
- [ ] Bridges are not interchangeable (they are not all "And now...")

### Character passages
- [ ] Each passage is 3–5 sentences
- [ ] Each passage is clearly different from the others in tone and concern
- [ ] Founder: business scenario with risk, cost, timing, or capital
- [ ] Policy Analyst: named framework, jurisdiction, or enforcement mechanism
- [ ] Engineer: BOTH technical implication AND workforce implication
- [ ] Generalist: zero jargon, human scale (jobs, daily life, or access)
- [ ] No character passage contains two unrelated main points

### Media close
- [ ] Non-obvious perspective — something not already said by a character
- [ ] Second-order effect — what happens after the obvious thing happens
- [ ] A reader who skips to the close wants to go back and read the whole thing

### Future question
- [ ] Genuinely open — not rhetorical, not yes/no
- [ ] A practitioner with 10 years of experience would still wrestle with it
- [ ] Does not have a clean answer today

### Numbered choices
- [ ] 4–6 options
- [ ] Mutually exclusive
- [ ] Recognisable from real professional experience
- [ ] At least one option invites disagreement or an outlier position

### Banned phrases
- [ ] None of the banned phrases appear anywhere in the post
- [ ] No sentence would fail the read-aloud test

### Evidence
- [ ] Every factual claim traces to an article reference, a specific number, or a named source
- [ ] No unattributed statistics

### Grammar
- [ ] GrammarAgent ran without errors
- [ ] All proper nouns correctly capitalised (LinkedIn, AI, EU AI Act, etc.)
- [ ] No repeated words within a sentence

### Dwell time
- [ ] Short paragraphs with blank lines between ideas
- [ ] Total reading time 45–60 seconds
- [ ] No wall of text

---

## Part 7 — Quick Reference

### 30-second post template

```
🎙️ [Hook — curiosity in first 10 words]

[Situation — 2-3 short paragraphs, plain English]

[Human analogy — makes technology concrete, zero jargon]

So I asked four people what [subject] means for them.

────────────────────

Let's start with the business question.

💼 Founder
[3-5 sentences — risk, cost, timing, capital]

Now let's look at the governance question.

🏛️ Policy Analyst
[3-5 sentences — named framework, jurisdiction, accountability gap]

And then there's the engineering question.

🧠 Engineer
[3-5 sentences — technical trade-off + workforce implication, both lenses]

But there's someone we haven't heard from yet — the person who isn't building AI or 
regulating it. Just using technology and wondering what any of this means.

🎓 Generalist
[3-5 sentences — zero jargon, human scale, jobs/daily life/access]

────────────────────

[Media Person close — non-obvious perspective + second-order effect]

[Source URL]

[Future question — genuinely open, not rhetorical]

[Numbered choices — 4-6 options from real professional experience]

Drop your take below 👇

⚠️ Perspectives are AI-simulated — not professional advice.
🤖 AIFeeders · Daily AI Intelligence · Powered by Jev

#AI #AINews #GenerativeAI #MachineLearning #AIStrategy #AIInnovation #DigitalTransformation
[dynamic company/topic hashtags]
```

---

### Four things that kill this format

**1. Character voices that sound identical**

If Founder and Engineer say the same thing in different words, the post is a list with different emoji headers, not a dialogue. Each character must have a different primary concern, a different vocabulary, and a different implication. If two characters could swap passages without the reader noticing, one of them needs to be rewritten.

**2. Media bridges that are just labels**

`🏛️ Policy Analyst:` is a label. It tells the reader nothing about what lens they are about to look through. `Now let's look at the governance question.` is a bridge. It sets up a frame. Every bridge must earn its position by reframing the question.

**3. Character passages shorter than 3 sentences**

A one-liner is a headline. A two-liner is a tweet. A character needs at least three sentences to establish context, make a claim, and show an implication. Anything shorter reads as an assigned conclusion, not a voice.

**4. Future question that is a rhetorical statement**

`AI is changing everything, isn't it?` is a statement. It requires no response. `If accountability has to operate at the speed of institutions but AI deploys at the speed of software, who decides when the gap becomes too dangerous?` is a question. It has no clean answer, it applies to real professional decisions, and a practitioner will feel the pull to answer it.

---

### Build #83 change summary

| Element | Before build #83 | Build #83 |
|---|---|---|
| Format | One-liner persona sentences as bullet items | Dialogue-delivery — narrative passages |
| Characters | Assigned conclusions from analysis scores | Speak naturally from story context |
| Summarisation | Single-pass compression of article | Two-pass: journalist frame + structured facts |
| Grammar | No grammar check | GrammarAgent at temperature=0 |
| Media bridges | None | Framing sentences before each character |
| Media close | Not present | Non-obvious perspective + second-order effect |
| Human analogy | Not present | Required element, tested for zero jargon |
| Narrative style | Not selected | 15 styles, selected by MediaStorytellerAgent |

---

*RichContent.md is a living document. Update it when the format changes. The post anatomy and quality checklist must reflect the current production standard — not the previous version.*
