"""
A generic audit trail entry (e.g. "application_submitted", "cv_generated",
"status_changed"). `user_id` is nullable so system-level events (e.g. a
scraper run completing) can also be logged without a user attached.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.user import User


class ActivityLog(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "activity_logs"
    __table_args__ = (Index("ix_activity_logs_created_at", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    details: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User | None"] = relationship(back_populates="activity_logs")

    def __repr__(self) -> str:
        return f"<ActivityLog {self.action!r}>"
