"""
The main entry point for the notification subsystem: create a
notification (persist + deliver through every configured provider),
mark one or all of a user's notifications read, and query unread counts —
the same orchestrator role `WorkflowService`/`DocumentService` play for
their own subsystems.

Callers should reach this class either through `notification_listeners.py`
(the event-bus-driven path every existing-module integration this
milestone adds uses) or directly from a dashboard route (marking read,
listing) — never by having a business-logic module import this service
and call `.create()` itself. That's what the event bus is for: see
`events.py`'s module docstring.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from job_automation.database.models.notification import Notification
from job_automation.notifications.notification_models import NotificationSeverity, NotificationType
from job_automation.notifications.notification_providers import (
    EmailNotificationProvider,
    InAppNotificationProvider,
    NotificationProvider,
)
from job_automation.notifications.notification_repository import NotificationRepository
from job_automation.utils.logger import logger


class NotificationService:
    def __init__(
        self,
        session: Session,
        *,
        repository: NotificationRepository | None = None,
        providers: list[NotificationProvider] | None = None,
    ) -> None:
        self._repository = repository or NotificationRepository(session)
        # In-app always; email decides per-user, per-type whether it
        # actually has anything to enqueue (see EmailNotificationProvider)
        # — never SMS/Push, which remain unimplemented placeholders. See
        # notification_providers.py's module docstring.
        self._providers = (
            providers
            if providers is not None
            else [InAppNotificationProvider(), EmailNotificationProvider(session)]
        )

    def create(
        self,
        *,
        user_id: uuid.UUID | None,
        type: NotificationType,
        title: str,
        message: str,
        severity: NotificationSeverity = NotificationSeverity.INFO,
        source: str,
        metadata: dict | None = None,
    ) -> Notification:
        notification = self._repository.create(
            user_id=user_id, type=type, title=title, message=message, severity=severity, source=source, metadata=metadata
        )
        for provider in self._providers:
            provider.send(notification)
        logger.debug("Notification created: {} ({})", notification.title, notification.type)
        return notification

    def mark_read(self, notification_id: uuid.UUID, *, user_id: uuid.UUID) -> Notification:
        notification = self._repository.get(notification_id)
        if notification is None or (notification.user_id is not None and notification.user_id != user_id):
            raise ValueError(f"No notification {notification_id} visible to user {user_id}")
        return self._repository.mark_read(notification)

    def mark_all_read(self, user_id: uuid.UUID) -> int:
        return self._repository.mark_all_read(user_id)

    def unread_count(self, user_id: uuid.UUID) -> int:
        return self._repository.count_unread(user_id)

    def list_notifications(
        self, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        return self._repository.list_for_user(user_id, unread_only=unread_only, limit=limit)
