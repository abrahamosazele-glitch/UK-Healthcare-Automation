"""
A persisted AI-generated document draft (supporting statement, cover
letter, or application answer) — always a draft pending human review, never
an auto-submitted application.

Named `GeneratedDocumentRecord`, not `GeneratedDocument`, to avoid colliding
with the domain dataclass of that name in `job_automation.documents
.document_models` — same naming convention already used for
`CandidateProfileRecord` vs. `profile.candidate_profile.CandidateProfile`.

`document_type`/`status` are plain strings, not `sa_enum(...)` columns —
every other model file in this package only imports from `database.*`, and
keeping that invariant here (rather than importing `documents
.document_models.DocumentType` into the database layer) avoids a downward
dependency from core ORM schema onto an application feature package. The
canonical enum values live in `documents.document_models`; this column just
stores their `.value`.

This is deliberately separate from the existing `CoverLetter` model (which
represents a finished file with a `file_path`, referenced by
`Application.cover_letter_id`) — see docs/DOCUMENT_GENERATION.md for why a
draft-with-review-workflow is a different concept from a finished,
application-attached file, and how the two could connect later.

`workflow_id` (added for the Application Workflow Management milestone)
links a document to the `ApplicationWorkflowRecord` it was generated for.
Every regeneration creates a *new* row (this model has never supported
in-place content updates — `document_repository.create()` always inserts),
so "track document versions" falls out of this link for free: querying all
documents for a `workflow_id` ordered by `created_at` gives the full
version history, newest last.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application_workflow_record import (
        ApplicationWorkflowRecord,
    )
    from job_automation.database.models.job import Job
    from job_automation.database.models.user import User


class GeneratedDocumentRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "generated_documents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable + SET NULL (not CASCADE): a draft is the candidate's own
    # content and should survive even if the job listing it was tailored to
    # is later deleted — mirrors Application.cv_id/cover_letter_id's
    # "history survives, just disassociated" pattern.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("application_workflows.id", ondelete="SET NULL"), index=True
    )
    document_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Only set for document_type == "application_answer".
    question: Mapped[str | None] = mapped_column(Text)
    #: List of {"severity", "message", "claim"} dicts from DocumentValidator.
    validation_issues: Mapped[list[dict] | None] = mapped_column(JSON)
    #: Optional free-text note left by a human reviewer on approve/reject.
    review_notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="generated_documents")
    job: Mapped["Job | None"] = relationship(back_populates="generated_documents")
    workflow: Mapped["ApplicationWorkflowRecord | None"] = relationship(back_populates="documents")

    def __repr__(self) -> str:
        return f"<GeneratedDocumentRecord {self.document_type} status={self.status}>"
