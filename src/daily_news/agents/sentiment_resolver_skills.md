# sentiment_resolver — Skills Reference

## Purpose

`sentiment_resolver.py` resolves the sentiment polarity of any AI-domain news
article using a **three-layer, no-LLM inference stack**. It provides a stable
`(sentiment, sentiment_stats, ai_tag)` triple consumed by `SummaryAgent`,
`PersonaAgent`, and `PublisherAgent`.

---

## Public API

```python
resolve_sentiment(
    article: dict[str, Any],
    jev_scores: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], str | None]
```

Returns:

| Return value | Type | Description |
|---|---|---|
| `sentiment` | `str` | `"positive"` \| `"negative"` \| `"neutral"` |
| `sentiment_stats` | `dict` | `{positive: float, negative: float, neutral: float, provider: str}` |
| `ai_tag` | `str \| None` | Article's `ai_tag` field or an inferred tag from `event_type` |

`provider` is always `"inferred"` — Jev + optional keyword validation.
Prompts treat this as a calibration hint, **not** a hard signal.

---

## Three-layer decision tree

### Layer 1+2 — Jev polarity + keyword cross-validation (PRIMARY)
**Used when:** `jev_scores["sentiment_polarity"]` ∈ `{"positive","negative","neutral"}`

- Jev's `sentiment_polarity` is the primary signal (confidence base = **0.65**)
- Keyword scan (title + content) cross-validates:
  - Agrees with Jev → `+0.07` → confidence 0.72
  - Disagrees with Jev → `-0.10` → confidence 0.55
  - Too close to call (diff ≤ 2 hits) → no adjustment
- Controversy amplification (when Jev says `"negative"`):
  - `controversy_level="high"` → `× 1.10` (capped at 0.72)
  - `controversy_level="medium"` → `× 1.05` (capped at 0.72)
- **Hard cap: confidence never exceeds 0.72**

### Layer 3 — Structural prior + keyword blend (TERTIARY)
**Used when:** Jev polarity is missing/empty (JEV_ENABLED=false or Jev call failed)

- Reads `event_type` from `jev_scores` and looks up prior in `_EVENT_TYPE_PRIOR`:

| event_type | pos | neg | neu |
|---|---|---|---|
| `product_launch` | 0.55 | 0.20 | 0.25 |
| `funding` | 0.55 | 0.20 | 0.25 |
| `acquisition` | 0.45 | 0.30 | 0.25 |
| `research` | 0.50 | 0.20 | 0.30 |
| `regulation` | 0.20 | 0.50 | 0.30 |
| `other` | 0.33 | 0.33 | 0.34 |

- Keyword hit ratios blended: **60% keywords + 40% structural prior**
- Controversy amplifies negative if `neg > pos`
- Normalised to sum to 1.0 before final classification
- **Max confidence capped at 0.60** (prior-path is less reliable than Jev)

---

## Polarity lexicons

### `_POSITIVE_WORDS` (44 terms)
Core AI-domain positive signals: `launch`, `breakthrough`, `milestone`, `raises`,
`funding`, `partnership`, `innovation`, `approved`, `acquisition`, `faster`, etc.

### `_NEGATIVE_WORDS` (52 terms)
Core AI-domain negative signals: `risk`, `fail`, `layoff`, `replace`, `ban`,
`investigation`, `lawsuit`, `breach`, `deepfake`, `antitrust`, `decline`, etc.

Lexicons are **deliberately narrow** — tight false-positive control. Keyword scan
is secondary to Jev. Generic English words that could mean anything are excluded.

---

## `_make_stats(label, confidence, provider) → dict`

Distributes `(1.0 - confidence) / 2` evenly to the other two classes.
Always includes `provider` as a string key alongside the three float values.
Example for `label="positive", confidence=0.72`:
```python
{"positive": 0.72, "negative": 0.14, "neutral": 0.14, "provider": "inferred"}
```

---

## `ai_tag` resolution

1. Use `article["ai_tag"]` if present
2. Otherwise look up `event_type` in `_EVENT_TYPE_TAG`:

| event_type | inferred tag |
|---|---|
| `product_launch` | `"artificial intelligence"` |
| `funding` | `"investment"` |
| `acquisition` | `"mergers and acquisitions"` |
| `research` | `"artificial intelligence research"` |
| `regulation` | `"AI regulation"` |
| `other` | `"artificial intelligence"` |

---

## Contracts

- **No LLM calls, no network calls** — pure Python + regex.
- **Never raises** — returns a valid triple for any input including empty dicts.
- **Idempotent** — same inputs always return the same outputs.
- **Confidence hard cap at 0.72** on both code paths — prevents over-confidence.

---

## Integration

Called by `SummaryAgent._run_summary_pipeline()` immediately after Jev prefilter:

```python
sentiment, sentiment_stats, ai_tag = resolve_sentiment(article, jev_scores)
```

The returned values are stored on `NewsSummary.sentiment`, `.sentiment_stats`,
and `.ai_tag` and flow through to `PersonaAgent` and `PublisherAgent` as part of
the intelligence signal block injected into every persona prompt.
