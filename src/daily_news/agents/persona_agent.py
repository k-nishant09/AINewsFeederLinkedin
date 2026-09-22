"""
Persona Agent Factory — single engine, five controlled personas.

Langfuse tracing
─────────────────
Each persona LLM call is traced independently with:
  - session_id = run_id  (links all persona traces to the same workflow run)
  - tags       = [persona_name, "persona", app_env]
  - metadata   = article_id, persona, model

The five calls run in parallel via asyncio.gather — all traces land in the
same Langfuse session so you can compare persona outputs side-by-side.

Ref: https://langfuse.com/docs/integrations/langchain/tracing
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from daily_news.config.settings import get_settings
from daily_news.models.persona import PersonaOutput, PersonaSetOutput, PersonaType
from daily_news.models.summary import NewsSummary
from daily_news.observability.tracing import get_langfuse_callback

PERSONA_FOCUS: dict[PersonaType, dict] = {
    PersonaType.BUSINESS: {
        "name": "Business Perspective",
        "focus": "revenue, cost reduction, productivity, market disruption, enterprise adoption, investment",
    },
    PersonaType.LABOR: {
        "name": "Labor Perspective",
        "focus": "jobs, employment, automation impact, worker skills, wages, reskilling, income distribution",
    },
    PersonaType.POLICY: {
        "name": "Government and Policy Perspective",
        "focus": "regulation, AI safety, privacy, public policy, national competitiveness, governance frameworks",
        "guardrail": (
            "Describe documented policy positions and factual consequences only. "
            "Do not advocate for any political outcome or party."
        ),
    },
    PersonaType.GENZ: {
        "name": "Gen Z Perspective",
        "focus": "career prospects, everyday technology impact, education, digital culture, skill building",
    },
    PersonaType.LINKEDIN: {
        "name": "Professional LinkedIn Perspective",
        "focus": "enterprise implications, leadership takeaways, technology strategy, career development, practical next steps",
    },
}

BASE_SYSTEM = """You are generating a clearly labeled perspective on a news story.

Do not invent facts.
Use only the supplied evidence.
Separate factual claims from interpretation.
Do not claim that your perspective is the objective truth.
Keep the response to 2-3 sentences.

Persona: {persona_name}
Focus areas: {focus}
{guardrail}
"""


def _load_prompt(name: str) -> str:
    # Prompts are at /app/prompts/ (repo root) — 4 levels up from this file
    p = Path(__file__).parent.parent.parent.parent / "prompts" / f"{name}.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


class PersonaAgent:
    """LangChain agent that generates a single persona perspective."""

    def __init__(self, persona: PersonaType) -> None:
        s = get_settings()
        self._persona = persona
        self._settings = s
        self._meta = PERSONA_FOCUS[persona]
        self._llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.4,
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )
        self._parser = PydanticOutputParser(pydantic_object=PersonaOutput)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    BASE_SYSTEM.format(
                        persona_name=self._meta["name"],
                        focus=self._meta["focus"],
                        guardrail=self._meta.get("guardrail", ""),
                    ),
                ),
                (
                    "human",
                    "Article ID: {article_id}\n\n"
                    "Headline: {headline}\n\n"
                    "Summary: {summary}\n\n"
                    "Key Points:\n{key_points}\n\n"
                    "Business Impact: {business_impact}\n\n"
                    "Relevant Evidence:\n{evidence_sections}\n\n"
                    "{format_instructions}",
                ),
            ]
        )

    async def generate(
        self,
        summary: NewsSummary,
        evidence_sections: str,
        run_id: str | None = None,
    ) -> PersonaOutput:
        # Langfuse v4 CallbackHandler — each persona gets its own trace
        # linked to the workflow run via the shared trace_id seed (run_id).
        # Ref: https://langfuse.com/docs/integrations/langchain/tracing
        handler, _trace_id = get_langfuse_callback(
            run_id=f"{run_id}:{self._persona.value}" if run_id else None,
            tags=[self._persona.value, "persona", self._settings.app_env],
            metadata={
                "article_id": summary.article_id,
                "persona":    self._persona.value,
                "model":      self._settings.llm_model,
                "agent":      "persona_agent",
            },
        )
        callbacks = [handler] if handler else []

        chain = self._prompt | self._llm | self._parser
        return await chain.ainvoke(
            {
                "article_id":          summary.article_id,
                "headline":            summary.headline,
                "summary":             summary.summary,
                "key_points":          "\n".join(f"- {p}" for p in summary.key_points),
                "business_impact":     summary.business_impact,
                "evidence_sections":   evidence_sections,
                "format_instructions": self._parser.get_format_instructions(),
            },
            config={"callbacks": callbacks} if callbacks else {},
        )


class PersonaAgentFactory:
    """Runs all five persona agents and returns a PersonaSetOutput."""

    def __init__(self) -> None:
        self._agents = {p: PersonaAgent(p) for p in PersonaType}

    async def generate_all(
        self,
        summary: NewsSummary,
        evidence_sections: str,
        run_id: str | None = None,
    ) -> PersonaSetOutput:
        # All five personas run in parallel — each gets its own Langfuse trace
        # but they all share session_id=run_id so they appear together in the UI
        outputs = await asyncio.gather(
            *[
                self._agents[p].generate(summary, evidence_sections, run_id=run_id)
                for p in PersonaType
            ]
        )
        mapping = {o.persona: o for o in outputs}
        return PersonaSetOutput(
            article_id=summary.article_id,
            business=mapping[PersonaType.BUSINESS],
            labor=mapping[PersonaType.LABOR],
            policy=mapping[PersonaType.POLICY],
            genz=mapping[PersonaType.GENZ],
            linkedin=mapping[PersonaType.LINKEDIN],
        )
