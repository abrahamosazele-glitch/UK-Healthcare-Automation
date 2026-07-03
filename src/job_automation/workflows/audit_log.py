"""
Records an audit-trail entry for a workflow action — a thin wrapper around
`WorkflowRepository.add_audit_log()` giving the rest of the package (and
callers) one obvious place to log "something happened" without needing to
know the repository's exact method signature.
"""

from __future__ import annotations

from job_automation.workflows.workflow_repository import WorkflowRepository


class AuditLog:
    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def record(self, workflow, *, action: str, details: dict | None = None):
        return self._repository.add_audit_log(workflow, action=action, details=details or {})

    def history(self, workflow_id):
        return self._repository.get_audit_log(workflow_id)
