"""
Persists `SchedulerTaskRunRecord` rows. Pure data access — no locking, no
retry, no decision about *when* to run a task (that's entirely
`scheduler_service.py`'s job), following the same repository pattern as
every other repository in this project.

Uses `scheduler_models.utc_now()` (naive UTC) for every timestamp it
writes — see that function's docstring for why a timezone-aware one would
break comparisons against SQLite-stored values.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from job_automation.database.models.scheduler_task_run_record import SchedulerTaskRunRecord
from job_automation.scheduler.scheduler_models import TaskStatus, utc_now


class SchedulerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, task_name: str, *, status: TaskStatus, triggered_by: str, max_attempts: int, attempt: int = 1
    ) -> SchedulerTaskRunRecord:
        run = SchedulerTaskRunRecord(
            task_name=task_name,
            status=status.value,
            triggered_by=triggered_by,
            attempt=attempt,
            max_attempts=max_attempts,
            started_at=utc_now(),
        )
        self._session.add(run)
        self._session.flush()
        return run

    def mark_success(self, run: SchedulerTaskRunRecord, *, attempt: int, result_summary: dict) -> SchedulerTaskRunRecord:
        run.status = TaskStatus.SUCCESS.value
        run.attempt = attempt
        run.finished_at = utc_now()
        run.result_summary = result_summary
        self._session.flush()
        return run

    def mark_failed(self, run: SchedulerTaskRunRecord, *, attempt: int, error_message: str) -> SchedulerTaskRunRecord:
        run.status = TaskStatus.FAILED.value
        run.attempt = attempt
        run.finished_at = utc_now()
        run.error_message = error_message
        self._session.flush()
        return run

    def create_skipped(self, task_name: str, *, triggered_by: str, reason: str) -> SchedulerTaskRunRecord:
        """A task that was never actually run because it was already
        RUNNING elsewhere (see `scheduler_service.py`'s locking) — recorded
        as already-finished immediately (`started_at == finished_at`),
        since nothing executed in between."""
        now = utc_now()
        run = SchedulerTaskRunRecord(
            task_name=task_name,
            status=TaskStatus.SKIPPED.value,
            triggered_by=triggered_by,
            attempt=0,
            max_attempts=0,
            started_at=now,
            finished_at=now,
            error_message=reason,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get(self, run_id: uuid.UUID) -> SchedulerTaskRunRecord | None:
        return self._session.get(SchedulerTaskRunRecord, run_id)

    def list_recent(self, *, task_name: str | None = None, limit: int = 50) -> list[SchedulerTaskRunRecord]:
        stmt = select(SchedulerTaskRunRecord).order_by(SchedulerTaskRunRecord.started_at.desc()).limit(limit)
        if task_name is not None:
            stmt = stmt.where(SchedulerTaskRunRecord.task_name == task_name)
        return list(self._session.scalars(stmt))

    def latest_per_task(self) -> dict[str, SchedulerTaskRunRecord]:
        """The single most recent run for every task that has ever run at
        least once — used by the dashboard's task list to show "last run"
        status without loading full history."""
        latest: dict[str, SchedulerTaskRunRecord] = {}
        for run in self._session.scalars(
            select(SchedulerTaskRunRecord).order_by(SchedulerTaskRunRecord.started_at.desc())
        ):
            latest.setdefault(run.task_name, run)
        return latest

    def delete_older_than(self, cutoff: datetime) -> int:
        """Deletes finished (non-RUNNING) runs older than `cutoff`. Never
        deletes a row still mid-flight — a RUNNING row with no
        `finished_at` is left alone regardless of `started_at`, since a
        genuinely stuck/crashed run is a problem to notice, not silently
        erase."""
        result = self._session.execute(
            delete(SchedulerTaskRunRecord).where(
                SchedulerTaskRunRecord.finished_at.is_not(None),
                SchedulerTaskRunRecord.finished_at < cutoff,
            )
        )
        self._session.flush()
        return result.rowcount or 0
