"""
Applications page. "Application" isn't a separate business object in this
system — it's an `ApplicationWorkflowRecord` that has reached at least
`READY_TO_APPLY` (see docs/DASHBOARD.md for why there's no
"ApplicationRepository": the workflow subsystem already *is* the
application-tracking mechanism). Interview date / offer / notes are all
derived from `WorkflowStatusHistoryRecord`, not new stored fields.

`build_application_rows()` is the single place this derivation happens —
`api/workflow_api.py`'s JSON applications endpoint imports it rather than
re-deriving interview_date/has_offer/latest_note itself, so this page and
that endpoint can never disagree about what counts as an "application."

`linked_interview` (added for the Interview & Calendar Management
milestone) is the real `InterviewRecord` linked to this workflow, if any
— genuinely different from `interview_date` above (which is just "when
did the workflow's status history first say 'interview'," a proxy that
existed before real interview scheduling did). Showing both isn't
redundant: `interview_date` answers "when did this application reach the
interview stage," `linked_interview` answers "is there an actual scheduled
interview, and what's its live status."
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from job_automation.database.models.application_workflow_record import ApplicationWorkflowRecord
from job_automation.database.models.interview_record import InterviewRecord
from job_automation.database.models.user import User
from job_automation.interviews.interview_repository import InterviewRepository
from job_automation.web.app import get_current_user, get_db_session, templates
from job_automation.workflows.workflow_models import WorkflowStatus
from job_automation.workflows.workflow_repository import WorkflowRepository

router = APIRouter()

_APPLICATION_STAGES = {
    WorkflowStatus.READY_TO_APPLY.value,
    WorkflowStatus.APPLIED.value,
    WorkflowStatus.INTERVIEW.value,
    WorkflowStatus.OFFER.value,
    WorkflowStatus.CLOSED.value,
}


@dataclass
class ApplicationRow:
    workflow: ApplicationWorkflowRecord
    interview_date: datetime | None
    has_offer: bool
    latest_note: str | None
    linked_interview: InterviewRecord | None = None


def build_application_rows(
    repository: WorkflowRepository, user_id: uuid.UUID, *, interview_repository: InterviewRepository | None = None
) -> list[ApplicationRow]:
    workflows = [w for w in repository.list_for_user(user_id) if w.status in _APPLICATION_STAGES]

    rows = []
    for workflow in workflows:
        history = repository.get_status_history(workflow.id)
        interview_entry = next((entry for entry in history if entry.to_status == WorkflowStatus.INTERVIEW.value), None)
        has_offer = any(entry.to_status == WorkflowStatus.OFFER.value for entry in history)
        notes = [entry.note for entry in reversed(history) if entry.note]
        linked_interview = None
        if interview_repository is not None:
            linked = interview_repository.list_for_application_workflow(
                user_id=user_id, application_workflow_id=workflow.id
            )
            linked_interview = linked[-1] if linked else None
        rows.append(
            ApplicationRow(
                workflow=workflow,
                interview_date=interview_entry.created_at if interview_entry else None,
                has_offer=has_offer,
                latest_note=notes[0] if notes else None,
                linked_interview=linked_interview,
            )
        )
    return rows


@router.get("/applications", response_class=HTMLResponse)
def applications_list(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    repository = WorkflowRepository(session)
    rows = build_application_rows(repository, current_user.id, interview_repository=InterviewRepository(session))

    return templates.TemplateResponse(
        request,
        "applications.html",
        {"active_page": "applications", "current_user": current_user, "applications": rows},
    )
