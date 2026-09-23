"""Evaluation Agent — applies deterministic quality gates after Jev / LLM scoring.

Scoring backend
───────────────
When JEV_ENABLED=true  (default):
    Uses JevClient.evaluate_content() — IBM Jev System One gateway.
    70–500 ms per call. Calibrated floats. Zero hallucination risk.

When JEV_ENABLED=false (fallback):
    Uses EvaluationMCPClient.evaluate_content() — LLM-backed MCP server.

In both cases the deterministic _apply_gate() method makes the final
publish/regenerate/block decision — the scoring backend only supplies
the float inputs.
"""
from __future__ import annotations

import logging

from daily_news.config.settings import get_settings
from daily_news.mcp.evaluation import EvaluationMCPClient
from daily_news.mcp.jev_client import JevClient
from daily_news.models.evaluation import EvaluationDecision, EvaluationResult
from daily_news.models.persona import PersonaSetOutput
from daily_news.models.summary import NewsSummary

logger = logging.getLogger(__name__)


class EvaluationAgent:
    """
    Calls the scoring backend (Jev or MCP) and applies deterministic thresholds.

    The LLM / Jev model never decides whether publishing is permitted —
    only _apply_gate() does.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        # Jev is the primary backend; MCP client kept as fallback
        self._jev = JevClient()
        self._mcp = EvaluationMCPClient()

    async def evaluate(
        self,
        summary: NewsSummary,
        personas: PersonaSetOutput,
        source_text: str,
    ) -> EvaluationResult:
        generated_text = self._compose_generated_text(summary, personas)
        enriched_source = _enrich_source(source_text, summary)

        if self._settings.jev_enabled:
            try:
                result = await self._jev.evaluate_content(
                    article_id=summary.article_id,
                    source_text=enriched_source,
                    generated_text=generated_text,
                )
                logger.debug(
                    "jev evaluate article=%s factuality=%.2f hallucination=%.2f",
                    summary.article_id, result.factuality, result.hallucination,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Jev evaluation failed for %s (%s) — falling back to MCP",
                    summary.article_id, exc,
                )
                result = await self._mcp.evaluate_content(
                    article_id=summary.article_id,
                    source_text=enriched_source,
                    generated_text=generated_text,
                    persona="all",
                )
        else:
            result = await self._mcp.evaluate_content(
                article_id=summary.article_id,
                source_text=enriched_source,
                generated_text=generated_text,
                persona="all",
            )

        # Deterministic gate — scoring backend is advisory only
        result.decision = self._apply_gate(result)
        result.publish_eligible = result.decision == EvaluationDecision.PASS
        return result

    def _apply_gate(self, r: EvaluationResult) -> EvaluationDecision:
        s = self._settings

        # Hard BLOCK — PII or prompt injection must never reach publication
        if r.pii_detected:
            return EvaluationDecision.BLOCK
        if r.prompt_injection_detected:
            return EvaluationDecision.BLOCK

        # Quality regeneration gates
        if r.factuality < s.eval_factuality_threshold:
            return EvaluationDecision.REGENERATE
        if r.groundedness < s.eval_groundedness_threshold:
            return EvaluationDecision.REGENERATE
        if r.hallucination > s.eval_hallucination_threshold:
            return EvaluationDecision.REGENERATE

        # Policy / bias gates
        if r.political_bias_detected:
            return EvaluationDecision.HUMAN_REVIEW
        if r.policy_check != "PASS":
            return EvaluationDecision.HUMAN_REVIEW

        return EvaluationDecision.PASS

    @staticmethod
    def _compose_generated_text(summary: NewsSummary, personas: PersonaSetOutput) -> str:
        parts = [
            summary.headline,
            summary.summary,
            personas.business.perspective,
            personas.labor.perspective,
            personas.policy.perspective,
            personas.genz.perspective,
            personas.linkedin.perspective,
        ]
        return "\n\n".join(parts)


def _enrich_source(raw_source: str, summary: NewsSummary) -> str:
    """
    Combine raw article content (often truncated to ~250 chars on GNews free
    plan) with the LLM-generated summary fields so the Jev evaluator has
    enough grounded context to score persona outputs fairly.
    """
    parts = [raw_source.strip()] if raw_source.strip() else []
    parts += [
        f"TITLE: {summary.headline}",
        f"SUMMARY: {summary.summary}",
        f"KEY POINTS: {' | '.join(summary.key_points)}" if summary.key_points else "",
        f"BUSINESS IMPACT: {summary.business_impact}" if summary.business_impact else "",
        f"JOB IMPACT: {summary.job_impact}" if summary.job_impact else "",
        f"TECHNOLOGY IMPACT: {summary.technology_impact}" if summary.technology_impact else "",
        f"WHY IT MATTERS: {summary.why_it_matters}" if summary.why_it_matters else "",
    ]
    return "\n\n".join(p for p in parts if p)
