"""
Settings page: the single, complete editor for `CandidatePreferences` (all
9 fields — deliberately not duplicated as a partial form on the Candidate
Profile page, which only *displays* preferences with a link here, to avoid
two different forms submitting different subsets of the same fields and
silently clobbering whichever fields the other form omitted).

Notification preferences (per-type email toggles, quiet hours, digest
timing, AI match threshold, preferred email) live at `/notifications
/settings` (`routes/notifications.py`), not here — squarely about the
notification subsystem specifically, not general application preferences.
This page links there rather than duplicating the form.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_automation.config.settings import settings as app_settings
from job_automation.database.models.user import User
from job_automation.profile.profile_service import ProfileService
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    profile = ProfileService(session).get(current_user.id)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active_page": "settings",
            "current_user": current_user,
            "profile": profile,
            "ai_configured": bool(app_settings.anthropic_api_key),
        },
    )


@router.post("/settings/preferences")
def update_preferences(
    preferred_locations: str | None = Form(None),
    preferred_employers: str | None = Form(None),
    preferred_salary_min: float | None = Form(None),
    preferred_nhs_band: str | None = Form(None),
    max_travel_distance_miles: float | None = Form(None),
    preferred_contract_type: str | None = Form(None),
    preferred_hours: str | None = Form(None),
    preferred_working_pattern: str | None = Form(None),
    remote_preference: str | None = Form(None),
    visa_sponsorship_required: bool = Form(False),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    service = ProfileService(session)
    profile = service.get(current_user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No candidate profile exists yet")

    updated = replace(
        profile,
        preferences=replace(
            profile.preferences,
            preferred_locations=_split_csv(preferred_locations),
            preferred_employers=_split_csv(preferred_employers),
            preferred_salary_min=preferred_salary_min,
            preferred_nhs_band=preferred_nhs_band or None,
            max_travel_distance_miles=max_travel_distance_miles,
            preferred_contract_type=preferred_contract_type or None,
            preferred_hours=preferred_hours or None,
            preferred_working_pattern=preferred_working_pattern or None,
            remote_preference=remote_preference or None,
            visa_sponsorship_required=visa_sponsorship_required,
        ),
    )
    service.save(updated, user_id=current_user.id)
    return RedirectResponse(url="/settings", status_code=303)


def _split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())
