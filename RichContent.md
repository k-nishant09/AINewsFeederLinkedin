# Rich Content Guide — How to Think, Write, and Publish Beautiful Organic Posts

> This guide is for anyone writing or editing AIFeeders content — or anyone who wants to understand *why* the posts are structured the way they are, and how to keep that quality consistent.
> **Updated after post analytics: Sep 25 2026 — 6 reactions, 0 comments. Here is what the data teaches us and what changes next.**

---

## The One Sentence That Matters Most

**A post is not a report. It is a conversation starter.**

Everything in this guide flows from that. A report is complete in itself. A conversation starter is incomplete on purpose — it leaves room for the reader to add something, disagree with something, or share something. If your post makes someone feel like they have nothing left to say, it has failed, even if it is accurate and well-written.

---

## What the Analytics Are Telling Us (Read This First)

### Post: Palo Alto CEO Rejects AI Slowdown — Sep 25, 2026

| Metric | Result | Interpretation |
|---|---|---|
| Impressions | 90 | Narrow initial distribution — algorithm waiting for early engagement signal |
| Members reached | 60 | Low amplification — no reposts/shares yet to push beyond 1st-degree network |
| Reactions | **6** | 6× better than previous post (1 reaction). Format improvement working. |
| Comments | 0 | Comment barrier not yet broken — the question didn't pull people in |
| Saves | 0 | Content not perceived as reference-worthy yet |
| Reposts | 0 | No social proof chain started |
| Seniority | Senior 23%, Director 20%, VP 13% | **High-value audience** — these people comment when the topic touches their real work |
| Company size | 42% enterprise (10,001+) | Enterprise practitioners — they care about production reality, not demos |
| Industry | IT Services 38%, Software Dev 25% | Builders and practitioners — the exact audience for technical depth |

### What the data says

**The good:** 6 reactions from 60 reached = 10% reaction rate. That is strong — LinkedIn's algorithm considers 2-3% good. The format change is working. People are responding.

**The problem:** Zero comments. Zero saves. Zero reposts. Reactions are the lowest-commitment signal on LinkedIn. They do not trigger algorithmic amplification the way comments and saves do. We reached Senior/Director/VP practitioners in enterprise tech — and they didn't find anything worth adding to.

**Root cause:** The CTA asked a political/policy question (*"What regulation would make AI safer?"*) to an audience of IT Services and Software Development practitioners. That is an audience-question mismatch. These people want to talk about **production AI challenges** — model reliability, cost, security, organisational adoption. The question didn't invite their experience.

### The algorithm reality (from LinkedIn's own behaviour)

```
Reaction alone    →  minimal boost (~1.2x)
Comment           →  medium boost  (~3-5x reach)
Save              →  strong signal (~5x reach, marks you as reference content)
Repost            →  strong amplification (reaches 2nd-degree network)
Comment from a 2nd-degree connection  →  viral trigger
```

**The lever we need to pull:** Get one Senior/Director/VP to comment from their real experience. That one comment reaches their network — which is the same audience — and starts the chain.

---

## Part 1 — How to Think Before You Write

### 1.1 Start with the "So What" — Not the Story

Most people start with the news. *"OpenAI released a new model."* That is the story. The "so what" is different:

- **The story:** OpenAI released a new model with a 200k context window.
- **The so what:** Any engineer who spent the last 6 months building a RAG pipeline just lost the clearest justification for it.

The "so what" is what makes someone stop scrolling. The story is what makes them believe you.

**Exercise before writing:** Ask yourself — *if I told this to a senior practitioner over coffee, what is the first thing they would say?* That reaction is usually your best first line.

---

### 1.2 Know Who You Are Writing For (And Who Is NOT the Audience)

Based on actual post demographics, the AIFeeders audience on LinkedIn is:

| Seniority | What They Need to See to Stop |
|---|---|
| **Senior (23%)** | Content that validates a decision they're currently making |
| **Director (20%)** | A real trade-off they haven't seen framed this clearly before |
| **VP (13%)** | An implication for their team or budget they hadn't considered |
| **Entry (10%)** | Something they can use to sound informed in their next meeting |

| Industry | What Stops Them |
|---|---|
| **IT Services & Consulting (38%)** | Client conversation starters — "here's what I'm telling clients about this" |
| **Software Development (25%)** | Production-reality framing — benchmarks vs. actual workloads, real trade-offs |

**The key insight:** This is a **practitioner audience disguised as a general AI news audience.** They don't want to debate AI policy in the abstract. They want to see their own production challenges reflected back at them and confirmed as real.

---

### 1.3 The Three Questions That Determine Whether a Post Is Worth Writing

