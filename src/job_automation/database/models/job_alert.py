"""A saved search a user wants to be notified about as new matching jobs appear."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.enums import JobAlertFrequency, sa_enum
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.user import User


class JobAlert(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "job_alerts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    keywords: Mapped[str] = mapped_column(String(500), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    frequency: Mapped[JobAlertFrequency] = mapped_column(
        sa_enum(JobAlertFrequency), default=JobAlertFrequency.DAILY, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime())

    user: Mapped["User"] = relationship(back_populates="job_alerts")

    def __repr__(self) -> str:
        return f"<JobAlert {self.keywords!r}>"
