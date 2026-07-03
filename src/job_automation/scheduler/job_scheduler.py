"""
APScheduler `BackgroundScheduler` bootstrap.

Wires every entry in `task_registry.TASK_REGISTRY` to an interval trigger
that calls `scheduler_service.run_task(name, triggered_by="schedule")` — the
exact same method, on the exact same `SchedulerService` singleton, that
`web/routes/scheduler.py`'s manual "Run now" button calls. This is what
makes the per-task locking meaningful: a periodic fire and a manual click
can never race each other into running the same task twice, because they
share one lock per task name.

Only actually starts if `settings.scheduler_enabled` is true (default
`False`) — importing this module, or even calling `create_scheduler()`,
never starts a background thread by itself; only `start()` does. This
keeps `pytest` (which imports the whole `web.app` module tree) from ever
silently spinning up a real background scheduler thread.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from job_automation.config.settings import settings
from job_automation.scheduler.scheduler_service import SchedulerService
from job_automation.scheduler.task_registry import TASK_REGISTRY
from job_automation.utils.logger import logger


def create_scheduler(scheduler_service: SchedulerService) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    for task_def in TASK_REGISTRY.values():
        trigger = (
            CronTrigger(hour=task_def.daily_at_hour, minute=0)
            if task_def.daily_at_hour is not None
            else IntervalTrigger(seconds=task_def.interval_seconds)
        )
        scheduler.add_job(
            scheduler_service.run_task,
            trigger=trigger,
            kwargs={"task_name": task_def.name, "triggered_by": "schedule"},
            id=task_def.name,
            replace_existing=True,
            max_instances=1,  # belt-and-braces alongside SchedulerService's own per-task lock
            coalesce=True,  # if runs were missed (e.g. app was down), fire once on resume, not once per missed interval
        )
    return scheduler


def start_if_enabled(scheduler_service: SchedulerService) -> BackgroundScheduler | None:
    if not settings.scheduler_enabled:
        logger.info("Background scheduler disabled (SCHEDULER_ENABLED=false) — not starting.")
        return None
    scheduler = create_scheduler(scheduler_service)
    scheduler.start()
    logger.info("Background scheduler started with {} task(s).", len(TASK_REGISTRY))
    return scheduler
