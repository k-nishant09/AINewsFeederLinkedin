# skills.md — PersonaAgent + PersonaAgentFactory

**File:** [`persona_agent.py`](persona_agent.py)
**Stage:** 9 — CREATE
**LLM temperature:** `0.4` (all four personas)

---

## Purpose

Generates four distinct human voice perspectives on the article — each from a different professional lens. The four voices are designed to create intellectual tension so readers choose sides and comment.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| `PageIndexMCPClient` | `get_relevant_sections(document_id, question)` | Retrieves evidence for persona grounding |
| Langfuse | `get_langfuse_callback(run_id, tags, metadata)` | Traces each persona independently (same session_id links them) |

---

## Personas

| Persona | Role | Focus | Prompt file |
|---|---|---|---|
| `BUSINESS` | AI Infrastructure Founder | Opportunity · commercial premise · market timing | [`prompts/capitalist.txt`](../../../prompts/capitalist.txt) |
| `LINKEDIN` | ML Platform Engineer | Operational reality · complexity trade-offs · architectural debt | [`prompts/linkedin.txt`](../../../prompts/linkedin.txt) |
| `GENZ` | AI Industry Analyst | Market dynamics · who benefits at scale · switching cost | [`prompts/genz.txt`](../../../prompts/genz.txt) |
| `POLICY` | AI Policy Lead | Governance · accountability gap · concentration risk | [`prompts/policy.txt`](../../../prompts/policy.txt) |

---

## Inputs

| Input | Source |
|---|---|
| `NewsSummary` (full object) | `summaries` state |
| `evidence_sections` | PageIndex MCP |
| `avoid_phrases` | `evaluation_results.failure_reasons` (retry cycles only) |
| `jev_active_personas` | `jev_router` node |

---

## Outputs

`PersonaSetOutput` — one `PersonaOutput` per persona:
- `perspective` — the voice's statement (2–4 punchy sentences)
- `evidence` — grounded facts cited
- `next_question` — explicit causal hand-off question to the next speaker

Consumed by: `EvaluationAgent`, `PublisherAgent` (post assembly + comment text), `ComicGenerator`.

---

## Design Decisions

- **Runs at temperature 0.4** — enough creativity for distinct voice, not so high that facts drift.
- **All four run in parallel** via `asyncio.gather` — total time is `max(persona_1..4)`, not sum.
- **Prefix-cache**: story context (60 fields) is computed once per article and cached in `_STORY_CONTEXT_CACHE`. Identical prefix across retries improves LLM provider KV cache hit rate.
- **`avoid_phrases` injection**: on REGENERATE cycles, exact banned phrases from the last evaluation are injected into the prompt. The model sees both the specific offenders and the full canonical banned list.
- **Jev routing**: only Jev-selected personas run. Skipped personas get a `_stub_persona()` (empty perspective) so `PersonaSetOutput` always has all four fields.
- **`next_question` grounding**: `PersonaOutput.next_question` must share at least one keyword with the speaker's own `perspective`. The comic generator validates this via `_grounded_question()` before rendering the panel.
