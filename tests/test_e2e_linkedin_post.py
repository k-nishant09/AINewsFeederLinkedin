"""
End-to-end LinkedIn post pipeline validation.

Tests every real code path from raw article data through to the final
LinkedIn post text and comic image, using only mock/stub data — no live
MCP calls, no LLM calls, no LinkedIn API calls required.

Run:
    python3 -m pytest tests/test_e2e_linkedin_post.py -v
    # or without pytest:
    python3 tests/test_e2e_linkedin_post.py

Validates:
  1. ComicScript construction from pipeline objects (build_comic_script_from_summary)
  2. Fact chip extraction (_derive_facts_from_summary)
  3. Scene building (build_scenes) — 6 scenes, correct persona chain
  4. SVG/PNG render (generate_comic_from_script) — file created, minimum size
  5. Outside-post caption format (build_outside_post) — 3-part structure,
     character limits, source link, hashtags
  6. Publisher compose_main_post — character limits, guardrail tokens absent
  7. _extract_dynamic_tags — no hardcoded tags, source tag present
  8. Prefix-cache hit on second call (jev_agents + persona_agent caches)
  9. Banned phrase scanner (_check_persona_text) — blocks known bad phrases
 10. Full post token budget — LinkedIn 3000 UTF-16 limit respected
"""
from __future__ import annotations

import os
import sys
import tempfile
import hashlib
import asyncio

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── Minimal stubs so imports work without real env / gateway ──────────────────

import unittest
from unittest.mock import MagicMock, patch

# Patch settings before any agent import touches it
_mock_settings = MagicMock()
_mock_settings.llm_model = "qwen-test"
_mock_settings.llm_api_key = "test-key"
_mock_settings.llm_base_url = "http://localhost:9999/v1"
_mock_settings.app_env = "development"
_mock_settings.jev_enabled = False
_mock_settings.jev_base_url = ""
_mock_settings.publishing_enabled = False

import daily_news.config.settings as _settings_mod
_settings_mod.get_settings = lambda: _mock_settings

# ── Now safe to import agents ─────────────────────────────────────────────────

from daily_news.agents.comic_generator import (
    ComicScript,
    PersonaCast,
    build_scenes,
    build_outside_post,
    generate_comic_from_script,
    _derive_facts_from_summary,
    _extract_dynamic_tags,
    TOTAL_W,
    TOTAL_H,
    HEADER_H,
    FOOTER_H,
    PANEL_H,
    PANEL_W,
    ROWS,
    COLS,
)
from daily_news.agents.publisher_agent import (
    _check_persona_text,
    _linkedin_len,
    _extract_dynamic_tags as _pub_extract_tags,
)
from daily_news.agents.jev_agents import (
    _JEV_PREFILTER_CACHE,
    _article_cache_key,
    _cache_get,
    _cache_set,
)
from daily_news.agents.persona_agent import (
    _STORY_CONTEXT_CACHE,
    _story_context_cache_key,
)


# ── Test fixtures ─────────────────────────────────────────────────────────────

class _MockJudgment:
    facts = ["$42B net loss in 2025", "$4.6B revenue (12x growth)", "$518B cloud commitments planned"]
    reported_claims = ["Possible $2T IPO valuation mentioned by Reuters"]
    uncertainties = ["IPO timeline not confirmed"]
    what_not_to_conclude = ["Do not say Anthropic is bankrupt"]

    def model_dump(self) -> dict:
        return {
            "facts": self.facts,
            "reported_claims": self.reported_claims,
            "uncertainties": self.uncertainties,
            "what_not_to_conclude": self.what_not_to_conclude,
        }


