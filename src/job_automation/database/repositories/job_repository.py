"""
Data access for Job rows.

`find_existing()` is the duplicate-detection query: a match on
`(source_site, external_id)` (the reference number) OR an exact `url` match
counts as the same listing. Deciding what to do with that result (insert vs.
update, mapping parsed fields onto columns) is business logic that belongs
in `database.services.job_ingestion_service`, not here — this class only
knows how to find, create, and update rows.

`JobFilter`/`search()` (added for the web dashboard milestone, extended for
the Job Management milestone) are a purely additive extension — the
dashboard's Jobs page needs filtering/sorting that nothing before it
required. Nothing about the existing methods changed.

The Job Management milestone's extension adds `user_id`-scoped filters
(saved/favourite/archived/pipeline stage) by `LEFT OUTER JOIN`-ing
`SavedJob` only when `filters.user_id` is set — every pre-existing filter
still works with `user_id=None` exactly as before, and hidden jobs are
excluded by default (that's what "hide" means) whenever a user is scoped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from job_automation.database.models.employer import Employer
from job_automation.database.models.job import Job
from job_automation.database.models.saved_job import SavedJob

_SORTABLE_COLUMNS = {
    "posted_date": Job.posted_date,
    "closing_date": Job.closing_date,
    "salary": Job.salary_min,
    "title": Job.title,
    "created_at": Job.created_at,
    # Added for the Job Management milestone's "Employer A-Z"/"Band" sort options.
    "employer": Employer.name,
    "band": Job.band,
}

_CLOSING_SOON_WINDOW_DAYS = 7


@dataclass(frozen=True)
class JobFilter:
    search: str | None = None
    location: str | None = None
    min_salary: float | None = None
    band: str | None = None
    employer_name: str | None = None
    visa_sponsorship: bool | None = None
    employment_type: str | None = None  # matched against contract_type or working_pattern
    sort_by: str = "posted_date"
    sort_descending: bool = True

    # --- Added for the Job Management milestone ---
    max_salary: float | None = None
    remote: bool | None = None
    closing_soon: bool | None = None  # closing within _CLOSING_SOON_WINDOW_DAYS and still active
    expired: bool | None = None  # closing_date passed, or no longer active
    #: A synonym search field for the dashboard's dedicated "Keywords" box
    #: — functionally identical to `search` today (both OR-match against
    #: title/description). Kept as a separate field since the milestone's
    #: spec lists "Search by ... Keywords" as distinct from a general
    #: search box; a future version could differentiate them (e.g.
    #: matching `keywords` against `Job.requirements`/`Job.benefits`
    #: instead), which is why this isn't just folded into `search`.
    keywords: str | None = None

    #: The user these saved/favourite/archived/pipeline-stage filters are
    #: scoped to. `None` (the default) means "don't join `SavedJob` at
    #: all" — every filter below this line is a no-op without it.
    user_id: uuid.UUID | None = None
    saved_only: bool = False
    favourite_only: bool = False
    archived_only: bool = False  # the "Restore archived jobs" view — shows ONLY archived jobs
    pipeline_stage: str | None = None

    #: Added for the Job Ingestion Service milestone — filters by
    #: `Job.source_site` (e.g. "nhs_jobs", "trac_jobs", "reed"), an exact
    #: match rather than a substring search: source names are a fixed,
    #: known set (`ingestion.PROVIDER_REGISTRY`), not free text.
    source: str | None = None


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_existing(self, *, source_site: str, external_id: str | None, url: str | None) -> Job | None:
        """Match on (source_site, external_id) or on url — either is
        sufficient to identify "the same listing seen again"."""
        conditions = []
        if external_id:
            conditions.append((Job.source_site == source_site) & (Job.external_id == external_id))
        if url:
            conditions.append(Job.url == url)
        if not conditions:
            return None
        return self._session.scalars(select(Job).where(or_(*conditions))).first()

    def find_by_content_hash(self, content_hash: str) -> Job | None:
        """Cross-source duplicate detection: the same role, re-posted under
        a different (source_site, external_id) — e.g. an NHS trust vacancy
        that also appears on Trac Jobs or Reed with its own listing ID.
        `find_existing()` alone can't catch this (it only matches within
        the same identity scheme); this is the second, source-independent
        key `Job.content_hash`'s docstring has always described. Returns
        the oldest match (`created_at` ascending) so repeated imports keep
        converging on the same canonical row rather than drifting to
        whichever source happened to be scraped most recently."""
        return self._session.scalars(
            select(Job).where(Job.content_hash == content_hash).order_by(Job.created_at.asc())
        ).first()

    def get(self, job_id: uuid.UUID) -> Job | None:
        return self._session.get(Job, job_id)

    def list_active(self) -> list[Job]:
        """Every currently-active job — the input set for the AI matching
        engine's `MatchingService.evaluate_all_active_jobs()`."""
        return list(self._session.scalars(select(Job).where(Job.is_active.is_(True))))

    def list_distinct_sources(self) -> list[str]:
        """Every `source_site` value actually present in the database,
        sorted — used to populate the Jobs page's "Source" filter dropdown
        with what's real rather than the full configured provider list
        (which may include a provider that hasn't imported anything yet)."""
        return sorted(self._session.scalars(select(Job.source_site).distinct()))

    def create(self, **fields: Any) -> Job:
        job = Job(**fields)
        self._session.add(job)
        self._session.flush()
        return job

    def update(self, job: Job, **fields: Any) -> Job:
        for key, value in fields.items():
            setattr(job, key, value)
        self._session.flush()
        return job

    def search(self, filters: JobFilter) -> list[Job]:
        """Filtered, sorted job listing for the dashboard's Jobs page. Every
        filter is optional — an unset field simply isn't applied."""
        stmt = select(Job).join(Employer, Employer.id == Job.employer_id)

        if filters.user_id is not None:
            stmt = stmt.outerjoin(
                SavedJob, (SavedJob.job_id == Job.id) & (SavedJob.user_id == filters.user_id)
            )
            # Hidden jobs are excluded from every user-scoped view — that's
            # what "hide" means — unless the caller explicitly wants only
            # archived jobs (the "Restore" view), where hidden-ness is
            # irrelevant to what's being shown.
            if filters.archived_only:
                stmt = stmt.where(SavedJob.is_archived.is_(True))
            else:
                stmt = stmt.where(or_(SavedJob.is_hidden.is_(False), SavedJob.is_hidden.is_(None)))
                stmt = stmt.where(or_(SavedJob.is_archived.is_(False), SavedJob.is_archived.is_(None)))
            if filters.saved_only:
                stmt = stmt.where(SavedJob.is_saved.is_(True))
            if filters.favourite_only:
                stmt = stmt.where(SavedJob.is_favourite.is_(True))
            if filters.pipeline_stage:
                stmt = stmt.where(SavedJob.pipeline_stage == filters.pipeline_stage)

        if filters.search:
            like = f"%{filters.search}%"
            stmt = stmt.where(or_(Job.title.ilike(like), Job.description.ilike(like)))
        if filters.keywords:
            like = f"%{filters.keywords}%"
            stmt = stmt.where(or_(Job.title.ilike(like), Job.description.ilike(like)))
        if filters.location:
            stmt = stmt.where(Job.location.ilike(f"%{filters.location}%"))
        if filters.min_salary is not None:
            stmt = stmt.where(or_(Job.salary_max >= filters.min_salary, Job.salary_min >= filters.min_salary))
        if filters.max_salary is not None:
            stmt = stmt.where(or_(Job.salary_min <= filters.max_salary, Job.salary_max <= filters.max_salary))
        if filters.band:
            stmt = stmt.where(Job.band == filters.band)
        if filters.employer_name:
            stmt = stmt.where(Employer.name.ilike(f"%{filters.employer_name}%"))
        if filters.visa_sponsorship is not None:
            stmt = stmt.where(Job.visa_sponsorship == filters.visa_sponsorship)
        if filters.employment_type:
            like = f"%{filters.employment_type}%"
            stmt = stmt.where(or_(Job.contract_type.ilike(like), Job.working_pattern.ilike(like)))
        if filters.source:
            stmt = stmt.where(Job.source_site == filters.source)
        if filters.remote is not None:
            remote_clause = or_(Job.working_pattern.ilike("%remote%"), Job.location.ilike("%remote%"))
            stmt = stmt.where(remote_clause if filters.remote else ~remote_clause)
        if filters.closing_soon:
            today = date.today()
            stmt = stmt.where(
                Job.is_active.is_(True),
                Job.closing_date.is_not(None),
                Job.closing_date >= today,
                Job.closing_date <= today + timedelta(days=_CLOSING_SOON_WINDOW_DAYS),
            )
        if filters.expired:
            today = date.today()
            stmt = stmt.where(or_(Job.is_active.is_(False), Job.closing_date < today))

        sort_column = _SORTABLE_COLUMNS.get(filters.sort_by, Job.posted_date)
        stmt = stmt.order_by(sort_column.desc() if filters.sort_descending else sort_column.asc())

        return list(self._session.scalars(stmt))
