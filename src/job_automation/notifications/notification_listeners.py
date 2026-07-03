"""
Subscribes notification-creating handlers to the event bus — the only
module in this codebase that imports both `events.py` and
`notification_service.py`. Every existing-module integration this
milestone adds (scheduler, AI matching, documents, workflow, auth)
publishes an `Event` and knows nothing about notifications at all; this
module is where "an event happened" becomes "create a notification,"
exactly the decoupling `events.py`'s module docstring describes.

A handler that decides an event isn't worth notifying about (e.g. a
fixture-import run that created and updated nothing) simply returns
without calling `NotificationService.create()` — silence is a valid,
deliberate outcome here, not a bug.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.notifications.notification_models import NotificationSeverity, NotificationType
from job_automation.notifications.notification_service import NotificationService
from job_automation.utils.logger import logger


def register_notification_listeners(bus: EventBus | None = None) -> None:
    """Idempotent per bus instance (tracked via a dynamic attribute on the
    `EventBus` object itself, not a module-level flag) — so the real
    shared `event_bus` singleton only ever registers once regardless of
    how many times this is called, while tests can freely construct a
    fresh `EventBus()` and register cleanly on it every time without
    being blocked by an unrelated test's earlier registration."""
    bus = bus if bus is not None else event_bus
    if getattr(bus, "_notification_listeners_registered", False):
        return

    bus.subscribe(EventType.SCHEDULER_TASK_STARTED, _on_scheduler_task_started)
    bus.subscribe(EventType.SCHEDULER_TASK_FINISHED, _on_scheduler_task_finished)
    bus.subscribe(EventType.JOB_IMPORTED, _on_job_imported)
    bus.subscribe(EventType.MATCH_COMPLETED, _on_match_completed)
    bus.subscribe(EventType.DOCUMENT_GENERATED, _on_document_generated)
    bus.subscribe(EventType.WORKFLOW_UPDATED, _on_workflow_updated)
    bus.subscribe(EventType.USER_REGISTERED, _on_user_registered)
    bus.subscribe(EventType.USER_LOGGED_IN, _on_user_logged_in)
    bus.subscribe(EventType.ERROR_OCCURRED, _on_error_occurred)
    bus.subscribe(EventType.PIPELINE_STAGE_UPDATED, _on_pipeline_stage_updated)
    bus.subscribe(EventType.REMINDER_DUE, _on_reminder_due)
    bus.subscribe(EventType.INTERVIEW_STATUS_UPDATED, _on_interview_status_updated)
    bus.subscribe(EventType.INTERVIEW_REMINDER_DUE, _on_interview_reminder_due)
    bus.subscribe(EventType.NEW_HIGH_MATCH_JOB, _on_new_high_match_job)
    bus.subscribe(EventType.NEW_BAND3_JOB, _on_new_band3_job)
    bus.subscribe(EventType.NEW_SPONSORSHIP_JOB, _on_new_sponsorship_job)
    bus.subscribe(EventType.JOB_CLOSING_SOON, _on_job_closing_soon)
    bus.subscribe(EventType.DAILY_DIGEST, _on_daily_digest)
    bus.subscribe(EventType.WEEKLY_SUMMARY, _on_weekly_summary)

    bus._notification_listeners_registered = True


def _display_name(task_name: str) -> str:
    return task_name.replace("_", " ").title()


def _on_scheduler_task_started(event: Event, session: Session) -> None:
    task_name = event.payload["task_name"]
    NotificationService(session).create(
        user_id=None,
        type=NotificationType.SCHEDULER_TASK_STARTED,
        title=f"Task started: {_display_name(task_name)}",
        message=f"'{_display_name(task_name)}' started (triggered by {event.payload.get('triggered_by', 'schedule')}).",
        severity=NotificationSeverity.INFO,
        source="scheduler",
        metadata=event.payload,
    )


