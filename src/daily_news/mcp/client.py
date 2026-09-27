"""
MCP HTTP client — calls MCP servers via their POST /call REST endpoint.

Each MCP server exposes:
    POST /call
    Body:    {"tool": "<name>", "arguments": {...}}
    Returns: {"result": <tool output>}

Architecture change: MCPHTTPClient now owns a single persistent httpx.AsyncClient
with a connection pool. This eliminates the per-call TCP+TLS handshake overhead
that was adding ~200–800ms per call (50+ calls per run = 15–40s wasted).
The factory singleton keeps the client alive for the process lifetime.
Call mcp_factory().aclose() at process shutdown to drain the pool gracefully.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from daily_news.config.settings import get_settings

logger = logging.getLogger(__name__)

# Connection pool limits — tuned for concurrent MCP calls:
#   max_connections:           total sockets across all hosts
#   max_keepalive_connections: idle sockets to keep warm per host
#   keepalive_expiry:          evict idle sockets after 30s
_POOL_LIMITS = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)


class MCPHTTPClient:
    """
    Calls a single MCP server via POST /call.
    base_url should be the server root, e.g. "http://news-mcp:8000"
    (the /call path is appended automatically).

    A single persistent httpx.AsyncClient is reused across all calls so that
    TCP connections are pooled and HTTP/1.1 keep-alive is honoured.
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        # Strip trailing /mcp if settings were set with it
        self._base = base_url.rstrip("/").removesuffix("/mcp")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

        # Persistent client — one per MCP server, shared across all tool calls.
        # verify=False retained to match existing cluster cert behaviour.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            verify=False,
            limits=_POOL_LIMITS,
        )

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """
        Invoke a tool on the MCP server.
        Returns the value of {"result": ...} from the response.
        Raises RuntimeError on tool error, httpx.HTTPStatusError on HTTP error.
        """
        body = {"tool": tool, "arguments": arguments}
        resp = await self._client.post(
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

    async def aclose(self) -> None:
        """Drain the connection pool. Call at process shutdown."""
        await self._client.aclose()


class MCPClientFactory:
    """Returns per-server MCPHTTPClient instances."""

    def __init__(self) -> None:
        s = get_settings()
        token = s.mcp_auth_token
        self.news       = MCPHTTPClient(s.news_mcp_url,       token)
        self.pageindex  = MCPHTTPClient(s.pageindex_mcp_url,  token)
        self.evaluation = MCPHTTPClient(s.evaluation_mcp_url, token)
        self.linkedin   = MCPHTTPClient(s.linkedin_mcp_url,   token)

    async def aclose(self) -> None:
        """Close all pooled connections. Safe to call more than once."""
        for client in (self.news, self.pageindex, self.evaluation, self.linkedin):
            await client.aclose()


_instance: MCPClientFactory | None = None


def mcp_factory() -> MCPClientFactory:
    """Lazy singleton — safe to call at import time."""
    global _instance
    if _instance is None:
        _instance = MCPClientFactory()
    return _instance


# ── JevClient singleton ───────────────────────────────────────────────────────
# Architecture fix: JevClient was instantiated fresh inside every graph node
# (jev_prefilter, jev_find_angle, jev_route_personas, EvaluationAgent.__init__)
# causing 4× cold TCP+TLS handshakes to the Jev gateway per run (~300-800ms each).
# Sharing one persistent client reuses the connection pool across all nodes.
# The factory pattern mirrors mcp_factory() — thread-safe for asyncio single-thread.

_jev_instance: "JevClient | None" = None  # type: ignore[name-defined]


def jev_singleton() -> "JevClient":  # type: ignore[name-defined]
    """
    Process-scoped JevClient singleton.
    Returns a new instance when Jev is not configured (jev_base_url is empty) —
    callers must still honour JEV_ENABLED before making real calls.
    """
    global _jev_instance
    if _jev_instance is None:
        from daily_news.mcp.jev_client import JevClient  # avoid circular import
        _jev_instance = JevClient()
    return _jev_instance


async def close_jev_singleton() -> None:
    """Drain the shared Jev connection pool. Call at process shutdown."""
    global _jev_instance
    if _jev_instance is not None:
        try:
            await _jev_instance.aclose()
        except Exception:  # noqa: BLE001
            pass
        _jev_instance = None
