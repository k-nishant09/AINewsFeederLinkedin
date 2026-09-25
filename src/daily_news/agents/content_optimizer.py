"""
Closed-loop Content Optimization & Performance Diagnosis Agent.

Analyzes measurable LinkedIn engagement signals (impressions, reactions, comments, reposts),
diagnoses structural bottlenecks (hook vs perspective vs question vs dialogue), and generates
targeted Story Mutations / learnings to calibrate future Jev angle recommendations and storytelling
without blind whole-story regeneration or sensationalism.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from daily_news.config.settings import get_settings
from daily_news.models.intelligence import (
    EngagementMetrics,
    PerformanceDiagnosis,
    StoryMutation,
)
from daily_news.models.summary import NewsSummary
from daily_news.observability.tracing import get_langfuse_callback

logger = logging.getLogger(__name__)


class _DiagnosisRaw(BaseModel):
    hook_score: float = Field(default=0.5, ge=0.0, le=1.0)
    storytelling_score: float = Field(default=0.5, ge=0.0, le=1.0)
    audience_relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    perspective_score: float = Field(default=0.5, ge=0.0, le=1.0)
    dialogue_score: float = Field(default=0.5, ge=0.0, le=1.0)
    question_score: float = Field(default=0.5, ge=0.0, le=1.0)
    weakest_component: str = "hook"
    hypotheses: list[str] = Field(default_factory=list)
    actionable_recommendation: str = ""


class _MutationRaw(BaseModel):
    mutations: list[StoryMutation] = Field(default_factory=list)


_DIAGNOSIS_PROMPT = """You are an expert LinkedIn editorial performance diagnostician.
Given the post content and observed engagement metrics, diagnose which specific component succeeded or failed:
1. Hook (curiosity, scroll-stopping ability)
2. Storytelling (structure, human analogy, clarity)
3. Audience relevance (did the topic target the right decision-makers?)
4. Perspective (was the take non-obvious or too generic?)
5. Dialogue (were the persona mental models distinct and compelling?)
6. Audience question (did the CTA encourage thoughtful comments vs silence?)

Rule: Do NOT advise sensationalism or clickbait. Advise precise, high-substance adjustments.
"""

_MUTATION_PROMPT = """You are a Story Mutation Engine.
Given an underperforming story's diagnosis and its verified facts & evidence, generate 3 alternative narrative approaches.

Available narrative styles:
- human_story
- unexpected_consequence
- contrarian
- future_scenario
- architect_lens
- developer_lens
- problem_solution

