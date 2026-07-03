"""
Evaluates jobs already stored in the database against a candidate profile
and persists the results — the bridge between `MatchingEngine` (pure,
DB-independent) and the `database.repositories` layer (persistence).

Deliberately does not bypass the repository layer: reads go through
`JobRepository`, writes go through `JobMatchRepository`. This class only
owns the business logic of *when* to insert vs. update a JobMatch (the same
job re-evaluated for the same user updates the existing row) and how a
`MatchResult` maps onto `JobMatch`'s columns.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from job_automation.ai.matching_engine import MatchingEngine
from job_automation.ai.matching_models import CandidateProfile, JobSnapshot
from job_automation.database.models.job import Job
from job_automation.database.models.job_match import JobMatch
from job_automation.database.repositories.job_match_repository import JobMatchRepository
from job_automation.database.repositories.job_repository import JobRepository
from job_automation.utils.logger import logger


class MatchingService:
    def __init__(
        self,
        session: Session,
        engine: MatchingEngine,
        *,
        job_repository: JobRepository | None = None,
        job_match_repository: JobMatchRepository | None = None,
    ) -> None:
        self._session = session
        self._engine = engine
        self._jobs = job_repository or JobRepository(session)
        self._matches = job_match_repository or JobMatchRepository(session)

    def evaluate_job(self, job: Job, candidate: CandidateProfile, *, user_id: uuid.UUID) -> JobMatch:
        """Evaluate one Job against the candidate profile and persist the
        result — inserting a new JobMatch, or updating the existing one for
        this (job, user) pair if a previous evaluation exists."""
        snapshot = JobSnapshot.from_job(job)
        result = self._engine.evaluate(candidate, snapshot)

        fields = {
            "match_score": result.overall_score,
            "matched_keywords": ", ".join(result.matched_keywords) or None,
            "analysis": result.to_dict(),
        }

        existing = self._matches.find(job_id=job.id, user_id=user_id)
        if existing is not None:
            match = self._matches.update(existing, **fields)
            logger.info("Updated match for {!r}: {}", job.title, result.overall_score)
        else:
            match = self._matches.create(job_id=job.id, user_id=user_id, **fields)
            logger.info("Created match for {!r}: {}", job.title, result.overall_score)
        return match

    def evaluate_active_jobs(self, candidate: CandidateProfile, *, user_id: uuid.UUID) -> list[JobMatch]:
        """Evaluate every currently-active job in the database for this
        candidate/user."""
        jobs = self._jobs.list_active()
        logger.info("Evaluating {} active jobs for user {}", len(jobs), user_id)
        return [self.evaluate_job(job, candidate, user_id=user_id) for job in jobs]
