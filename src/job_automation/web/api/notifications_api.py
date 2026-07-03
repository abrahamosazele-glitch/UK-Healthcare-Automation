"""
JSON API for notifications: list, unread count, mark one/all read — the
programmatic counterpart to `routes/notifications.py`'s HTML page. Every
notification this milestone creates already exists by the time a caller
hits these routes (via the event bus, see `notifications.events`'s module
docstring) — nothing here ever creates a notification itself.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from job_automation.database.models.notification import Notification
from job_automation.database.models.user import User
from job_automation.notifications.notification_service import NotificationService
from job_automation.web.app import get_current_api_user, get_db_session

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    notifications = NotificationService(session).list_notifications(
        current_user.id, unread_only=unread_only, limit=limit
    )
    return [_notification_to_dict(n) for n in notifications]


@router.get("/unread-count")
def unread_count(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> dict:
    return {"count": NotificationService(session).unread_count(current_user.id)}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        notification = NotificationService(session).mark_read(notification_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _notification_to_dict(notification)


@router.post("/mark-all-read")
def mark_all_read(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> dict:
    count = NotificationService(session).mark_all_read(current_user.id)
    return {"marked_read": count}


def _notification_to_dict(notification: Notification) -> dict:
    return {
        "id": str(notification.id),
        "user_id": str(notification.user_id) if notification.user_id else None,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "severity": notification.severity,
        "source": notification.source,
        "metadata": notification.metadata_,
        "created_at": notification.created_at.isoformat(),
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
    }
