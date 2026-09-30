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
import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from daily_news.config.settings import get_settings
from daily_news.mcp.client import jev_singleton
from daily_news.mcp.jev_client import JevPrefilterResult
from daily_news.models.persona import PersonaType

logger = logging.getLogger(__name__)

# ── Prefix cache — avoids re-scoring the same article on pipeline retries ────
#
# Design:
#   Key   = SHA-256 of (title + description + published_at) — content fingerprint.
#   Value = JevPrefilterResult (immutable after scoring; never needs invalidation).
#   Scope = process-level dict — survives across graph retries within the same pod run.
#           Cleared automatically when the pod restarts (daily CronJob).
#   Size  = bounded by articles fetched per run (typically 10-30); no eviction needed.
#
# Why this matters:
#   jev_prefilter_articles is called AGAIN on every REGENERATE cycle (banned phrases,
#   eval fail, etc.).  Each Jev call is ~800ms.  With 10 articles × 3 retries = 30
#   redundant calls wasted.  Cache makes retries effectively free.

_JEV_PREFILTER_CACHE: dict[str, JevPrefilterResult] = {}


def _article_cache_key(article: dict) -> str:
    """Stable content fingerprint for an article dict."""
    raw = (
        str(article.get("title", ""))
        + str(article.get("description", ""))
        + str(article.get("published_at", ""))
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(article: dict) -> JevPrefilterResult | None:
    return _JEV_PREFILTER_CACHE.get(_article_cache_key(article))


def _cache_set(article: dict, result: JevPrefilterResult) -> None:
    _JEV_PREFILTER_CACHE[_article_cache_key(article)] = result

# ── Fallback scorer (no Jev, no LLM) ─────────────────────────────────────────
# Used when JEV_BASE_URL is not set. Scores articles purely from metadata so
# the workflow never blindly picks [:1] (always the same GNews-ordered article).
#
# Scoring dimensions (all 0–1, summed to a composite 0–4):
#   recency         — published within last 24h → 1.0, 48h → 0.6, 72h → 0.3, older → 0.1
#   ai_relevance    — count of high-signal AI keywords in title + description
#   source_quality  — whitelist of known-good AI/tech sources
#   title_length    — sweet-spot 60–100 chars (informative but not clickbait)

_AI_KEYWORDS = re.compile(
    r"\b(openai|anthropic|deepmind|gemini|gpt|llm|claude|mistral|qwen|llama|"
    r"artificial intelligence|machine learning|neural|chatgpt|copilot|agentic|"
    r"generative ai|foundation model|large language|transformer|nvidia|ai chip|"
    r"ai regulation|ai policy|ai safety|ai startup|ai funding|ai agent)\b",
    re.IGNORECASE,
)
_QUALITY_SOURCES = {
    "techcrunch", "wired", "mit technology review", "the verge", "venturebeat",
    "siliconangle", "reuters", "bloomberg", "financial times", "the information",
    "ars technica", "zdnet", "cnet", "ieee spectrum", "nature", "science",
    "axios", "politico", "wall street journal", "new york times", "washington post",
    "the guardian", "bbc", "cnbc", "fortune", "fast company",
}


def _heuristic_score(article: dict) -> float:
    """Return a 0–4 composite score from article metadata alone."""
    title = article.get("title", "")
    desc  = article.get("description", "") or ""
    src   = (article.get("source", "") or "").lower()
    pub   = article.get("published_at", "") or ""

    # Recency
    recency = 0.1
    try:
        age_h = (datetime.now(UTC) - datetime.fromisoformat(pub.replace("Z", "+00:00"))).total_seconds() / 3600
        if age_h <= 24:   recency = 1.0
        elif age_h <= 48: recency = 0.6
        elif age_h <= 72: recency = 0.3
    except Exception:  # noqa: BLE001
        pass

    # AI relevance — keyword hits in title + description (cap at 1.0)
    text = f"{title} {desc}"
    hits = len(_AI_KEYWORDS.findall(text))
    ai_relevance = min(hits * 0.25, 1.0)

    # Source quality
    source_quality = 0.5
    for q in _QUALITY_SOURCES:
        if q in src:
            source_quality = 1.0
            break

    # Title length sweet-spot
    tl = len(title)
    title_score = 1.0 if 60 <= tl <= 100 else (0.6 if 40 <= tl <= 120 else 0.3)

    return recency + ai_relevance + source_quality + title_score


def _heuristic_select(articles: list[dict], top_n: int = 3) -> list[dict]:
    """Sort articles by heuristic score, return top_n."""
    scored = sorted(articles, key=_heuristic_score, reverse=True)
    if logger.isEnabledFor(logging.INFO):
        for i, a in enumerate(scored[:top_n]):
            logger.info(
                "jev_prefilter_fallback: #%d score=%.2f title=%s",
                i + 1, _heuristic_score(a), a.get("title", "")[:70],
            )
    return scored[:top_n]


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

    top_n = max(1, s.workflow_max_articles)

    if not s.jev_enabled:
        logger.info("[%s] jev_prefilter: JEV_ENABLED=false — heuristic fallback (%d articles, top_n=%d)", run_id, len(articles), top_n)
        return {**state, "selected_articles": _heuristic_select(articles, top_n=top_n), "workflow_status": "JEV_PREFILTERED"}

    if not s.jev_base_url:
        logger.info("[%s] jev_prefilter: JEV_BASE_URL not set — heuristic fallback (%d articles, top_n=%d)", run_id, len(articles), top_n)
        return {**state, "selected_articles": _heuristic_select(articles, top_n=top_n), "workflow_status": "JEV_PREFILTERED"}

    # ── Prefix cache: separate articles into cache hits and misses ────────────
    cached:   dict[int, JevPrefilterResult] = {}   # index → cached result
    miss_idx: list[int]                     = []   # indices that need Jev calls

    for idx, article in enumerate(articles):
        hit = _cache_get(article)
        if hit is not None:
            cached[idx] = hit
            logger.debug(
                "[%s] jev_prefilter: cache HIT article_id=%s",
                run_id, article.get("article_id", "?"),
            )
        else:
            miss_idx.append(idx)

    if cached:
        logger.info(
            "[%s] jev_prefilter: prefix-cache %d hits, %d misses (of %d)",
            run_id, len(cached), len(miss_idx), len(articles),
        )

    try:
        client = jev_singleton()
        sem = asyncio.Semaphore(5)

        async def _score(article: dict) -> JevPrefilterResult:
            async with sem:
                result = await client.prefilter_article(article)
                _cache_set(article, result)   # warm the cache for future retries
                return result

        miss_articles = [articles[i] for i in miss_idx]
        miss_results: list[JevPrefilterResult] = await asyncio.gather(
            *[_score(a) for a in miss_articles],
            return_exceptions=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] jev_prefilter failed (%s) — heuristic fallback", run_id, exc)
        return {**state, "selected_articles": _heuristic_select(articles), "workflow_status": "JEV_PREFILTERED"}

    # Merge cache hits and fresh results back into a full results list
    full_results: list[JevPrefilterResult | Exception] = []
    miss_iter = iter(miss_results)
    for idx in range(len(articles)):
        if idx in cached:
            full_results.append(cached[idx])
        else:
            full_results.append(next(miss_iter))
    results = full_results

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
        logger.warning("[%s] jev_prefilter: no AI articles passed — heuristic fallback", run_id)
        return {**state, "selected_articles": _heuristic_select(articles, top_n=top_n), "workflow_status": "JEV_PREFILTERED"}

    # Pick top_n articles by composite score.
    # Keeping at least 1 extra internally gives the eval/retry loop a fallback
    # if the best article triggers banned phrases — prevents a single-article deadlock.
    # The publish node caps actual posts to top_n via WORKFLOW_MAX_ARTICLES.
    internal_n = max(top_n, 2)   # always score ≥2 so retry has a fallback
    scored.sort(key=lambda t: t[0], reverse=True)
    top_selected = scored[:internal_n]

    best_score, best_article, best_result = top_selected[0]

    for rank, (score, article, result) in enumerate(top_selected[:top_n], start=1):
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
    for _, article, result in top_selected:
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
        "selected_articles":    [article for _, article, _ in top_selected],
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

    client = jev_singleton()
    updated_scores = dict(all_jev)

    # Architecture fix: was a serial for-loop — each content_angle call ~800ms.
    # With 3 articles: 3 × 800ms = 2.4s serial → now ~800ms parallel via gather.
    async def _angle_one(summary_dict: dict) -> None:
        aid = summary_dict.get("article_id", "")
        try:
            intel = all_jev.get(aid, {})
            intelligence_state = _build_intelligence_state(summary_dict, intel)
            angle = await client.content_angle(intelligence_state)
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

    await asyncio.gather(*[_angle_one(s) for s in summaries])

    return {
        **state,
        "jev_prefilter_scores": updated_scores,
        "workflow_status":      "JEV_ANGLE_FOUND",
    }


def _build_conflict_graph(summary_dict: dict, intel: dict) -> str:
    """
    Build a CONFLICT GRAPH for this story — the editorial spine of the post.

    The conflict graph is prepended to the intelligence state string fed to Jev.
    It forces the angle-finding step to identify the TWO FORCES in tension
    (opportunity vs. risk) so that persona generation can produce genuine
    disagreement rather than parallel opinions.

    Output format matches the CONFLICT GRAPH schema in the storyteller prompt.
    This is deterministic — no LLM call — derived from existing intelligence signals.
    """
    headline = summary_dict.get("headline", "")
    summary  = summary_dict.get("summary", "")
    business = summary_dict.get("business_impact", "")
    tech     = summary_dict.get("technology_impact", "")
    job      = summary_dict.get("job_impact", "")
    event    = intel.get("event_type", "other")

    # Derive the opportunity and risk signals from existing intelligence
    # Opportunity → what the event enables (business / tech capability gain)
    # Risk        → what the event creates as a problem (jobs, governance, dependency)
    opportunity_signals = [s for s in [business, tech] if s.strip()]
    risk_signals        = [s for s in [job, summary_dict.get("policy_impact", "")] if s.strip()]

    opportunity = opportunity_signals[0][:200] if opportunity_signals else f"New {event} capability enabled"
    risk        = risk_signals[0][:200]        if risk_signals        else "Governance and dependency exposure created"

    conflict_lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "CONFLICT GRAPH (story spine):",
        f"  NEWS:        {headline[:120]}",
        f"  OPPORTUNITY: {opportunity}",
        f"  RISK:        {risk}",
        "  → FOUNDER   sees the OPPORTUNITY (cost reduction, market timing, competitive position)",
        "  → ENGINEER  sees the RISK (architecture, operational debt, hidden complexity)",
        "  → ANALYST   challenges both (who wins at platform scale, second-order displacement)",
        "  → POLICY    adds governance layer (accountability, concentration, what happens when wrong)",
        "  → HOST      names the unresolved tension that survives the debate",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(conflict_lines)


def _build_intelligence_state(summary_dict: dict, intel: dict) -> str:
    """Build the state string fed to Jev for content_angle scoring.
    Prepends the CONFLICT GRAPH so Jev understands the editorial spine before
    choosing a content angle — forces tension-first framing over news-summary framing.
    """
    emotion = intel.get("emotion", {})
    impact  = intel.get("impact", {})
    parts = [
        _build_conflict_graph(summary_dict, intel),
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

    client = jev_singleton()
    # Architecture fix: was a serial for-loop over summaries (same 800ms-per-call
    # serial penalty as find_angle).  Use gather — each summary is independent.
    # We collect the union of all active personas across articles (typically 1 article
    # after jev_prefilter, but gather is correct for the multi-article case too).
    all_active: set[str] = set()

    async def _route_one(summary_dict: dict) -> None:
        try:
            persona_types = await client.route_personas(summary_dict)
            vals = [p.value for p in persona_types]
            all_active.update(vals)
            logger.info(
                "[%s] jev_route_personas: article=%s active_personas=%s",
                run_id, summary_dict.get("article_id"), vals,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] jev_route_personas failed (%s) — falling back to all personas", run_id, exc
            )
            all_active.update(all_personas)

    await asyncio.gather(*[_route_one(s) for s in summaries])

    active_personas = list(all_active) if all_active else all_personas

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
