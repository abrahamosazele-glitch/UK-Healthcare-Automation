"""
The application journey for one (user, job) pair — connects `Job`,
`JobMatch`, the user's `CandidateProfile` (transitively via `user_id`, which
`CandidateProfileRecord` is also keyed on), and `GeneratedDocumentRecord`
(via that model's `workflow_id`) into a single trackable aggregate.

Named `ApplicationWorkflowRecord` (not `ApplicationWorkflow`) to avoid
colliding with the domain state-machine class of that name in
`job_automation.workflows.application_workflow` — the now-familiar
`...Record` suffix convention already used for `CandidateProfileRecord` and
`GeneratedDocumentRecord`.

`status` is a plain `String` column, not `sa_enum(...)` — same reasoning as
`GeneratedDocumentRecord.document_type`/`.status`: every file in
`database/models/` only imports from `database.*`, and the canonical
`WorkflowStatus` enum lives in the domain layer
(`workflows.workflow_models`), not here.

Status history and audit log entries are **not** JSON blobs on this record,
unlike `JobMatch.analysis`/`CandidateProfileRecord.data` — they get their
own tables (`WorkflowStatusHistoryRecord`, `WorkflowAuditLogRecord`) because
an append-only historical/audit trail is exactly the kind of data that
should be immutable, independently queryable, and not editable via a normal
UPDATE on its parent row the way an evolving JSON blob is.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
    from job_automation.database.models.job import Job
    from job_automation.database.models.job_match import JobMatch
    from job_automation.database.models.user import User
    from job_automation.database.models.workflow_audit_log_record import WorkflowAuditLogRecord
    from job_automation.database.models.workflow_status_history_record import (
        WorkflowStatusHistoryRecord,
    )


class ApplicationWorkflowRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "application_workflows"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_application_workflows_user_id_job_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # No ondelete/cascade — same protective reasoning as Application.job_id:
    # a Job shouldn't be deletable while a workflow (application history)
    # references it.
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    # Optional: a workflow can outlive the JobMatch that started it (e.g. if
    # matching is re-run and the old JobMatch row is superseded).
    job_match_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_matches.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    user: Mapped["User"] = relationship(back_populates="application_workflows")
    job: Mapped["Job"] = relationship(back_populates="application_workflows")
    job_match: Mapped["JobMatch | None"] = relationship(back_populates="application_workflows")
    documents: Mapped[list["GeneratedDocumentRecord"]] = relationship(back_populates="workflow")
    status_history: Mapped[list["WorkflowStatusHistoryRecord"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", passive_deletes=True,
        order_by="WorkflowStatusHistoryRecord.created_at",
    )
    audit_logs: Mapped[list["WorkflowAuditLogRecord"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", passive_deletes=True,
        order_by="WorkflowAuditLogRecord.created_at",
    )

    def __repr__(self) -> str:
        return f"<ApplicationWorkflowRecord job={self.job_id} user={self.user_id} status={self.status}>"
