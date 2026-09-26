"""News article Pydantic models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NewsCategory(str, Enum):
    AI_TECHNOLOGY = "AI_TECHNOLOGY"
    AI_BUSINESS = "AI_BUSINESS"
    AI_JOBS = "AI_JOBS"
    AI_POLICY = "AI_POLICY"
    AI_PRODUCTS = "AI_PRODUCTS"
    AI_RESEARCH = "AI_RESEARCH"
    AI_INFRASTRUCTURE = "AI_INFRASTRUCTURE"
    AI_SECURITY = "AI_SECURITY"
    UNCATEGORIZED = "UNCATEGORIZED"


class NewsSentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class NewsArticle(BaseModel):
    article_id: str
    title: str
    source: str
    url: str
    published_at: datetime
    category: NewsCategory = NewsCategory.UNCATEGORIZED
    author: str | None = None
    content: str
    content_hash: str
    credibility_score: float | None = None
    language: str = "en"

    # ── Sentiment / analysis fields ───────────────────────────────────────────
    # Populated downstream by the Intelligent News Agent (sentiment_resolver
    # + Jev inference) — not at fetch time.  GNews does not return these fields.
    #
    # ai_tag:          AI-classified tag / category string
    # sentiment:       overall article sentiment
    # sentiment_stats: per-class probability distribution
    # ai_region:       AI-classified geographic region (future)
    # ai_org:          AI-extracted organisation name  (future)
    ai_tag: str | None = None
    sentiment: NewsSentiment | None = None
    sentiment_stats: dict[str, Any] | None = None
    ai_region: str | None = None
    ai_org: str | None = None

    # ── Source provider ───────────────────────────────────────────────────────
    # "gnews" | "mock"
    provider: str | None = None


class NewsSearchRequest(BaseModel):
    query: str
    published_after: str | None = None
    language: str = "en"
    limit: int = Field(default=20, ge=1, le=100)
    category: NewsCategory | None = None


class NewsSearchResponse(BaseModel):
    articles: list[NewsArticle]
    total: int
    query: str
