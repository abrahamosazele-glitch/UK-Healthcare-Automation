"""
Turns a scraper's `ParsedJob` into a persisted `Job` row, deduplicating
against what's already stored.

This depends on `job_automation.scrapers.base.ParsedJob` — a deliberate
upward dependency from the database layer onto the scraper framework's
generic output type. It's justified here because this service is meant to
be reused by *every* future scraper (NHS, TRAC, Indeed, Reed all produce the
same `ParsedJob` shape), not just NHS's; putting it under `database/`
keeps persistence logic in one place rather than duplicating an "insert or
update a Job" routine inside each site's scraper module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from job_automation.database.models.job import Job
from job_automation.database.repositories.employer_repository import EmployerRepository
from job_automation.database.repositories.job_repository import JobRepository
from job_automation.scrapers.base.base_parser import ParsedJob
from job_automation.utils.helpers import compute_content_hash, parse_salary_range
from job_automation.utils.logger import logger


@dataclass
class JobSaveResult:
    job: Job
    created: bool  # True = a new row was inserted, False = an existing row was updated


class JobIngestionService:
    def __init__(self, session: Session, *, source_site: str) -> None:
        self._session = session
        self._source_site = source_site
        self._jobs = JobRepository(session)
        self._employers = EmployerRepository(session)
        #: IDs of every Job newly inserted (not updated) by this service
        #: instance across however many `save_parsed_job()` calls it's made
        #: — read by `ingestion` package providers after a full scrape run
        #: so the auto-match/notification step only evaluates genuinely
        #: new listings, not re-imports of jobs already seen before.
        self.created_job_ids: list[uuid.UUID] = []

    def save_parsed_job(self, parsed: ParsedJob) -> JobSaveResult:
        """Insert a new Job, or update the existing one if a match is
        found — never creates a duplicate row for the same listing.

        Two independent ways to match an existing row, checked in order:
        1. `(source_site, external_id)` or `url` — the same listing seen
           again from *this* source (a re-run of the same provider).
        2. Failing that, `content_hash` (normalized title+employer+location)
           against *any* source — the same real-world role, re-posted
           under a different listing ID on a different job board. When
           this is what matches, the existing row's `source_site`/
           `external_id` are deliberately left untouched (whichever source
           discovered it first stays its identity); only its content
           fields are refreshed."""
        if not parsed.job_url:
            raise ValueError(f"Cannot persist {parsed.title!r}: ParsedJob has no job_url")

        employer = self._employers.get_or_create(
            parsed.employer or "Unknown employer", website=parsed.employer_url
        )
        external_id = parsed.reference_number or parsed.job_url
        content_hash = compute_content_hash(parsed.title, parsed.employer, parsed.location)

        existing = self._jobs.find_existing(
            source_site=self._source_site, external_id=external_id, url=parsed.job_url
        )
        matched_cross_source = existing is None
        if existing is None:
            existing = self._jobs.find_by_content_hash(content_hash)

        salary_min, salary_max, salary_period = parse_salary_range(parsed.salary)
        fields = {
            "employer_id": employer.id,
            "title": parsed.title,
            "description": parsed.description,
            "location": parsed.location,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_period": salary_period,
            "salary_raw": parsed.salary,
            "band": parsed.band,
            "contract_type": parsed.contract_type,
            "working_pattern": parsed.hours,
            "url": parsed.job_url,
            "posted_date": _as_datetime(parsed.posted_date),
            "closing_date": parsed.closing_date,
            "requirements": parsed.requirements or None,
            "benefits": parsed.benefits or None,
            "visa_sponsorship": parsed.visa_sponsorship,
            "content_hash": content_hash,
            "is_active": True,
        }

        if existing is not None:
            self._jobs.update(existing, **fields)
            if matched_cross_source:
                logger.info(
                    "Cross-source duplicate: {} ({} {!r}) matches existing job from {!r} — updated in place, not duplicated",
                    parsed.title,
                    self._source_site,
                    external_id,
                    existing.source_site,
                )
            else:
                logger.info("Updated existing job: {} ({})", parsed.title, external_id)
            return JobSaveResult(job=existing, created=False)

        job = self._jobs.create(source_site=self._source_site, external_id=external_id, **fields)
        self.created_job_ids.append(job.id)
        logger.info("Inserted new job: {} ({})", parsed.title, external_id)
        return JobSaveResult(job=job, created=True)


def _as_datetime(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, datetime.min.time())
