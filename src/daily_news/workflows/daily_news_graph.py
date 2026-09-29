"""
LangGraph Daily News Workflow — with Jev System One decision nodes
==================================================================

7-stage AI News Intelligence pipeline:

  Stage 1 DISCOVER  → discover_news   GNews → raw articles
  Stage 2 UNDERSTAND→ index_pageindex PageIndex → document tree + evidence
  Stage 3 ANALYZE   → jev_prefilter   Jev → sentiment, emotion, impact, novelty, trend
  Stage 4 EXPLAIN   → summarize       LLM → what happened, why it matters, who is affected
  Stage 5 ANGLE     → find_angle      Jev → common narrative, missing angle, audience
  Stage 6 CREATE    → generate_personas + publish  LinkedIn content per audience
  Stage 7 LEARN     → (future) feedback loop

LangGraph state machine:

  START
    └─► discover_news         9 GNews queries → ~27 fresh articles
          └─► deduplicate     3-pass dedup: URL-hash + PublishedStore + title-similarity
                └─► fetch_articles
                      └─► index_pageindex     PageIndex document tree
                            └─► jev_prefilter          ← Stage 3: full intelligence scoring
                                  └─► summarize              ← Stage 4: LLM explanation
                                        └─► find_angle        ← Stage 5: Jev content angle
                                              └─► jev_router        ← route relevant personas
                                                    └─► generate_personas  (parallel)
                                                          └─► linkedin_optimize  ← Hook + Humanizer + Audit
                                                                └─► evaluate    ← Jev quality gate
                                                                ├─► REGENERATE ─► summarize
                                                                ├─► BLOCK      ─► hard stop
                                                                ├─► HUMAN_REVIEW ─► approval
                                                                └─► PASS
                                                                      └─► score_reach
                                                                            └─► publish
                                                                                  └─► END

Jev integration points
──────────────────────
1. jev_prefilter   Stage 3 ANALYZE — full intelligence (emotion/impact/novelty/trend)
2. find_angle      Stage 5 ANGLE   — content_opportunity (missing angle, audience)
3. jev_router      routes relevant personas from summary + content_opportunity
4. EvaluationAgent uses JevClient.evaluate_content() → 9 quality questions

All nodes fall back gracefully when JEV_ENABLED=false or on network error.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from daily_news.agents.content_optimizer import ContentOptimizerAgent
from daily_news.agents.evaluation_agent import EvaluationAgent
from daily_news.agents.linkedin_skills_optimizer import LinkedInSkillsOptimizer
from daily_news.config.settings import get_settings
from daily_news.agents.guardrails import InputGuardrail
from daily_news.agents.jev_agents import jev_find_angle, jev_prefilter_articles, jev_route_personas
from daily_news.agents.judgment_agent import JudgmentAgent
from daily_news.agents.persona_agent import PersonaAgentFactory
from daily_news.agents.published_store import published_store
from daily_news.agents.publisher_agent import PublisherAgent
from daily_news.agents.reach_score_agent import REACH_THRESHOLD, ReachScoreAgent
from daily_news.agents.sentiment_resolver import resolve_sentiment
from daily_news.agents.summary_agent import MediaStorytellerAgent, SummaryAgent
from daily_news.mcp.linkedin import LinkedInMCPClient
from daily_news.mcp.news import NewsMCPClient
from daily_news.mcp.pageindex import PageIndexMCPClient
from daily_news.models.evaluation import EvaluationDecision
from daily_news.models.intelligence import EngagementMetrics
from daily_news.models.news import NewsCategory
from daily_news.models.persona import PersonaType
from daily_news.observability.tracing import flush_langfuse, langfuse_trace

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
# Queries issued against the News MCP server (GNews).
# Each tuple: (query_string, NewsCategory)
# 9 queries across distinct topic buckets — maximises variety in the daily article pool.
# hours=24 (set in discover_news) ensures only today's articles are returned.
AI_SEARCH_QUERIES = [
    ("artificial intelligence LLM agentic AI model",              NewsCategory.AI_TECHNOLOGY),
    ("artificial intelligence finance investment funding fintech", NewsCategory.AI_BUSINESS),
    ("AI enterprise automation business productivity",             NewsCategory.AI_BUSINESS),
    ("AI jobs employment automation workforce reskilling",         NewsCategory.AI_JOBS),
    ("AI regulation policy governance AI Act",                     NewsCategory.AI_POLICY),
    ("AI product launch release announcement",                     NewsCategory.AI_PRODUCTS),
    # 3 additional queries added to surface fresh articles Jev hasn't seen before
    ("AI chip semiconductor Nvidia GPU datacenter",                NewsCategory.AI_TECHNOLOGY),
    ("Anthropic OpenAI Google DeepMind model release",             NewsCategory.AI_TECHNOLOGY),
    ("AI acquisition merger startup funding round",                NewsCategory.AI_BUSINESS),
]


# ── Shared LangGraph state ────────────────────────────────────────────────────

class NewsWorkflowState(TypedDict):
    run_id: str

    # News discovery
    raw_articles: list[dict]
    deduplicated_articles: list[dict]
    selected_articles: list[dict]

    # PageIndex
    pageindex_documents: list[dict]

    # Jev decision signals
    jev_persona_hints: list[str]     # from jev_prefilter — persona suggestions from article text
    jev_active_personas: list[str]   # from jev_route_personas — confirmed active persona values
    jev_prefilter_scores: dict       # from jev_prefilter — raw scores for the winning article (for post display)

    # Generation
    summaries: list[dict]
    persona_outputs: list[dict]

    # Evaluation
    evaluation_results: list[dict]
    retry_count: int

    # Approval
    approval_status: str   # "PENDING" | "APPROVED" | "REJECTED"

    # Publishing
    linkedin_results: list[dict]

    # Closed-loop Feedback & Optimization
    optimization_diagnosis: list[dict]
    story_mutations: list[dict]

    # Reach scoring (pre-publish gate)
    reach_scores: list[dict]      # one ReachScore.summary_line() dict per article

    # Run metadata
    workflow_status: str
    errors: list[str]


# ── Node implementations ──────────────────────────────────────────────────────

async def discover_news(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Architecture: queries are serialised with a 1.2 s gap to respect GNews free-tier
    rate limit (1 req/s per API key).  Firing all 9 in parallel caused 429s because
    each MCP pod applies its own internal delay — but all pods receive the requests
    simultaneously so the delay is useless against burst traffic.

    9 queries × 1.2 s = ~11 s total (vs serial ~9 s previously — comparable, no 429s).
    """
    run_id = state["run_id"]
    logger.info("[%s] discover_news started", run_id)

    trace = langfuse_trace(
        name="daily_news_workflow",
        run_id=run_id,
        input={"queries": len(AI_SEARCH_QUERIES)},
        tags=["workflow", "discover_news"],
        metadata={"run_id": run_id},
    )

    client = NewsMCPClient()
    errors: list[str] = []
    # Serialise GNews requests — free tier enforces 1 req/s per API key.
    # A Semaphore(1) + 1.2 s delay between acquisitions keeps us safely under the limit.
    _gnews_sem = asyncio.Semaphore(1)

    async def _search_one(query: str, category: NewsCategory) -> list[dict]:
        async with _gnews_sem:
            span = trace.span(
                name="news.search_latest",
                input={"query": query, "category": category.value},
            ) if trace else None
            try:
                results = await client.search_latest(
                    query=query,
                    hours=168,  # 7-day window — maximises article pool on free-tier GNews
                    limit=10,   # 10 results per query (free-tier cap)
                    category=category.value,
                )
                found = results.get("articles", [])
                if span:
                    span.end(output={"count": len(found)})
                return found
            except Exception as exc:  # noqa: BLE001
                logger.warning("news search failed for %s: %s", query, exc)
                errors.append(f"news.search_latest failed for '{query}': {exc}")
                if span:
                    span.end(output={"error": str(exc)}, level="ERROR")
                return []
            finally:
                # Stagger requests — 1.2 s between each GNews API call
                await asyncio.sleep(1.2)

    all_results: list[list[dict]] = await asyncio.gather(
        *[_search_one(q, c) for q, c in AI_SEARCH_QUERIES]
    )
    articles: list[dict] = [a for batch in all_results for a in batch]

    logger.info("[%s] discovered %d raw articles", run_id, len(articles))
    if trace:
        trace.update(output={"raw_articles": len(articles)})

    combined_errors = list(state["errors"]) + errors
    return {**state, "raw_articles": articles, "errors": combined_errors, "workflow_status": "DISCOVERED"}


