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

BASE_SYSTEM = """You are speaking live in a conversational round-table news discussion.
Your role is to respond directly to the host and challenge other viewpoints with your distinct real-world lens.

Rules:
- Speak naturally like a real human in a podcast or roundtable debate, NOT like an essay or corporate summary.
- Ground your point in the verified facts & evidence provided.
- Do not invent facts or attribute fake quotes.
- Be punchy, direct, and conversational (2-4 crisp sentences).
- Avoid essay transitions or stilted phrasing. Speak with conviction and lived experience.

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
                    "{avoid_phrases_block}"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "STORY CONTEXT (use this as your analytical foundation)\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "  hook: {story_hook}\n"
                    "  what_actually_happened: {story_what_happened}\n"
                    "  what_changed: {story_what_changed}\n"
                    "  why_now: {story_why_now}\n"
                    "  perspective: {story_perspective}\n"
                    "  second_order_effect: {story_second_order}\n"
                    "  human_analogy: {story_analogy}\n"
                    "  why_reader_should_care: {story_why_care}\n"
                    "  future_question: {story_future_question}\n"
                    "  business_consequence: {story_business}\n"
                    "  technology_consequence: {story_technology}\n"
                    "  human_consequence: {story_human}\n"
                    "  narrative_style: {story_narrative_style}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "INTELLIGENCE SIGNALS\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "  sentiment: {sentiment}  |  ai_tag: {ai_tag}\n"
                    "  novelty: {novelty:.2f}  |  trend_velocity: {trend_velocity:.2f}\n"
                    "  emotion — curiosity: {emotion_curiosity:.2f}  excitement: {emotion_excitement:.2f}"
                    "  concern: {emotion_concern:.2f}  urgency: {emotion_urgency:.2f}\n"
                    "  impact  — enterprise: {impact_enterprise:.2f}  developers: {impact_developers:.2f}"
                    "  business: {impact_business:.2f}  policy: {impact_policy:.2f}\n"
                    "  content angle — {missing_angle}\n"
                    "  recommended audience — {recommended_audience}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "JUDGMENT & BOUNDARIES\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "  verified_facts: {verified_facts}\n"
                    "  reported_claims: {reported_claims}\n"
                    "  uncertainties: {uncertainties}\n"
                    "  what_not_to_conclude: {what_not_to_conclude}\n\n"
                    "{format_instructions}",
                ),
            ]
        )

    async def generate(
        self,
        summary: NewsSummary,
        evidence_sections: str,
        run_id: str | None = None,
        avoid_phrases: list[str] | None = None,
    ) -> PersonaOutput:
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

        # Pull intelligence signals from the backbone object when available
        intel = summary.intelligence
        emotion = {}
        impact  = {}
        co      = {}
        novelty        = 0.0
        trend_velocity = 0.0
        if intel:
            _get = (lambda k, d=0.0: intel.get(k, d)) if isinstance(intel, dict) else (lambda k, d=0.0: getattr(intel, k, d))
            _sub = (lambda k, sk, d=0.0: (intel.get(k) or {}).get(sk, d)) if isinstance(intel, dict) \
                   else (lambda k, sk, d=0.0: getattr(getattr(intel, k, None) or type("_", (), {})(), sk, d))
            novelty        = float(_get("novelty"))
            trend_velocity = float(_get("trend_velocity"))
            emotion = {
                "curiosity":  float(_sub("emotion", "curiosity")),
                "excitement": float(_sub("emotion", "excitement")),
                "concern":    float(_sub("emotion", "concern")),
                "urgency":    float(_sub("emotion", "urgency")),
            }
            impact = {
                "enterprise": float(_sub("impact", "enterprise")),
                "developers": float(_sub("impact", "developers")),
                "business":   float(_sub("impact", "business")),
                "policy":     float(_sub("impact", "policy")),
            }
            co = {
                "missing_angle":        _sub("content_opportunity", "missing_angle", "not available"),
                "recommended_audience": _sub("content_opportunity", "recommended_audience", "not available"),
            }

        # Pull story fields — dict-safe (LangGraph serialises objects to dicts)
        st = summary.story or {}
        def _sg(k: str) -> str:
            if isinstance(st, dict):
                return str(st.get(k) or "not available")
            return str(getattr(st, k, None) or "not available")

        # Build avoid_phrases block.
        # On retry (avoid_phrases passed in): show the exact rejected phrases from the last cycle.
        # On first pass: show a compact static reminder of the top offenders so the model
        # starts clean without needing a failure to learn from.
        if avoid_phrases:
            avoid_lines = "\n".join(f"  ✗ {p}" for p in avoid_phrases)
            avoid_block = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⛔ RETRY — PREVIOUS ATTEMPT REJECTED\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "These EXACT phrases caused rejection — do NOT use them or any variant:\n"
                f"{avoid_lines}\n\n"
                "Open with a SPECIFIC, article-grounded claim. No abstract framing.\n\n"
            )
        else:
            avoid_block = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⛔ MOST COMMON REJECTION TRIGGERS\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  ✗ 'the real challenge'  ✗ 'challenge lies in'  ✗ 'at the end of the day'\n"
                "  ✗ 'sounds great'  ✗ 'sounds promising'  ✗ 'the real question'\n"
                "  ✗ 'the real issue is'  ✗ 'the real problem is'  ✗ 'the real business metric'\n"
                "  ✗ 'it remains to be seen'  ✗ 'only time will tell'  ✗ 'what remains to be seen'\n\n"
                "Open with a SPECIFIC fact, number, or implication directly from this article.\n\n"
            )

        chain = self._prompt | self._llm | self._parser
        raw: _PersonaOutputRaw = await chain.ainvoke(
            {
                "article_id":              summary.article_id,
                "avoid_phrases_block":     avoid_block,
                "headline":                summary.headline,
                "summary":                 summary.summary,
                "key_points":              "\n".join(f"- {p}" for p in summary.key_points),
                "business_impact":         summary.business_impact,
                "evidence_sections":       evidence_sections,
                # Story context — the analytical foundation for persona perspectives
                "story_hook":              _sg("hook"),
                "story_what_happened":     _sg("what_actually_happened"),
                "story_what_changed":      _sg("what_changed"),
                "story_why_now":           _sg("why_now"),
                "story_perspective":       _sg("perspective"),
                "story_second_order":      _sg("second_order_effect"),
                "story_analogy":           _sg("human_analogy"),
                "story_why_care":          _sg("why_reader_should_care"),
                "story_future_question":   _sg("future_question"),
                "story_business":          _sg("business_consequence"),
                "story_technology":        _sg("technology_consequence"),
                "story_human":             _sg("human_consequence"),
                "story_narrative_style":   _sg("narrative_style"),
                # Intelligence signals
                "sentiment":               summary.sentiment or "not available",
                "ai_tag":                  summary.ai_tag or "not available",
                "novelty":                 novelty,
                "trend_velocity":          trend_velocity,
                "emotion_curiosity":       float(emotion.get("curiosity",  0.0)),
                "emotion_excitement":      float(emotion.get("excitement", 0.0)),
                "emotion_concern":         float(emotion.get("concern",    0.0)),
                "emotion_urgency":         float(emotion.get("urgency",    0.0)),
                "impact_enterprise":       float(impact.get("enterprise",  0.0)),
                "impact_developers":       float(impact.get("developers",  0.0)),
                "impact_business":         float(impact.get("business",    0.0)),
                "impact_policy":           float(impact.get("policy",      0.0)),
                "missing_angle":           co.get("missing_angle",        "not available"),
                "recommended_audience":    co.get("recommended_audience", "not available"),
                "verified_facts":          getattr(getattr(summary, "intelligence", None), "judgment", None).facts if getattr(summary, "intelligence", None) and getattr(summary.intelligence, "judgment", None) else [],
                "reported_claims":         getattr(getattr(summary, "intelligence", None), "judgment", None).reported_claims if getattr(summary, "intelligence", None) and getattr(summary.intelligence, "judgment", None) else [],
                "uncertainties":           getattr(getattr(summary, "intelligence", None), "judgment", None).uncertainties if getattr(summary, "intelligence", None) and getattr(summary.intelligence, "judgment", None) else [],
                "what_not_to_conclude":    getattr(getattr(summary, "intelligence", None), "judgment", None).what_not_to_conclude if getattr(summary, "intelligence", None) and getattr(summary.intelligence, "judgment", None) else [],
                "format_instructions":     self._parser.get_format_instructions(),
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
        avoid_phrases: list[str] | None = None,
    ) -> PersonaSetOutput:
        """
        Generate perspectives for the given personas in parallel.

        personas      — subset to run (from jev_route_personas). Defaults to all four.
        avoid_phrases — banned phrases from the previous evaluation cycle, injected
                        into the prompt so the LLM knows exactly what to avoid on retry.
        Any persona not in the active subset receives a stub output so that
        PersonaSetOutput (which requires all four fields) can always be constructed.
        """
        active = set(personas) if personas else set(PersonaType)

        # Run only the active personas in parallel
        outputs = await asyncio.gather(
            *[
                self._agents[p].generate(
                    summary, evidence_sections,
                    run_id=run_id, avoid_phrases=avoid_phrases,
                )
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
