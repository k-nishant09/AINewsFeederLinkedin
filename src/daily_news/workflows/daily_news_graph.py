"""
LangGraph Daily News Workflow — with Jev System One decision nodes
==================================================================

State machine:

  START
    └─► discover_news         9 GNews queries (hours=24) → ~27 fresh articles
          └─► deduplicate     3-pass dedup: URL-hash + PublishedStore + title-similarity
                └─► fetch_articles
                      └─► index_pageindex
                            └─► jev_prefilter         ← Jev Decision #1: scores all, picks top 1
                                  └─► summarize             LLM → structured summary (≤350 chars)
                                        └─► jev_router      ← Jev Decision #2: picks relevant personas
                                              └─► generate_personas  (active subset only, parallel)
                                                    └─► evaluate     ← Jev Decision #3: quality gating
                                                          ├─► REGENERATE ─► summarize  (max 2 retries)
                                                          ├─► BLOCK      ─► hard stop (PII / injection)
                                                          ├─► HUMAN_REVIEW ─► approval gate
                                                          └─► PASS
                                                                └─► score_reach  ← reach optimiser
                                                                      └─► publish  (3 dedup gates)
                                                                            └─► END

Jev integration points
──────────────────────
1. jev_prefilter   replaces blind [:2] article selection
2. jev_router      replaces always-run-all-four persona generation
3. EvaluationAgent uses JevClient.evaluate_content() → 9 quality questions

All three fall back gracefully when JEV_ENABLED=false or on network error.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Literal, TypedDict

from daily_news.agents.published_store import published_store

from langgraph.graph import END, START, StateGraph

from daily_news.agents.evaluation_agent import EvaluationAgent
from daily_news.agents.jev_agents import jev_prefilter_articles, jev_route_personas
from daily_news.agents.persona_agent import PersonaAgentFactory
from daily_news.agents.publisher_agent import PublisherAgent
from daily_news.agents.reach_score_agent import ReachScoreAgent, REACH_THRESHOLD
from daily_news.agents.summary_agent import SummaryAgent
from daily_news.mcp.news import NewsMCPClient
from daily_news.mcp.pageindex import PageIndexMCPClient
from daily_news.models.evaluation import EvaluationDecision
from daily_news.models.news import NewsCategory
from daily_news.models.persona import PersonaType
from daily_news.observability.tracing import langfuse_trace, flush_langfuse

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
# Queries issued against the News MCP server (GNews backend).
# Each tuple: (query_string, NewsCategory)
# 9 queries across distinct topic buckets — maximises variety in the daily article pool.
# hours=24 (set in discover_news) ensures only today's articles are returned, not stale GNews cache.
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

    # Reach scoring (pre-publish gate)
    reach_scores: list[dict]      # one ReachScore.summary_line() dict per article

    # Run metadata
    workflow_status: str
    errors: list[str]


# ── Node implementations ──────────────────────────────────────────────────────

async def discover_news(state: NewsWorkflowState) -> NewsWorkflowState:
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
    articles: list[dict] = []

    for query, category in AI_SEARCH_QUERIES:
        span = trace.span(name="news.search_latest", input={"query": query, "category": category.value}) if trace else None
        try:
            results = await client.search_latest(
                query=query,
                hours=24,   # 24h window — ensures today's articles, not stale GNews cache
                limit=10,   # GNews free-tier cap is 10 results per query
                category=category.value,
            )
            found = results.get("articles", [])
            articles.extend(found)
            if span:
                span.end(output={"count": len(found)})
        except Exception as exc:  # noqa: BLE001
            logger.warning("news search failed for %s: %s", query, exc)
            state["errors"].append(f"news.search_latest failed for '{query}': {exc}")
            if span:
                span.end(output={"error": str(exc)}, level="ERROR")

    logger.info("[%s] discovered %d raw articles", run_id, len(articles))
    if trace:
        trace.update(output={"raw_articles": len(articles)})
    return {**state, "raw_articles": articles, "workflow_status": "DISCOVERED"}


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

    return {**state, "deduplicated_articles": unique, "workflow_status": "DEDUPLICATED"}


async def fetch_articles(state: NewsWorkflowState) -> NewsWorkflowState:
    client = NewsMCPClient()
    enriched: list[dict] = []
    for article in state["deduplicated_articles"][:30]:  # cap at 30 for Jev scoring budget
        try:
            full = await client.fetch_article(article.get("url", ""))
            enriched.append({**article, **full})
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch failed for %s: %s", article.get("url"), exc)
            enriched.append(article)

    return {**state, "selected_articles": enriched, "workflow_status": "FETCHED"}


async def index_pageindex(state: NewsWorkflowState) -> NewsWorkflowState:
    client = PageIndexMCPClient()
    documents: list[dict] = []
    for article in state["selected_articles"]:
        try:
            doc = await client.index_document(
                document_id=article["article_id"],
                title=article.get("title", ""),
                content=article.get("content", ""),
                source_url=article.get("url", ""),
            )
            documents.append(doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pageindex.index_document failed: %s", exc)
            state["errors"].append(f"pageindex failed for {article.get('article_id')}: {exc}")

    return {**state, "pageindex_documents": documents, "workflow_status": "INDEXED"}


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


async def jev_router(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Graph node: Jev decides which personas are relevant for this article.
    Runs after summarize so it has access to structured NewsSummary fields.
    Populates state.jev_active_personas consumed by generate_personas.
    """
    return await jev_route_personas(state)


