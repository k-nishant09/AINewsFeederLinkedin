"""Unit tests — Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from daily_news.models.news import NewsArticle, NewsCategory
from daily_news.models.evaluation import EvaluationResult, EvaluationDecision
from datetime import datetime


def test_news_article_valid():
    a = NewsArticle(
        article_id="news-001",
        title="AI Breakthrough",
        source="TechCrunch",
        url="https://techcrunch.com/article",
        published_at=datetime.utcnow(),
        content="Content here",
        content_hash="abc123",
    )
    assert a.category == NewsCategory.UNCATEGORIZED


def test_evaluation_result_score_bounds():
    with pytest.raises(ValidationError):
        EvaluationResult(
            article_id="x",
            factuality=1.5,  # out of bounds
            groundedness=0.9,
            hallucination=0.01,
            relevance=0.9,
            persona_adherence=0.9,
            toxicity=0.0,
            policy_check="PASS",
            overall_score=0.9,
            decision=EvaluationDecision.PASS,
        )
