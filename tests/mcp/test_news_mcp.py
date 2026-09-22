"""
Tests for the GNews-backed News MCP server.

Covers:
  - health endpoint
  - _normalise_article mapping
  - _mock_articles fallback (no API key)
  - _from_timestamp generates a valid ISO-8601 string
  - news_search_latest mock path
  - news_search_ai_tech mock path
  - news_search_ai_finance mock path
  - news_search_by_category with AI_FINANCE category
  - news_top_headlines_technology mock path
  - news_fetch_article mock URL guard
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def news_client():
    from mcp_servers.news_mcp.server import _app
    return TestClient(_app)


# ── Health ────────────────────────────────────────────────────────────────────

def test_news_mcp_health(news_client):
    resp = news_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["server"] == "news-mcp"
    assert data["provider"] == "gnews"
    assert "api_key_set" in data
    assert "max_per_request" in data


# ── Unit: helpers ─────────────────────────────────────────────────────────────

def test_make_article_id_stable():
    from mcp_servers.news_mcp.server import _make_article_id
    id1 = _make_article_id("https://example.com/article-1")
    id2 = _make_article_id("https://example.com/article-1")
    assert id1 == id2
    assert id1.startswith("news-")
    assert len(id1) == 17   # "news-" + 12 hex chars


def test_from_timestamp_format():
    from mcp_servers.news_mcp.server import _from_timestamp
    ts = _from_timestamp(24)
    # Must be ISO-8601 UTC with Z suffix
    assert ts.endswith("Z")
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    assert dt.year >= 2024


def test_normalise_article_maps_fields():
    from mcp_servers.news_mcp.server import _normalise_article
    raw = {
        "title": "AI Breakthrough",
        "description": "Short desc",
        "content": "Full content here",
        "url": "https://example.com/ai-news",
        "image": "https://example.com/img.jpg",
        "publishedAt": "2025-09-21T06:00:00Z",
        "source": {"name": "TechCrunch", "url": "https://techcrunch.com"},
    }
    result = _normalise_article(raw)
    assert result["title"] == "AI Breakthrough"
    assert result["source"] == "TechCrunch"
    assert result["source_url"] == "https://techcrunch.com"
    assert result["published_at"] == "2025-09-21T06:00:00Z"
    assert result["content"] == "Full content here"
    assert result["language"] == "en"
    assert result["article_id"].startswith("news-")


def test_normalise_article_falls_back_to_description():
    from mcp_servers.news_mcp.server import _normalise_article
    raw = {
        "title": "Test",
        "description": "Only desc",
        "content": "",       # empty content → fall back to description
        "url": "https://example.com/x",
        "publishedAt": "2025-09-21T06:00:00Z",
        "source": {"name": "X"},
    }
    result = _normalise_article(raw)
    assert result["content"] == "Only desc"


def test_mock_articles_returns_two_items():
    from mcp_servers.news_mcp.server import _mock_articles
    articles = _mock_articles("LLM agentic AI")
    assert len(articles) == 2
    titles = [a["title"] for a in articles]
    assert any("Tech" in t for t in titles)
    assert any("Finance" in t for t in titles)


# ── Integration: MCP tool calls via mock (no real GNews HTTP) ─────────────────

@pytest.mark.asyncio
async def test_news_search_latest_returns_mock_when_no_key(monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "")
    import importlib
    import mcp_servers.news_mcp.server as srv
    importlib.reload(srv)

    result = await srv.news_search_latest(
        query="artificial intelligence", hours=24, limit=3
    )
    assert "articles" in result
    assert "total" in result
    assert "query" in result
    assert result["query"] == "artificial intelligence"
    # Mock should return 2 articles
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_news_search_ai_tech_returns_mock_when_no_key(monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "")
    import importlib
    import mcp_servers.news_mcp.server as srv
    importlib.reload(srv)

    result = await srv.news_search_ai_tech(hours=24, limit=3)
    assert result["category"] == "AI_TECHNOLOGY"
    assert result["total"] >= 1


@pytest.mark.asyncio
async def test_news_search_ai_finance_returns_mock_when_no_key(monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "")
    import importlib
    import mcp_servers.news_mcp.server as srv
    importlib.reload(srv)

    result = await srv.news_search_ai_finance(hours=24, limit=3)
    assert result["category"] == "AI_FINANCE"
    assert result["total"] >= 1


@pytest.mark.asyncio
async def test_news_search_by_category_ai_finance(monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "")
    import importlib
    import mcp_servers.news_mcp.server as srv
    importlib.reload(srv)

    result = await srv.news_search_by_category(
        category="AI_FINANCE", hours=24, limit=3
    )
    assert result["category"] == "AI_FINANCE"
    assert "articles" in result


@pytest.mark.asyncio
async def test_news_search_by_category_unknown_falls_back(monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "")
    import importlib
    import mcp_servers.news_mcp.server as srv
    importlib.reload(srv)

    result = await srv.news_search_by_category(category="UNKNOWN_CAT")
    assert "articles" in result


@pytest.mark.asyncio
async def test_news_top_headlines_technology_mock(monkeypatch):
    monkeypatch.setenv("GNEWS_API_KEY", "")
    import importlib
    import mcp_servers.news_mcp.server as srv
    importlib.reload(srv)

    result = await srv.news_top_headlines_technology(limit=3)
    assert result["topic"] == "technology"
    assert "articles" in result


@pytest.mark.asyncio
async def test_news_fetch_article_mock_url():
    from mcp_servers.news_mcp.server import news_fetch_article
    result = await news_fetch_article("https://example.com/mock-ai-tech-1")
    assert result["fetch_status"] == "mock"
    assert "article_id" in result


# ── Category query coverage ───────────────────────────────────────────────────

def test_all_categories_have_queries():
    from mcp_servers.news_mcp.server import CATEGORY_QUERIES
    expected = {
        "AI_TECHNOLOGY", "AI_FINANCE", "AI_BUSINESS", "AI_JOBS",
        "AI_POLICY", "AI_PRODUCTS", "AI_RESEARCH",
        "AI_INFRASTRUCTURE", "AI_SECURITY",
    }
    assert expected == set(CATEGORY_QUERIES.keys())


def test_category_queries_non_empty():
    from mcp_servers.news_mcp.server import CATEGORY_QUERIES
    for cat, query in CATEGORY_QUERIES.items():
        assert query.strip(), f"Empty query for category {cat}"
