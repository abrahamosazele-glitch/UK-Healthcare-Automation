"""
Data access for JobMatch rows — mirrors JobRepository's shape (find/create/
update, no business logic) for the same reasons: `(job_id, user_id)` is
unique, so re-running the matching engine for a candidate against a job
they've already been scored against must update that row, never insert a
second one. Deciding *what* score/analysis to write is
`job_automation.ai.matching_service.MatchingService`'s job, not this class's.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.job_match import JobMatch


class JobMatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, *, job_id: uuid.UUID, user_id: uuid.UUID) -> JobMatch | None:
        return self._session.scalars(
            select(JobMatch).where(JobMatch.job_id == job_id, JobMatch.user_id == user_id)
        ).first()

    def get(self, match_id: uuid.UUID) -> JobMatch | None:
        return self._session.get(JobMatch, match_id)

    def list_for_user(self, user_id: uuid.UUID) -> list[JobMatch]:
        """Every match for this candidate, best score first — added for the
        web dashboard's Matches page (purely additive; nothing about
        `find()`/`create()`/`update()` changed)."""
        return list(
            self._session.scalars(
                select(JobMatch).where(JobMatch.user_id == user_id).order_by(JobMatch.match_score.desc())
            )
        )

    def create(self, **fields: Any) -> JobMatch:
        match = JobMatch(**fields)
        self._session.add(match)
        self._session.flush()
        return match

    def update(self, match: JobMatch, **fields: Any) -> JobMatch:
        for key, value in fields.items():
            setattr(match, key, value)
        self._session.flush()
        return match
