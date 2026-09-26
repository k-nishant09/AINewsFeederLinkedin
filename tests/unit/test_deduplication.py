"""
Unit tests — all three deduplication passes in the daily news workflow.

Pass 1 — URL normalisation + exact hash dedup (within a single run)
Pass 2 — PublishedStore cross-run dedup (already published today)
Pass 3 — Title-similarity dedup (same story, different sources)

Also tests the pure helper functions: _normalise_url, _normalise_title,
_title_similarity, and the _TITLE_SIMILARITY_THRESHOLD constant.
"""
from __future__ import annotations

import pytest

from daily_news.workflows.daily_news_graph import (
    _TITLE_SIMILARITY_THRESHOLD,
    _normalise_title,
    _normalise_url,
    _title_similarity,
    deduplicate,
    make_initial_state,
)

# ── Helper: build a minimal article dict ─────────────────────────────────────

def _art(article_id: str, title: str, url: str) -> dict:
    return {"article_id": article_id, "title": title, "url": url}


# ─────────────────────────────────────────────────────────────────────────────
# _normalise_url
# ─────────────────────────────────────────────────────────────────────────────

class TestNormaliseUrl:

    def test_strips_https(self):
        assert _normalise_url("https://bbc.com/news/ai") == "bbc.com/news/ai"

    def test_strips_http(self):
        assert _normalise_url("http://bbc.com/news/ai") == "bbc.com/news/ai"

    def test_strips_www(self):
        assert _normalise_url("https://www.bbc.com/news") == "bbc.com/news"

    def test_strips_trailing_slash(self):
        assert _normalise_url("https://bbc.com/news/ai/") == "bbc.com/news/ai"

    def test_lowercases(self):
        assert _normalise_url("https://BBC.COM/News") == "bbc.com/news"

    def test_http_and_www_and_trailing_slash(self):
        assert _normalise_url("http://www.example.com/path/") == "example.com/path"

    def test_empty_string(self):
        assert _normalise_url("") == ""

    def test_already_normalised(self):
        assert _normalise_url("bbc.com/news/ai") == "bbc.com/news/ai"

    def test_different_paths_stay_different(self):
        a = _normalise_url("https://bbc.com/news/ai")
        b = _normalise_url("https://bbc.com/news/policy")
        assert a != b


# ─────────────────────────────────────────────────────────────────────────────
# _normalise_title
# ─────────────────────────────────────────────────────────────────────────────

class TestNormaliseTitle:

    def test_lowercases(self):
        result = _normalise_title("OpenAI Releases GPT-5")
        assert result == result.lower()

    def test_strips_punctuation(self):
        result = _normalise_title("OpenAI: What You Need to Know!")
        assert ":" not in result
        assert "!" not in result

    def test_removes_stop_words(self):
        result = _normalise_title("The New AI Model is Here")
        assert "the" not in result.split()
        assert "is" not in result.split()

    def test_collapses_whitespace(self):
        result = _normalise_title("AI  Model   Release")
        assert "  " not in result

    def test_empty_title(self):
        assert _normalise_title("") == ""

    def test_all_stop_words(self):
        # A title made entirely of stop words should produce empty string
        result = _normalise_title("the and or but in on at")
        assert result == ""

    def test_preserves_meaningful_words(self):
        result = _normalise_title("OpenAI releases GPT-5 model")
        assert "openai" in result
        assert "releases" in result
        assert "gpt5" in result or "gpt" in result  # hyphen stripped


# ─────────────────────────────────────────────────────────────────────────────
# _title_similarity
# ─────────────────────────────────────────────────────────────────────────────

class TestTitleSimilarity:

    def test_identical_titles_score_one(self):
        t = _normalise_title("OpenAI Releases GPT-5 Model")
        assert _title_similarity(t, t) == pytest.approx(1.0)

    def test_completely_different_titles_score_low(self):
        a = _normalise_title("OpenAI releases GPT-5 language model")
        b = _normalise_title("NVIDIA announces next generation GPU chip")
        assert _title_similarity(a, b) < 0.2

    def test_near_duplicate_scores_above_threshold(self):
        # Same story, slightly different wording (BBC vs Reuters style).
        # "Releases" vs "Launches" and minor word differences — Jaccard ~0.57 > 0.55.
        a = _normalise_title("Anthropic Releases Claude 4 Safety Model AI")
        b = _normalise_title("Anthropic Launches Claude 4 Safety AI Model System")
        assert _title_similarity(a, b) >= _TITLE_SIMILARITY_THRESHOLD

    def test_both_empty_scores_one(self):
        assert _title_similarity("", "") == pytest.approx(1.0)

    def test_one_empty_scores_zero(self):
        assert _title_similarity("openai gpt5", "") == pytest.approx(0.0)
        assert _title_similarity("", "openai gpt5") == pytest.approx(0.0)

    def test_single_shared_word_low_score(self):
        a = "openai gpt5 reasoning benchmark enterprise adoption"
        b = "nvidia gpu chip datacenter openai"
        # Only "openai" shared — should be well below threshold
        sim = _title_similarity(a, b)
        assert sim < _TITLE_SIMILARITY_THRESHOLD

    def test_threshold_is_conservative(self):
        # Threshold must be between 0.5 and 0.8 (conservative but not too strict)
        assert 0.5 <= _TITLE_SIMILARITY_THRESHOLD <= 0.8


