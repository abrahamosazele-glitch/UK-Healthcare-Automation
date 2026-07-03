"""
Persists `InterviewRecord` rows. Pure data access — no status-transition
validation, no reminder/checklist seeding, no event publishing (that's
`interview_service.py`'s job), following the same repository pattern as
every other repository in this project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.interview_record import InterviewRecord


class InterviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, **fields: Any) -> InterviewRecord:
        interview = InterviewRecord(**fields)
        self._session.add(interview)
        self._session.flush()
        return interview

    def get(self, interview_id: uuid.UUID) -> InterviewRecord | None:
        return self._session.get(InterviewRecord, interview_id)

    def update(self, interview: InterviewRecord, **fields: Any) -> InterviewRecord:
        for key, value in fields.items():
            setattr(interview, key, value)
        self._session.flush()
        return interview

    def list_for_user(self, user_id: uuid.UUID, *, status: str | None = None) -> list[InterviewRecord]:
        stmt = select(InterviewRecord).where(InterviewRecord.user_id == user_id)
        if status is not None:
            stmt = stmt.where(InterviewRecord.status == status)
        return list(self._session.scalars(stmt.order_by(InterviewRecord.scheduled_at)))

    def list_for_employer(self, *, user_id: uuid.UUID, employer_id: uuid.UUID) -> list[InterviewRecord]:
        return list(
            self._session.scalars(
                select(InterviewRecord)
                .where(InterviewRecord.user_id == user_id, InterviewRecord.employer_id == employer_id)
                .order_by(InterviewRecord.scheduled_at.desc())
            )
        )

    def list_for_application_workflow(
        self, *, user_id: uuid.UUID, application_workflow_id: uuid.UUID
    ) -> list[InterviewRecord]:
        return list(
            self._session.scalars(
                select(InterviewRecord)
                .where(
                    InterviewRecord.user_id == user_id,
                    InterviewRecord.application_workflow_id == application_workflow_id,
                )
                .order_by(InterviewRecord.scheduled_at)
            )
        )

    def list_between(self, user_id: uuid.UUID, *, start: datetime, end: datetime) -> list[InterviewRecord]:
        """Every interview scheduled within `[start, end)` — the calendar
        page's one query for a month/week/day grid."""
        return list(
            self._session.scalars(
                select(InterviewRecord)
                .where(
                    InterviewRecord.user_id == user_id,
                    InterviewRecord.scheduled_at >= start,
                    InterviewRecord.scheduled_at < end,
                )
                .order_by(InterviewRecord.scheduled_at)
            )
        )

    def list_upcoming(self, user_id: uuid.UUID, *, as_of: datetime, limit: int = 10) -> list[InterviewRecord]:
        return list(
            self._session.scalars(
                select(InterviewRecord)
                .where(InterviewRecord.user_id == user_id, InterviewRecord.scheduled_at >= as_of)
                .order_by(InterviewRecord.scheduled_at)
                .limit(limit)
            )
        )

    def list_recent_outcomes(self, user_id: uuid.UUID, *, limit: int = 10) -> list[InterviewRecord]:
        from job_automation.interviews.interview_models import InterviewStatus

        completed_family = {
            InterviewStatus.COMPLETED.value,
            InterviewStatus.OFFER_RECEIVED.value,
            InterviewStatus.REJECTED.value,
            InterviewStatus.WAITING_DECISION.value,
        }
        return list(
            self._session.scalars(
                select(InterviewRecord)
                .where(InterviewRecord.user_id == user_id, InterviewRecord.status.in_(completed_family))
                .order_by(InterviewRecord.updated_at.desc())
                .limit(limit)
            )
        )
