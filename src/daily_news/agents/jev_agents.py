"""
Jev-powered graph nodes.

Three decision points where Jev (System One) replaces LLM-based logic:

  1. jev_prefilter_articles(state)
       Scores all fetched articles in parallel via Jev.
       Selects the single best article by relevance + estimated engagement.
       Populates jev_prefilter_scores with the full Stage 3 intelligence signals:
         event_type, significance, controversy_level, sentiment_polarity,
         emotion (curiosity/excitement/concern/urgency),
         impact (enterprise/developers/infrastructure/business/policy/public),
         novelty, trend_velocity, audience_relevance.

  2. jev_find_angle(state)
       Stage 5 — given the generated summary, asks Jev to identify:
         - the common narrative (what everyone else is saying)
         - the missing angle (what's underreported)
         - recommended audience
       Writes content_opportunity into each summary's intelligence object.

  3. jev_route_personas(state)
       Given the generated NewsSummary, decides which subset of the four
       personas are genuinely relevant to this article.
       Replaces the always-run-all-four behaviour in generate_personas.

  4. JevEvaluationClient  (used by EvaluationAgent)
       Drop-in replacement for EvaluationMCPClient — same interface,
       backed by Jev instead of the LLM evaluation MCP server.

Jev fallback
────────────
If JEV_ENABLED=false in settings, or if the Jev gateway call fails,
all nodes fall back gracefully:
  - prefilter falls back to [:1] selection
  - find_angle falls back to empty content_opportunity
  - router falls back to all four personas
  - evaluator falls back to EvaluationMCPClient
"""
from __future__ import annotations

import asyncio
import logging

from daily_news.config.settings import get_settings
from daily_news.mcp.jev_client import JevClient, JevPrefilterResult
from daily_news.models.persona import PersonaType

logger = logging.getLogger(__name__)


# ── Node 1: Article pre-filter + Stage 3 intelligence ────────────────────────

