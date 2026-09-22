"""Prometheus metrics definitions."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

workflow_runs_total = Counter(
    "daily_news_workflow_runs_total",
    "Total workflow runs",
    ["status"],
)

articles_discovered_total = Counter(
    "daily_news_articles_discovered_total",
    "Total articles discovered",
)

articles_published_total = Counter(
    "daily_news_articles_published_total",
    "Total articles published to LinkedIn",
)

evaluation_scores = Histogram(
    "daily_news_evaluation_scores",
    "Evaluation metric scores",
    ["metric"],
    buckets=[0.5, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0],
)

workflow_duration_seconds = Histogram(
    "daily_news_workflow_duration_seconds",
    "End-to-end workflow duration",
    buckets=[30, 60, 120, 300, 600, 1200, 1800],
)

active_workflow_runs = Gauge(
    "daily_news_active_workflow_runs",
    "Currently running workflow instances",
)
