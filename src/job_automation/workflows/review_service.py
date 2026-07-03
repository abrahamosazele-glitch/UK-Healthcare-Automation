"""
The human review decision: approve or reject a workflow currently in
`NEEDS_REVIEW`. Built on `StatusManager` (which only knows generic
transitions) — this class owns the specific business meaning of a review
decision: recording *why* in the audit log, in addition to the bare status
change `StatusManager` already records in status history.
"""

from __future__ import annotations

from job_automation.workflows.audit_log import AuditLog
from job_automation.workflows.status_manager import StatusManager
from job_automation.workflows.workflow_models import WorkflowStatus


class ReviewService:
    def __init__(self, status_manager: StatusManager, audit_log: AuditLog) -> None:
        self._status_manager = status_manager
        self._audit_log = audit_log

    def approve(self, workflow, *, reviewer_notes: str | None = None):
        updated = self._status_manager.transition(workflow, WorkflowStatus.APPROVED, note=reviewer_notes)
        self._audit_log.record(workflow, action="approved", details={"reviewer_notes": reviewer_notes})
        return updated

    def reject(self, workflow, *, reviewer_notes: str | None = None):
        updated = self._status_manager.transition(workflow, WorkflowStatus.REJECTED, note=reviewer_notes)
        self._audit_log.record(workflow, action="rejected", details={"reviewer_notes": reviewer_notes})
        return updated
