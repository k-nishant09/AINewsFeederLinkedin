"""
News MCP Server — GNews sole provider.

Provider
────────
  GNews  (GNEWS_API_KEY — set via env)
  API docs: https://docs.gnews.io
  Endpoint: GET https://gnews.io/api/v4/search
  Free plan: 100 requests/day, up to 10 articles/request
  Rate limit: 1 req/sec on free plan — use GNEWS_REQUEST_DELAY_MS (default 1100ms)

Pipeline role
─────────────
  GNews → normalised article corpus → PageIndex → Intelligent Analysis → LinkedIn

GNews response shape
────────────────────
{
  "totalArticles": 123,
  "articles": [
    {
      "title": "...", "description": "...", "content": "...",
      "url": "...", "image": "...", "publishedAt": "2025-09-21T06:00:00Z",
      "source": { "name": "...", "url": "..." }
    }
  ]
}

Rate limit notes
─────────────────
  GNews free: 1 req/sec, 100 requests/day.  GNEWS_REQUEST_DELAY_MS=1100 (default).
  Paid plans: higher limits — set GNEWS_REQUEST_DELAY_MS=0 to remove delay.

Category mapping
─────────────────
  CATEGORY_QUERIES maps our AI news categories to query strings used by GNews.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer as FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("News MCP Server")

# ── GNews config ──────────────────────────────────────────────────────────────
# Sole news provider.  Set GNEWS_API_KEY in env / OpenShift Secret.
# 32-char hex key from https://gnews.io/dashboard
_GNEWS_API_KEY         = os.environ.get("GNEWS_API_KEY", "")
GNEWS_SEARCH_URL       = "https://gnews.io/api/v4/search"
GNEWS_HEADLINES_URL    = "https://gnews.io/api/v4/top-headlines"
GNEWS_MAX_PER_REQUEST  = int(os.environ.get("GNEWS_MAX_PER_REQUEST", "10"))
GNEWS_REQUEST_DELAY_MS = int(os.environ.get("GNEWS_REQUEST_DELAY_MS", "1100"))


# ── Curated queries per AI news category ──────────────────────────────────────
CATEGORY_QUERIES: dict[str, str] = {
    # AI Technology — LLMs, agentic AI, models, foundational research
    "AI_TECHNOLOGY":     "artificial intelligence LLM agentic AI model",
    # AI Finance — market impact, investment, funding rounds, fintech AI
    "AI_FINANCE":        "artificial intelligence finance investment funding fintech",
    # AI Business — enterprise adoption, automation, productivity
    "AI_BUSINESS":       "AI enterprise automation business productivity",
    # AI Jobs — employment, displacement, reskilling, hiring
    "AI_JOBS":           "AI jobs employment automation workforce reskilling",
    # AI Policy — regulation, governance, AI Act, executive orders
    "AI_POLICY":         "AI regulation policy governance AI Act",
    # AI Products — product launches, updates, releases
    "AI_PRODUCTS":       "AI product launch release announcement",
    # AI Research — papers, breakthroughs, academic
    "AI_RESEARCH":       "AI research breakthrough paper academic",
    # AI Infrastructure — GPUs, data centres, cloud AI
    "AI_INFRASTRUCTURE": "AI GPU data centre cloud infrastructure NVIDIA",
    # AI Security — adversarial attacks, safety, red-teaming
    "AI_SECURITY":       "AI security safety red team adversarial",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_article_id(url: str) -> str:
    return "news-" + hashlib.md5(url.encode()).hexdigest()[:12]


def _from_timestamp(hours: int) -> str:
    """Return ISO-8601 datetime string for 'now minus hours' (GNews `from` param)."""
    dt = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_gnews_article(item: dict[str, Any]) -> dict[str, Any]:
    """Map a GNews article object to our internal NewsArticle shape."""
    source = item.get("source", {})
    url    = item.get("url", "")
    return {
        "article_id":      _make_article_id(url),
        "title":           item.get("title", ""),
        "url":             url,
        "source":          source.get("name", ""),
        "source_url":      source.get("url", ""),
        "published_at":    item.get("publishedAt", ""),
        "content":         item.get("content", "") or item.get("description", ""),
        "description":     item.get("description", ""),
        "image":           item.get("image", ""),
        "content_hash":    hashlib.md5(url.encode()).hexdigest(),
        "language":        "en",
        # Sentiment fields — populated downstream by the Intelligent News Agent
        # (sentiment_resolver → Jev inference) rather than from the API.
        "ai_tag":          None,
        "sentiment":       None,
        "sentiment_stats": None,
        "ai_region":       None,
        "ai_org":          None,
        "provider":        "gnews",
    }


# Backward-compat alias — keeps any code that imports _normalise_article working.
_normalise_article = _normalise_gnews_article


# ── GNews API calls ───────────────────────────────────────────────────────────

async def _gnews_search(
    query: str,
    hours: int = 24,
    max_results: int = 10,
    lang: str = "en",
    country: str = "us",
) -> list[dict[str, Any]]:
    """
    Search GNews for articles matching `query` published within the last `hours`.

    Ref: https://docs.gnews.io/#search-endpoint
    Params:
      q       — keyword query
      apikey  — GNews API key
      lang    — language code (e.g. "en")
      country — country code (e.g. "us")
      max     — max results per request (free plan cap: 10)
      from    — ISO-8601 lower bound for publishedAt
      in      — fields to search: title,description,content
      sortby  — sort order: publishedAt | relevance

    On HTTP 403 (quota exhausted or invalid key) logs an error and returns [].
    When no key is configured, returns mock articles for local dev / CI.
    """
    if not _GNEWS_API_KEY:
        logger.warning("No GNEWS_API_KEY configured — returning mock results for dev")
        return _mock_articles(query)

    if GNEWS_REQUEST_DELAY_MS > 0:
        import asyncio
        await asyncio.sleep(GNEWS_REQUEST_DELAY_MS / 1000)

    params: dict[str, Any] = {
        "q":       query,
        "apikey":  _GNEWS_API_KEY,
        "lang":    lang,
        "country": country,
        "max":     min(max_results, GNEWS_MAX_PER_REQUEST),
        "from":    _from_timestamp(hours),
        "in":      "title,description,content",
        "sortby":  "publishedAt",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(GNEWS_SEARCH_URL, params=params)
        if resp.status_code == 403:
            logger.error(
                "GNews key quota exhausted or invalid (403). "
                "Quota resets at midnight UTC."
            )
            return []
        resp.raise_for_status()
        data = resp.json()

    return [_normalise_gnews_article(a) for a in data.get("articles", [])]


async def _gnews_top_headlines(
    topic: str = "technology",
    max_results: int = 10,
    lang: str = "en",
    country: str = "us",
) -> list[dict[str, Any]]:
    """
    Fetch top headlines for a GNews topic.

    Ref: https://docs.gnews.io/#top-headlines-endpoint
    Supported topics: breaking-news, world, nation, business,
                      technology, entertainment, sports, science, health
    """
    if not _GNEWS_API_KEY:
        return _mock_articles(f"top-headlines:{topic}")

    if GNEWS_REQUEST_DELAY_MS > 0:
        import asyncio
        await asyncio.sleep(GNEWS_REQUEST_DELAY_MS / 1000)

    params: dict[str, Any] = {
        "topic":   topic,
        "apikey":  _GNEWS_API_KEY,
        "lang":    lang,
        "country": country,
        "max":     min(max_results, GNEWS_MAX_PER_REQUEST),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(GNEWS_HEADLINES_URL, params=params)
        if resp.status_code == 403:
            logger.error("GNews top-headlines: key quota exhausted (403).")
            return []
        resp.raise_for_status()
        data = resp.json()

    return [_normalise_gnews_article(a) for a in data.get("articles", [])]


def _mock_articles(query: str) -> list[dict[str, Any]]:
    """Return predictable mock articles when no API key is configured (CI / local dev)."""
    return [
        {
            "article_id":      "news-mock-0001",
            "title":           f"[MOCK] AI Tech News — {query[:50]}",
            "url":             "https://example.com/mock-ai-tech-1",
            "source":          "Mock Tech Source",
            "source_url":      "https://example.com",
            "published_at":    datetime.now(tz=timezone.utc).isoformat(),
            "content":         "Mock AI technology article for local development without API keys.",
            "description":     "Mock description for AI tech news.",
            "image":           "",
            "content_hash":    hashlib.md5(b"mock-tech-1").hexdigest(),
            "language":        "en",
            "ai_tag":          None,
            "sentiment":       None,
            "sentiment_stats": None,
            "ai_region":       None,
            "ai_org":          None,
            "provider":        "mock",
        },
        {
            "article_id":      "news-mock-0002",
            "title":           f"[MOCK] AI Finance News — {query[:50]}",
            "url":             "https://example.com/mock-ai-finance-1",
            "source":          "Mock Finance Source",
            "source_url":      "https://example.com",
            "published_at":    datetime.now(tz=timezone.utc).isoformat(),
            "content":         "Mock AI finance article for local development without API keys.",
            "description":     "Mock description for AI finance news.",
            "image":           "",
            "content_hash":    hashlib.md5(b"mock-finance-1").hexdigest(),
            "language":        "en",
            "ai_tag":          None,
            "sentiment":       None,
            "sentiment_stats": None,
            "ai_region":       None,
            "ai_org":          None,
            "provider":        "mock",
        },
    ]


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def news_search_latest(
    query: str,
    hours: int = 24,
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Search for the latest articles matching `query`.

    Provider: GNews (https://docs.gnews.io/#search-endpoint)

    Sentiment, impact, and novelty analysis is performed downstream by the
    Intelligent News Agent (Jev + sentiment_resolver) — not at fetch time.

    Example queries:
      "artificial intelligence enterprise AI"
      "AI finance investment funding"
      "LLM model release"
    """
    articles = await _gnews_search(
        query=query, hours=hours, max_results=limit,
        lang=lang, country=country,
    )
    return {"articles": articles, "total": len(articles), "query": query, "provider": "gnews"}


