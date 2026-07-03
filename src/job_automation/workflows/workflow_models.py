"""
Value objects for the application workflow subsystem.

Deliberately dependency-free — same design as `ai.matching_models`,
`profile.candidate_profile`, and `documents.document_models`: pure
dataclasses/enums, no SQLAlchemy, no other package imports. The canonical
`WorkflowStatus` values live here; the ORM side
(`database.models.application_workflow_record.ApplicationWorkflowRecord`)
stores `.value` as a plain string rather than importing this enum, for the
same reason `GeneratedDocumentRecord` doesn't import `DocumentType`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class WorkflowStatus(str, enum.Enum):
    #: A job has been matched (JobMatch computed) but nothing else has happened yet.
    NEW_MATCH = "new_match"
    #: At least one document (supporting statement / cover letter / answer) has been generated.
    DOCUMENTS_GENERATED = "documents_generated"
    #: Submitted for human review.
    NEEDS_REVIEW = "needs_review"
    #: A human approved the generated documents.
    APPROVED = "approved"
    #: A human rejected the generated documents — can regenerate and resubmit.
    REJECTED = "rejected"
    #: Approved and the checklist is complete — ready for the candidate to apply (manually).
    READY_TO_APPLY = "ready_to_apply"
    #: The candidate has manually applied (recorded, never triggered automatically).
    APPLIED = "applied"
    #: The candidate has been invited to interview.
    INTERVIEW = "interview"
    #: An offer has been made.
    OFFER = "offer"
    #: Terminal — the workflow is no longer being pursued (withdrawn, rejected by employer, offer declined, etc.).
    CLOSED = "closed"


@dataclass(frozen=True)
class StatusHistoryEntry:
    from_status: WorkflowStatus | None
    to_status: WorkflowStatus
    note: str | None = None
    changed_at: datetime | None = None


@dataclass(frozen=True)
class AuditLogEntry:
    action: str
    details: dict = field(default_factory=dict)
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class ChecklistItem:
    name: str
    is_complete: bool
    detail: str | None = None


@dataclass(frozen=True)
class ApplicationChecklist:
    items: tuple[ChecklistItem, ...] = ()

    @property
    def is_complete(self) -> bool:
        return all(item.is_complete for item in self.items)

    @property
    def missing_items(self) -> tuple[ChecklistItem, ...]:
        return tuple(item for item in self.items if not item.is_complete)
