# skills.md — MediaStorytellerAgent + SummaryAgent

**File:** [`summary_agent.py`](summary_agent.py)
**Stage:** 6 — EXPLAIN
**LLM temperature:** Storyteller `0.5` · Summary `0.2`

---

## Purpose

Two-pass pipeline that transforms a raw news article into structured intelligence:

- **Pass 1 — MediaStorytellerAgent**: Reads the article like a journalist. Extracts the *story* — not facts, the narrative. Outputs a `NewsStory` with hook, analogy, perspective, second-order effect, and narrative style.
- **Pass 2 — SummaryAgent**: Produces the structured `NewsSummary` (headline, key points, business/job/technology/policy impacts) using the story object as a framing calibration layer.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| `PageIndexMCPClient` | `get_relevant_sections(document_id, question)` | Retrieves structured evidence from the article's PageIndex tree |
| `JudgmentAgent` | `analyze(article_id, title, source, content, …)` | Provides fact/claim/uncertainty boundaries before storytelling begins |
| Langfuse | `get_langfuse_callback(run_id, tags, metadata)` | Traces both LLM calls independently |

---

## Inputs

| Input | Source |
|---|---|
| `article_id`, `title`, `source`, `source_url`, `content` | `selected_articles` state |
| `pageindex_sections` | PageIndex MCP |
| `jev_scores` | `jev_prefilter_scores` state |
| `judgment` | JudgmentAgent output |
| `sentiment`, `sentiment_stats`, `ai_tag` | `SentimentResolver` |

---

## Outputs

| Output | Type | Consumed by |
|---|---|---|
| `NewsStory` | Pydantic model | PersonaAgent (story context), SummaryAgent (framing) |
| `NewsSummary` | Pydantic model | PersonaAgent, EvaluationAgent, PublisherAgent |
| `NewsIntelligence` | Embedded in NewsSummary | PublisherAgent (post assembly) |

---

## Prompts

| Agent | Prompt file |
|---|---|
| MediaStorytellerAgent | [`prompts/storyteller.txt`](../../../prompts/storyteller.txt) |
| SummaryAgent | [`prompts/summary.txt`](../../../prompts/summary.txt) |

---

## Design Decisions

- **Pass 1 runs at temperature 0.5** — needs creative framing ability to identify the hook and analogy, not just extract facts.
- **Pass 2 runs at temperature 0.2** — structured JSON output; lower temperature reduces parse failures.
- **JudgmentAgent boundaries are injected into Pass 1** — the storyteller is explicitly told which claims are verified vs. reported vs. uncertain, so it never asserts beyond the evidence.
- **On Pass 1 failure**, an empty `NewsStory()` is returned — all downstream agents degrade gracefully via `or "not available"` fallbacks.
- **`_build_intelligence()`** is a pure function that assembles the `NewsIntelligence` backbone from Jev scores + summary fields. No LLM call.
