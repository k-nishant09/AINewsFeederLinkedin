"""Workflow tests — LangGraph state machine structure."""
from __future__ import annotations

import pytest

from daily_news.workflows.daily_news_graph import (
    NewsWorkflowState,
    make_initial_state,
    route_evaluation,
)
from daily_news.models.evaluation import EvaluationDecision


def test_initial_state_has_run_id():
    state = make_initial_state()
    assert state["run_id"].startswith("RUN-")
    assert state["workflow_status"] == "STARTED"
    assert state["errors"] == []


def test_route_evaluation_pass():
    state = make_initial_state()
    state["evaluation_results"] = [
        {"article_id": "x", "decision": EvaluationDecision.PASS.value}
    ]
    assert route_evaluation(state) == "publish"


def test_route_evaluation_regenerate_within_retries():
    state = make_initial_state()
    state["retry_count"] = 0
    state["evaluation_results"] = [
        {"article_id": "x", "decision": EvaluationDecision.REGENERATE.value}
    ]
    assert route_evaluation(state) == "summarize"


def test_route_evaluation_regenerate_exceeds_retries():
    state = make_initial_state()
    state["retry_count"] = 2  # MAX_RETRIES reached
    state["evaluation_results"] = [
        {"article_id": "x", "decision": EvaluationDecision.REGENERATE.value}
    ]
    # When retries are exhausted the router sends to publish so any PASS items
    # that did succeed can still be published — it does NOT hard-stop to __end__.
    assert route_evaluation(state) == "publish"


def test_route_evaluation_empty_results():
    state = make_initial_state()
    state["evaluation_results"] = []
    assert route_evaluation(state) == "__end__"
