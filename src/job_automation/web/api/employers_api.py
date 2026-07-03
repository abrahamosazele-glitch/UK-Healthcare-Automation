"""
JSON API for the employer CRM — the programmatic equivalent of
`routes/employers.py`. Every mutation goes through `EmployerCrmService`,
which already owns favourite/visa-note/department/contact/activity
logic; this file adds no new business rules, only maps JSON request
bodies onto the existing named service methods (same relationship
`api/job_organization_api.py` has to `JobOrganizationService`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_automation.analytics.analytics_service import AnalyticsService
from job_automation.database.models.employer import Employer
from job_automation.database.models.employer_contact import EmployerContact
from job_automation.database.models.employer_department import EmployerDepartment
from job_automation.database.models.employer_profile import EmployerProfile
from job_automation.database.models.user import User
from job_automation.database.repositories.employer_repository import EmployerFilter, EmployerRepository
from job_automation.employer_crm.employer_crm_models import ActivityEntryType, CommunicationChannel
from job_automation.employer_crm.employer_crm_service import EmployerCrmService
from job_automation.web.app import get_current_api_user, get_db_session

router = APIRouter(prefix="/api/employers", tags=["employers"])

_FLAG_ACTIONS = {
    "favourite": lambda service, user_id, employer_id: service.favourite(user_id=user_id, employer_id=employer_id),
    "unfavourite": lambda service, user_id, employer_id: service.unfavourite(
        user_id=user_id, employer_id=employer_id
    ),
}


class FlagRequest(BaseModel):
    action: str


class VisaNotesRequest(BaseModel):
    notes: str | None = None


class DepartmentRequest(BaseModel):
    name: str
    location: str | None = None


class ContactRequest(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    department_id: uuid.UUID | None = None


class NoteRequest(BaseModel):
    body: str


class CommunicationRequest(BaseModel):
    channel: str
    body: str
    occurred_at: datetime | None = None
    contact_id: uuid.UUID | None = None


@router.get("")
def list_employers(
    search: str | None = None,
    employer_type: str | None = None,
    favourite: bool = False,
    sort_by: str = "name",
    sort_descending: bool = False,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    filters = EmployerFilter(
        search=search, employer_type=employer_type, user_id=current_user.id, favourite_only=favourite,
        sort_by=sort_by, sort_descending=sort_descending,
    )
    employers = EmployerRepository(session).search(filters)
    return [_employer_to_dict(employer) for employer in employers]


@router.get("/{employer_id}")
def get_employer(
    employer_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    employer = EmployerRepository(session).get(employer_id)
    if employer is None:
        raise HTTPException(status_code=404, detail="Employer not found")
    payload = _employer_to_dict(employer)
    payload["outcome"] = _outcome_to_dict(AnalyticsService(session).employer_outcome_summary(current_user.id, employer_id))
    profile = EmployerCrmService(session).get_profile(user_id=current_user.id, employer_id=employer_id)
    payload["is_favourite"] = bool(profile and profile.is_favourite)
    payload["visa_sponsorship_notes"] = profile.visa_sponsorship_notes if profile else None
    return payload


@router.post("/{employer_id}/flag")
def update_flag(
    employer_id: uuid.UUID,
    body: FlagRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    handler = _FLAG_ACTIONS.get(body.action)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown action {body.action!r}. Allowed: {sorted(_FLAG_ACTIONS)}")
    profile = handler(EmployerCrmService(session), current_user.id, employer_id)
    return _profile_to_dict(profile)


@router.post("/{employer_id}/visa-notes")
def update_visa_notes(
    employer_id: uuid.UUID,
    body: VisaNotesRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    profile = EmployerCrmService(session).update_visa_notes(
        user_id=current_user.id, employer_id=employer_id, notes=body.notes
    )
    return _profile_to_dict(profile)


@router.get("/{employer_id}/departments")
def list_departments(
    employer_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    return [_department_to_dict(d) for d in EmployerCrmService(session).list_departments(employer_id)]


@router.post("/{employer_id}/departments")
def add_department(
    employer_id: uuid.UUID,
    body: DepartmentRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    department = EmployerCrmService(session).add_department(
        employer_id=employer_id, name=body.name, location=body.location
    )
    return _department_to_dict(department)


@router.delete("/{employer_id}/departments/{department_id}")
def remove_department(
    employer_id: uuid.UUID,
    department_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        EmployerCrmService(session).remove_department(department_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/{employer_id}/contacts")
def list_contacts(
    employer_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    contacts = EmployerCrmService(session).list_contacts(user_id=current_user.id, employer_id=employer_id)
    return [_contact_to_dict(c) for c in contacts]


@router.post("/{employer_id}/contacts")
def add_contact(
    employer_id: uuid.UUID,
    body: ContactRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    contact = EmployerCrmService(session).add_contact(
        user_id=current_user.id,
        employer_id=employer_id,
        name=body.name,
        role=body.role,
        email=body.email,
        phone=body.phone,
        notes=body.notes,
        department_id=body.department_id,
    )
    return _contact_to_dict(contact)


@router.delete("/{employer_id}/contacts/{contact_id}")
def remove_contact(
    employer_id: uuid.UUID,
    contact_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        EmployerCrmService(session).remove_contact(contact_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/{employer_id}/activity")
def list_activity(
    employer_id: uuid.UUID,
    entry_type: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    kind = ActivityEntryType(entry_type) if entry_type else None
    entries = EmployerCrmService(session).list_activity(
        user_id=current_user.id, employer_id=employer_id, entry_type=kind
    )
    return [_activity_to_dict(entry) for entry in entries]


@router.post("/{employer_id}/notes")
def add_note(
    employer_id: uuid.UUID,
    body: NoteRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    entry = EmployerCrmService(session).add_note(user_id=current_user.id, employer_id=employer_id, body=body.body)
    return _activity_to_dict(entry)


@router.post("/{employer_id}/communications")
def add_communication(
    employer_id: uuid.UUID,
    body: CommunicationRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        channel = CommunicationChannel(body.channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown channel {body.channel!r}") from exc
    entry = EmployerCrmService(session).add_communication(
        user_id=current_user.id,
        employer_id=employer_id,
        channel=channel,
        body=body.body,
        occurred_at=body.occurred_at,
        contact_id=body.contact_id,
    )
    return _activity_to_dict(entry)


@router.delete("/{employer_id}/activity/{entry_id}")
def remove_activity_entry(
    employer_id: uuid.UUID,
    entry_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> dict:
    try:
        EmployerCrmService(session).remove_activity_entry(entry_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


def _employer_to_dict(employer: Employer) -> dict:
    return {
        "id": str(employer.id),
        "name": employer.name,
        "employer_type": employer.employer_type,
        "website": employer.website,
        "email": employer.email,
        "phone": employer.phone,
        "address": employer.address,
        "description": employer.description,
    }


def _profile_to_dict(profile: EmployerProfile) -> dict:
    return {
        "id": str(profile.id),
        "employer_id": str(profile.employer_id),
        "is_favourite": profile.is_favourite,
        "visa_sponsorship_notes": profile.visa_sponsorship_notes,
    }


def _department_to_dict(department: EmployerDepartment) -> dict:
    return {
        "id": str(department.id),
        "employer_id": str(department.employer_id),
        "name": department.name,
        "location": department.location,
    }


def _contact_to_dict(contact: EmployerContact) -> dict:
    return {
        "id": str(contact.id),
        "employer_id": str(contact.employer_id),
        "department_id": str(contact.department_id) if contact.department_id else None,
        "name": contact.name,
        "role": contact.role,
        "email": contact.email,
        "phone": contact.phone,
        "notes": contact.notes,
    }


def _activity_to_dict(entry) -> dict:
    return {
        "id": str(entry.id),
        "employer_id": str(entry.employer_id),
        "contact_id": str(entry.contact_id) if entry.contact_id else None,
        "entry_type": entry.entry_type,
        "channel": entry.channel,
        "body": entry.body,
        "occurred_at": entry.occurred_at.isoformat(),
    }


def _outcome_to_dict(outcome) -> dict:
    return {
        "employer_id": outcome.employer_id,
        "employer_name": outcome.employer_name,
        "applications_sent": outcome.applications_sent,
        "interviews": outcome.interviews,
        "offers": outcome.offers,
        "rejections": outcome.rejections,
        "interview_rate": outcome.interview_rate,
        "offer_rate": outcome.offer_rate,
    }
