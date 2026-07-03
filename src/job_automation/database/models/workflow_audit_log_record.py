"""One immutable audit-log entry for an ApplicationWorkflowRecord."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application_workflow_record import (
        ApplicationWorkflowRecord,
    )


class WorkflowAuditLogRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "workflow_audit_logs"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    details: Mapped[dict | None] = mapped_column(JSON)

    workflow: Mapped["ApplicationWorkflowRecord"] = relationship(back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<WorkflowAuditLogRecord {self.action!r}>"
