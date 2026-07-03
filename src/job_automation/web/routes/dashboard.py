"""
Dashboard home page. Calls `AnalyticsService` for every number/list shown —
no query logic lives in this route.

`job_organization_summary`/`upcoming_reminders` were added for the Job
Management milestone. "Recent activity" for the Kanban side is
deliberately *not* a second activity feed: pipeline-stage transitions and
due reminders already publish real notifications, so `recent_job_notifications`
just re-reads the last few via `NotificationService` (already-existing,
reused as-is) rather than this route deriving the same feed a second way —
see `analytics_models.JobOrganizationSummary`'s docstring for the same
reasoning stated on the data side.

`employer_summaries`/`favourite_employer_count`/`employers_tracked_count`
were added for the Employer & Application CRM milestone — top employers by
application volume/success rate, reusing
`AnalyticsService.list_employer_outcome_summaries()` as-is (already sorted
by `applications_sent` descending). Full per-employer detail lives on
`/employers/{id}`, not here — this section is a summary, not a duplicate.

`upcoming_interviews`/`interviews_this_week`/`next_interview`/
`interview_prep_completion`/`recent_interview_outcomes` were added for the
Interview & Calendar Management milestone. "Interviews this week" uses the
same Monday-start week boundary `routes/calendar.py`'s week view uses, so
the dashboard count and the calendar's week view always agree on what
"this week" means.

`ai_status` was added for the Anthropic AI Integration milestone —
`AnalyticsService.ai_status()`, reused as-is.

`job_ingestion_summary` was added for the Job Ingestion Service milestone
— `AnalyticsService.job_ingestion_summary()`, reused as-is. Computed
server-side (not client-side-fetched like the Analytics page's Chart.js
canvases) since it's rendered as plain stat cards/lists here, with no
canvas that needs JS to draw into.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from job_automation.analytics import AnalyticsService
from job_automation.database.models.user import User
from job_automation.employer_crm.employer_crm_service import EmployerCrmService
from job_automation.interviews.interview_repository import InterviewRepository
from job_automation.interviews.interview_service import InterviewService
from job_automation.job_organization.reminder_service import ReminderService
from job_automation.notifications.notification_service import NotificationService
from job_automation.utils.helpers import utc_now
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()


def _format_countdown(delta: timedelta) -> str:
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    days, remainder_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_home(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    analytics = AnalyticsService(session)
    employer_summaries = analytics.list_employer_outcome_summaries(current_user.id)

    interview_repository = InterviewRepository(session)
    now = utc_now()
    upcoming_interviews = interview_repository.list_upcoming(current_user.id, as_of=now, limit=8)
    today = date.today()
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = midnight_today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)
    interviews_this_week = len(interview_repository.list_between(current_user.id, start=week_start, end=week_end))

    next_interview = upcoming_interviews[0] if upcoming_interviews else None
    next_interview_countdown = _format_countdown(next_interview.scheduled_at - now) if next_interview else None

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "current_user": current_user,
            "summary": analytics.dashboard_summary(current_user.id),
            "recent_activity": analytics.recent_activity(current_user.id, limit=10),
            "upcoming_deadlines": analytics.upcoming_deadlines(current_user.id, limit=8),
            "job_org_summary": analytics.job_organization_summary(current_user.id),
            "upcoming_reminders": ReminderService(session).list_upcoming_for_user(current_user.id, limit=8),
            "recent_job_notifications": [
                n
                for n in NotificationService(session).list_notifications(current_user.id, limit=20)
                if n.source == "job_organization"
            ][:8],
            "employer_summaries": employer_summaries[:8],
            "favourite_employer_count": len(EmployerCrmService(session).list_favourite_employer_ids(current_user.id)),
            "employers_tracked_count": len(employer_summaries),
            "interview_analytics": analytics.interview_analytics_summary(current_user.id),
            "upcoming_interviews": upcoming_interviews,
            "next_interview": next_interview,
            "next_interview_countdown": next_interview_countdown,
            "interviews_this_week": interviews_this_week,
            "interview_prep_completion": InterviewService(session).average_upcoming_preparation_completion(
                current_user.id
            ),
            "recent_interview_outcomes": interview_repository.list_recent_outcomes(current_user.id, limit=5),
            "ai_status": analytics.ai_status(current_user.id),
            "job_ingestion_summary": analytics.job_ingestion_summary(latest_limit=8),
        },
    )
