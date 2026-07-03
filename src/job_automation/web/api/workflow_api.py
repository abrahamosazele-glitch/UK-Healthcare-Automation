"""
JSON API for workflow status/transitions plus applications data — grouped
together since an "application" in this system *is* an
`ApplicationWorkflowRecord` at its later stages, not a separate object (see
`routes/applications.py`'s docstring). Every mutation goes through
`WorkflowService`, which already owns validated transitions, status
history, and audit logging (see `workflows/status_manager.py` /
`workflows/application_workflow.py`) — this file adds no new business
rules, only maps HTTP actions onto the existing named methods
(`submit_for_review`, `approve`, `reject`, `mark_ready_to_apply`,
`mark_applied`, `mark_interview`, `mark_offer`, `close`).

The Workflow dashboard page itself is read-only (a timeline view, per the
milestone's page spec) — these transition endpoints exist for
programmatic/future use and are exercised directly by tests, the same way
a REST API commonly exposes more than the current UI wires up.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.orm import Session

from job_automation.database.models.application_workflow_record import ApplicationWorkflowRecord
from job_automation.database.models.user import User
from job_automation.database.models.workflow_status_history_record import WorkflowStatusHistoryRecord
from job_automation.web.app import get_current_api_user, get_db_session
from job_automation.web.routes.applications import build_application_rows
from job_automation.workflows.application_workflow import InvalidTransitionError
from job_automation.workflows.workflow_repository import WorkflowRepository
from job_automation.workflows.workflow_service import WorkflowService

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

_TRANSITION_ACTIONS = {
    "submit_for_review": lambda service, workflow, note: service.submit_for_review(workflow),
    "approve": lambda service, workflow, note: service.approve(workflow, reviewer_notes=note),
    "reject": lambda service, workflow, note: service.reject(workflow, reviewer_notes=note),
    "mark_ready_to_apply": lambda service, workflow, note: service.mark_ready_to_apply(workflow),
    "mark_applied": lambda service, workflow, note: service.mark_applied(workflow, note=note),
    "mark_interview": lambda service, workflow, note: service.mark_interview(workflow, note=note),
    "mark_offer": lambda service, workflow, note: service.mark_offer(workflow, note=note),
    "close": lambda service, workflow, note: service.close(workflow, reason=note),
}


@router.get("")
def list_workflows(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> list[dict]:
    workflows = WorkflowRepository(session).list_for_user(current_user.id)
    return [_workflow_to_dict(workflow) for workflow in workflows]


@router.get("/applications")
def list_applications(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> list[dict]:
    rows = build_application_rows(WorkflowRepository(session), current_user.id)
    return [
        {
            "workflow": _workflow_to_dict(row.workflow),
            "interview_date": row.interview_date.isoformat() if row.interview_date else None,
            "has_offer": row.has_offer,
            "latest_note": row.latest_note,
        }
        for row in rows
    ]


@router.get("/{workflow_id}")
def get_workflow(
    workflow_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    repository = WorkflowRepository(session)
    workflow = _owned_workflow_or_404(repository, workflow_id, current_user)
    history = repository.get_status_history(workflow_id)
    payload = _workflow_to_dict(workflow)
    payload["status_history"] = [_history_entry_to_dict(entry) for entry in history]
    return payload


@router.post("/{workflow_id}/transition")
def transition_workflow(
    workflow_id: uuid.UUID,
    action: str = Form(...),
    note: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    repository = WorkflowRepository(session)
    workflow = _owned_workflow_or_404(repository, workflow_id, current_user)

    handler = _TRANSITION_ACTIONS.get(action)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown action {action!r}. Allowed: {sorted(_TRANSITION_ACTIONS)}")

    service = WorkflowService(session, repository=repository)
    try:
        updated = handler(service, workflow, note)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _workflow_to_dict(updated)


def _owned_workflow_or_404(
    repository: WorkflowRepository, workflow_id: uuid.UUID, user: User
) -> ApplicationWorkflowRecord:
    workflow = repository.get(workflow_id)
    if workflow is None or workflow.user_id != user.id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


def _workflow_to_dict(workflow: ApplicationWorkflowRecord) -> dict:
    return {
        "id": str(workflow.id),
        "job_id": str(workflow.job_id),
        "job_title": workflow.job.title if workflow.job else None,
        "employer": workflow.job.employer.name if workflow.job and workflow.job.employer else None,
        "status": workflow.status,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }


def _history_entry_to_dict(entry: WorkflowStatusHistoryRecord) -> dict:
    return {
        "from_status": entry.from_status,
        "to_status": entry.to_status,
        "note": entry.note,
        "created_at": entry.created_at.isoformat(),
    }
