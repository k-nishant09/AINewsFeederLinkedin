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
from pydantic import BaseModel

from daily_news.config.settings import get_settings
from daily_news.models.persona import PersonaOutput, PersonaSetOutput, PersonaType
from daily_news.models.summary import NewsSummary
from daily_news.observability.tracing import get_langfuse_callback


class _PersonaOutputRaw(BaseModel):
    """Lenient parse target — accepts any string for persona so the LLM's
    display-name output doesn't fail validation. The real PersonaType is
    injected from agent context after parsing."""
    persona:     str
    perspective: str
    evidence:    list[str]
    article_id:  str

PERSONA_FOCUS: dict[PersonaType, dict] = {
    PersonaType.BUSINESS: {
        "name": "Capitalist Mind",
        "focus": "revenue growth, cost reduction, productivity gains, market disruption, enterprise adoption, ROI, competitive advantage, investment thesis",
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
        "name": "Generalist Mind",
        "focus": "broad societal impact, everyday technology use, learning opportunities, career entry, digital culture, skill building, entrepreneurial angles, what this means for people outside the tech bubble",
    },
    PersonaType.LINKEDIN: {
        "name": "Tech & Workforce Mind",
        "focus": "technology strategy, engineering trade-offs, build-vs-buy decisions, architectural impact, AND workforce implications — job security, automation threats, worker reskilling, career transitions, what practitioners and knowledge workers should do next",
    },
}

BASE_SYSTEM = """You are generating a clearly labeled perspective on a news story for a LinkedIn audience.

Rules:
- Do not invent facts. Use only the supplied evidence.
- Separate factual claims from interpretation.
- Do not claim your perspective is the objective truth.
- Write exactly 1 complete sentence. The sentence must end with a full stop.
- The sentence must be under 200 characters and self-contained — a reader who has not seen the article must fully understand it without trailing off.
- Do NOT cut off mid-sentence. If the idea is too long, simplify it — never truncate.
- Write it to spark LinkedIn engagement: specific, opinionated, and worth sharing.
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
        self._parser = PydanticOutputParser(pydantic_object=_PersonaOutputRaw)

        # Map persona enum values to prompt file names
        _PROMPT_FILE_MAP = {
            PersonaType.BUSINESS: "capitalist",
            PersonaType.POLICY:   "policy",
            PersonaType.GENZ:     "genz",
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
        raw: _PersonaOutputRaw = await chain.ainvoke(
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
    """Runs all four persona agents and returns a PersonaSetOutput."""

    def __init__(self) -> None:
        self._agents = {p: PersonaAgent(p) for p in PersonaType}

    async def generate_all(
        self,
        summary: NewsSummary,
        evidence_sections: str,
        run_id: str | None = None,
        personas: list[PersonaType] | None = None,
    ) -> PersonaSetOutput:
        """
        Generate perspectives for the given personas in parallel.

        personas — subset to run (from jev_route_personas). Defaults to all four.
        Any persona not in the active subset receives a stub output so that
        PersonaSetOutput (which requires all four fields) can always be constructed.
        """
        active = set(personas) if personas else set(PersonaType)

        # Run only the active personas in parallel
        outputs = await asyncio.gather(
            *[
                self._agents[p].generate(summary, evidence_sections, run_id=run_id)
                for p in PersonaType
                if p in active
            ]
        )
        mapping = {o.persona: o for o in outputs}

        # Fill any skipped persona with a stub so the model is always complete
        for p in PersonaType:
            if p not in mapping:
                mapping[p] = _stub_persona(p, summary.article_id)

        return PersonaSetOutput(
            article_id=summary.article_id,
            business=mapping[PersonaType.BUSINESS],
            policy=mapping[PersonaType.POLICY],
            genz=mapping[PersonaType.GENZ],
            linkedin=mapping[PersonaType.LINKEDIN],
        )


def _stub_persona(persona: PersonaType, article_id: str) -> "PersonaOutput":
    """
    Returns an empty PersonaOutput for a persona that was skipped by Jev routing.
    The publisher agent checks for empty perspective strings and omits them.
    """
    return PersonaOutput(
        persona=persona,
        article_id=article_id,
        perspective="",   # publisher treats empty string as skipped
        evidence=[],
    )
