"""Integration tests — FastAPI endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import os
    # Provide minimum required env vars for settings
    os.environ.setdefault("LLM_API_KEY", "test-key")
    os.environ.setdefault("NEWS_MCP_URL", "http://localhost:8101/mcp")
    os.environ.setdefault("PAGEINDEX_MCP_URL", "http://localhost:8102/mcp")
    os.environ.setdefault("EVALUATION_MCP_URL", "http://localhost:8103/mcp")
    os.environ.setdefault("LINKEDIN_MCP_URL", "http://localhost:8104/mcp")
    os.environ.setdefault("MCP_AUTH_TOKEN", "test-token")

    from daily_news.api.main import app
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_ready(client):
    resp = client.get("/ready")
    assert resp.status_code == 200


def test_workflow_trigger_returns_run_id(client):
    resp = client.post("/workflow/daily-news")
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["status"] == "STARTED"


def test_workflow_status_not_found(client):
    resp = client.get("/workflow/NONEXISTENT-RUN")
    assert resp.status_code == 404
