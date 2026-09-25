"""
NewsIntelligence — the backbone JSON contract for the AI News Intelligence pipeline.

Every stage of the pipeline enriches this object rather than passing arbitrary text
from agent to agent.  It is the bridge between news intelligence and content generation.

Pipeline enrichment order
──────────────────────────
  1. DISCOVER  (GNews)           → article raw fields
  2. UNDERSTAND (PageIndex)      → evidence sections
  3. ANALYZE (Jev prefilter)     → sentiment, emotion, impact, novelty, trend_velocity,
                                   audience_relevance, event_type, controversy_level
  4. EXPLAIN (SummaryAgent)      → what_happened, what_changed, why_it_matters,
                                   who_is_affected, key_points, uncertainty
  5. FIND THE ANGLE (Jev router) → content_opportunity
  6. CREATE (LinkedIn agent)     → post text
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class EmotionSignals(BaseModel):
    """Emotional signals inferred by Jev from the article's framing and content."""
    curiosity:   float = Field(0.0, ge=0.0, le=1.0, description="Novelty-driven intellectual pull")
    excitement:  float = Field(0.0, ge=0.0, le=1.0, description="Positive momentum / breakthrough energy")
    concern:     float = Field(0.0, ge=0.0, le=1.0, description="Risk / worry / caution signal")
    urgency:     float = Field(0.0, ge=0.0, le=1.0, description="Time-sensitive or action-required feel")


class AudienceImpact(BaseModel):
    """Audience-segment impact scores (0-1) inferred by Jev."""
    enterprise:     float = Field(0.0, ge=0.0, le=1.0)
    developers:     float = Field(0.0, ge=0.0, le=1.0)
    infrastructure: float = Field(0.0, ge=0.0, le=1.0)
    business:       float = Field(0.0, ge=0.0, le=1.0)
    policy:         float = Field(0.0, ge=0.0, le=1.0)
    general_public: float = Field(0.0, ge=0.0, le=1.0)


class ContentOpportunity(BaseModel):
    """
    Jev's content angle recommendation — what makes this worth saying,
    and how to say it differently from the common narrative.
    """
    common_narrative:      str = ""   # what every other outlet is saying
    missing_angle:         str = ""   # what's underreported / contrarian
    recommended_audience:  str = ""   # primary LinkedIn audience for this story
    discussion_question:   str = ""   # the one question that sparks real conversation


class EngagementMetrics(BaseModel):
    """Observable LinkedIn engagement metrics."""
    post_urn:         str = ""
    article_id:       str = ""
    impressions:      int = Field(default=0, ge=0)
    reactions:        int = Field(default=0, ge=0)
    comments:         int = Field(default=0, ge=0)
    reposts:          int = Field(default=0, ge=0)
    engagement_rate:  float = Field(default=0.0, ge=0.0)


class PerformanceDiagnosis(BaseModel):
    """Diagnostic breakdown evaluating which structural component underperformed."""
    hook_score:               float = Field(default=0.0, ge=0.0, le=1.0)
    storytelling_score:       float = Field(default=0.0, ge=0.0, le=1.0)
    audience_relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    perspective_score:        float = Field(default=0.0, ge=0.0, le=1.0)
    dialogue_score:           float = Field(default=0.0, ge=0.0, le=1.0)
    question_score:           float = Field(default=0.0, ge=0.0, le=1.0)
    weakest_component:        str = ""
    hypotheses:               list[str] = Field(default_factory=list)
    actionable_recommendation: str = ""


class StoryMutation(BaseModel):
    """A mutated narrative angle/style candidate for closed-loop refinement."""
    style_variant:            str = ""
    proposed_hook:            str = ""
    proposed_perspective:     str = ""
    proposed_analogy:         str = ""
    proposed_future_question: str = ""
    rationale:                str = ""


class JudgmentAnalysis(BaseModel):
    """
    Separation of Facts, Claims, Analysis, Unknowns and Guard boundaries
    produced before storytelling to prevent fictional certainty.
    """
    facts:                 list[str] = Field(default_factory=list)  # verified/announced facts
    reported_claims:       list[str] = Field(default_factory=list)  # claims by companies/actors
    analysis_implications: list[str] = Field(default_factory=list)  # logical technical/business implications
    uncertainties:         list[str] = Field(default_factory=list)  # what is unknown/unproven
    what_not_to_conclude:  list[str] = Field(default_factory=list)  # boundaries models must not overclaim


