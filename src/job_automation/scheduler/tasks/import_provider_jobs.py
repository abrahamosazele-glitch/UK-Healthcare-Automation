"""
Scheduled task: refresh jobs from every configured real provider (NHS Jobs,
Trac Jobs, Reed by default — see `settings.job_ingestion_providers`), then
automatically AI-match every newly-created listing and publish the
appropriate notifications.

Runs once daily (see `task_registry.TASK_REGISTRY`'s `daily_at_hour` for
this task, and `scheduler.job_scheduler.create_scheduler()`'s `CronTrigger`
branch), not on a fixed interval — "refresh every morning" means a
predictable time of day. The dashboard's manual "Run now" button still
works regardless, calling this exact same function through
`SchedulerService.run_task()`.

Publishes one `JOB_IMPORTED` event summarizing every provider's totals
combined (never calls `NotificationService` directly — see
`notifications.events`'s module docstring), the same event
`import_fixture_jobs` already publishes — the dashboard's "recent
activity"/notification feed doesn't need to know which of the two tasks
produced it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.ingestion.auto_match_service import process_new_jobs
from job_automation.ingestion.ingestion_orchestrator import run_ingestion
from job_automation.notifications.event_bus import event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.utils.logger import logger


def run(session: Session) -> dict:
    result = run_ingestion(session)
    session.flush()

    match_summary = process_new_jobs(session, result.newly_created_job_ids)

    summary = result.to_summary_dict()
    summary.update(match_summary)
    logger.info("import_provider_jobs: {}", summary)

    event_bus.publish(
        Event(
            event_type=EventType.JOB_IMPORTED,
            payload={"jobs_created": result.jobs_created, "jobs_updated": result.jobs_updated, **summary},
        ),
        session,
    )
    return summary
