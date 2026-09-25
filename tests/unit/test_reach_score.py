"""
Tests for ReachScoreAgent — pre-publish LinkedIn reach scorer.

Coverage:
  - _score_hook:        strong signal, weak signal, neutral, empty
  - _score_specificity: numbers, named entities, source link, bullets
  - _score_question:    numbered-choice CTA, plain CTA, no CTA
  - _score_length:      in sweet spot, too short, too long
  - _score_bait:        engagement-bait phrases, excess hashtags, clean post
  - _score_topic:       AI domain signals present / absent
  - score():            full scoring on realistic post → PUBLISH verdict
  - score():            deliberate low-quality post → REVISE verdict
  - repair():           hashtag trim, bait removal, length trim
  - ReachScore dataclass: total computation, verdict threshold
"""
from __future__ import annotations

import pytest

from daily_news.agents.reach_score_agent import (
    MAX_HASHTAGS,
    REACH_THRESHOLD,
    ReachScore,
    ReachScoreAgent,
    WORD_COUNT_MAX,
    WORD_COUNT_MIN,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

GOOD_POST = """\
Everyone is talking about AI agents. Here's the production question nobody is asking.

OpenAI Releases GPT-5 With 40% Latency Improvement

Security leaders are watching closely — this changes enterprise AI timelines.

This story is getting traction in boardrooms — decision-makers are re-evaluating vendor contracts.

Here's what you need to know:

▸ OpenAI launched GPT-5 with a 40% latency reduction over GPT-4.
▸ The new model scores 92% on MMLU and costs $0.03 per 1K tokens.
▸ Anthropic and Google are expected to respond with their own releases.

For businesses: Cost-per-query drops materially — budget model for 2027 needs revisiting.
For practitioners: Latency improvement changes real-time agentic viability immediately.
For builders: Inference infrastructure assumptions need to be re-evaluated now.

────────────────────
Different perspectives on this:

💼 Business view
The ROI calculus shifts: if inference cost drops 40%, AI-first product bets become more defensible.

🧠 Tech & careers
40% latency at same cost changes which use-cases become viable — real-time agents move from experiment to production.

Full story → https://openai.com/blog/gpt5

If you were evaluating GPT-5 for your team today, what would you prioritise?

1️⃣ Accuracy on real tasks
2️⃣ Cost at scale
3️⃣ Security & data privacy
4️⃣ Integration with existing tools

Drop your priority below. 👇

⚠️ Perspectives are AI-simulated — not professional advice.
🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev

#AI #AINews #OpenAI
"""

BAIT_POST = """\
AI is changing everything and everyone should know this.

Some company did something with artificial intelligence.

Here's what you need to know:

▸ A thing happened.
▸ Another thing happened.
▸ A third thing happened.

Comment YES if you agree! Share this post!

What do you think?

⚠️ Perspectives are AI-simulated.
🤖 AIFeeders

#AI #AINews #GenerativeAI #MachineLearning #AIStrategy #AIInnovation #DigitalTransformation #OpenAI #Anthropic
"""


# ── Unit tests: individual scoring methods ─────────────────────────────────────

class TestHookScore:
    def test_strong_hook_scores_20(self):
        text = "Everyone is talking about AI agents. Here's the production question nobody is asking."
        score, notes = ReachScoreAgent._score_hook(text + " " * 300)
        assert score == 20.0
        assert any("strong" in n for n in notes)

    def test_weak_hook_penalised(self):
        text = "Artificial intelligence is rapidly transforming the enterprise landscape."
        score, notes = ReachScoreAgent._score_hook(text + " " * 300)
        assert score < 12.0
        assert any("weak" in n for n in notes)

    def test_neutral_hook_mid_range(self):
        text = "OpenAI launched a new model today that affects enterprise users."
        score, notes = ReachScoreAgent._score_hook(text + " " * 300)
        assert 0 < score <= 12.0

    def test_empty_hook_zero(self):
        score, _ = ReachScoreAgent._score_hook("")
        assert score == 0.0


class TestSpecificityScore:
    def test_full_score_all_signals(self):
        text = "OpenAI costs $0.03/1K tokens. 40% improvement. Full story → https://example.com\n▸ Fact one."
        score, _ = ReachScoreAgent._score_specificity(text)
        assert score == 20.0

    def test_no_numbers_loses_points(self):
        text = "OpenAI released something. Full story → https://example.com\n▸ Fact one."
        score, notes = ReachScoreAgent._score_specificity(text)
        assert score < 20.0
        assert any("numeric" in n for n in notes)

    def test_no_link_loses_points(self):
        text = "OpenAI costs $0.03/1K tokens. 40% improvement.\n▸ Fact one."
        score, notes = ReachScoreAgent._score_specificity(text)
        assert score < 20.0
        assert any("source link" in n for n in notes)

    def test_no_bullets_loses_points(self):
        text = "OpenAI costs $0.03/1K tokens. Full story → https://example.com"
        score, notes = ReachScoreAgent._score_specificity(text)
        assert score < 20.0
        assert any("bullet" in n for n in notes)


class TestQuestionScore:
    def test_numbered_choice_cta_scores_20(self):
        text = "\n" * 20 + "What would you prioritise?\n\n1️⃣ Option A\n2️⃣ Option B\n\nDrop your priority below. 👇"
        score, _ = ReachScoreAgent._score_question(text)
        assert score == 20.0

    def test_generic_cta_penalised(self):
        text = "\n" * 20 + "what do you think?"
        score, notes = ReachScoreAgent._score_question(text)
        assert score <= 4.0
        assert any("generic" in n for n in notes)

    def test_single_signal_mid_range(self):
        # Only one signal: "genuinely curious" — no numbered choice, no drop-phrase
        text = "\n" * 20 + "I'm genuinely curious how this affects your workflow."
        score, _ = ReachScoreAgent._score_question(text)
        assert score == 14.0


class TestLengthScore:
    def test_in_sweet_spot_full_score(self):
        # ~200 words
        text = ("word " * 200).strip()
        score, wc, _ = ReachScoreAgent._score_length(text)
        assert score == 15.0
        assert WORD_COUNT_MIN <= wc <= WORD_COUNT_MAX

    def test_too_short_penalised(self):
        text = ("word " * 80).strip()
        score, wc, notes = ReachScoreAgent._score_length(text)
        assert score < 15.0
        assert wc < WORD_COUNT_MIN
        assert any("short" in n for n in notes)

    def test_too_long_penalised(self):
        text = ("word " * 400).strip()
        score, wc, notes = ReachScoreAgent._score_length(text)
        assert score < 15.0
        assert wc > WORD_COUNT_MAX
        assert any("above" in n for n in notes)


class TestBaitScore:
    def test_clean_post_full_score(self):
        text = "No bait here. #AI #AINews #OpenAI"
        score, ht, hits, _ = ReachScoreAgent._score_bait(text)
        assert score == 15.0
        assert hits == []
        assert ht == 3

    def test_bait_phrase_penalised(self):
        text = "Comment YES if you agree!"
        score, _, hits, notes = ReachScoreAgent._score_bait(text)
        assert score < 15.0
        assert hits
        assert any("bait" in n for n in notes)

    def test_excess_hashtags_penalised(self):
        tags = " ".join(f"#tag{i}" for i in range(MAX_HASHTAGS + 3))
        text = f"Clean content. {tags}"
        score, ht, _, notes = ReachScoreAgent._score_bait(text)
        assert score < 15.0
        assert ht > MAX_HASHTAGS
        assert any("hashtag" in n for n in notes)


class TestTopicScore:
    def test_multiple_ai_signals_full_score(self):
        text = "OpenAI released a new LLM model with AI regulation implications for enterprise."
        score, _ = ReachScoreAgent._score_topic(text)
        assert score == 10.0

    def test_two_signals_partial(self):
        text = "OpenAI and AI are mentioned here."
        score, _ = ReachScoreAgent._score_topic(text)
        assert score == 7.0

    def test_no_signals_zero(self):
        text = "The stock market rose today on positive earnings reports."
        score, notes = ReachScoreAgent._score_topic(text)
        assert score == 0.0
        assert any("off-beat" in n for n in notes)


# ── Integration tests: full scoring ───────────────────────────────────────────

class TestFullScoring:
    def test_good_post_publishes(self):
        agent = ReachScoreAgent()
        rs = agent.score(GOOD_POST)
        assert rs.verdict == "PUBLISH"
        assert rs.total >= REACH_THRESHOLD

    def test_bait_post_revises(self):
        agent = ReachScoreAgent()
        rs = agent.score(BAIT_POST)
        assert rs.verdict == "REVISE"
        assert rs.total < REACH_THRESHOLD

    def test_score_components_sum_to_total(self):
        agent = ReachScoreAgent()
        rs = agent.score(GOOD_POST)
        expected = round(
            rs.hook_strength + rs.specificity + rs.question_quality
            + rs.length_fit + rs.bait_penalty + rs.topic_coherence,
            1,
        )
        assert rs.total == expected

    def test_summary_line_contains_all_fields(self):
        agent = ReachScoreAgent()
        rs = agent.score(GOOD_POST)
        line = rs.summary_line()
        assert "reach_score=" in line
        assert "verdict=" in line
        assert "hook=" in line
        assert "words=" in line
        assert "hashtags=" in line


# ── ReachScore dataclass ───────────────────────────────────────────────────────

class TestReachScoreDataclass:
    def test_above_threshold_publish(self):
        rs = ReachScore(
            hook_strength=20, specificity=20, question_quality=20,
            length_fit=15, bait_penalty=15, topic_coherence=10,
        )
        assert rs.verdict == "PUBLISH"
        assert rs.total == 100.0

    def test_below_threshold_revise(self):
        rs = ReachScore(
            hook_strength=5, specificity=5, question_quality=5,
            length_fit=5, bait_penalty=5, topic_coherence=5,
        )
        assert rs.verdict == "REVISE"
        assert rs.total < REACH_THRESHOLD

    def test_exactly_at_threshold_publish(self):
        # Distribute exactly REACH_THRESHOLD points across dimensions
        per = REACH_THRESHOLD / 6
        rs = ReachScore(
            hook_strength=per, specificity=per, question_quality=per,
            length_fit=per, bait_penalty=per, topic_coherence=per,
        )
        assert rs.total == pytest.approx(REACH_THRESHOLD, abs=0.1)
        assert rs.verdict == "PUBLISH"


# ── Repair tests ───────────────────────────────────────────────────────────────

class TestRepair:
    def test_excess_hashtags_trimmed(self):
        many_tags = " ".join(f"#tag{i}" for i in range(10))
        post = f"Some content about AI and machine learning.\n\n{many_tags}"
        repaired = ReachScoreAgent.repair(post)
        remaining = len(__import__("re").findall(r"#\w+", repaired))
        assert remaining <= MAX_HASHTAGS

    def test_bait_lines_removed(self):
        post = "Good opener about AI.\n\nComment YES if you agree!\n\nFooter."
        repaired = ReachScoreAgent.repair(post)
        assert "Comment YES" not in repaired

    def test_oversized_post_trimmed(self):
        long_body = ("This is a sentence about artificial intelligence. " * 30)
        footer = "\nFooter line 1\nFooter line 2\nFooter line 3"
        post = long_body + footer
        repaired = ReachScoreAgent.repair(post)
        # Should be shorter
        assert len(repaired.split()) <= len(post.split())

    def test_repair_preserves_hook(self):
        post = "Everyone is talking about AI agents.\n\nComment YES if agree!\n\n#t1 #t2 #t3 #t4 #t5"
        repaired = ReachScoreAgent.repair(post)
        assert "Everyone is talking about AI agents." in repaired

    def test_repair_is_idempotent(self):
        repaired_once = ReachScoreAgent.repair(BAIT_POST)
        repaired_twice = ReachScoreAgent.repair(repaired_once)
        # Second repair should not change the post materially
        assert abs(len(repaired_once) - len(repaired_twice)) < 20
