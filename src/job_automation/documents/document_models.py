"""
Value objects for the document-generation subsystem.

Deliberately dependency-free (no SQLAlchemy, no LLM SDK, no `ai`/`profile`
imports) — the same design used for `ai.matching_models` and
`profile.candidate_profile`, so `GeneratedDocument` is usable and testable
without a database session or a live LLM connection.

`DocumentType`/`DocumentStatus` are the canonical enum definitions; the ORM
side (`database.models.generated_document_record.GeneratedDocumentRecord`)
stores their `.value` as a plain string rather than importing these enums,
to keep the database layer independent of this application package (see
that model's docstring).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class DocumentType(str, enum.Enum):
    SUPPORTING_STATEMENT = "supporting_statement"
    COVER_LETTER = "cover_letter"
    APPLICATION_ANSWER = "application_answer"
    #: Added for the Anthropic AI Integration milestone. Both reuse the
    #: exact same generate -> validate -> draft/needs_review -> approve/
    #: reject -> export pipeline as the three types above — no new
    #: persistence, review UI, or export logic was needed.
    INTERVIEW_PREP = "interview_prep"
    SKILLS_GAP_ANALYSIS = "skills_gap_analysis"
    #: Added for the AI Career Assistant milestone — the optional,
    #: explicit-click, real-LLM-backed narrative companion to
    #: `career_assistant.CareerAssistantService`'s always-on, zero-cost
    #: rule-based insight. Same reuse-the-existing-pipeline reasoning as
    #: `INTERVIEW_PREP`/`SKILLS_GAP_ANALYSIS` above.
    CAREER_INSIGHT = "career_insight"


class DocumentStatus(str, enum.Enum):
    #: Freshly generated, no validation concerns — still needs a human look
    #: before use, but nothing flagged.
    DRAFT = "draft"
    #: Freshly generated, but DocumentValidator found at least one issue —
    #: needs closer human review than a plain DRAFT.
    NEEDS_REVIEW = "needs_review"
    #: A human has reviewed and approved this draft for use.
    APPROVED = "approved"
    #: A human has reviewed and rejected this draft (e.g. needs regenerating).
    REJECTED = "rejected"


@dataclass(frozen=True)
class DocumentValidationIssue:
    severity: str  # "warning" | "error"
    message: str
    claim: str | None = None  # the specific unsupported claim text, if identifiable

    def to_dict(self) -> dict:
        return {"severity": self.severity, "message": self.message, "claim": self.claim}

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentValidationIssue":
        return cls(severity=data["severity"], message=data["message"], claim=data.get("claim"))


@dataclass
class GeneratedDocument:
    """A generated draft, not yet (necessarily) persisted — generators
    return this; `DocumentService` validates, assigns a status, and saves it
    as a `GeneratedDocumentRecord`. Has no database id of its own, mirroring
    `ai.matching_models.MatchResult`'s same design."""

    document_type: DocumentType
    content: str
    status: DocumentStatus = DocumentStatus.DRAFT
    question: str | None = None  # only set for APPLICATION_ANSWER
    job_title: str | None = None
    employer: str | None = None
    validation_issues: list[DocumentValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "document_type": self.document_type.value,
            "content": self.content,
            "status": self.status.value,
            "question": self.question,
            "job_title": self.job_title,
            "employer": self.employer,
            "validation_issues": [issue.to_dict() for issue in self.validation_issues],
        }
