"""MCP server tests — evaluation server."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def eval_client():
    from mcp_servers.evaluation_mcp.server import _app
    return TestClient(_app)


def test_evaluation_health(eval_client):
    resp = eval_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
