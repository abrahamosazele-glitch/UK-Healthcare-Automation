"""An interview scheduled as part of one application."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.enums import InterviewOutcome, InterviewType, sa_enum
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application import Application


class Interview(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "interviews"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(), index=True)
    interview_type: Mapped[InterviewType | None] = mapped_column(sa_enum(InterviewType))
    location: Mapped[str | None] = mapped_column(String(500))
    outcome: Mapped[InterviewOutcome] = mapped_column(
        sa_enum(InterviewOutcome), default=InterviewOutcome.SCHEDULED, nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    application: Mapped["Application"] = relationship(back_populates="interviews")

    def __repr__(self) -> str:
        return f"<Interview application={self.application_id} outcome={self.outcome}>"