def _normalise_url(url: str) -> str:
    """
    Canonicalise a URL for deduplication comparison.
    Strips scheme (http/https), www prefix, and trailing slash.
    'https://www.bbc.com/news/ai/' → 'bbc.com/news/ai'
    """
    url = url.lower().strip()
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\.', '', url)
    url = url.rstrip('/')
    return url


def _normalise_title(title: str) -> str:
    """
    Normalise a headline for similarity comparison.
    Lowercases, strips punctuation/extra spaces, removes common filler words.
    'OpenAI Releases GPT-5: What You Need to Know!' → 'openai releases gpt5 need know'
    """
    title = title.lower()
    title = re.sub(r'[^\w\s]', '', title)      # strip punctuation
    title = re.sub(r'\s+', ' ', title).strip() # collapse whitespace
    stop = {
        'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
        'of', 'for', 'with', 'by', 'from', 'as', 'is', 'are', 'was',
        'were', 'be', 'been', 'will', 'that', 'this', 'it', 'its',
        'you', 'your', 'what', 'how', 'why', 'who', 'all', 'says',
        'said', 'new', 'can', 'has', 'have', 'had', 'about', 'up',
    }
    tokens = [w for w in title.split() if w not in stop and len(w) > 1]
    return ' '.join(tokens)


def _title_similarity(a: str, b: str) -> float:
    """
    Jaccard similarity over word tokens of two normalised titles.
    Returns 0.0–1.0.  Values ≥ TITLE_SIMILARITY_THRESHOLD are considered duplicates.
    Pure stdlib — no external dependencies.
    """
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


# Tuned against real BBC/Reuters headline pairs: synonyms (releases/launches,
# improved/enhanced) reduce Jaccard overlap to ~0.57.  0.55 captures these
# cross-source near-duplicates while keeping a safe gap from genuinely different
# stories (which score ≤ 0.20 in practice).
_TITLE_SIMILARITY_THRESHOLD = 0.55


