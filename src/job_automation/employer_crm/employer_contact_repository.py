"""
Persists `EmployerContact` rows — one candidate's personal contact book.
Pure data access, following the same repository pattern as every other
repository in this project.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.employer_contact import EmployerContact


class EmployerContactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, employer_id: uuid.UUID, user_id: uuid.UUID, **fields: Any) -> EmployerContact:
        contact = EmployerContact(employer_id=employer_id, user_id=user_id, **fields)
        self._session.add(contact)
        self._session.flush()
        return contact

    def get(self, contact_id: uuid.UUID) -> EmployerContact | None:
        return self._session.get(EmployerContact, contact_id)

    def list_for_employer(self, *, user_id: uuid.UUID, employer_id: uuid.UUID) -> list[EmployerContact]:
        return list(
            self._session.scalars(
                select(EmployerContact)
                .where(EmployerContact.user_id == user_id, EmployerContact.employer_id == employer_id)
                .order_by(EmployerContact.name)
            )
        )

    def update(self, contact: EmployerContact, **fields: Any) -> EmployerContact:
        for key, value in fields.items():
            setattr(contact, key, value)
        self._session.flush()
        return contact

    def delete(self, contact: EmployerContact) -> None:
        self._session.delete(contact)
        self._session.flush()
