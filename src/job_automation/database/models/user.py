"""The candidate/account model — one row per person using the system."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.activity_log import ActivityLog
    from job_automation.database.models.application import Application
    from job_automation.database.models.application_workflow_record import (
        ApplicationWorkflowRecord,
    )
    from job_automation.database.models.candidate_profile_record import CandidateProfileRecord
    from job_automation.database.models.certificate import Certificate
    from job_automation.database.models.cv import CV
    from job_automation.database.models.employer_activity_log_entry import EmployerActivityLogEntry
    from job_automation.database.models.employer_contact import EmployerContact
    from job_automation.database.models.employer_profile import EmployerProfile
    from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
    from job_automation.database.models.interview_record import InterviewRecord
    from job_automation.database.models.job_alert import JobAlert
    from job_automation.database.models.job_match import JobMatch
    from job_automation.database.models.notification import Notification
    from job_automation.database.models.saved_job import SavedJob


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # bcrypt via passlib (job_automation.auth.password_hasher) — never a
    # plaintext password, never any other hashing scheme. Column, not a
    # separate table: a 1:1 credential is part of the account itself, not
    # an evolving/append-only concern like the workflow subsystem's history
    # tables.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    right_to_work_uk: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    cvs: Mapped[list["CV"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    applications: Mapped[list["Application"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    job_alerts: Mapped[list["JobAlert"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    activity_logs: Mapped[list["ActivityLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    job_matches: Mapped[list["JobMatch"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    candidate_profile: Mapped["CandidateProfileRecord | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    generated_documents: Mapped[list["GeneratedDocumentRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    application_workflows: Mapped[list["ApplicationWorkflowRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    saved_jobs: Mapped[list["SavedJob"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    employer_profiles: Mapped[list["EmployerProfile"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    employer_contacts: Mapped[list["EmployerContact"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    employer_activity_log: Mapped[list["EmployerActivityLogEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    interviews: Mapped[list["InterviewRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<User {self.email!r}>"
