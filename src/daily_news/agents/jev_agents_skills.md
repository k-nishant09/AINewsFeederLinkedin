# jev_agents — Skills Reference

## Purpose

`jev_agents.py` provides four LangGraph nodes and a fallback heuristic scorer that
together form the **System One** (fast, non-LLM) intelligence layer of the pipeline.
Jev replaces LLM reasoning at the three points where latency and cost dominate:
article prefiltering, angle discovery, and persona routing.

---

## Nodes

### `jev_prefilter_articles(state) → dict`
**Stage 3 — ANALYZE**

Scores every article in `selected_articles` in parallel via Jev and selects the
**top-3** by composite score:

```
composite = relevance_score × 0.6 + estimated_engagement × 0.4
```

Picks top-3 (not top-1) to give the eval/retry loop fallback articles if the best
article keeps triggering banned phrases — eliminates the single-article deadlock.

**Populates in state:**

| Field | Type | Description |
|---|---|---|
| `selected_articles` | `list[dict]` | Top-3 articles by composite score |
| `jev_persona_hints` | `list[str]` | Persona fit from best article — warm start for routing |
| `jev_prefilter_scores` | `dict[article_id → intel_dict]` | Full Stage 3 intelligence signals per article |
| `workflow_status` | `str` | `"JEV_PREFILTERED"` |

**Full intelligence object** stored per article in `jev_prefilter_scores`:

```
event_type, relevance_score, significance, estimated_engagement,
controversy_level, active_personas, persona_scores, sentiment_polarity,
emotion: {curiosity, excitement, concern, urgency},
impact: {enterprise, developers, infrastructure, business, policy, general_public},
novelty, trend_velocity, audience_relevance
```

**Prefix cache:** `_JEV_PREFILTER_CACHE` — keyed by `SHA-256(title+description+published_at)`.
Survives across REGENERATE retries within a pod run; cleared on pod restart.

**Fallback paths:**
- `JEV_ENABLED=false` → `_heuristic_select(articles)`
- `JEV_BASE_URL` not set → `_heuristic_select(articles)`
- Any exception from Jev gateway → `_heuristic_select(articles)`
- All articles fail `is_ai_topic` check → `_heuristic_select(articles)`

---

### `jev_find_angle(state) → dict`
**Stage 5 — FIND THE ANGLE**

After summarisation, asks Jev for each summary's:
- `common_narrative` — what the mainstream coverage says
- `missing_angle` — underreported or contrarian perspective
- `recommended_audience` — segment most likely to engage
- `discussion_question` — prompt to spark conversation

Writes `content_opportunity` back into `jev_prefilter_scores[article_id]` so the
publisher and persona agents can derive the post angle from it.

Calls are **parallel via `asyncio.gather`** — one `content_angle` call per summary
(~800 ms each); total cost for 3 summaries ≈ 800 ms, not 2.4 s.

**Fallback:** skips silently (leaves `content_opportunity` absent) if Jev is disabled
or throws.

---

### `jev_route_personas(state) → dict`
**Stage 5 — ROUTE (sits between `jev_find_angle` and `generate_personas`)**

Asks Jev which of the four personas are *genuinely relevant* to this article.
Writes `jev_active_personas` into state; `generate_personas` reads this list to
skip irrelevant LLM calls.

Merges with `jev_persona_hints` from prefilter (union — keeps any persona flagged
by either signal).

Parallel gather across summaries (same pattern as `jev_find_angle`).

**Fallback:** all four personas if Jev is disabled, `JEV_BASE_URL` not set, or fails.

---

## Heuristic Fallback Scorer

Used by `_heuristic_select()` when Jev is unavailable.
Scores articles 0–4 across four deterministic dimensions:

| Dimension | Signal | Score |
|---|---|---|
| **Recency** | `published_at` age | 1.0 / 0.6 / 0.3 / 0.1 (≤24h / ≤48h / ≤72h / older) |
| **AI relevance** | keyword hits in title+description | `min(hits × 0.25, 1.0)` |
| **Source quality** | whitelist of 25 known AI/tech publishers | 1.0 if matched, else 0.5 |
| **Title length** | sweet-spot 60–100 chars | 1.0 / 0.6 / 0.3 |

Returns `top_n` (default 3) sorted by composite score.

---

## Internal helpers

### `_build_conflict_graph(summary_dict, intel) → str`
Deterministic (no LLM). Derives OPPORTUNITY and RISK signals from existing
intelligence fields and formats the **CONFLICT GRAPH** editorial spine prepended
to every Jev `content_angle` call. Forces tension-first framing.

### `_build_intelligence_state(summary_dict, intel) → str`
Assembles the full intelligence payload (CONFLICT GRAPH + headline/summary/impacts
/emotion/novelty) passed to Jev as the `content_angle` input string.

---

## Contracts

- **No LLM calls** — all decisions are either Jev gateway calls or pure-Python heuristics.
- **Graceful degradation** — every code path ends in a valid state dict regardless of Jev availability.
- **Idempotent** — safe to call multiple times on the same state (prefix cache prevents redundant Jev calls).
- **State is immutable** — every node returns `{**state, ...updated_fields}`.

---

## Configuration

| Setting | Default | Effect |
|---|---|---|
| `JEV_ENABLED` | `true` | Set to `false` to force heuristic fallback everywhere |
| `JEV_BASE_URL` | _(none)_ | If unset, heuristic fallback is used (no error) |

---

## Dependencies

- `daily_news.mcp.client.jev_singleton()` — singleton Jev gateway client
- `daily_news.mcp.jev_client.JevPrefilterResult` — typed result from `prefilter_article()`
- `daily_news.models.persona.PersonaType` — enum of the four personas
- `daily_news.config.settings.get_settings()` — reads `jev_enabled` / `jev_base_url`