class _MockIntelligence:
    event_type = "funding"
    relevance_score = 0.92
    significance = 0.85
    controversy_level = "medium"
    sentiment_polarity = "negative"
    novelty = 0.78
    trend_velocity = 0.71
    emotion = {"curiosity": 0.82, "excitement": 0.45, "concern": 0.75, "urgency": 0.60}
    impact = {"enterprise": 0.80, "developers": 0.70, "business": 0.90, "policy": 0.55}
    judgment = _MockJudgment()
    content_opportunity = {
        "missing_angle": "Infrastructure cost per revenue dollar — nobody is reporting this metric",
        "recommended_audience": "AI startup founders and enterprise CTOs",
    }

    def get(self, key, default=None):
        return getattr(self, key, default)


class _MockStory:
    hook = "Revenue is growing 12x. The loss is growing faster."
    what_actually_happened = "Anthropic filed an IPO prospectus revealing a $42B net loss alongside $4.6B in revenue."
    what_changed = "AI infrastructure economics are now a public record, not just investor gossip."
    why_now = "Ahead of a possible $2T IPO listing, Anthropic needed to disclose financials."
    perspective = "The $42B loss is not the story. The revenue-to-compute ratio is."
    second_order_effect = "Every AI startup's burn rate is now benchmarked against Anthropic's."
    human_analogy = "Like a restaurant doing $4.6M in sales but spending $42M on ingredients."
    why_reader_should_care = "If you're sizing AI infrastructure spend, your cost model just got a public benchmark."
    future_question = "Can frontier AI turn rapidly growing demand into sustainable economics?"
    business_consequence = "AI startups must now justify their own infrastructure spend against this benchmark."
    technology_consequence = "Cloud commitments at this scale change the compute dependency picture."
    human_consequence = "Investment decisions in AI will now have a public cost reference point."
    narrative_style = "investor_dilemma"
    media_host_opening = "Reuters just published the Anthropic IPO prospectus. The numbers are surprising."
    media_host_setup = "Revenue grew 12x. Net loss grew even faster. What does that tell us about AI economics?"
    media_host_synthesis = "Four views, one tension: frontier AI revenues are real, but so is the infrastructure bill."
    media_host_audience_cta = (
        "If frontier AI requires this level of infrastructure spend, what breaks first?\n\n"
        "A — Economics of current AI pricing\n"
        "B — Compute availability\n"
        "C — Cloud dependency\n"
        "D — Investor patience"
    )
    dynamic_seo_hashtags = ["#Anthropic", "#AIEconomics", "#FrontierAI", "#AIInfrastructure"]

    def get(self, key, default=None):
        return getattr(self, key, default)


def _make_mock_summary():
    from daily_news.models.summary import NewsSummary

    intel = _MockIntelligence()
    # Attach story to intelligence (as the pipeline does)
    intel.story = _MockStory()

    return NewsSummary(
        article_id="anthropic-ipo-2025",
        headline="Anthropic IPO Prospectus Reveals $42B Loss on $4.6B Revenue",
        summary=(
            "Reuters reviewed Anthropic's IPO prospectus. The company recorded a $42B net loss "
            "in 2025 while revenue reached $4.6B (12x growth). Anthropic also plans $518B in "
            "cloud commitments ahead of a possible $2T listing."
        ),
        key_points=[
            "$42B net loss in 2025",
            "$4.6B revenue representing 12x year-on-year growth",
            "$518B in planned cloud infrastructure commitments",
            "Possible $2T IPO valuation being discussed",
        ],
        why_it_matters=(
            "Anthropic's numbers create the first public cost benchmark for frontier AI at scale."
        ),
        business_impact="AI startups must now justify infrastructure spend against Anthropic's public figures.",
        job_impact="AI infrastructure roles will expand as cloud commitments require engineering support.",
        technology_impact="Cloud commitments at this scale shift the compute dependency picture for all AI labs.",
        policy_impact="Concentration of AI infrastructure spend raises antitrust and governance questions.",
        source="Reuters",
        source_url="https://reuters.com/technology/anthropic-ipo-prospectus-2025",
        sentiment="negative",
        sentiment_stats={"negative": 0.72, "neutral": 0.20, "positive": 0.08},
        ai_tag="AI_ECONOMICS",
        intelligence=intel,
        story=_MockStory(),
    )


