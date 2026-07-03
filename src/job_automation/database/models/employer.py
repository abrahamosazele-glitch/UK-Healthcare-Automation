"""The organization posting jobs (NHS Trust, care home group, agency, etc.)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.employer_activity_log_entry import EmployerActivityLogEntry
    from job_automation.database.models.employer_contact import EmployerContact
    from job_automation.database.models.employer_department import EmployerDepartment
    from job_automation.database.models.employer_profile import EmployerProfile
    from job_automation.database.models.interview_record import InterviewRecord
    from job_automation.database.models.job import Job


class Employer(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "employers"

    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    website: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Added for the Employer & Application CRM milestone. Plain String, not
    # `sa_enum(...)` — the canonical `EmployerType` enum lives in
    # `employer_crm.employer_crm_models`, same reasoning as
    # `SavedJob.pipeline_stage`.
    employer_type: Mapped[str | None] = mapped_column(String(30), index=True)

    jobs: Mapped[list["Job"]] = relationship(
        back_populates="employer", cascade="all, delete-orphan", passive_deletes=True
    )
    # Named `candidate_profiles`, not `employer_profiles` — `employer
    # .employer_profiles` reads as "the employers this employer has," which
    # is backwards; these are per-candidate CRM rows about this employer
    # (see `User.employer_profiles` for the naturally-worded direction of
    # this same relationship, mirroring `Job.saved_job_states`/
    # `User.saved_jobs`).
    candidate_profiles: Mapped[list["EmployerProfile"]] = relationship(
        back_populates="employer", cascade="all, delete-orphan", passive_deletes=True
    )
    departments: Mapped[list["EmployerDepartment"]] = relationship(
        back_populates="employer", cascade="all, delete-orphan", passive_deletes=True
    )
    contacts: Mapped[list["EmployerContact"]] = relationship(
        back_populates="employer", cascade="all, delete-orphan", passive_deletes=True
    )
    activity_log: Mapped[list["EmployerActivityLogEntry"]] = relationship(
        back_populates="employer", cascade="all, delete-orphan", passive_deletes=True
    )
    interviews: Mapped[list["InterviewRecord"]] = relationship(
        back_populates="employer", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Employer {self.name!r}>"
