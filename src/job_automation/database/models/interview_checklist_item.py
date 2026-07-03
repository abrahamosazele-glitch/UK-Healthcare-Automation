"""
One preparation-checklist item for an interview (e.g. "Research employer",
"Prepare STAR examples"). `InterviewService.schedule()` seeds a default set
of items on creation (see that module's docstring for the full list); a
candidate can add/remove their own on top of the defaults.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.interview_record import InterviewRecord


class InterviewChecklistItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "interview_checklist_items"

    interview_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    interview: Mapped["InterviewRecord"] = relationship(back_populates="checklist_items")

    def __repr__(self) -> str:
        return f"<InterviewChecklistItem {self.label!r} done={self.is_complete}>"
