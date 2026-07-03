"""
A recruiter/HR contact at an employer — one candidate's personal contact
book, not a shared directory. Two candidates who both apply to the same
Trust build up their own independent contact lists; nothing here is
visible across users. Optionally linked to an `EmployerDepartment` (the
department this contact belongs to), which *is* shared reference data.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.employer import Employer
    from job_automation.database.models.employer_department import EmployerDepartment
    from job_automation.database.models.user import User


class EmployerContact(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "employer_contacts"

    employer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: removing a department shouldn't delete a
    # candidate's record of the person they spoke to there.
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employer_departments.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)

    employer: Mapped["Employer"] = relationship(back_populates="contacts")
    user: Mapped["User"] = relationship(back_populates="employer_contacts")
    department: Mapped["EmployerDepartment | None"] = relationship()

    def __repr__(self) -> str:
        return f"<EmployerContact {self.name!r} @ employer={self.employer_id}>"
