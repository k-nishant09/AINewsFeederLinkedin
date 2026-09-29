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

Prefix-caching
──────────────
The story-context block (story_hook, what_changed, perspective, etc.) and the
intelligence-signals block are fully deterministic for a given (article_id, run).
They never change between retries — only the LLM output changes.

We cache the serialised context strings keyed by article_id so repeated calls
(e.g. REGENERATE cycles) skip re-serialising the same ~60-field dict and re-format
the same prompt prefix.  The LLM provider's prompt cache also benefits: identical
prefix text sent in consecutive calls is far more likely to land in its KV cache.

Cache scope: process-level dict.  Bounded by articles per run (≤ 5 per day).
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from daily_news.agents.publisher_agent import CANONICAL_BANNED_PHRASES
from daily_news.config.llm_factory import make_llm
from daily_news.config.settings import get_settings
from daily_news.models.persona import PersonaOutput, PersonaSetOutput, PersonaType
from daily_news.models.summary import NewsSummary
from daily_news.observability.tracing import get_langfuse_callback

# ── Story-context prefix cache ────────────────────────────────────────────────
# Key:   article_id (stable for a given article within a pod run)
# Value: dict of pre-serialised story / intelligence field strings
#        These fields are 100% deterministic from the NewsSummary object and never
#        change between retries, so we compute them once and reuse on every retry.
_STORY_CONTEXT_CACHE: dict[str, dict] = {}


def _story_context_cache_key(summary: NewsSummary) -> str:
    """Cache key = article_id (sufficient: article is immutable within a run)."""
    return summary.article_id


class _PersonaOutputRaw(BaseModel):
    """Lenient parse target — accepts any string for persona so the LLM's
    display-name output doesn't fail validation. The real PersonaType is
    injected from agent context after parsing."""
    persona:       str
    perspective:   str
    evidence:      list[str]
    article_id:    str
    # One direct question to the NEXT speaker in the conversation chain.
    # Must end with "?". Empty string is acceptable (comic falls back to heuristic).
    next_question: str = ""

PERSONA_FOCUS: dict[PersonaType, dict] = {
    PersonaType.BUSINESS: {
        "name": "AI Infrastructure Founder",
        "focus": (
            "OPPORTUNITY VOICE. Your incentive: cost reduction, differentiation, and platform dependency. "
            "You see the commercial opportunity first — market timing, competitive position, tool consolidation. "
            "You are GENUINELY OPTIMISTIC. The Engineer (next speaker) will directly contradict you — so make a concrete, arguable commercial claim. "
            "Do not hedge."
        ),
    },
    PersonaType.POLICY: {
        "name": "AI Policy Lead",
        "focus": (
            "GOVERNANCE VOICE. Your incentive: accountability, concentration risk, what happens when things go wrong. "
            "You add the governance layer that the Engineer and Analyst both missed. "
            "When one platform becomes the default workspace, governance becomes part of the product — not a checkbox. "
            "Name the specific accountability gap, not a generic regulation."
        ),
        "guardrail": (
            "Describe documented governance positions and factual accountability consequences only. "
            "Do not advocate for any political outcome or party. "
            "Only cite EU AI Act or GDPR if this article is explicitly about EU regulation."
        ),
    },
    PersonaType.GENZ: {
        "name": "AI Industry Analyst",
        "focus": (
            "MARKET DYNAMICS VOICE. Your incentive: who wins at platform scale, second-order displacement, switching cost. "
            "You CHALLENGE THE FRAMING of both the Founder and the Engineer. They argue about internal implementation. "
            "You ask: who does this benefit at scale? Who becomes the default workspace? What does that mean for everyone else? "
            "Do not both-sides the debate. Shift the frame from 'does this work' to 'who does this benefit at scale'."
        ),
    },
    PersonaType.LINKEDIN: {
        "name": "ML Platform Engineer",
        "focus": (
            "OPERATIONAL REALITY VOICE. Your incentive: architecture, reliability, identity/permissions/observability, operational debt. "
            "You DIRECTLY CONTRADICT the Founder's commercial premise with a specific operational constraint. "
            "Simplification promises move complexity underneath the platform — it does not disappear. "
            "Name the specific system, team, or workflow that actually has to absorb this complexity."
        ),
    },
}

