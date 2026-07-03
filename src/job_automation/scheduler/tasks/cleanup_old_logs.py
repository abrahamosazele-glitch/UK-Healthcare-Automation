"""
Scheduled task: prune old `SchedulerTaskRunRecord` history rows.

This targets the scheduler's *own* task-run history table, not the
application's physical log files (`data/logs/app.log`) — those already
have their own rotation/retention entirely handled by loguru
(`config/logging_config.py`: `rotation="1 day", retention="14 days"`),
configured back in the Browser Framework milestone. Duplicating that here
would just be a second, competing retention policy for the same files.
What loguru doesn't (and can't) know about is `scheduler_task_runs` — a
table this milestone just introduced, which grows by one row every time
any task runs and would otherwise accumulate forever on a continuously
running scheduler. That's what "cleaning old logs" means concretely here.

Only ever deletes rows that have actually finished
(`SchedulerRepository.delete_older_than()` never touches a still-RUNNING
row, however old `started_at` is) — a stuck/crashed run stays visible
rather than being silently erased.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.scheduler.scheduler_models import utc_now
from job_automation.scheduler.scheduler_repository import SchedulerRepository
from job_automation.utils.logger import logger


def run(session: Session) -> dict:
    # Naive UTC, matching `SchedulerRepository`'s own `finished_at` values —
    # see `utc_now()`'s docstring for why (SQLite has no real
    # timezone-aware storage; comparing a naive column against an aware
    # cutoff raises `TypeError` during the bulk delete).
    cutoff = utc_now() - timedelta(days=settings.scheduler_log_retention_days)
    deleted = SchedulerRepository(session).delete_older_than(cutoff)
    logger.info("cleanup_old_logs: deleted {} scheduler task run(s) older than {}", deleted, cutoff.date())
    return {"deleted": deleted, "retention_days": settings.scheduler_log_retention_days}
