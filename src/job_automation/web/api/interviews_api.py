"""
JSON API for interview & calendar management — the programmatic
equivalent of `routes/interviews.py`. Every mutation goes through
`InterviewService`, which already owns validated status transitions,
checklist seeding, reminder (re)generation, and event publishing; this
file adds no new business rules, only maps JSON request bodies onto the
existing named service methods (same relationship
`api/job_organization_api.py` has to `JobOrganizationService`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_automation.database.models.interview_record import InterviewRecord
from job_automation.database.models.user import User
from job_automation.interviews.interview_models import (
    InterviewStatus,
    InterviewType,
    InvalidInterviewStatusTransitionError,
    NoteCategory,
    ReminderOffset,
)
from job_automation.interviews.interview_service import InterviewService
from job_automation.web.app import get_current_api_user, get_db_session

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


class ScheduleInterviewRequest(BaseModel):
    employer_id: uuid.UUID
    interview_type: str
    scheduled_at: datetime
    job_id: uuid.UUID | None = None
    application_workflow_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    interview_stage: str | None = None
    duration_minutes: int | None = None
    timezone: str | None = None
    location: str | None = None
    meeting_link: str | None = None
    interviewer_names: list[str] | None = None
    reminder_offsets: list[str] | None = None
    notes: str | None = None


class RescheduleRequest(BaseModel):
    new_scheduled_at: datetime


class StatusUpdateRequest(BaseModel):
    target_status: str
    outcome: str | None = None


class WorkflowSyncRequest(BaseModel):
    action: str


class ChecklistItemRequest(BaseModel):
    label: str


class NoteRequest(BaseModel):
    category: str
    body: str


@router.get("")
def list_interviews(
    status: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    parsed_status = InterviewStatus(status) if status else None
    interviews = InterviewService(session).list_for_user(current_user.id, status=parsed_status)
    return [_interview_to_dict(i) for i in interviews]


@router.get("/{interview_id}")
def get_interview(
    interview_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        interview = InterviewService(session).get(interview_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")
    return _interview_to_dict(interview)


@router.post("")
def schedule_interview(
    body: ScheduleInterviewRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        kind = InterviewType(body.interview_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown interview type {body.interview_type!r}") from exc
    try:
        offsets = [ReminderOffset(value) for value in (body.reminder_offsets or [])]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    interview = InterviewService(session).schedule(
        user_id=current_user.id,
        employer_id=body.employer_id,
        interview_type=kind,
        scheduled_at=body.scheduled_at,
        job_id=body.job_id,
        application_workflow_id=body.application_workflow_id,
        contact_id=body.contact_id,
        interview_stage=body.interview_stage,
        duration_minutes=body.duration_minutes,
        timezone=body.timezone,
        location=body.location,
        meeting_link=body.meeting_link,
        interviewer_names=body.interviewer_names,
        reminder_offsets=offsets or None,
        notes=body.notes,
    )
    return _interview_to_dict(interview)


@router.post("/{interview_id}/status")
def update_status(
    interview_id: uuid.UUID,
    body: StatusUpdateRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        status_value = InterviewStatus(body.target_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown status {body.target_status!r}") from exc
    try:
        interview = InterviewService(session).update_status(
            interview_id, user_id=current_user.id, target_status=status_value, outcome=body.outcome
        )
    except InvalidInterviewStatusTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _interview_to_dict(interview)


@router.post("/{interview_id}/reschedule")
def reschedule_interview(
    interview_id: uuid.UUID,
    body: RescheduleRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        interview = InterviewService(session).reschedule(
            interview_id, user_id=current_user.id, new_scheduled_at=body.new_scheduled_at
        )
    except InvalidInterviewStatusTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _interview_to_dict(interview)


@router.post("/{interview_id}/workflow-sync")
def sync_workflow(
    interview_id: uuid.UUID,
    body: WorkflowSyncRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        InterviewService(session).sync_workflow_status(interview_id, user_id=current_user.id, action=body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"synced": True}


@router.get("/{interview_id}/checklist")
def list_checklist(
    interview_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    try:
        items = InterviewService(session).list_checklist(interview_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")
    return [{"id": str(i.id), "label": i.label, "is_complete": i.is_complete} for i in items]


@router.post("/{interview_id}/checklist")
def add_checklist_item(
    interview_id: uuid.UUID,
    body: ChecklistItemRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        item = InterviewService(session).add_checklist_item(interview_id, user_id=current_user.id, label=body.label)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")
    return {"id": str(item.id), "label": item.label, "is_complete": item.is_complete}


@router.post("/{interview_id}/checklist/{item_id}/toggle")
def toggle_checklist_item(
    interview_id: uuid.UUID,
    item_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        item = InterviewService(session).toggle_checklist_item(item_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": str(item.id), "label": item.label, "is_complete": item.is_complete}


@router.delete("/{interview_id}/checklist/{item_id}")
def remove_checklist_item(
    interview_id: uuid.UUID,
    item_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        InterviewService(session).remove_checklist_item(item_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/{interview_id}/notes")
def list_notes(
    interview_id: uuid.UUID,
    category: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    kind = NoteCategory(category) if category else None
    try:
        notes = InterviewService(session).list_notes(interview_id, user_id=current_user.id, category=kind)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")
    return [{"id": str(n.id), "category": n.category, "body": n.body, "created_at": n.created_at.isoformat()} for n in notes]


@router.post("/{interview_id}/notes")
def add_note(
    interview_id: uuid.UUID,
    body: NoteRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        kind = NoteCategory(body.category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown note category {body.category!r}") from exc
    try:
        note = InterviewService(session).add_note(interview_id, user_id=current_user.id, category=kind, body=body.body)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")
    return {"id": str(note.id), "category": note.category, "body": note.body}


@router.delete("/{interview_id}/notes/{note_id}")
def remove_note(
    interview_id: uuid.UUID,
    note_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        InterviewService(session).remove_note(note_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/{interview_id}/reminders")
def list_reminders(
    interview_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    try:
        reminders = InterviewService(session).list_reminders(interview_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")
    return [
        {"id": str(r.id), "offset": r.offset, "remind_at": r.remind_at.isoformat(), "is_sent": r.is_sent}
        for r in reminders
    ]


def _interview_to_dict(interview: InterviewRecord) -> dict:
    return {
        "id": str(interview.id),
        "user_id": str(interview.user_id),
        "employer_id": str(interview.employer_id),
        "job_id": str(interview.job_id) if interview.job_id else None,
        "application_workflow_id": str(interview.application_workflow_id)
        if interview.application_workflow_id
        else None,
        "contact_id": str(interview.contact_id) if interview.contact_id else None,
        "interview_type": interview.interview_type,
        "interview_stage": interview.interview_stage,
        "status": interview.status,
        "scheduled_at": interview.scheduled_at.isoformat(),
        "duration_minutes": interview.duration_minutes,
        "timezone": interview.timezone,
        "location": interview.location,
        "meeting_link": interview.meeting_link,
        "interviewer_names": interview.interviewer_names or [],
        "reminder_offsets": interview.reminder_offsets or [],
        "outcome": interview.outcome,
        "notes": interview.notes,
    }
