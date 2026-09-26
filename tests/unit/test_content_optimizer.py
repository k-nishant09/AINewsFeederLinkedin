"""Unit tests for ContentOptimizerAgent and Story Mutation Engine."""
import pytest

from daily_news.models.intelligence import EngagementMetrics, PerformanceDiagnosis, StoryMutation


@pytest.mark.asyncio
async def test_performance_diagnosis_model():
    metrics = EngagementMetrics(
        post_urn="urn:li:share:12345",
        article_id="art-101",
        impressions=4200,
        reactions=31,
        comments=2,
        reposts=1,
        engagement_rate=0.008,
    )
    assert metrics.impressions == 4200
    assert metrics.comments == 2

    diagnosis = PerformanceDiagnosis(
        hook_score=0.42,
        storytelling_score=0.71,
        audience_relevance_score=0.83,
        perspective_score=0.54,
        dialogue_score=0.78,
        question_score=0.31,
        weakest_component="question",
        hypotheses=["Audience question was too broad"],
        actionable_recommendation="Narrow down the CTA question to concrete production trade-offs.",
    )
    assert diagnosis.weakest_component == "question"
    assert len(diagnosis.hypotheses) == 1


@pytest.mark.asyncio
async def test_story_mutation_model():
    mutation = StoryMutation(
        style_variant="unexpected_consequence",
        proposed_hook="What happens when AI auditing begins before code deployment?",
        proposed_perspective="The bottleneck shifts from model accuracy to compliance telemetry.",
        proposed_analogy="Think of it like building inspections for software.",
        proposed_future_question="Who owns audit liability on your team?",
        rationale="Targeting specific engineering trade-offs instead of generic policy.",
    )
    assert mutation.style_variant == "unexpected_consequence"
    assert "auditing" in mutation.proposed_hook
