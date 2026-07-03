"""
One categorized note on an interview — before, during, or after (questions
asked, my answers, recruiter feedback, things to improve, salary
discussed, next steps). Its own table, not a JSON blob on
`InterviewRecord`, matching this project's established "own table for an
append-only, independently meaningful, timestamped log" convention
(`WorkflowStatusHistoryRecord`, `EmployerActivityLogEntry`) rather than
the "JSON blob" convention reserved for small, never-independently-queried
data.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.interview_record import InterviewRecord


class InterviewNote(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "interview_notes"

    interview_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: `NoteCategory.value` — canonical enum lives in
    #: `interviews.interview_models`, same reasoning as
    #: `InterviewRecord.status`.
    category: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    interview: Mapped["InterviewRecord"] = relationship(back_populates="interview_notes")

    def __repr__(self) -> str:
        return f"<InterviewNote {self.category} interview={self.interview_id}>"