async def jev_prefilter_articles(state: dict) -> dict:
    """
    LangGraph node — Stage 3 ANALYZE.

    Scores every article from selected_articles in parallel via Jev and
    picks the single best article by composite score:
        composite = relevance_score * 0.6 + estimated_engagement * 0.4

    Populates jev_prefilter_scores with the FULL Stage 3 intelligence object
    (emotion, impact, novelty, trend_velocity, audience_relevance, etc.)
    so the summarize node can build a rich NewsIntelligence object.

    Falls back to [:1] if Jev is disabled or the call fails.
    Stores jev_persona_hints in state for jev_route_personas to consume.
    """
    s = get_settings()
    run_id = state.get("run_id", "")
    articles: list[dict] = state.get("selected_articles", [])

    if not articles:
        logger.warning("[%s] jev_prefilter: no articles to score", run_id)
        return {**state, "workflow_status": "JEV_PREFILTERED"}

    if not s.jev_enabled:
        logger.info("[%s] jev_prefilter: JEV_ENABLED=false — keeping [:1]", run_id)
        return {**state, "selected_articles": articles[:1], "workflow_status": "JEV_PREFILTERED"}

    if not s.jev_base_url:
        logger.info("[%s] jev_prefilter: JEV_BASE_URL not set — keeping [:1]", run_id)
        return {**state, "selected_articles": articles[:1], "workflow_status": "JEV_PREFILTERED"}

    try:
        client = JevClient()
        sem = asyncio.Semaphore(5)

        async def _score(article: dict) -> JevPrefilterResult:
            async with sem:
                return await client.prefilter_article(article)

        results: list[JevPrefilterResult] = await asyncio.gather(
            *[_score(a) for a in articles],
            return_exceptions=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] jev_prefilter failed (%s) — falling back to [:1]", run_id, exc)
        return {**state, "selected_articles": articles[:1], "workflow_status": "JEV_PREFILTERED"}

    # Filter out exceptions, pair with original article
    scored: list[tuple[float, dict, JevPrefilterResult]] = []
    for article, result in zip(articles, results):
        if isinstance(result, Exception):
            logger.warning("[%s] jev_prefilter: article %s errored: %r",
                           run_id, article.get("article_id"), result)
            continue
        if not result.is_ai_topic:
            logger.info("[%s] jev_prefilter: skipping non-AI article %s — %s",
                        run_id, result.article_id, result.skip_reason)
            continue
        composite = result.relevance_score * 0.6 + result.estimated_engagement * 0.4
        scored.append((composite, article, result))

    if not scored:
        logger.warning("[%s] jev_prefilter: no AI articles passed — keeping [:1]", run_id)
        return {**state, "selected_articles": articles[:1], "workflow_status": "JEV_PREFILTERED"}

    # Pick the single best article by composite score
    scored.sort(key=lambda t: t[0], reverse=True)
    top1 = scored[:1]

    best_score, best_article, best_result = top1[0]

    for rank, (score, article, result) in enumerate(top1, start=1):
        logger.info(
            "[%s] jev_prefilter: #%d article_id=%s relevance=%.2f engagement=%.2f "
            "composite=%.2f novelty=%.2f trend=%.2f emotion=C%.2f/E%.2f/W%.2f/U%.2f",
            run_id, rank,
            result.article_id,
            result.relevance_score,
            result.estimated_engagement,
            score,
            result.novelty,
            result.trend_velocity,
            result.emotion_curiosity,
            result.emotion_excitement,
            result.emotion_concern,
            result.emotion_urgency,
        )

    # jev_prefilter_scores carries the FULL Stage 3 intelligence signals.
    # Shape: { "<article_id>": { ...all intelligence fields... } }
    prefilter_scores: dict[str, dict] = {}
    for _, article, result in top1:
        aid = article.get("article_id", result.article_id)
        prefilter_scores[aid] = {
            # Core selection
            "event_type":           result.event_type,
            "relevance_score":      result.relevance_score,
            "significance":         result.significance,
            "estimated_engagement": result.estimated_engagement,
            "controversy_level":    result.controversy_level,
            "active_personas":      result.persona_fit,
            "persona_scores":       result.persona_scores,
            "sentiment_polarity":   result.sentiment_polarity,
            # Stage 3: emotion signals
            "emotion": {
                "curiosity":   result.emotion_curiosity,
                "excitement":  result.emotion_excitement,
                "concern":     result.emotion_concern,
                "urgency":     result.emotion_urgency,
            },
            # Stage 3: audience impact
            "impact": {
                "enterprise":     result.impact_enterprise,
                "developers":     result.impact_developers,
                "infrastructure": result.impact_infrastructure,
                "business":       result.impact_business,
                "policy":         result.impact_policy,
                "general_public": result.impact_general_public,
            },
            # Stage 3: novelty + trend
            "novelty":            result.novelty,
            "trend_velocity":     result.trend_velocity,
            "audience_relevance": result.audience_relevance,
        }

    return {
        **state,
        "selected_articles":    [article for _, article, _ in top1],
        "jev_persona_hints":    best_result.persona_fit,
        "jev_prefilter_scores": prefilter_scores,
        "workflow_status":      "JEV_PREFILTERED",
    }


# ── Node 2: Find the Angle (Stage 5) ─────────────────────────────────────────

async def jev_find_angle(state: dict) -> dict:
    """
    LangGraph node — Stage 5 FIND THE ANGLE.

    After summarization, asks Jev to identify:
      - what the common narrative is (what everyone else is saying)
      - what the missing/contrarian angle is (what's underreported)
      - which audience segment to target
      - what discussion question would spark genuine conversation

    Writes content_opportunity back into the summary's intelligence object
    in jev_prefilter_scores so the publisher can drive the post angle from it.

    Falls back to empty content_opportunity if Jev is disabled or fails.
    """
    s = get_settings()
    run_id = state.get("run_id", "")
    summaries: list[dict] = state.get("summaries", [])
    all_jev: dict = state.get("jev_prefilter_scores") or {}

    if not summaries:
        return {**state, "workflow_status": "JEV_ANGLE_FOUND"}

    if not s.jev_enabled:
        logger.info("[%s] jev_find_angle: JEV_ENABLED=false — skipping", run_id)
        return {**state, "workflow_status": "JEV_ANGLE_FOUND"}

    if not s.jev_base_url:
        logger.info("[%s] jev_find_angle: JEV_BASE_URL not set — skipping", run_id)
        return {**state, "workflow_status": "JEV_ANGLE_FOUND"}

    client = JevClient()
    updated_scores = dict(all_jev)

    for summary_dict in summaries:
        aid = summary_dict.get("article_id", "")
        try:
            # Build a rich intelligence state string for Jev
            intel = all_jev.get(aid, {})
            intelligence_state = _build_intelligence_state(summary_dict, intel)

            angle = await client.content_angle(intelligence_state)

            # Inject content_opportunity into the article's intelligence scores
            if aid in updated_scores:
                updated_scores[aid]["content_opportunity"] = angle
            else:
                updated_scores[aid] = {"content_opportunity": angle}

            logger.info(
                "[%s] jev_find_angle: article=%s audience=%s missing_angle=%s",
                run_id, aid,
                angle.get("recommended_audience", ""),
                angle.get("missing_angle", "")[:60],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] jev_find_angle failed for %s (%s) — skipping", run_id, aid, exc)

    return {
        **state,
        "jev_prefilter_scores": updated_scores,
        "workflow_status":      "JEV_ANGLE_FOUND",
    }


