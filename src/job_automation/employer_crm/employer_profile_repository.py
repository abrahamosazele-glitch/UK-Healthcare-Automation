"""
Persists `EmployerProfile` rows. Pure data access — no favourite/visa-note
business logic (that's `employer_crm_service.py`'s job), following the
same repository pattern as every other repository in this project.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.employer_profile import EmployerProfile


class EmployerProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, *, user_id: uuid.UUID, employer_id: uuid.UUID) -> EmployerProfile | None:
        return self._session.scalars(
            select(EmployerProfile).where(
                EmployerProfile.user_id == user_id, EmployerProfile.employer_id == employer_id
            )
        ).first()

    def get_or_create(self, *, user_id: uuid.UUID, employer_id: uuid.UUID) -> EmployerProfile:
        existing = self.find(user_id=user_id, employer_id=employer_id)
        if existing is not None:
            return existing
        profile = EmployerProfile(user_id=user_id, employer_id=employer_id, is_favourite=False)
        self._session.add(profile)
        self._session.flush()
        return profile

    def update(self, profile: EmployerProfile, **fields: Any) -> EmployerProfile:
        for key, value in fields.items():
            setattr(profile, key, value)
        self._session.flush()
        return profile

    def list_favourites_for_user(self, user_id: uuid.UUID) -> list[EmployerProfile]:
        return list(
            self._session.scalars(
                select(EmployerProfile).where(
                    EmployerProfile.user_id == user_id, EmployerProfile.is_favourite.is_(True)
                )
            )
        )
