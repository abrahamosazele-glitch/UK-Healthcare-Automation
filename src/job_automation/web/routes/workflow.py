"""
Workflow list and single-workflow timeline pages — entirely backed by
`WorkflowService`/`WorkflowRepository`. `PIPELINE_STAGES` here is display
order only (for the visual pipeline strip); the actual allowed-transition
rules remain solely `workflows.application_workflow.ApplicationWorkflow`'s
responsibility — this list is not re-validated against, only rendered.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from job_automation.database.models.user import User
from job_automation.web.app import get_current_user, get_db_session, templates
from job_automation.workflows.workflow_models import WorkflowStatus
from job_automation.workflows.workflow_repository import WorkflowRepository

router = APIRouter()

PIPELINE_STAGES = [
    WorkflowStatus.NEW_MATCH.value,
    WorkflowStatus.DOCUMENTS_GENERATED.value,
    WorkflowStatus.NEEDS_REVIEW.value,
    WorkflowStatus.APPROVED.value,
    WorkflowStatus.READY_TO_APPLY.value,
    WorkflowStatus.APPLIED.value,
    WorkflowStatus.INTERVIEW.value,
    WorkflowStatus.OFFER.value,
    WorkflowStatus.CLOSED.value,
]


@router.get("/workflow", response_class=HTMLResponse)
def workflow_list(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    workflows = WorkflowRepository(session).list_for_user(current_user.id)
    return templates.TemplateResponse(
        request,
        "workflow.html",
        {"active_page": "workflow", "current_user": current_user, "workflows": workflows, "workflow": None},
    )


@router.get("/workflow/{workflow_id}", response_class=HTMLResponse)
def workflow_detail(
    workflow_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    repository = WorkflowRepository(session)
    workflow = repository.get(workflow_id)
    if workflow is None or workflow.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return templates.TemplateResponse(
        request,
        "workflow.html",
        {
            "active_page": "workflow",
            "current_user": current_user,
            "workflow": workflow,
            "status_history": repository.get_status_history(workflow_id),
            "pipeline_stages": PIPELINE_STAGES,
        },
    )
