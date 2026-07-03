"""
Persists `EmployerDepartment` rows — shared reference data (not
user-scoped; see the model's docstring). Pure data access, following the
same repository pattern as every other repository in this project.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.employer_department import EmployerDepartment


class EmployerDepartmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, employer_id: uuid.UUID, name: str, location: str | None = None) -> EmployerDepartment:
        department = EmployerDepartment(employer_id=employer_id, name=name, location=location)
        self._session.add(department)
        self._session.flush()
        return department

    def get(self, department_id: uuid.UUID) -> EmployerDepartment | None:
        return self._session.get(EmployerDepartment, department_id)

    def list_for_employer(self, employer_id: uuid.UUID) -> list[EmployerDepartment]:
        return list(
            self._session.scalars(
                select(EmployerDepartment)
                .where(EmployerDepartment.employer_id == employer_id)
                .order_by(EmployerDepartment.name)
            )
        )

    def delete(self, department: EmployerDepartment) -> None:
        self._session.delete(department)
        self._session.flush()
