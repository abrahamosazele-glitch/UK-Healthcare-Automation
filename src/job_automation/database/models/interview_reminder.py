"""
A reminder attached to an `InterviewRecord` — 7 days / 3 days / 1 day / 2
hours / 30 minutes before, per `InterviewRecord.reminder_offsets`. The
background scheduler's `send_due_interview_reminders` task finds rows
where `remind_at` has passed and `is_sent` is still `False`, publishes an
`INTERVIEW_REMINDER_DUE` event (never calls `NotificationService`
directly — see `notifications.events`'s module docstring), and marks them
sent. Generates in-app notification records only — no real email/SMS is
ever sent, per this milestone's explicit constraints.

Tied to `interview_id`, not duplicating `user_id` — mirrors
`JobReminder`'s relationship to `SavedJob`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.interview_record import InterviewRecord


class InterviewReminder(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "interview_reminders"

    interview_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: `ReminderOffset.value` — canonical enum lives in
    #: `interviews.interview_models`.
    offset: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    interview: Mapped["InterviewRecord"] = relationship(back_populates="reminders")

    def __repr__(self) -> str:
        return f"<InterviewReminder {self.offset} interview={self.interview_id} sent={self.is_sent}>"
