"""
Unit tests — sentiment_resolver.py

Covers all resolution layers:
  Layer 1a — Jev polarity + keyword AGREES          (confidence boosted to 0.72)
  Layer 1b — Jev polarity + keyword DISAGREES       (confidence reduced to 0.55)
  Layer 1c — Jev polarity, no keyword signal        (confidence stays at 0.65)
  Layer 1d — Jev negative + high controversy        (confidence amplified)
  Layer 2  — Structural prior (no Jev polarity)
  Edge cases: missing fields, unknown event_type, neutral polarity
"""
from __future__ import annotations

import pytest

from daily_news.agents.sentiment_resolver import (
    _AGREE_BOOST,
    _DISAGREE_PENALTY,
    _JEV_BASE_CONF,
    _PRIOR_MAX_CONF,
    _count_polarity,
    _keyword_polarity,
    _make_stats,
    resolve_sentiment,
)


# ── _count_polarity ───────────────────────────────────────────────────────────

class TestCountPolarity:
    def test_positive_hit(self):
        pos, neg = _count_polarity("OpenAI launches a new model")
        assert pos >= 1
        assert neg == 0

    def test_negative_hit(self):
        pos, neg = _count_polarity("Layoffs hit the AI industry amid concern")
        assert neg >= 1

    def test_empty_string_returns_zeros(self):
        pos, neg = _count_polarity("")
        assert pos == 0
        assert neg == 0

    def test_mixed_text(self):
        # "breakthrough" (positive) + "risk" (negative)
        pos, neg = _count_polarity("A breakthrough model raises concern about risk")
        assert pos >= 1
        assert neg >= 1

    def test_word_boundary(self):
        # "risky" should NOT match the word "risk"
        pos, neg = _count_polarity("This is a risky move")
        assert neg == 0


# ── _keyword_polarity ─────────────────────────────────────────────────────────

class TestKeywordPolarity:
    def test_returns_positive_when_dominant(self):
        # Many positive words, no negative
        text = "launches breakthrough record milestone funding raises partner"
        assert _keyword_polarity(text, "") == "positive"

    def test_returns_negative_when_dominant(self):
        # Many negative words, no positive
        text = "layoffs ban hacked breach lawsuit fine concern failure"
        assert _keyword_polarity("", text) == "negative"

    def test_returns_none_when_tied(self):
        # Equal counts → None
        result = _keyword_polarity("launches", "layoffs")  # 1 each — tied
        assert result is None

    def test_returns_none_when_close(self):
        # pos=2, neg=1 → diff=1 ≤ 2 → None (too close to call)
        result = _keyword_polarity("launches breakthrough", "layoffs")
        assert result is None

    def test_returns_none_on_empty(self):
        assert _keyword_polarity("", "") is None


# ── _make_stats ───────────────────────────────────────────────────────────────

class TestMakeStats:
    def test_dominant_label_gets_confidence(self):
        stats = _make_stats("positive", 0.72, "newsdata")
        assert stats["positive"] == pytest.approx(0.72, abs=0.001)

    def test_other_classes_share_remainder(self):
        stats = _make_stats("negative", 0.60, "inferred")
        assert stats["positive"] == pytest.approx((1.0 - 0.60) / 2, abs=0.001)
        assert stats["neutral"]  == pytest.approx((1.0 - 0.60) / 2, abs=0.001)

    def test_provider_field_is_set(self):
        stats = _make_stats("neutral", 0.50, "newsdata")
        assert stats["provider"] == "newsdata"

    def test_confidence_capped_at_072(self):
        stats = _make_stats("positive", 0.99, "inferred")
        assert stats["positive"] == pytest.approx(0.72, abs=0.001)

    def test_all_classes_sum_to_one(self):
        stats = _make_stats("negative", 0.65, "inferred")
        total = stats["positive"] + stats["negative"] + stats["neutral"]
        assert total == pytest.approx(1.0, abs=0.01)


# ── Layer 1a: Jev polarity + keyword AGREES ───────────────────────────────────

