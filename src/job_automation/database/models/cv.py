"""
A generated CV document belonging to a user.

A user can hold several CVs (e.g. different tailored versions), and the same
CV can be attached to more than one application, so the foreign key lives on
`Application` (`application.cv_id`) rather than here.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application import Application
    from job_automation.database.models.user import User


class CV(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "cvs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    is_base: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship(back_populates="cvs")
    applications: Mapped[list["Application"]] = relationship(
        back_populates="cv", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<CV {self.label or self.id}>"
