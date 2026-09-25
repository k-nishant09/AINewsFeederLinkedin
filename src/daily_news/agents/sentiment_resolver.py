"""
Sentiment Resolver — Intelligent News Agent sentiment inference.

Architecture
────────────
Every article that enters the pipeline needs a resolved sentiment before
the summary, persona, and publisher agents run.  Since GNews does not
return sentiment data, the resolver infers it in three layers:

  Layer 1 — Jev sentiment_polarity  (PRIMARY)
    Jev already scores the article in prefilter_article() and returns
    sentiment_polarity ("positive"|"negative"|"neutral").
    This is a direct LM classification of the article tone — not a rule.
    Confidence is set at 0.65 (Jev is a lightweight lora_decision_head;
    reliable for polarity but calibrated conservatively).

  Layer 2 — Keyword cross-validation  (SECONDARY — adjusts confidence)
    A narrow polarity lexicon scans the title + content.
    If keyword evidence AGREES with Jev's polarity → boost confidence to 0.72.
    If it DISAGREES → reduce confidence to 0.55 (flag uncertainty).

  Layer 3 — Event-type structural prior  (TERTIARY — last resort)
    If Jev prefilter scores are not available (JEV_ENABLED=false or error),
    event_type alone provides a soft prior:
      regulation → negative prior
      product_launch / funding → positive prior
      other → neutral
    Combined with keyword scan.  Max confidence 0.60.

  All results are tagged provider="inferred" in sentiment_stats.
  Prompts treat "inferred" as a soft calibration hint, not a hard signal.

Jev's role in sentiment decisions
──────────────────────────────────
  sentiment_polarity  → PRIMARY tone classification
  event_type          → structural prior when Jev polarity unavailable
  controversy_level   → amplifier: high controversy + negative Jev → boost neg confidence
  significance        → not used in sentiment (used elsewhere for context line weight)
  estimated_engagement → not used in sentiment

Public API
──────────
    resolve_sentiment(article, jev_scores) → (sentiment, sentiment_stats, ai_tag)

      sentiment:       "positive" | "negative" | "neutral"
      sentiment_stats: {"positive": f, "negative": f, "neutral": f, "provider": str}
      ai_tag:          str | None

      The "provider" field in sentiment_stats is always set:
        "inferred"  → derived from Jev + optional keyword cross-validation
"""
from __future__ import annotations

import re
from typing import Any

# ── Polarity lexicons (narrow — unambiguous AI-domain signal words only) ──────
# Kept deliberately tight: false positives from generic English are a bigger
# risk than false negatives.  Keyword scan is secondary to Jev's polarity.

_POSITIVE_WORDS: frozenset[str] = frozenset({
    "launch", "launches", "launched", "release", "releases", "released",
    "breakthrough", "record", "milestone", "raises", "raised", "funding",
    "investment", "partner", "partnership", "expand", "growth", "surge",
    "outperforms", "beats", "exceeds", "improves", "improvement", "upgrade",
    "advance", "innovation", "efficient", "accuracy", "accelerate", "boost",
    "approved", "approval", "agreement", "deal", "acquired", "acquisition",
    "open-source", "accessible", "cheaper", "faster",
})

_NEGATIVE_WORDS: frozenset[str] = frozenset({
    "risk", "risks", "danger", "threat", "threatens", "fail", "fails",
    "failed", "failure", "crash", "outage", "layoff", "layoffs", "cuts",
    "fired", "job losses", "displaces", "replace", "replaces", "automate",
    "concern", "concerned", "worried", "worry", "fear", "bias", "biased",
    "hallucinate", "hallucination", "error", "inaccurate", "mislead",
    "misinformation", "ban", "banned", "restrict", "fine", "fined", "penalty",
    "investigation", "probe", "lawsuit", "breach", "hack", "hacked",
    "vulnerability", "attack", "deepfake", "manipulation", "privacy",
    "surveillance", "monopoly", "antitrust", "decline", "fell", "drop",
    "warn", "warning", "criticism", "criticised", "criticized",
})

# Jev event_type → structural prior used ONLY when Jev polarity is unavailable
# (JEV_ENABLED=false or Jev call failed entirely).  Tuple: (pos, neg, neu).
_EVENT_TYPE_PRIOR: dict[str, tuple[float, float, float]] = {
    "product_launch": (0.55, 0.20, 0.25),
    "funding":        (0.55, 0.20, 0.25),
    "acquisition":    (0.45, 0.30, 0.25),
    "research":       (0.50, 0.20, 0.30),
    "regulation":     (0.20, 0.50, 0.30),
    "other":          (0.33, 0.33, 0.34),
}

# Jev event_type → inferred ai_tag when no tag is provided by the source
_EVENT_TYPE_TAG: dict[str, str] = {
    "product_launch": "artificial intelligence",
    "funding":        "investment",
    "acquisition":    "mergers and acquisitions",
    "research":       "artificial intelligence research",
    "regulation":     "AI regulation",
    "other":          "artificial intelligence",
}

# Keyword agreement → confidence adjustments on top of Jev base (0.65)
_AGREE_BOOST    = 0.07   # keywords agree  with Jev  → +0.07 → 0.72
_DISAGREE_PENALTY = 0.10  # keywords disagree with Jev → -0.10 → 0.55
_JEV_BASE_CONF  = 0.65   # Jev polarity without keyword cross-validation
_PRIOR_MAX_CONF = 0.60   # structural-prior-only path (no Jev polarity)


def _count_polarity(text: str) -> tuple[int, int]:
    """Return (positive_hits, negative_hits) from a lowercased text scan."""
    lowered = text.lower()
    pos = sum(1 for w in _POSITIVE_WORDS if re.search(r"\b" + re.escape(w) + r"\b", lowered))
    neg = sum(1 for w in _NEGATIVE_WORDS if re.search(r"\b" + re.escape(w) + r"\b", lowered))
    return pos, neg


