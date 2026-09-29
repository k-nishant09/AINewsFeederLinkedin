# skills.md — JudgmentAgent

**File:** [`judgment_agent.py`](judgment_agent.py)
**Stage:** 6 — EXPLAIN (Phase 1, runs in parallel with PageIndex fetch)
**LLM temperature:** `0.2`

---

## Purpose

Epistemological fact-boundary analysis. Separates what is **objectively true** from what is **claimed**, **interpreted**, or **unknown** before any creative or persona agent touches the article. Provides the guardrails that prevent hallucinated certainty downstream.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| Langfuse | `get_langfuse_callback(run_id, tags, metadata)` | Traces the LLM call |

---

## Inputs

| Input | Source |
|---|---|
| `article_id`, `title`, `source`, `content` | `selected_articles` state |
| `pageindex_sections` | Empty string at this stage — judgment uses raw content |

---

## Output

`JudgmentAnalysis` — five categories:

| Category | Description |
|---|---|
| `facts` | Direct events, verified launches, official announcements, concrete numbers |
| `reported_claims` | Company/author statements — their perspective, not established truth |
| `analysis_implications` | Logical technical/economic consequences grounded in evidence |
| `uncertainties` | Unproven claims, pending benchmarks, open regulatory outcomes |
| `what_not_to_conclude` | Explicit list of things that must NOT be asserted as fact |

Consumed by: `MediaStorytellerAgent`, `PersonaAgent` (injected into every persona prompt).

---

## Prompts

Inline system prompt in [`judgment_agent.py`](judgment_agent.py) — `_JUDGMENT_SYSTEM_PROMPT`.

---

## Design Decisions

- **Runs at temperature 0.2** — near-deterministic. Factual boundary analysis must be reproducible, not creative.
- **Runs in parallel with PageIndex fetch** in `summarize()` — both are independent of each other. Saves ~2–3 s per article.
- **On failure**, returns a minimal `JudgmentAnalysis(facts=[title])` — downstream agents still run, just with weaker grounding signals.
- **The `what_not_to_conclude` list is the most important field** — it is injected verbatim into both the storyteller and all persona prompts as a hard constraint.
