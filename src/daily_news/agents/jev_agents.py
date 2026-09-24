"""
Jev-powered graph nodes.

Three decision points where Jev (System One) replaces LLM-based logic:

  1. jev_prefilter_articles(state)
       Scores all fetched articles in parallel and selects the single best
       article by relevance + estimated engagement.
       Replaces the blind selected_articles[:1] cut.

  2. jev_route_personas(state)
       Given the generated NewsSummary, decides which subset of the five
       personas are genuinely relevant to this article.
       Replaces the always-run-all-five behaviour in generate_personas.

  3. JevEvaluationClient  (used by EvaluationAgent)
       Drop-in replacement for EvaluationMCPClient — same interface,
       backed by Jev instead of the LLM evaluation MCP server.

Jev fallback
────────────
If JEV_ENABLED=false in settings, or if the Jev gateway call fails,
both nodes fall back gracefully:
  - prefilter falls back to [:2] selection
  - router falls back to all five personas
  - evaluator falls back to EvaluationMCPClient
"""
from __future__ import annotations

import asyncio
import logging

from daily_news.config.settings import get_settings
from daily_news.mcp.jev_client import JevClient, JevPrefilterResult
from daily_news.models.persona import PersonaType

logger = logging.getLogger(__name__)


# ── Node 1: Article pre-filter ────────────────────────────────────────────────

async def jev_prefilter_articles(state: dict) -> dict:
    """
    LangGraph node — replaces select_stories.

    Scores every article from selected_articles in parallel via Jev and
    picks the single best article by composite score:
        composite = relevance_score * 0.6 + estimated_engagement * 0.4

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

    client = JevClient()
    sem = asyncio.Semaphore(5)

    async def _score(article: dict) -> JevPrefilterResult:
        async with sem:
            return await client.prefilter_article(article)

    try:
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
            "[%s] jev_prefilter: #%d article_id=%s relevance=%.2f engagement=%.2f composite=%.2f personas=%s",
            run_id, rank,
            result.article_id,
            result.relevance_score,
            result.estimated_engagement,
            score,
            result.persona_fit,
        )

    # jev_prefilter_scores is keyed by article_id so the publish loop can look
    # up the single article's own Jev scores.
    # Shape: { "<article_id>": { event_type, relevance_score, ... } }
    prefilter_scores: dict[str, dict] = {}
    for _, article, result in top1:
        aid = article.get("article_id", result.article_id)
        prefilter_scores[aid] = {
            "event_type":           result.event_type,
            "relevance_score":      result.relevance_score,
            "significance":         result.significance,
            "estimated_engagement": result.estimated_engagement,
            "controversy_level":    result.controversy_level,
            "active_personas":      result.persona_fit,
            "persona_scores":       result.persona_scores,
        }

    return {
        **state,
        "selected_articles":    [article for _, article, _ in top1],
        "jev_persona_hints":    best_result.persona_fit,
        "jev_prefilter_scores": prefilter_scores,
        "workflow_status":      "JEV_PREFILTERED",
    }


# ── Node 2: Persona router ────────────────────────────────────────────────────

async def jev_route_personas(state: dict) -> dict:
    """
    LangGraph node — sits between summarize and generate_personas.

    Uses the generated NewsSummary to ask Jev which personas are genuinely
    relevant.  Writes jev_active_personas into state; generate_personas
    reads this to skip irrelevant persona LLM calls.

    Falls back to all five personas if Jev is disabled or fails.
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
