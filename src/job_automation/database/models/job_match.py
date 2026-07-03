"""
A computed relevance match between one job and one user (e.g. an AI/keyword
match score). `(job_id, user_id)` is unique so re-running matching updates the
existing score instead of accumulating duplicate rows.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.enums import JobMatchStatus, sa_enum
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application_workflow_record import (
        ApplicationWorkflowRecord,
    )
    from job_automation.database.models.job import Job
    from job_automation.database.models.user import User


class JobMatch(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint("job_id", "user_id", name="uq_job_matches_job_id_user_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    match_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, index=True)
    matched_keywords: Mapped[str | None] = mapped_column(Text)
    status: Mapped[JobMatchStatus] = mapped_column(
        sa_enum(JobMatchStatus), default=JobMatchStatus.NEW, nullable=False, index=True
    )

    # Added for the AI matching engine milestone. The rich per-category
    # breakdown (confidence, strengths, weaknesses, missing_requirements,
    # recommended_actions, per-category scores) is naturally nested and the
    # exact category set may evolve, so it's stored as one JSON blob rather
    # than ~8 additional scalar columns — `match_score` above stays the
    # single overall 0-100 figure for fast querying/sorting/filtering.
    analysis: Mapped[dict | None] = mapped_column(JSON)

    job: Mapped["Job"] = relationship(back_populates="job_matches")
    user: Mapped["User"] = relationship(back_populates="job_matches")
    # No cascade: job_match_id uses ON DELETE SET NULL — a workflow can
    # outlive the JobMatch that started it (e.g. after matching is re-run).
    application_workflows: Mapped[list["ApplicationWorkflowRecord"]] = relationship(
        back_populates="job_match"
    )

    def __repr__(self) -> str:
        return f"<JobMatch job={self.job_id} user={self.user_id} score={self.match_score}>"
