"""
LangGraph Daily News Workflow — with Langfuse tracing on every node
====================================================================

State machine:

  START
    └─► discover_news
          └─► deduplicate
                └─► fetch_articles
                      └─► index_pageindex
                            └─► select_stories
                                  └─► summarize
                                        └─► generate_personas
                                              └─► evaluate
                                                    ├─► REGENERATE ─► summarize  (max retries)
                                                    ├─► HUMAN_REVIEW ─► human_approval gate
                                                    └─► PASS
                                                          └─► publish
                                                                └─► END
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from daily_news.agents.evaluation_agent import EvaluationAgent
from daily_news.agents.persona_agent import PersonaAgentFactory
from daily_news.agents.publisher_agent import PublisherAgent
from daily_news.agents.summary_agent import SummaryAgent
from daily_news.mcp.news import NewsMCPClient
from daily_news.mcp.pageindex import PageIndexMCPClient
from daily_news.models.evaluation import EvaluationDecision
from daily_news.models.news import NewsCategory
from daily_news.observability.tracing import langfuse_trace, flush_langfuse

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
# Queries issued against the News MCP server (GNews backend).
# Each tuple: (query_string, NewsCategory)
AI_SEARCH_QUERIES = [
    ("artificial intelligence LLM agentic AI model",           NewsCategory.AI_TECHNOLOGY),
    ("artificial intelligence finance investment funding fintech", NewsCategory.AI_BUSINESS),
    ("AI enterprise automation business productivity",          NewsCategory.AI_BUSINESS),
    ("AI jobs employment automation workforce reskilling",      NewsCategory.AI_JOBS),
    ("AI regulation policy governance AI Act",                  NewsCategory.AI_POLICY),
    ("AI product launch release announcement",                  NewsCategory.AI_PRODUCTS),
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

    # Run metadata
    workflow_status: str
    errors: list[str]


# ── Node implementations ──────────────────────────────────────────────────────

async def discover_news(state: NewsWorkflowState) -> NewsWorkflowState:
    run_id = state["run_id"]
    logger.info("[%s] discover_news started", run_id)

    # Top-level Langfuse trace for the entire workflow run
    # All subsequent spans from agents share session_id=run_id
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
                hours=48,
                limit=20,
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


async def deduplicate(state: NewsWorkflowState) -> NewsWorkflowState:
    seen: set[str] = set()
    unique: list[dict] = []
    for article in state["raw_articles"]:
        content_hash = hashlib.md5(
            (article.get("title", "") + article.get("url", "")).encode()
        ).hexdigest()
        article["content_hash"] = content_hash
        if content_hash not in seen:
            seen.add(content_hash)
            unique.append(article)

    logger.info(
        "[%s] deduplicated: %d → %d",
        state["run_id"],
        len(state["raw_articles"]),
        len(unique),
    )
    return {**state, "deduplicated_articles": unique, "workflow_status": "DEDUPLICATED"}


async def fetch_articles(state: NewsWorkflowState) -> NewsWorkflowState:
    client = NewsMCPClient()
    enriched: list[dict] = []
    for article in state["deduplicated_articles"][:30]:  # cap at 30 for cost
        try:
            full = await client.fetch_article(article.get("url", ""))
            enriched.append({**article, **full})
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch failed for %s: %s", article.get("url"), exc)
            enriched.append(article)  # use shallow metadata if fetch fails

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


async def select_stories(state: NewsWorkflowState) -> NewsWorkflowState:
    # Select 1 article per run — summarise → 5 persona comments → publish in one shot.
    # Increase this to process more articles per CronJob invocation once pipeline is stable.
    selected = state["selected_articles"][:1]
    logger.info("[%s] selected %d story for summarisation", state["run_id"], len(selected))
    return {**state, "selected_articles": selected, "workflow_status": "SELECTED"}


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
            # run_id passed → Langfuse CallbackHandler created inside agent
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
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    factory = PersonaAgentFactory()
    pi_client = PageIndexMCPClient()
    persona_outputs: list[dict] = []

    for summary_dict in state["summaries"]:
        summary = NewsSummary(**summary_dict)
        try:
            sections_resp = await pi_client.get_relevant_sections(
                document_id=summary.article_id,
                question="jobs, policy, business, and technology evidence",
            )
            evidence = sections_resp.get("sections_text", "")
            # run_id passed → all 5 parallel persona traces share session_id=run_id
            persona_set = await factory.generate_all(summary, evidence, run_id=run_id)
            persona_outputs.append(persona_set.model_dump())
        except Exception as exc:  # noqa: BLE001
            logger.error("persona generation failed for %s: %s", summary.article_id, exc)
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
        # Trace evaluation scores as a Langfuse span on the run trace
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
            logger.info("[%s] eval article=%s decision=%s factuality=%.2f groundedness=%.2f hallucination=%.2f",
                        run_id, summary.article_id, result.decision.value,
                        result.factuality, result.groundedness, result.hallucination)
            results.append(result_dict)
        except Exception as exc:  # noqa: BLE001
            logger.error("evaluation failed for %s: %s", summary.article_id, exc)
            state["errors"].append(f"evaluation failed: {exc}")
            if trace:
                trace.update(output={"error": str(exc)}, level="ERROR")

    # Increment retry_count if any REGENERATE decisions so route_evaluation can cap the loop
    any_regen = any(r.get("decision") == EvaluationDecision.REGENERATE.value for r in results)
    new_retry = state.get("retry_count", 0) + (1 if any_regen else 0)

    return {**state, "evaluation_results": results, "retry_count": new_retry, "workflow_status": "EVALUATED"}


async def publish(state: NewsWorkflowState) -> NewsWorkflowState:
    from daily_news.models.evaluation import EvaluationResult
    from daily_news.models.persona import PersonaSetOutput
    from daily_news.models.summary import NewsSummary

    run_id = state["run_id"]
    agent = PublisherAgent()
    linkedin_results: list[dict] = []

    # Build evaluation lookup: article_id → EvaluationResult
    eval_by_article: dict[str, EvaluationResult] = {}
    for r in state["evaluation_results"]:
        try:
            eval_by_article[r["article_id"]] = EvaluationResult(**r)
        except Exception:
            pass  # malformed eval dict — publisher_agent will gate on None

    # Only publish items that PASSED evaluation
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
        try:
            result = await agent.publish(summary, personas, run_id, evaluation=evaluation)
            if span_trace:
                comments = result.get("comments", {})
                span_trace.update(output={
                    "publication_key":   result.get("publication_key"),
                    "post_urn":          result.get("post_urn"),
                    "post_status":       result.get("post_status"),
                    "comments_posted":   sum(
                        1 for v in comments.values()
                        if v.get("status") in ("published", "mock")
                    ),
                    "comments_failed":   sum(
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

    # Flush all pending Langfuse events for this run
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
            # retry_count is incremented inside the evaluate node on REGENERATE
            logger.info("[%s] REGENERATE decision — retry %d/%d", state["run_id"], retry_count, MAX_RETRIES)
            return "summarize"
        # Max retries exceeded — publish whatever passed, skip the rest
        logger.warning("[%s] max retries (%d) exceeded — publishing PASS items only", state["run_id"], MAX_RETRIES)
        return "publish"

    if all(d == EvaluationDecision.PASS.value for d in decisions):
        return "publish"

    # HUMAN_REVIEW or BLOCK mixed in — publishing agent filters by passed_ids internally
    return "publish"


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_daily_news_graph():
    graph = StateGraph(NewsWorkflowState)

    graph.add_node("discover_news", discover_news)
    graph.add_node("deduplicate", deduplicate)
    graph.add_node("fetch_articles", fetch_articles)
    graph.add_node("index_pageindex", index_pageindex)
    graph.add_node("select_stories", select_stories)
    graph.add_node("summarize", summarize)
    graph.add_node("generate_personas", generate_personas)
    graph.add_node("evaluate", evaluate)
    graph.add_node("publish", publish)

    graph.add_edge(START, "discover_news")
    graph.add_edge("discover_news", "deduplicate")
    graph.add_edge("deduplicate", "fetch_articles")
    graph.add_edge("fetch_articles", "index_pageindex")
    graph.add_edge("index_pageindex", "select_stories")
    graph.add_edge("select_stories", "summarize")
    graph.add_edge("summarize", "generate_personas")
    graph.add_edge("generate_personas", "evaluate")

    graph.add_conditional_edges(
        "evaluate",
        route_evaluation,
        {
            "publish": "publish",
            "summarize": "summarize",
            "__end__": END,
        },
    )
    graph.add_edge("publish", END)

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
        summaries=[],
        persona_outputs=[],
        evaluation_results=[],
        retry_count=0,
        approval_status="PENDING",
        linkedin_results=[],
        workflow_status="STARTED",
        errors=[],
    )
