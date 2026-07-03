"""
The main entry point for job reminders: create a reminder for a tracked
job, list upcoming ones, and process due ones (publishing a
`REMINDER_DUE` event per reminder — never calling `NotificationService`
directly, see `notifications.events`'s module docstring).

`process_due_reminders()` is genuinely reusable business logic, not
scheduler-specific — `scheduler.tasks.send_due_reminders` is a thin
wrapper that just calls it with "now," the same separation already
established by `run_ai_matching.py`/`generate_draft_documents.py`
wrapping `MatchingService`/`DocumentService`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from job_automation.job_organization.job_organization_models import ReminderType
from job_automation.job_organization.reminder_repository import ReminderRepository
from job_automation.job_organization.saved_job_repository import SavedJobRepository
from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.utils.helpers import utc_now
from job_automation.utils.logger import logger


class ReminderService:
    def __init__(
        self,
        session: Session,
        *,
        repository: ReminderRepository | None = None,
        saved_job_repository: SavedJobRepository | None = None,
        event_bus: EventBus = event_bus,
    ) -> None:
        self._session = session
        self._repository = repository or ReminderRepository(session)
        self._saved_jobs = saved_job_repository or SavedJobRepository(session)
        self._event_bus = event_bus

    def create_reminder(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        reminder_type: ReminderType,
        remind_at: datetime,
        message: str | None = None,
    ):
        saved_job = self._saved_jobs.get_or_create(user_id=user_id, job_id=job_id)
        return self._repository.create(
            saved_job_id=saved_job.id, reminder_type=reminder_type, remind_at=remind_at, message=message
        )

    def list_for_job(self, *, user_id: uuid.UUID, job_id: uuid.UUID):
        saved_job = self._saved_jobs.find(user_id=user_id, job_id=job_id)
        if saved_job is None:
            return []
        return self._repository.list_for_saved_job(saved_job.id)

    def list_upcoming_for_user(self, user_id: uuid.UUID, *, limit: int = 10):
        return self._repository.list_upcoming_for_user(user_id, limit=limit)

    def delete_reminder(self, reminder_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        reminder = self._repository.get(reminder_id)
        if reminder is None or reminder.saved_job.user_id != user_id:
            raise ValueError(f"No reminder {reminder_id} visible to user {user_id}")
        self._repository.delete(reminder)

    def process_due_reminders(self, *, as_of: datetime | None = None) -> dict:
        as_of = as_of or utc_now()
        due = self._repository.list_due(as_of=as_of)

        processed = 0
        for reminder in due:
            saved_job = reminder.saved_job
            self._event_bus.publish(
                Event(
                    event_type=EventType.REMINDER_DUE,
                    payload={
                        "reminder_id": str(reminder.id),
                        "job_id": str(saved_job.job_id),
                        "reminder_type": reminder.reminder_type,
                        "message": reminder.message,
                    },
                    user_id=saved_job.user_id,
                ),
                self._session,
            )
            self._repository.mark_sent(reminder)
            processed += 1

        logger.info("process_due_reminders: {} reminder(s) processed", processed)
        return {"reminders_processed": processed}
