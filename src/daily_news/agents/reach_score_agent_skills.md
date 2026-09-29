# skills.md — ReachScoreAgent

**File:** [`reach_score_agent.py`](reach_score_agent.py)
**Stage:** 12 — REACH SCORE
**LLM:** None — purely deterministic text analysis

---

## Purpose

Pre-publish LinkedIn organic-reach optimiser. Scores the composed post on 6 dimensions and auto-repairs it if it falls below the threshold. Runs in under 1 ms. No LLM calls.

---

## Tools Called

None — pure Python stdlib regex and string analysis.

---

## Scoring Dimensions

| Dimension | Max pts | What it measures |
|---|---|---|
| `hook_strength` | 20 | First 220 chars: number-first, named entity, structural tension; penalises weak openers |
| `specificity` | 20 | Numbers/stats, named entities, source link, bulleted facts |
| `question_quality` | 20 | Numbered-choice CTA, specific invite; penalises "what do you think?" |
| `length_fit` | 15 | 150–300 words optimal; sliding penalty outside range |
| `bait_penalty` | 15 | Full score = zero bait; -3 per engagement-bait pattern; -1 per excess hashtag (cap 3) |
| `topic_coherence` | 10 | AI/tech domain signals: ≥3 hits = 10 pts |

**Total:** 0–100. **Threshold:** 55. Below threshold → `verdict=REVISE`.

---

## Auto-Repair (on REVISE, no LLM)

1. Trim hashtags to last 3
2. Drop lines containing engagement-bait patterns
3. Clip post to 300 words at last sentence boundary (preserves footer)

---

## Inputs

| Input | Source |
|---|---|
| `post_text` | `PublisherAgent._compose_main_post()` |

---

## Output

`ReachScore` dataclass:
- `total`, `verdict` (`PUBLISH` | `REVISE`)
- Per-dimension scores
- `word_count`, `hashtag_count`, `bait_hits`, `notes`

Consumed by: `score_reach` graph node (stores in `reach_scores` state; repaired text stored in `persona_outputs["_repaired_post"]`).

---

## Design Decisions

- **Deterministic** — no LLM call, no randomness, reproducible on every run.
- **Auto-repair never touches** the hook (first 2 lines) or source link — only the tail and hashtag block.
- **Score metadata is logged** to Langfuse via the `score_reach` graph node for post-run analytics.
