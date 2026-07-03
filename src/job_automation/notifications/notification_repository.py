"""
Persists `Notification` rows. Pure data access — no event handling, no
provider dispatch (that's `notification_service.py`'s job), following the
same repository pattern as every other repository in this project.

Every read method treats a `NULL` `user_id` row as visible to *every*
user (a system-wide notice), unioned with that user's own rows — see
`database.models.notification.Notification`'s docstring for why
`user_id` is nullable at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from job_automation.database.models.notification import Notification
from job_automation.notifications.notification_models import NotificationSeverity, NotificationType
from job_automation.utils.helpers import utc_now


class NotificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: uuid.UUID | None,
        type: NotificationType,
        title: str,
        message: str,
        severity: NotificationSeverity,
        source: str,
        metadata: dict | None = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            type=type.value,
            title=title,
            message=message,
            severity=severity.value,
            source=source,
            metadata_=metadata,
        )
        self._session.add(notification)
        self._session.flush()
        return notification

    def get(self, notification_id: uuid.UUID) -> Notification | None:
        return self._session.get(Notification, notification_id)

    def _visible_to_user_clause(self, user_id: uuid.UUID) -> Any:
        return or_(Notification.user_id == user_id, Notification.user_id.is_(None))

    def list_for_user(
        self, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        stmt = (
            select(Notification)
            .where(self._visible_to_user_clause(user_id))
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        return list(self._session.scalars(stmt))

    def count_unread(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Notification).where(
            and_(self._visible_to_user_clause(user_id), Notification.read_at.is_(None))
        )
        return self._session.scalar(stmt) or 0

    def mark_read(self, notification: Notification) -> Notification:
        if notification.read_at is None:
            notification.read_at = utc_now()
            self._session.flush()
        return notification

    def mark_all_read(self, user_id: uuid.UUID) -> int:
        unread = self.list_for_user(user_id, unread_only=True, limit=10_000)
        now = utc_now()
        for notification in unread:
            notification.read_at = now
        self._session.flush()
        return len(unread)
