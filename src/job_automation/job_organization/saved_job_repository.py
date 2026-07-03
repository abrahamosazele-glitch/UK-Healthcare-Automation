"""
Persists `SavedJob` rows. Pure data access — no pipeline-transition
validation, no event publishing (that's `job_organization_service.py`'s
job), following the same repository pattern as every other repository in
this project.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.saved_job import SavedJob
from job_automation.job_organization.job_organization_models import PipelineStage


class SavedJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, *, user_id: uuid.UUID, job_id: uuid.UUID) -> SavedJob | None:
        return self._session.scalars(
            select(SavedJob).where(SavedJob.user_id == user_id, SavedJob.job_id == job_id)
        ).first()

    def get(self, saved_job_id: uuid.UUID) -> SavedJob | None:
        return self._session.get(SavedJob, saved_job_id)

    def get_or_create(self, *, user_id: uuid.UUID, job_id: uuid.UUID) -> SavedJob:
        existing = self.find(user_id=user_id, job_id=job_id)
        if existing is not None:
            return existing
        saved_job = SavedJob(
            user_id=user_id, job_id=job_id, is_saved=True, pipeline_stage=PipelineStage.NEW.value
        )
        self._session.add(saved_job)
        self._session.flush()
        return saved_job

    def update(self, saved_job: SavedJob, **fields: Any) -> SavedJob:
        for key, value in fields.items():
            setattr(saved_job, key, value)
        self._session.flush()
        return saved_job

    def map_by_job_id(self, user_id: uuid.UUID, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, SavedJob]:
        """Batched lookup for rendering a job list page: one query for
        "does this user have tracking state for any of these jobs," keyed
        by job_id, instead of an N+1 `find()` call per row."""
        if not job_ids:
            return {}
        rows = self._session.scalars(
            select(SavedJob).where(SavedJob.user_id == user_id, SavedJob.job_id.in_(job_ids))
        )
        return {row.job_id: row for row in rows}

    def list_for_user(
        self, user_id: uuid.UUID, *, include_hidden: bool = False, archived_only: bool = False
    ) -> list[SavedJob]:
        """The default view (used by the Kanban board) excludes hidden and
        archived jobs — that's what "hide"/"archive" mean. `archived_only`
        flips this to show exactly the archived jobs, for the "Restore"
        view; `include_hidden` is an escape hatch for callers that
        genuinely want hidden jobs included alongside everything else."""
        stmt = select(SavedJob).where(SavedJob.user_id == user_id)
        if archived_only:
            stmt = stmt.where(SavedJob.is_archived.is_(True))
        else:
            stmt = stmt.where(SavedJob.is_archived.is_(False))
            if not include_hidden:
                stmt = stmt.where(SavedJob.is_hidden.is_(False))
        return list(self._session.scalars(stmt.order_by(SavedJob.updated_at.desc())))
