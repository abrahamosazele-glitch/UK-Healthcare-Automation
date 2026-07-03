"""
A reminder attached to a tracked job (`SavedJob`) — application deadline,
interview, documents needed, or reference request. The background
scheduler's `send_due_reminders` task finds rows where `remind_at` has
passed and `is_sent` is still `False`, publishes a `REMINDER_DUE` event
(never calls `NotificationService` directly — see
`notifications.events`'s module docstring), and marks them sent.

Tied to `saved_job_id`, not directly to `(user_id, job_id)` — a reminder
only makes sense for a job the candidate is already tracking, and this
avoids duplicating `user_id`/`job_id` columns that `SavedJob` already has.

`reminder_type` is a plain `String` column, not `sa_enum(...)` — same
reasoning as `SavedJob.pipeline_stage`: the canonical `ReminderType` enum
lives in `job_organization.job_organization_models`, not in
`database/models/`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.saved_job import SavedJob


class JobReminder(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "job_reminders"

    saved_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reminder_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    message: Mapped[str | None] = mapped_column(Text)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    saved_job: Mapped["SavedJob"] = relationship(back_populates="reminders")

    def __repr__(self) -> str:
        return f"<JobReminder {self.reminder_type} saved_job={self.saved_job_id} sent={self.is_sent}>"
