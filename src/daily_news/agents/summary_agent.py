"""
Summary Agent — two-pass pipeline:

  Pass 1 — MediaStorytellerAgent
    Reads the raw article + PageIndex evidence and extracts the NewsStory:
    hook, human_analogy, perspective, second_order_effect, future_question,
    narrative_style, context, turning_point, why_reader_should_care, etc.
    This is the analyst pass — not writing, just understanding the story.

  Pass 2 — SummaryAgent
    Produces the structured NewsSummary (headline, key_points, impacts, etc.)
    with the full story object available for calibration.

Both passes are traced independently in Langfuse.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from daily_news.config.settings import get_settings
from daily_news.models.intelligence import (
    AudienceImpact,
    ContentOpportunity,
    EmotionSignals,
    NewsIntelligence,
    NewsStory,
)
from daily_news.models.summary import NewsSummary
from daily_news.observability.tracing import get_langfuse_callback

log = logging.getLogger(__name__)


def _load_prompt(name: str) -> str:
    from pathlib import Path
    p = Path(__file__).parent.parent.parent.parent / "prompts" / f"{name}.txt"
    return p.read_text(encoding="utf-8")


# ── NewsStory parse target ─────────────────────────────────────────────────────

class _NewsStoryRaw(BaseModel):
    """Lenient parse target for the MediaStorytellerAgent LLM output."""
    what_actually_happened:   str = ""
    who:                      list[str] = []
    what_changed:             str = ""
    why_now:                  str = ""
    what_came_before:         str = ""
    what_problem_it_solves:   str = ""
    hook:                     str = ""
    human_analogy:            str = ""
    turning_point:            str = ""
    why_reader_should_care:   str = ""
    perspective:              str = ""
    second_order_effect:      str = ""
    future_question:          str = ""
    media_host_opening:       str = ""
    media_host_setup:         str = ""
    media_transitions:        dict[str, str] = {}
    media_host_synthesis:     str = ""
    media_host_audience_cta:  str = ""
    dynamic_seo_hashtags:     list[str] = []
    business_consequence:     str = ""
    technology_consequence:   str = ""
    human_consequence:        str = ""
    risks:                    list[str] = []
    opportunities:            list[str] = []
    narrative_style:          str = "problem_solution"
    tone:                     str = "curious_analytical"


# ── Intelligence backbone builder ─────────────────────────────────────────────

def _build_intelligence(
    article_id: str,
    headline: str,
    source: str,
    source_url: str,
    jev_scores: dict | None,
    sentiment: str | None,
    sentiment_stats: dict | None,
    ai_tag: str | None,
    summary_result: NewsSummary,
    story: NewsStory | None,
    judgment: Any | None = None,
) -> NewsIntelligence:
    js = jev_scores or {}
    emotion_raw = js.get("emotion", {})
    impact_raw  = js.get("impact",  {})
    co_raw      = js.get("content_opportunity", {})

    return NewsIntelligence(
        article_id=article_id,
        headline=headline,
        source=source,
        source_url=source_url,
        topics=[ai_tag] if ai_tag else [],

        sentiment=sentiment or js.get("sentiment_polarity", "neutral"),
        sentiment_score=float((sentiment_stats or {}).get(
            sentiment or "neutral", 0.0
        )) if sentiment_stats else 0.0,
        sentiment_stats=dict(sentiment_stats) if sentiment_stats else {},

        emotion=EmotionSignals(
            curiosity=float(emotion_raw.get("curiosity",  0.0)),
            excitement=float(emotion_raw.get("excitement", 0.0)),
            concern=float(emotion_raw.get("concern",    0.0)),
            urgency=float(emotion_raw.get("urgency",    0.0)),
        ),

        impact=AudienceImpact(
            enterprise=float(impact_raw.get("enterprise",     0.0)),
            developers=float(impact_raw.get("developers",     0.0)),
            infrastructure=float(impact_raw.get("infrastructure", 0.0)),
            business=float(impact_raw.get("business",       0.0)),
            policy=float(impact_raw.get("policy",         0.0)),
            general_public=float(impact_raw.get("general_public", 0.0)),
        ),

        novelty=float(js.get("novelty",            0.0)),
        trend_velocity=float(js.get("trend_velocity",    0.0)),
        audience_relevance=float(js.get("audience_relevance", 0.0)),
        event_type=str(js.get("event_type",       "other")),
        significance=float(js.get("significance",      0.0)),
        controversy_level=str(js.get("controversy_level", "low")),

        what_happened=summary_result.summary,
        what_changed=summary_result.technology_impact,
        why_it_matters=summary_result.why_it_matters,
        who_is_affected=summary_result.job_impact,
        uncertainty="",
        key_points=list(summary_result.key_points),

        # Legacy compat
        summary=summary_result.summary,
        business_impact=summary_result.business_impact,
        job_impact=summary_result.job_impact,
        technology_impact=summary_result.technology_impact,
        policy_impact=summary_result.policy_impact,

        content_opportunity=ContentOpportunity(
            common_narrative=co_raw.get("common_narrative",    ""),
            missing_angle=co_raw.get("missing_angle",        ""),
            recommended_audience=co_raw.get("recommended_audience", ""),
            discussion_question=co_raw.get("discussion_question",  ""),
        ),

        # The storytelling layer — produced by MediaStorytellerAgent
        story=story,

        # The judgment layer — facts vs interpretation vs unknowns
        judgment=judgment,

        ai_tag=ai_tag,
    )


# ── MediaStorytellerAgent ──────────────────────────────────────────────────────

class MediaStorytellerAgent:
    """
    Pass 1: Read the article like a journalist. Extract the story — not facts, the STORY.

    Outputs a NewsStory object with hook, analogy, perspective, second-order effect,
    narrative style, and all context a content writer needs to tell the story compellingly.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.5,   # slightly higher than summary — needs creative framing
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )
        self._parser = PydanticOutputParser(pydantic_object=_NewsStoryRaw)
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", _load_prompt("storyteller")),
            (
                "human",
                "Article ID: {article_id}\n\n"
                "Headline: {title}\n\n"
                "Source: {source} ({source_url})\n\n"
                "Content:\n{content}\n\n"
                "PageIndex Evidence:\n{pageindex_sections}\n\n"
                "Intelligence Signals (calibrate depth and narrative style):\n"
                "  event_type: {event_type}\n"
                "  sentiment: {sentiment}  |  novelty: {novelty:.2f}  |  trend_velocity: {trend_velocity:.2f}\n"
                "  emotion — curiosity: {emotion_curiosity:.2f}  concern: {emotion_concern:.2f}"
                "  excitement: {emotion_excitement:.2f}  urgency: {emotion_urgency:.2f}\n"
                "  controversy: {controversy}  |  significance: {significance:.2f}\n"
                "  content_angle (Jev): {missing_angle}\n"
                "  recommended_audience: {recommended_audience}\n\n"
                "Judgment Boundaries (Do NOT assert claims as facts beyond these boundaries):\n"
                "  verified_facts: {verified_facts}\n"
                "  reported_claims: {reported_claims}\n"
                "  uncertainties: {uncertainties}\n"
                "  what_not_to_conclude: {what_not_to_conclude}\n\n"
                "{format_instructions}",
            ),
        ])

    async def extract_story(
        self,
        article_id: str,
        title: str,
        source: str,
        source_url: str,
        content: str,
        pageindex_sections: str,
        jev_scores: dict | None = None,
        judgment: Any | None = None,
        run_id: str | None = None,
    ) -> NewsStory:
        handler, _ = get_langfuse_callback(
            run_id=run_id,
            tags=["storyteller", self._settings.app_env],
            metadata={"article_id": article_id, "source": source,
                      "model": self._settings.llm_model, "agent": "media_storyteller"},
        )
        callbacks = [handler] if handler else []

        js = jev_scores or {}
        emotion = js.get("emotion", {})

        try:
            chain = self._prompt | self._llm | self._parser
            raw: _NewsStoryRaw = await chain.ainvoke(
                {
                    "article_id":         article_id,
                    "title":              title,
                    "source":             source,
                    "source_url":         source_url,
                    "content":            content,
                    "pageindex_sections": pageindex_sections,
                    "event_type":         js.get("event_type", "other"),
                    "sentiment":          js.get("sentiment_polarity") or "neutral",
                    "novelty":            float(js.get("novelty", 0.0)),
                    "trend_velocity":     float(js.get("trend_velocity", 0.0)),
                    "emotion_curiosity":  float(emotion.get("curiosity",  0.0)),
                    "emotion_concern":    float(emotion.get("concern",    0.0)),
                    "emotion_excitement": float(emotion.get("excitement", 0.0)),
                    "emotion_urgency":    float(emotion.get("urgency",    0.0)),
                    "controversy":        js.get("controversy_level", "low"),
                    "significance":       float(js.get("significance", 0.0)),
                    "missing_angle":      (js.get("content_opportunity") or {}).get("missing_angle", ""),
                    "recommended_audience": (js.get("content_opportunity") or {}).get("recommended_audience", ""),
                    "verified_facts":     getattr(judgment, "facts", []) if judgment else [],
                    "reported_claims":    getattr(judgment, "reported_claims", []) if judgment else [],
                    "uncertainties":      getattr(judgment, "uncertainties", []) if judgment else [],
                    "what_not_to_conclude": getattr(judgment, "what_not_to_conclude", []) if judgment else [],
                    "format_instructions": self._parser.get_format_instructions(),
                },
                config={"callbacks": callbacks} if callbacks else {},
            )
        except Exception as exc:
            log.warning("MediaStorytellerAgent failed for %s: %s", article_id, exc)
            return NewsStory()   # empty story — downstream agents degrade gracefully

        return NewsStory(
            what_actually_happened=raw.what_actually_happened,
            who=raw.who,
            what_changed=raw.what_changed,
            why_now=raw.why_now,
            what_came_before=raw.what_came_before,
            what_problem_it_solves=raw.what_problem_it_solves,
            hook=raw.hook,
            human_analogy=raw.human_analogy,
            turning_point=raw.turning_point,
            why_reader_should_care=raw.why_reader_should_care,
            perspective=raw.perspective,
            second_order_effect=raw.second_order_effect,
            future_question=raw.future_question,
            business_consequence=raw.business_consequence,
            technology_consequence=raw.technology_consequence,
            human_consequence=raw.human_consequence,
            risks=raw.risks,
            opportunities=raw.opportunities,
            narrative_style=raw.narrative_style or "problem_solution",
            tone=raw.tone or "curious_analytical",
            media_host_opening=raw.media_host_opening or raw.hook,
            media_host_setup=raw.media_host_setup,
            media_transitions=raw.media_transitions or {},
            media_host_synthesis=raw.media_host_synthesis or raw.perspective,
            media_host_audience_cta=raw.media_host_audience_cta or raw.future_question,
            dynamic_seo_hashtags=raw.dynamic_seo_hashtags or [],
        )