Rule: Change the weakest structural component (e.g. hook or perspective or framing) while strictly preserving factual grounding and evidence.
"""


class ContentOptimizerAgent:
    """Closed-loop performance diagnostician and story mutation optimizer."""

    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.3,
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )
        self._diag_parser = PydanticOutputParser(pydantic_object=_DiagnosisRaw)
        self._diag_prompt = ChatPromptTemplate.from_messages([
            ("system", _DIAGNOSIS_PROMPT),
            (
                "human",
                "Article ID: {article_id}\n\n"
                "Post Content:\n{post_text}\n\n"
                "Engagement Metrics:\n"
                "  Impressions: {impressions}\n"
                "  Reactions: {reactions}\n"
                "  Comments: {comments}\n"
                "  Reposts: {reposts}\n"
                "  Engagement Rate: {engagement_rate:.4f}\n\n"
                "{format_instructions}",
            ),
        ])

        self._mut_parser = PydanticOutputParser(pydantic_object=_MutationRaw)
        self._mut_prompt = ChatPromptTemplate.from_messages([
            ("system", _MUTATION_PROMPT),
            (
                "human",
                "Article ID: {article_id}\n\n"
                "Headline: {headline}\n\n"
                "Current Story Hook: {current_hook}\n\n"
                "Current Perspective: {current_perspective}\n\n"
                "Diagnosis Weakness: {weakest_component}\n"
                "Recommendations: {recommendation}\n\n"
                "Evidence & Verified Facts:\n{evidence}\n\n"
                "{format_instructions}",
            ),
        ])

    async def diagnose_performance(
        self,
        metrics: EngagementMetrics,
        post_text: str,
        run_id: str | None = None,
    ) -> PerformanceDiagnosis:
        handler, _ = get_langfuse_callback(
            run_id=run_id,
            tags=["performance_diagnosis", self._settings.app_env],
            metadata={"article_id": metrics.article_id, "post_urn": metrics.post_urn},
        )
        callbacks = [handler] if handler else []

        try:
            chain = self._diag_prompt | self._llm | self._diag_parser
            raw: _DiagnosisRaw = await chain.ainvoke(
                {
                    "article_id": metrics.article_id,
                    "post_text": post_text[:2500],
                    "impressions": metrics.impressions,
                    "reactions": metrics.reactions,
                    "comments": metrics.comments,
                    "reposts": metrics.reposts,
                    "engagement_rate": metrics.engagement_rate,
                    "format_instructions": self._diag_parser.get_format_instructions(),
                },
                config={"callbacks": callbacks} if callbacks else {},
            )
            return PerformanceDiagnosis(
                hook_score=raw.hook_score,
                storytelling_score=raw.storytelling_score,
                audience_relevance_score=raw.audience_relevance_score,
                perspective_score=raw.perspective_score,
                dialogue_score=raw.dialogue_score,
                question_score=raw.question_score,
                weakest_component=raw.weakest_component,
                hypotheses=raw.hypotheses,
                actionable_recommendation=raw.actionable_recommendation,
            )
        except Exception as exc:
            logger.warning("diagnose_performance failed for %s: %s", metrics.article_id, exc)
            # Fallback deterministic diagnosis based on comment-to-impression ratio
            comment_ratio = metrics.comments / max(metrics.impressions, 1)
            weakness = "question" if comment_ratio < 0.001 else "hook"
            return PerformanceDiagnosis(
                hook_score=0.5,
                storytelling_score=0.6,
                audience_relevance_score=0.6,
                perspective_score=0.5,
                dialogue_score=0.6,
                question_score=0.4 if weakness == "question" else 0.6,
                weakest_component=weakness,
                hypotheses=[f"Underperformance in {weakness}"],
                actionable_recommendation=f"Refine {weakness} in subsequent iterations.",
            )

    async def mutate_story(
        self,
        summary: NewsSummary,
        diagnosis: PerformanceDiagnosis,
        evidence: str = "",
        run_id: str | None = None,
    ) -> list[StoryMutation]:
        handler, _ = get_langfuse_callback(
            run_id=run_id,
            tags=["story_mutation", self._settings.app_env],
            metadata={"article_id": summary.article_id, "weakness": diagnosis.weakest_component},
        )
        callbacks = [handler] if handler else []

        st = summary.story
        current_hook = getattr(st, "hook", "") if st else ""
        current_persp = getattr(st, "perspective", "") if st else ""

        try:
            chain = self._mut_prompt | self._llm | self._mut_parser
            raw: _MutationRaw = await chain.ainvoke(
                {
                    "article_id": summary.article_id,
                    "headline": summary.headline,
                    "current_hook": current_hook,
                    "current_perspective": current_persp,
                    "weakest_component": diagnosis.weakest_component,
                    "recommendation": diagnosis.actionable_recommendation,
                    "evidence": evidence or "\n".join(summary.key_points),
                    "format_instructions": self._mut_parser.get_format_instructions(),
                },
                config={"callbacks": callbacks} if callbacks else {},
            )
            return raw.mutations
        except Exception as exc:
            logger.warning("mutate_story failed for %s: %s", summary.article_id, exc)
            return [
                StoryMutation(
                    style_variant="unexpected_consequence",
                    proposed_hook=f"The most overlooked aspect of {summary.headline[:40]} is its downstream effect.",
                    proposed_perspective="The bottleneck is moving from model capability to deployment controls.",
                    proposed_analogy="Think of it as granting root access to an automated system.",
                    proposed_future_question="Where does your team draw the line on system autonomy?",
                    rationale="Fallback structural mutation targeting deeper consequences.",
                )
            ]