Ask these before you start:

1. **Is there a real human implication here — or just a technology update?**
   A new model version is not a post. A new model version that changes what enterprises pay for compute infrastructure — that is a post.

2. **Is there a specific detail that most people will not know?**
   General AI hype gets scrolled past. One surprising specific number, one named enforcement action, one concrete job-impact figure — that earns the read.

3. **Can I write one CTA question that a Senior practitioner would answer from personal experience?**
   If the answer is no — if you are writing a political question to an engineering audience — you do not have the right CTA yet.

---

## Part 2 — The Virality Framework

### 2.1 The Dwell Time Principle

LinkedIn's algorithm measures how long someone pauses on your post. 30-40 seconds of dwell time is the threshold that triggers wider distribution. Short paragraphs, one idea per line, and a format that requires reading all the way to the CTA — these all increase dwell time.

**Format rules for dwell time:**

```
One sentence.

Then a blank line.

Another single thought.

The reader's eye keeps moving down.
```

Versus the format that kills dwell time:

```
This is a long paragraph that contains multiple ideas at once and reads
like a report executive summary where all the information is front-loaded
and the reader has no reason to continue reading because nothing is held back.
```

### 2.2 The Comment Flywheel

The first comment is the hardest. After the first comment, the algorithm pushes the post to the commenter's network — who sees it, recognises a peer engaged, and is more likely to comment themselves.

**How to trigger the first comment:**

1. **Ask a question they can answer from memory** — not from research. "What's your biggest production challenge with AI?" is answerable in 20 seconds from experience. "What do you think about AI governance?" requires forming a policy opinion.

2. **Numbered choices lower the barrier to zero** — the reader just types "3" and they've commented. That's enough to trigger the algorithm.

3. **Name the tension they live with** — *"Everyone talks about AI agents. Very few talk about the production reality."* This sentence makes practitioners feel seen. People comment to say "exactly this."

4. **Use "you" not "we"** — "What's YOUR biggest challenge" is more personal than "What are the biggest challenges." The personal address makes the question feel directed at them specifically.

### 2.3 The Save Signal

Saves are the strongest quality signal on LinkedIn. A save means "this is reference material — I want to find this again." Content that gets saved earns sustained reach for days, not hours.

**What gets saved:**
- Structured how-to content with numbered steps
- Content that maps a complex topic onto a simple framework
- Posts that contain a decision tree or evaluation guide
- Content that someone can screenshot and share in Slack

**What our posts can do to earn saves:**
- The "3 sharp facts" section → make these more surprising, more specific
- The "4 Voices" section → make these feel like a decision framework, not just opinion
- Add one "what to do with this information" line per post

### 2.4 The Repost Trigger

Reposts happen when someone thinks "my network needs to see this." That requires:
- The content reflects the poster's professional identity
- Sharing it makes the poster look informed
- It names something real that their audience also experiences

**The sentence that triggers reposts:** A shareable sentence is self-contained, quotable, and sounds like something a smart person said — not something a content pipeline generated. *"Running this model at Flash pricing is technically interesting, but the workforce story is that three junior analysts just lost the work that kept them employed while they learned."* That is a repost-triggering sentence because it is specific, opinionated, and real.

---

## Part 3 — The Post Anatomy

Every AIFeeders post follows this structure. Each element has a job.

```
① Hook opener       — stop the scroll (dwell time starts here)
② Headline          — the specific story
③ Why it matters    — human "so what" in practitioner voice
④ Context line      — editorial framing, plain English
⑤ 3 sharp facts     — what you need to know (specific, surprising)
⑥ Real-world impact — what changes for people in these roles
⑦ 4 Voices          — four human reactions that reflect their experience
⑧ Source link
⑨ CTA               — one question practitioners can answer from experience
⑩ Footer            — disclaimer + hashtags
```

---

### ① The Hook Opener

**Job:** Make the reader stop. Not curious — stopped. One or two short lines with a blank line between them for dwell time.

**The rule:** Every event type has a pool of openers. Never repeat the same opener twice in a row. The opener must contain tension — something unresolved that only reading on will resolve.

**Hook pools by event type:**

**Regulation:**
- *"Most people underestimate how fast AI regulation is moving."*
- *"The compliance deadline your legal team doesn't know about yet."*
- *"AI governance just got real. Here's what the timeline actually looks like."*

**Product launch:**
- *"A new AI capability just dropped — and it changes what's possible."*
- *"The benchmark numbers look clean. The production reality is more complicated."*
- *"Something shipped today that practitioners need to evaluate, not just read about."*