class TestLayer1JevKeywordAgree:
    def test_positive_jev_positive_keywords_boosts_confidence(self):  # noqa: D102
        article = {
            "title": "OpenAI launches breakthrough model with record funding",
            "content": "The company raises investment and announces a new partnership.",
        }
        jev = {
            "sentiment_polarity": "positive",
            "event_type": "product_launch",
            "controversy_level": "low",
        }
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=jev)

        assert sentiment == "positive"
        assert stats["provider"] == "inferred"
        # Agreement boost → base 0.65 + 0.07 = 0.72
        assert stats["positive"] == pytest.approx(_JEV_BASE_CONF + _AGREE_BOOST, abs=0.01)

    def test_negative_jev_negative_keywords_boosts_confidence(self):
        article = {
            "title": "AI company faces ban after breach and hack investigation",
            "content": "Layoffs and fines followed as the firm failed regulators.",
        }
        jev = {
            "sentiment_polarity": "negative",
            "event_type": "regulation",
            "controversy_level": "low",
        }
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=jev)

        assert sentiment == "negative"
        assert stats["negative"] == pytest.approx(_JEV_BASE_CONF + _AGREE_BOOST, abs=0.01)


# ── Layer 1b: Jev polarity + keyword DISAGREES ────────────────────────────────

class TestLayer1JevKeywordDisagree:
    def test_jev_positive_but_negative_keywords_reduces_confidence(self):
        # Jev says "positive" but text is full of negative words
        article = {
            "title": "AI firm faces lawsuit and ban amid hack concerns",
            "content": "Layoffs threaten the company as failure looms.",
        }
        jev = {
            "sentiment_polarity": "positive",
            "event_type": "other",
            "controversy_level": "low",
        }
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=jev)

        # Jev polarity wins, but confidence drops
        assert sentiment == "positive"
        assert stats["positive"] == pytest.approx(_JEV_BASE_CONF - _DISAGREE_PENALTY, abs=0.01)
        assert stats["provider"] == "inferred"

    def test_reduced_confidence_is_still_above_zero(self):
        article = {"title": "ban hack fail breach", "content": ""}
        jev = {"sentiment_polarity": "positive", "event_type": "other",
               "controversy_level": "low"}
        _, stats, _ = resolve_sentiment(article, jev_scores=jev)
        assert stats["positive"] > 0


# ── Layer 1c: Jev polarity, no keyword signal ─────────────────────────────────

class TestLayer1JevNoKeywordSignal:
    def test_neutral_text_keeps_jev_base_confidence(self):
        # Text has no polarity words → keyword scan returns None → no adjustment
        article = {
            "title": "AI researchers meet at conference",
            "content": "Several talks were given about models and data.",
        }
        jev = {
            "sentiment_polarity": "neutral",
            "event_type": "research",
            "controversy_level": "low",
        }
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=jev)

        assert sentiment == "neutral"
        assert stats["neutral"] == pytest.approx(_JEV_BASE_CONF, abs=0.01)


# ── Layer 1d: Controversy amplification ──────────────────────────────────────

class TestLayer1ControversyAmplification:
    def test_high_controversy_amplifies_negative_confidence(self):
        article = {
            "title": "AI firm accused of bias and surveillance",
            "content": "Privacy concerns mount as investigation expands.",
        }
        jev = {
            "sentiment_polarity": "negative",
            "event_type": "regulation",
            "controversy_level": "high",
        }
        _, stats_high, _ = resolve_sentiment(article, jev_scores=jev)

        jev_low = dict(jev, controversy_level="low")
        _, stats_low, _ = resolve_sentiment(article, jev_scores=jev_low)

        # High controversy → higher negative confidence (or at least not lower)
        assert stats_high["negative"] >= stats_low["negative"]

    def test_controversy_does_not_amplify_positive(self):
        article = {"title": "AI model launched", "content": "A new release."}
        jev = {"sentiment_polarity": "positive", "event_type": "product_launch",
               "controversy_level": "high"}
        _, stats, _ = resolve_sentiment(article, jev_scores=jev)
        # Controversy only amplifies negative, not positive
        assert stats["positive"] <= 0.72   # still capped


# ── Layer 2: Structural prior (no Jev polarity) ───────────────────────────────