BASE_SYSTEM = """You are speaking live in a conversational round-table news discussion.
Your role is to respond directly to the host and challenge other viewpoints with your distinct real-world lens.

Rules:
- Speak naturally like a real human in a podcast or roundtable debate, NOT like an essay or corporate summary.
- Ground your point in the verified facts & evidence provided.
- Do not invent facts or attribute fake quotes. Frame speculative claims as scenario analysis.
- Be punchy, direct, and conversational (2-4 crisp sentences).
- CONVERSATIONAL INTERLINKING & FLOW:
  * Your perspective MUST directly acknowledge, challenge, or build upon the previous speaker's argument.
  * Your final sentence (or next_question) MUST naturally hand off the discussion to the next speaker.
  * The "next_question" field MUST be explicitly anchored to a topic, constraint, or claim you just raised in your perspective text (share clear keywords). Do NOT invent an unrelated topic for next_question.

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
        self._llm = make_llm(temperature=0.4, settings=s)
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

        # ── Prefix-cached story/intelligence context ──────────────────────────
        # These fields are deterministic for a given article_id and never change
        # between retries. Compute once, reuse on every subsequent call.
        _cache_key = _story_context_cache_key(summary)
        _ctx = _STORY_CONTEXT_CACHE.get(_cache_key)

        if _ctx is None:
            # First call for this article — compute and cache
            intel = summary.intelligence
            emotion: dict = {}
            impact:  dict = {}
            co:      dict = {}
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

            # Judgment fields (safe accessor — may be Pydantic or dict or None)
            _intel_obj  = getattr(summary, "intelligence", None)
            _j_obj      = getattr(_intel_obj, "judgment", None) if _intel_obj and not isinstance(_intel_obj, dict) else (_intel_obj or {}).get("judgment") if isinstance(_intel_obj, dict) else None
            _jget       = (lambda f: _j_obj.get(f, []) if isinstance(_j_obj, dict) else getattr(_j_obj, f, [])) if _j_obj else (lambda f: [])

            _ctx = {
                "headline":              summary.headline,
                "summary":               summary.summary,
                "key_points":            "\n".join(f"- {p}" for p in summary.key_points),
                "business_impact":       summary.business_impact,
                "story_hook":            _sg("hook"),
                "story_what_happened":   _sg("what_actually_happened"),
                "story_what_changed":    _sg("what_changed"),
                "story_why_now":         _sg("why_now"),
                "story_perspective":     _sg("perspective"),
                "story_second_order":    _sg("second_order_effect"),
                "story_analogy":         _sg("human_analogy"),
                "story_why_care":        _sg("why_reader_should_care"),
                "story_future_question": _sg("future_question"),
                "story_business":        _sg("business_consequence"),
                "story_technology":      _sg("technology_consequence"),
                "story_human":           _sg("human_consequence"),
                "story_narrative_style": _sg("narrative_style"),
                "sentiment":             summary.sentiment or "not available",
                "ai_tag":                summary.ai_tag or "not available",
                "novelty":               novelty,
                "trend_velocity":        trend_velocity,
                "emotion_curiosity":     float(emotion.get("curiosity",  0.0)),
                "emotion_excitement":    float(emotion.get("excitement", 0.0)),
                "emotion_concern":       float(emotion.get("concern",    0.0)),
                "emotion_urgency":       float(emotion.get("urgency",    0.0)),
                "impact_enterprise":     float(impact.get("enterprise",  0.0)),
                "impact_developers":     float(impact.get("developers",  0.0)),
                "impact_business":       float(impact.get("business",    0.0)),
                "impact_policy":         float(impact.get("policy",      0.0)),
                "missing_angle":         co.get("missing_angle",        "not available"),
                "recommended_audience":  co.get("recommended_audience", "not available"),
                "verified_facts":        _jget("facts"),
                "reported_claims":       _jget("reported_claims"),
                "uncertainties":         _jget("uncertainties"),
                "what_not_to_conclude":  _jget("what_not_to_conclude"),
            }
            _STORY_CONTEXT_CACHE[_cache_key] = _ctx

        # Build avoid_phrases block.
        # On retry (avoid_phrases passed in): show the exact rejected phrases from the last cycle.
        # On first pass: show a compact static reminder of the top offenders so the model
        # starts clean without needing a failure to learn from.
        if avoid_phrases:
            # Last-cycle offenders first (most specific signal).
            cycle_lines = "\n".join(f"  ✗ {p}" for p in avoid_phrases)
            # Full canonical list — every phrase the scanner will reject.
            full_lines = "\n".join(
                f"  ✗ {p}" for p in CANONICAL_BANNED_PHRASES
            )
            avoid_block = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⛔ RETRY — PREVIOUS ATTEMPT REJECTED\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "These exact phrases triggered AUTOMATIC REJECTION last attempt:\n"
                f"{cycle_lines}\n\n"
                "FULL BANNED LIST — every phrase below causes instant rejection:\n"
                f"{full_lines}\n\n"
                "STRICT RULE: Do NOT write any sentence that begins or contains:\n"
                "  - 'the real <any word> is/lies/isn't/becomes' — ALL forms are rejected\n"
                "  - 'marks a significant' — ALL forms are rejected\n"
                "  - 'the first major issue/challenge/concern will be' — ALL forms are rejected\n\n"
                "Instead: open with the SPECIFIC company name, system, number, or market position "
                "from THIS article. No abstract framing.\n\n"
            )
        else:
            avoid_block = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⛔ AUTOMATIC REJECTION TRIGGERS\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "A scanner rejects any response containing these patterns — ALL forms:\n\n"
                "  ✗ 'the real <X> is/lies/isn't' — includes: 'the real challenge lies in',\n"
                "    'the real question is', 'the real test is', 'the real issue is',\n"
                "    'the real risk lies', 'the real concern here is' — ALL rejected.\n"
                "  ✗ 'marks a significant' — includes 'marks a significant shift',\n"
                "    'marks a significant step', 'marks a significant change' — ALL rejected.\n"
                "  ✗ 'the first major issue/challenge will be' — ALL rejected.\n"
                "  ✗ 'in the end' · 'at the end of the day' · 'only time will tell'\n"
                "  ✗ 'sounds promising' · 'sounds great' · 'it remains to be seen'\n\n"
                "Every sentence must name a SPECIFIC company, system, number, or outcome "
                "from THIS article. No abstract framing. No hedged generalisations.\n\n"
            )

        chain = self._prompt | self._llm | self._parser

        # Merge prefix-cached context with per-call fields (article_id, avoid_phrases,
        # evidence_sections, format_instructions all vary by persona/retry/call).
        invoke_input = {
            **_ctx,   # cached story/intelligence fields — identical across all retries
            "article_id":          summary.article_id,
            "avoid_phrases_block": avoid_block,
            "evidence_sections":   evidence_sections,
            "format_instructions": self._parser.get_format_instructions(),
        }
        raw: _PersonaOutputRaw = await chain.ainvoke(
            invoke_input,
            config={"callbacks": callbacks} if callbacks else {},
        )
        # Always override persona + article_id from the known agent context —
        # the LLM sometimes mis-fills the enum value (e.g. display name vs key).
        return PersonaOutput(
            persona=self._persona,
            article_id=summary.article_id,
            perspective=raw.perspective,
            evidence=raw.evidence,
            next_question=raw.next_question or "",
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


def _stub_persona(persona: PersonaType, article_id: str) -> PersonaOutput:
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
