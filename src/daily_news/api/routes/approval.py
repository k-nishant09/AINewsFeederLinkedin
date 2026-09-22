"""Approval routes — human-in-the-loop gate for workflow runs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# Shared run store reference (imported from workflow router in production
# would be a DB-backed service)
from daily_news.api.routes.workflow import _runs  # noqa: E402


class ApprovalDecision(BaseModel):
    reviewer: str
    notes: str = ""


@router.post("/{run_id}/approve")
async def approve_run(run_id: str, decision: ApprovalDecision):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    run["state"]["approval_status"] = "APPROVED"
    run["state"]["approval_reviewer"] = decision.reviewer
    run["state"]["approval_notes"] = decision.notes
    return {"run_id": run_id, "approval_status": "APPROVED"}


@router.post("/{run_id}/reject")
async def reject_run(run_id: str, decision: ApprovalDecision):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    run["state"]["approval_status"] = "REJECTED"
    run["state"]["approval_reviewer"] = decision.reviewer
    run["state"]["approval_notes"] = decision.notes
    return {"run_id": run_id, "approval_status": "REJECTED"}