async def deduplicate(state: NewsWorkflowState) -> NewsWorkflowState:
    run_id = state["run_id"]
    raw    = state["raw_articles"]

    # ── Pass 1: exact dedup by normalised URL ─────────────────────────────────
    # Also compute content_hash (title+url) for downstream idempotency.
    # URL normalisation catches http vs https, www prefix, trailing slash variants
    # of the same article served by the same source.
    seen_url: set[str] = set()
    seen_hash: set[str] = set()
    unique: list[dict] = []
    for article in raw:
        norm_url = _normalise_url(article.get("url", ""))
        content_hash = hashlib.md5(
            (article.get("title", "") + article.get("url", "")).encode()
        ).hexdigest()
        article["content_hash"] = content_hash
        if norm_url not in seen_url and content_hash not in seen_hash:
            seen_url.add(norm_url)
            seen_hash.add(content_hash)
            unique.append(article)

    after_url = len(unique)

    # ── Pass 2: cross-run dedup — drop anything published today already ───────
    unique = published_store.filter_unpublished(unique)
    after_store = len(unique)

    # ── Pass 3: title-similarity dedup — catch same story from different sources
    # Two articles covering the same event will have different URLs and article_ids
    # but nearly identical headlines.  Jaccard similarity over normalised tokens
    # clusters them; only the first representative per cluster is kept.
    norm_titles: list[str] = [_normalise_title(a.get("title", "")) for a in unique]
    kept_indices: list[int] = []
    for i, title_i in enumerate(norm_titles):
        is_dup = False
        for j in kept_indices:
            if _title_similarity(title_i, norm_titles[j]) >= _TITLE_SIMILARITY_THRESHOLD:
                logger.info(
                    "[%s] dedup pass3: dropping near-duplicate '%s' (similar to '%s', sim=%.2f)",
                    run_id,
                    unique[i].get("title", "")[:80],
                    unique[j].get("title", "")[:80],
                    _title_similarity(title_i, norm_titles[j]),
                )
                is_dup = True
                break
        if not is_dup:
            kept_indices.append(i)

    unique = [unique[i] for i in kept_indices]
    after_similarity = len(unique)

    logger.info(
        "[%s] deduplicated: %d raw → %d url-unique → %d unpublished-today → %d title-unique",
        run_id, len(raw), after_url, after_store, after_similarity,
    )

    if after_similarity == 0:
        logger.warning(
            "[%s] deduplicate: no articles remain after all dedup passes — nothing to do",
            run_id,
        )

    # ── Input Guardrail validation pass ───────────────────────────────────────
    guarded_articles: list[dict] = []
    for art in unique:
        check = InputGuardrail.inspect_article(art)
        if not check.is_safe:
            logger.warning(
                "[%s] InputGuardrail BLOCKED article url=%s violations=%s",
                run_id, art.get("url"), check.violations,
            )
            state["errors"].append(f"InputGuardrail dropped '{art.get('title', '')[:40]}': {check.violations}")
            continue
        if check.sanitized_text:
            art["content"] = check.sanitized_text
        guarded_articles.append(art)

    logger.info(
        "[%s] input guardrail: %d passed / %d inspected",
        run_id, len(guarded_articles), len(unique),
    )

    return {**state, "deduplicated_articles": guarded_articles, "workflow_status": "DEDUPLICATED"}


