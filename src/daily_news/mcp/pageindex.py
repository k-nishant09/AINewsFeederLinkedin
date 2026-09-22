"""PageIndex MCP client — direct httpx calls to pageindex.* tools."""
from __future__ import annotations

from typing import Any

from daily_news.mcp.client import mcp_factory


class PageIndexMCPClient:

    async def index_document(
        self,
        document_id: str,
        title: str,
        content: str,
        source_url: str,
    ) -> dict[str, Any]:
        return await mcp_factory().pageindex.call(
            "pageindex_index_document",
            {"document_id": document_id, "title": title, "content": content, "source_url": source_url},
        )

    async def get_relevant_sections(
        self,
        document_id: str,
        question: str,
    ) -> dict[str, Any]:
        return await mcp_factory().pageindex.call(
            "pageindex_get_relevant_sections",
            {"document_id": document_id, "question": question},
        )