**Funding / acquisition:**
- *"Big capital is moving fast in AI. Here's what the money is chasing."*
- *"Another consolidation move. Here's what it means for everyone building on top of these platforms."*
- *"When the big players acquire, the dependency risk changes for everyone else."*

**Research:**
- *"A research finding that could shift how we build AI systems."*
- *"The benchmark looked impressive until someone tested it on production data."*
- *"Academic paper today. Production reality in 18 months. Here's the gap."*

**General / other:**
- *"Here's an AI story that's worth 2 minutes of your attention."*
- *"Everyone is talking about AI agents. Here's the production question nobody is asking."*
- *"The news cycle moved on. This story hasn't finished mattering yet."*

---

### ② The Headline

Specific, factual, ≤12 words. This is where credibility lives.

**✓ Good:** *Palo Alto CEO Rejects AI Slowdown, Cites Low Extinction Risk*
**✗ Bad:** *AI Executive Has Interesting Take on Safety*

---

### ③ Why It Matters

One sentence. Practitioner voice. Named implication, not abstract significance.

**The banned phrases test** — if any of these appear, rewrite:
- ❌ "this highlights the ongoing debate"
- ❌ "this underscores the importance of"
- ❌ "in the rapidly evolving AI landscape"
- ❌ "it remains to be seen"
- ❌ "this could have significant implications"

**✓ Practitioner voice:**
*"Any team that built compliance plans around the previous EU AI Act timeline now has 90 days less than they thought."*

**✗ News wire voice:**
*"This development highlights the ongoing importance of AI governance in the enterprise context."*

---

### ④ Context Line

One editorial sentence. No scores. No jargon. Just honest framing calibrated to significance + controversy.

---

### ⑤ Three Sharp Facts

Three bullet-point facts from the article. Specific details that reward the reader for getting this far. Not things they could infer from the headline.

Each bullet = `▸` prefix + complete sentence + ≤160 characters.

**The test:** Cover the headline, read each bullet. Does it add something new? If the reader already knows this from the headline — cut and replace.

---

### ⑥ Real-World Impact

Three sentences. One per role. Plain English. No section headers in the post.

```
For businesses: [revenue, cost, or market effect]
For practitioners: [what changes on Tuesday morning]
For builders: [technical or product implication]
```

---

### ⑦ Four Voices

Four distinct perspectives. Each one ≤200 characters, one complete sentence, stakes a clear position.

**The seniority test:** Would a Senior/Director/VP in IT Services or Software Development nod at this sentence — or scroll past it? If they would nod, it is working. If it sounds like an AI wrote it, rewrite it.

**Voice labels (current):**

| Label | Voice | Core claim type |
|---|---|---|
| 💼 Business view | Founder/operator | Unit economics, margin, adoption curve |
| 🏛️ Policy view | Policy analyst | Named regulation, enforcement signal, compliance timeline |
| 🎓 Generalist view | Non-specialist | Plain English, everyday implication, hype check |
| 🧠 Tech & careers | Senior practitioner | Architectural trade-off + workforce reality in one sentence |

---

### ⑧ Source Link

`Full story → [URL]` — always exact.

---

### ⑨ The CTA (Most Critical Element)

**The analytics confirmed:** The right question to this audience is a production-reality question, not a policy debate question.

This audience (IT Services + Software Dev + Senior/Director/VP) responds to questions about **their own experience building and deploying AI in production.**

**The highest-performing CTA format for this audience:**

```
Everyone is talking about [topic].

But here is the question I am increasingly interested in:

Can we actually [do X] reliably in production?

[One-sentence tension statement about the gap between hype and reality]

What is your biggest challenge right now?

1️⃣ [Option — from their real daily work]
2️⃣ [Option — from their real daily work]
3️⃣ [Option — from their real daily work]
4️⃣ [Option — from their real daily work]
5️⃣ [Option — from their real daily work]
6️⃣ [Something else — drop it below]

Drop the number + your experience. 👇
```

**Why 6 choices work better than 4 for this audience:** More choices means more people find their real answer. When someone finds their exact experience listed, the comment becomes effortless — they just type the number.

**CTA by event type — updated for practitioner audience:**

| Event Type | CTA Frame |
|---|---|
| **Any / other** | Production AI challenge: model quality, security, cost, observability, data, scaling |
| **Product launch** | Would you evaluate this? What would you test first? (accuracy, cost, security, integration) |
| **Regulation** | Which compliance challenge hits your team hardest? (documentation, audit, liability, timeline) |
| **Funding / acquisition** | Platform dependency risk — what does consolidation mean for teams building on top? |
| **Research** | Research-to-production gap — how long does it actually take to reach your stack? |
| **Workforce** | What is the human skill that AI genuinely cannot automate in your role, today? |

