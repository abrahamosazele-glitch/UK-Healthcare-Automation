"""
Candidate profile display + editing.

`CandidateProfile` and its nested entities are frozen dataclasses (by
design — see `profile.candidate_profile`'s module docstring) — "editing"
here means loading the current profile, building an updated copy via
`dataclasses.replace()`, and saving that through the existing
`ProfileService.save()`. No new mutation methods were added to the profile
classes themselves.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_automation.database.models.user import User
from job_automation.profile.candidate_profile import PersonalInformation, VisaStatus
from job_automation.profile.profile_service import ProfileService
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()


@router.get("/candidate", response_class=HTMLResponse)
def candidate_profile_page(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    profile = ProfileService(session).get(current_user.id)
    return templates.TemplateResponse(
        request,
        "candidate_profile.html",
        {"active_page": "candidate", "current_user": current_user, "profile": profile},
    )


@router.post("/candidate/personal-information")
def update_personal_information(
    full_name: str = Form(...),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    address: str | None = Form(None),
    personal_statement: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    service = ProfileService(session)
    profile = _require_profile(service, current_user)
    updated = replace(
        profile,
        personal_information=PersonalInformation(
            full_name=full_name,
            email=email or None,
            phone=phone or None,
            address=address or None,
            personal_statement=personal_statement or None,
        ),
    )
    service.save(updated, user_id=current_user.id)
    return RedirectResponse(url="/candidate", status_code=303)


@router.post("/candidate/visa-status")
def update_visa_status(
    right_to_work_uk: bool = Form(False),
    visa_type: str | None = Form(None),
    sponsorship_required: bool = Form(False),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    service = ProfileService(session)
    profile = _require_profile(service, current_user)
    updated = replace(
        profile,
        visa_status=VisaStatus(
            right_to_work_uk=right_to_work_uk, visa_type=visa_type or None, sponsorship_required=sponsorship_required
        ),
    )
    service.save(updated, user_id=current_user.id)
    return RedirectResponse(url="/candidate", status_code=303)


def _require_profile(service: ProfileService, user: User):
    profile = service.get(user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No candidate profile exists yet to edit")
    return profile
