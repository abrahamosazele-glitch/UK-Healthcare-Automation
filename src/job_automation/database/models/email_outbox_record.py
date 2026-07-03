"""
A queued notification email. `EmailNotificationProvider.send()` inserts a
row here — a plain, synchronous DB write — instead of ever calling SMTP
itself; the `send_pending_emails` scheduled task is what actually talks to
the mail server, on its own schedule. This is the entire mechanism behind
"email sending is asynchronous, so imports stay fast": enqueueing is as
cheap as any other insert in this request, and the slow part (an SMTP
round trip, subject to network latency and provider rate limits) never
happens inline with a job import, an AI match, or any other request/task
that triggers a notification.

Also backs the notification history page — every email this app has ever
tried to send, queued/sent/failed, in one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.notification import Notification
    from job_automation.database.models.user import User


class EmailOutboxRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "email_outbox"

    #: Nullable — a user account can be deleted while its sent-mail history
    #: is still worth keeping (same reasoning as `GeneratedDocumentRecord
    #: .user_id`'s `ondelete="SET NULL"`), and a genuinely orphaned row is
    #: still visible on the history page rather than silently vanishing.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    #: Same reasoning — the `Notification` this email was generated from,
    #: kept for traceability, but never required for the email itself to
    #: remain valid/sendable.
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL"), index=True
    )
    #: The resolved recipient at enqueue time (`NotificationPreferences
    #: .preferred_email` or `User.email`) — stored directly rather than
    #: re-resolved at send time, so a later preference change can't alter
    #: where an already-queued email goes.
    to_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    #: A `notifications.notification_models.NotificationType` value — plain
    #: string, not `sa_enum(...)`, same reasoning as `Notification.type`.
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    #: "pending" (queued, not yet attempted or a previous attempt failed
    #: and will retry), "sent", "failed" (exhausted `max_attempts`) — plain
    #: string for the same reason every other status column in this schema
    #: is (`ApplicationWorkflowRecord.status`, `SchedulerTaskRunRecord
    #: .status`, ...).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime())

    user: Mapped["User | None"] = relationship()
    notification: Mapped["Notification | None"] = relationship()

    def __repr__(self) -> str:
        return f"<EmailOutboxRecord to={self.to_email!r} type={self.notification_type} status={self.status}>"