# ── SummaryAgent ───────────────────────────────────────────────────────────────

class SummaryAgent:
    """
    Pass 2: Structured summary from article + story context.
    Produces the NewsSummary with headline, key_points, impacts, etc.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.2,
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )
        self._parser = PydanticOutputParser(pydantic_object=NewsSummary)
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", _load_prompt("summary")),
            (
                "human",
                "Article ID: {article_id}\n\n"
                "Title: {title}\n\n"
                "Source: {source} ({source_url})\n\n"
                "Content:\n{content}\n\n"
                "Relevant PageIndex Sections:\n{pageindex_sections}\n\n"
                "Story Context (extracted by Media Storyteller — use to sharpen framing):\n"
                "  hook: {story_hook}\n"
                "  perspective: {story_perspective}\n"
                "  second_order_effect: {story_second_order}\n"
                "  human_analogy: {story_analogy}\n"
                "  why_reader_should_care: {story_why_care}\n\n"
                "Intelligence Signals:\n"
                "  sentiment: {sentiment}  |  ai_tag: {ai_tag}\n"
                "  novelty: {novelty:.2f}  |  trend_velocity: {trend_velocity:.2f}\n"
                "  emotion — curiosity: {emotion_curiosity:.2f}  excitement: {emotion_excitement:.2f}"
                "  concern: {emotion_concern:.2f}  urgency: {emotion_urgency:.2f}\n"
                "  impact  — enterprise: {impact_enterprise:.2f}  developers: {impact_developers:.2f}"
                "  business: {impact_business:.2f}  policy: {impact_policy:.2f}\n\n"
                "{format_instructions}",
            ),
        ])

    async def summarize(
        self,
        article_id: str,
        title: str,
        source: str,
        source_url: str,
        content: str,
        pageindex_sections: str,
        run_id: str | None = None,
        sentiment: str | None = None,
        sentiment_stats: dict | None = None,
        ai_tag: str | None = None,
        jev_scores: dict | None = None,
        story: NewsStory | None = None,
    ) -> NewsSummary:
        handler, _ = get_langfuse_callback(
            run_id=run_id,
            tags=["summary", self._settings.app_env],
            metadata={"article_id": article_id, "source": source,
                      "model": self._settings.llm_model, "agent": "summary_agent"},
        )
        callbacks = [handler] if handler else []

        js = jev_scores or {}
        emotion = js.get("emotion", {})
        impact  = js.get("impact",  {})
        s = story or NewsStory()

        chain = self._prompt | self._llm | self._parser
        result: NewsSummary = await chain.ainvoke(
            {
                "article_id":          article_id,
                "title":               title,
                "source":              source,
                "source_url":          source_url,
                "content":             content,
                "pageindex_sections":  pageindex_sections,
                # Story context — sharpens framing
                "story_hook":          s.hook or "not available",
                "story_perspective":   s.perspective or "not available",
                "story_second_order":  s.second_order_effect or "not available",
                "story_analogy":       s.human_analogy or "not available",
                "story_why_care":      s.why_reader_should_care or "not available",
                # Intelligence signals
                "sentiment":           sentiment or js.get("sentiment_polarity") or "not available",
                "ai_tag":              ai_tag or "not available",
                "novelty":             float(js.get("novelty",            0.0)),
                "trend_velocity":      float(js.get("trend_velocity",     0.0)),
                "emotion_curiosity":   float(emotion.get("curiosity",  0.0)),
                "emotion_excitement":  float(emotion.get("excitement", 0.0)),
                "emotion_concern":     float(emotion.get("concern",    0.0)),
                "emotion_urgency":     float(emotion.get("urgency",    0.0)),
                "impact_enterprise":   float(impact.get("enterprise",  0.0)),
                "impact_developers":   float(impact.get("developers",  0.0)),
                "impact_business":     float(impact.get("business",    0.0)),
                "impact_policy":       float(impact.get("policy",      0.0)),
                "format_instructions": self._parser.get_format_instructions(),
            },
            config={"callbacks": callbacks} if callbacks else {},
        )

        result.sentiment       = sentiment
        result.sentiment_stats = sentiment_stats
        result.ai_tag          = ai_tag
        result.story           = story

        result.intelligence = _build_intelligence(
            article_id=article_id,
            headline=result.headline,
            source=source,
            source_url=source_url,
            jev_scores=jev_scores,
            sentiment=sentiment,
            sentiment_stats=sentiment_stats,
            ai_tag=ai_tag,
            summary_result=result,
            story=story,
        )

        return result
