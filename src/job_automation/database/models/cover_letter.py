"""
A generated cover letter document.

Deliberately has no direct `user_id`/relationship to `User` — per the given
schema, `CoverLetter` only relates to `Application` (unlike `CV`, which is
explicitly owned by a `User`). A cover letter's owner is reachable via
`cover_letter.applications[0].user` once it's attached to an application.
This means a `CoverLetter` row should be created in the same flow as the
`Application` it belongs to, or "list all of a user's cover letters" needs a
join through `Application` rather than a direct query. If standalone
cover-letter management (independent of any single application) turns out to
be needed later, add a `user_id` FK here the same way `CV` has one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application import Application


class CoverLetter(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "cover_letters"

    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))

    applications: Mapped[list["Application"]] = relationship(
        back_populates="cover_letter", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<CoverLetter {self.label or self.id}>"
