"""
Plain HTML form-POST + redirect actions for organizing/tracking a job
(save/favourite/hide/archive, Kanban stage moves, notes/rating/priority/
deadline/interview date, tags, checklist, reminders) — the same "full page
reload, no HTMX partial swap" pattern already used by
`routes/documents.py`'s regenerate button, `routes/scheduler.py`'s "Run
now" button, and `routes/notifications.py`'s mark-read actions.
`api/job_organization_api.py` is the separate, JSON equivalent for
programmatic callers.

Every mutation delegates to `JobOrganizationService`/`ReminderService` —
this file adds no business rules, only maps form fields onto the existing
named service methods.

`_safe_redirect_target()` mirrors `routes/auth.py`'s helper of the same
name/shape (guard against `?next=` pointing off-site) — small enough
(3 lines) that duplicating it here was judged simpler than importing a
leading-underscore "private" name across modules.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from job_automation.database.models.user import User
from job_automation.job_organization.job_organization_models import (
    InvalidStageTransitionError,
    JobPriority,
    PipelineStage,
    ReminderType,
)
from job_automation.job_organization.job_organization_service import JobOrganizationService
from job_automation.job_organization.reminder_service import ReminderService
from job_automation.web.app import get_current_user, get_db_session

router = APIRouter()

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


def _safe_redirect_target(next_path: str | None, *, default: str) -> str:
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return default


def _parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date {value!r}") from exc


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime {value!r}") from exc


@router.post("/jobs/{job_id}/flag")
def update_job_flag(
    job_id: uuid.UUID,
    action: str = Form(...),
    next: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    handler = _FLAG_ACTIONS.get(action)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown action {action!r}. Allowed: {sorted(_FLAG_ACTIONS)}")
    handler(JobOrganizationService(session), current_user.id, job_id)
    return RedirectResponse(url=_safe_redirect_target(next, default=f"/jobs/{job_id}"), status_code=303)


@router.post("/jobs/{job_id}/stage")
def update_job_stage(
    job_id: uuid.UUID,
    target_stage: str = Form(...),
    next: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        stage = PipelineStage(target_stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline stage {target_stage!r}") from exc

    try:
        JobOrganizationService(session).update_stage(user_id=current_user.id, job_id=job_id, target_stage=stage)
    except InvalidStageTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_redirect_target(next, default=f"/jobs/{job_id}"), status_code=303)


@router.post("/jobs/{job_id}/details")
def update_job_details(
    job_id: uuid.UUID,
    notes: str | None = Form(None),
    personal_rating: str | None = Form(None),
    priority: str | None = Form(None),
    deadline: str | None = Form(None),
    interview_date: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    rating = int(personal_rating) if personal_rating else None
    try:
        JobOrganizationService(session).update_details(
            user_id=current_user.id,
            job_id=job_id,
            notes=notes or None,
            personal_rating=rating,
            priority=JobPriority(priority) if priority else None,
            deadline=_parse_optional_date(deadline),
            interview_date=_parse_optional_datetime(interview_date),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/tags")
def update_job_tags(
    job_id: uuid.UUID,
    tags: str = Form(""),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    JobOrganizationService(session).set_tags(
        user_id=current_user.id, job_id=job_id, tags=tags.split(",")
    )
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/checklist")
def add_job_checklist_item(
    job_id: uuid.UUID,
    label: str = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    JobOrganizationService(session).add_checklist_item(user_id=current_user.id, job_id=job_id, label=label)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/checklist/{index}/toggle")
def toggle_job_checklist_item(
    job_id: uuid.UUID,
    index: int,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        JobOrganizationService(session).toggle_checklist_item(user_id=current_user.id, job_id=job_id, index=index)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/checklist/{index}/remove")
def remove_job_checklist_item(
    job_id: uuid.UUID,
    index: int,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        JobOrganizationService(session).remove_checklist_item(user_id=current_user.id, job_id=job_id, index=index)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/reminders")
def create_job_reminder(
    job_id: uuid.UUID,
    reminder_type: str = Form(...),
    remind_at: str = Form(...),
    message: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        kind = ReminderType(reminder_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown reminder type {reminder_type!r}") from exc
    when = _parse_optional_datetime(remind_at)
    if when is None:
        raise HTTPException(status_code=400, detail="remind_at is required")

    ReminderService(session).create_reminder(
        user_id=current_user.id, job_id=job_id, reminder_type=kind, remind_at=when, message=message or None
    )
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/reminders/{reminder_id}/delete")
def delete_job_reminder(
    reminder_id: uuid.UUID,
    job_id: uuid.UUID = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        ReminderService(session).delete_reminder(reminder_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
