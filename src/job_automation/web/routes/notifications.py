"""
Notifications page, the navbar bell badge fragment, mark-read/
mark-all-read actions, the notification settings page (Real Email
Notification Delivery milestone), and the email history page — all plain
HTML, all requiring a session like every other dashboard page.

"Mark read" and "mark all read" are plain HTML form POSTs that redirect
back to `/notifications` (full page reload), the same pattern already used
by `routes/documents.py`'s regenerate button and `routes/scheduler.py`'s
"Run now" button — not an HTMX partial swap. `api/notifications_api.py`'s
JSON `POST /api/notifications/{id}/read` is the separate, programmatic
equivalent for external callers/tests; this file's routes call
`NotificationService` directly rather than each other, so there's exactly
one mutation path per action, not two layered on top of each other.

The navbar bell polls `GET /notifications/bell` (`hx-trigger="load, every
30s"`, see `templates/components/navbar.html`) for its unread-count badge
— a dedicated HTML-fragment route, not `/api/notifications/unread-count`
(JSON), since HTMX swaps HTML into the DOM, not a JSON body.

`/notifications/settings` and `/notifications/history` live here (not in
`routes/settings.py`) since they're both squarely about the notification
subsystem specifically, not general application preferences — `settings
.html` links to the former for discoverability rather than duplicating
the form.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.email_outbox_record import EmailOutboxRecord
from job_automation.database.models.user import User
from job_automation.notifications.notification_preferences_service import (
    NotificationPreferencesService,
)
from job_automation.notifications.notification_service import NotificationService
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    notifications = NotificationService(session).list_notifications(current_user.id, limit=100)
    return templates.TemplateResponse(
        request,
        "notifications.html",
        {"active_page": "notifications", "current_user": current_user, "notifications": notifications},
    )


@router.get("/notifications/bell", response_class=HTMLResponse)
def notifications_bell(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_user)
) -> HTMLResponse:
    count = NotificationService(session).unread_count(current_user.id)
    if count == 0:
        return HTMLResponse("")
    display = "99+" if count > 99 else str(count)
    return HTMLResponse(
        f'<span class="badge rounded-pill bg-danger notification-bell-badge">{display}</span>'
    )


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        NotificationService(session).mark_read(notification_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Notification not found")
    return RedirectResponse(url="/notifications", status_code=303)


@router.post("/notifications/mark-all-read")
def mark_all_notifications_read(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_user)
) -> RedirectResponse:
    NotificationService(session).mark_all_read(current_user.id)
    return RedirectResponse(url="/notifications", status_code=303)


@router.get("/notifications/settings", response_class=HTMLResponse)
def notification_settings_page(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    prefs = NotificationPreferencesService(session).get_or_create(current_user.id)
    return templates.TemplateResponse(
        request,
        "notification_settings.html",
        {"active_page": "notifications", "current_user": current_user, "prefs": prefs},
    )


@router.post("/notifications/settings")
def update_notification_settings(
    email_new_jobs_imported: bool = Form(False),
    email_high_match: bool = Form(False),
    email_interview_reminders: bool = Form(False),
    email_closing_soon: bool = Form(False),
    email_daily_digest: bool = Form(False),
    email_weekly_summary: bool = Form(False),
    email_scheduler_status: bool = Form(False),
    email_document_generated: bool = Form(False),
    quiet_hours_start: str | None = Form(None),
    quiet_hours_end: str | None = Form(None),
    daily_digest_hour: int = Form(8),
    ai_match_threshold: float = Form(80.0),
    preferred_email: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    NotificationPreferencesService(session).update(
        current_user.id,
        email_new_jobs_imported=email_new_jobs_imported,
        email_high_match=email_high_match,
        email_interview_reminders=email_interview_reminders,
        email_closing_soon=email_closing_soon,
        email_daily_digest=email_daily_digest,
        email_weekly_summary=email_weekly_summary,
        email_scheduler_status=email_scheduler_status,
        email_document_generated=email_document_generated,
        quiet_hours_start=int(quiet_hours_start) if quiet_hours_start not in (None, "") else None,
        quiet_hours_end=int(quiet_hours_end) if quiet_hours_end not in (None, "") else None,
        daily_digest_hour=daily_digest_hour,
        ai_match_threshold=ai_match_threshold,
        preferred_email=preferred_email,
    )
    return RedirectResponse(url="/notifications/settings", status_code=303)


@router.get("/notifications/history", response_class=HTMLResponse)
def notification_history_page(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    emails = list(
        session.scalars(
            select(EmailOutboxRecord)
            .where(EmailOutboxRecord.user_id == current_user.id)
            .order_by(EmailOutboxRecord.created_at.desc())
            .limit(200)
        )
    )
    return templates.TemplateResponse(
        request,
        "notification_history.html",
        {"active_page": "notifications", "current_user": current_user, "emails": emails},
    )
