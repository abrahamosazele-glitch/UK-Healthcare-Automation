"""
One application by one user to one job — the central record tying
job, user, CV, and cover letter together.

`(job_id, user_id)` is unique: a user can only have one application per job
(re-applying updates the existing row's status instead of creating a new
one). `cv_id`/`cover_letter_id` are nullable with `ON DELETE SET NULL`: if
the underlying document is deleted, the application record itself should
survive as history, just without a document attached. `job_id` has no
`ON DELETE` cascade/rule, so a `Job` can't be deleted while applications
reference it — application history should outlive the listing itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.enums import ApplicationStatus, sa_enum
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.cover_letter import CoverLetter
    from job_automation.database.models.cv import CV
    from job_automation.database.models.interview import Interview
    from job_automation.database.models.job import Job
    from job_automation.database.models.user import User


class Application(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("job_id", "user_id", name="uq_applications_job_id_user_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cv_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cvs.id", ondelete="SET NULL"), index=True
    )
    cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cover_letters.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        sa_enum(ApplicationStatus), default=ApplicationStatus.DRAFT, nullable=False, index=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    job: Mapped["Job"] = relationship(back_populates="applications")
    user: Mapped["User"] = relationship(back_populates="applications")
    cv: Mapped["CV | None"] = relationship(back_populates="applications")
    cover_letter: Mapped["CoverLetter | None"] = relationship(back_populates="applications")
    interviews: Mapped[list["Interview"]] = relationship(
        back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Application job={self.job_id} user={self.user_id} status={self.status}>"
