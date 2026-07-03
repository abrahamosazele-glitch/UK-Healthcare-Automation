"""
The Kanban board page: every non-hidden, non-archived tracked job for the
current user, grouped into columns by `PipelineStage`.

Deliberately button-based, not drag-and-drop — a "move to <next stage>"
button per card, rendered only for the stages `JobPipeline.allowed_next_
stages()` actually permits from that card's current stage, submitting to
the same `POST /jobs/{job_id}/stage` route `routes/job_organization.py`
already exposes. Real drag-and-drop would need a JS library, client-side
state reconciliation, and an HTMX/JSON PATCH endpoint beyond this
milestone's stated scope ("premium job management platform" was not read
as "must have drag-and-drop"); a working, testable button covers the same
requirement ("Kanban-style workflow") without that added surface area. See
docs/JOB_MANAGEMENT.md's "Known limitations".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from job_automation.database.models.user import User
from job_automation.job_organization.job_organization_models import JobPipeline, PipelineStage
from job_automation.job_organization.saved_job_repository import SavedJobRepository
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()


@router.get("/board", response_class=HTMLResponse)
def board_page(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    saved_jobs = SavedJobRepository(session).list_for_user(current_user.id)

    columns = {stage: [] for stage in PipelineStage}
    for saved_job in saved_jobs:
        stage = PipelineStage(saved_job.pipeline_stage)
        columns[stage].append(saved_job)

    board_columns = [
        {
            "stage": stage,
            "saved_jobs": columns[stage],
            "next_stages": sorted(JobPipeline.allowed_next_stages(stage), key=lambda s: list(PipelineStage).index(s)),
        }
        for stage in PipelineStage
    ]

    return templates.TemplateResponse(
        request,
        "board.html",
        {"active_page": "board", "current_user": current_user, "board_columns": board_columns},
    )
