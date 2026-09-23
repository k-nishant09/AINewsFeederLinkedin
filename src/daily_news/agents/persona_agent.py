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
        "name": "Capitalist Mind",
        "focus": "revenue growth, cost reduction, productivity gains, market disruption, enterprise adoption, ROI, competitive advantage, investment thesis",
    },
    PersonaType.LABOR: {
        "name": "Working Professional Mind",
        "focus": "job security, employment impact, automation threats, worker reskilling, wage effects, career transitions, union implications, income distribution",
    },
    PersonaType.POLICY: {
        "name": "Government Mind",
        "focus": "regulation, AI safety, data privacy, public policy, national competitiveness, governance frameworks, ethical standards, cross-border implications",
        "guardrail": (
            "Describe documented policy positions and factual consequences only. "
            "Do not advocate for any political outcome or party."
        ),
    },
    PersonaType.GENZ: {
        "name": "Young / Fresher Mind",
        "focus": "career entry, learning opportunities, everyday technology impact, digital culture, skill building, entrepreneurial angles, generational opportunity",
    },
    PersonaType.LINKEDIN: {
        "name": "Tech Strategist Mind",
        "focus": "technology strategy, product innovation, platform implications, engineering trade-offs, startup opportunities, build-vs-buy decisions, architectural impact, what practitioners should do next",
    },
}

BASE_SYSTEM = """You are generating a clearly labeled perspective on a news story for a LinkedIn audience.

Rules:
- Do not invent facts. Use only the supplied evidence.
- Separate factual claims from interpretation.
- Do not claim your perspective is the objective truth.
- Write 3 complete sentences. Every sentence must end with a full stop.
- Do NOT cut off mid-sentence. If space is tight, write fewer sentences rather than an incomplete one.
- Be specific and actionable — avoid generic platitudes.
- Use plain English. No jargon overload.

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

        # Map persona enum values to prompt file names
        _PROMPT_FILE_MAP = {
            PersonaType.BUSINESS: "capitalist",
            PersonaType.LABOR: "labor",
            PersonaType.POLICY: "policy",
            PersonaType.GENZ: "genz",
            PersonaType.LINKEDIN: "linkedin",
        }

        # Load the humanized persona prompt from the prompts directory
        prompt_file = _PROMPT_FILE_MAP.get(persona, persona.value)
        persona_prompt = _load_prompt(prompt_file)
        if not persona_prompt:
            # Fallback to BASE_SYSTEM if prompt file not found
            persona_prompt = BASE_SYSTEM.format(
                persona_name=self._meta["name"],
                focus=self._meta["focus"],
                guardrail=self._meta.get("guardrail", ""),
            )

        self._prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    persona_prompt,
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
        raw: PersonaOutput = await chain.ainvoke(
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
        # Always override persona + article_id from the known agent context —
        # the LLM sometimes mis-fills the enum value (e.g. display name vs key).
        return PersonaOutput(
            persona=self._persona,
            article_id=summary.article_id,
            perspective=raw.perspective,
            evidence=raw.evidence,
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
