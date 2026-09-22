"""Evaluation MCP client — direct httpx calls to evaluation.* tools."""
from __future__ import annotations

from typing import Any

from daily_news.mcp.client import mcp_factory
from daily_news.models.evaluation import EvaluationResult


class EvaluationMCPClient:

    async def evaluate_content(
        self,
        article_id: str,
        source_text: str,
        generated_text: str,
        persona: str,
    ) -> EvaluationResult:
        data = await mcp_factory().evaluation.call(
            "evaluation_evaluate_all",
            {
                "article_id": article_id,
                "source_text": source_text,
                "generated_text": generated_text,
                "persona": persona,
            },
        )
        return EvaluationResult(**data)

    async def policy_check(
        self,
        article_id: str,
        generated_text: str,
    ) -> dict[str, Any]:
        return await mcp_factory().evaluation.call(
            "evaluation_policy_check",
            {"article_id": article_id, "generated_text": generated_text},
        )
