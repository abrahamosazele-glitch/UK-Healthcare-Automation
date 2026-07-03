"""
One department/site under an `Employer` (e.g. "Emergency Department" at
"Riverside NHS Foundation Trust", located "London - Main Hospital Site").

Shared reference data, like `Employer`/`Job` — **not** user-scoped. An NHS
Trust's departmental structure is a fact about the organization, not about
any one candidate's relationship with it (unlike `EmployerContact`/
`EmployerActivityLogEntry`, which are each candidate's own CRM data).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.employer import Employer


class EmployerDepartment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "employer_departments"

    employer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))

    employer: Mapped["Employer"] = relationship(back_populates="departments")

    def __repr__(self) -> str:
        return f"<EmployerDepartment {self.name!r} @ employer={self.employer_id}>"
