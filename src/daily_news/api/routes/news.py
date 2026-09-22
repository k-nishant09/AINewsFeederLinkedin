"""News routes — search and retrieve news articles."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from daily_news.mcp.news import NewsMCPClient
from daily_news.models.news import NewsSearchRequest

router = APIRouter()


@router.post("/search")
async def search_news(request: NewsSearchRequest):
    client = NewsMCPClient()
    return await client.search_latest(
        query=request.query,
        hours=24,
        limit=request.limit,
    )


@router.get("/{article_id}")
async def get_article(article_id: str):
    # Placeholder — production implementation would look up from PageIndex/DB
    raise HTTPException(status_code=501, detail="Direct article lookup not yet implemented.")
