"""Unit tests — PublishedStore cross-run deduplication."""
from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from daily_news.agents.published_store import PublishedStore, _today


class TestPublishedStore:

    def _store(self, tmp_path: Path) -> PublishedStore:
        return PublishedStore(path=tmp_path / "published.json")

    def test_new_article_not_published(self, tmp_path):
        s = self._store(tmp_path)
        assert not s.is_published("article-001")

    def test_mark_and_check(self, tmp_path):
        s = self._store(tmp_path)
        s.mark_published("article-001")
        assert s.is_published("article-001")

    def test_different_article_not_affected(self, tmp_path):
        s = self._store(tmp_path)
        s.mark_published("article-001")
        assert not s.is_published("article-002")

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "published.json"
        s1 = PublishedStore(path=path)
        s1.mark_published("article-001")

        # New instance reads same file
        s2 = PublishedStore(path=path)
        assert s2.is_published("article-001")

    def test_filter_unpublished_removes_already_published(self, tmp_path):
        s = self._store(tmp_path)
        s.mark_published("a1")
        articles = [
            {"article_id": "a1"},
            {"article_id": "a2"},
            {"article_id": "a3"},
        ]
        fresh = s.filter_unpublished(articles)
        assert [a["article_id"] for a in fresh] == ["a2", "a3"]

    def test_filter_unpublished_all_new(self, tmp_path):
        s = self._store(tmp_path)
        articles = [{"article_id": "x1"}, {"article_id": "x2"}]
        assert s.filter_unpublished(articles) == articles

    def test_stale_entries_purged_on_load(self, tmp_path):
        path = tmp_path / "published.json"
        # Write an entry from 10 days ago
        old_date = (date.today() - timedelta(days=10)).isoformat()
        path.write_text(json.dumps({f"article-old:{old_date}": "2025-01-01T00:00:00+00:00"}))

        s = PublishedStore(path=path)
        # Should not surface as published today
        assert not s.is_published("article-old")
        # Cache should be empty after purge
        assert s._cache == {}

    def test_today_entries_survive_purge(self, tmp_path):
        path = tmp_path / "published.json"
        today = _today()
        path.write_text(json.dumps({f"article-today:{today}": "2025-01-01T00:00:00+00:00"}))

        s = PublishedStore(path=path)
        assert s.is_published("article-today")

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "published.json"
        path.write_text("NOT VALID JSON }{")
        s = PublishedStore(path=path)
        assert not s.is_published("article-001")

    def test_clear_today(self, tmp_path):
        s = self._store(tmp_path)
        s.mark_published("article-001")
        s.mark_published("article-002")
        s.clear_today()
        assert not s.is_published("article-001")
        assert not s.is_published("article-002")

    def test_key_is_scoped_to_today(self, tmp_path):
        path = tmp_path / "published.json"
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # Simulate yesterday's entry for the same article_id
        path.write_text(json.dumps({f"article-001:{yesterday}": "2025-01-01T00:00:00+00:00"}))
        s = PublishedStore(path=path)
        # Yesterday is within TTL but today's key is different
        assert not s.is_published("article-001")
