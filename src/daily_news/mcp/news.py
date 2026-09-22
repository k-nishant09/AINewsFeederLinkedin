"""News MCP client — direct httpx calls to news.* tools."""
from __future__ import annotations

from typing import Any

from daily_news.mcp.client import mcp_factory


class NewsMCPClient:

    async def search_latest(
        self,
        query: str,
        hours: int = 24,
        limit: int = 50,
        category: str | None = None,
    ) -> dict[str, Any]:
        # news_search_latest tool signature: query, hours, limit, lang, country
        # 'category' is workflow metadata — not a tool parameter
        return await mcp_factory().news.call(
            "news_search_latest",
            {"query": query, "hours": hours, "limit": limit},
        )

    async def fetch_article(self, url: str) -> dict[str, Any]:
        return await mcp_factory().news.call(
            "news_fetch_article",
            {"url": url},
        )
