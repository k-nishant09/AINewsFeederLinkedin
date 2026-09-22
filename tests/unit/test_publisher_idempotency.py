"""Unit tests — publisher idempotency and post composition."""
from __future__ import annotations

import hashlib
from datetime import date

from daily_news.agents.publisher_agent import PublisherAgent


class TestPublicationKey:
    def test_same_article_and_date_produce_same_key(self):
        text = "AI news post"
        key1 = PublisherAgent._make_publication_key("article-001", text)
        key2 = PublisherAgent._make_publication_key("article-001", text)
        assert key1 == key2

    def test_different_text_produces_different_key(self):
        key1 = PublisherAgent._make_publication_key("article-001", "text A")
        key2 = PublisherAgent._make_publication_key("article-001", "text B")
        assert key1 != key2

    def test_key_contains_article_id(self):
        key = PublisherAgent._make_publication_key("article-XYZ", "some text")
        assert "article-XYZ" in key

    def test_key_contains_today(self):
        key = PublisherAgent._make_publication_key("article-001", "text")
        assert date.today().isoformat() in key