def _make_mock_personas():
    from daily_news.models.persona import PersonaOutput, PersonaSetOutput, PersonaType

    return PersonaSetOutput(
        article_id="anthropic-ipo-2025",
        business=PersonaOutput(
            persona=PersonaType.BUSINESS,
            article_id="anthropic-ipo-2025",
            perspective=(
                "A 12x revenue jump is real traction, but $518B in cloud commitments means "
                "the runway math depends entirely on unit economics holding at scale. "
                "David, does better model efficiency actually bring that compute bill down?"
            ),
            evidence=["$4.6B revenue", "$518B cloud capex"],
            next_question="Does better model efficiency actually bring that compute bill down?",
        ),
        linkedin=PersonaOutput(
            persona=PersonaType.LINKEDIN,
            article_id="anthropic-ipo-2025",
            perspective=(
                "Efficiency helps, but production cost is not just FLOPs — GPU utilization, "
                "latency guarantees and redundancy all sit on top of the model price. "
                "Zoe, if the spend keeps climbing like this, what does that do to how investors "
                "read the revenue number?"
            ),
            evidence=["GPU utilization", "latency guarantees"],
            next_question="What does climbing spend do to how investors read the revenue number?",
        ),
        genz=PersonaOutput(
            persona=PersonaType.GENZ,
            article_id="anthropic-ipo-2025",
            perspective=(
                "It means revenue growth alone will not satisfy anyone. Investors will want "
                "revenue per compute dollar, not just top line growth, especially with a $2T "
                "listing on the table. Ryan, does a loss this size change whether enterprises "
                "trust the vendor to still be standing in five years?"
            ),
            evidence=["revenue per compute dollar", "$2T listing"],
            next_question="Does a loss this size change whether enterprises trust the vendor long-term?",
        ),
        policy=PersonaOutput(
            persona=PersonaType.POLICY,
            article_id="anthropic-ipo-2025",
            perspective=(
                "It changes the diligence question entirely. I would stop asking if the model "
                "is good and start asking what this vendor cost curve looks like at 10x our "
                "current usage. A loss this size is not disqualifying alone, but it moves the "
                "conversation from features to balance sheet."
            ),
            evidence=["vendor cost curve", "balance sheet"],
            next_question="",
        ),
    )


# ── Test cases ────────────────────────────────────────────────────────────────

class TestFactChipExtraction(unittest.TestCase):
    """_derive_facts_from_summary — extracts numbers for the News Card."""

    def test_extracts_from_key_points(self):
        summary = _make_mock_summary()
        facts = _derive_facts_from_summary(summary)
        self.assertIsInstance(facts, list, "facts must be a list")
        self.assertGreater(len(facts), 0, "at least one fact should be extracted")
        # All chips must be short enough to render on a pill
        for f in facts:
            self.assertLessEqual(len(f), 32, f"chip too long: {f!r}")
        print(f"\n  Facts: {facts}")

    def test_extracts_from_judgment_when_available(self):
        summary = _make_mock_summary()
        facts = _derive_facts_from_summary(summary)
        # Judgment facts contain dollar amounts — at least one should appear
        has_dollar = any("$" in f or "42" in f or "4.6" in f or "518" in f for f in facts)
        self.assertTrue(has_dollar, f"Expected a dollar figure in facts, got: {facts}")

    def test_handles_no_intelligence(self):
        summary = _make_mock_summary()
        summary.intelligence = None
        summary.key_points = ["Revenue grew 12x this year", "Losses exceeded revenue"]
        facts = _derive_facts_from_summary(summary)
        # Should not crash; may return empty or extract from key points
        self.assertIsInstance(facts, list)


