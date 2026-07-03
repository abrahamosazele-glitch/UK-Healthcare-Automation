"""
The main entry point for the background scheduler subsystem: runs one
named task with locking, retries, and full status-history recording — the
same orchestrator role `WorkflowService`/`DocumentService`/`MatchingService`
play for their own subsystems.

Both the periodic APScheduler trigger (`job_scheduler.py`) and the
dashboard's manual "Run now" button call the exact same
`run_task()` method, on the exact same `SchedulerService` instance
(`scheduler_service`, the module-level singleton `web/app.py` wires up) —
so a scheduled fire and a manual click can never both run the same task at
the same time, and both are recorded in the identical history table.

**Publishes events, never calls `NotificationService` directly** (added in
the Notification & Event System milestone) — `SCHEDULER_TASK_STARTED`
right after a run is recorded, `SCHEDULER_TASK_FINISHED` after success or
failure, and `ERROR_OCCURRED` specifically on failure. See
`notifications.events`'s module docstring for why this stays decoupled
from notification creation.
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from typing import Callable

from sqlalchemy.orm import Session

from job_automation.core.retry_manager import RetryManager
from job_automation.database.db_manager import get_session
from job_automation.database.models.scheduler_task_run_record import SchedulerTaskRunRecord
from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.scheduler.scheduler_models import TaskDefinition, TaskRunSummary, TaskStatus
from job_automation.scheduler.scheduler_repository import SchedulerRepository
from job_automation.scheduler.task_registry import TASK_REGISTRY
from job_automation.utils.logger import logger

#: Matches `db_manager.get_session`'s shape: a zero-arg callable returning a
#: context manager that yields a `Session`. Overridden in tests with a
#: factory pointing at an in-memory database — see tests/test_scheduler.py.
SessionFactory = Callable[[], AbstractContextManager[Session]]


class SchedulerService:
    def __init__(
        self,
        *,
        session_factory: SessionFactory = get_session,
        task_registry: dict[str, TaskDefinition] | None = None,
        retry_manager_factory: Callable[[int], RetryManager] | None = None,
        event_bus: EventBus = event_bus,
    ) -> None:
        self._session_factory = session_factory
        self._tasks = task_registry if task_registry is not None else TASK_REGISTRY
        self._retry_manager_factory = retry_manager_factory or self._default_retry_manager
        self._event_bus = event_bus
        # One lock per task name — "the same task cannot run twice at the
        # same time." In-process only (a `threading.Lock`, not a DB-level
        # lock): this app is a single Python process with one scheduler
        # instance, so this correctly prevents the only real race that can
        # happen here — a scheduled fire overlapping a manual "Run now"
        # click, or two manual clicks in quick succession.
        self._locks: dict[str, threading.Lock] = {name: threading.Lock() for name in self._tasks}

    @staticmethod
    def _default_retry_manager(max_attempts: int) -> RetryManager:
        from job_automation.config.settings import settings

        return RetryManager(
            max_retries=max_attempts,
            base_delay_seconds=settings.scheduler_task_retry_base_delay_seconds,
            max_delay_seconds=settings.scheduler_task_retry_max_delay_seconds,
        )

    def list_task_definitions(self) -> list[TaskDefinition]:
        return list(self._tasks.values())

    def run_task(self, task_name: str, *, triggered_by: str = "manual") -> TaskRunSummary:
        if task_name not in self._tasks:
            raise KeyError(f"Unknown scheduler task: {task_name!r}")

        task_def = self._tasks[task_name]
        lock = self._locks[task_name]

        if not lock.acquire(blocking=False):
            logger.warning("Skipping {!r}: already running", task_name)
            with self._session_factory() as session:
                repo = SchedulerRepository(session)
                run = repo.create_skipped(task_name, triggered_by=triggered_by, reason="Task already running")
                session.commit()
                return _to_summary(run)

        try:
            return self._run_locked(task_def, triggered_by=triggered_by)
        finally:
            lock.release()

    def _run_locked(self, task_def: TaskDefinition, *, triggered_by: str) -> TaskRunSummary:
        with self._session_factory() as session:
            repo = SchedulerRepository(session)
            run = repo.create(
                task_def.name, status=TaskStatus.RUNNING, triggered_by=triggered_by, max_attempts=task_def.max_attempts
            )
            session.commit()
            logger.info("Task {!r} started (run {}, triggered by {})", task_def.name, run.id, triggered_by)
            self._event_bus.publish(
                Event(
                    event_type=EventType.SCHEDULER_TASK_STARTED,
                    payload={"task_name": task_def.name, "triggered_by": triggered_by},
                ),
                session,
            )

            attempts = {"count": 0}

            def _attempt() -> dict:
                attempts["count"] += 1
                try:
                    return task_def.func(session)
                except Exception:
                    session.rollback()
                    raise

            retry_manager = self._retry_manager_factory(task_def.max_attempts)
            try:
                result = retry_manager.execute(_attempt, operation_name=task_def.name, retry_on=(Exception,))
            except Exception as exc:
                error_message = _format_error(exc)
                logger.error("Task {!r} failed after {} attempt(s): {}", task_def.name, attempts["count"], error_message)
                repo.mark_failed(run, attempt=attempts["count"], error_message=error_message)
                self._event_bus.publish(
                    Event(
                        event_type=EventType.ERROR_OCCURRED,
                        payload={"source": "scheduler", "task_name": task_def.name, "error_message": error_message},
                    ),
                    session,
                )
                self._event_bus.publish(
                    Event(
                        event_type=EventType.SCHEDULER_TASK_FINISHED,
                        payload={"task_name": task_def.name, "status": "failed", "error_message": error_message},
                    ),
                    session,
                )
            else:
                logger.info("Task {!r} succeeded after {} attempt(s): {}", task_def.name, attempts["count"], result)
                repo.mark_success(run, attempt=attempts["count"], result_summary=result)
                self._event_bus.publish(
                    Event(
                        event_type=EventType.SCHEDULER_TASK_FINISHED,
                        payload={"task_name": task_def.name, "status": "success", "result_summary": result},
                    ),
                    session,
                )

            session.commit()
            return _to_summary(run)

    def get_history(self, *, task_name: str | None = None, limit: int = 50) -> list[TaskRunSummary]:
        with self._session_factory() as session:
            runs = SchedulerRepository(session).list_recent(task_name=task_name, limit=limit)
            return [_to_summary(run) for run in runs]

    def get_latest_per_task(self) -> dict[str, TaskRunSummary]:
        with self._session_factory() as session:
            latest = SchedulerRepository(session).latest_per_task()
            return {name: _to_summary(run) for name, run in latest.items()}


def _format_error(exc: Exception) -> str:
    if exc.__cause__ is not None:
        return f"{exc}: {exc.__cause__}"
    return str(exc)


def _to_summary(run: SchedulerTaskRunRecord) -> TaskRunSummary:
    return TaskRunSummary(
        id=run.id,
        task_name=run.task_name,
        status=TaskStatus(run.status),
        triggered_by=run.triggered_by,
        attempt=run.attempt,
        max_attempts=run.max_attempts,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_message=run.error_message,
        result_summary=run.result_summary or {},
    )
