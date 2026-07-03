"""One recorded status transition for an ApplicationWorkflowRecord."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application_workflow_record import (
        ApplicationWorkflowRecord,
    )


class WorkflowStatusHistoryRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "workflow_status_history"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    workflow: Mapped["ApplicationWorkflowRecord"] = relationship(back_populates="status_history")

    def __repr__(self) -> str:
        return f"<WorkflowStatusHistoryRecord {self.from_status} -> {self.to_status}>"
