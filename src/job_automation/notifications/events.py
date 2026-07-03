"""
Event definitions for the lightweight event bus (`event_bus.py`).

Dependency-free, like `notification_models.py` — an `Event` is a plain,
JSON-serializable-ish record of "something happened," with no knowledge of
notifications, SQLAlchemy, or any specific subscriber. This is what makes
the event bus a genuine decoupling point: `job_automation.workflows`,
`job_automation.documents`, `job_automation.auth`, and
`job_automation.scheduler` each publish `Event`s without importing
anything from `job_automation.notifications` — only
`notification_listeners.py` (which subscribes to the bus) knows that a
`WORKFLOW_UPDATED` event should become a notification. A future consumer
(analytics, an audit log, a real email dispatcher) can subscribe to the
exact same events without the publisher ever changing.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from job_automation.utils.helpers import utc_now


class EventType(str, enum.Enum):
    SCHEDULER_TASK_STARTED = "scheduler_task_started"
    SCHEDULER_TASK_FINISHED = "scheduler_task_finished"
    JOB_IMPORTED = "job_imported"
    MATCH_COMPLETED = "match_completed"
    DOCUMENT_GENERATED = "document_generated"
    WORKFLOW_UPDATED = "workflow_updated"
    USER_REGISTERED = "user_registered"
    USER_LOGGED_IN = "user_logged_in"
    ERROR_OCCURRED = "error_occurred"
    #: Added for the Job Management milestone — published by
    #: `job_organization.job_organization_service.JobOrganizationService
    #: .update_stage()` on every validated Kanban pipeline transition.
    PIPELINE_STAGE_UPDATED = "pipeline_stage_updated"
    #: Published by `scheduler.tasks.send_due_reminders` when a
    #: `JobReminder`'s `remind_at` has passed.
    REMINDER_DUE = "reminder_due"
    #: Added for the Interview & Calendar Management milestone — published
    #: by `interviews.interview_service.InterviewService` on every
    #: scheduled/rescheduled/status-changed interview.
    INTERVIEW_STATUS_UPDATED = "interview_status_updated"
    #: Published by `scheduler.tasks.send_due_interview_reminders` when an
    #: `InterviewReminder`'s `remind_at` has passed.
    INTERVIEW_REMINDER_DUE = "interview_reminder_due"
    #: Added for the Job Ingestion Service milestone — published by
    #: `ingestion.auto_match_service` when a newly-imported job's AI match
    #: score for a given user exceeds `settings
    #: .job_ingestion_high_match_threshold`. Never triggers document
    #: generation itself — see that module's docstring for why real
    #: spend still requires an explicit click.
    NEW_HIGH_MATCH_JOB = "new_high_match_job"
    #: Published by `ingestion.auto_match_service` for every newly-imported
    #: `Job` with `band == "Band 3"`, regardless of match score.
    NEW_BAND3_JOB = "new_band3_job"
    #: Published by `ingestion.auto_match_service` for every newly-imported
    #: `Job` with `visa_sponsorship is True`, regardless of match score.
    NEW_SPONSORSHIP_JOB = "new_sponsorship_job"
    #: Published by `scheduler.tasks.check_closing_soon_jobs` for a job
    #: newly within `settings.job_ingestion_closing_soon_hours` of its
    #: closing date — once per (job, user) pair, guarded by
    #: `Job.closing_soon_notified_at`.
    JOB_CLOSING_SOON = "job_closing_soon"
    #: Added for the Real Email Notification Delivery milestone —
    #: published once per user by `scheduler.tasks.send_daily_digest` when
    #: that user's configured `daily_digest_hour` (UTC) matches the
    #: current hour and today's digest hasn't already been sent
    #: (`NotificationPreferences.last_daily_digest_sent_date`).
    DAILY_DIGEST = "daily_digest"
    #: Same as `DAILY_DIGEST`, weekly (Mondays), published by
    #: `scheduler.tasks.send_weekly_summary`.
    WEEKLY_SUMMARY = "weekly_summary"


@dataclass(frozen=True)
class Event:
    event_type: EventType
    #: Free-form, JSON-serializable details a subscriber needs — e.g.
    #: `{"task_name": "import_fixture_jobs", "result_summary": {...}}` for
    #: a `SCHEDULER_TASK_FINISHED` event. Deliberately a plain `dict`
    #: rather than a per-event-type dataclass: with only a handful of
    #: subscribers today (all inside `notification_listeners.py`), a
    #: dedicated payload type per event would be pure ceremony; revisit if
    #: a second, independent subscriber ever needs stronger typing.
    payload: dict = field(default_factory=dict)
    #: Which user this event is about, if any — `None` for system-wide
    #: events (e.g. a scheduler task isn't "about" any one user). Kept as
    #: a top-level field (not buried in `payload`) since almost every
    #: subscriber needs it to know who to notify.
    user_id: uuid.UUID | None = None
    occurred_at: datetime = field(default_factory=utc_now)