async def summarize(state: NewsWorkflowState) -> NewsWorkflowState:
    run_id = state["run_id"]
    agent = SummaryAgent()
    pi_client = PageIndexMCPClient()
    summaries: list[dict] = []

    for article in state["selected_articles"]:
        try:
            sections_resp = await pi_client.get_relevant_sections(
                document_id=article["article_id"],
                question="key business and technology facts",
            )
            sections_text = sections_resp.get("sections_text", "")
            summary = await agent.summarize(
                article_id=article["article_id"],
                title=article.get("title", ""),
                source=article.get("source", ""),
                source_url=article.get("url", ""),
                content=article.get("content", ""),
                pageindex_sections=sections_text,
                run_id=run_id,
            )
            summaries.append(summary.model_dump())
        except Exception as exc:  # noqa: BLE001
            logger.error("summarize failed for %s: %s", article.get("article_id"), exc)
            state["errors"].append(f"summarize failed: {exc}")

    logger.info("[%s] summarised %d articles", run_id, len(summaries))
    return {**state, "summaries": summaries, "workflow_status": "SUMMARIZED"}


async def generate_personas(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Runs persona LLM agents only for the Jev-selected active personas.
    Falls back to all five if jev_active_personas is empty.
    """
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    factory = PersonaAgentFactory()
    pi_client = PageIndexMCPClient()
    persona_outputs: list[dict] = []

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

    logger.info("[%s] generate_personas: running %s", run_id, [p.value for p in active_personas])

    for summary_dict in state["summaries"]:
        summary = NewsSummary(**summary_dict)
        try:
            sections_resp = await pi_client.get_relevant_sections(
                document_id=summary.article_id,
                question="jobs, policy, business, and technology evidence",
            )
            evidence = sections_resp.get("sections_text", "")
            persona_set = await factory.generate_all(
                summary, evidence, run_id=run_id, personas=active_personas
            )
            persona_outputs.append(persona_set.model_dump())
        except Exception as exc:  # noqa: BLE001
            logger.error("persona generation failed for %s: %s", summary.article_id, exc, exc_info=True)
            state["errors"].append(f"personas failed: {exc}")

    return {**state, "persona_outputs": persona_outputs, "workflow_status": "PERSONAS_GENERATED"}


async def evaluate(state: NewsWorkflowState) -> NewsWorkflowState:
    from daily_news.models.persona import PersonaSetOutput
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    agent = EvaluationAgent()
    results: list[dict] = []

    for summary_dict, persona_dict in zip(state["summaries"], state["persona_outputs"]):
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
            result = await agent.evaluate(summary, personas, source_text)
            if trace:
                trace.update(output={
                    "decision":      result.decision.value,
                    "factuality":    result.factuality,
                    "groundedness":  result.groundedness,
                    "hallucination": result.hallucination,
                    "policy_check":  result.policy_check,
                })
            result_dict = result.model_dump()
            logger.info(
                "[%s] eval article=%s decision=%s factuality=%.2f groundedness=%.2f hallucination=%.2f",
                run_id, summary.article_id, result.decision.value,
                result.factuality, result.groundedness, result.hallucination,
            )
            results.append(result_dict)
        except Exception as exc:  # noqa: BLE001
            logger.error("evaluation failed for %s: %s", summary.article_id, exc)
            state["errors"].append(f"evaluation failed: {exc}")
            if trace:
                trace.update(output={"error": str(exc)}, level="ERROR")

    any_regen = any(r.get("decision") == EvaluationDecision.REGENERATE.value for r in results)
    new_retry = state.get("retry_count", 0) + (1 if any_regen else 0)

    return {**state, "evaluation_results": results, "retry_count": new_retry, "workflow_status": "EVALUATED"}


async def score_reach(state: NewsWorkflowState) -> NewsWorkflowState:
    """
    Pre-publish reach optimiser node.

    For each PASS article, composes the post text, scores it on 6 LinkedIn
    reach dimensions (0–100), and applies auto-repair if score < REACH_THRESHOLD.

    Stores scoring metadata in state["reach_scores"] for observability.
    Does NOT call LinkedIn — purely deterministic text analysis.
    """
    from daily_news.models.evaluation import EvaluationResult
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


# ── Routing ───────────────────────────────────────────────────────────────────

def route_evaluation(state: NewsWorkflowState) -> Literal["publish", "summarize", "__end__"]:
    results = state.get("evaluation_results", [])
    if not results:
        return "__end__"

    decisions = [r["decision"] for r in results]
    retry_count = state.get("retry_count", 0)

    if EvaluationDecision.REGENERATE.value in decisions:
        if retry_count < MAX_RETRIES:
            logger.info("[%s] REGENERATE decision — retry %d/%d", state["run_id"], retry_count, MAX_RETRIES)
            return "summarize"
        logger.warning("[%s] max retries (%d) exceeded — publishing PASS items only", state["run_id"], MAX_RETRIES)
        return "publish"

    if all(d == EvaluationDecision.PASS.value for d in decisions):
        return "publish"

    # HUMAN_REVIEW or BLOCK mixed in — publishing agent filters by passed_ids
    return "publish"


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_daily_news_graph():
    graph = StateGraph(NewsWorkflowState)

    graph.add_node("discover_news",     discover_news)
    graph.add_node("deduplicate",       deduplicate)
    graph.add_node("fetch_articles",    fetch_articles)
    graph.add_node("index_pageindex",   index_pageindex)
    graph.add_node("jev_prefilter",     jev_prefilter)
    graph.add_node("summarize",         summarize)
    graph.add_node("jev_router",        jev_router)
    graph.add_node("generate_personas", generate_personas)
    graph.add_node("evaluate",          evaluate)
    graph.add_node("score_reach",       score_reach)   # pre-publish reach optimiser
    graph.add_node("publish",           publish)

    graph.add_edge(START,               "discover_news")
    graph.add_edge("discover_news",     "deduplicate")
    graph.add_edge("deduplicate",       "fetch_articles")
    graph.add_edge("fetch_articles",    "index_pageindex")
    graph.add_edge("index_pageindex",   "jev_prefilter")
    graph.add_edge("jev_prefilter",     "summarize")
    graph.add_edge("summarize",         "jev_router")
    graph.add_edge("jev_router",        "generate_personas")
    graph.add_edge("generate_personas", "evaluate")

    graph.add_conditional_edges(
        "evaluate",
        route_evaluation,
        {
            "publish":   "score_reach",   # route through reach scorer before publish
            "summarize": "summarize",
            "__end__":   END,
        },
    )
    graph.add_edge("score_reach", "publish")
    graph.add_edge("publish",     END)

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
        workflow_status="STARTED",
        errors=[],
    )
