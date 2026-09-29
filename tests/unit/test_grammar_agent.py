"""
Unit tests for GrammarAgent.

GrammarAgent.correct() is an async LLM pass that:
  - Returns the corrected text on success
  - Returns the original text unchanged on empty LLM output
  - Returns the original text unchanged on any exception
  - Returns the input unchanged if the input is empty / whitespace-only
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent():
    """Construct a GrammarAgent with the LLM fully mocked out."""
    with patch("daily_news.agents.grammar_agent.make_llm") as mock_make_llm, \
         patch("daily_news.agents.grammar_agent._load_prompt", return_value="system prompt"):
        mock_llm = MagicMock()
        mock_make_llm.return_value = mock_llm
        from daily_news.agents.grammar_agent import GrammarAgent
        agent = GrammarAgent()
        return agent, mock_llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGrammarAgentCorrect:
    """GrammarAgent.correct()"""

    @pytest.mark.asyncio
    async def test_returns_corrected_text_on_success(self):
        """Normal path: LLM returns corrected text."""
        agent, mock_llm = _make_agent()

        original = "this is a sentance with erors."
        corrected = "This is a sentence with errors."

        mock_result = MagicMock()
        mock_result.content = corrected

        # Mock the prompt | llm chain
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=mock_result)

        with patch.object(agent, "_prompt") as mock_prompt:
            mock_prompt.__or__ = MagicMock(return_value=mock_chain)

            result = await agent.correct(original, run_id="run1", article_id="art1")

        assert result == corrected

    @pytest.mark.asyncio
    async def test_returns_original_on_empty_llm_output(self):
        """If LLM returns empty string, original text is returned unchanged."""
        agent, _ = _make_agent()

        original = "Some text that needs checking."

        mock_result = MagicMock()
        mock_result.content = ""   # empty output

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=mock_result)

        with patch.object(agent, "_prompt") as mock_prompt:
            mock_prompt.__or__ = MagicMock(return_value=mock_chain)

            result = await agent.correct(original)

        assert result == original

    @pytest.mark.asyncio
    async def test_returns_original_on_exception(self):
        """If the LLM call raises, the original text is returned unchanged."""
        agent, _ = _make_agent()

        original = "Post text that will fail correction."

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(side_effect=RuntimeError("network error"))

        with patch.object(agent, "_prompt") as mock_prompt:
            mock_prompt.__or__ = MagicMock(return_value=mock_chain)

            result = await agent.correct(original, run_id="run1")

        assert result == original

    @pytest.mark.asyncio
    async def test_returns_empty_string_unchanged(self):
        """Empty string input is returned immediately without calling the LLM."""
        agent, mock_llm = _make_agent()

        result = await agent.correct("")

        # No LLM call should have been made
        mock_llm.ainvoke.assert_not_called()
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_whitespace_only_unchanged(self):
        """Whitespace-only input is returned without calling the LLM."""
        agent, mock_llm = _make_agent()

        result = await agent.correct("   \n\t  ")

        mock_llm.ainvoke.assert_not_called()
        assert result == "   \n\t  "

    @pytest.mark.asyncio
    async def test_whitespace_stripped_from_corrected_output(self):
        """content.strip() is applied to the LLM output."""
        agent, _ = _make_agent()

        original = "some text"
        corrected_with_whitespace = "  Some text.  \n"

        mock_result = MagicMock()
        mock_result.content = corrected_with_whitespace

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=mock_result)

        with patch.object(agent, "_prompt") as mock_prompt:
            mock_prompt.__or__ = MagicMock(return_value=mock_chain)

            result = await agent.correct(original)

        assert result == "Some text."

    @pytest.mark.asyncio
    async def test_none_run_id_does_not_raise(self):
        """run_id=None and article_id=None are valid — no exception."""
        agent, _ = _make_agent()

        mock_result = MagicMock()
        mock_result.content = "Corrected."

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=mock_result)

        with patch.object(agent, "_prompt") as mock_prompt:
            mock_prompt.__or__ = MagicMock(return_value=mock_chain)

            result = await agent.correct("text", run_id=None, article_id=None)

        assert result == "Corrected."
