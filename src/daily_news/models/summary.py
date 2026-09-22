"""Summary Pydantic models."""
from __future__ import annotations

from pydantic import BaseModel


class NewsSummary(BaseModel):
    article_id: str
    headline: str
    summary: str
    key_points: list[str]
    why_it_matters: str
    business_impact: str
    job_impact: str
    technology_impact: str
    policy_impact: str | None = None
    source: str
    source_url: str
