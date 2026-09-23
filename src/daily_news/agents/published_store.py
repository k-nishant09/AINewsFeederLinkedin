"""
PublishedStore — persistent cross-run deduplication for AIFeeders.

Problem
───────
The CronJob may be retried, re-triggered manually, or run twice on the same day.
Without a persistent store, the same article (same article_id + same date) would
be published multiple times.

Design
───────
- Backed by a single JSON file: AIFEEDERS_STORE_PATH env var, default /tmp/aifeeders_published.json
- Keys: "{article_id}:{YYYY-MM-DD}"  — one entry per article per calendar day
- Values: ISO-8601 published_at timestamp
- Thread-safe: file is read/written under a threading.Lock; safe for a single process
- Auto-purge: entries older than STORE_TTL_DAYS (default 7) are removed on each load
  to keep the file small across long-running pods
- Failure-safe: if the store file cannot be read/written (permissions, disk full),
  the error is logged and publishing is NOT blocked — availability beats deduplication

Usage
─────
    store = PublishedStore()

    # Check before publishing
    if store.is_published(article_id):
        return skipped_result(...)

    # Mark after successful publish
    store.mark_published(article_id)

    # Filter a list of articles (used in the deduplicate graph node)
    fresh = store.filter_unpublished(articles)   # articles: list[dict] with "article_id" key
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH    = Path(os.getenv("AIFEEDERS_STORE_PATH", "/tmp/aifeeders_published.json"))
_STORE_TTL_DAYS  = int(os.getenv("AIFEEDERS_STORE_TTL_DAYS", "7"))


def _today() -> str:
    return date.today().isoformat()


def _key(article_id: str) -> str:
    return f"{article_id}:{_today()}"


class PublishedStore:
    """
    Thread-safe, file-backed store of published article IDs.

    A single shared instance is sufficient per process — use the module-level
    singleton `published_store` for all production use.
    """

    def __init__(self, path: Path = _DEFAULT_PATH) -> None:
        self._path  = path
        self._lock  = threading.Lock()
        self._cache: dict[str, str] = {}   # key → published_at ISO string
        self._loaded = False

    # ── Public API ────────────────────────────────────────────────────────────

    def is_published(self, article_id: str) -> bool:
        """Return True if this article was already published today."""
        self._ensure_loaded()
        return _key(article_id) in self._cache

    def mark_published(self, article_id: str) -> None:
        """Record a successful publish.  Silently no-ops on I/O error."""
        self._ensure_loaded()
        key = _key(article_id)
        with self._lock:
            self._cache[key] = datetime.now(tz=timezone.utc).isoformat()
            self._flush_locked()

    def filter_unpublished(self, articles: list[dict]) -> list[dict]:
        """
        Return only articles not yet published today.
        Logs each filtered article at INFO level so operators can see what was skipped.
        """
        self._ensure_loaded()
        fresh, skipped = [], []
        for a in articles:
            if self.is_published(a.get("article_id", "")):
                skipped.append(a.get("article_id", "?"))
            else:
                fresh.append(a)
        if skipped:
            logger.info(
                "published_store: skipping %d already-published articles: %s",
                len(skipped), skipped,
            )
        return fresh

    def clear_today(self) -> None:
        """Remove all entries for today — useful in tests / manual resets."""
        self._ensure_loaded()
        today = _today()
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if not k.endswith(today)}
            self._flush_locked()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_locked()
            self._loaded = True

    def _load_locked(self) -> None:
        """Read from disk, purge stale entries, populate cache."""
        if not self._path.exists():
            self._cache = {}
            return
        try:
            raw: dict[str, str] = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("published_store: failed to read %s (%s) — starting empty", self._path, exc)
            self._cache = {}
            return

        # Purge entries older than TTL
        cutoff = (date.today() - timedelta(days=_STORE_TTL_DAYS)).isoformat()
        purged = 0
        fresh: dict[str, str] = {}
        for k, v in raw.items():
            parts = k.rsplit(":", 1)
            if len(parts) == 2 and parts[1] >= cutoff:
                fresh[k] = v
            else:
                purged += 1

        if purged:
            logger.debug("published_store: purged %d stale entries", purged)

        self._cache = fresh
        logger.debug("published_store: loaded %d entries from %s", len(fresh), self._path)

    def _flush_locked(self) -> None:
        """Write cache to disk.  Must be called while holding self._lock."""
        try:
            self._path.write_text(
                json.dumps(self._cache, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("published_store: failed to write %s (%s) — dedup state not persisted", self._path, exc)


# Module-level singleton — shared across all nodes in the same process
published_store = PublishedStore()
