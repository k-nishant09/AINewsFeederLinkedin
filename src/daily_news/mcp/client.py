"""
MCP HTTP client — calls MCP servers via their POST /call REST endpoint.

Each MCP server exposes:
    POST /call
    Body:    {"tool": "<name>", "arguments": {...}}
    Returns: {"result": <tool output>}

This is simpler and more reliable than the MCP SDK streaming transport.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from daily_news.config.settings import get_settings

logger = logging.getLogger(__name__)


class MCPHTTPClient:
    """
    Calls a single MCP server via POST /call.
    base_url should be the server root, e.g. "http://news-mcp:8000"
    (the /call path is appended automatically).
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        # Strip trailing /mcp if settings were set with it
        self._base = base_url.rstrip("/").removesuffix("/mcp")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """
        Invoke a tool on the MCP server.
        Returns the value of {"result": ...} from the response.
        Raises RuntimeError on tool error, httpx.HTTPStatusError on HTTP error.
        """
        body = {"tool": tool, "arguments": arguments}
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(
                f"{self._base}/call",
                headers=self._headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            raise RuntimeError(f"MCP tool error [{tool}]: {data['error']}")
        result = data.get("result")
        # LinkedIn MCP wraps errors inside {"result": {"error": "..."}} when the
        # top-level response is HTTP 200 but the tool itself failed (e.g. oversize
        # post before Fix C).  Surface those as RuntimeError so callers see them.
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(f"MCP tool inner error [{tool}]: {result['error']}")
        return result


class MCPClientFactory:
    """Returns per-server MCPHTTPClient instances."""

    def __init__(self) -> None:
        s = get_settings()
        token = s.mcp_auth_token
        self.news      = MCPHTTPClient(s.news_mcp_url, token)
        self.pageindex = MCPHTTPClient(s.pageindex_mcp_url, token)
        self.evaluation = MCPHTTPClient(s.evaluation_mcp_url, token)
        self.linkedin  = MCPHTTPClient(s.linkedin_mcp_url, token)


_instance: MCPClientFactory | None = None


def mcp_factory() -> MCPClientFactory:
    """Lazy singleton — safe to call at import time."""
    global _instance
    if _instance is None:
        _instance = MCPClientFactory()
    return _instance
