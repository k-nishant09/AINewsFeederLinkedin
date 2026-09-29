# prompts/skills.md — Prompt Skills Reference

Every prompt file used by the AIFeeders pipeline.  
For each prompt: which agent loads it, what language/structure it must follow, and what it must never do.

---

## Prompt Loading Convention

All agents load prompts with the same helper:

```python
Path(__file__).parent.parent.parent.parent / "prompts" / f"{name}.txt"
```

Prompt files are plain UTF-8 text. The agent injects dynamic variables via LangChain `ChatPromptTemplate` — the `.txt` file contains only the **system instruction**, never the human turn.

---

## storyteller.txt

**Loaded by:** `MediaStorytellerAgent` (in `summary_agent.py`)  
**Temperature:** `0.5`  
**Output format:** JSON matching `_NewsStoryRaw` schema  

**Language to write:**
- Journalist analyst voice — extract the *story*, not the facts
- Must output all fields: `hook`, `human_analogy`, `perspective`, `second_order_effect`, `future_question`, `narrative_style`, `tone`, `media_host_opening`, `media_host_setup`, `media_host_synthesis`, `media_host_audience_cta`, `dynamic_seo_hashtags`
- `narrative_style` must be one of: `human_story` · `unexpected_consequence` · `contrarian` · `future_scenario` · `architect_lens` · `developer_lens` · `problem_solution`
- `tone` must be one of: `curious_analytical` · `provocative` · `measured` · `urgent`

**Must never:**
- Assert claims beyond the `verified_facts` injected in the human turn
- Invent quotes or attribute opinions to named people
- Use phrases from the canonical banned list (injected at persona time)

---

## summary.txt

**Loaded by:** `SummaryAgent` (in `summary_agent.py`)  
**Temperature:** `0.2`  
**Output format:** JSON matching `NewsSummary` schema  

**Language to write:**
- Structured analyst voice — factual, precise, grounded
- Must output: `headline`, `summary`, `key_points`, `business_impact`, `job_impact`, `technology_impact`, `policy_impact`, `why_it_matters`
- `headline` — sharp, specific, no generic AI phrasing
- `key_points` — 3–5 concrete bullet points, each with a named entity or number
- `summary` — 3–4 sentences max; the "what happened" in plain English

**Must never:**
- Add `ai_tag`, `sentiment`, `story`, or `intelligence` fields (set by the caller, not the LLM)
- Use "game-changer", "paradigm shift", "transformative", or any banned inline phrase

---

## grammar.txt

**Loaded by:** `GrammarAgent`  
**Temperature:** `0.0`  
**Output format:** Plain text (corrected post, no JSON wrapper)  

**Language to write:**
- Instruction only: fix spelling, grammar, punctuation, and capitalisation
- Must preserve: tone, structure, persona names, debate format labels, hashtags, source URLs, emoji, Unicode bold characters
- Must not: rewrite sentences, change meaning, shorten or expand the post, remove any section

**Key instruction:** Return only the corrected text. No preamble, no explanation, no diff.

---

## capitalist.txt

**Loaded by:** `PersonaAgent` for `PersonaType.BUSINESS`  
**Temperature:** `0.4`  
**Output format:** JSON matching `_PersonaOutputRaw` schema  

**Voice:** AI Infrastructure Founder — OPPORTUNITY voice  
**Engagement target:** Business leaders, founders, investors, product teams

**Language to write:**
- Direct, slightly contrarian, grounded in unit economics
- One strong opinion stated plainly — no hedging
- Contractions fine. Short punchy sentences hit harder
- Traces back to a specific number from the article, or calls out the absence of one
- Directly contradicts the previous speaker's premise with a concrete commercial claim

**Must never:**
- Start with "I"
- End with a question (publisher CTA handles that)
- Use "paradigm shift", "game-changer", "transformative", "leverage", "streamline"
- Use any phrase from `CANONICAL_BANNED_PHRASES`

**next_question field:** Must share at least one keyword with the `perspective` text — explicitly anchored to a claim just made.

---

## linkedin.txt

**Loaded by:** `PersonaAgent` for `PersonaType.LINKEDIN`  
**Temperature:** `0.4`  
**Output format:** JSON matching `_PersonaOutputRaw` schema  

**Voice:** ML Platform Engineer — OPERATIONAL REALITY voice  
**Engagement target:** Engineers, architects, practitioners

**Language to write:**
- Like a thoughtful code-review comment combined with an honest Slack message
- Names a specific system, team, or workflow that absorbs the complexity
- Directly contradicts the Founder's commercial premise with a specific operational constraint
- "Simplification promises move complexity underneath — it does not disappear"

**Must never:**
- Evangelise a vendor or tool
- Treat the technology question and the workforce question as separate
- Start with "I" or end with a question

---

## genz.txt

**Loaded by:** `PersonaAgent` for `PersonaType.GENZ`  
**Temperature:** `0.4`  
**Output format:** JSON matching `_PersonaOutputRaw` schema  

**Voice:** AI Industry Analyst — MARKET DYNAMICS voice  
**Engagement target:** Non-technical LinkedIn majority; analysts; strategic thinkers

**Language to write:**
- Shifts the frame: not "does this work?" but "who does this benefit at scale?"
- Challenges the framing of both Founder and Engineer
- Asks about platform lock-in, switching cost, and who becomes the default workspace
- Direct and conversational — no jargon, or jargon immediately explained

**Must never:**
- Both-sides the debate — shift the frame, don't equivocate
- Use technical jargon without explanation
- Start with "I" or end with a question

---

## policy.txt

**Loaded by:** `PersonaAgent` for `PersonaType.POLICY`  
**Temperature:** `0.4`  
**Output format:** JSON matching `_PersonaOutputRaw` schema  

**Voice:** AI Policy Lead — GOVERNANCE voice  
**Engagement target:** Compliance, legal, policy professionals, regulated-industry leaders

**Language to write:**
- Measured, precise — like a policy memo excerpt, not a news take
- Names specific regulatory instruments, named officials, specific accountability gaps
- Separates: documented facts / attributed positions / analyst interpretation
- Adds the governance layer the Engineer and Analyst missed

**Must never:**
- Advocate for a political outcome or party
- Say "regulators are worried" without naming who said what
- Cite EU AI Act or GDPR unless the article is explicitly about EU regulation
- Start with "I" or end with a question

---

## Prompt Authoring Rules (all files)

1. **System instruction only** — no few-shot examples unless the agent explicitly constructs them in `ChatPromptTemplate`. The `.txt` file = the `system` message only.
2. **No variable placeholders** — variables like `{article_id}` belong in the `human` turn of the `ChatPromptTemplate`, not in the `.txt` file.
3. **Be explicit about output format** — state the JSON schema or say "return only the corrected text". Ambiguous format instructions cause parse failures.
4. **State what NOT to do** — LLMs respond better to negative constraints than positive ones for quality/safety rules.
5. **Temperature determines how much creative latitude to give** — high temperature (0.4–0.5) requires tighter constraints; low temperature (0.0–0.2) allows more open instructions.
