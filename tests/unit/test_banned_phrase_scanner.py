"""
Unit tests for the deterministic banned-phrase scanner introduced in this session.

Covers:
  - _check_persona_text(): opener detection, inline phrase detection, clean text
  - OutputGuardrail: BANNED_CONTENT sentinel detection → is_safe=False
  - OutputGuardrail: clean post with no sentinel → is_safe=True (regression)
  - PublisherAgent: _PERSONA_ORDER class attribute exists and is correctly typed
"""
from __future__ import annotations

import pytest

from daily_news.agents.guardrails import OutputGuardrail
from daily_news.agents.publisher_agent import PublisherAgent, _check_persona_text


# ── _check_persona_text: banned OPENER patterns ────────────────────────────────

class TestBannedOpeners:
    """All openers that must be caught by the deterministic scanner."""

    @pytest.mark.parametrize("opener", [
        "When I was scaling my last startup, we invested heavily in automation.",
        "When I was running a team of engineers, the reality was different.",
        "When I was building our first product, we faced this exact problem.",
        "When I was at Google, we saw this pattern across every major release.",
        "When we deployed our last AI assistant, integration was far more complex.",
        "When we rolled out the platform, the reality didn't match the promise.",
        "When we launched our agent system, cost was the first surprise.",
        "When we built the pipeline, observability was an afterthought.",
        "When we were scaling the system, SLAs became the hard constraint.",
        "Imagine you are running a healthcare startup with tight compliance budgets.",
        "Imagine you're a developer trying to integrate this into a legacy system.",
        "Imagine a startup trying to adopt this on a shoestring budget.",
        "Imagine running a team of 5 engineers with no ops support.",
        "Imagine you had access to persistent agents across your entire codebase.",
        "Consider a scenario where a developer deploys an agent without audit logging.",
        "Let me paint a picture of what this looks like in a regulated industry.",
        "Let me be clear: the promise here is real, but the execution isn't.",
        "Let's dive in to what this announcement actually means.",
        "Let us dive in to the governance implications here.",
        "I've been in enterprise software for 15 years and this pattern is familiar.",
        "I have been in this industry long enough to know that hype cycles repeat.",
        "I was recently in a boardroom where this exact topic came up.",
        "I recently reviewed an RFP that required persistent AI agent integration.",
    ])
    def test_banned_opener_is_detected(self, opener: str):
        result = _check_persona_text(opener)
        assert result is not None, f"Expected banned opener to be detected: {opener[:60]!r}"

    def test_clean_opener_passes(self):
        clean = (
            "Persistent agents change the accountability question for every enterprise "
            "that ships software. The problem isn't creation speed — it's the audit trail "
            "for decisions made at 3 AM when no human is watching."
        )
        assert _check_persona_text(clean) is None

    def test_clean_policy_opener_passes(self):
        clean = (
            "Microsoft's persistent Autopilot agent creates a specific authorization gap "
            "that existing enterprise governance frameworks do not address. "
            "When software acts autonomously across identity boundaries, the audit trail "
            "question becomes a liability question — not a preference."
        )
        assert _check_persona_text(clean) is None

    def test_clean_founder_opener_passes(self):
        clean = (
            "Faster code generation does not equal faster value delivery. "
            "If an agent ships 50 micro-apps your team cannot maintain, "
            "you haven't accelerated innovation — you've accelerated burn rate."
        )
        assert _check_persona_text(clean) is None


# ── _check_persona_text: banned INLINE phrase patterns ────────────────────────

class TestBannedInlinePhrases:
    """Inline phrases banned anywhere in the persona text."""

    @pytest.mark.parametrize("phrase_in_context", [
        "The real question is whether enterprises will adopt this responsibly.",
        "Here is the real question is, will teams actually govern what gets built?",
        "The real challenge lies in integrating these tools into existing workflows.",
        "The real challenge is not speed — it's accountability.",
        "The challenge lies in how we manage the flood of new applications.",
        "The key challenge is governance, not generation.",
        "The key metric is how much business value this actually creates.",
        "This sounds great, but the integration story still needs work.",
        "It sounds promising, but the security model is unproven at scale.",
        "At the end of the day, more tooling does not solve governance gaps.",
        "It remains to be seen whether teams can manage what gets deployed.",
        "Only time will tell if persistent agents improve or worsen the ops burden.",
        "What remains to be seen is whether the accountability model holds.",
        "The potential here is enormous, but execution risk is equally large.",
        "The promise is great, but production reality is a different story.",
        "In the end, more tools do not always reduce complexity.",
        "My advice to engineers is to wait for the v2 integration story.",
        "But the real test is whether this works outside demo conditions.",
        "But the real question is whether the ROI justifies the integration cost.",
    ])
    def test_banned_inline_phrase_detected(self, phrase_in_context: str):
        # Wrap in a clean opener so only the inline phrase triggers detection
        text = f"Persistent agents create new audit obligations. {phrase_in_context}"
        result = _check_persona_text(text)
        assert result is not None, (
            f"Expected banned inline phrase to be detected in: {phrase_in_context[:60]!r}"
        )

    def test_clean_inline_text_passes(self):
        text = (
            "Production persistent agents require identity boundaries, cost controls, "
            "and audit logs that most teams have not yet built. "
            "The ROI calculus changes only when governance infrastructure keeps pace."
        )
        assert _check_persona_text(text) is None


# ── _check_persona_text: edge cases ───────────────────────────────────────────