def _keyword_polarity(title: str, content: str) -> str | None:
    """
    Return the keyword-dominant polarity ("positive"/"negative") or None if
    neither side has a clear majority (pos ≈ neg within 2 hits).
    """
    pos, neg = _count_polarity(f"{title} {content}")
    if pos == neg:
        return None
    if abs(pos - neg) <= 2:
        return None   # too close to call — don't adjust Jev confidence
    return "positive" if pos > neg else "negative"


def _make_stats(label: str, confidence: float, provider: str) -> dict[str, Any]:
    """
    Build a sentiment_stats dict from a dominant label + confidence.
    Distributes remaining probability evenly across the other two classes.
    """
    confidence = min(confidence, 0.72)   # hard cap — never claim more than 72%
    other = (1.0 - confidence) / 2
    classes = {"positive": other, "negative": other, "neutral": other}
    classes[label] = confidence
    classes["provider"] = provider
    return {k: round(v, 3) if k != "provider" else v for k, v in classes.items()}


def _infer_from_jev(
    jev_polarity: str,
    controversy: str,
    title: str,
    content: str,
) -> tuple[str, dict[str, Any]]:
    """
    Layer 1+2: Use Jev's sentiment_polarity as primary signal, keyword scan
    as secondary cross-validator to adjust confidence.

    controversy amplifies negative confidence when Jev says "negative".
    """
    kw_polarity = _keyword_polarity(title, content)

    confidence = _JEV_BASE_CONF

    if kw_polarity is not None:
        if kw_polarity == jev_polarity:
            confidence += _AGREE_BOOST       # keyword evidence agrees → more confident
        else:
            confidence -= _DISAGREE_PENALTY  # keyword evidence disagrees → less confident

    # Controversy amplifies negative signal when Jev already says negative
    if jev_polarity == "negative":
        if controversy == "high":
            confidence = min(confidence * 1.10, 0.72)
        elif controversy == "medium":
            confidence = min(confidence * 1.05, 0.72)

    return jev_polarity, _make_stats(jev_polarity, confidence, "inferred")


def _infer_from_prior(
    event_type: str,
    controversy: str,
    title: str,
    content: str,
) -> tuple[str, dict[str, Any]]:
    """
    Layer 3: Structural prior + keyword scan when Jev polarity is unavailable.
    Max confidence capped at _PRIOR_MAX_CONF (0.60).
    """
    pos_hits, neg_hits = _count_polarity(f"{title} {content}")
    total_hits = pos_hits + neg_hits or 1

    prior_pos, prior_neg, prior_neu = _EVENT_TYPE_PRIOR.get(event_type, _EVENT_TYPE_PRIOR["other"])

    kw_pos = pos_hits / total_hits
    kw_neg = neg_hits / total_hits
    kw_neu = max(0.0, 1.0 - kw_pos - kw_neg)

    # Blend: 60% keywords + 40% structural prior
    p = 0.6 * kw_pos + 0.4 * prior_pos
    n = 0.6 * kw_neg + 0.4 * prior_neg
    u = 0.6 * kw_neu + 0.4 * prior_neu

    # Controversy amplification
    if controversy in ("high", "medium") and n > p:
        amp = 1.12 if controversy == "high" else 1.06
        n = min(n * amp, _PRIOR_MAX_CONF)

    # Normalise
    total = p + n + u or 1.0
    p, n, u = p / total, n / total, u / total

    if p >= n and p >= u:
        label, conf = "positive", min(p, _PRIOR_MAX_CONF)
    elif n >= p and n >= u:
        label, conf = "negative", min(n, _PRIOR_MAX_CONF)
    else:
        label, conf = "neutral",  min(u, _PRIOR_MAX_CONF)

    return label, _make_stats(label, conf, "inferred")


def resolve_sentiment(
    article: dict[str, Any],
    jev_scores: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    """
    Return (sentiment, sentiment_stats, ai_tag) for any article.

    Decision tree:
      1. Jev sentiment_polarity present  → Layer 1+2 inference (provider="inferred")
      2. Jev polarity unavailable        → Layer 3 prior inference (provider="inferred")

    The "provider" key in sentiment_stats is always "inferred":
      → Jev + optional keyword validation — treat as calibration hint.

    Parameters
    ──────────
    article:    normalised article dict from _normalise_gnews_article
                (news_mcp/server.py)
    jev_scores: dict from jev_prefilter_scores[article_id] — contains
                event_type, controversy_level, sentiment_polarity
    """
    api_tag = article.get("ai_tag")

    # ── Resolve Jev context ───────────────────────────────────────────────────
    js           = jev_scores or {}
    event_type   = str(js.get("event_type",        "other")).lower().strip()
    controversy  = str(js.get("controversy_level", "low")).lower().strip()
    jev_polarity = str(js.get("sentiment_polarity", "")).lower().strip()

    title   = article.get("title",   "") or ""
    content = article.get("content", "") or article.get("description", "") or ""

    # ── 2. Jev polarity available — Layer 1+2 ─────────────────────────────────
    if jev_polarity in ("positive", "negative", "neutral"):
        label, stats = _infer_from_jev(jev_polarity, controversy, title, content)
    # ── 3. No Jev polarity — Layer 3 structural prior ─────────────────────────
    else:
        label, stats = _infer_from_prior(event_type, controversy, title, content)

    # ── Resolve ai_tag ────────────────────────────────────────────────────────
    resolved_tag = api_tag or _EVENT_TYPE_TAG.get(event_type, "artificial intelligence")

    return label, stats, resolved_tag
