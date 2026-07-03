"""
One in-app notification. `user_id` is nullable — deliberately, unlike
almost every other user-owned table in this schema — because some
notifications are system-wide (e.g. "a background scheduler task failed")
rather than about one candidate's data. `NotificationRepository`/
`NotificationService` treat a `NULL` `user_id` as "visible to every user,"
the same way a real notice-board notification isn't addressed to anyone
specifically. See docs/NOTIFICATIONS.md.

`type`/`severity` are plain `String` columns, not `sa_enum(...)` — same
reasoning as `ApplicationWorkflowRecord.status`/`GeneratedDocumentRecord
.document_type`: every file in `database/models/` only imports from
`database.*`, and the canonical `NotificationType`/`NotificationSeverity`
enums live in the domain layer (`notifications.notification_models`), not
here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.user import User


class Notification(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime())
    # Which subsystem raised this — "scheduler", "ai_matching", "documents",
    # "workflow", "auth" — free text, not a FK/enum; purely informational
    # (e.g. for filtering the notifications page), not used in any query
    # that needs referential integrity.
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # `metadata` is reserved on every SQLAlchemy declarative model (it
    # shadows `Base.metadata`, the table registry) — `metadata_` is the
    # Python attribute name; the actual column is still named `metadata`,
    # matching this milestone's literal field list.
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    user: Mapped["User | None"] = relationship(back_populates="notifications")

    def __repr__(self) -> str:
        return f"<Notification {self.type} user={self.user_id} severity={self.severity}>"
