"""
JSON API for job organization (save/favourite/hide/archive, Kanban stage
moves, notes/rating/priority/deadline/interview date, tags, checklist) and
reminders — the programmatic equivalent of `routes/job_organization.py`.
Every mutation goes through `JobOrganizationService`/`ReminderService`,
which already own validated stage transitions, rating-range checks, and
event publishing; this file adds no new business rules, only maps JSON
request bodies onto the existing named service methods (same relationship
`api/workflow_api.py` has to `WorkflowService`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_automation.database.models.saved_job import SavedJob
from job_automation.database.models.user import User
from job_automation.job_organization.job_organization_models import (
    InvalidStageTransitionError,
    JobPriority,
    PipelineStage,
    ReminderType,
)
from job_automation.job_organization.job_organization_service import JobOrganizationService
from job_automation.job_organization.reminder_service import ReminderService
from job_automation.job_organization.saved_job_repository import SavedJobRepository
from job_automation.web.app import get_current_api_user, get_db_session

router = APIRouter(prefix="/api/job-organization", tags=["job-organization"])

_FLAG_ACTIONS = {
    "save": lambda service, user_id, job_id: service.save(user_id=user_id, job_id=job_id),
    "unsave": lambda service, user_id, job_id: service.unsave(user_id=user_id, job_id=job_id),
    "favourite": lambda service, user_id, job_id: service.favourite(user_id=user_id, job_id=job_id),
    "unfavourite": lambda service, user_id, job_id: service.unfavourite(user_id=user_id, job_id=job_id),
    "hide": lambda service, user_id, job_id: service.hide(user_id=user_id, job_id=job_id),
    "unhide": lambda service, user_id, job_id: service.unhide(user_id=user_id, job_id=job_id),
    "archive": lambda service, user_id, job_id: service.archive(user_id=user_id, job_id=job_id),
    "restore": lambda service, user_id, job_id: service.restore(user_id=user_id, job_id=job_id),
}


class FlagRequest(BaseModel):
    action: str


class StageRequest(BaseModel):
    target_stage: str


class DetailsRequest(BaseModel):
    notes: str | None = None
    personal_rating: int | None = None
    priority: str | None = None
    deadline: date | None = None
    interview_date: datetime | None = None


class TagsRequest(BaseModel):
    tags: list[str]


class ChecklistItemRequest(BaseModel):
    label: str


class ReminderRequest(BaseModel):
    reminder_type: str
    remind_at: datetime
    message: str | None = None


@router.get("")
def list_saved_jobs(
    include_hidden: bool = False,
    archived_only: bool = False,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    saved_jobs = SavedJobRepository(session).list_for_user(
        current_user.id, include_hidden=include_hidden, archived_only=archived_only
    )
    return [_saved_job_to_dict(saved_job) for saved_job in saved_jobs]


@router.get("/{job_id}")
def get_saved_job(
    job_id: uuid.UUID, session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> dict:
    saved_job = SavedJobRepository(session).find(user_id=current_user.id, job_id=job_id)
    if saved_job is None:
        raise HTTPException(status_code=404, detail="No tracking state for this job yet")
    return _saved_job_to_dict(saved_job)


@router.post("/{job_id}/flag")
def update_flag(
    job_id: uuid.UUID,
    body: FlagRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    handler = _FLAG_ACTIONS.get(body.action)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown action {body.action!r}. Allowed: {sorted(_FLAG_ACTIONS)}")
    saved_job = handler(JobOrganizationService(session), current_user.id, job_id)
    return _saved_job_to_dict(saved_job)


@router.post("/{job_id}/stage")
def update_stage(
    job_id: uuid.UUID,
    body: StageRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        stage = PipelineStage(body.target_stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline stage {body.target_stage!r}") from exc
    try:
        saved_job = JobOrganizationService(session).update_stage(
            user_id=current_user.id, job_id=job_id, target_stage=stage
        )
    except InvalidStageTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _saved_job_to_dict(saved_job)


@router.post("/{job_id}/details")
def update_details(
    job_id: uuid.UUID,
    body: DetailsRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        saved_job = JobOrganizationService(session).update_details(
            user_id=current_user.id,
            job_id=job_id,
            notes=body.notes,
            personal_rating=body.personal_rating,
            priority=JobPriority(body.priority) if body.priority else None,
            deadline=body.deadline,
            interview_date=body.interview_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _saved_job_to_dict(saved_job)


@router.post("/{job_id}/tags")
def update_tags(
    job_id: uuid.UUID,
    body: TagsRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    saved_job = JobOrganizationService(session).set_tags(user_id=current_user.id, job_id=job_id, tags=body.tags)
    return _saved_job_to_dict(saved_job)


@router.post("/{job_id}/checklist")
def add_checklist_item(
    job_id: uuid.UUID,
    body: ChecklistItemRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    saved_job = JobOrganizationService(session).add_checklist_item(
        user_id=current_user.id, job_id=job_id, label=body.label
    )
    return _saved_job_to_dict(saved_job)


@router.post("/{job_id}/checklist/{index}/toggle")
def toggle_checklist_item(
    job_id: uuid.UUID,
    index: int,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        saved_job = JobOrganizationService(session).toggle_checklist_item(
            user_id=current_user.id, job_id=job_id, index=index
        )
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _saved_job_to_dict(saved_job)


@router.delete("/{job_id}/checklist/{index}")
def remove_checklist_item(
    job_id: uuid.UUID,
    index: int,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        saved_job = JobOrganizationService(session).remove_checklist_item(
            user_id=current_user.id, job_id=job_id, index=index
        )
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _saved_job_to_dict(saved_job)


@router.get("/reminders/upcoming")
def list_upcoming_reminders(
    limit: int = 10,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    reminders = ReminderService(session).list_upcoming_for_user(current_user.id, limit=limit)
    return [_reminder_to_dict(reminder) for reminder in reminders]


@router.post("/{job_id}/reminders")
def create_reminder(
    job_id: uuid.UUID,
    body: ReminderRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        kind = ReminderType(body.reminder_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown reminder type {body.reminder_type!r}") from exc
    reminder = ReminderService(session).create_reminder(
        user_id=current_user.id, job_id=job_id, reminder_type=kind, remind_at=body.remind_at, message=body.message
    )
    return _reminder_to_dict(reminder)


@router.delete("/reminders/{reminder_id}")
def delete_reminder(
    reminder_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        ReminderService(session).delete_reminder(reminder_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


def _saved_job_to_dict(saved_job: SavedJob) -> dict:
    return {
        "id": str(saved_job.id),
        "job_id": str(saved_job.job_id),
        "job_title": saved_job.job.title if saved_job.job else None,
        "employer": saved_job.job.employer.name if saved_job.job and saved_job.job.employer else None,
        "is_saved": saved_job.is_saved,
        "is_favourite": saved_job.is_favourite,
        "is_hidden": saved_job.is_hidden,
        "is_archived": saved_job.is_archived,
        "pipeline_stage": saved_job.pipeline_stage,
        "notes": saved_job.notes,
        "personal_rating": saved_job.personal_rating,
        "priority": saved_job.priority,
        "deadline": saved_job.deadline.isoformat() if saved_job.deadline else None,
        "interview_date": saved_job.interview_date.isoformat() if saved_job.interview_date else None,
        "tags": saved_job.tags or [],
        "checklist": saved_job.checklist or [],
    }


def _reminder_to_dict(reminder) -> dict:
    return {
        "id": str(reminder.id),
        "saved_job_id": str(reminder.saved_job_id),
        "job_id": str(reminder.saved_job.job_id) if reminder.saved_job else None,
        "reminder_type": reminder.reminder_type,
        "remind_at": reminder.remind_at.isoformat(),
        "message": reminder.message,
        "is_sent": reminder.is_sent,
    }
