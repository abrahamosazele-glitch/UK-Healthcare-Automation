"""
Value objects for the background scheduler subsystem.

Deliberately dependency-free — same design as `workflows.workflow_models`,
`ai.matching_models`, `documents.document_models`: pure dataclasses/enums,
no SQLAlchemy, no APScheduler imports. The canonical `TaskStatus` values
live here; the ORM side (`database.models.scheduler_task_run_record
.SchedulerTaskRunRecord`) stores `.value` as a plain string rather than
importing this enum, for the same reason `ApplicationWorkflowRecord`
doesn't import `WorkflowStatus`.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from job_automation.utils.helpers import utc_now

#: A task function takes the current-attempt `Session` and returns a small
#: JSON-serializable summary dict (e.g. `{"jobs_imported": 3}`), stored as
#: `SchedulerTaskRunRecord.result_summary`. Raises on failure — the
#: scheduler service's retry/locking/history-recording wraps every call,
#: no task function needs to handle that itself.
TaskFunc = Callable[[Session], dict]

#: Re-exported for `scheduler_repository.py`/`tasks/cleanup_old_logs.py`,
#: which already imported `utc_now` from this module — now promoted to
#: `utils.helpers` since the notifications subsystem needed the identical
#: naive-UTC helper. Kept as a re-export here rather than updating those
#: two call sites' imports, to avoid an unrelated diff in already-shipped
#: Background Scheduler milestone code.
__all__ = ["TaskDefinition", "TaskFunc", "TaskRunSummary", "TaskStatus", "utc_now"]


class TaskStatus(str, enum.Enum):
    #: Created, about to run — exists only for the brief window between
    #: "decided to run" and "actually started"; in practice every run this
    #: service creates goes straight to RUNNING (see scheduler_service.py).
    PENDING = "pending"
    #: Currently executing (including any in-progress retry attempt).
    RUNNING = "running"
    #: Completed without raising, on this attempt or a retry.
    SUCCESS = "success"
    #: Every attempt (including retries) raised; the last error is recorded.
    FAILED = "failed"
    #: Not run at all because the same task was already RUNNING — see
    #: `scheduler_service.py`'s per-task `threading.Lock`.
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TaskDefinition:
    """One registered background task — see `task_registry.TASK_REGISTRY`
    for every concrete task this project defines."""

    name: str
    description: str
    func: TaskFunc
    interval_seconds: int
    max_attempts: int = 3
    #: Added for the Job Ingestion Service milestone. When set (0-23), this
    #: task runs once daily at that hour (server local time) via a
    #: `CronTrigger`, instead of `interval_seconds`'s `IntervalTrigger` —
    #: "refresh every morning" means a fixed time of day, not "every N
    #: seconds since the app started." `interval_seconds` is still required
    #: even for a cron-scheduled task (used for the scheduler dashboard's
    #: display of "how often this runs"); `job_scheduler.py`'s
    #: `create_scheduler()` is the only place `daily_at_hour` actually
    #: changes behavior. `None` (every pre-existing task) preserves the
    #: original interval-only behavior exactly.
    daily_at_hour: int | None = None


@dataclass(frozen=True)
class TaskRunSummary:
    """A plain-dataclass snapshot of one `SchedulerTaskRunRecord` row,
    returned by `SchedulerService.run_task()` instead of the ORM object
    itself — the ORM row's session may already be closed by the time the
    caller inspects the result (see `scheduler_service.py`), so this
    captures every scalar field up front rather than risking a
    `DetachedInstanceError` on later attribute access."""

    id: uuid.UUID
    task_name: str
    status: TaskStatus
    triggered_by: str
    attempt: int
    max_attempts: int
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None
    result_summary: dict = field(default_factory=dict)
