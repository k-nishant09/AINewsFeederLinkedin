# skills.md — EvaluationAgent

**File:** [`evaluation_agent.py`](evaluation_agent.py)
**Stage:** 11 — EVALUATE
**LLM temperature:** Judge `0.1` (deterministic critic)

---

## Purpose

The quality gate. Decides whether a generated post is fit for LinkedIn publication. Three independent layers ensure no single point of failure can pass bad content.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| `JevClient` | `evaluate_content(article_id, source_text, generated_text)` | Primary factuality/groundedness/hallucination scores |
| `EvaluationMCPClient` | `evaluate_content(…)` | Fallback when Jev is unavailable |
| Langfuse | `get_langfuse_callback(run_id, tags, metadata)` | Traces the judge LLM call |

---

## Evaluation Layers

### Layer 0 — Deterministic Pre-scan (no LLM)
Calls `_check_persona_text()` on all four persona perspectives before any LLM call.
- Scans for **banned openers** (anecdote/hypothetical/throat-clearing patterns)
- Scans for **banned inline phrases** (AI clichés, generic abstractions, lazy closers)
- Scans regex patterns: `the real <X> is/lies`, `marks a significant`, `in the end`

Any hit → immediate `REGENERATE` with the exact phrase as `failure_reason`.

### Layer 1 — Jev / MCP Quality Scoring
Returns float scores 0–1:
- `factuality` threshold: `0.50` (configurable via `EVAL_FACTUALITY_THRESHOLD`)
- `groundedness` threshold: `0.50` (configurable via `EVAL_GROUNDEDNESS_THRESHOLD`)
- `hallucination` threshold: `0.85` (configurable via `EVAL_HALLUCINATION_THRESHOLD`)

Fallback chain: Jev → MCP → neutral scores `(0.6/0.6/0.3)` when both fail.

### Layer 2 — LLM-as-a-Judge
Independent LLM call at `temperature=0.1`. Checks story-level quality only:
- Does genuine intellectual clash exist between personas?
- Is the post article-specific (not generic enough to apply to any AI story)?
- Does at least one claim trace back to the source?

Sets `verdict=REVISE` **only when all three fail simultaneously**.

---

## Gate Decisions

| Decision | Trigger | Next node |
|---|---|---|
| `PASS` | All thresholds met, judge approves | `score_reach` |
| `REGENERATE` | Below threshold, pre-scan hit, or judge boilerplate | `generate_personas` (retry) |
| `BLOCK` | PII detected or prompt injection | END — never published |
| `HUMAN_REVIEW` | Political bias or policy check failed | `approval` queue |

---

## Prompts

Inline system prompt in [`evaluation_agent.py`](evaluation_agent.py) — `self._judge_prompt`.

---

## Gateway

Uses **`make_eval_llm()`** from `llm_factory.py` — routes to `EVAL_LLM_*` env vars, falling back to `LLM_*`. To run the judge on a different provider from the generator, set `EVAL_LLM_BASE_URL`, `EVAL_LLM_API_KEY`, `EVAL_LLM_MODEL`.

---

## Design Decisions

- **Generator and judge are never the same chain invocation** — they are separate `ChatOpenAI` instances at different temperatures, even when using the same model.
- **Degenerate score detection**: `factuality=0.0, groundedness=0.0, hallucination=1.0` is treated as a backend failure, not a content failure. Replaced with neutral scores.
- **LinkedIn audit signals** from `LinkedInSkillsOptimizer` are advisory — they add `failure_reasons` for retry guidance but do not directly force `REGENERATE`.
- **Pool size is intentionally small** (`max_connections=10`) — the judge runs serially once per article, not in parallel bursts.