# ─────────────────────────────────────────────────────────────────────────────
# deduplicate() node — integration of all three passes
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDeduplicateNode:

    def _state(self, articles: list[dict]) -> dict:
        s = make_initial_state()
        s["raw_articles"] = articles
        return s

    # ── Pass 1: URL normalisation ────────────────────────────────────────────

    async def test_pass1_exact_duplicate_url_removed(self):
        articles = [
            _art("a1", "OpenAI Releases GPT-5", "https://bbc.com/news/ai"),
            _art("a2", "OpenAI Releases GPT-5", "https://bbc.com/news/ai"),  # exact dup
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 1

    async def test_pass1_http_vs_https_deduplicated(self):
        articles = [
            _art("a1", "OpenAI Releases GPT-5", "http://bbc.com/news/ai"),
            _art("a2", "OpenAI Releases GPT-5", "https://bbc.com/news/ai"),  # same URL, different scheme
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 1

    async def test_pass1_www_prefix_deduplicated(self):
        articles = [
            _art("a1", "AI News Story", "https://www.bbc.com/ai"),
            _art("a2", "AI News Story", "https://bbc.com/ai"),  # same without www
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 1

    async def test_pass1_trailing_slash_deduplicated(self):
        articles = [
            _art("a1", "AI News Story", "https://bbc.com/ai/"),
            _art("a2", "AI News Story", "https://bbc.com/ai"),  # same without trailing slash
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 1

    async def test_pass1_different_urls_both_kept(self):
        articles = [
            _art("a1", "OpenAI story", "https://bbc.com/openai"),
            _art("a2", "Anthropic story", "https://reuters.com/anthropic"),
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 2

    async def test_pass1_content_hash_written(self):
        articles = [_art("a1", "AI story", "https://bbc.com/ai")]
        result = await deduplicate(self._state(articles))
        kept = result["deduplicated_articles"]
        assert len(kept) == 1
        assert "content_hash" in kept[0]
        assert len(kept[0]["content_hash"]) == 32  # MD5 hex

    # ── Pass 2: PublishedStore cross-run dedup ───────────────────────────────

    async def test_pass2_already_published_today_filtered(self):
        from daily_news.agents.published_store import published_store
        # Temporarily mark a1 as published
        published_store.mark_published("dup-a1")
        try:
            articles = [
                _art("dup-a1", "Already Published Story", "https://bbc.com/published"),
                _art("dup-a2", "Fresh Story Today",       "https://bbc.com/fresh"),
            ]
            result = await deduplicate(self._state(articles))
            ids = [a["article_id"] for a in result["deduplicated_articles"]]
            assert "dup-a1" not in ids
            assert "dup-a2" in ids
        finally:
            published_store.clear_today()

    async def test_pass2_unpublished_articles_pass_through(self):
        from daily_news.agents.published_store import published_store
        # Ensure clean slate for these ids
        published_store.clear_today()
        articles = [
            _art("fresh-x1", "Fresh Article One", "https://bbc.com/x1"),
            _art("fresh-x2", "Fresh Article Two", "https://bbc.com/x2"),
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 2

    # ── Pass 3: Title-similarity dedup ──────────────────────────────────────

    async def test_pass3_same_story_different_source_deduplicated(self):
        # BBC and Reuters covering the same Anthropic event — near-identical titles.
        # "Releases" vs "Launches", Jaccard ~0.625 > threshold 0.55.
        articles = [
            _art("bbc-1",     "Anthropic Releases Claude 4 Safety Model AI",           "https://bbc.com/claude4"),
            _art("reuters-1", "Anthropic Launches Claude 4 Safety AI Model System",     "https://reuters.com/claude4"),
        ]
        result = await deduplicate(self._state(articles))
        # Only one should survive
        assert len(result["deduplicated_articles"]) == 1
        # The first one (bbc-1) wins — it was first in the list
        assert result["deduplicated_articles"][0]["article_id"] == "bbc-1"

    async def test_pass3_completely_different_stories_both_kept(self):
        articles = [
            _art("a1", "OpenAI Releases GPT-5 Language Model Benchmark",    "https://bbc.com/openai"),
            _art("a2", "NVIDIA Announces New Hopper GPU Architecture Chip",  "https://techcrunch.com/nvidia"),
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 2

    async def test_pass3_first_article_wins_in_cluster(self):
        # Three articles about the same story — only the first should survive
        articles = [
            _art("first",  "Anthropic Releases Claude 4 Safety Model AI",       "https://bbc.com/claude4"),
            _art("second", "Anthropic Launches Claude 4 Safety AI Model System", "https://reuters.com/claude4"),
            _art("third",  "Anthropic Unveils Claude 4 Safety AI Model Release", "https://techcrunch.com/claude4"),
        ]
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 1
        assert result["deduplicated_articles"][0]["article_id"] == "first"

    async def test_pass3_empty_titles_do_not_crash(self):
        articles = [
            _art("a1", "", "https://bbc.com/a1"),
            _art("a2", "", "https://reuters.com/a2"),
        ]
        # Both have empty titles → similarity = 1.0 → second is dropped
        result = await deduplicate(self._state(articles))
        assert len(result["deduplicated_articles"]) == 1

    async def test_pass3_partial_overlap_below_threshold_kept(self):
        # Articles share some words but are genuinely different stories
        articles = [
            _art("a1", "OpenAI Announces New AI Safety Research Programme",  "https://bbc.com/openai-safety"),
            _art("a2", "OpenAI Reports Strong Revenue Growth Enterprise AI",  "https://reuters.com/openai-revenue"),
        ]
        result = await deduplicate(self._state(articles))
        # "OpenAI" and "AI" shared but not enough to hit threshold after normalisation
        assert len(result["deduplicated_articles"]) == 2

    # ── Combined: all three passes interact correctly ────────────────────────

    async def test_all_passes_combined(self):
        """
        A realistic input pool:
          - a1 & a2: same URL (http vs https) → Pass 1 drops a2
          - a3: already published today → Pass 2 drops a3
          - a4 & a5: same story, different sources → Pass 3 drops a5
          - a6: genuinely unique → survives all passes
        Expected survivors: a1, a4, a6
        NVIDIA "Announces/Releases" pairs score ~0.571 > threshold 0.55.
        """
        from daily_news.agents.published_store import published_store
        published_store.mark_published("combo-a3")
        try:
            articles = [
                _art("combo-a1", "OpenAI Releases GPT-5 Model",                        "https://bbc.com/gpt5"),
                _art("combo-a2", "OpenAI Releases GPT-5 Model",                        "http://bbc.com/gpt5"),  # http dup of a1
                _art("combo-a3", "Published Story Yesterday Still Indexed",             "https://reuters.com/published"),  # already in store
                _art("combo-a4", "Anthropic Releases Claude 4 Safety Model AI",        "https://techcrunch.com/claude4"),
                _art("combo-a5", "Anthropic Launches Claude 4 Safety AI Model System", "https://theverge.com/claude4"),  # near-dup of a4, sim=0.625
                _art("combo-a6", "EU AI Act Enforcement Begins October 2026",           "https://euractiv.com/eu-ai-act"),
            ]
            result = await deduplicate(self._state(articles))
            ids = [a["article_id"] for a in result["deduplicated_articles"]]
            assert "combo-a1" in ids, "a1 should survive"
            assert "combo-a2" not in ids, "a2 is URL duplicate of a1"
            assert "combo-a3" not in ids, "a3 was already published today"
            assert "combo-a4" in ids, "a4 should survive"
            assert "combo-a5" not in ids, "a5 is title near-duplicate of a4"
            assert "combo-a6" in ids, "a6 is unique and should survive"
            assert len(ids) == 3
        finally:
            published_store.clear_today()

    async def test_workflow_status_set_correctly(self):
        articles = [_art("s1", "Some AI Story", "https://bbc.com/ai")]
        result = await deduplicate(self._state(articles))
        assert result["workflow_status"] == "DEDUPLICATED"

    async def test_empty_raw_articles(self):
        result = await deduplicate(self._state([]))
        assert result["deduplicated_articles"] == []
        assert result["workflow_status"] == "DEDUPLICATED"
