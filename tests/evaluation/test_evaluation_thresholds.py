"""Evaluation tests — scoring and threshold behaviour."""
from __future__ import annotations

import pytest

from daily_news.agents.evaluation_agent import EvaluationAgent
from daily_news.models.evaluation import EvaluationDecision, EvaluationResult


def _result(**kwargs) -> EvaluationResult:
    base = dict(
        article_id="ev-001",
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
    base.update(kwargs)
    return EvaluationResult(**base)


# Production thresholds (src/daily_news/config/settings.py):
#   eval_factuality_threshold   = 0.50  → factuality  < 0.50 → REGENERATE
#   eval_groundedness_threshold = 0.50  → groundedness < 0.50 → REGENERATE
#   eval_hallucination_threshold= 0.85  → hallucination > 0.85 → REGENERATE
@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("factuality",    0.40, EvaluationDecision.REGENERATE),   # 0.40 < 0.50 threshold
        ("groundedness",  0.45, EvaluationDecision.REGENERATE),   # 0.45 < 0.50 threshold
        ("hallucination", 0.90, EvaluationDecision.REGENERATE),   # 0.90 > 0.85 threshold
        ("policy_check",  "FAIL", EvaluationDecision.HUMAN_REVIEW),
        ("factuality",    0.95, EvaluationDecision.PASS),
    ],
)
def test_gate_parametrised(field, value, expected):
    result = _result(**{field: value})
    agent = EvaluationAgent()
    assert agent._apply_gate(result) == expected