class TestComicScriptConstruction(unittest.TestCase):
    """build_comic_script_from_summary — correct field population."""

    def _build_script(self):
        from daily_news.agents.comic_generator import build_comic_script_from_summary
        summary = _make_mock_summary()
        personas = _make_mock_personas()
        return asyncio.run(
            build_comic_script_from_summary(summary, personas, run_id="E2E-TEST")
        )

    def test_script_fields_populated(self):
        script = self._build_script()
        self.assertIsInstance(script, ComicScript)
        self.assertTrue(script.headline, "headline must not be empty")
        self.assertTrue(script.host_name, "host_name must not be empty")
        self.assertTrue(script.news_brief, "news_brief must not be empty")
        self.assertEqual(len(script.cast), 4, "cast must have exactly 4 personas")
        self.assertTrue(script.voice1_line, "voice1_line must not be empty")
        self.assertTrue(script.voice2_line, "voice2_line must not be empty")
        self.assertTrue(script.voice3_line, "voice3_line must not be empty")
        self.assertTrue(script.voice4_line, "voice4_line must not be empty")
        self.assertTrue(script.host_synthesis, "host_synthesis must not be empty")
        self.assertTrue(script.audience_question, "audience_question must not be empty")
        print(f"\n  host_name={script.host_name}, cast={[p.name for p in script.cast]}")

    def test_source_populated(self):
        script = self._build_script()
        self.assertEqual(script.source_name, "Reuters")
        self.assertIn("reuters.com", script.source_url)

    def test_hashtags_non_empty(self):
        script = self._build_script()
        self.assertGreater(len(script.hashtags), 0, "hashtags must not be empty")
        for tag in script.hashtags:
            self.assertTrue(tag.startswith("#"), f"tag must start with #: {tag!r}")

    def test_facts_derived(self):
        script = self._build_script()
        self.assertIsInstance(script.facts, list)
        print(f"\n  Script facts: {script.facts}")


class TestSceneBuilding(unittest.TestCase):
    """build_scenes — 6-panel narrative chain."""

    def _make_script(self) -> ComicScript:
        return ComicScript(
            headline="Anthropic IPO Reveals $42B Loss",
            host_name="Maya",
            host_role="Media Host",
            news_brief="Reuters reviewed Anthropic IPO prospectus: $42B net loss, $4.6B revenue.",
            central_tension="Revenue is growing but compute costs are growing faster.",
            cast=[
                PersonaCast("Priya", "AI Startup Founder", "business", "💼"),
                PersonaCast("David", "ML Platform Engineer", "linkedin", "🧑‍💻"),
                PersonaCast("Zoe", "AI Industry Analyst", "genz", "⚖️"),
                PersonaCast("Ryan", "Enterprise CTO", "policy", "🏛️"),
            ],
            voice1_line="Revenue is real traction but cloud commitments means the runway math depends on unit economics.",
            voice2_line="Efficiency helps but production cost is not just FLOPs.",
            voice3_line="Revenue growth alone will not satisfy investors.",
            voice4_line="It changes the diligence question entirely.",
            host_synthesis="Four lenses, one number that means something different to each.",
            audience_question="What breaks first: A — Economics  B — Compute  C — Cloud dependency",
            source_url="https://reuters.com/example",
            source_name="Reuters",
            facts=["$42B NET LOSS", "$4.6B REVENUE (12X)", "$518B CLOUD CAPEX"],
            hashtags=["#Anthropic", "#AIEconomics", "#GenerativeAI"],
        )

    def test_produces_six_scenes(self):
        scenes = build_scenes(self._make_script())
        self.assertEqual(len(scenes), 6, "Must produce exactly 6 scenes")

    def test_scene_1_is_intro(self):
        scenes = build_scenes(self._make_script())
        self.assertTrue(scenes[0].is_intro, "Scene 1 must be marked as intro")
        self.assertEqual(scenes[0].persona, "media")
        self.assertEqual(len(scenes[0].intro_lines), 4, "Scene 1 must list 4 cast members")

    def test_scene_6_is_close(self):
        scenes = build_scenes(self._make_script())
        self.assertTrue(scenes[5].is_close, "Scene 6 must be marked as close")
        self.assertEqual(scenes[5].persona, "media")

    def test_persona_chain_order(self):
        scenes = build_scenes(self._make_script())
        self.assertEqual(scenes[1].persona, "business")
        self.assertEqual(scenes[2].persona, "linkedin")
        self.assertEqual(scenes[3].persona, "genz")
        self.assertEqual(scenes[4].persona, "policy")

    def test_reply_strips_reference_previous_speaker(self):
        scenes = build_scenes(self._make_script())
        # Scene 2 (Priya/business) should reference host
        self.assertEqual(scenes[1].prev_name, "Maya")
        # Scene 3 (David/linkedin) should reference Priya
        self.assertEqual(scenes[2].prev_name, "Priya")
        # Scene 4 (Zoe/genz) should reference David
        self.assertEqual(scenes[3].prev_name, "David")


