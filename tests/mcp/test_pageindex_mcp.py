"""MCP server tests — pageindex document operations."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def pageindex_client():
    from mcp_servers.pageindex_mcp.server import _app
    return TestClient(_app)


def test_pageindex_health(pageindex_client):
    resp = pageindex_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
