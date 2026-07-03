"""
The interview calendar: month grid, week grid, and day agenda, all behind
one `/calendar` route switched by a `view` query parameter (rather than
three separate routes) — the three views share the same "fetch interviews
in a date range, group by day" shape, and a single page with a view
switcher is simpler to navigate than three disconnected pages.

Colour-coding and "click an event to open its detail page" are template
concerns (`calendar.html` links straight to `/interviews/{id}`) — this
route only computes the date range and groups `InterviewRecord` rows by
day, reusing `InterviewRepository.list_between()`.
"""

from __future__ import annotations

import calendar as calendar_module
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from job_automation.database.models.user import User
from job_automation.interviews.interview_repository import InterviewRepository
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()


def _to_range_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    view: str = "month",
    date_str: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    anchor = date.fromisoformat(date_str) if date_str else date.today()
    repository = InterviewRepository(session)

    if view == "day":
        start_day = anchor
        end_day = anchor + timedelta(days=1)
        prev_date = (anchor - timedelta(days=1)).isoformat()
        next_date = (anchor + timedelta(days=1)).isoformat()
    elif view == "week":
        start_day = anchor - timedelta(days=anchor.weekday())  # Monday
        end_day = start_day + timedelta(days=7)
        prev_date = (start_day - timedelta(days=7)).isoformat()
        next_date = (start_day + timedelta(days=7)).isoformat()
    else:
        view = "month"
        start_day = anchor.replace(day=1)
        next_month = (start_day.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_day = next_month
        prev_date = (start_day - timedelta(days=1)).replace(day=1).isoformat()
        next_date = next_month.isoformat()

    interviews = repository.list_between(current_user.id, start=_to_range_start(start_day), end=_to_range_start(end_day))
    by_day: dict[date, list] = {}
    for interview in interviews:
        by_day.setdefault(interview.scheduled_at.date(), []).append(interview)

    weeks: list[list[date]] = []
    if view == "month":
        calendar_module.setfirstweekday(calendar_module.MONDAY)
        weeks = [
            [d for d in week if d.month == start_day.month]
            for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(anchor.year, anchor.month)
        ]

    days_in_range = [start_day + timedelta(days=i) for i in range((end_day - start_day).days)]

    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "active_page": "calendar",
            "current_user": current_user,
            "view": view,
            "anchor": anchor,
            "start_day": start_day,
            "end_day": end_day - timedelta(days=1),
            "days_in_range": days_in_range,
            "weeks": weeks,
            "by_day": by_day,
            "prev_date": prev_date,
            "next_date": next_date,
            "today": date.today(),
        },
    )