class NewsStory(BaseModel):
    """
    The storytelling layer — produced by the MediaStorytellerAgent before content generation.

    This is the "what is the story?" object, not the "what happened?" object.
    Separating storytelling from copywriting is what makes the content non-generic.
    """
    # ── The Situation ─────────────────────────────────────────────────────────
    what_actually_happened:   str = ""  # plain-English: what happened, stripped of jargon
    who:                      list[str] = Field(default_factory=list)  # actors: company, person, sector
    what_changed:             str = ""  # the structural shift — what is different now

    # ── The Context / Background ──────────────────────────────────────────────
    why_now:                  str = ""  # why did this happen at this moment?
    what_came_before:         str = ""  # relevant backstory the reader needs
    what_problem_it_solves:   str = ""  # the pain point or gap this addresses

    # ── The Story ─────────────────────────────────────────────────────────────
    hook:                     str = ""  # the opening that stops the scroll — relatable, not corporate
    human_analogy:            str = ""  # make the complex simple — "think of it like..."
    turning_point:            str = ""  # the moment the story changes direction

    # ── The Perspective ───────────────────────────────────────────────────────
    why_reader_should_care:   str = ""  # "this matters to YOU because..."
    perspective:              str = ""  # the non-obvious take: what everyone is missing
    second_order_effect:      str = ""  # what happens next — downstream consequence
    future_question:          str = ""  # the open question that deserves a conversation

    # ── Dynamic Dialogue Delivery (Generated per news case) ───────────────────
    media_host_opening:       str = ""  # Live dynamic opening hook framing the news
    media_host_setup:         str = ""  # Conversational setup & grounding
    media_transitions:        dict[str, str] = Field(default_factory=dict)  # Dynamic transitions between personas
    media_host_synthesis:     str = ""  # Factual + judgment conclusion from the host
    media_host_audience_cta:  str = ""  # Concluding open-ended audience dilemma
    dynamic_seo_hashtags:     list[str] = Field(default_factory=list)  # SEO/AEO targeted hashtags (companies, entities, domains)

    # ── The Analysis ──────────────────────────────────────────────────────────
    business_consequence:     str = ""  # revenue / cost / competitive impact
    technology_consequence:   str = ""  # what changes in systems / architecture
    human_consequence:        str = ""  # what this means for people — jobs, skills, access
    risks:                    list[str] = Field(default_factory=list)
    opportunities:            list[str] = Field(default_factory=list)

    # ── The Format Selection ──────────────────────────────────────────────────
    narrative_style:          str = "problem_solution"  # chosen story format
    tone:                     str = "curious_analytical"


class EvidenceItem(BaseModel):
    """A specific claim or fact extracted from PageIndex tree retrieval."""
    source:  str = ""
    section: str = ""
    url:     str = ""
    text:    str = ""


class NewsIntelligence(BaseModel):
    """
    The AI News Intelligence Object — enriched progressively through the pipeline.

    Stages that populate this object:
      Stage 3 (Jev/Analyze):      sentiment, emotion, impact, novelty,
                                   trend_velocity, audience_relevance,
                                   event_type, controversy_level
      Stage 4 (Summary/Explain):  what_happened, what_changed, why_it_matters,
                                   who_is_affected, key_points, uncertainty,
                                   headline, source, source_url, topics
      Stage 5 (Jev/Angle):        content_opportunity
      Evidence (PageIndex):        evidence
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    article_id: str
    headline:   str = ""
    source:     str = ""
    source_url: str = ""
    topics:     list[str] = Field(default_factory=list)

    # ── Stage 3: Intelligent analysis (Jev-scored) ────────────────────────────
    # Sentiment polarity: "positive" | "negative" | "neutral"
    sentiment:         str   = "neutral"
    # Sentiment confidence 0-1
    sentiment_score:   float = Field(0.0, ge=0.0, le=1.0)
    sentiment_stats:   dict[str, Any] = Field(default_factory=dict)

    # Emotion signals
    emotion: EmotionSignals = Field(default_factory=EmotionSignals)

    # Per-audience impact
    impact: AudienceImpact = Field(default_factory=AudienceImpact)

    # Novelty: how new/different is this vs existing discourse?
    novelty:           float = Field(0.0, ge=0.0, le=1.0)
    # Trend velocity: is this accelerating or fading?
    trend_velocity:    float = Field(0.0, ge=0.0, le=1.0)
    # Overall audience relevance for LinkedIn AI audience
    audience_relevance: float = Field(0.0, ge=0.0, le=1.0)

    # Jev event classification and significance
    event_type:        str   = "other"
    significance:      float = Field(0.0, ge=0.0, le=1.0)
    controversy_level: str   = "low"

    # ── Stage 4: Explanation (SummaryAgent) ───────────────────────────────────
    what_happened:  str = ""   # factual description of the event
    what_changed:   str = ""   # what is structurally different now
    why_it_matters: str = ""   # direct human "so what"
    who_is_affected: str = ""  # specific audience / sector
    uncertainty:    str = ""   # what is still unknown or contested
    key_points:     list[str] = Field(default_factory=list)

    # Legacy compat — kept for PublisherAgent composition
    summary:            str  = ""
    business_impact:    str  = ""
    job_impact:         str  = ""
    technology_impact:  str  = ""
    policy_impact:      Optional[str] = None

    # ── Stage 4b: Story (MediaStorytellerAgent) ───────────────────────────────
    story: Optional["NewsStory"] = None

    # ── Judgment Analysis ─────────────────────────────────────────────────────
    judgment: Optional[JudgmentAnalysis] = None

    # ── Stage 5: Content angle (Jev router) ───────────────────────────────────
    content_opportunity: ContentOpportunity = Field(default_factory=ContentOpportunity)

    # ── PageIndex evidence ────────────────────────────────────────────────────
    evidence: list[EvidenceItem] = Field(default_factory=list)

    # ── Resolved ai_tag (from sentiment_resolver or Jev) ─────────────────────
    ai_tag: Optional[str] = None
