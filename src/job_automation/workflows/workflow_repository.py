"""
Persists `ApplicationWorkflowRecord` plus its status history and audit log
entries, and manages the link between a workflow and its
`GeneratedDocumentRecord`s. Pure data access — no business logic (deciding
*whether* a transition is valid is `application_workflow.py`'s job; deciding
*when* to make one is `status_manager.py`'s). Follows the same repository
pattern as every other repository in this project.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.application_workflow_record import ApplicationWorkflowRecord
from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
from job_automation.database.models.workflow_audit_log_record import WorkflowAuditLogRecord
from job_automation.database.models.workflow_status_history_record import (
    WorkflowStatusHistoryRecord,
)


class WorkflowRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        """Exposed so `StatusManager` (which is only constructed with a
        repository, not a session directly) can publish a
        `WORKFLOW_UPDATED` event on the same session/transaction as the
        transition it just performed — added for the Notification & Event
        System milestone."""
        return self._session

    # --- ApplicationWorkflowRecord ---

    def create(self, **fields: Any) -> ApplicationWorkflowRecord:
        record = ApplicationWorkflowRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    def get(self, workflow_id: uuid.UUID) -> ApplicationWorkflowRecord | None:
        return self._session.get(ApplicationWorkflowRecord, workflow_id)

    def find_by_job_and_user(
        self, job_id: uuid.UUID, user_id: uuid.UUID
    ) -> ApplicationWorkflowRecord | None:
        return self._session.scalars(
            select(ApplicationWorkflowRecord).where(
                ApplicationWorkflowRecord.job_id == job_id, ApplicationWorkflowRecord.user_id == user_id
            )
        ).first()

    def list_for_user(self, user_id: uuid.UUID) -> list[ApplicationWorkflowRecord]:
        return list(
            self._session.scalars(
                select(ApplicationWorkflowRecord)
                .where(ApplicationWorkflowRecord.user_id == user_id)
                .order_by(ApplicationWorkflowRecord.created_at.desc())
            )
        )

    def update_status(self, workflow: ApplicationWorkflowRecord, status: str) -> ApplicationWorkflowRecord:
        workflow.status = status
        self._session.flush()
        return workflow

    # --- Status history ---

    def add_status_history(
        self,
        workflow: ApplicationWorkflowRecord,
        *,
        from_status: str | None,
        to_status: str,
        note: str | None = None,
    ) -> WorkflowStatusHistoryRecord:
        entry = WorkflowStatusHistoryRecord(
            workflow_id=workflow.id, from_status=from_status, to_status=to_status, note=note
        )
        self._session.add(entry)
        self._session.flush()
        return entry

    def get_status_history(self, workflow_id: uuid.UUID) -> list[WorkflowStatusHistoryRecord]:
        return list(
            self._session.scalars(
                select(WorkflowStatusHistoryRecord)
                .where(WorkflowStatusHistoryRecord.workflow_id == workflow_id)
                .order_by(WorkflowStatusHistoryRecord.created_at)
            )
        )

    # --- Audit log ---

    def add_audit_log(
        self, workflow: ApplicationWorkflowRecord, *, action: str, details: dict | None = None
    ) -> WorkflowAuditLogRecord:
        entry = WorkflowAuditLogRecord(workflow_id=workflow.id, action=action, details=details or {})
        self._session.add(entry)
        self._session.flush()
        return entry

    def get_audit_log(self, workflow_id: uuid.UUID) -> list[WorkflowAuditLogRecord]:
        return list(
            self._session.scalars(
                select(WorkflowAuditLogRecord)
                .where(WorkflowAuditLogRecord.workflow_id == workflow_id)
                .order_by(WorkflowAuditLogRecord.created_at)
            )
        )

    # --- Linked documents (version tracking) ---

    def link_document(
        self, document: GeneratedDocumentRecord, workflow: ApplicationWorkflowRecord
    ) -> GeneratedDocumentRecord:
        document.workflow_id = workflow.id
        self._session.flush()
        return document

    def get_documents(self, workflow_id: uuid.UUID) -> list[GeneratedDocumentRecord]:
        """Every document ever linked to this workflow, oldest first — the
        full version history (each regeneration is a new row; see
        GeneratedDocumentRecord's docstring)."""
        return list(
            self._session.scalars(
                select(GeneratedDocumentRecord)
                .where(GeneratedDocumentRecord.workflow_id == workflow_id)
                .order_by(GeneratedDocumentRecord.created_at)
            )
        )
