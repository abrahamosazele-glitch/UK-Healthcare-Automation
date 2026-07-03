"""
JSON API for the background scheduler: list registered tasks (with their
latest run), full run history, and a manual "Run now" trigger — the
programmatic counterpart to `routes/scheduler.py`'s HTML page/form.

`TaskDefinition.func` (a plain Python callable) is deliberately excluded
from `_task_to_dict()` — it isn't JSON-serializable and callers have no use
for it; everything else about a task (name, description, interval,
max attempts) is.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from job_automation.database.models.user import User
from job_automation.scheduler.scheduler_models import TaskDefinition, TaskRunSummary
from job_automation.scheduler.scheduler_service import SchedulerService
from job_automation.web.app import get_current_api_user, get_scheduler_service

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/tasks")
def list_tasks(
    current_user: User = Depends(get_current_api_user),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> list[dict]:
    latest_runs = scheduler_service.get_latest_per_task()
    return [
        {**_task_to_dict(task), "latest_run": _run_to_dict(latest_runs[task.name]) if task.name in latest_runs else None}
        for task in scheduler_service.list_task_definitions()
    ]


@router.get("/history")
def get_history(
    task_name: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_api_user),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> list[dict]:
    return [_run_to_dict(run) for run in scheduler_service.get_history(task_name=task_name, limit=limit)]


@router.post("/{task_name}/run")
def run_task_now(
    task_name: str,
    current_user: User = Depends(get_current_api_user),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> dict:
    try:
        run = scheduler_service.run_task(task_name, triggered_by="manual")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown scheduler task: {task_name!r}")
    return _run_to_dict(run)


def _task_to_dict(task: TaskDefinition) -> dict:
    return {
        "name": task.name,
        "description": task.description,
        "interval_seconds": task.interval_seconds,
        "max_attempts": task.max_attempts,
    }


def _run_to_dict(run: TaskRunSummary) -> dict:
    return {
        "id": str(run.id),
        "task_name": run.task_name,
        "status": run.status.value,
        "triggered_by": run.triggered_by,
        "attempt": run.attempt,
        "max_attempts": run.max_attempts,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_message": run.error_message,
        "result_summary": run.result_summary,
    }