def _build_intelligence_state(summary_dict: dict, intel: dict) -> str:
    """Build the state string fed to Jev for content_angle scoring."""
    emotion = intel.get("emotion", {})
    impact  = intel.get("impact", {})
    parts = [
        f"HEADLINE: {summary_dict.get('headline', '')}",
        f"SUMMARY: {summary_dict.get('summary', '')}",
        f"WHY IT MATTERS: {summary_dict.get('why_it_matters', '')}",
        f"BUSINESS IMPACT: {summary_dict.get('business_impact', '')}",
        f"JOB IMPACT: {summary_dict.get('job_impact', '')}",
        f"TECHNOLOGY IMPACT: {summary_dict.get('technology_impact', '')}",
        f"KEY POINTS: {' | '.join(summary_dict.get('key_points', []))}",
        f"EVENT_TYPE: {intel.get('event_type', '')}",
        f"SENTIMENT: {intel.get('sentiment_polarity', '')}",
        f"NOVELTY: {intel.get('novelty', 0.0):.2f}",
        f"TREND_VELOCITY: {intel.get('trend_velocity', 0.0):.2f}",
        f"EMOTION curiosity={emotion.get('curiosity', 0.0):.2f} excitement={emotion.get('excitement', 0.0):.2f} concern={emotion.get('concern', 0.0):.2f}",
        f"IMPACT enterprise={impact.get('enterprise', 0.0):.2f} developers={impact.get('developers', 0.0):.2f} business={impact.get('business', 0.0):.2f}",
    ]
    return "\n".join(p for p in parts if p.split(": ", 1)[-1].strip())


# ── Node 3: Persona router ────────────────────────────────────────────────────

async def jev_route_personas(state: dict) -> dict:
    """
    LangGraph node — sits between jev_find_angle and generate_personas.

    Uses the generated NewsSummary to ask Jev which personas are genuinely
    relevant.  Writes jev_active_personas into state; generate_personas
    reads this to skip irrelevant persona LLM calls.

    Falls back to all four personas if Jev is disabled or fails.
    Also accepts jev_persona_hints from jev_prefilter as a warm start.
    """
    s = get_settings()
    run_id = state.get("run_id", "")
    summaries: list[dict] = state.get("summaries", [])
    hints: list[str] = state.get("jev_persona_hints", [])

    all_personas = [p.value for p in PersonaType]

    if not s.jev_enabled:
        logger.info("[%s] jev_route_personas: JEV_ENABLED=false — running all personas", run_id)
        return {**state, "jev_active_personas": all_personas, "workflow_status": "JEV_ROUTED"}

    if not s.jev_base_url:
        logger.info("[%s] jev_route_personas: JEV_BASE_URL not set — running all personas", run_id)
        return {**state, "jev_active_personas": all_personas, "workflow_status": "JEV_ROUTED"}

    if not summaries:
        return {**state, "jev_active_personas": all_personas, "workflow_status": "JEV_ROUTED"}

    client = JevClient()
    active_personas: list[str] = []

    for summary_dict in summaries:
        try:
            persona_types = await client.route_personas(summary_dict)
            active_personas = [p.value for p in persona_types]
            logger.info(
                "[%s] jev_route_personas: article=%s active_personas=%s",
                run_id, summary_dict.get("article_id"), active_personas,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] jev_route_personas failed (%s) — falling back to all personas", run_id, exc
            )
            active_personas = all_personas

    # Merge with prefilter hints (union — keep any persona flagged by either signal)
    if hints:
        merged = list(set(active_personas) | set(hints))
        logger.info("[%s] jev_route_personas: merged with prefilter hints → %s", run_id, merged)
        active_personas = merged

    return {
        **state,
        "jev_active_personas": active_personas,
        "workflow_status": "JEV_ROUTED",
    }