async def fetch_articles(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Architecture: all article fetches fire concurrently (bounded by Semaphore(10)).
    Was serial (30 × ~500ms = ~15s). Now parallel (~2s for 30 articles).
    """
    client = NewsMCPClient()
    sem = asyncio.Semaphore(10)  # respect upstream rate limits

    async def _fetch_one(article: dict) -> dict:
        async with sem:
            try:
                full = await client.fetch_article(article.get("url", ""))
                return {**article, **full}
            except Exception as exc:  # noqa: BLE001
                logger.warning("fetch failed for %s: %s", article.get("url"), exc)
                return article

    enriched = list(await asyncio.gather(
        *[_fetch_one(a) for a in state["deduplicated_articles"][:30]]
    ))
    return {**state, "selected_articles": enriched, "workflow_status": "FETCHED"}


async def index_pageindex(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Architecture: all document indexing calls fire concurrently (bounded by Semaphore(10)).
    Was serial (30 × ~300ms = ~9s). Now parallel (~1s for 30 documents).
    """
    client = PageIndexMCPClient()
    sem = asyncio.Semaphore(10)
    errors: list[str] = []

    async def _index_one(article: dict) -> dict | None:
        async with sem:
            try:
                return await client.index_document(
                    document_id=article["article_id"],
                    title=article.get("title", ""),
                    content=article.get("content", ""),
                    source_url=article.get("url", ""),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("pageindex.index_document failed: %s", exc)
                errors.append(f"pageindex failed for {article.get('article_id')}: {exc}")
                return None

    results = await asyncio.gather(*[_index_one(a) for a in state["selected_articles"]])
    documents = [d for d in results if d is not None]

    combined_errors = list(state["errors"]) + errors
    return {**state, "pageindex_documents": documents, "errors": combined_errors, "workflow_status": "INDEXED"}


# ── Jev node wrappers ─────────────────────────────────────────────────────────
# jev_prefilter_articles and jev_route_personas are imported from jev_agents.
# They are registered directly in the graph below.
# Wrapper aliases make graph registration explicit.

async def jev_prefilter(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Graph node: Jev scores all fetched articles, picks the best one.
    Replaces the old select_stories[:1] hard-cut.
    Populates state.jev_persona_hints as a warm signal for jev_router.
    """
    return await jev_prefilter_articles(state)


async def find_angle(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Graph node: Stage 5 — Jev identifies the content opportunity angle.
    Runs after summarize, before jev_router.
    Writes content_opportunity into jev_prefilter_scores for publisher.
    """
    return await jev_find_angle(state)


async def jev_router(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Graph node: Jev decides which personas are relevant for this article.
    Runs after find_angle so it has access to structured NewsSummary + content_opportunity.
    Populates state.jev_active_personas consumed by generate_personas.
    """
    return await jev_route_personas(state)


async def summarize(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Architecture — two levels of parallelism:

    1. Per-article: PageIndex fetch + JudgmentAgent fire concurrently (both are
       independent of each other).  MediaStoryteller starts right after judgment
       is ready (it needs judgment boundaries).  SummaryAgent is the final pass.

    2. Across articles: all articles in selected_articles are processed
       concurrently via asyncio.gather — each article is a fully independent
       coroutine.

    Old: 3 articles × ~10s serial = ~30s
    New: max(article1, article2, article3) ≈ ~10s — 3× throughput improvement.
    """
    run_id = state["run_id"]
    all_jev_scores: dict = state.get("jev_prefilter_scores") or {}

    # Build agents once — they are stateless and safe to share across coroutines
    storyteller    = MediaStorytellerAgent()
    judgment_agent = JudgmentAgent()
    agent          = SummaryAgent()
    pi_client      = PageIndexMCPClient()
    errors: list[str] = []

    async def _summarize_one(article: dict) -> dict | None:
        aid = article.get("article_id", "")
        jev_scores = all_jev_scores.get(aid)

        try:
            r_sentiment, r_stats, r_tag = resolve_sentiment(article, jev_scores)
            logger.info(
                "[%s] sentiment resolved article=%s provider=%s label=%s",
                run_id, aid, r_stats.get("provider", "unknown"), r_sentiment,
            )

            # ── Phase 1: PageIndex fetch + Judgment Analysis in parallel ──────
            # These two are completely independent so run them concurrently.
            sections_resp, judgment = await asyncio.gather(
                pi_client.get_relevant_sections(
                    document_id=aid,
                    question="key business and technology facts",
                ),
                judgment_agent.analyze(
                    article_id=aid,
                    title=article.get("title", ""),
                    source=article.get("source", ""),
                    content=article.get("content", ""),
                    pageindex_sections="",   # judgment uses raw content; sections enrich pass 2
                    run_id=run_id,
                ),
            )
            sections_text = sections_resp.get("sections_text", "")
            logger.info(
                "[%s] judgment analysis article=%s facts=%d claims=%d uncertainties=%d",
                run_id, aid,
                len(judgment.facts),
                len(judgment.reported_claims),
                len(judgment.uncertainties),
            )

            # ── Phase 2: Media Storyteller (needs judgment boundaries) ────────
            story = await storyteller.extract_story(
                article_id=aid,
                title=article.get("title", ""),
                source=article.get("source", ""),
                source_url=article.get("url", ""),
                content=article.get("content", ""),
                pageindex_sections=sections_text,
                jev_scores=jev_scores,
                judgment=judgment,
                run_id=run_id,
            )
            logger.info(
                "[%s] story extracted article=%s style=%s hook_len=%d",
                run_id, aid, story.narrative_style, len(story.hook),
            )

            # ── Phase 3: Structured Summary (needs story + judgment) ──────────
            summary = await agent.summarize(
                article_id=aid,
                title=article.get("title", ""),
                source=article.get("source", ""),
                source_url=article.get("url", ""),
                content=article.get("content", ""),
                pageindex_sections=sections_text,
                run_id=run_id,
                sentiment=r_sentiment,
                sentiment_stats=r_stats,
                ai_tag=r_tag,
                jev_scores={**(jev_scores or {}), "judgment": judgment.model_dump()},
                story=story,
            )
            return summary.model_dump()

        except Exception as exc:  # noqa: BLE001
            logger.error("summarize failed for %s: %s", aid, exc)
            errors.append(f"summarize failed: {exc}")
            return None

    # Process all articles concurrently — each is fully independent
    results = await asyncio.gather(*[_summarize_one(a) for a in state["selected_articles"]])
    summaries = [s for s in results if s is not None]

    combined_errors = list(state["errors"]) + errors
    logger.info("[%s] summarised %d articles", run_id, len(summaries))
    return {**state, "summaries": summaries, "errors": combined_errors, "workflow_status": "SUMMARIZED"}


async def generate_personas(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Runs persona LLM agents only for the Jev-selected active personas.
    Falls back to all four if jev_active_personas is empty.

    Architecture: PageIndex fetch + persona generation run concurrently across
    all articles via asyncio.gather. Was serial (N articles × ~15s = ~45s).
    Now parallel (~15s regardless of article count).

    On retry cycles (retry_count > 0), extracts failure_reasons from the
    previous evaluation_results and injects them into the persona prompt as
    avoid_phrases so the LLM has an explicit signal about what to avoid on retry.
    """
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    factory = PersonaAgentFactory()
    pi_client = PageIndexMCPClient()
    errors: list[str] = []

    # Jev-selected personas — fall back to all if missing
    active_values: list[str] = state.get("jev_active_personas", [])
    if active_values:
        try:
            active_personas = [PersonaType(v) for v in active_values]
        except ValueError:
            logger.warning("[%s] invalid jev_active_personas values %s — running all", run_id, active_values)
            active_personas = list(PersonaType)
    else:
        active_personas = list(PersonaType)

    # On retry: collect all failure_reasons from the previous eval cycle so the
    # generator can see exactly which phrases were banned and avoid them.
    # Extract bare quoted phrases from judge critiques like:
    #   "llm_judge: The post contains the banned phrase 'the real question' ..."
    # so the LLM gets the concise signal, not the full verbose critique.
    import re as _re_local
    avoid_phrases: list[str] = []
    if state.get("retry_count", 0) > 0:
        for r in state.get("evaluation_results", []):
            for reason in r.get("failure_reasons", []):
                # Extract all single-quoted phrases from the reason string
                quoted = _re_local.findall(r"'([^']+)'", reason)
                if quoted:
                    avoid_phrases.extend(quoted)
                else:
                    # Fallback: strip "llm_judge: " prefix and use full reason
                    avoid_phrases.append(reason.removeprefix("llm_judge: ").strip())
        avoid_phrases = list(dict.fromkeys(avoid_phrases))  # deduplicate, preserve order
        if avoid_phrases:
            logger.info(
                "[%s] generate_personas retry=%d — injecting %d avoid_phrases into prompts",
                run_id, state["retry_count"], len(avoid_phrases),
            )

    logger.info("[%s] generate_personas: running %s", run_id, [p.value for p in active_personas])

    async def _generate_one(summary_dict: dict) -> dict | None:
        summary = NewsSummary(**summary_dict)
        try:
            sections_resp = await pi_client.get_relevant_sections(
                document_id=summary.article_id,
                question="jobs, policy, business, and technology evidence",
            )
            evidence = sections_resp.get("sections_text", "")
            persona_set = await factory.generate_all(
                summary, evidence, run_id=run_id, personas=active_personas,
                avoid_phrases=avoid_phrases or None,
            )
            return persona_set.model_dump()
        except Exception as exc:  # noqa: BLE001
            logger.error("persona generation failed for %s: %s", summary.article_id, exc, exc_info=True)
            errors.append(f"personas failed: {exc}")
            return None

    raw_outputs = await asyncio.gather(*[_generate_one(s) for s in state["summaries"]])
    persona_outputs = [p for p in raw_outputs if p is not None]

    combined_errors = list(state["errors"]) + errors
    return {**state, "persona_outputs": persona_outputs, "errors": combined_errors, "workflow_status": "PERSONAS_GENERATED"}


async def linkedin_optimize(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    LinkedIn Skills optimization layer — runs after persona generation, before evaluation.

    Applies three passes in sequence:
      Pass 1 — Hook Selector: scores and optionally replaces the opening hook
               using 2026 formula heuristics (number-first, contrarian, false-binary).
      Pass 2 — Humanizer: deterministic scrub of AI-vocabulary density, reveal bridges,
               staccato fragments, and engagement-bait closers.
      Pass 3 — Audit: LLM-backed scoring of hook_strength, commentability,
               ai_style_density, cta_quality, algorithm_compliance.

    Stores optimization_audit in state for the evaluation judge to use as
    additional scoring signals. Falls back gracefully — never blocks the pipeline.

    Contract:
      - NEVER changes: persona identities, factual claims, source URLs, debate structure,
        comic caption, or header.
      - MAY change: opening hook, AI-vocab density, fragment runs, reveal bridges, CTA phrasing.
    """
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    s = get_settings()

    # Skip if LLM not configured (e.g. local dev without gateway)
    if not s.llm_base_url:
        logger.info("[%s] linkedin_optimize: LLM_BASE_URL not set — skipping", run_id)
        return {**state, "workflow_status": "LI_OPTIMIZED"}

    try:
        optimizer = LinkedInSkillsOptimizer()
    except Exception as exc:
        logger.warning("[%s] linkedin_optimize: init failed (%s) — skipping", run_id, exc)
        return {**state, "workflow_status": "LI_OPTIMIZED"}

    publisher = PublisherAgent()
    new_persona_outputs: list[dict] = []
    audit_results: list[dict] = []

    for summary_dict, persona_dict in zip(state["summaries"], state["persona_outputs"]):
        try:
            from daily_news.models.persona import PersonaSetOutput
            summary  = NewsSummary(**summary_dict)
            personas = PersonaSetOutput(**persona_dict)

            # Compose current post text so the optimizer can see it holistically
            jev_scores = {}
            for r in state.get("jev_scores", []):
                if r.get("article_id") == summary.article_id:
                    jev_scores = r
                    break
            post_text = publisher._compose_main_post(summary, personas, jev_scores)

            # Extract story metadata for hook selection
            story = persona_dict.get("story") or {}
            if isinstance(story, dict):
                central_tension = story.get("media_host_synthesis", "") or story.get("perspective", "")
                story_style = story.get("narrative_style", "ai_debate")
            else:
                central_tension = ""
                story_style = "ai_debate"

            result = await optimizer.optimize(
                post_text=post_text,
                headline=summary.headline,
                central_tension=central_tension,
                story_style=story_style,
                run_id=run_id,
            )

            # Store audit scores in persona_dict so evaluate() can read them
            enriched = {**persona_dict, "linkedin_audit": {
                "hook_strength":       result.audit.hook_strength,
                "commentability":      result.audit.commentability,
                "ai_style_density":    result.audit.ai_style_density,
                "cta_quality":         result.audit.cta_quality,
                "algorithm_compliance": result.audit.algorithm_compliance,
                "overall":             result.audit.overall,
                "hook_formula":        result.audit.hook_formula_used,
                "hook_was_replaced":   result.hook_was_replaced,
                "blockers":            result.audit.blockers,
                "warnings":            result.audit.warnings,
            }}
            new_persona_outputs.append(enriched)
            audit_results.append(enriched["linkedin_audit"])

            logger.info(
                "[%s] linkedin_optimize article=%s hook=%.2f comment=%.2f ai_density=%.2f overall=%.2f formula=%s",
                run_id, summary.article_id,
                result.audit.hook_strength, result.audit.commentability,
                result.audit.ai_style_density, result.audit.overall,
                result.audit.hook_formula_used,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] linkedin_optimize failed for article: %s", run_id, exc)
            new_persona_outputs.append(persona_dict)
            audit_results.append({})

    return {**state, "persona_outputs": new_persona_outputs, "workflow_status": "LI_OPTIMIZED"}


async def evaluate(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Architecture: all article evaluations fire concurrently via asyncio.gather.
    Was serial (3 articles × ~10s judge call = ~30s). Now parallel (~10s regardless).
    The EvaluationAgent is stateless — safe to share across concurrent coroutines.
    """
    from daily_news.models.persona import PersonaSetOutput
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    agent = EvaluationAgent()
    errors: list[str] = []

    async def _evaluate_one(summary_dict: dict, persona_dict: dict) -> dict | None:
        summary = NewsSummary(**summary_dict)
        personas = PersonaSetOutput(**persona_dict)
        source_text = next(
            (a.get("content", "") for a in state["selected_articles"]
             if a["article_id"] == summary.article_id),
            "",
        )
        trace = langfuse_trace(
            name="evaluate",
            run_id=f"{run_id}:eval:{summary.article_id}",
            input={"article_id": summary.article_id},
            tags=["evaluate"],
            metadata={"run_id": run_id},
        )
        try:
            linkedin_audit = persona_dict.get("linkedin_audit") if isinstance(persona_dict, dict) else None
            result = await agent.evaluate(summary, personas, source_text, run_id=run_id,
                                          linkedin_audit=linkedin_audit)
            if trace:
                trace.update(output={
                    "decision":      result.decision.value,
                    "factuality":    result.factuality,
                    "groundedness":  result.groundedness,
                    "hallucination": result.hallucination,
                    "policy_check":  result.policy_check,
                })
            logger.info(
                "[%s] eval article=%s decision=%s factuality=%.2f groundedness=%.2f hallucination=%.2f",
                run_id, summary.article_id, result.decision.value,
                result.factuality, result.groundedness, result.hallucination,
            )
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            logger.error("evaluation failed for %s: %s", summary.article_id, exc)
            errors.append(f"evaluation failed: {exc}")
            if trace:
                trace.update(output={"error": str(exc)}, level="ERROR")
            return None

    raw_results = await asyncio.gather(
        *[_evaluate_one(s, p) for s, p in zip(state["summaries"], state["persona_outputs"])]
    )
    results = [r for r in raw_results if r is not None]

    any_regen = any(r.get("decision") == EvaluationDecision.REGENERATE.value for r in results)
    new_retry = state.get("retry_count", 0) + (1 if any_regen else 0)

    combined_errors = list(state["errors"]) + errors
    return {**state, "evaluation_results": results, "retry_count": new_retry, "errors": combined_errors, "workflow_status": "EVALUATED"}


async def score_reach(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Pre-publish reach optimiser node.

    For each PASS article, composes the post text, scores it on 6 LinkedIn
    reach dimensions (0–100), and applies auto-repair if score < REACH_THRESHOLD.

    Stores scoring metadata in state["reach_scores"] for observability.
    Does NOT call LinkedIn — purely deterministic text analysis.
    """
    from daily_news.models.persona import PersonaSetOutput
    from daily_news.models.summary import NewsSummary

    run_id  = state["run_id"]
    agent   = ReachScoreAgent()
    scorer  = PublisherAgent()
    scores: list[dict] = []

    passed_ids = {
        r["article_id"]
        for r in state["evaluation_results"]
        if r["decision"] == EvaluationDecision.PASS.value
    }

    # Build updated persona_outputs list (repaired posts replace originals in state)
    updated_persona_outputs: list[dict] = list(state["persona_outputs"])

    for idx, (summary_dict, persona_dict) in enumerate(
        zip(state["summaries"], state["persona_outputs"])
    ):
        if summary_dict["article_id"] not in passed_ids:
            continue

        summary  = NewsSummary(**summary_dict)
        personas = PersonaSetOutput(**persona_dict)

        _all_jev  = state.get("jev_prefilter_scores") or {}
        jev_scores = _all_jev.get(summary.article_id) if isinstance(_all_jev, dict) else None

        # Compose the full post text (same call PublisherAgent.publish() will make)
        post_text = scorer._compose_main_post(summary, personas, jev_scores or {})
        rs        = agent.score(post_text)

        logger.info(
            "[%s] score_reach article=%s %s",
            run_id, summary.article_id, rs.summary_line(),
        )

        if rs.notes:
            for note in rs.notes:
                logger.debug("[%s] score_reach note: %s", run_id, note)

        score_record = {
            "article_id":      summary.article_id,
            "total":           rs.total,
            "verdict":         rs.verdict,
            "hook_strength":   rs.hook_strength,
            "specificity":     rs.specificity,
            "question_quality":rs.question_quality,
            "length_fit":      rs.length_fit,
            "bait_penalty":    rs.bait_penalty,
            "topic_coherence": rs.topic_coherence,
            "word_count":      rs.word_count,
            "hashtag_count":   rs.hashtag_count,
            "bait_hits":       rs.bait_hits,
            "notes":           rs.notes,
        }
        scores.append(score_record)

        if rs.verdict == "REVISE":
            logger.info(
                "[%s] score_reach: score %.0f below threshold %d — applying auto-repair",
                run_id, rs.total, REACH_THRESHOLD,
            )
            # Auto-repair is purely cosmetic text cleanup — no LLM call
            # The repaired text is stored back so PublisherAgent uses it directly.
            # We signal this by patching a marker into persona_outputs so the
            # publisher skips re-composition and uses the pre-repaired text.
            # (Implementation: publisher checks for "_repaired_post" key.)
            repaired = ReachScoreAgent.repair(post_text)
            rs2      = agent.score(repaired)
            logger.info(
                "[%s] score_reach: after repair %s",
                run_id, rs2.summary_line(),
            )
            updated_persona_outputs[idx] = {
                **persona_dict,
                "_repaired_post": repaired,
                "_reach_score_before": rs.total,
                "_reach_score_after":  rs2.total,
            }

    return {
        **state,
        "reach_scores":      scores,
        "persona_outputs":   updated_persona_outputs,
        "workflow_status":   "REACH_SCORED",
    }


async def publish(state: NewsWorkflowState) -> NewsWorkflowState:
    from daily_news.models.evaluation import EvaluationResult
    from daily_news.models.persona import PersonaSetOutput
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    agent = PublisherAgent()
    linkedin_results: list[dict] = []

    eval_by_article: dict[str, EvaluationResult] = {}
    for r in state["evaluation_results"]:
        try:
            eval_by_article[r["article_id"]] = EvaluationResult(**r)
        except Exception:
            pass

    passed_ids = {
        r["article_id"]
        for r in state["evaluation_results"]
        if r["decision"] == EvaluationDecision.PASS.value
    }

    # ── Bulk pre-claim ALL passed articles before the first LinkedIn call ──────
    # This is the single write that prevents duplicate posts across:
    #   1. Same-run concurrent triggers (two HTTP requests land on the same pod)
    #   2. Re-triggers while a run is in-flight (pod restart kills bg task mid-loop)
    #   3. Same-day scheduled + manual runs
    # Each article is claimed atomically here.  publisher_agent rolls back
    # (unmark_published) only if its LinkedIn API call fails, keeping the store
    # in sync with what was actually sent.
    already_claimed: set[str] = set()
    for aid in list(passed_ids):
        if published_store.is_published(aid):
            logger.info(
                "[%s] publish: article=%s already claimed by another run — skipping",
                run_id, aid,
            )
            already_claimed.add(aid)
        else:
            published_store.mark_published(aid)
            logger.info("[%s] publish: pre-claimed article=%s in store", run_id, aid)

    # Drop articles claimed by a concurrent run
    passed_ids -= already_claimed

    for summary_dict, persona_dict in zip(state["summaries"], state["persona_outputs"]):
        if summary_dict["article_id"] not in passed_ids:
            continue
        summary = NewsSummary(**summary_dict)
        personas = PersonaSetOutput(**persona_dict)
        evaluation = eval_by_article.get(summary.article_id)

        span_trace = langfuse_trace(
            name="publish",
            run_id=f"{run_id}:publish:{summary.article_id}",
            input={"article_id": summary.article_id},
            tags=["publish"],
            metadata={"run_id": run_id},
        )
        # jev_prefilter_scores is now keyed by article_id (dict[article_id → scores])
        # so each article gets its own Jev scores, not always article #1's scores.
        _all_jev = state.get("jev_prefilter_scores") or {}
        jev_scores = _all_jev.get(summary.article_id) if isinstance(_all_jev, dict) else None
        try:
            result = await agent.publish(
                summary, personas, run_id,
                evaluation=evaluation,
                jev_scores=jev_scores or None,
            )
            if span_trace:
                comments = result.get("comments", {})
                span_trace.update(output={
                    "publication_key":  result.get("publication_key"),
                    "post_urn":         result.get("post_urn"),
                    "post_status":      result.get("post_status"),
                    "image_attached":   result.get("image_attached", False),
                    "comic_path":       result.get("comic_path", ""),
                    "comments_posted":  sum(
                        1 for v in comments.values()
                        if v.get("status") in ("published", "mock")
                    ),
                    "comments_failed":  sum(
                        1 for v in comments.values()
                        if v.get("status") == "error"
                    ),
                })
            linkedin_results.append(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("publish failed for %s: %s", summary.article_id, exc)
            state["errors"].append(f"publish failed: {exc}")
            if span_trace:
                span_trace.update(output={"error": str(exc)}, level="ERROR")

    flush_langfuse()
    return {**state, "linkedin_results": linkedin_results, "workflow_status": "PUBLISHED"}


async def optimize_content(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Closed-loop Content Optimization Node.

    Runs after publishing. Observes baseline/initial engagement or historical engagement,
    diagnoses the structural component strengths/weaknesses (hook, storytelling, audience,
    perspective, dialogue, question), and generates Story Mutations for learning and
    calibrating future Jev angle recommendations.
    """
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    optimizer = ContentOptimizerAgent()
    li_client = LinkedInMCPClient()

    diagnoses: list[dict] = []
    all_mutations: list[dict] = []

    for pub_res, summary_dict in zip(state.get("linkedin_results", []), state.get("summaries", [])):
        post_urn = pub_res.get("post_urn", "")
        article_id = summary_dict.get("article_id", "")
        summary = NewsSummary(**summary_dict)

        # 1. Fetch available engagement analytics via LinkedIn client
        analytics_raw = await li_client.get_post_analytics(post_urn) if post_urn else {}
        metrics = EngagementMetrics(
            post_urn=post_urn,
            article_id=article_id,
            impressions=analytics_raw.get("impressions", 0),
            reactions=analytics_raw.get("reactions", 0),
            comments=analytics_raw.get("comments", 0),
            reposts=analytics_raw.get("reposts", 0),
            engagement_rate=analytics_raw.get("engagement_rate", 0.0),
        )

        # 2. Diagnose performance & structural weaknesses
        post_text = summary_dict.get("summary", "")
        diagnosis = await optimizer.diagnose_performance(metrics, post_text, run_id=run_id)
        diagnoses.append(diagnosis.model_dump())

        logger.info(
            "[%s] ContentOptimizer: article=%s weakest=%s recommendation=%s",
            run_id, article_id, diagnosis.weakest_component, diagnosis.actionable_recommendation,
        )

        # 3. Generate story mutations based on diagnosis for learning loop
        evidence_text = "\n".join(summary.key_points)
        mutations = await optimizer.mutate_story(summary, diagnosis, evidence=evidence_text, run_id=run_id)
        all_mutations.extend([m.model_dump() for m in mutations])

    return {
        **state,
        "optimization_diagnosis": diagnoses,
        "story_mutations": all_mutations,
        "workflow_status": "OPTIMIZED",
    }


# ── Routing ───────────────────────────────────────────────────────────────────

def route_evaluation(state: NewsWorkflowState) -> Literal["publish", "generate_personas", "__end__"]:
    """
    Architecture change: on REGENERATE, loop back only to generate_personas
    (not all the way back to find_angle + summarize).

    Summaries and Jev intelligence signals are deterministic for a given article —
    re-running them produces identical output and wastes ~8–15s of LLM budget per retry.
    Only persona generation changes between retries (avoid_phrases is already injected
    by generate_personas via state["evaluation_results"]).

    Old loop: evaluate → find_angle → summarize → jev_router → generate_personas → evaluate
    New loop: evaluate → generate_personas → evaluate
    """
    results = state.get("evaluation_results", [])
    if not results:
        return "__end__"

    decisions = [r["decision"] for r in results]
    retry_count = state.get("retry_count", 0)

    if EvaluationDecision.REGENERATE.value in decisions:
        if retry_count < MAX_RETRIES:
            logger.info(
                "[%s] REGENERATE decision — retry %d/%d (personas only, skipping summarize)",
                state["run_id"], retry_count, MAX_RETRIES,
            )
            return "generate_personas"
        logger.warning("[%s] max retries (%d) exceeded — publishing PASS items only", state["run_id"], MAX_RETRIES)
        return "publish"

    if all(d == EvaluationDecision.PASS.value for d in decisions):
        return "publish"

    # HUMAN_REVIEW or BLOCK mixed in — publishing agent filters by passed_ids
    return "publish"


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_daily_news_graph():
    """
    Graph topology after architectural fixes:

    Normal path (unchanged):
      START → discover_news → deduplicate → fetch_articles → index_pageindex
            → jev_prefilter → find_angle → summarize → jev_router
            → generate_personas → evaluate → score_reach → publish
            → optimize_content → END

    Retry path (narrowed — was looping back to find_angle):
      evaluate → generate_personas   (personas only, summaries reused)

    This means a retry no longer re-runs find_angle + summarize (~8–15s of
    wasted LLM budget). Only persona generation is re-attempted with
    avoid_phrases injected from the failed evaluation.
    """
    graph = StateGraph(NewsWorkflowState)

    graph.add_node("discover_news",     discover_news)
    graph.add_node("deduplicate",       deduplicate)
    graph.add_node("fetch_articles",    fetch_articles)
    graph.add_node("index_pageindex",   index_pageindex)
    graph.add_node("jev_prefilter",     jev_prefilter)
    graph.add_node("find_angle",        find_angle)       # Stage 5: Jev Context DNA & Missing Angle
    graph.add_node("summarize",         summarize)        # Stage 4: Judgment Analysis + Storyteller
    graph.add_node("jev_router",        jev_router)       # Dynamic Persona Routing based on Context DNA
    graph.add_node("generate_personas", generate_personas)
    graph.add_node("linkedin_optimize", linkedin_optimize) # LinkedIn Skills: Hook + Humanizer + Audit
    graph.add_node("evaluate",          evaluate)         # Judge gate: MCP scores + Qwen critic @ 0.1
    graph.add_node("score_reach",       score_reach)      # Pre-publish Reach Optimizer & Unicode Bold Formatter
    graph.add_node("publish",           publish)          # Safe LinkedIn Distribution
    graph.add_node("optimize_content",  optimize_content) # Closed-loop Strategy Memory & Mutation Feedback

    graph.add_edge(START,               "discover_news")
    graph.add_edge("discover_news",     "deduplicate")
    graph.add_edge("deduplicate",       "fetch_articles")
    graph.add_edge("fetch_articles",    "index_pageindex")
    graph.add_edge("index_pageindex",   "jev_prefilter")
    graph.add_edge("jev_prefilter",     "find_angle")
    graph.add_edge("find_angle",        "summarize")
    graph.add_edge("summarize",         "jev_router")
    graph.add_edge("jev_router",        "generate_personas")
    graph.add_edge("generate_personas", "linkedin_optimize")
    graph.add_edge("linkedin_optimize", "evaluate")

    graph.add_conditional_edges(
        "evaluate",
        route_evaluation,
        {
            "publish":          "score_reach",      # route through reach scorer before publish
            "generate_personas": "generate_personas", # narrow retry — personas only, no re-summarize
            "__end__":          END,
        },
    )
    graph.add_edge("score_reach",      "publish")
    graph.add_edge("publish",          "optimize_content")
    graph.add_edge("optimize_content", END)

    return graph.compile()


# Module-level compiled graph
daily_news_graph = build_daily_news_graph()


def make_initial_state(run_id: str | None = None) -> NewsWorkflowState:
    return NewsWorkflowState(
        run_id=run_id or f"RUN-{uuid.uuid4().hex[:12].upper()}",
        raw_articles=[],
        deduplicated_articles=[],
        selected_articles=[],
        pageindex_documents=[],
        jev_persona_hints=[],
        jev_active_personas=[],
        jev_prefilter_scores={},
        summaries=[],
        persona_outputs=[],
        evaluation_results=[],
        retry_count=0,
        approval_status="PENDING",
        linkedin_results=[],
        reach_scores=[],
        optimization_diagnosis=[],
        story_mutations=[],
        workflow_status="STARTED",
        errors=[],
    )
