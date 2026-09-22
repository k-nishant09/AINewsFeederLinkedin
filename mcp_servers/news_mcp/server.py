"""
News MCP Server — AI news search and article fetch via GNews API.

Rate limit note (free plan)
────────────────────────────
  GNews free plan allows 1 request/second and 100 requests/day per key.
  Set GNEWS_REQUEST_DELAY_MS=1100 (default) to stay within the rate limit.

  Key rotation — automatic failover
  ───────────────────────────────────
  Two keys are supported:
    GNEWS_API_KEY   — primary key (env var, required)
    GNEWS_API_KEY_2 — secondary key (env var, optional)

  On every GNews call the active key is tried first.  If the response is
  HTTP 403 (quota exhausted or invalid key) the server automatically
  switches to the other key for that call and all subsequent calls in this
  process lifetime.  If both keys return 403 the error is surfaced to the
  caller as usual.

  Key state is in-process — it resets on pod restart.  The GNews free plan
  resets quotas at midnight UTC so a scheduled pod restart is not required.

API reference: https://docs.gnews.io
Endpoint used: GET https://gnews.io/api/v4/search
               GET https://gnews.io/api/v4/top-headlines

GNews response shape
─────────────────────
{
  "totalArticles": 123,
  "articles": [
    {
      "title": "...",
      "description": "...",
      "content": "...",        # truncated at 250 chars on free plan
      "url": "...",
      "image": "...",
      "publishedAt": "2025-09-21T06:00:00Z",
      "source": { "name": "...", "url": "..." }
    }
  ]
}

Free tier limits
─────────────────
  100 requests/day per key  ·  max=10 articles per request
  Paid plans: up to 100 articles per request, higher rate limits

Category mapping
────────────────
  GNews supports the "topic" param for top-headlines:
    breaking-news, world, nation, business, technology, entertainment,
    sports, science, health
  For search, we use curated query strings per AI category.
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
# Primary key — injected from OpenShift Secret / .env as GNEWS_API_KEY
# Secondary key — injected as GNEWS_API_KEY_2 (fallback when primary quota exhausted)
_GNEWS_KEY_1 = os.environ.get("GNEWS_API_KEY", "")
_GNEWS_KEY_2 = os.environ.get("GNEWS_API_KEY_2", "")

# Build the rotation pool — skip empty strings
_GNEWS_KEY_POOL: list[str] = [k for k in [_GNEWS_KEY_1, _GNEWS_KEY_2] if k]

# Index into _GNEWS_KEY_POOL for the currently active key.
# Mutated in-place by _rotate_key() when a 403 is received.
_active_key_index: int = 0


def _get_active_key() -> str:
    """Return the currently active GNews API key, or '' if no keys configured."""
    if not _GNEWS_KEY_POOL:
        return ""
    return _GNEWS_KEY_POOL[_active_key_index]


def _rotate_key(exhausted_key: str) -> str | None:
    """
    Switch to the next key in the pool after a 403 on `exhausted_key`.

    Returns the new active key, or None if all keys are exhausted.
    Logs a warning so operators can see when rotation occurs.
    """
    global _active_key_index
    current_key = _get_active_key()

    # Only rotate if the 403 was for the key we're actually using
    # (guards against concurrent calls racing on the same rotation)
    if exhausted_key != current_key:
        return current_key  # already rotated by another coroutine

    next_index = _active_key_index + 1
    if next_index >= len(_GNEWS_KEY_POOL):
        logger.error(
            "GNews: all %d key(s) quota exhausted — no more keys to try. "
            "Quota resets at midnight UTC.",
            len(_GNEWS_KEY_POOL),
        )
        return None

    _active_key_index = next_index
    logger.warning(
        "GNews key #%d quota exhausted (403) — rotating to key #%d",
        next_index,       # 1-based: was using key N
        next_index + 1,   # now using key N+1
    )
    return _GNEWS_KEY_POOL[_active_key_index]


GNEWS_SEARCH_URL       = "https://gnews.io/api/v4/search"
GNEWS_HEADLINES_URL    = "https://gnews.io/api/v4/top-headlines"

# GNews free plan max = 10; paid plans support up to 100
GNEWS_MAX_PER_REQUEST  = int(os.environ.get("GNEWS_MAX_PER_REQUEST", "10"))

# Delay between successive GNews requests (ms). Free plan: 1 req/sec → 1100ms.
# Set to 0 to disable (e.g. paid plan with higher rate limits).
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


def _normalise_article(item: dict[str, Any]) -> dict[str, Any]:
    """Map a GNews article object to our internal NewsArticle shape."""
    source = item.get("source", {})
    return {
        "article_id":   _make_article_id(item.get("url", "")),
        "title":        item.get("title", ""),
        "url":          item.get("url", ""),
        "source":       source.get("name", ""),
        "source_url":   source.get("url", ""),
        "published_at": item.get("publishedAt", ""),
        "content":      item.get("content", "") or item.get("description", ""),
        "description":  item.get("description", ""),
        "image":        item.get("image", ""),
        "content_hash": hashlib.md5(item.get("url", "").encode()).hexdigest(),
        "language":     "en",
    }


# ── GNews API call ────────────────────────────────────────────────────────────

async def _gnews_search(
    query: str,
    hours: int = 24,
    max_results: int = 10,
    lang: str = "en",
    country: str = "us",
) -> list[dict[str, Any]]:
    """
    Search GNews for articles matching `query` published within the last `hours`.

    Automatically rotates to the secondary key (GNEWS_API_KEY_2) on HTTP 403.

    Ref: https://docs.gnews.io/#search-endpoint
    Params:
      q        — search query (supports AND, OR, NOT, exact phrases)
      apikey   — GNews API key
      lang     — language code (en, fr, de …)
      country  — country code (us, gb, in …)
      max      — number of results (1–10 free, 100 paid)
      from     — ISO-8601 datetime lower bound
      in       — fields to search: title, description, content (comma-separated)
      sortby   — publishedAt | relevance
    """
    active_key = _get_active_key()
    if not active_key:
        logger.warning("No GNEWS_API_KEY configured — returning mock results for dev")
        return _mock_articles(query)

    # Rate-limit guard: free plan allows 1 req/sec
    if GNEWS_REQUEST_DELAY_MS > 0:
        import asyncio
        await asyncio.sleep(GNEWS_REQUEST_DELAY_MS / 1000)

    params: dict[str, Any] = {
        "q":       query,
        "apikey":  active_key,
        "lang":    lang,
        "country": country,
        "max":     min(max_results, GNEWS_MAX_PER_REQUEST),
        "from":    _from_timestamp(hours),
        "in":      "title,description,content",
        "sortby":  "publishedAt",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(GNEWS_SEARCH_URL, params=params)

        # On 403 (quota exhausted or invalid key) — rotate and retry once
        if resp.status_code == 403:
            fallback_key = _rotate_key(active_key)
            if fallback_key and fallback_key != active_key:
                params["apikey"] = fallback_key
                resp = await client.get(GNEWS_SEARCH_URL, params=params)

        resp.raise_for_status()
        data = resp.json()

    return [_normalise_article(a) for a in data.get("articles", [])]


async def _gnews_top_headlines(
    topic: str = "technology",
    max_results: int = 10,
    lang: str = "en",
    country: str = "us",
) -> list[dict[str, Any]]:
    """
    Fetch top headlines for a GNews topic.

    Automatically rotates to the secondary key (GNEWS_API_KEY_2) on HTTP 403.

    Ref: https://docs.gnews.io/#top-headlines-endpoint
    Supported topics: breaking-news, world, nation, business,
                      technology, entertainment, sports, science, health
    """
    active_key = _get_active_key()
    if not active_key:
        return _mock_articles(f"top-headlines:{topic}")

    if GNEWS_REQUEST_DELAY_MS > 0:
        import asyncio
        await asyncio.sleep(GNEWS_REQUEST_DELAY_MS / 1000)

    params: dict[str, Any] = {
        "topic":   topic,
        "apikey":  active_key,
        "lang":    lang,
        "country": country,
        "max":     min(max_results, GNEWS_MAX_PER_REQUEST),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(GNEWS_HEADLINES_URL, params=params)

        # On 403 — rotate and retry once
        if resp.status_code == 403:
            fallback_key = _rotate_key(active_key)
            if fallback_key and fallback_key != active_key:
                params["apikey"] = fallback_key
                resp = await client.get(GNEWS_HEADLINES_URL, params=params)

        resp.raise_for_status()
        data = resp.json()

    return [_normalise_article(a) for a in data.get("articles", [])]


def _mock_articles(query: str) -> list[dict[str, Any]]:
    """Return predictable mock articles when GNEWS_API_KEY is absent (CI / local dev)."""
    return [
        {
            "article_id":   "news-mock-0001",
            "title":        f"[MOCK] AI Tech News — {query[:50]}",
            "url":          "https://example.com/mock-ai-tech-1",
            "source":       "Mock Tech Source",
            "source_url":   "https://example.com",
            "published_at": datetime.now(tz=timezone.utc).isoformat(),
            "content":      "Mock AI technology article for local development without GNEWS_API_KEY.",
            "description":  "Mock description for AI tech news.",
            "image":        "",
            "content_hash": hashlib.md5(b"mock-tech-1").hexdigest(),
            "language":     "en",
        },
        {
            "article_id":   "news-mock-0002",
            "title":        f"[MOCK] AI Finance News — {query[:50]}",
            "url":          "https://example.com/mock-ai-finance-1",
            "source":       "Mock Finance Source",
            "source_url":   "https://example.com",
            "published_at": datetime.now(tz=timezone.utc).isoformat(),
            "content":      "Mock AI finance article for local development without GNEWS_API_KEY.",
            "description":  "Mock description for AI finance news.",
            "image":        "",
            "content_hash": hashlib.md5(b"mock-finance-1").hexdigest(),
            "language":     "en",
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
    Search GNews for the latest articles matching `query`.

    Uses GET https://gnews.io/api/v4/search with `from` set to
    `now - hours`.  `limit` is capped at GNEWS_MAX_PER_REQUEST
    (10 on free plan, 100 on paid).

    Example queries:
      "artificial intelligence enterprise AI"
      "AI finance investment funding"
      "LLM model release"
    """
    articles = await _gnews_search(query, hours=hours, max_results=limit,
                                   lang=lang, country=country)
    return {"articles": articles, "total": len(articles), "query": query}