def _on_scheduler_task_finished(event: Event, session: Session) -> None:
    task_name = event.payload["task_name"]
    status = event.payload.get("status", "unknown")
    severity = NotificationSeverity.SUCCESS if status == "success" else NotificationSeverity.WARNING
    message = f"'{_display_name(task_name)}' finished with status: {status}."
    if event.payload.get("result_summary"):
        message += f" {event.payload['result_summary']}"
    NotificationService(session).create(
        user_id=None,
        type=NotificationType.SCHEDULER_TASK_FINISHED,
        title=f"Task finished: {_display_name(task_name)}",
        message=message,
        severity=severity,
        source="scheduler",
        metadata=event.payload,
    )


def _on_job_imported(event: Event, session: Session) -> None:
    created = event.payload.get("jobs_created", 0)
    updated = event.payload.get("jobs_updated", 0)
    if not created and not updated:
        return  # nothing changed — not worth a notification
    NotificationService(session).create(
        user_id=None,
        type=NotificationType.JOB_IMPORTED,
        title="New jobs imported",
        message=f"Imported {created} new job listing(s), updated {updated} existing one(s).",
        severity=NotificationSeverity.INFO,
        source="scheduler",
        metadata=event.payload,
    )


def _on_match_completed(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    matches = event.payload.get("matches_evaluated", 0)
    if not matches:
        return  # no active jobs to match against — not worth a notification
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.MATCH_COMPLETED,
        title="AI matching completed",
        message=f"{matches} job match(es) evaluated for your profile.",
        severity=NotificationSeverity.INFO,
        source="ai_matching",
        metadata=event.payload,
    )


def _on_document_generated(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    document_type = event.payload.get("document_type", "document").replace("_", " ")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.DOCUMENT_GENERATED,
        title="Document generated",
        message=f"A new {document_type} draft is ready for your review.",
        severity=NotificationSeverity.INFO,
        source="documents",
        metadata=event.payload,
    )


def _on_workflow_updated(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    from_status = event.payload.get("from_status") or "new match"
    to_status = event.payload.get("to_status", "updated")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.WORKFLOW_UPDATED,
        title="Application status updated",
        message=f"Your application moved from {from_status.replace('_', ' ')} to {to_status.replace('_', ' ')}.",
        severity=NotificationSeverity.INFO,
        source="workflow",
        metadata=event.payload,
    )


def _on_user_registered(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.USER_REGISTERED,
        title="Welcome to UK Healthcare Job Automation",
        message="Your account has been created successfully.",
        severity=NotificationSeverity.SUCCESS,
        source="auth",
        metadata=event.payload,
    )


def _on_user_logged_in(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.USER_LOGGED_IN,
        title="New login",
        message="You logged in successfully.",
        severity=NotificationSeverity.INFO,
        source="auth",
        metadata=event.payload,
    )


def _on_error_occurred(event: Event, session: Session) -> None:
    source = event.payload.get("source", "application")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.ERROR_OCCURRED,
        title=f"Error in {_display_name(source)}",
        message=event.payload.get("error_message", "An unknown error occurred."),
        severity=NotificationSeverity.ERROR,
        source=source,
        metadata=event.payload,
    )


def _on_pipeline_stage_updated(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    from_stage = event.payload.get("from_stage", "new")
    to_stage = event.payload.get("to_stage", "updated")
    severity = NotificationSeverity.ERROR if to_stage == "rejected" else NotificationSeverity.SUCCESS
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.PIPELINE_STAGE_UPDATED,
        title="Job pipeline updated",
        message=f"Moved from {from_stage.replace('_', ' ')} to {to_stage.replace('_', ' ')}.",
        severity=severity,
        source="job_organization",
        metadata=event.payload,
    )


def _on_reminder_due(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    reminder_type = event.payload.get("reminder_type", "reminder").replace("_", " ")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.REMINDER_DUE,
        title=f"Reminder: {reminder_type}",
        message=event.payload.get("message") or f"Your {reminder_type} reminder is due.",
        severity=NotificationSeverity.WARNING,
        source="job_organization",
        metadata=event.payload,
    )