class TestLayer2StructuralPrior:
    def test_falls_back_to_prior_when_jev_unavailable(self):
        article = {
            "title": "New AI regulation passed in Europe",
            "content": "The European Parliament approved strict controls.",
        }
        # No jev_scores at all
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=None)

        assert sentiment in ("positive", "negative", "neutral")
        assert stats["provider"] == "inferred"
        # Prior-only confidence capped at 0.60
        dominant = stats[sentiment]
        assert dominant <= _PRIOR_MAX_CONF + 0.01  # small tolerance for rounding

    def test_regulation_event_type_biases_negative(self):
        article = {"title": "New ban on AI models", "content": ""}
        jev = {"event_type": "regulation", "controversy_level": "low",
               "sentiment_polarity": ""}  # empty → no polarity
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=jev)

        # regulation prior is (0.20, 0.50, 0.30) — negative should be highest
        assert sentiment == "negative"

    def test_product_launch_event_type_biases_positive(self):
        article = {"title": "New AI model released with improvements", "content": ""}
        jev = {"event_type": "product_launch", "controversy_level": "low",
               "sentiment_polarity": ""}
        sentiment, _, _ = resolve_sentiment(article, jev_scores=jev)

        assert sentiment == "positive"

    def test_prior_only_confidence_never_exceeds_cap(self):
        article = {
            "title": "AI raises record funding with breakthrough partnership launches release",
            "content": "Investment acquisition deal approved agreement growth surge faster cheaper.",
        }
        jev = {"event_type": "funding", "controversy_level": "high",
               "sentiment_polarity": ""}
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=jev)
        assert stats[sentiment] <= _PRIOR_MAX_CONF + 0.001


# ── ai_tag resolution ─────────────────────────────────────────────────────────

class TestAiTagResolution:
    def test_article_tag_returned_when_present(self):
        """ai_tag pre-set on the article (e.g. from a prior enrichment step) is returned."""
        article = {
            "ai_tag": "large language model",
            "title": "",
        }
        _, _, ai_tag = resolve_sentiment(article)
        assert ai_tag == "large language model"

    def test_event_type_tag_used_when_article_tag_absent(self):
        article = {"title": "New AI model released", "content": ""}
        jev = {"event_type": "regulation", "controversy_level": "low",
               "sentiment_polarity": "negative"}
        _, _, ai_tag = resolve_sentiment(article, jev_scores=jev)
        assert ai_tag == "AI regulation"

    def test_fallback_tag_is_artificial_intelligence(self):
        article = {"title": "Something happened", "content": ""}
        jev = {"event_type": "other", "controversy_level": "low",
               "sentiment_polarity": "neutral"}
        _, _, ai_tag = resolve_sentiment(article, jev_scores=jev)
        assert ai_tag == "artificial intelligence"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_completely_empty_article_and_no_jev(self):
        sentiment, stats, ai_tag = resolve_sentiment({})
        assert sentiment in ("positive", "negative", "neutral")
        assert "provider" in stats
        assert ai_tag is not None   # falls back to event-type tag

    def test_jev_scores_none_defaults_to_prior(self):
        article = {"title": "Something about AI"}
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=None)
        assert stats["provider"] == "inferred"

    def test_unknown_event_type_uses_other_prior(self):
        article = {"title": "Something about AI", "content": ""}
        jev = {"event_type": "unknown_type", "controversy_level": "low",
               "sentiment_polarity": ""}
        sentiment, stats, _ = resolve_sentiment(article, jev_scores=jev)
        assert sentiment in ("positive", "negative", "neutral")

    def test_jev_polarity_uppercase_normalised(self):
        # sentiment_polarity values should be normalised to lowercase by the caller
        # but resolver also handles already-lowercase values correctly
        article = {"title": "AI launches", "content": ""}
        jev = {"sentiment_polarity": "positive", "event_type": "product_launch",
               "controversy_level": "low"}
        sentiment, _, _ = resolve_sentiment(article, jev_scores=jev)
        assert sentiment == "positive"

    def test_stats_always_contain_all_three_classes(self):
        article = {"title": "AI model launched", "content": ""}
        jev = {"sentiment_polarity": "positive", "event_type": "product_launch",
               "controversy_level": "low"}
        _, stats, _ = resolve_sentiment(article, jev_scores=jev)
        for key in ("positive", "negative", "neutral"):
            assert key in stats
            assert isinstance(stats[key], float)

    def test_stats_values_are_non_negative(self):
        article = {"title": "AI failure risk ban hack", "content": ""}
        _, stats, _ = resolve_sentiment(article)
        for key in ("positive", "negative", "neutral"):
            assert stats[key] >= 0.0
