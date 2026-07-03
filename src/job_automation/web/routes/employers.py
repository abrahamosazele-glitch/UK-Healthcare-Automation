"""
Employer CRM pages: the employer list/search page and an employer's full
profile page (departments, recruiter contacts, activity timeline, favourite
status, visa-sponsorship notes, success-rate analytics).

Mutations are plain HTML form POSTs that redirect back to the profile page
— the same "full page reload, no HTMX partial swap" pattern already used
throughout this dashboard (`routes/documents.py`, `routes/job_organization.py`).
`api/employers_api.py` is the separate JSON equivalent for programmatic
callers.

No "create employer" UI: `Employer` rows are reference data populated by
the job-ingestion pipeline (`EmployerRepository.get_or_create()`), not
something a candidate manually adds — same reasoning `routes/jobs.py`
never grew a "create job" form.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_automation.analytics.analytics_service import AnalyticsService
from job_automation.database.models.user import User
from job_automation.database.repositories.employer_repository import EmployerFilter, EmployerRepository
from job_automation.employer_crm.employer_crm_models import CommunicationChannel, EmployerType
from job_automation.employer_crm.employer_crm_service import EmployerCrmService
from job_automation.web.app import get_current_user, get_db_session, templates

router = APIRouter()

_FLAG_ACTIONS = {
    "favourite": lambda service, user_id, employer_id: service.favourite(user_id=user_id, employer_id=employer_id),
    "unfavourite": lambda service, user_id, employer_id: service.unfavourite(
        user_id=user_id, employer_id=employer_id
    ),
}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime {value!r}") from exc


@router.get("/employers", response_class=HTMLResponse)
def employers_list(
    request: Request,
    search: str | None = None,
    employer_type: str | None = None,
    favourite: bool = False,
    sort_by: str = "name",
    sort_descending: bool = False,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    filters = EmployerFilter(
        search=search or None,
        employer_type=employer_type or None,
        user_id=current_user.id,
        favourite_only=favourite,
        sort_by=sort_by,
        sort_descending=sort_descending,
    )
    employers = EmployerRepository(session).search(filters)
    favourite_ids = EmployerCrmService(session).list_favourite_employer_ids(current_user.id)
    return templates.TemplateResponse(
        request,
        "employers.html",
        {
            "active_page": "employers",
            "current_user": current_user,
            "employers": employers,
            "filters": filters,
            "favourite_ids": favourite_ids,
            "employer_types": list(EmployerType),
        },
    )


@router.get("/employers/{employer_id}", response_class=HTMLResponse)
def employer_detail(
    employer_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    employer = EmployerRepository(session).get(employer_id)
    if employer is None:
        raise HTTPException(status_code=404, detail="Employer not found")

    crm = EmployerCrmService(session)
    profile = crm.get_profile(user_id=current_user.id, employer_id=employer_id)
    departments = crm.list_departments(employer_id)
    contacts = crm.list_contacts(user_id=current_user.id, employer_id=employer_id)
    activity = crm.list_activity(user_id=current_user.id, employer_id=employer_id)
    analytics = AnalyticsService(session)
    outcome = analytics.employer_outcome_summary(current_user.id, employer_id)

    from job_automation.interviews.interview_repository import InterviewRepository

    interviews = InterviewRepository(session).list_for_employer(user_id=current_user.id, employer_id=employer_id)
    interview_stats = analytics.employer_interview_stats(current_user.id, employer_id)

    return templates.TemplateResponse(
        request,
        "employer_detail.html",
        {
            "active_page": "employers",
            "current_user": current_user,
            "employer": employer,
            "profile": profile,
            "departments": departments,
            "contacts": contacts,
            "activity": activity,
            "outcome": outcome,
            "communication_channels": list(CommunicationChannel),
            "interviews": interviews,
            "interview_stats": interview_stats,
        },
    )


@router.post("/employers/{employer_id}/flag")
def update_employer_flag(
    employer_id: uuid.UUID,
    action: str = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    handler = _FLAG_ACTIONS.get(action)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown action {action!r}. Allowed: {sorted(_FLAG_ACTIONS)}")
    handler(EmployerCrmService(session), current_user.id, employer_id)
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/visa-notes")
def update_visa_notes(
    employer_id: uuid.UUID,
    notes: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    EmployerCrmService(session).update_visa_notes(user_id=current_user.id, employer_id=employer_id, notes=notes)
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/departments")
def add_department(
    employer_id: uuid.UUID,
    name: str = Form(...),
    location: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    EmployerCrmService(session).add_department(employer_id=employer_id, name=name, location=location or None)
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/departments/{department_id}/delete")
def remove_department(
    employer_id: uuid.UUID,
    department_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        EmployerCrmService(session).remove_department(department_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/contacts")
def add_contact(
    employer_id: uuid.UUID,
    name: str = Form(...),
    role: str | None = Form(None),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    notes: str | None = Form(None),
    department_id: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    EmployerCrmService(session).add_contact(
        user_id=current_user.id,
        employer_id=employer_id,
        name=name,
        role=role or None,
        email=email or None,
        phone=phone or None,
        notes=notes or None,
        department_id=uuid.UUID(department_id) if department_id else None,
    )
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/contacts/{contact_id}/delete")
def remove_contact(
    employer_id: uuid.UUID,
    contact_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        EmployerCrmService(session).remove_contact(contact_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/notes")
def add_note(
    employer_id: uuid.UUID,
    body: str = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    EmployerCrmService(session).add_note(user_id=current_user.id, employer_id=employer_id, body=body)
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/communications")
def add_communication(
    employer_id: uuid.UUID,
    channel: str = Form(...),
    body: str = Form(...),
    occurred_at: str | None = Form(None),
    contact_id: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        kind = CommunicationChannel(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown channel {channel!r}") from exc
    EmployerCrmService(session).add_communication(
        user_id=current_user.id,
        employer_id=employer_id,
        channel=kind,
        body=body,
        occurred_at=_parse_datetime(occurred_at),
        contact_id=uuid.UUID(contact_id) if contact_id else None,
    )
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)


@router.post("/employers/{employer_id}/activity/{entry_id}/delete")
def remove_activity_entry(
    employer_id: uuid.UUID,
    entry_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        EmployerCrmService(session).remove_activity_entry(entry_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/employers/{employer_id}", status_code=303)
