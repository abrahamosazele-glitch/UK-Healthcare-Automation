"""
JSON API for jobs and matches — grouped together since a match is
inherently job-scoped (see `api/__init__.py`'s docstring for the full
4-file endpoint mapping). `Job`/`JobMatch` are SQLAlchemy ORM instances, not
dataclasses, so (unlike `dashboard_api.py`) small serializer functions are
used here rather than returning them directly.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from job_automation.database.models.job import Job
from job_automation.database.models.job_match import JobMatch
from job_automation.database.models.user import User
from job_automation.database.repositories.job_match_repository import JobMatchRepository
from job_automation.database.repositories.job_repository import JobFilter, JobRepository
from job_automation.web.app import get_current_api_user, get_db_session

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    search: str | None = None,
    keywords: str | None = None,
    location: str | None = None,
    employer_name: str | None = None,
    band: str | None = None,
    min_salary: float | None = None,
    max_salary: float | None = None,
    employment_type: str | None = None,
    source: str | None = None,
    visa_sponsorship: bool | None = None,
    remote: bool | None = None,
    closing_soon: bool = False,
    expired: bool = False,
    saved: bool = False,
    favourite: bool = False,
    archived: bool = False,
    pipeline_stage: str | None = None,
    sort_by: str = "posted_date",
    sort_descending: bool = True,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    filters = JobFilter(
        search=search, keywords=keywords, location=location, employer_name=employer_name, band=band,
        min_salary=min_salary, max_salary=max_salary, employment_type=employment_type, source=source,
        visa_sponsorship=visa_sponsorship, remote=remote, closing_soon=closing_soon or None,
        expired=expired or None, sort_by=sort_by, sort_descending=sort_descending,
        user_id=current_user.id, saved_only=saved, favourite_only=favourite, archived_only=archived,
        pipeline_stage=pipeline_stage,
    )
    jobs = JobRepository(session).search(filters)
    return [_job_to_dict(job) for job in jobs]


@router.get("/{job_id}")
def get_job(
    job_id: uuid.UUID, session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> dict:
    job = JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dict(job)


@router.get("/matches/all")
def list_matches(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> list[dict]:
    matches = JobMatchRepository(session).list_for_user(current_user.id)
    return [_match_to_dict(match) for match in matches]


def _job_to_dict(job: Job) -> dict:
    return {
        "id": str(job.id),
        "title": job.title,
        "employer": job.employer.name if job.employer else None,
        "location": job.location,
        "salary_min": float(job.salary_min) if job.salary_min is not None else None,
        "salary_max": float(job.salary_max) if job.salary_max is not None else None,
        "salary_period": job.salary_period,
        "band": job.band,
        "contract_type": job.contract_type,
        "working_pattern": job.working_pattern,
        "visa_sponsorship": job.visa_sponsorship,
        "posted_date": job.posted_date.isoformat() if job.posted_date else None,
        "closing_date": job.closing_date.isoformat() if job.closing_date else None,
        "url": job.url,
        "is_active": job.is_active,
    }


def _match_to_dict(match: JobMatch) -> dict:
    return {
        "id": str(match.id),
        "job_id": str(match.job_id),
        "job_title": match.job.title if match.job else None,
        "employer": match.job.employer.name if match.job and match.job.employer else None,
        "match_score": float(match.match_score),
        "status": match.status.value,
        "analysis": match.analysis,
    }
