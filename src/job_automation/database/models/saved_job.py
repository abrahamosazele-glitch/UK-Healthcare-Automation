"""
One candidate's personal organization/tracking state for one job:
save/favourite/hide/archive flags, the personal Kanban pipeline stage, and
free-form tracking data (notes, rating, priority, deadline, interview
date, tags, checklist). `(job_id, user_id)` is unique — a candidate has at
most one tracking row per job, created on first save/favourite/hide/stage
change (see `job_organization.saved_job_repository.SavedJobRepository
.get_or_create()`).

Deliberately separate from `JobMatch` (an AI-computed relevance score —
a job can be saved/tracked without ever having been matched) and from
`ApplicationWorkflowRecord` (the document-generation-and-review state
machine — this table's `pipeline_stage` is a simpler, personal-tracking
concept with no document-review gate; see
`job_organization.job_organization_models`'s module docstring for the
full reasoning and docs/JOB_MANAGEMENT.md for the milestone-level
explanation).

`pipeline_stage`, `priority` are plain `String` columns, not
`sa_enum(...)` — same reasoning as `ApplicationWorkflowRecord.status`:
every file in `database/models/` only imports from `database.*`, and the
canonical `PipelineStage`/`JobPriority` enums live in the domain layer
(`job_organization.job_organization_models`), not here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.job import Job
    from job_automation.database.models.job_reminder import JobReminder
    from job_automation.database.models.user import User


class SavedJob(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "saved_jobs"
    __table_args__ = (
        UniqueConstraint("job_id", "user_id", name="uq_saved_jobs_job_id_user_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- Organization flags ---
    is_saved: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    # --- Kanban pipeline ---
    pipeline_stage: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # --- Personal tracking data ---
    notes: Mapped[str | None] = mapped_column(Text)
    personal_rating: Mapped[int | None] = mapped_column(Integer)  # 1-5, validated in the service layer
    priority: Mapped[str | None] = mapped_column(String(20))
    deadline: Mapped[date | None] = mapped_column(Date)
    interview_date: Mapped[datetime | None] = mapped_column(DateTime())
    # Small, per-job, user-authored lists — JSON blobs rather than child
    # tables, matching the project's established "JSON for an evolving,
    # per-parent-row blob" pattern (e.g. `Job.requirements`,
    # `JobMatch.analysis`) rather than the "own table" pattern reserved for
    # append-only, independently-queried history (e.g.
    # `WorkflowStatusHistoryRecord`). Neither tags nor checklist items are
    # ever queried independently of their parent `SavedJob`.
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    checklist: Mapped[list[dict] | None] = mapped_column(JSON)  # [{"label": str, "done": bool}, ...]

    job: Mapped["Job"] = relationship(back_populates="saved_job_states")
    user: Mapped["User"] = relationship(back_populates="saved_jobs")
    reminders: Mapped[list["JobReminder"]] = relationship(
        back_populates="saved_job", cascade="all, delete-orphan", passive_deletes=True,
        order_by="JobReminder.remind_at",
    )

    def __repr__(self) -> str:
        return f"<SavedJob job={self.job_id} user={self.user_id} stage={self.pipeline_stage}>"
