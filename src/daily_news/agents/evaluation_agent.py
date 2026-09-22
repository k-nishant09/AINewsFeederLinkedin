"""Evaluation Agent — applies deterministic quality gates after LLM scoring."""
from __future__ import annotations

from daily_news.config.settings import get_settings
from daily_news.mcp.evaluation import EvaluationMCPClient
from daily_news.models.evaluation import EvaluationDecision, EvaluationResult
from daily_news.models.persona import PersonaSetOutput
from daily_news.models.summary import NewsSummary


class EvaluationAgent:
    """
    Calls the Evaluation MCP server and applies deterministic thresholds.

    The LLM never decides whether publishing is permitted — only this gate does.
    """

    def __init__(self) -> None:
        self._client = EvaluationMCPClient()
        self._settings = get_settings()

    async def evaluate(
        self,
        summary: NewsSummary,
        personas: PersonaSetOutput,
        source_text: str,
    ) -> EvaluationResult:
        generated_text = self._compose_generated_text(summary, personas)

        # GNews free plan truncates article content to ~250 chars.
        # Enrich source_text with the LLM-generated summary so the hallucination
        # checker has enough grounded context to score persona outputs fairly.
        enriched_source = _enrich_source(source_text, summary)

        result = await self._client.evaluate_content(
            article_id=summary.article_id,
            source_text=enriched_source,
            generated_text=generated_text,
            persona="all",
        )

        # Deterministic gate — LLM scores are advisory inputs, not decisions
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
    Combine the raw article content (often truncated to ~250 chars on GNews free
    plan) with the LLM-generated summary fields so the hallucination evaluator
    has enough grounded context to score persona outputs fairly.

    The summary is already LLM-verified against the article, so including it as
    reference material is sound — personas are evaluated against the summary,
    not invented from thin air.
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
