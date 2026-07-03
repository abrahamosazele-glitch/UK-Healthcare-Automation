"""
Persists `JobReminder` rows. Pure data access, following the same
repository pattern as every other repository in this project.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.job_reminder import JobReminder
from job_automation.database.models.saved_job import SavedJob
from job_automation.job_organization.job_organization_models import ReminderType


class ReminderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, *, saved_job_id: uuid.UUID, reminder_type: ReminderType, remind_at: datetime, message: str | None = None
    ) -> JobReminder:
        reminder = JobReminder(
            saved_job_id=saved_job_id, reminder_type=reminder_type.value, remind_at=remind_at, message=message
        )
        self._session.add(reminder)
        self._session.flush()
        return reminder

    def get(self, reminder_id: uuid.UUID) -> JobReminder | None:
        return self._session.get(JobReminder, reminder_id)

    def list_for_saved_job(self, saved_job_id: uuid.UUID) -> list[JobReminder]:
        return list(
            self._session.scalars(
                select(JobReminder).where(JobReminder.saved_job_id == saved_job_id).order_by(JobReminder.remind_at)
            )
        )

    def list_upcoming_for_user(self, user_id: uuid.UUID, *, limit: int = 10) -> list[JobReminder]:
        stmt = (
            select(JobReminder)
            .join(SavedJob, SavedJob.id == JobReminder.saved_job_id)
            .where(SavedJob.user_id == user_id, JobReminder.is_sent.is_(False))
            .order_by(JobReminder.remind_at)
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_due(self, *, as_of: datetime) -> list[JobReminder]:
        stmt = select(JobReminder).where(JobReminder.is_sent.is_(False), JobReminder.remind_at <= as_of)
        return list(self._session.scalars(stmt))

    def mark_sent(self, reminder: JobReminder) -> JobReminder:
        reminder.is_sent = True
        self._session.flush()
        return reminder

    def delete(self, reminder: JobReminder) -> None:
        self._session.delete(reminder)
        self._session.flush()
