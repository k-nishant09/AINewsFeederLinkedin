"""Unit tests — models and evaluation gate."""
from __future__ import annotations

import pytest

from daily_news.agents.evaluation_agent import EvaluationAgent
from daily_news.models.evaluation import EvaluationDecision, EvaluationResult


def _make_result(**overrides) -> EvaluationResult:
    defaults = dict(
        article_id="test-001",
        factuality=0.95,
        groundedness=0.94,
        hallucination=0.02,
        relevance=0.92,
        persona_adherence=0.91,
        toxicity=0.01,
        policy_check="PASS",
        unsupported_claims=[],
        overall_score=0.93,
        decision=EvaluationDecision.PASS,
    )
    defaults.update(overrides)
    return EvaluationResult(**defaults)


class TestEvaluationGate:
    def _gate(self, result: EvaluationResult) -> EvaluationDecision:
        return EvaluationAgent()._apply_gate(result)

    def test_passes_when_all_thresholds_met(self):
        assert self._gate(_make_result()) == EvaluationDecision.PASS

    def test_regenerate_when_factuality_low(self):
        assert self._gate(_make_result(factuality=0.85)) == EvaluationDecision.REGENERATE

    def test_regenerate_when_groundedness_low(self):
        assert self._gate(_make_result(groundedness=0.80)) == EvaluationDecision.REGENERATE

    def test_regenerate_when_hallucination_high(self):
        assert self._gate(_make_result(hallucination=0.10)) == EvaluationDecision.REGENERATE

    def test_human_review_when_policy_fails(self):
        assert self._gate(_make_result(policy_check="FAIL")) == EvaluationDecision.HUMAN_REVIEW
