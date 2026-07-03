"""
Persists `CandidateProfile` via `CandidateProfileRecord`, following the same
repository pattern already established in `database.repositories`
(`JobRepository`, `EmployerRepository`, `JobMatchRepository`): a thin,
Session-wrapped class with find/create/update methods and no business logic
of its own. Placed inside `job_automation.profile` rather than
`database.repositories` per this milestone's file list — the pattern is
identical either way, only the package location differs.

One profile per user (`CandidateProfileRecord.user_id` is unique), so
`save()` always finds-or-creates rather than requiring the caller to know
whether a profile already exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.candidate_profile_record import CandidateProfileRecord
from job_automation.profile.candidate_profile import CandidateProfile


class ProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, user_id: uuid.UUID) -> CandidateProfileRecord | None:
        return self._session.scalars(
            select(CandidateProfileRecord).where(CandidateProfileRecord.user_id == user_id)
        ).first()

    def save(
        self, profile: CandidateProfile, *, user_id: uuid.UUID, source_format: str | None = None
    ) -> CandidateProfileRecord:
        """Insert a new record, or update the existing one for this user —
        never creates a second row for the same user."""
        existing = self.find(user_id)
        if existing is not None:
            existing.data = profile.to_dict()
            existing.source_format = source_format
            self._session.flush()
            return existing

        record = CandidateProfileRecord(user_id=user_id, data=profile.to_dict(), source_format=source_format)
        self._session.add(record)
        self._session.flush()
        return record

    def load(self, user_id: uuid.UUID) -> CandidateProfile | None:
        record = self.find(user_id)
        if record is None:
            return None
        return CandidateProfile.from_dict(record.data)

    def delete(self, user_id: uuid.UUID) -> None:
        record = self.find(user_id)
        if record is not None:
            self._session.delete(record)
            self._session.flush()
