# skills.md — LinkedInSkillsOptimizer

**File:** [`linkedin_skills_optimizer.py`](linkedin_skills_optimizer.py)
**Stage:** 10 — OPTIMISE
**LLM temperature:** Hook rewriter `0.3` · Audit judge `0.0`

---

## Purpose

Applies 2026 LinkedIn algorithm heuristics to the assembled post before evaluation. Three passes: hook selection, humanizer scrub, and algorithmic audit. Never changes factual claims or persona identities.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| LLM (hook rewriter) | `_pass1_hook()` → inline ChatPromptTemplate | Generates stronger hook variants when current score < 0.72 |
| LLM (audit judge) | `_pass3_audit()` → inline ChatPromptTemplate | Scores 5 dimensions and returns structured JSON |

---

## Three Passes

### Pass 1 — Hook Selector
- Scores current hook (first `🚨 **...**` line) using deterministic heuristics
- If score ≥ 0.72 → keep (no LLM call)
- If score < 0.72 → generate one stronger hook using the best formula for this story style:

| Formula | When | Signal |
|---|---|---|
| F7 — Number-first | investor/product stories | +34% median likes (2026) |
| F10 — Contrarian + historical | debate/AI stories | comments-optimised |
| F18 — False-binary dissolve | architecture/dual-answer stories | comments + reposts |
| F2 — R.I.P. Obituary | era-ending / pivot stories | reposts |

### Pass 2 — Humanizer (deterministic, no LLM)
- Removes reveal bridges: `"The result?"`, `"Here's what"`, `"Plot twist:"`
- Reduces AI-vocabulary density (leverage, streamline, game-changer, fundamentally…) when ≥3 hits per paragraph
- Caps staccato fragment runs (≤ 2 standalone short lines per post)
- Strips engagement-bait closers

**Never touches:** persona names, debate structure labels, factual claims, source URLs, hashtags, comic caption, header.

### Pass 3 — Audit (LLM)
Scores 0.0–1.0 on five dimensions:
- `hook_strength` · `commentability` · `ai_style_density` · `cta_quality` · `algorithm_compliance`

Returns `AuditResult` stored in `persona_dict["linkedin_audit"]` for the evaluation judge.

---

## Inputs

| Input | Source |
|---|---|
| `post_text` | `PublisherAgent._compose_main_post()` |
| `headline` | `NewsSummary.headline` |
| `central_tension` | `story.media_host_synthesis` or `story.perspective` |
| `story_style` | `story.narrative_style` |

---

## Output

`OptimizedPost`:
- `post_text` — post after hook + humanizer passes
- `audit` — `AuditResult` with 5 dimension scores + blockers + warnings
- `hook_was_replaced` — bool
- `humanizer_changes` — list of changes applied

---

## Design Decisions

- **Falls back gracefully** — any pass failure returns original post with partial audit. Pipeline never blocked.
- **Small connection pool** (`max_connections=5`) — optimizer runs once per article, not in bursts.
- **Audit scores are advisory** to the EvaluationAgent — low `hook_strength` or `commentability` adds a `failure_reason` for retry guidance but does not directly force `REGENERATE`.
