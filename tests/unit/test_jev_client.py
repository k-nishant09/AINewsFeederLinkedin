"""
Unit tests — JevClient helpers, prefilter logic, persona routing, evaluation.

All Jev HTTP calls are mocked via pytest-httpx / unittest.mock so these tests
run entirely offline.  They validate:
  - _as_float / _as_bool type coercion
  - JevPrefilterResult construction from raw gateway response (answers envelope)
  - persona_fit extraction including edge cases
  - skip_reason normalization
  - evaluate_content → EvaluationResult mapping
  - jev_prefilter_articles fallback when JEV_ENABLED=false
  - jev_prefilter_articles article selection by composite score
  - jev_route_personas subset selection + all-five fallback
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from daily_news.mcp.jev_client import (
    JevClient,
    JevPrefilterResult,
    _article_to_state,
    _as_bool,
    _as_float,
    _summary_to_state,
)
from daily_news.models.evaluation import EvaluationDecision
from daily_news.models.persona import PersonaType

# ── _as_float / _as_bool ─────────────────────────────────────────────────────

class TestHelpers:
    def test_as_float_string(self):
        assert _as_float("0.95") == 0.95

    def test_as_float_clamps_high(self):
        assert _as_float(1.5) == 1.0

    def test_as_float_clamps_low(self):
        assert _as_float(-0.5) == 0.0

    def test_as_float_none(self):
        assert _as_float(None) == 0.5

    def test_as_float_invalid_string(self):
        assert _as_float("n/a") == 0.5

    def test_as_bool_true_variants(self):
        for v in (True, "true", "True", "yes", "1", 1):
            assert _as_bool(v) is True

    def test_as_bool_false_variants(self):
        for v in (False, "false", "False", "no", "0", 0):
            assert _as_bool(v) is False


# ── State serialisation ───────────────────────────────────────────────────────

class TestStateSerialisation:
    def test_article_to_state_contains_title(self):
        article = {"title": "AI Takes Over", "content": "Details here", "source": "BBC"}
        state = _article_to_state(article)
        assert "AI Takes Over" in state
        assert "BBC" in state

    def test_article_to_state_includes_sentiment_when_present(self):
        """Sentiment fields should appear in the state string when present."""
        article = {
            "title": "Model Launch",
            "content": "Body",
            "source": "TechCrunch",
            "sentiment": "positive",
            "ai_tag": "large language model",
        }
        state = _article_to_state(article)
        assert "SENTIMENT: positive" in state
        assert "AI_TAG: large language model" in state

    def test_article_to_state_omits_sentiment_when_absent(self):
        """No SENTIMENT or AI_TAG lines when fields are None / missing."""
        article = {"title": "Test", "content": "Body", "source": "GNews"}
        state = _article_to_state(article)
        assert "SENTIMENT:" not in state
        assert "AI_TAG:" not in state

    def test_summary_to_state_contains_headline(self):
        summary = {
            "headline": "Model beats GPT-5",
            "summary": "A new model...",
            "business_impact": "High",
            "job_impact": "",
            "technology_impact": "",
            "policy_impact": None,
            "key_points": ["point A", "point B"],
        }
        state = _summary_to_state(summary)
        assert "Model beats GPT-5" in state
        assert "point A | point B" in state


# ── JevClient.prefilter_article ───────────────────────────────────────────────

def _noul_block(v: float) -> dict:
    return {"type": "noul", "noul": v, "confidence": 0.9}

def _choice_block(label: str) -> dict:
    return {"type": "choice", "choice": label, "confidence": 0.9}

def _score_block(v: float) -> dict:
    return {"type": "score", "score": v, "confidence": 0.9}


class TestPrefilterArticle:
    def _mock_answers(self, overrides: dict | None = None) -> dict:
        """Build a mock answers dict in real gateway envelope format."""
        base = {
            "relevance_score":      _noul_block(0.92),
            "is_ai_topic":          _noul_block(0.95),
            "controversy_level":    _choice_block("medium"),
            "event_type":           _choice_block("product_launch"),
            "significance":         _score_block(3.4),   # 3.4/4 = 0.85
            "persona_fit_business": _noul_block(0.9),
            "persona_fit_policy":   _noul_block(0.1),
            "persona_fit_genz":     _noul_block(0.2),
            "persona_fit_linkedin": _noul_block(0.88),
            "estimated_engagement": _noul_block(0.75),
            "skip_reason":          _choice_block("none"),
        }
        if overrides:
            base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_prefilter_maps_persona_fit(self):
        client = JevClient()
        article = {"article_id": "art-001", "title": "Test AI News", "content": "x", "source": "BBC"}

        with patch.object(client, "systemone", new=AsyncMock(return_value=self._mock_answers())):
            result = await client.prefilter_article(article)

        assert result.article_id == "art-001"
        assert result.relevance_score == pytest.approx(0.92)
        assert result.is_ai_topic is True
        assert result.event_type == "product_launch"
        assert result.significance == pytest.approx(0.85)
        assert PersonaType.BUSINESS.value in result.persona_fit
        assert PersonaType.LINKEDIN.value in result.persona_fit
        assert PersonaType.POLICY.value not in result.persona_fit
        assert result.skip_reason == ""

    @pytest.mark.asyncio
    async def test_prefilter_skip_reason_normalization(self):
        client = JevClient()
        article = {"article_id": "art-002", "title": "Sports news", "content": "y"}

        with patch.object(client, "systemone", new=AsyncMock(
            return_value=self._mock_answers({
                "skip_reason": _choice_block("not_ai"),
                "is_ai_topic": _noul_block(0.05),
            })
        )):
            result = await client.prefilter_article(article)

        assert result.skip_reason == "not_ai"
        assert result.is_ai_topic is False

    @pytest.mark.asyncio
    async def test_prefilter_fallback_persona_when_empty(self):
        """When all persona noul values are low but relevance > 0.5, linkedin is added."""
        client = JevClient()
        article = {"article_id": "art-003", "title": "Edge case", "content": "z"}
        resp = self._mock_answers({
            "persona_fit_business": _noul_block(0.1),
            "persona_fit_labor":    _noul_block(0.1),
            "persona_fit_policy":   _noul_block(0.1),
            "persona_fit_genz":     _noul_block(0.1),
            "persona_fit_linkedin": _noul_block(0.1),
            "relevance_score":      _noul_block(0.8),
        })
        with patch.object(client, "systemone", new=AsyncMock(return_value=resp)):
            result = await client.prefilter_article(article)

        assert PersonaType.LINKEDIN.value in result.persona_fit


# ── JevClient.route_personas ──────────────────────────────────────────────────

class TestRoutePersonas:
    @pytest.mark.asyncio
    async def test_returns_only_true_flags(self):
        client = JevClient()
        summary = {"headline": "AI Layoffs", "summary": "...", "business_impact": "",
                   "job_impact": "High", "technology_impact": "", "policy_impact": None,
                   "key_points": []}

        resp = {
            "needs_business": _noul_block(0.1),
            "needs_policy":   _noul_block(0.85),
            "needs_genz":     _noul_block(0.2),
            "needs_linkedin": _noul_block(0.3),
        }
        with patch.object(client, "systemone", new=AsyncMock(return_value=resp)):
            result = await client.route_personas(summary)

        assert PersonaType.POLICY in result
        assert PersonaType.BUSINESS not in result
        assert PersonaType.GENZ not in result
        assert PersonaType.LINKEDIN not in result

    @pytest.mark.asyncio
    async def test_fallback_to_linkedin_when_all_false(self):
        client = JevClient()
        summary = {"headline": "X", "summary": "", "business_impact": "",
                   "job_impact": "", "technology_impact": "", "policy_impact": None,
                   "key_points": []}
        resp = {k: _noul_block(0.1) for k in
                ["needs_business", "needs_policy", "needs_genz", "needs_linkedin"]}

        with patch.object(client, "systemone", new=AsyncMock(return_value=resp)):
            result = await client.route_personas(summary)

        assert result == [PersonaType.LINKEDIN]


# ── JevClient.evaluate_content ────────────────────────────────────────────────

class TestEvaluateContent:
    @pytest.mark.asyncio
    async def test_evaluate_content_maps_to_evaluation_result(self):
        client = JevClient()
        # score(3.88) / 4 = 0.97, score(0.04) / 4 = 0.01
        resp = {
            "factuality":                _score_block(3.88),
            "groundedness":              _score_block(3.80),
            "hallucination":             _score_block(0.04),
            "relevance":                 _score_block(3.72),
            "toxicity":                  _score_block(0.00),
            "pii_detected":              _noul_block(0.05),
            "prompt_injection_detected": _noul_block(0.03),
            "political_bias_detected":   _noul_block(0.04),
            "policy_check":              _choice_block("PASS"),
            "overall_score":             _score_block(3.84),
        }
        with patch.object(client, "systemone", new=AsyncMock(return_value=resp)):
            result = await client.evaluate_content(
                article_id="art-001",
                source_text="Source content here.",
                generated_text="Generated content here.",
            )

        assert result.article_id == "art-001"
        assert result.factuality == pytest.approx(3.88 / 4.0)
        assert result.hallucination == pytest.approx(0.04 / 4.0)
        assert result.pii_detected is False   # 0.05 < 0.5
        assert result.policy_check == "PASS"
        assert result.decision == EvaluationDecision.PASS

    @pytest.mark.asyncio
    async def test_evaluate_content_pii_flag(self):
        client = JevClient()
        resp = {
            "factuality":                _score_block(3.8),
            "groundedness":              _score_block(3.72),
            "hallucination":             _score_block(0.08),
            "relevance":                 _score_block(3.60),
            "toxicity":                  _score_block(0.04),
            "pii_detected":              _noul_block(0.92),  # > 0.5 → True
            "prompt_injection_detected": _noul_block(0.05),
            "political_bias_detected":   _noul_block(0.04),
            "policy_check":              _choice_block("FAIL"),
            "overall_score":             _score_block(2.0),
        }
        with patch.object(client, "systemone", new=AsyncMock(return_value=resp)):
            result = await client.evaluate_content("art-002", "src", "gen")

        assert result.pii_detected is True
        assert result.policy_check == "FAIL"


# ── _linkedin_len in publisher_agent ─────────────────────────────────────────

class TestLinkedinLen:
    def test_ascii_only(self):
        from daily_news.agents.publisher_agent import _linkedin_len
        assert _linkedin_len("hello") == 5

    def test_bmp_emoji_counts_as_two(self):
        # U+1F4BC BRIEFCASE — outside BMP, should count as 2
        from daily_news.agents.publisher_agent import _linkedin_len
        assert _linkedin_len("💼") == 2

    def test_mixed(self):
        from daily_news.agents.publisher_agent import _linkedin_len
        # "A💼B" = 1 + 2 + 1 = 4
        assert _linkedin_len("A💼B") == 4

    def test_in_bmp_char_counts_as_one(self):
        from daily_news.agents.publisher_agent import _linkedin_len
        # U+2705 CHECK MARK — in BMP, counts as 1
        assert _linkedin_len("✅") == 1


# ── jev_prefilter_articles node ───────────────────────────────────────────────

class TestJevPrefilterNode:
    def _make_state(self, articles: list[dict]) -> dict:
        return {
            "run_id": "TEST-001",
            "selected_articles": articles,
            "errors": [],
        }

    @pytest.mark.asyncio
    async def test_falls_back_when_jev_disabled(self):
        from daily_news.agents.jev_agents import jev_prefilter_articles

        articles = [
            {"article_id": "a1", "title": "Art 1", "content": ""},
            {"article_id": "a2", "title": "Art 2", "content": ""},
        ]
        state = self._make_state(articles)

        with patch("daily_news.agents.jev_agents.get_settings") as mock_settings:
            mock_settings.return_value.jev_enabled = False
            result = await jev_prefilter_articles(state)

        # Fallback keeps the single best (first) article
        assert len(result["selected_articles"]) == 1
        assert result["selected_articles"][0]["article_id"] == "a1"

    @pytest.mark.asyncio
    async def test_selects_highest_composite_score(self):
        from daily_news.agents.jev_agents import jev_prefilter_articles

        articles = [
            {"article_id": "low",  "title": "Low relevance", "content": ""},
            {"article_id": "high", "title": "High relevance", "content": ""},
        ]
        state = self._make_state(articles)

        def make_prefilter_result(article_id, relevance, engagement):
            return JevPrefilterResult(
                article_id=article_id,
                relevance_score=relevance,
                is_ai_topic=True,
                controversy_level="medium",
                event_type="product_launch",
                significance=0.7,
                persona_fit=[PersonaType.LINKEDIN.value],
                estimated_engagement=engagement,
            )

        side_effects = [
            make_prefilter_result("low",  0.4, 0.3),
            make_prefilter_result("high", 0.95, 0.85),
        ]

        with patch("daily_news.agents.jev_agents.get_settings") as mock_settings, \
             patch("daily_news.agents.jev_agents.JevClient") as MockJev:

            mock_settings.return_value.jev_enabled = True
            instance = MockJev.return_value
            instance.prefilter_article = AsyncMock(side_effect=side_effects)

            result = await jev_prefilter_articles(state)

        # Top 1 returned — highest composite score selected
        assert len(result["selected_articles"]) == 1
        assert result["selected_articles"][0]["article_id"] == "high"
        # jev_prefilter_scores only contains the selected article
        scores = result["jev_prefilter_scores"]
        assert isinstance(scores, dict)
        assert "high" in scores
        assert "low" not in scores
        assert scores["high"]["relevance_score"] == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_falls_back_when_jev_raises(self):
        from daily_news.agents.jev_agents import jev_prefilter_articles

        articles = [{"article_id": "a1", "title": "T", "content": ""}]
        state = self._make_state(articles)

        with patch("daily_news.agents.jev_agents.get_settings") as mock_settings, \
             patch("daily_news.agents.jev_agents.JevClient") as MockJev:

            mock_settings.return_value.jev_enabled = True
            instance = MockJev.return_value
            instance.prefilter_article = AsyncMock(side_effect=Exception("gateway timeout"))

            result = await jev_prefilter_articles(state)

        assert result["selected_articles"] == articles  # original unchanged


# ── jev_route_personas node ───────────────────────────────────────────────────

class TestJevRouterNode:
    @pytest.mark.asyncio
    async def test_falls_back_to_all_when_jev_disabled(self):
        from daily_news.agents.jev_agents import jev_route_personas

        state = {
            "run_id": "TEST-001",
            "summaries": [{"headline": "X", "article_id": "a1"}],
            "jev_persona_hints": [],
            "errors": [],
        }
        with patch("daily_news.agents.jev_agents.get_settings") as mock_settings:
            mock_settings.return_value.jev_enabled = False
            result = await jev_route_personas(state)

        all_values = {p.value for p in PersonaType}
        assert set(result["jev_active_personas"]) == all_values

    @pytest.mark.asyncio
    async def test_merges_prefilter_hints_with_jev_output(self):
        from daily_news.agents.jev_agents import jev_route_personas

        state = {
            "run_id": "TEST-001",
            "summaries": [{"headline": "Y", "article_id": "a2",
                           "summary": "", "business_impact": "",
                           "job_impact": "", "technology_impact": "",
                           "policy_impact": None, "key_points": []}],
            "jev_persona_hints": [PersonaType.POLICY.value],
            "errors": [],
        }

        with patch("daily_news.agents.jev_agents.get_settings") as mock_settings, \
             patch("daily_news.agents.jev_agents.JevClient") as MockJev:

            mock_settings.return_value.jev_enabled = True
            instance = MockJev.return_value
            # Jev returns only GENZ
            instance.route_personas = AsyncMock(return_value=[PersonaType.GENZ])

            result = await jev_route_personas(state)

        # Should contain both GENZ (from Jev) and POLICY (from prefilter hints)
        active = set(result["jev_active_personas"])
        assert PersonaType.GENZ.value in active
        assert PersonaType.POLICY.value in active
