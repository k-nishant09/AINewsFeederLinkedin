"""News article Pydantic models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

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


class NewsArticle(BaseModel):
    article_id: str
    title: str
    source: str
    url: str
    published_at: datetime
    category: NewsCategory = NewsCategory.UNCATEGORIZED
    author: Optional[str] = None
    content: str
    content_hash: str
    credibility_score: Optional[float] = None
    language: str = "en"


class NewsSearchRequest(BaseModel):
    query: str
    published_after: Optional[str] = None
    language: str = "en"
    limit: int = Field(default=20, ge=1, le=100)
    category: Optional[NewsCategory] = None


class NewsSearchResponse(BaseModel):
    articles: list[NewsArticle]
    total: int
    query: str