@mcp.tool()
async def news_search_ai_tech(
    hours: int = 24,
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Fetch AI technology news — LLMs, agentic AI, model releases, research.

    Uses the curated AI_TECHNOLOGY query against GNews /search.
    """
    query = CATEGORY_QUERIES["AI_TECHNOLOGY"]
    articles = await _gnews_search(query, hours=hours, max_results=limit,
                                   lang=lang, country=country)
    return {"articles": articles, "total": len(articles), "category": "AI_TECHNOLOGY"}


@mcp.tool()
async def news_search_ai_finance(
    hours: int = 24,
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Fetch AI finance news — investment, funding rounds, market impact, fintech AI.

    Uses the curated AI_FINANCE query against GNews /search.
    """
    query = CATEGORY_QUERIES["AI_FINANCE"]
    articles = await _gnews_search(query, hours=hours, max_results=limit,
                                   lang=lang, country=country)
    return {"articles": articles, "total": len(articles), "category": "AI_FINANCE"}


@mcp.tool()
async def news_search_by_category(
    category: str,
    hours: int = 24,
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Search GNews by one of the predefined AI categories.

    Valid categories: AI_TECHNOLOGY, AI_FINANCE, AI_BUSINESS, AI_JOBS,
                      AI_POLICY, AI_PRODUCTS, AI_RESEARCH,
                      AI_INFRASTRUCTURE, AI_SECURITY
    """
    query = CATEGORY_QUERIES.get(category, "artificial intelligence")
    articles = await _gnews_search(query, hours=hours, max_results=limit,
                                   lang=lang, country=country)
    return {"articles": articles, "category": category, "total": len(articles)}


@mcp.tool()
async def news_top_headlines_technology(
    limit: int = 10,
    lang: str = "en",
    country: str = "us",
) -> dict:
    """
    Fetch top technology headlines from GNews.

    Uses GET https://gnews.io/api/v4/top-headlines?topic=technology
    Useful for discovering trending AI stories beyond keyword search.
    """
    articles = await _gnews_top_headlines(topic="technology",
                                          max_results=limit, lang=lang, country=country)
    return {"articles": articles, "total": len(articles), "topic": "technology"}


@mcp.tool()
async def news_fetch_article(url: str) -> dict:
    """
    Fetch full article content from a URL.
    Note: GNews already returns content (truncated at 250 chars on free plan).
    Production: replace body extraction with trafilatura for full text.
    """
    if not url or url.startswith("https://example.com/mock"):
        return {
            "url": url,
            "article_id": _make_article_id(url),
            "content": "Mock content for local development.",
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
        "url": url,
        "article_id": _make_article_id(url),
        "content": content,
        "fetch_status": "ok" if content else "failed",
    }


# ── Tool registry — direct call dispatch ─────────────────────────────────────
# Maps tool name → async function for use by POST /call
_TOOLS: dict[str, Any] = {
    "news_search_latest":          news_search_latest,
    "news.search_latest":          news_search_latest,   # alias
    "news_fetch_article":          news_fetch_article,
    "news.fetch_article":          news_fetch_article,   # alias
    "news_search_by_category":     news_search_by_category,
    "news.search_by_category":     news_search_by_category,
    "news_search_ai_tech":         news_search_ai_tech,
    "news_search_ai_finance":      news_search_ai_finance,
    "news_top_headlines_technology": news_top_headlines_technology,
}

# ── Health + app assembly ──────────────────────────────────────────────────────

_app = FastAPI()


@_app.get("/health")
def health():
    active_key = _get_active_key()
    return {
        "status":            "healthy",
        "server":            "news-mcp",
        "provider":          "gnews",
        "keys_configured":   len(_GNEWS_KEY_POOL),
        "active_key_index":  _active_key_index + 1,   # 1-based for humans
        "active_key_prefix": active_key[:8] + "..." if active_key else "none",
        "max_per_request":   GNEWS_MAX_PER_REQUEST,
        "tools":             list(_TOOLS.keys()),
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
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name!r}. Available: {list(_TOOLS)}")
    import inspect
    result = await fn(**arguments) if inspect.iscoroutinefunction(fn) else fn(**arguments)
    return {"result": result}


_app.mount("/mcp", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(_app, host="0.0.0.0", port=8000)
