"""
Persists `EmployerActivityLogEntry` rows — one candidate's notes +
communication-history timeline for an employer. Pure data access,
following the same repository pattern as every other repository in this
project.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.employer_activity_log_entry import EmployerActivityLogEntry


class EmployerActivityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        employer_id: uuid.UUID,
        user_id: uuid.UUID,
        entry_type: str,
        body: str,
        occurred_at: datetime,
        contact_id: uuid.UUID | None = None,
        channel: str | None = None,
    ) -> EmployerActivityLogEntry:
        entry = EmployerActivityLogEntry(
            employer_id=employer_id,
            user_id=user_id,
            entry_type=entry_type,
            body=body,
            occurred_at=occurred_at,
            contact_id=contact_id,
            channel=channel,
        )
        self._session.add(entry)
        self._session.flush()
        return entry

    def get(self, entry_id: uuid.UUID) -> EmployerActivityLogEntry | None:
        return self._session.get(EmployerActivityLogEntry, entry_id)

    def list_for_employer(
        self, *, user_id: uuid.UUID, employer_id: uuid.UUID, entry_type: str | None = None
    ) -> list[EmployerActivityLogEntry]:
        stmt = select(EmployerActivityLogEntry).where(
            EmployerActivityLogEntry.user_id == user_id, EmployerActivityLogEntry.employer_id == employer_id
        )
        if entry_type is not None:
            stmt = stmt.where(EmployerActivityLogEntry.entry_type == entry_type)
        stmt = stmt.order_by(EmployerActivityLogEntry.occurred_at.desc())
        return list(self._session.scalars(stmt))

    def delete(self, entry: EmployerActivityLogEntry) -> None:
        self._session.delete(entry)
        self._session.flush()