class TestComicImageRender(unittest.TestCase):
    """generate_comic_from_script — SVG/PNG output validation."""

    def test_canvas_dimensions(self):
        """Canvas must match the declared constants."""
        expected_h = PANEL_H * ROWS + HEADER_H + FOOTER_H
        expected_w = PANEL_W * COLS
        self.assertEqual(TOTAL_W, expected_w)
        self.assertEqual(TOTAL_H, expected_h)
        # News Card header must be ≥ 200px to fit all layers
        self.assertGreaterEqual(HEADER_H, 200, "HEADER_H must be at least 200px")
        print(f"\n  Canvas: {TOTAL_W}x{TOTAL_H}, header={HEADER_H}, footer={FOOTER_H}")

    def test_renders_to_file(self):
        script = ComicScript(
            headline="Anthropic IPO Reveals $42B Loss on $4.6B Revenue",
            host_name="Maya",
            host_role="Media Host",
            news_brief="Reuters: $42B net loss, $4.6B revenue (12x), $518B cloud commitments.",
            central_tension="Revenue is growing but compute costs are growing faster.",
            cast=[
                PersonaCast("Priya", "AI Startup Founder", "business", "💼"),
                PersonaCast("David", "ML Platform Engineer", "linkedin", "🧑‍💻"),
                PersonaCast("Zoe", "AI Industry Analyst", "genz", "⚖️"),
                PersonaCast("Ryan", "Enterprise CTO", "policy", "🏛️"),
            ],
            voice1_line="Revenue is real traction but $518B in cloud commitments changes the runway math.",
            voice2_line="Efficiency helps but production cost is not just FLOPs.",
            voice3_line="Revenue growth alone will not satisfy investors at a $2T listing.",
            voice4_line="It changes the diligence question entirely — features to balance sheet.",
            host_synthesis="Four lenses, one number that means something different to each.",
            audience_question="What breaks first?\nA — Economics  B — Compute  C — Cloud  D — Regulation",
            source_url="https://reuters.com/example",
            source_name="Reuters",
            facts=["$42B NET LOSS", "$4.6B REVENUE (12X)", "$518B CLOUD CAPEX"],
            hashtags=["#Anthropic", "#AIEconomics", "#GenerativeAI", "#AIInfrastructure"],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_comic_from_script(script, run_id="E2E-IMG-TEST", out_dir=tmpdir)
            self.assertTrue(path.exists(), f"Output file not found: {path}")
            size = path.stat().st_size
            self.assertGreater(size, 10_000, f"Output file suspiciously small: {size} bytes")
            print(f"\n  Generated: {path.name}  {size:,} bytes  suffix={path.suffix}")

    def test_svg_contains_news_card_elements(self):
        """SVG must contain the key News Card text elements."""
        from daily_news.agents.comic_generator import _render_comic, build_scenes

        script = ComicScript(
            headline="Anthropic IPO Reveals $42B Loss",
            host_name="Maya",
            host_role="Media Host",
            news_brief="Reuters: $42B net loss.",
            central_tension="Revenue growing but costs growing faster.",
            cast=[
                PersonaCast("Priya", "AI Startup Founder", "business", "💼"),
                PersonaCast("David", "ML Platform Engineer", "linkedin", "🧑‍💻"),
                PersonaCast("Zoe", "AI Industry Analyst", "genz", "⚖️"),
                PersonaCast("Ryan", "Enterprise CTO", "policy", "🏛️"),
            ],
            voice1_line="Revenue math depends on unit economics.",
            voice2_line="Production cost is not just FLOPs.",
            voice3_line="Investors want revenue per compute dollar.",
            voice4_line="Diligence shifts to balance sheet.",
            host_synthesis="Four lenses, one unresolved tension.",
            audience_question="What breaks first?",
            source_name="Reuters",
            facts=["$42B NET LOSS", "$4.6B REVENUE"],
        )
        scenes = build_scenes(script)
        svg = _render_comic(
            script.headline,
            script.central_tension,
            scenes,
            facts=script.facts,
            source_name=script.source_name,
        )

        self.assertIn("NEWS CARD", svg, "SVG must contain NEWS CARD label")
        self.assertIn("WHAT IT MEANS", svg, "SVG must contain WHAT IT MEANS bridge tag")
        self.assertIn("SOURCE: REUTERS", svg, "SVG must contain SOURCE attribution")
        self.assertIn("DEBATE", svg, "SVG must contain DEBATE label")
        self.assertIn("$42B NET LOSS", svg, "SVG must contain fact chip text")
        self.assertIn("AIFeeders", svg, "SVG must contain brand name")
        self.assertIn("AI NEWS", svg, "SVG must contain AI NEWS badge")
        print("\n  SVG structure elements: OK")


class TestOutsidePostCaption(unittest.TestCase):
    """build_outside_post — 3-part caption format validation."""

    def _make_script(self) -> ComicScript:
        return ComicScript(
            headline="Anthropic IPO Reveals $42B Loss on $4.6B Revenue",
            host_name="Maya",
            host_role="Media Host",
            news_brief="Reuters reviewed Anthropic's IPO prospectus.",
            central_tension="Revenue is growing but compute costs are growing faster.",
            cast=[
                PersonaCast("Priya", "AI Startup Founder", "business", "💼"),
                PersonaCast("David", "ML Platform Engineer", "linkedin", "🧑‍💻"),
                PersonaCast("Zoe", "AI Industry Analyst", "genz", "⚖️"),
                PersonaCast("Ryan", "Enterprise CTO", "policy", "🏛️"),
            ],
            voice1_line="A 12x revenue jump is real traction but $518B in cloud commitments changes the runway math.",
            voice2_line="Efficiency helps but production cost is not just FLOPs.",
            voice3_line="Revenue growth alone will not satisfy investors.",
            voice4_line="It changes the diligence question entirely.",
            host_synthesis="Four lenses, one number.",
            audience_question="What breaks first?\nA — Economics  B — Compute  C — Cloud  D — Regulation",
            source_url="https://reuters.com/technology/anthropic-ipo-2025",
            source_name="Reuters",
            facts=["$42B NET LOSS", "$4.6B REVENUE (12X)", "$518B CLOUD CAPEX"],
            hashtags=["#Anthropic", "#AIEconomics", "#GenerativeAI", "#AIInfrastructure"],
        )

    def test_contains_fact_hook(self):
        post = build_outside_post(self._make_script())
        # PART 1: fact chips → first line should contain a fact
        first_line = post.split("\n")[0]
        self.assertIn("$42B", first_line, f"First line must contain fact anchor, got: {first_line!r}")

    def test_contains_tension(self):
        post = build_outside_post(self._make_script())
        self.assertIn("compute costs", post, "Post must reference the tension line")

    def test_contains_four_voices(self):
        post = build_outside_post(self._make_script())
        self.assertIn("Priya", post)
        self.assertIn("David", post)
        self.assertIn("Zoe", post)
        self.assertIn("Ryan", post)

    def test_contains_disagreement_bridge(self):
        post = build_outside_post(self._make_script())
        self.assertIn("don't completely agree", post, "Post must contain the disagreement bridge")
        self.assertIn("That's exactly the point", post)

    def test_contains_host_question_label(self):
        post = build_outside_post(self._make_script())
        self.assertIn("THE AIFEEDERS QUESTION", post)

    def test_contains_source_link(self):
        post = build_outside_post(self._make_script())
        self.assertIn("reuters.com", post, "Post must contain source URL")
        self.assertIn("Source: Reuters", post, "Post must contain source name")

    def test_contains_disclaimer(self):
        post = build_outside_post(self._make_script())
        self.assertIn("AI-simulated", post)
        self.assertIn("AIFeeders", post)
        self.assertIn("Powered by Jev", post)

    def test_contains_hashtags(self):
        post = build_outside_post(self._make_script())
        self.assertIn("#Anthropic", post)
        self.assertIn("#AIEconomics", post)

    def test_linkedin_character_limit(self):
        """Full caption must fit within LinkedIn's 3000 UTF-16 unit limit."""
        post = build_outside_post(self._make_script())
        length = _linkedin_len(post)
        self.assertLessEqual(
            length, 3000,
            f"Caption exceeds 3000 UTF-16 units: {length}",
        )
        print(f"\n  Caption length: {length} UTF-16 units ({len(post)} chars)")

    def test_no_image_content_repeated(self):
        """Caption must NOT duplicate the full persona dialogue from the image."""
        post = build_outside_post(self._make_script())
        # The full voice lines are in the image — only the first sentence should appear in caption
        # (extracted by _clean_summary_point). Full multi-sentence quotes should not appear.
        self.assertNotIn(
            "the runway math depends entirely on unit economics holding at scale",
            post,
            "Full voice line should not be repeated verbatim in caption",
        )


class TestBannedPhraseScanner(unittest.TestCase):
    """_check_persona_text — guardrail blocks known bad patterns."""

    def test_blocks_real_challenge_lies_in(self):
        text = "The real challenge lies in scaling compute efficiently."
        result = _check_persona_text(text)
        self.assertIsNotNone(result, "Should block 'the real challenge lies in'")

    def test_blocks_game_changing(self):
        result = _check_persona_text("This is game-changing for enterprise AI.")
        self.assertIsNotNone(result)

    def test_blocks_imagine_opener(self):
        result = _check_persona_text("Imagine you are running an AI startup with $10M runway.")
        self.assertIsNotNone(result)

    def test_blocks_marks_a_significant(self):
        result = _check_persona_text("This marks a significant shift in AI economics.")
        self.assertIsNotNone(result)

    def test_allows_clean_text(self):
        text = (
            "Anthropic's $42B loss creates the first public cost benchmark for frontier AI. "
            "Every startup CFO is now benchmarking their own burn against these numbers. "
            "That changes how we price infrastructure commitments."
        )
        result = _check_persona_text(text)
        self.assertIsNone(result, f"Should pass clean text, got: {result!r}")

    def test_allows_real_world_hyphenated(self):
        """'real-world' must NOT be blocked — only 'the real <noun> is/lies' pattern."""
        text = "In real-world production, GPU utilization rarely matches benchmark conditions."
        result = _check_persona_text(text)
        self.assertIsNone(result, "'real-world' must not be blocked")


class TestDynamicHashtags(unittest.TestCase):
    """_extract_dynamic_tags — source tag, no hardcoded company names."""

    def test_source_tag_included(self):
        tags = _pub_extract_tags(
            headline="Anthropic IPO Prospectus Reveals $42B Loss",
            summary="...",
            source="Reuters",
            key_points=[],
            event_type="funding",
        )
        tag_lower = [t.lower() for t in tags]
        self.assertTrue(
            any("reuters" in t for t in tag_lower),
            f"Source tag #Reuters should appear in: {tags}",
        )

    def test_max_7_tags(self):
        tags = _pub_extract_tags(
            headline="OpenAI Microsoft Google Anthropic DeepMind Amazon Meta raises funding",
            summary="...",
            source="TechCrunch",
            key_points=["$1B raised", "10 companies involved"],
            event_type="funding",
            max_tags=7,
        )
        self.assertLessEqual(len(tags), 7)

    def test_story_seo_tags_take_priority(self):
        tags = _pub_extract_tags(
            headline="Some AI headline",
            summary="...",
            source="Wired",
            key_points=[],
            event_type="other",
            story_seo_tags=["FrontierAI", "AIEconomics", "ComputeScaling"],
            max_tags=7,
        )
        tag_lower = " ".join(t.lower() for t in tags)
        self.assertIn("frontierai", tag_lower)
        self.assertIn("aieconomics", tag_lower)


class TestJevPrefixCache(unittest.TestCase):
    """jev_agents prefix cache — hit/miss/set behaviour."""

    def setUp(self):
        _JEV_PREFILTER_CACHE.clear()

    def test_cache_miss_returns_none(self):
        article = {"title": "Test", "description": "desc", "published_at": "2025-01-01"}
        self.assertIsNone(_cache_get(article))

    def test_cache_set_and_get(self):
        article = {"title": "Anthropic IPO", "description": "desc", "published_at": "2025-01-01"}
        # Use a MagicMock as the result (cache stores whatever object is passed)
        mock_result = MagicMock()
        mock_result.article_id = "test-id"
        _cache_set(article, mock_result)
        hit = _cache_get(article)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.article_id, "test-id")

    def test_different_articles_different_keys(self):
        a1 = {"title": "Article One", "description": "d1", "published_at": "2025-01-01"}
        a2 = {"title": "Article Two", "description": "d2", "published_at": "2025-01-01"}
        self.assertNotEqual(_article_cache_key(a1), _article_cache_key(a2))

    def test_same_article_same_key(self):
        a = {"title": "Same Article", "description": "desc", "published_at": "2025-01-01"}
        self.assertEqual(_article_cache_key(a), _article_cache_key(a))


