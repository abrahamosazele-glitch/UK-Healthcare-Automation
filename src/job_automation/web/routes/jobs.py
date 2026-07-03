"""
Jobs list (filtered/sorted via `JobRepository.search()`) and job detail
pages. No query logic here beyond building a `JobFilter` from query
parameters — the actual filtering/sorting lives in the repository.

Extended for the Job Management milestone: `filters.user_id` is now always
set to the logged-in user, so hidden/archived jobs are excluded from the
main list by default (see `JobRepository.search()`'s docstring) — every
visitor to `/jobs` implicitly gets their own organization state applied,
not just when a saved/favourite/status filter is explicitly chosen.
`saved_states` (a `job_id -> SavedJob` map, batched via `SavedJobRepository
.map_by_job_id()`) is passed alongside `jobs` so `job_card.html` can render
save/favourite/hide/archive button states without an N+1 query per card.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.career_assistant.career_assistant_service import CareerAssistantService
from job_automation.database.models.user import User
from job_automation.database.repositories.job_match_repository import JobMatchRepository
from job_automation.database.repositories.job_repository import JobFilter, JobRepository
from job_automation.job_organization.job_organization_models import (
    JobPipeline,
    JobPriority,
    PipelineStage,
    ReminderType,
)
from job_automation.job_organization.reminder_service import ReminderService
from job_automation.job_organization.saved_job_repository import SavedJobRepository
from job_automation.workflows.workflow_repository import WorkflowRepository
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()


def _parse_optional_float(value: str | None) -> float | None:
    """`min_salary`/`max_salary` are declared `str | None`, not `float |
    None`, specifically so a blank number input still submits — the
    filter form's `<input type="number">` fields always send their name
    even when empty (unlike a checkbox), and FastAPI/Pydantic rejects an
    empty string for a `float | None` query parameter with a `422`
    instead of treating it as "not provided." That `422` silently emptied
    the whole results panel via HTMX's `hx-select` (which found no
    matching element in the JSON error body), making a filter-form
    submission with blank salary fields look like "no jobs found" even
    though every filter was empty."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid number {value!r}") from exc


@router.get("/jobs", response_class=HTMLResponse)
def jobs_list(
    request: Request,
    search: str | None = None,
    keywords: str | None = None,
    location: str | None = None,
    employer_name: str | None = None,
    band: str | None = None,
    min_salary: str | None = None,
    max_salary: str | None = None,
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
    sort_descending: bool = Query(default=True),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    filters = JobFilter(
        search=search or None,
        keywords=keywords or None,
        location=location or None,
        employer_name=employer_name or None,
        band=band or None,
        min_salary=_parse_optional_float(min_salary),
        max_salary=_parse_optional_float(max_salary),
        employment_type=employment_type or None,
        source=source or None,
        visa_sponsorship=visa_sponsorship,
        remote=remote,
        closing_soon=closing_soon or None,
        expired=expired or None,
        sort_by=sort_by,
        sort_descending=sort_descending,
        user_id=current_user.id,
        saved_only=saved,
        favourite_only=favourite,
        archived_only=archived,
        pipeline_stage=pipeline_stage or None,
    )
    jobs = JobRepository(session).search(filters)
    saved_states = SavedJobRepository(session).map_by_job_id(current_user.id, [job.id for job in jobs])
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "active_page": "jobs",
            "current_user": current_user,
            "jobs": jobs,
            "filters": filters,
            "saved_states": saved_states,
            "pipeline_stages": list(PipelineStage),
            "available_sources": JobRepository(session).list_distinct_sources(),
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    job_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    job = JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    match = JobMatchRepository(session).find(job_id=job_id, user_id=current_user.id)
    workflow = WorkflowRepository(session).find_by_job_and_user(job_id, current_user.id)
    saved_job = SavedJobRepository(session).find(user_id=current_user.id, job_id=job_id)
    reminders = ReminderService(session).list_for_job(user_id=current_user.id, job_id=job_id)
    current_stage = PipelineStage(saved_job.pipeline_stage) if saved_job else PipelineStage.NEW

    # AI Career Assistant — purely additive: a rule-based, zero-LLM-cost
    # insight derived from the match already computed, `None` when there's
    # no match yet (a brand new/unmatched job) so the template can hide
    # the whole panel rather than rendering with empty data. See
    # docs/CAREER_ASSISTANT.md.
    career_insight = None
    if match is not None and match.analysis:
        match_result = MatchResult.from_dict(match.analysis, fallback_overall_score=float(match.match_score))
        career_insight = CareerAssistantService().build_insight(match_result, JobSnapshot.from_job(job))

    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "active_page": "jobs",
            "current_user": current_user,
            "job": job,
            "match": match,
            "workflow": workflow,
            "saved_job": saved_job,
            "reminders": reminders,
            "pipeline_stages": list(PipelineStage),
            "job_priorities": list(JobPriority),
            "reminder_types": list(ReminderType),
            "next_stages": sorted(JobPipeline.allowed_next_stages(current_stage), key=list(PipelineStage).index),
            "career_insight": career_insight,
        },
    )