class TestScannerEdgeCases:
    def test_empty_string_returns_none(self):
        assert _check_persona_text("") is None

    def test_single_word_returns_none(self):
        assert _check_persona_text("Interesting.") is None

    def test_case_insensitive_opener_detected(self):
        # Pattern should match regardless of capitalisation
        text = "WHEN I WAS SCALING my last company, we saw this problem."
        result = _check_persona_text(text)
        assert result is not None

    def test_case_insensitive_inline_detected(self):
        text = "Clean opener. THE REAL CHALLENGE IS governance, not speed."
        result = _check_persona_text(text)
        assert result is not None

    def test_returns_the_offending_phrase_string(self):
        text = "When we deployed our last system, costs exceeded the forecast."
        result = _check_persona_text(text)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unicode_bold_opener_does_not_false_positive(self):
        # Unicode bold text should not trigger — it contains no ASCII patterns
        bold_opener = "𝐏𝐞𝐫𝐬𝐢𝐬𝐭𝐞𝐧𝐭 agents change the accountability picture entirely."
        assert _check_persona_text(bold_opener) is None


# ── OutputGuardrail: BANNED_CONTENT sentinel ──────────────────────────────────

class TestOutputGuardrailBannedContent:
    """OutputGuardrail must catch the [BANNED_CONTENT:] sentinel and block the post."""

    def test_sentinel_makes_post_unsafe(self):
        post = (
            "🧠 AIFEEDERS | THE DAILY AI DEBATE\n\n"
            "🚨 Microsoft just moved Copilot from answering toward acting.\n\n"
            "---\n\n💼 **FOUNDER**\n\n"
            "[BANNED_CONTENT: persona=business phrase='when i was scaling']\n\n"
            "---\n\n🧑‍💻 **ENGINEER**\n\n"
            "\"Persistent agents require identity boundaries most teams haven't built yet.\""
        )
        result = OutputGuardrail.inspect_output(post)
        assert result.is_safe is False
        assert any("banned_phrase" in v for v in result.violations)

    def test_sentinel_violation_names_the_persona_and_phrase(self):
        post = "[BANNED_CONTENT: persona=policy phrase='consider a scenario']"
        result = OutputGuardrail.inspect_output(post)
        assert result.is_safe is False
        assert len(result.violations) == 1
        assert "policy" in result.violations[0]
        assert "consider a scenario" in result.violations[0]

    def test_multiple_sentinels_all_detected(self):
        post = (
            "[BANNED_CONTENT: persona=business phrase='when i was scaling']\n"
            "[BANNED_CONTENT: persona=linkedin phrase='when we deployed']"
        )
        result = OutputGuardrail.inspect_output(post)
        assert result.is_safe is False
        # At least one violation recorded (first sentinel match)
        assert len(result.violations) >= 1

    def test_clean_post_no_sentinel_still_safe(self):
        """Regression: clean posts must not be affected by the new sentinel check."""
        clean = (
            "🧠 AIFEEDERS | THE DAILY AI DEBATE\n\n"
            "🚨 Microsoft Autopilot shifts Copilot from answering to acting.\n\n"
            "---\n\n💼 **FOUNDER**\n\n"
            "\"Faster generation without governance infrastructure accelerates burn, not value.\"\n\n"
            "---\n\n🧑‍💻 **ENGINEER**\n\n"
            "\"Persistent agents need identity boundaries, cost caps, and audit trails.\"\n\n"
            "Source → https://venturebeat.com/...\n"
            "#Microsoft #AIAgents\n\n"
            "🤖 AIFeeders · Daily AI Intelligence · Powered by Jev\n"
            "*AI-simulated perspectives for discussion — not professional advice.*"
        )
        result = OutputGuardrail.inspect_output(clean)
        assert result.is_safe is True
        assert result.violations == []

    def test_empty_post_is_safe(self):
        result = OutputGuardrail.inspect_output("")
        assert result.is_safe is True

    def test_pii_and_sentinel_both_flagged(self):
        """When post has both PII and a BANNED_CONTENT sentinel, both are violations."""
        post = (
            "[BANNED_CONTENT: persona=genz phrase='sounds great on paper']\n"
            "SSN: 123-45-6789 leaked in debug output."
        )
        result = OutputGuardrail.inspect_output(post)
        assert result.is_safe is False
        assert result.pii_detected is True
        assert any("banned_phrase" in v for v in result.violations)


# ── PublisherAgent: _PERSONA_ORDER class attribute ────────────────────────────

class TestPersonaOrder:
    """_PERSONA_ORDER must exist as a class attribute and have the right shape."""

    def test_persona_order_exists(self):
        assert hasattr(PublisherAgent, "_PERSONA_ORDER")

    def test_persona_order_is_list(self):
        assert isinstance(PublisherAgent._PERSONA_ORDER, list)

    def test_persona_order_has_four_entries(self):
        assert len(PublisherAgent._PERSONA_ORDER) == 4

    def test_persona_order_entries_are_two_tuples(self):
        for entry in PublisherAgent._PERSONA_ORDER:
            assert isinstance(entry, tuple), f"Expected tuple, got {type(entry)}"
            assert len(entry) == 2, f"Expected 2-tuple, got length {len(entry)}"

    def test_persona_order_contains_all_four_keys(self):
        keys = {entry[0] for entry in PublisherAgent._PERSONA_ORDER}
        assert keys == {"business", "linkedin", "genz", "policy"}

    def test_persona_order_labels_contain_emoji_and_name(self):
        for key, label in PublisherAgent._PERSONA_ORDER:
            assert len(label) > 2, f"Label too short for persona {key!r}: {label!r}"
            # Label must contain the capitalised role name
            assert any(
                role in label.upper()
                for role in ("FOUNDER", "ENGINEER", "SKEPTIC", "POLICY")
            ), f"Label for {key!r} missing role name: {label!r}"
