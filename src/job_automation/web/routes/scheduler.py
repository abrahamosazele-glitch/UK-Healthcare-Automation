"""
Background scheduler dashboard page: shows every registered task, its
last-run status, and a manual "Run now" button per task. Read/trigger
only — this page cannot change a task's schedule or configuration (those
are `.env` settings, per docs/BACKGROUND_SCHEDULER.md).

The manual "Run now" button posts here (not to `/api/scheduler/...`)
because it's a plain HTML form submit expecting a redirect back to this
page, matching every other mutation-via-plain-form route in this codebase
(e.g. `routes/documents.py`'s regenerate button) — `/api/scheduler/...`
(JSON, no redirect) exists separately for programmatic callers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_automation.database.models.user import User
from job_automation.scheduler.scheduler_service import SchedulerService
from job_automation.web.app import get_current_user, get_scheduler_service, templates

router = APIRouter()


@router.get("/scheduler", response_class=HTMLResponse)
def scheduler_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> HTMLResponse:
    tasks = scheduler_service.list_task_definitions()
    latest_runs = scheduler_service.get_latest_per_task()
    history = scheduler_service.get_history(limit=25)
    return templates.TemplateResponse(
        request,
        "scheduler.html",
        {
            "active_page": "scheduler",
            "current_user": current_user,
            "tasks": tasks,
            "latest_runs": latest_runs,
            "history": history,
        },
    )


@router.post("/scheduler/{task_name}/run")
def run_task_now(
    task_name: str,
    current_user: User = Depends(get_current_user),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> RedirectResponse:
    try:
        scheduler_service.run_task(task_name, triggered_by="manual")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown scheduler task: {task_name!r}")
    return RedirectResponse(url="/scheduler", status_code=303)
