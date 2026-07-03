"""
Application Workflow Management: the internal journey from a matched job to
a reviewed, ready-to-apply status and beyond (applied/interview/offer/
closed) — connecting Job, JobMatch, CandidateProfile, and generated
documents built in earlier milestones.

**No automatic application submission anywhere in this package.** Every
status transition (including the manual "APPLIED" record) requires an
explicit caller action; there is no scraper, browser-automation, or
scheduled-job code path in this codebase that calls into `WorkflowService`.
See docs/APPLICATION_WORKFLOW.md.
"""

from job_automation.workflows.application_workflow import ApplicationWorkflow, InvalidTransitionError
from job_automation.workflows.audit_log import AuditLog
from job_automation.workflows.checklist_service import ChecklistService
from job_automation.workflows.review_service import ReviewService
from job_automation.workflows.status_manager import StatusManager
from job_automation.workflows.workflow_models import (
    ApplicationChecklist,
    AuditLogEntry,
    ChecklistItem,
    StatusHistoryEntry,
    WorkflowStatus,
)
from job_automation.workflows.workflow_repository import WorkflowRepository
from job_automation.workflows.workflow_service import WorkflowService

__all__ = [
    "ApplicationWorkflow",
    "InvalidTransitionError",
    "AuditLog",
    "ChecklistService",
    "ReviewService",
    "StatusManager",
    "ApplicationChecklist",
    "AuditLogEntry",
    "ChecklistItem",
    "StatusHistoryEntry",
    "WorkflowStatus",
    "WorkflowRepository",
    "WorkflowService",
]
