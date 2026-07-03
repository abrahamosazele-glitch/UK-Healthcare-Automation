"""
The main entry point for interview & calendar management: schedule an
interview (seeding its default preparation checklist and reminders),
reschedule/cancel/update its status, and manage its notes — the same
orchestrator role `JobOrganizationService`/`WorkflowService` play for
their own subsystems.

Publishes an `INTERVIEW_STATUS_UPDATED` event on scheduling and on every
validated status transition (never calls `NotificationService` directly —
see `notifications.events`'s module docstring), satisfying "integrate with
... Notifications." Checklist-item toggles and note edits deliberately do
**not** publish notifications, the same scope line
`JobOrganizationService` draws for flag toggles/detail edits — see
docs/INTERVIEWS.md's "Known limitations."

**Workflow integration is opt-in and always explicit.** `sync_workflow_status()`
calls the *existing* `WorkflowService.mark_interview()`/`.mark_offer()`/
`.close()` methods when an interview is linked to an
`ApplicationWorkflowRecord` (`application_workflow_id` is set) — but only
ever when a caller (a user clicking a button) invokes it. Nothing in
`schedule()`/`update_status()` calls it automatically, matching this
milestone's explicit "no automatic workflow changes without explicit user
actions" requirement (the same "never auto-advance" rule
`WorkflowService`/`SchedulerService` already established).

**`scheduled_at` is always normalized to naive UTC before it's stored**
(`_as_naive_utc()`), matching this codebase's established "naive UTC
timestamps only" convention (see `utils.helpers.utc_now()`'s docstring —
mixing naive/aware datetimes has caused real `TypeError`s in this project
before). The HTML form posts a timezone-*less* `datetime-local` value
(already effectively naive), but the JSON API accepts a `datetime` body
field that a client could send as timezone-aware — normalizing here, once,
means every other computation in this service (reminder math, "days from
application to interview," dashboard countdowns) can assume naive UTC
throughout without re-checking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from job_automation.interviews.interview_checklist_repository import InterviewChecklistRepository
from job_automation.interviews.interview_models import (
    DEFAULT_CHECKLIST_ITEMS,
    InterviewLifecycle,
    InterviewStatus,
    InterviewType,
    NoteCategory,
    RESCHEDULABLE_STATUSES,
    ReminderOffset,
)
from job_automation.interviews.interview_note_repository import InterviewNoteRepository
from job_automation.interviews.interview_reminder_repository import InterviewReminderRepository
from job_automation.interviews.interview_repository import InterviewRepository
from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.utils.logger import logger


class InterviewService:
    def __init__(
        self,
        session: Session,
        *,
        repository: InterviewRepository | None = None,
        checklist_repository: InterviewChecklistRepository | None = None,
        note_repository: InterviewNoteRepository | None = None,
        reminder_repository: InterviewReminderRepository | None = None,
        event_bus: EventBus = event_bus,
    ) -> None:
        self._session = session
        self._repository = repository or InterviewRepository(session)
        self._checklist = checklist_repository or InterviewChecklistRepository(session)
        self._notes = note_repository or InterviewNoteRepository(session)
        self._reminders = reminder_repository or InterviewReminderRepository(session)
        self._event_bus = event_bus

    # --- Scheduling ---

    def schedule(
        self,
        *,
        user_id: uuid.UUID,
        employer_id: uuid.UUID,
        interview_type: InterviewType,
        scheduled_at: datetime,
        job_id: uuid.UUID | None = None,
        application_workflow_id: uuid.UUID | None = None,
        contact_id: uuid.UUID | None = None,
        interview_stage: str | None = None,
        duration_minutes: int | None = None,
        timezone: str | None = None,
        location: str | None = None,
        meeting_link: str | None = None,
        interviewer_names: list[str] | None = None,
        reminder_offsets: list[ReminderOffset] | None = None,
        notes: str | None = None,
    ):
        scheduled_at = self._as_naive_utc(scheduled_at)
        interview = self._repository.create(
            user_id=user_id,
            employer_id=employer_id,
            job_id=job_id,
            application_workflow_id=application_workflow_id,
            contact_id=contact_id,
            interview_type=interview_type.value,
            interview_stage=interview_stage,
            status=InterviewStatus.SCHEDULED.value,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            timezone=timezone,
            location=location,
            meeting_link=meeting_link,
            interviewer_names=interviewer_names,
            reminder_offsets=[offset.value for offset in reminder_offsets] if reminder_offsets else None,
            notes=notes,
        )
        self._checklist.create_many(interview_id=interview.id, labels=list(DEFAULT_CHECKLIST_ITEMS))
        self._create_reminders(interview)
        self._publish_status_event(interview, from_status=None, to_status=InterviewStatus.SCHEDULED)
        logger.info("Scheduled interview {} for employer {} at {}", interview.id, employer_id, scheduled_at)
        return interview

    def reschedule(self, interview_id: uuid.UUID, *, user_id: uuid.UUID, new_scheduled_at: datetime, **field_updates):
        interview = self._get_owned(interview_id, user_id)
        current = InterviewStatus(interview.status)
        if current not in RESCHEDULABLE_STATUSES:
            from job_automation.interviews.interview_models import InvalidInterviewStatusTransitionError

            raise InvalidInterviewStatusTransitionError(
                f"Cannot reschedule an interview with status {current.value!r}. "
                f"Allowed from: {sorted(s.value for s in RESCHEDULABLE_STATUSES)}"
            )
        new_scheduled_at = self._as_naive_utc(new_scheduled_at)
        updated = self._repository.update(
            interview, scheduled_at=new_scheduled_at, status=InterviewStatus.SCHEDULED.value, **field_updates
        )
        self._reminders.delete_unsent_for_interview(interview.id)
        self._create_reminders(updated)
        self._publish_status_event(
            updated, from_status=current, to_status=InterviewStatus.RESCHEDULED,
            extra={"new_scheduled_at": new_scheduled_at.isoformat()},
        )
        logger.info("Rescheduled interview {} to {}", interview.id, new_scheduled_at)
        return updated

    def update_status(
        self, interview_id: uuid.UUID, *, user_id: uuid.UUID, target_status: InterviewStatus, outcome: str | None = None
    ):
        interview = self._get_owned(interview_id, user_id)
        current = InterviewStatus(interview.status)
        InterviewLifecycle.validate_transition(current, target_status)
        updated = self._repository.update(
            interview, status=target_status.value, outcome=outcome if outcome is not None else interview.outcome
        )
        self._publish_status_event(updated, from_status=current, to_status=target_status)
        logger.info("Interview {} status {} -> {}", interview.id, current.value, target_status.value)
        return updated

    def update_details(self, interview_id: uuid.UUID, *, user_id: uuid.UUID, **field_updates):
        interview = self._get_owned(interview_id, user_id)
        return self._repository.update(interview, **field_updates)

    def get(self, interview_id: uuid.UUID, *, user_id: uuid.UUID):
        return self._get_owned(interview_id, user_id)

    def list_for_user(self, user_id: uuid.UUID, *, status: InterviewStatus | None = None):
        return self._repository.list_for_user(user_id, status=status.value if status else None)

    # --- Workflow integration (always explicit — see module docstring) ---

    def sync_workflow_status(self, interview_id: uuid.UUID, *, user_id: uuid.UUID, action: str):
        """Advance the linked `ApplicationWorkflowRecord` using the
        *existing* `WorkflowService` methods — only ever called from a
        user-clicked button (`routes/interviews.py`), never from
        `schedule()`/`update_status()`. `action` is one of
        `"mark_interview"`, `"mark_offer"`, `"close_rejected"`."""
        interview = self._get_owned(interview_id, user_id)
        if interview.application_workflow_id is None:
            raise ValueError("This interview has no linked application to sync")

        from job_automation.workflows.workflow_repository import WorkflowRepository
        from job_automation.workflows.workflow_service import WorkflowService

        workflow_repository = WorkflowRepository(self._session)
        workflow = workflow_repository.get(interview.application_workflow_id)
        if workflow is None or workflow.user_id != user_id:
            raise ValueError("Linked application workflow not found")

        workflow_service = WorkflowService(self._session, repository=workflow_repository)
        if action == "mark_interview":
            return workflow_service.mark_interview(workflow, note=f"Interview scheduled: {interview.interview_type}")
        if action == "mark_offer":
            return workflow_service.mark_offer(workflow, note="Offer received after interview")
        if action == "close_rejected":
            return workflow_service.close(workflow, reason="Rejected after interview")
        raise ValueError(f"Unknown workflow sync action {action!r}")

    # --- Checklist ---

    def list_checklist(self, interview_id: uuid.UUID, *, user_id: uuid.UUID):
        self._get_owned(interview_id, user_id)
        return self._checklist.list_for_interview(interview_id)

    def add_checklist_item(self, interview_id: uuid.UUID, *, user_id: uuid.UUID, label: str):
        self._get_owned(interview_id, user_id)
        return self._checklist.create(interview_id=interview_id, label=label)

    def toggle_checklist_item(self, item_id: uuid.UUID, *, user_id: uuid.UUID):
        item = self._checklist.get(item_id)
        if item is None:
            raise ValueError(f"No checklist item {item_id}")
        self._get_owned(item.interview_id, user_id)
        return self._checklist.update(item, is_complete=not item.is_complete)

    def remove_checklist_item(self, item_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        item = self._checklist.get(item_id)
        if item is None:
            raise ValueError(f"No checklist item {item_id}")
        self._get_owned(item.interview_id, user_id)
        self._checklist.delete(item)

    def checklist_completion_percent(self, interview_id: uuid.UUID, *, user_id: uuid.UUID) -> float:
        items = self.list_checklist(interview_id, user_id=user_id)
        if not items:
            return 0.0
        done = sum(1 for item in items if item.is_complete)
        return round(100 * done / len(items), 1)

    def average_upcoming_preparation_completion(self, user_id: uuid.UUID) -> float:
        """The dashboard's "Preparation completion %" widget — averaged
        across every interview that hasn't happened yet (scheduled/
        upcoming/rescheduled); a completed/cancelled interview's checklist
        isn't "upcoming prep" anymore."""
        upcoming_statuses = {InterviewStatus.SCHEDULED, InterviewStatus.UPCOMING, InterviewStatus.RESCHEDULED}
        interviews = [
            interview
            for status in upcoming_statuses
            for interview in self._repository.list_for_user(user_id, status=status.value)
        ]
        if not interviews:
            return 0.0
        percentages = [self.checklist_completion_percent(interview.id, user_id=user_id) for interview in interviews]
        return round(sum(percentages) / len(percentages), 1)

    # --- Notes ---

    def add_note(self, interview_id: uuid.UUID, *, user_id: uuid.UUID, category: NoteCategory, body: str):
        self._get_owned(interview_id, user_id)
        return self._notes.create(interview_id=interview_id, category=category.value, body=body)

    def list_notes(self, interview_id: uuid.UUID, *, user_id: uuid.UUID, category: NoteCategory | None = None):
        self._get_owned(interview_id, user_id)
        return self._notes.list_for_interview(interview_id, category=category.value if category else None)

    def remove_note(self, note_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        note = self._notes.get(note_id)
        if note is None:
            raise ValueError(f"No note {note_id}")
        self._get_owned(note.interview_id, user_id)
        self._notes.delete(note)

    # --- Reminders ---

    def list_reminders(self, interview_id: uuid.UUID, *, user_id: uuid.UUID):
        self._get_owned(interview_id, user_id)
        return self._reminders.list_for_interview(interview_id)

    def process_due_reminders(self, *, as_of: datetime | None = None) -> dict:
        """Publishes an `INTERVIEW_REMINDER_DUE` event per due reminder and
        marks it sent — genuinely reusable business logic, wrapped by
        `scheduler.tasks.send_due_interview_reminders` the same way
        `job_organization.reminder_service.ReminderService
        .process_due_reminders()` is wrapped by `send_due_reminders`."""
        from job_automation.utils.helpers import utc_now

        as_of = as_of or utc_now()
        due = self._reminders.list_due(as_of=as_of)

        processed = 0
        for reminder in due:
            interview = self._repository.get(reminder.interview_id)
            if interview is None:
                continue
            self._event_bus.publish(
                Event(
                    event_type=EventType.INTERVIEW_REMINDER_DUE,
                    payload={
                        "reminder_id": str(reminder.id),
                        "interview_id": str(interview.id),
                        "offset": reminder.offset,
                    },
                    user_id=interview.user_id,
                ),
                self._session,
            )
            self._reminders.mark_sent(reminder)
            processed += 1

        logger.info("process_due_reminders (interviews): {} reminder(s) processed", processed)
        return {"reminders_processed": processed}

    # --- internals ---

    @staticmethod
    def _as_naive_utc(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def _get_owned(self, interview_id: uuid.UUID, user_id: uuid.UUID):
        interview = self._repository.get(interview_id)
        if interview is None or interview.user_id != user_id:
            raise ValueError(f"No interview {interview_id} visible to user {user_id}")
        return interview

    def _create_reminders(self, interview) -> None:
        for offset_value in interview.reminder_offsets or []:
            offset = ReminderOffset(offset_value)
            remind_at = interview.scheduled_at - offset.timedelta
            self._reminders.create(interview_id=interview.id, offset=offset.value, remind_at=remind_at)

    def _publish_status_event(
        self, interview, *, from_status: InterviewStatus | None, to_status: InterviewStatus, extra: dict | None = None
    ) -> None:
        payload = {
            "interview_id": str(interview.id),
            "employer_id": str(interview.employer_id),
            "from_status": from_status.value if from_status else None,
            "to_status": to_status.value,
        }
        if extra:
            payload.update(extra)
        self._event_bus.publish(
            Event(event_type=EventType.INTERVIEW_STATUS_UPDATED, payload=payload, user_id=interview.user_id),
            self._session,
        )
