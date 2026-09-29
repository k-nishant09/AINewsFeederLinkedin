# skills.md — GrammarAgent

**File:** [`grammar_agent.py`](grammar_agent.py)
**Stage:** 13 — PUBLISH (called inside PublisherAgent before LinkedIn)
**LLM temperature:** `0.0` (deterministic)

---

## Purpose

Final proofreading pass on the assembled post text. Fixes spelling, grammar, punctuation, and capitalisation without changing tone, structure, or meaning. Lightweight, best-effort — never blocks publication.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| Langfuse | `get_langfuse_callback(run_id, tags, metadata)` | Traces the correction call |

---

## Inputs

| Input | Source |
|---|---|
| `post_text` | Assembled post from `PublisherAgent._compose_main_post()` |
| `run_id`, `article_id` | Passed through for tracing |

---

## Output

Corrected post text (string). On any error — network, parse, timeout — returns original text unchanged.

---

## Prompts

| Prompt file | Content |
|---|---|
| [`prompts/grammar.txt`](../../../prompts/grammar.txt) | System instruction: correct only, do not rewrite |

---

## Design Decisions

- **Temperature 0.0** — correction is deterministic. The model should not make creative choices.
- **Best-effort, non-blocking** — `except Exception` returns the original. A grammar failure never kills a publish.
- **Empty output guard** — if the LLM returns an empty string, the original post is used (defensive against truncation or refusal).
