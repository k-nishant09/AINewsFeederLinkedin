"""Summary Pydantic models."""
from __future__ import annotations

from typing import Any

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

    # ── Intelligence enrichment (from Jev + sentiment_resolver) ──────────────
    sentiment: str | None = None
    sentiment_stats: dict[str, Any] | None = None
    ai_tag: str | None = None

    # ── NewsIntelligence object (full backbone contract) ──────────────────────
    # Import deferred to avoid circular deps.
    intelligence: Any | None = None

    # ── NewsStory object (from MediaStorytellerAgent) ─────────────────────────
    # The structured story — hook, analogy, perspective, second_order_effect,
    # future_question, narrative_style — used by persona and publisher agents.
    # Import deferred to avoid circular deps.
    story: Any | None = None
