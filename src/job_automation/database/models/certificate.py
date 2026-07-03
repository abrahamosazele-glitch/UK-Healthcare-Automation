"""
A candidate's certification/qualification record (DBS check, Manual
Handling, Basic Life Support, etc.).

Note: the task's relationship list didn't specify a relationship for
`Certificate`, but a certificate with no owner isn't a usable record — it
mirrors the `certifications` list already present in
`data/candidate_profile.example.json`. Added `user_id` + a `User.certificates`
back-reference as the one deliberate addition beyond the given schema.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.user import User


class Certificate(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "certificates"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuing_body: Mapped[str | None] = mapped_column(String(255))
    issued_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date, index=True)
    file_path: Mapped[str | None] = mapped_column(String(1000))

    user: Mapped["User"] = relationship(back_populates="certificates")

    def __repr__(self) -> str:
        return f"<Certificate {self.name!r}>"
