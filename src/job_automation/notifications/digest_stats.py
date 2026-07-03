"""
Computes the small stats table `email_templates._render_digest()` shows —
shared by `scheduler.tasks.send_daily_digest` and `.send_weekly_summary`,
since both need the same four numbers for one user, differing only in
which cutoff datetime they measure "new" against.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.database.models.job import Job
from job_automation.database.models.job_match import JobMatch


def compute_stats(session: Session, user_id: uuid.UUID, *, since: datetime) -> dict[str, int]:
    jobs_discovered = session.scalar(select(func.count()).select_from(Job).where(Job.created_at >= since)) or 0

    new_matches = (
        session.scalar(
            select(func.count())
            .select_from(JobMatch)
            .where(JobMatch.user_id == user_id, JobMatch.created_at >= since)
        )
        or 0
    )

    high_matches = (
        session.scalar(
            select(func.count())
            .select_from(JobMatch)
            .where(
                JobMatch.user_id == user_id,
                JobMatch.created_at >= since,
                JobMatch.match_score >= settings.job_ingestion_high_match_threshold,
            )
        )
        or 0
    )

    closing_soon_cutoff = date.today() + timedelta(days=7)
    closing_soon = (
        session.scalar(
            select(func.count())
            .select_from(JobMatch)
            .join(Job, Job.id == JobMatch.job_id)
            .where(
                JobMatch.user_id == user_id,
                Job.is_active.is_(True),
                Job.closing_date.is_not(None),
                Job.closing_date <= closing_soon_cutoff,
            )
        )
        or 0
    )

    return {
        "New jobs discovered": jobs_discovered,
        "New matches for you": new_matches,
        f"High-scoring matches (≥{settings.job_ingestion_high_match_threshold:.0f}%)": high_matches,
        "Jobs closing within 7 days": closing_soon,
    }
