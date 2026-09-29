"""
Unit tests for JudgmentAgent.

JudgmentAgent.analyze() is an async LLM call that:
  - Returns a JudgmentAnalysis on success
  - Falls back to a safe JudgmentAnalysis(facts=[title], ...) on any exception
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daily_news.models.intelligence import JudgmentAnalysis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent():
    """Construct a JudgmentAgent with the LLM fully mocked out."""
    with patch("daily_news.agents.judgment_agent.make_llm") as mock_make_llm:
        mock_llm = MagicMock()
        mock_make_llm.return_value = mock_llm
        from daily_news.agents.judgment_agent import JudgmentAgent
        agent = JudgmentAgent()
        return agent, mock_llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestJudgmentAgentAnalyze:
    """JudgmentAgent.analyze()"""

    @pytest.mark.asyncio
    async def test_returns_judgment_analysis_on_success(self):
        """Normal path: LLM returns a valid structured response."""
        agent, _ = _make_agent()

        from daily_news.agents.judgment_agent import _JudgmentAnalysisRaw
        raw = _JudgmentAnalysisRaw(
            facts=["OpenAI released GPT-5"],
            reported_claims=["Claims 90% accuracy"],
            analysis_implications=["Cost reduction possible"],
            uncertainties=["Benchmark results unverified"],
            what_not_to_conclude=["Not proven to replace engineers"],
        )

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=raw)

        with patch.object(agent, "_prompt") as mock_prompt, \
             patch.object(agent, "_parser") as mock_parser, \
             patch.object(agent, "_llm"):
            mock_prompt.__or__ = MagicMock(return_value=MagicMock(
                __or__=MagicMock(return_value=mock_chain)
            ))

            # Directly patch the chain construction
            with patch("daily_news.agents.judgment_agent.JudgmentAgent.analyze",
                       wraps=agent.analyze):
                # Simulate chain returning raw
                agent._prompt = MagicMock()
                agent._llm = MagicMock()
                agent._parser = MagicMock()

                full_chain = MagicMock()
                full_chain.ainvoke = AsyncMock(return_value=raw)

                agent._prompt.__or__ = MagicMock(return_value=MagicMock(
                    __or__=MagicMock(return_value=full_chain)
                ))

                result = await agent.analyze(
                    article_id="art1",
                    title="OpenAI released GPT-5",
                    source="techcrunch",
                    content="Full article content...",
                    pageindex_sections="Section text...",
                    run_id="run1",
                )

        assert isinstance(result, JudgmentAnalysis)
        assert result.facts == ["OpenAI released GPT-5"]
        assert result.reported_claims == ["Claims 90% accuracy"]
        assert result.analysis_implications == ["Cost reduction possible"]
        assert result.uncertainties == ["Benchmark results unverified"]
        assert result.what_not_to_conclude == ["Not proven to replace engineers"]

    @pytest.mark.asyncio
    async def test_fallback_on_exception(self):
        """If the LLM chain raises, returns a safe fallback JudgmentAnalysis."""
        agent, _ = _make_agent()

        title = "Test Article Title"

        full_chain = MagicMock()
        full_chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        agent._prompt = MagicMock()
        agent._llm = MagicMock()
        agent._parser = MagicMock()
        agent._prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=full_chain)
        ))

        result = await agent.analyze(
            article_id="art2",
            title=title,
            source="reuters",
            content="Article text",
            pageindex_sections="",
            run_id=None,
        )

        assert isinstance(result, JudgmentAnalysis)
        assert result.facts == [title]
        assert result.reported_claims == []
        assert result.analysis_implications == []
        assert result.uncertainties == []
        assert result.what_not_to_conclude == []

    @pytest.mark.asyncio
    async def test_fallback_contains_title_as_fact(self):
        """The fallback always puts the article title in facts[]."""
        agent, _ = _make_agent()

        title = "Some breaking AI news story"

        full_chain = MagicMock()
        full_chain.ainvoke = AsyncMock(side_effect=ValueError("parse error"))

        agent._prompt = MagicMock()
        agent._llm = MagicMock()
        agent._parser = MagicMock()
        agent._prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=full_chain)
        ))

        result = await agent.analyze(
            article_id="art3",
            title=title,
            source="wired",
            content="content",
            pageindex_sections="sections",
        )

        assert title in result.facts
