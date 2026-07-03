"""
The main entry point for the employer CRM: favourite/unfavourite an
employer, record visa-sponsorship research, manage departments/recruiter
contacts, and log notes/communication history — the same orchestrator role
`JobOrganizationService`/`WorkflowService` play for their own subsystems.

Deliberately publishes no events/notifications. Unlike Job Management's
pipeline transitions ("every transition must create a notification" was an
explicit requirement there), nothing in this milestone's spec asks for CRM
mutations to notify anyone — favouriting an employer or jotting down a note
is a passive record-keeping action, not a pipeline event a candidate needs
to be alerted about. Kept out rather than added speculatively.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from job_automation.employer_crm.employer_activity_repository import EmployerActivityRepository
from job_automation.employer_crm.employer_contact_repository import EmployerContactRepository
from job_automation.employer_crm.employer_crm_models import ActivityEntryType, CommunicationChannel
from job_automation.employer_crm.employer_department_repository import EmployerDepartmentRepository
from job_automation.employer_crm.employer_profile_repository import EmployerProfileRepository
from job_automation.utils.helpers import utc_now


class EmployerCrmService:
    def __init__(
        self,
        session: Session,
        *,
        profile_repository: EmployerProfileRepository | None = None,
        department_repository: EmployerDepartmentRepository | None = None,
        contact_repository: EmployerContactRepository | None = None,
        activity_repository: EmployerActivityRepository | None = None,
    ) -> None:
        self._session = session
        self._profiles = profile_repository or EmployerProfileRepository(session)
        self._departments = department_repository or EmployerDepartmentRepository(session)
        self._contacts = contact_repository or EmployerContactRepository(session)
        self._activity = activity_repository or EmployerActivityRepository(session)

    # --- Favourite / visa notes ---

    def favourite(self, *, user_id: uuid.UUID, employer_id: uuid.UUID):
        profile = self._profiles.get_or_create(user_id=user_id, employer_id=employer_id)
        return self._profiles.update(profile, is_favourite=True)

    def unfavourite(self, *, user_id: uuid.UUID, employer_id: uuid.UUID):
        profile = self._profiles.get_or_create(user_id=user_id, employer_id=employer_id)
        return self._profiles.update(profile, is_favourite=False)

    def update_visa_notes(self, *, user_id: uuid.UUID, employer_id: uuid.UUID, notes: str | None):
        profile = self._profiles.get_or_create(user_id=user_id, employer_id=employer_id)
        return self._profiles.update(profile, visa_sponsorship_notes=notes or None)

    def get_profile(self, *, user_id: uuid.UUID, employer_id: uuid.UUID):
        return self._profiles.find(user_id=user_id, employer_id=employer_id)

    def list_favourite_employer_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        return {profile.employer_id for profile in self._profiles.list_favourites_for_user(user_id)}

    # --- Departments (shared reference data) ---

    def add_department(self, *, employer_id: uuid.UUID, name: str, location: str | None = None):
        return self._departments.create(employer_id=employer_id, name=name, location=location)

    def list_departments(self, employer_id: uuid.UUID):
        return self._departments.list_for_employer(employer_id)

    def remove_department(self, department_id: uuid.UUID) -> None:
        department = self._departments.get(department_id)
        if department is None:
            raise ValueError(f"No department {department_id}")
        self._departments.delete(department)

    # --- Recruiter contacts (per-candidate) ---

    def add_contact(
        self,
        *,
        user_id: uuid.UUID,
        employer_id: uuid.UUID,
        name: str,
        role: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        notes: str | None = None,
        department_id: uuid.UUID | None = None,
    ):
        return self._contacts.create(
            employer_id=employer_id,
            user_id=user_id,
            name=name,
            role=role,
            email=email,
            phone=phone,
            notes=notes,
            department_id=department_id,
        )

    def list_contacts(self, *, user_id: uuid.UUID, employer_id: uuid.UUID):
        return self._contacts.list_for_employer(user_id=user_id, employer_id=employer_id)

    def remove_contact(self, contact_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        contact = self._contacts.get(contact_id)
        if contact is None or contact.user_id != user_id:
            raise ValueError(f"No contact {contact_id} visible to user {user_id}")
        self._contacts.delete(contact)

    # --- Activity timeline: notes + communication history ---

    def add_note(self, *, user_id: uuid.UUID, employer_id: uuid.UUID, body: str, occurred_at: datetime | None = None):
        return self._activity.create(
            employer_id=employer_id,
            user_id=user_id,
            entry_type=ActivityEntryType.NOTE.value,
            body=body,
            occurred_at=occurred_at or utc_now(),
        )

    def add_communication(
        self,
        *,
        user_id: uuid.UUID,
        employer_id: uuid.UUID,
        channel: CommunicationChannel,
        body: str,
        occurred_at: datetime | None = None,
        contact_id: uuid.UUID | None = None,
    ):
        return self._activity.create(
            employer_id=employer_id,
            user_id=user_id,
            entry_type=ActivityEntryType.COMMUNICATION.value,
            body=body,
            occurred_at=occurred_at or utc_now(),
            contact_id=contact_id,
            channel=channel.value,
        )

    def list_activity(
        self, *, user_id: uuid.UUID, employer_id: uuid.UUID, entry_type: ActivityEntryType | None = None
    ):
        return self._activity.list_for_employer(
            user_id=user_id, employer_id=employer_id, entry_type=entry_type.value if entry_type else None
        )

    def remove_activity_entry(self, entry_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        entry = self._activity.get(entry_id)
        if entry is None or entry.user_id != user_id:
            raise ValueError(f"No activity entry {entry_id} visible to user {user_id}")
        self._activity.delete(entry)