@mcp.tool()
async def news_search_ai_tech(
    hours: int = 24,
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Fetch AI technology news — LLMs, agentic AI, model releases, research.

    Uses the curated AI_TECHNOLOGY query via GNews.
    """
    query = CATEGORY_QUERIES["AI_TECHNOLOGY"]
    articles = await _gnews_search(
        query=query, hours=hours, max_results=limit,
        lang=lang, country=country,
    )
    return {"articles": articles, "total": len(articles), "category": "AI_TECHNOLOGY", "provider": "gnews"}


@mcp.tool()
async def news_search_ai_finance(
    hours: int = 24,
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Fetch AI finance news — investment, funding rounds, market impact, fintech AI.

    Uses the curated AI_FINANCE query via GNews.
    """
    query = CATEGORY_QUERIES["AI_FINANCE"]
    articles = await _gnews_search(
        query=query, hours=hours, max_results=limit,
        lang=lang, country=country,
    )
    return {"articles": articles, "total": len(articles), "category": "AI_FINANCE", "provider": "gnews"}


@mcp.tool()
async def news_search_by_category(
    category: str,
    hours: int = 24,
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Search by one of the predefined AI categories via GNews.

    Valid categories: AI_TECHNOLOGY, AI_FINANCE, AI_BUSINESS, AI_JOBS,
                      AI_POLICY, AI_PRODUCTS, AI_RESEARCH,
                      AI_INFRASTRUCTURE, AI_SECURITY
    """
    query = CATEGORY_QUERIES.get(category, "artificial intelligence")
    articles = await _gnews_search(
        query=query, hours=hours, max_results=limit,
        lang=lang, country=country,
    )
    return {"articles": articles, "category": category, "total": len(articles), "provider": "gnews"}


@mcp.tool()
async def news_top_headlines_technology(
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Fetch top technology headlines via GNews top-headlines endpoint.
    """
    articles = await _gnews_top_headlines(
        topic="technology", max_results=limit, lang=lang, country=country,
    )
    return {"articles": articles, "total": len(articles), "topic": "technology", "provider": "gnews"}


@mcp.tool()
async def news_fetch_article(url: str) -> dict:
    """
    Fetch full article content from a URL.

    Used to supplement GNews article content with the full HTML body
    (e.g. for PageIndex document indexing).
    """
    if not url or url.startswith("https://example.com/mock"):
        return {
            "url":          url,
            "article_id":   _make_article_id(url),
            "content":      "Mock content for local development.",
            "fetch_status": "mock",
        }

    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": "AI-Daily-News-Bot/1.0"},
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.text[:8000]
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch %s: %s", url, exc)
            content = ""

    return {
        "url":          url,
        "article_id":   _make_article_id(url),
        "content":      content,
        "fetch_status": "ok" if content else "failed",
    }


# ── Tool registry — direct call dispatch ──────────────────────────────────────
_TOOLS: dict[str, Any] = {
    "news_search_latest":            news_search_latest,
    "news.search_latest":            news_search_latest,          # alias
    "news_fetch_article":            news_fetch_article,
    "news.fetch_article":            news_fetch_article,          # alias
    "news_search_by_category":       news_search_by_category,
    "news.search_by_category":       news_search_by_category,
    "news_search_ai_tech":           news_search_ai_tech,
    "news_search_ai_finance":        news_search_ai_finance,
    "news_top_headlines_technology": news_top_headlines_technology,
}


# ── Health + app assembly ──────────────────────────────────────────────────────

_app = FastAPI()


@_app.get("/health")
def health():
    return {
        "status":                "healthy",
        "server":                "news-mcp",
        "provider":              "gnews",
        "gnews_api_key_set":     bool(_GNEWS_API_KEY),
        "gnews_max_per_request": GNEWS_MAX_PER_REQUEST,
        "tools":                 list(_TOOLS.keys()),
    }


@_app.post("/call")
async def call_tool(request: dict):
    """
    Simple REST tool dispatcher — no MCP streaming required.
    Body: {"tool": "<name>", "arguments": {...}}
    Returns: {"result": <tool output>} or {"error": "..."}
    """
    tool_name = request.get("tool", "")
    arguments = request.get("arguments", {})
    fn = _TOOLS.get(tool_name)
    if fn is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"Unknown tool: {tool_name!r}. Available: {list(_TOOLS)}",
        )
    import inspect
    result = await fn(**arguments) if inspect.iscoroutinefunction(fn) else fn(**arguments)
    return {"result": result}


_app.mount("/mcp", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(_app, host="0.0.0.0", port=8000)
