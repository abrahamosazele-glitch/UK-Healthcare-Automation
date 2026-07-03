"""
The scheduled tasks this app runs, registered by name. `SchedulerService`,
the dashboard, and `job_scheduler.py`'s APScheduler bootstrap all
iterate/look up tasks through this one dict rather than each hard-coding
the list separately.

`send_due_reminders` was added for the Job Management milestone;
`send_due_interview_reminders` for the Interview & Calendar Management
milestone — everything else is from the Background Scheduler milestone.
"""

from __future__ import annotations

from job_automation.config.settings import settings
from job_automation.scheduler.scheduler_models import TaskDefinition
from job_automation.scheduler.tasks import (
    check_closing_soon_jobs,
    cleanup_old_logs,
    generate_draft_documents,
    import_fixture_jobs,
    import_provider_jobs,
    run_ai_matching,
    send_daily_digest,
    send_due_interview_reminders,
    send_due_reminders,
    send_pending_emails,
    send_weekly_summary,
    update_workflow_statuses,
)

TASK_REGISTRY: dict[str, TaskDefinition] = {
    "import_fixture_jobs": TaskDefinition(
        name="import_fixture_jobs",
        description="Import job listings from a local JSON fixture file (never a live website).",
        func=import_fixture_jobs.run,
        interval_seconds=settings.scheduler_import_jobs_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "run_ai_matching": TaskDefinition(
        name="run_ai_matching",
        description="Evaluate every active job against every user's candidate profile, using a FakeLLMProvider.",
        func=run_ai_matching.run,
        interval_seconds=settings.scheduler_ai_matching_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "generate_draft_documents": TaskDefinition(
        name="generate_draft_documents",
        description="Draft a supporting statement for strong matches that don't already have one.",
        func=generate_draft_documents.run,
        interval_seconds=settings.scheduler_generate_documents_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "update_workflow_statuses": TaskDefinition(
        name="update_workflow_statuses",
        description="Ensure every job match has a workflow record (never advances a workflow beyond creation).",
        func=update_workflow_statuses.run,
        interval_seconds=settings.scheduler_update_workflows_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "cleanup_old_logs": TaskDefinition(
        name="cleanup_old_logs",
        description="Delete scheduler task-run history older than the configured retention period.",
        func=cleanup_old_logs.run,
        interval_seconds=settings.scheduler_cleanup_logs_interval_seconds,
        max_attempts=1,
    ),
    "send_due_reminders": TaskDefinition(
        name="send_due_reminders",
        description="Publish a REMINDER_DUE notification for every job reminder whose time has come.",
        func=send_due_reminders.run,
        interval_seconds=settings.scheduler_reminders_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "send_due_interview_reminders": TaskDefinition(
        name="send_due_interview_reminders",
        description="Publish an INTERVIEW_REMINDER_DUE notification for every interview reminder whose time has come.",
        func=send_due_interview_reminders.run,
        interval_seconds=settings.scheduler_interview_reminders_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "import_provider_jobs": TaskDefinition(
        name="import_provider_jobs",
        description="Refresh jobs from every configured real provider (NHS Jobs, Trac Jobs, Reed), auto-match new listings, and notify.",
        func=import_provider_jobs.run,
        # Only used for the scheduler dashboard's "how often" display —
        # `daily_at_hour` below is what job_scheduler.py actually uses.
        interval_seconds=60 * 60 * 24,
        daily_at_hour=settings.scheduler_job_ingestion_hour,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "check_closing_soon_jobs": TaskDefinition(
        name="check_closing_soon_jobs",
        description="Notify matched users when a job is newly within the closing-soon window.",
        func=check_closing_soon_jobs.run,
        interval_seconds=settings.scheduler_closing_soon_check_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "send_pending_emails": TaskDefinition(
        name="send_pending_emails",
        description="Flush the queued-email outbox via SMTP (the async half of email notification delivery).",
        func=send_pending_emails.run,
        interval_seconds=settings.scheduler_send_emails_interval_seconds,
        max_attempts=1,  # send_pending_emails.py already retries each row up to its own MAX_ATTEMPTS
    ),
    "send_daily_digest": TaskDefinition(
        name="send_daily_digest",
        description="Send each user their daily job-search digest at their configured hour.",
        func=send_daily_digest.run,
        interval_seconds=settings.scheduler_digest_check_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
    "send_weekly_summary": TaskDefinition(
        name="send_weekly_summary",
        description="Send each user their weekly job-search summary on Mondays, at their configured hour.",
        func=send_weekly_summary.run,
        interval_seconds=settings.scheduler_digest_check_interval_seconds,
        max_attempts=settings.scheduler_task_max_attempts,
    ),
}
