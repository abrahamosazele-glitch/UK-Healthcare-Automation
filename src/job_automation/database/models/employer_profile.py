"""
One candidate's personal CRM relationship with one employer: is this
employer favourited, and any personal research notes on whether they
sponsor visas. `(employer_id, user_id)` is unique — created on first
favourite/visa-note edit via
`employer_crm.employer_profile_repository.EmployerProfileRepository
.get_or_create()`, the same lazy-creation pattern `SavedJob` established
for jobs.

Deliberately the employer-level counterpart to `SavedJob`: `Employer` is
shared reference data (like `Job`), but *caring about* an employer —
favouriting it, writing "they historically sponsor Tier 2 visas" — is a
per-candidate fact, not a fact about the employer itself. Kept as its own
small anchor row (rather than bolting `is_favourite`/`visa_sponsorship_notes`
onto `Employer` directly) for the same reason `SavedJob` isn't columns on
`Job`: `Employer` has no `user_id` and must stay shared.

Contacts and notes/communication history live in their own tables
(`EmployerContact`, `EmployerActivityLogEntry`) rather than as JSON blobs
here, since both are naturally many-per-employer, independently
meaningful entries (matching why `JobReminder` got its own table instead
of a JSON blob on `SavedJob`).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.employer import Employer
    from job_automation.database.models.user import User


class EmployerProfile(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "employer_profiles"
    __table_args__ = (
        UniqueConstraint("employer_id", "user_id", name="uq_employer_profiles_employer_id_user_id"),
    )

    employer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    visa_sponsorship_notes: Mapped[str | None] = mapped_column(Text)

    employer: Mapped["Employer"] = relationship(back_populates="candidate_profiles")
    user: Mapped["User"] = relationship(back_populates="employer_profiles")

    def __repr__(self) -> str:
        return f"<EmployerProfile employer={self.employer_id} user={self.user_id} favourite={self.is_favourite}>"
