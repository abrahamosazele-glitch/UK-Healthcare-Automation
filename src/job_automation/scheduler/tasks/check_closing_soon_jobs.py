"""
Scheduled task: notify users when a job they've been matched against is
closing within `settings.job_ingestion_closing_soon_hours` (48 by default).

Distinct from `JobFilter.closing_soon` (a 7-day browsing filter on the Jobs
page) — this is the automatic, urgent notification the Job Ingestion
Service milestone asks for, with its own tighter window and its own
once-only guard (`Job.closing_soon_notified_at`) so an hourly scan doesn't
re-notify the same users about the same job every run.

`Job.closing_date` is a `date`, not a `datetime` — there's no sub-day
precision to check "exactly 48 hours" against, so this rounds the
configured hour window up to whole days (`ceil(hours / 24)`) and treats
"closing today or within that many days" as the window. A job closing
tomorrow at 9am and one closing tomorrow at 11pm are indistinguishable at
this granularity; that's an accepted approximation, not a bug — closing
dates on real job listings are dates, not timestamps, on every site this
project imports from.

Only notifies users who actually have a `JobMatch` for the job (i.e. it
was already evaluated as relevant to them) — not every active user,
avoiding "closing soon" spam for a job a candidate was never matched to.
"""

from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.database.models.job import Job
from job_automation.database.models.job_match import JobMatch
from job_automation.notifications.event_bus import event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.utils.helpers import utc_now
from job_automation.utils.logger import logger


def run(session: Session) -> dict:
    now = utc_now()
    window_days = math.ceil(settings.job_ingestion_closing_soon_hours / 24)
    today = now.date()
    cutoff = today + timedelta(days=window_days)

    candidates = list(
        session.scalars(
            select(Job).where(
                Job.is_active.is_(True),
                Job.closing_date.is_not(None),
                Job.closing_date >= today,
                Job.closing_date <= cutoff,
                Job.closing_soon_notified_at.is_(None),
            )
        )
    )

    jobs_notified = 0
    notifications_sent = 0
    for job in candidates:
        user_ids = set(session.scalars(select(JobMatch.user_id).where(JobMatch.job_id == job.id)).all())
        if user_ids:
            employer_name = job.employer.name if job.employer else None
            payload = {
                "job_id": str(job.id),
                "job_title": job.title,
                "employer_name": employer_name,
                "closing_date": job.closing_date.isoformat(),
            }
            for user_id in user_ids:
                event_bus.publish(Event(event_type=EventType.JOB_CLOSING_SOON, user_id=user_id, payload=payload), session)
                notifications_sent += 1
        job.closing_soon_notified_at = now
        jobs_notified += 1

    logger.info(
        "check_closing_soon_jobs: {} job(s) newly within the closing-soon window, {} notification(s) sent",
        jobs_notified,
        notifications_sent,
    )
    return {"jobs_checked": len(candidates), "jobs_notified": jobs_notified, "notifications_sent": notifications_sent}