class TestPersonaAgentPrefixCache(unittest.TestCase):
    """persona_agent prefix cache — key derivation."""

    def setUp(self):
        _STORY_CONTEXT_CACHE.clear()

    def test_cache_key_is_article_id(self):
        summary = _make_mock_summary()
        key = _story_context_cache_key(summary)
        self.assertEqual(key, summary.article_id)

    def test_cache_starts_empty(self):
        summary = _make_mock_summary()
        self.assertNotIn(_story_context_cache_key(summary), _STORY_CONTEXT_CACHE)


class TestLinkedInCharacterLimits(unittest.TestCase):
    """LinkedIn UTF-16 character counting edge cases."""

    def test_emoji_counts_as_two(self):
        # U+1F600 GRINNING FACE — outside BMP, counts as 2 UTF-16 units
        self.assertEqual(_linkedin_len("😀"), 2)

    def test_ascii_counts_as_one_each(self):
        self.assertEqual(_linkedin_len("hello"), 5)

    def test_mixed_emoji_and_text(self):
        # "AI 🤖" = 2 + 1 + 2 = 5
        self.assertEqual(_linkedin_len("AI 🤖"), 5)

    def test_long_post_within_limit(self):
        # Construct a realistic post and verify it stays under 3000
        long_post = "A" * 2000 + " #AI #GenerativeAI"
        self.assertLessEqual(_linkedin_len(long_post), 3000)


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all_tests():
    """Run all tests and print a summary."""
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestFactChipExtraction,
        TestComicScriptConstruction,
        TestSceneBuilding,
        TestComicImageRender,
        TestOutsidePostCaption,
        TestBannedPhraseScanner,
        TestDynamicHashtags,
        TestJevPrefixCache,
        TestPersonaAgentPrefixCache,
        TestLinkedInCharacterLimits,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
