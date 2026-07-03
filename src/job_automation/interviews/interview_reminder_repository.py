"""
Persists `InterviewReminder` rows. Pure data access, following the same
repository pattern as every other repository in this project (mirrors
`job_organization.reminder_repository.ReminderRepository` exactly).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.interview_reminder import InterviewReminder


class InterviewReminderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, interview_id: uuid.UUID, offset: str, remind_at: datetime) -> InterviewReminder:
        reminder = InterviewReminder(interview_id=interview_id, offset=offset, remind_at=remind_at)
        self._session.add(reminder)
        self._session.flush()
        return reminder

    def get(self, reminder_id: uuid.UUID) -> InterviewReminder | None:
        return self._session.get(InterviewReminder, reminder_id)

    def list_for_interview(self, interview_id: uuid.UUID) -> list[InterviewReminder]:
        return list(
            self._session.scalars(
                select(InterviewReminder)
                .where(InterviewReminder.interview_id == interview_id)
                .order_by(InterviewReminder.remind_at)
            )
        )

    def list_due(self, *, as_of: datetime) -> list[InterviewReminder]:
        return list(
            self._session.scalars(
                select(InterviewReminder).where(
                    InterviewReminder.is_sent.is_(False), InterviewReminder.remind_at <= as_of
                )
            )
        )

    def mark_sent(self, reminder: InterviewReminder) -> InterviewReminder:
        reminder.is_sent = True
        self._session.flush()
        return reminder

    def delete_unsent_for_interview(self, interview_id: uuid.UUID) -> None:
        """Used when rescheduling: every not-yet-fired reminder is stale
        against the old date and gets recreated from the new one — an
        already-sent reminder is historical and left alone."""
        for reminder in self._session.scalars(
            select(InterviewReminder).where(
                InterviewReminder.interview_id == interview_id, InterviewReminder.is_sent.is_(False)
            )
        ):
            self._session.delete(reminder)
        self._session.flush()

    def delete(self, reminder: InterviewReminder) -> None:
        self._session.delete(reminder)
        self._session.flush()
