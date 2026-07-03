"""
A single job listing, scraped from one source site.

`content_hash` and the `(source_site, external_id)` unique constraint are the
two keys the (not-yet-built) deduplication module will use: an exact match on
`(source_site, external_id)` catches the same listing seen twice, while
`content_hash` (a hash of normalized title+employer+location) is meant to
catch the same role re-posted under a new listing ID.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.enums import JobType, sa_enum
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application import Application
    from job_automation.database.models.application_workflow_record import (
        ApplicationWorkflowRecord,
    )
    from job_automation.database.models.employer import Employer
    from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
    from job_automation.database.models.job_match import JobMatch
    from job_automation.database.models.saved_job import SavedJob


class Job(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source_site", "external_id", name="uq_jobs_source_site_external_id"),
        Index("ix_jobs_title_location", "title", "location"),
    )

    employer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255), index=True)
    salary_min: Mapped[float | None] = mapped_column(Numeric(10, 2))
    salary_max: Mapped[float | None] = mapped_column(Numeric(10, 2))
    salary_period: Mapped[str | None] = mapped_column(String(20))
    salary_raw: Mapped[str | None] = mapped_column(String(255))
    job_type: Mapped[JobType | None] = mapped_column(sa_enum(JobType))
    source_site: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    posted_date: Mapped[datetime | None] = mapped_column(DateTime(), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # Fields added for the NHS Jobs scraper milestone — kept generic (not
    # NHS-specific columns) since any future site scraper may expose the
    # same information. `job_type` (the shared enum) doesn't map cleanly
    # onto NHS's two separate axes (contract type vs. working pattern), so
    # both are stored as free text rather than forced into that enum.
    band: Mapped[str | None] = mapped_column(String(50))
    contract_type: Mapped[str | None] = mapped_column(String(255))
    working_pattern: Mapped[str | None] = mapped_column(String(255))
    closing_date: Mapped[date | None] = mapped_column(Date, index=True)
    requirements: Mapped[list[str] | None] = mapped_column(JSON)
    benefits: Mapped[list[str] | None] = mapped_column(JSON)

    # Added for the AI matching engine milestone — ParsedJob (the scraper
    # framework's generic output type) has carried this field since the
    # DummyScraper milestone, but no scraper had wired it to a Job column
    # yet since NHS's own field list didn't include it. The matching
    # engine's "visa sponsorship" scoring category needs a job-side signal
    # to compare against the candidate's preference; None means "unknown",
    # not "no".
    visa_sponsorship: Mapped[bool | None] = mapped_column(Boolean)

    # Added for the Job Ingestion Service milestone. Tracks whether this
    # job has already triggered a "closing within 48 hours" notification
    # (scheduler.tasks.check_closing_soon_jobs) — without it, every hourly
    # scan would re-notify the same users about the same job again. `None`
    # means "not yet notified"; the field is otherwise never cleared, since
    # a job's closing date doesn't move backward.
    closing_soon_notified_at: Mapped[datetime | None] = mapped_column(DateTime())

    employer: Mapped["Employer"] = relationship(back_populates="jobs")
    applications: Mapped[list["Application"]] = relationship(back_populates="job")
    job_matches: Mapped[list["JobMatch"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    # No cascade: job_id uses ON DELETE SET NULL (see GeneratedDocumentRecord's
    # docstring) — a draft document outlives the job it was generated for.
    generated_documents: Mapped[list["GeneratedDocumentRecord"]] = relationship(back_populates="job")
    # No cascade — job_id has no ondelete (protective, like Application.job_id):
    # a Job can't be deleted while a workflow references it.
    application_workflows: Mapped[list["ApplicationWorkflowRecord"]] = relationship(back_populates="job")
    # Named `saved_job_states`, not `saved_jobs` — `job.saved_jobs` reads as
    # "the jobs this job saved," which is backwards; the per-user tracking
    # rows for this job are what's actually here (see `User.saved_jobs` for
    # the naturally-worded direction of this same relationship).
    saved_job_states: Mapped[list["SavedJob"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Job {self.title!r} @ {self.source_site}>"