**The best-performing example CTA (from the IBM AI Platform post that inspired this):**

```
Everyone is building AI agents.

But here is the question I am increasingly interested in:

Can we actually operate them reliably in production?

The architecture quickly becomes:
Agent + Model + Data + Tools + Security + Observability + Governance

For those building AI agents today — what is your biggest production challenge?

1️⃣ Model quality
2️⃣ Security
3️⃣ Data
4️⃣ Observability
5️⃣ Cost
6️⃣ Scaling

Drop the number + your experience below. 👇
```

---

### ⑩ Footer

```
⚠️ Perspectives are AI-simulated — not professional advice.
🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev

#AI #AINews #GenerativeAI ... [dynamic hashtags from article]
```

---

## Part 4 — The Social Agent Workflow

This section describes how AIFeeders uses an AI-assisted workflow to maximise organic reach. **Nothing here automates posting or comments on your behalf.** The agents do research, drafting, and analysis. Every post and every comment goes through human review before publishing.

### The Four Agents

```
┌───────────────────────────────────────────────────────────┐
│                    AI Content Pipeline                     │
└───────────────────────────────────────────────────────────┘

  [Trend Agent]          [Research Agent]       [Audience Agent]
  ─ What's trending      ─ Article facts         ─ Who's reading this
  ─ Event type           ─ Key numbers           ─ Seniority mix
  ─ Controversy level    ─ Named sources         ─ Industry mix
         │                      │                       │
         └──────────────────────┴───────────────────────┘
                                │
                    [Content Strategist Agent]
                    ─ Hook selection from pool
                    ─ Persona sentence drafts
                    ─ CTA selection + framing
                                │
                    [Human Review + Edit]
                                │
                         LinkedIn Post
                                │
                    [Analytics Agent]
                    ─ Impressions / Reach
                    ─ Reactions / Comments
                    ─ Saves / Reposts
                    ─ Demographic breakdown
                                │
                    [Learning loop]
                    ─ Which hooks got dwell time?
                    ─ Which CTAs got comments?
                    ─ Which personas got engagement?
                    ─ Which event types underperformed?
```

### Agent 1 — Trend Scout

Every morning, scores articles across:
- Relevance to AI practitioners (not just AI news consumers)
- Significance of the development (landmark → minor)
- Controversy level (high → low)
- Audience fit (Senior/Director/VP in IT Services + Software Dev)

Output: ranked article list with event type + recommended persona lead.

### Agent 2 — Content Strategist

Takes the article and produces:
- 3 hook candidates (different angles, different controversy levels)
- Draft why_it_matters sentence in practitioner voice
- Draft 4 persona sentences
- Recommended CTA from the event-type CTA library
- Dynamic hashtag selection from article content

Output goes to human review. The human picks the hook, approves the personas, and confirms the CTA.

### Agent 3 — Analytics Agent

After every post, feeds the metrics back into the learning loop:

```
Post metrics input:
  Impressions, Reach, Reactions, Comments, Reposts, Saves
  Demographic breakdown (seniority, industry, company size, location)

Questions asked:
  - Which event type got the most comments?
  - Which CTA format got the most replies?
  - Which persona sentence got the most engagement?
  - What seniority and industry mix engaged most?
  - What is the minimum impressions needed before a comment appears?

Output: content strategy adjustments for next 7 posts
```

### What We Know So Far (2 Posts)

| Pattern | Evidence |
|---|---|
| Reaction rate improving | 1 → 6 reactions (6×) after format change |
| Comment barrier still unbroken | 0 comments both posts — CTA-audience mismatch |
| High-seniority audience confirmed | Senior + Director + VP = 56% of viewers |
| Enterprise tech confirmed | IT Services 38% + Software Dev 25% = 63% |
| Geographic concentration | Seattle + Bay Area + Bengaluru = tech practitioner hubs |
| Impressions low | 90-220 — algorithm waiting for early comment to amplify |

**Next hypothesis to test:** A CTA asking about production AI challenges (model quality, security, cost, observability, scaling) should outperform a policy debate question for this specific audience.

---

## Part 5 — The Voice Principles

### 5.1 Organic vs. Generated Voice

**Organic voice sounds like a practitioner who has shipped things:**
- Contractions used naturally
- Short sentences. Then a longer one. Then short again.
- Makes one clear claim rather than listing all possibilities
- Occasionally contrarian — "this is less impressive than it sounds"
- Acknowledges uncertainty without hedging everything

**Generated voice sounds like a press release:**
- No contractions or mechanical overuse of them
- All sentences the same length
- Hedges everything ("could potentially," "may have implications")
- Never stakes a position — tries to be true from all angles simultaneously
- Uses "landscape" and "rapidly evolving" in every third sentence

