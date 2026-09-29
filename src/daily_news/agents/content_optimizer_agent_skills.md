# skills.md — ContentOptimizerAgent

**File:** [`content_optimizer.py`](content_optimizer.py)
**Stage:** 14 — LEARN
**LLM temperature:** `0.3`

---

## Purpose

Closed-loop performance diagnostician. Runs after publishing to observe engagement signals, diagnose which structural component underperformed, and generate story mutations for the learning loop. Feeds back into future Jev angle recommendations.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| `LinkedInMCPClient` | `get_post_analytics(post_urn)` | Fetches impressions/reactions/comments/reposts |
| Langfuse | `get_langfuse_callback(run_id, tags, metadata)` | Traces diagnosis and mutation calls |

---

## Two-Step Process

### Step 1 — `diagnose_performance()`
Given engagement metrics and the post text, scores 6 structural components (0–1):

| Component | What it measures |
|---|---|
| `hook_score` | Did the opening stop the scroll? |
| `storytelling_score` | Was the structure clear and human? |
| `audience_relevance_score` | Did it reach the right decision-makers? |
| `perspective_score` | Was the take non-obvious? |
| `dialogue_score` | Were persona mental models distinct? |
| `question_score` | Did the CTA earn substantive replies? |

Returns `weakest_component` and `actionable_recommendation`.

### Step 2 — `mutate_story()`
Generates 3 alternative narrative framings targeting the weakest component.

Available mutation styles: `human_story` · `unexpected_consequence` · `contrarian` · `future_scenario` · `architect_lens` · `developer_lens` · `problem_solution`

Rule: change only the weakest structural component (hook, perspective, or framing). Preserve all factual grounding.

---

## Inputs

| Input | Source |
|---|---|
| `EngagementMetrics` | LinkedIn MCP analytics |
| `post_text` | `NewsSummary.summary` |
| `NewsSummary` | `summaries` state |

---

## Outputs

| Output | Type | Consumed by |
|---|---|---|
| `PerformanceDiagnosis` | Pydantic model | `optimization_diagnosis` state |
| `list[StoryMutation]` | Pydantic models | `story_mutations` state |

---

## Design Decisions

- **Non-blocking** — runs after publish, cannot affect the current post.
- **No sensationalism rule** — the agent is explicitly instructed: "Do NOT advise sensationalism or clickbait. Advise precise, high-substance adjustments."
- **Fallback on diagnosis failure** — deterministic fallback: `comment_ratio < 0.001` → weakness = `"question"`, else `"hook"`.
- **Fallback on mutation failure** — returns one hardcoded `unexpected_consequence` mutation so the state always has at least one learning signal.
