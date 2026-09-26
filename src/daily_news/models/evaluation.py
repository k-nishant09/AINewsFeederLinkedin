"""Evaluation result Pydantic models."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EvaluationDecision(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REGENERATE = "REGENERATE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCK = "BLOCK"


class EvaluationResult(BaseModel):
    article_id: str

    factuality: float = Field(ge=0.0, le=1.0)
    groundedness: float = Field(ge=0.0, le=1.0)
    hallucination: float = Field(ge=0.0, le=1.0)
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    persona_adherence: float = Field(default=0.0, ge=0.0, le=1.0)
    toxicity: float = Field(ge=0.0, le=1.0)

    policy_check: str  # "PASS" | "FAIL" | "REVIEW"
    unsupported_claims: list[str] = []

    overall_score: float = Field(ge=0.0, le=1.0)
    decision: EvaluationDecision
    notes: str = ""

    # ── Guardrail pipeline fields (added in v2; all have defaults) ────────────
    pii_detected: bool = False
    prompt_injection_detected: bool = False
    political_bias_detected: bool = False
    guardrail_failures: list[str] = []
    publish_eligible: bool = False
    layer_results: dict[str, Any] = {}
    failure_reasons: list[str] = Field(default_factory=list)
