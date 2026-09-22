"""Workflow routes — trigger and query the LangGraph pipeline."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from daily_news.workflows.daily_news_graph import daily_news_graph, make_initial_state

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory run store — replace with PostgreSQL/Redis for production
_runs: dict[str, dict[str, Any]] = {}


class WorkflowRunResponse(BaseModel):
    run_id: str
    status: str
    message: str


@router.post("/daily-news", response_model=WorkflowRunResponse)
async def trigger_daily_news(background_tasks: BackgroundTasks):
    """Trigger the full daily news workflow asynchronously."""
    state = make_initial_state()
    run_id = state["run_id"]
    _runs[run_id] = {"status": "STARTED", "state": state}

    async def _run():
        try:
            final = await daily_news_graph.ainvoke(state)
            _runs[run_id] = {"status": final.get("workflow_status", "DONE"), "state": final}
        except Exception as exc:  # noqa: BLE001
            logger.exception("workflow %s failed", run_id)
            _runs[run_id] = {"status": "FAILED", "error": str(exc)}

    background_tasks.add_task(_run)
    return WorkflowRunResponse(run_id=run_id, status="STARTED", message="Workflow triggered.")


@router.get("/{run_id}")
async def get_workflow_status(run_id: str):
    """Get the current state of a workflow run."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return run