def _on_interview_status_updated(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    from_status = event.payload.get("from_status") or "none"
    to_status = event.payload.get("to_status", "updated")
    severity = (
        NotificationSeverity.ERROR
        if to_status == "rejected"
        else NotificationSeverity.SUCCESS
        if to_status in ("offer_received", "scheduled")
        else NotificationSeverity.INFO
    )
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.INTERVIEW_STATUS_UPDATED,
        title="Interview updated",
        message=f"Interview moved from {from_status.replace('_', ' ')} to {to_status.replace('_', ' ')}.",
        severity=severity,
        source="interviews",
        metadata=event.payload,
    )


def _on_interview_reminder_due(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    offset = event.payload.get("offset", "reminder").replace("_", " ")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.INTERVIEW_REMINDER_DUE,
        title=f"Interview reminder: {offset}",
        message=event.payload.get("message") or f"Your interview is coming up ({offset}).",
        severity=NotificationSeverity.WARNING,
        source="interviews",
        metadata=event.payload,
    )


def _on_new_high_match_job(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    job_title = event.payload.get("job_title", "A job")
    employer_name = event.payload.get("employer_name", "an employer")
    score = event.payload.get("match_score", 0)
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.NEW_HIGH_MATCH_JOB,
        title="New high-match job",
        message=(
            f"{job_title} at {employer_name} matches your profile at {score:.0f}%. "
            "View the job to generate a cover letter, supporting statement, interview prep, "
            "or skills-gap analysis."
        ),
        severity=NotificationSeverity.SUCCESS,
        source="ingestion",
        metadata=event.payload,
    )


def _on_new_band3_job(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    job_title = event.payload.get("job_title", "A job")
    employer_name = event.payload.get("employer_name", "an employer")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.NEW_BAND3_JOB,
        title="New Band 3 job",
        message=f"{job_title} at {employer_name} was just imported as a Band 3 role.",
        severity=NotificationSeverity.INFO,
        source="ingestion",
        metadata=event.payload,
    )


def _on_new_sponsorship_job(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    job_title = event.payload.get("job_title", "A job")
    employer_name = event.payload.get("employer_name", "an employer")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.NEW_SPONSORSHIP_JOB,
        title="New visa sponsorship job",
        message=f"{job_title} at {employer_name} was just imported offering visa sponsorship.",
        severity=NotificationSeverity.INFO,
        source="ingestion",
        metadata=event.payload,
    )


def _on_job_closing_soon(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    job_title = event.payload.get("job_title", "A job")
    employer_name = event.payload.get("employer_name", "an employer")
    closing_date = event.payload.get("closing_date", "soon")
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.JOB_CLOSING_SOON,
        title="Job closing soon",
        message=f"{job_title} at {employer_name} closes on {closing_date} — less than 48 hours away.",
        severity=NotificationSeverity.WARNING,
        source="ingestion",
        metadata=event.payload,
    )


def _on_daily_digest(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    stats = event.payload.get("stats", {})
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.DAILY_DIGEST,
        title="Your daily job digest",
        message="Here's what's new in your job search over the last day.",
        severity=NotificationSeverity.INFO,
        source="notifications",
        metadata=event.payload,
    )
    logger.debug("Daily digest created for user {}: {}", event.user_id, stats)


def _on_weekly_summary(event: Event, session: Session) -> None:
    if event.user_id is None:
        return
    stats = event.payload.get("stats", {})
    NotificationService(session).create(
        user_id=event.user_id,
        type=NotificationType.WEEKLY_SUMMARY,
        title="Your weekly job search summary",
        message="Here's what's new in your job search over the last week.",
        severity=NotificationSeverity.INFO,
        source="notifications",
        metadata=event.payload,
    )
    logger.debug("Weekly summary created for user {}: {}", event.user_id, stats)
