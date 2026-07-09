"""
Read-only database diagnostics — proves the scraper, scheduler, and
dashboard are really reading/writing the same `settings.database_url`
rather than three silently-different databases (e.g. a scraper run against
a local file while the deployed dashboard reads a different one on
Railway). Shared by the `/diagnostics/database` route and
`scripts/db_diagnostics.py` so both report identically; neither
reimplements the query logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.database.models.job import Job
from job_automation.database.models.scheduler_task_run_record import SchedulerTaskRunRecord


@dataclass
class LatestJobInfo:
    id: str
    title: str
    source_site: str
    created_at: str


@dataclass
class DatabaseDiagnostics:
    database_url: str
    total_jobs: int
    jobs_by_source: dict[str, int]
    latest_jobs: list[LatestJobInfo]
    scheduler_task_run_count: int


def collect_database_diagnostics(session: Session, *, latest_limit: int = 10) -> DatabaseDiagnostics:
    source_rows = session.execute(
        select(Job.source_site, func.count()).group_by(Job.source_site).order_by(func.count().desc())
    ).all()
    jobs_by_source = {source: count for source, count in source_rows}

    latest_rows = session.execute(
        select(Job.id, Job.title, Job.source_site, Job.created_at)
        .order_by(Job.created_at.desc())
        .limit(latest_limit)
    ).all()
    latest_jobs = [
        LatestJobInfo(id=str(job_id), title=title, source_site=source_site, created_at=created_at.isoformat())
        for job_id, title, source_site, created_at in latest_rows
    ]

    scheduler_task_run_count = session.scalar(select(func.count()).select_from(SchedulerTaskRunRecord)) or 0

    return DatabaseDiagnostics(
        database_url=settings.database_url,
        total_jobs=sum(jobs_by_source.values()),
        jobs_by_source=jobs_by_source,
        latest_jobs=latest_jobs,
        scheduler_task_run_count=scheduler_task_run_count,
    )
