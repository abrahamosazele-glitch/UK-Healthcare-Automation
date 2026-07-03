"""
Value objects for the notification subsystem.

Deliberately dependency-free — same design as `workflows.workflow_models`,
`scheduler.scheduler_models`, `documents.document_models`: pure
dataclasses/enums, no SQLAlchemy imports. The canonical
`NotificationType`/`NotificationSeverity` values live here; the ORM side
(`database.models.notification.Notification`) stores `.value` as a plain
string rather than importing these enums, for the same reason
`ApplicationWorkflowRecord` doesn't import `WorkflowStatus`.
"""

from __future__ import annotations

import enum


class NotificationType(str, enum.Enum):
    """What happened. Named after the event that generated it — see
    `notifications.events.EventType`, which this deliberately mirrors
    one-to-one (every event type this milestone wires up produces exactly
    one notification type); kept as a separate enum rather than reusing
    `EventType` directly so a future event could map to zero, one, or
    several notification types without forcing the two to stay identical
    forever."""

    SCHEDULER_TASK_STARTED = "scheduler_task_started"
    SCHEDULER_TASK_FINISHED = "scheduler_task_finished"
    JOB_IMPORTED = "job_imported"
    MATCH_COMPLETED = "match_completed"
    DOCUMENT_GENERATED = "document_generated"
    WORKFLOW_UPDATED = "workflow_updated"
    USER_REGISTERED = "user_registered"
    USER_LOGGED_IN = "user_logged_in"
    ERROR_OCCURRED = "error_occurred"
    PIPELINE_STAGE_UPDATED = "pipeline_stage_updated"
    REMINDER_DUE = "reminder_due"
    INTERVIEW_STATUS_UPDATED = "interview_status_updated"
    INTERVIEW_REMINDER_DUE = "interview_reminder_due"
    NEW_HIGH_MATCH_JOB = "new_high_match_job"
    NEW_BAND3_JOB = "new_band3_job"
    NEW_SPONSORSHIP_JOB = "new_sponsorship_job"
    JOB_CLOSING_SOON = "job_closing_soon"
    DAILY_DIGEST = "daily_digest"
    WEEKLY_SUMMARY = "weekly_summary"


class NotificationSeverity(str, enum.Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
