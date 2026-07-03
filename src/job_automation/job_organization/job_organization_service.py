"""
The main entry point for job organization: save/favourite/hide/archive a
job, move it through the personal Kanban pipeline, and edit its tracking
details (notes, rating, priority, deadline, interview date, tags,
checklist) — the same orchestrator role `WorkflowService`/`DocumentService`
play for their own subsystems.

Publishes a `PIPELINE_STAGE_UPDATED` event on every validated stage
transition (never calls `NotificationService` directly — see
`notifications.events`'s module docstring), satisfying "every transition
must create a notification." Flag toggles (save/favourite/hide/archive)
and detail edits (notes/rating/tags/etc.) deliberately do **not** publish
notifications — see docs/JOB_MANAGEMENT.md's "Known limitations" for why
that scope was drawn there (only pipeline progress is notification-worthy;
toggling a checkbox isn't).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy.orm import Session

from job_automation.job_organization.job_organization_models import (
    JobPipeline,
    JobPriority,
    PipelineStage,
)
from job_automation.job_organization.saved_job_repository import SavedJobRepository
from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.utils.logger import logger

_MIN_RATING = 1
_MAX_RATING = 5


class JobOrganizationService:
    def __init__(
        self,
        session: Session,
        *,
        repository: SavedJobRepository | None = None,
        event_bus: EventBus = event_bus,
    ) -> None:
        self._session = session
        self._repository = repository or SavedJobRepository(session)
        self._event_bus = event_bus

    # --- Organization flags ---

    def save(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_saved=True)

    def unsave(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_saved=False)

    def favourite(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        """Favouriting also saves the job — a favourite that isn't even
        saved would be a confusing, inconsistent state to show in the UI."""
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_saved=True, is_favourite=True)

    def unfavourite(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_favourite=False)

    def hide(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_hidden=True)

    def unhide(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_hidden=False)

    def archive(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_archived=True)

    def restore(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        """"Restore archived jobs" — the one explicitly-requested inverse
        action; every other flag already has a symmetric unset method."""
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(saved_job, is_archived=False)

    # --- Kanban pipeline ---

    def update_stage(self, *, user_id: uuid.UUID, job_id: uuid.UUID, target_stage: PipelineStage):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        current_stage = PipelineStage(saved_job.pipeline_stage)
        JobPipeline.validate_transition(current_stage, target_stage)

        updated = self._repository.update(saved_job, pipeline_stage=target_stage.value)
        logger.info(
            "SavedJob {} pipeline stage {} -> {}", saved_job.id, current_stage.value, target_stage.value
        )
        self._event_bus.publish(
            Event(
                event_type=EventType.PIPELINE_STAGE_UPDATED,
                payload={
                    "saved_job_id": str(saved_job.id),
                    "job_id": str(job_id),
                    "from_stage": current_stage.value,
                    "to_stage": target_stage.value,
                },
                user_id=user_id,
            ),
            self._session,
        )
        return updated

    # --- Tracking details ---

    def update_details(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        notes: str | None = None,
        personal_rating: int | None = None,
        priority: JobPriority | None = None,
        deadline: date | None = None,
        interview_date: datetime | None = None,
    ):
        if personal_rating is not None and not (_MIN_RATING <= personal_rating <= _MAX_RATING):
            raise ValueError(f"personal_rating must be between {_MIN_RATING} and {_MAX_RATING}")

        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.update(
            saved_job,
            notes=notes,
            personal_rating=personal_rating,
            priority=priority.value if priority else None,
            deadline=deadline,
            interview_date=interview_date,
        )

    def set_tags(self, *, user_id: uuid.UUID, job_id: uuid.UUID, tags: list[str]):
        """Replaces the whole tag list — matches how the rest of this
        dashboard edits comma-separated lists (e.g. `routes/settings.py`'s
        `preferred_locations`), one full-replacement form submission
        rather than incremental add/remove calls."""
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        cleaned = [tag.strip() for tag in tags if tag.strip()]
        return self._repository.update(saved_job, tags=cleaned or None)

    # --- Checklist ---

    def add_checklist_item(self, *, user_id: uuid.UUID, job_id: uuid.UUID, label: str):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        items = list(saved_job.checklist or [])
        items.append({"label": label, "done": False})
        return self._repository.update(saved_job, checklist=items)

    def toggle_checklist_item(self, *, user_id: uuid.UUID, job_id: uuid.UUID, index: int):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        items = list(saved_job.checklist or [])
        if not (0 <= index < len(items)):
            raise IndexError(f"No checklist item at index {index}")
        items[index] = {**items[index], "done": not items[index]["done"]}
        return self._repository.update(saved_job, checklist=items)

    def remove_checklist_item(self, *, user_id: uuid.UUID, job_id: uuid.UUID, index: int):
        saved_job = self._repository.get_or_create(user_id=user_id, job_id=job_id)
        items = list(saved_job.checklist or [])
        if not (0 <= index < len(items)):
            raise IndexError(f"No checklist item at index {index}")
        items.pop(index)
        return self._repository.update(saved_job, checklist=items or None)

    # --- Reads ---

    def get_for_user_and_job(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        return self._repository.find(user_id=user_id, job_id=job_id)

    def list_for_user(self, user_id: uuid.UUID, *, include_hidden: bool = False, archived_only: bool = False):
        return self._repository.list_for_user(
            user_id, include_hidden=include_hidden, archived_only=archived_only
        )
