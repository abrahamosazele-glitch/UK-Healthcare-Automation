"""
Data access for Employer rows.

`EmployerFilter`/`search()` (added for the Employer & Application CRM
milestone) is a purely additive extension, following the exact same
pattern `JobRepository.search()`/`JobFilter` established for the Job
Management milestone: an optional `user_id` triggers a `LEFT OUTER JOIN`
onto `EmployerProfile` so `favourite_only` can be applied, and every
pre-existing method is untouched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.employer import Employer
from job_automation.database.models.employer_profile import EmployerProfile

_SORTABLE_COLUMNS = {
    "name": Employer.name,
    "created_at": Employer.created_at,
}


@dataclass(frozen=True)
class EmployerFilter:
    search: str | None = None
    employer_type: str | None = None
    user_id: uuid.UUID | None = None
    favourite_only: bool = False
    sort_by: str = "name"
    sort_descending: bool = False


class EmployerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, name: str, *, website: str | None = None) -> Employer:
        """Find an Employer by exact name, or create one. If an existing
        Employer has no website on file and one is now known, fill it in —
        but never overwrite a website already recorded (a job scraper isn't
        an authoritative source for an employer's own site)."""
        employer = self._session.scalars(select(Employer).where(Employer.name == name)).first()
        if employer is None:
            employer = Employer(name=name, website=website)
            self._session.add(employer)
            self._session.flush()  # assigns employer.id without committing
        elif website and not employer.website:
            employer.website = website
        return employer

    def get(self, employer_id: uuid.UUID) -> Employer | None:
        return self._session.get(Employer, employer_id)

    def list_all(self) -> list[Employer]:
        return list(self._session.scalars(select(Employer).order_by(Employer.name)))

    def search(self, filters: EmployerFilter) -> list[Employer]:
        """Filtered, sorted employer listing for the CRM's Employers page.
        Every filter is optional — an unset field simply isn't applied."""
        stmt = select(Employer)

        if filters.user_id is not None and filters.favourite_only:
            stmt = stmt.join(
                EmployerProfile,
                (EmployerProfile.employer_id == Employer.id) & (EmployerProfile.user_id == filters.user_id),
            ).where(EmployerProfile.is_favourite.is_(True))

        if filters.search:
            stmt = stmt.where(Employer.name.ilike(f"%{filters.search}%"))
        if filters.employer_type:
            stmt = stmt.where(Employer.employer_type == filters.employer_type)

        sort_column = _SORTABLE_COLUMNS.get(filters.sort_by, Employer.name)
        stmt = stmt.order_by(sort_column.desc() if filters.sort_descending else sort_column.asc())

        return list(self._session.scalars(stmt))
