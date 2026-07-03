"""
The main entry point for the application workflow subsystem: composes
`WorkflowRepository`, `StatusManager`, `ReviewService`, `ChecklistService`,
and `AuditLog` into the full journey from a new job match to a
ready-to-apply (and beyond: applied/interview/offer/closed) status —
mirroring the orchestrator role `MatchingService`/`ProfileService`/
`DocumentService` play for their own subsystems.

**No method on this class submits, sends, or applies to anything.**
`mark_applied()` only *records* that the candidate applied — it must be
called explicitly by a human (or a future UI action they take), and nothing
in this codebase's scraper, browser, or scheduler layers calls it. This is
what "prevent automatic submission" means at the code level: the capability
to move a workflow to `APPLIED` exists, but no automated code path anywhere
exercises it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from job_automation.profile.candidate_profile import CandidateProfile
from job_automation.workflows.audit_log import AuditLog
from job_automation.workflows.checklist_service import ChecklistService
from job_automation.workflows.review_service import ReviewService
from job_automation.workflows.status_manager import StatusManager
from job_automation.workflows.workflow_models import ApplicationChecklist, WorkflowStatus
from job_automation.workflows.workflow_repository import WorkflowRepository
from job_automation.utils.logger import logger


class WorkflowService:
    def __init__(
        self,
        session: Session,
        *,
        repository: WorkflowRepository | None = None,
        status_manager: StatusManager | None = None,
        review_service: ReviewService | None = None,
        checklist_service: ChecklistService | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        self._repository = repository or WorkflowRepository(session)
        self._status_manager = status_manager or StatusManager(self._repository)
        self._audit_log = audit_log or AuditLog(self._repository)
        self._review_service = review_service or ReviewService(self._status_manager, self._audit_log)
        self._checklist_service = checklist_service or ChecklistService()

    def start_workflow(
        self, *, user_id: uuid.UUID, job_id: uuid.UUID, job_match_id: uuid.UUID | None = None
    ):
        """Create a new workflow at NEW_MATCH, or return the existing one
        for this (user, job) pair — never a second workflow for the same
        job/candidate combination."""
        existing = self._repository.find_by_job_and_user(job_id, user_id)
        if existing is not None:
            return existing

        workflow = self._repository.create(
            user_id=user_id, job_id=job_id, job_match_id=job_match_id, status=WorkflowStatus.NEW_MATCH.value
        )
        self._repository.add_status_history(workflow, from_status=None, to_status=WorkflowStatus.NEW_MATCH.value)
        self._audit_log.record(workflow, action="workflow_started", details={"job_id": str(job_id)})
        logger.info("Started workflow {} for job {} / user {}", workflow.id, job_id, user_id)
        return workflow

    def attach_document(self, workflow, document):
        """Link a freshly-generated document to this workflow (see
        `GeneratedDocumentRecord.workflow_id`), advancing to
        DOCUMENTS_GENERATED if the workflow was at NEW_MATCH (first
        generation) or REJECTED (regenerating after a rejection — the state
        machine's explicit "reject then retry" loop)."""
        self._repository.link_document(document, workflow)
        self._audit_log.record(
            workflow,
            action="document_attached",
            details={"document_id": str(document.id), "document_type": document.document_type},
        )
        if WorkflowStatus(workflow.status) in (WorkflowStatus.NEW_MATCH, WorkflowStatus.REJECTED):
            workflow = self._status_manager.transition(workflow, WorkflowStatus.DOCUMENTS_GENERATED)
        return workflow

    def submit_for_review(self, workflow):
        updated = self._status_manager.transition(workflow, WorkflowStatus.NEEDS_REVIEW)
        self._audit_log.record(workflow, action="submitted_for_review")
        return updated

    def approve(self, workflow, *, reviewer_notes: str | None = None):
        return self._review_service.approve(workflow, reviewer_notes=reviewer_notes)

    def reject(self, workflow, *, reviewer_notes: str | None = None):
        return self._review_service.reject(workflow, reviewer_notes=reviewer_notes)

    def build_checklist(self, workflow, profile: CandidateProfile) -> ApplicationChecklist:
        documents = self._repository.get_documents(workflow.id)
        return self._checklist_service.build_checklist(profile, documents)

    def mark_ready_to_apply(self, workflow):
        updated = self._status_manager.transition(workflow, WorkflowStatus.READY_TO_APPLY)
        self._audit_log.record(workflow, action="marked_ready_to_apply")
        return updated

    def mark_applied(self, workflow, *, note: str | None = None):
        """Explicitly, manually record that the candidate applied outside
        this system. Never called by any scraper, scheduler, or automated
        process in this codebase — see this module's docstring."""
        updated = self._status_manager.transition(workflow, WorkflowStatus.APPLIED, note=note)
        self._audit_log.record(workflow, action="marked_applied", details={"note": note})
        return updated

    def mark_interview(self, workflow, *, note: str | None = None):
        updated = self._status_manager.transition(workflow, WorkflowStatus.INTERVIEW, note=note)
        self._audit_log.record(workflow, action="marked_interview", details={"note": note})
        return updated

    def mark_offer(self, workflow, *, note: str | None = None):
        updated = self._status_manager.transition(workflow, WorkflowStatus.OFFER, note=note)
        self._audit_log.record(workflow, action="marked_offer", details={"note": note})
        return updated

    def close(self, workflow, *, reason: str | None = None):
        updated = self._status_manager.transition(workflow, WorkflowStatus.CLOSED, note=reason)
        self._audit_log.record(workflow, action="closed", details={"reason": reason})
        return updated

    def get_status_history(self, workflow_id: uuid.UUID):
        return self._repository.get_status_history(workflow_id)

    def get_audit_log(self, workflow_id: uuid.UUID):
        return self._audit_log.history(workflow_id)

    def list_for_user(self, user_id: uuid.UUID):
        return self._repository.list_for_user(user_id)

    def get_documents(self, workflow_id: uuid.UUID):
        """Every document ever linked to this workflow, oldest first — the
        full version history (see GeneratedDocumentRecord's docstring)."""
        return self._repository.get_documents(workflow_id)