**The read-aloud test:** Say the sentence out loud as if you were saying it in a stand-up meeting. Does it sound like something a real person said? Or does it sound like something a corporate comms team approved?

---

### 5.2 Evidence Rules

Every factual claim needs one of three anchors:

1. **Article attribution:** *"The article states X"* / *"According to the release, Y"*
2. **Specific number:** *"The 2.1% failure rate"* / *"$4.2B in funding"*
3. **Named source:** *"Commissioner X stated..."* / *"The EU AI Office requires..."*

No anchor = opinion. Label it or cut it. Never invent metrics.

---

### 5.3 The One-Thing Rule

Every section communicates exactly one thing. When a section tries to say two things, it says neither.

- Hook → *here's why you should stop*
- Headline → *here's the specific story*
- Why it matters → *here's the real practitioner implication*
- Each persona → *here's how one specific type of person should think about this*
- CTA → *here's the specific question worth debating from your own experience*

---

### 5.4 What Organic Content Is Not

| It is not... | Because... |
|---|---|
| A summary of the article | Gives readers no reason to reply |
| A list of possibilities | "Could" and "might" feel uncommitted |
| A celebration of the technology | Enthusiasm without skepticism reads as marketing |
| A policy debate question to a practitioner audience | Audience-question mismatch = 0 comments |
| Generic advice | "Companies should adopt AI" applies to every AI article ever |
| An AI-written post that sounds like an AI wrote it | LinkedIn's algorithm penalises repetitive, generic AI text; practitioners scroll past it |

---

## Part 6 — Pre-Publish Quality Check

Run through this before every post. If any answer is "no" — fix it before publishing.

- [ ] **Hook:** From the pool, not the same opener as last post. Contains an unresolved tension.
- [ ] **Why it matters:** Specific to this story. Replace the company name with a different company — if the sentence still applies, rewrite it.
- [ ] **Facts:** Each bullet adds a detail the reader couldn't infer from the headline.
- [ ] **Persona sentences:** Would a Senior/Director/VP practitioner in IT Services nod at each one? Are all four voices clearly different from each other?
- [ ] **CTA audience match:** Is this a question that IT Services / Software Dev Senior–VP practitioners can answer from their own experience — not from abstract opinion?
- [ ] **CTA format:** Numbered choices (6 preferred, 4 minimum). Specific options from their real work.
- [ ] **No banned phrases:** "highlights the ongoing debate," "underscores the importance of," "rapidly evolving landscape," "it remains to be seen."
- [ ] **Evidence anchors:** Every factual claim is traced to article, number, or named source. No invented metrics.
- [ ] **Dwell time format:** Short paragraphs. Blank lines between ideas. Reading time ~40 seconds.

---

## Part 7 — Quick Reference

### The full post structure in 30 seconds

```
[Hook — 1-2 short lines from the event-type pool, blank line for dwell time]

[Headline — specific, ≤12 words]

[Why it matters — 1 sentence, practitioner voice, named implication]

[Context line — editorial framing, plain English, no scores]

Here's what you need to know:

▸ [Surprising specific fact 1]
▸ [Surprising specific fact 2]
▸ [Surprising specific fact 3]

For businesses: [one sentence]
For practitioners: [one sentence]
For builders: [one sentence]

────────────────────
Different perspectives on this:

💼 Business view
[One sentence, <200 chars, unit economics or adoption reality]

🏛️ Policy view
[One sentence, <200 chars, named regulation or enforcement signal]

🎓 Generalist view
[One sentence, <200 chars, plain English, no jargon, hype check]

🧠 Tech & careers
[One sentence, <200 chars, production trade-off + workforce implication]

Full story → [URL]

[CTA — 6-choice numbered question from practitioner's real production challenges]

⚠️ Perspectives are AI-simulated — not professional advice.
🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev

#AI #AINews #GenerativeAI ... [dynamic hashtags]
```

### The four things that kill organic content

1. **Static hook openers repeated post after post** — readers recognise the pattern and scroll; rotate from a pool
2. **CTA that asks a policy question to a practitioner audience** — zero comment trigger; always match the question to the audience's daily work
3. **Persona sentences that sound identical** — if two voices say the same thing differently, the ensemble fails; each sentence must stake a different, specific claim
4. **Paragraph blocks with no breathing room** — kills dwell time; one idea per line, blank line between thoughts

---

*When in doubt, ask: would a Senior Director at Microsoft in Bengaluru forward this to their team Slack? If not — find the one sentence about production AI challenges that would make them do it, and build the post around that sentence.*
