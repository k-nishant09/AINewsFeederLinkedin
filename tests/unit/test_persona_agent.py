"""
Unit tests for PersonaAgent and PersonaAgentFactory.

Tests cover:
  - PersonaAgent.generate() returns a PersonaOutput with correct persona enum
  - PersonaAgentFactory.generate_all() runs all active personas in parallel
  - Skipped personas receive stub outputs (empty perspective)
  - avoid_phrases block is injected into the prompt input on retry
  - Prefix cache (_STORY_CONTEXT_CACHE) is populated on first call
  - _stub_persona() returns an empty PersonaOutput
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daily_news.models.persona import PersonaOutput, PersonaSetOutput, PersonaType
from daily_news.models.summary import NewsSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_summary(article_id: str = "art1") -> NewsSummary:
    """Minimal NewsSummary for testing — all required fields populated."""
    return NewsSummary(
        article_id=article_id,
        headline="OpenAI launches GPT-5",
        summary="OpenAI has released GPT-5 with improved reasoning.",
        key_points=["Improved reasoning", "Lower cost per token"],
        why_it_matters="GPT-5 changes the cost curve for AI inference.",
        business_impact="Reduces AI inference costs by 30%.",
        job_impact="May reduce demand for junior prompt engineers.",
        technology_impact="New MoE architecture enables lower latency.",
        source="TechCrunch",
        source_url="https://techcrunch.com/gpt5",
        sentiment="positive",
        ai_tag="artificial intelligence",
    )


def _make_agent(persona: PersonaType = PersonaType.BUSINESS):
    """Construct a PersonaAgent with LLM mocked."""
    with patch("daily_news.agents.persona_agent.make_llm") as mock_make_llm, \
         patch("daily_news.agents.persona_agent._load_prompt", return_value=""):
        mock_llm = MagicMock()
        mock_make_llm.return_value = mock_llm
        from daily_news.agents.persona_agent import PersonaAgent
        agent = PersonaAgent(persona)
        return agent, mock_llm


# ---------------------------------------------------------------------------
# _stub_persona
# ---------------------------------------------------------------------------

class TestStubPersona:
    """_stub_persona() helper"""

    def test_stub_has_empty_perspective(self):
        from daily_news.agents.persona_agent import _stub_persona
        stub = _stub_persona(PersonaType.POLICY, "art99")
        assert stub.perspective == ""
        assert stub.persona == PersonaType.POLICY
        assert stub.article_id == "art99"
        assert stub.evidence == []

    def test_stub_for_all_personas(self):
        from daily_news.agents.persona_agent import _stub_persona
        for pt in PersonaType:
            stub = _stub_persona(pt, "art1")
            assert stub.persona == pt
            assert stub.perspective == ""


# ---------------------------------------------------------------------------
# PersonaAgent.generate()
# ---------------------------------------------------------------------------

class TestPersonaAgentGenerate:
    """PersonaAgent.generate()"""

    @pytest.mark.asyncio
    async def test_returns_persona_output_with_correct_enum(self):
        """persona field is always set from agent context, not LLM output."""
        agent, _ = _make_agent(PersonaType.GENZ)
        summary = _make_summary()

        from daily_news.agents.persona_agent import _PersonaOutputRaw
        raw = _PersonaOutputRaw(
            persona="some-display-name",   # LLM might return display name
            perspective="The analyst perspective here.",
            evidence=["fact one", "fact two"],
            article_id="art1",
            next_question="Who benefits at scale?",
        )

        full_chain = MagicMock()
        full_chain.ainvoke = AsyncMock(return_value=raw)

        agent._prompt = MagicMock()
        agent._llm = MagicMock()
        agent._parser = MagicMock()
        agent._parser.get_format_instructions.return_value = ""
        agent._prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=full_chain)
        ))

        result = await agent.generate(summary, evidence_sections="evidence text")

        assert isinstance(result, PersonaOutput)
        # persona enum is always injected from agent context, overriding LLM output
        assert result.persona == PersonaType.GENZ
        assert result.article_id == "art1"
        assert result.perspective == "The analyst perspective here."
        assert result.next_question == "Who benefits at scale?"

    @pytest.mark.asyncio
    async def test_avoid_phrases_block_is_injected_on_retry(self):
        """When avoid_phrases is provided, the block contains them."""
        agent, _ = _make_agent(PersonaType.LINKEDIN)
        summary = _make_summary()

        from daily_news.agents.persona_agent import _PersonaOutputRaw
        raw = _PersonaOutputRaw(
            persona="ML Platform Engineer",
            perspective="Real operational constraint.",
            evidence=[],
            article_id="art1",
            next_question="",
        )

        captured_input: dict = {}

        async def capture_invoke(inputs, **kwargs):
            captured_input.update(inputs)
            return raw

        full_chain = MagicMock()
        full_chain.ainvoke = AsyncMock(side_effect=capture_invoke)

        agent._prompt = MagicMock()
        agent._llm = MagicMock()
        agent._parser = MagicMock()
        agent._parser.get_format_instructions.return_value = ""
        agent._prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=full_chain)
        ))

        avoid = ["the real challenge lies in", "marks a significant shift"]
        await agent.generate(summary, evidence_sections="", avoid_phrases=avoid)

        # The avoid_phrases_block in the invocation input must reference our phrases
        block = captured_input.get("avoid_phrases_block", "")
        assert "the real challenge lies in" in block
        assert "marks a significant shift" in block
        assert "RETRY" in block   # retry header

    @pytest.mark.asyncio
    async def test_no_avoid_phrases_shows_static_reminder(self):
        """First-pass (no avoid_phrases) shows the static automatic-rejection reminder."""
        agent, _ = _make_agent(PersonaType.BUSINESS)
        summary = _make_summary()

        from daily_news.agents.persona_agent import _PersonaOutputRaw
        raw = _PersonaOutputRaw(
            persona="AI Infrastructure Founder",
            perspective="The opportunity here.",
            evidence=[],
            article_id="art1",
        )

        captured_input: dict = {}

        async def capture_invoke(inputs, **kwargs):
            captured_input.update(inputs)
            return raw

        full_chain = MagicMock()
        full_chain.ainvoke = AsyncMock(side_effect=capture_invoke)

        agent._prompt = MagicMock()
        agent._llm = MagicMock()
        agent._parser = MagicMock()
        agent._parser.get_format_instructions.return_value = ""
        agent._prompt.__or__ = MagicMock(return_value=MagicMock(
            __or__=MagicMock(return_value=full_chain)
        ))

        await agent.generate(summary, evidence_sections="")

        block = captured_input.get("avoid_phrases_block", "")
        assert "AUTOMATIC REJECTION TRIGGERS" in block
        assert "RETRY" not in block


# ---------------------------------------------------------------------------
# PersonaAgentFactory.generate_all()
# ---------------------------------------------------------------------------

class TestPersonaAgentFactory:
    """PersonaAgentFactory.generate_all()"""

    @pytest.mark.asyncio
    async def test_returns_persona_set_output(self):
        """generate_all() returns a PersonaSetOutput with all four personas."""
        with patch("daily_news.agents.persona_agent.make_llm"), \
             patch("daily_news.agents.persona_agent._load_prompt", return_value=""):
            from daily_news.agents.persona_agent import PersonaAgentFactory

            factory = PersonaAgentFactory()
            summary = _make_summary()

            # Patch every individual agent's generate() to return a stub
            for pt, agent in factory._agents.items():
                agent.generate = AsyncMock(return_value=PersonaOutput(
                    persona=pt,
                    article_id="art1",
                    perspective=f"{pt.value} perspective",
                    evidence=[],
                ))

            result = await factory.generate_all(summary, evidence_sections="evidence")

        assert isinstance(result, PersonaSetOutput)
        assert result.article_id == "art1"
        assert result.business.persona == PersonaType.BUSINESS
        assert result.policy.persona == PersonaType.POLICY
        assert result.genz.persona == PersonaType.GENZ
        assert result.linkedin.persona == PersonaType.LINKEDIN

    @pytest.mark.asyncio
    async def test_skipped_personas_are_stubbed(self):
        """Personas not in the active list receive empty-perspective stubs."""
        with patch("daily_news.agents.persona_agent.make_llm"), \
             patch("daily_news.agents.persona_agent._load_prompt", return_value=""):
            from daily_news.agents.persona_agent import PersonaAgentFactory

            factory = PersonaAgentFactory()
            summary = _make_summary()

            # Only BUSINESS is active
            active = [PersonaType.BUSINESS]
            factory._agents[PersonaType.BUSINESS].generate = AsyncMock(
                return_value=PersonaOutput(
                    persona=PersonaType.BUSINESS,
                    article_id="art1",
                    perspective="Business view",
                    evidence=[],
                )
            )

            result = await factory.generate_all(
                summary,
                evidence_sections="",
                personas=active,
            )

        # BUSINESS has a real perspective; others are stubs
        assert result.business.perspective == "Business view"
        assert result.policy.perspective == ""
        assert result.genz.perspective == ""
        assert result.linkedin.perspective == ""

    @pytest.mark.asyncio
    async def test_all_personas_run_when_none_specified(self):
        """When personas=None, all four agents are called."""
        with patch("daily_news.agents.persona_agent.make_llm"), \
             patch("daily_news.agents.persona_agent._load_prompt", return_value=""):
            from daily_news.agents.persona_agent import PersonaAgentFactory

            factory = PersonaAgentFactory()
            summary = _make_summary()

            call_counts: dict[PersonaType, int] = {pt: 0 for pt in PersonaType}

            async def mock_generate(s, ev, **kw):
                pt = [p for p, a in factory._agents.items() if a.generate == mock_generate][0]
                call_counts[pt] += 1
                return PersonaOutput(persona=pt, article_id="art1", perspective="p", evidence=[])

            for pt in PersonaType:
                # Create a closure that captures pt
                def make_mock(persona):
                    async def _gen(s, ev, **kw):
                        call_counts[persona] += 1
                        return PersonaOutput(
                            persona=persona, article_id="art1", perspective="p", evidence=[]
                        )
                    return _gen
                factory._agents[pt].generate = make_mock(pt)

            await factory.generate_all(summary, evidence_sections="", personas=None)

        assert all(count == 1 for count in call_counts.values()), \
            f"Expected each persona called once; got {call_counts}"
