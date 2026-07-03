"""
One recorded execution attempt of a background scheduler task
(`job_automation.scheduler`) — the persisted half of "add a task history
table" / "store job run status." Not user-scoped (unlike almost every
other table in this schema): a scheduled task like "import fixture jobs"
or "clean old logs" is a system-level operation, not something one
candidate owns.

`status`/`task_name` are plain `String` columns, not `sa_enum(...)` — same
reasoning as `ApplicationWorkflowRecord.status`/`GeneratedDocumentRecord
.document_type`: every file in `database/models/` only imports from
`database.*`, and the canonical `TaskStatus` enum lives in the domain layer
(`scheduler.scheduler_models`), not here.

`result_summary` is a JSON blob (small, per-task-defined dict, e.g.
`{"jobs_imported": 3}`) rather than dedicated columns, matching the
`JobMatch.analysis`/`GeneratedDocumentRecord.validation_issues` pattern —
its shape genuinely differs per task and isn't queried on individually.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin


class SchedulerTaskRunRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "scheduler_task_runs"

    task_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # "schedule" (fired by APScheduler's interval trigger) or "manual" (a
    # dashboard "Run now" click) — kept as a plain string, not a FK/enum,
    # since there's nothing else to normalize against.
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime())
    error_message: Mapped[str | None] = mapped_column(Text)
    result_summary: Mapped[dict | None] = mapped_column(JSON)

    def __repr__(self) -> str:
        return f"<SchedulerTaskRunRecord {self.task_name} status={self.status}>"
