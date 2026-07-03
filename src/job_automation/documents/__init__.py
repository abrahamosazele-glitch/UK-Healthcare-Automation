"""
Document Generation Intelligence: generates reviewable draft application
documents (NHS-style supporting statements, cover letters, short
application answers) from a `CandidateProfile`, a `JobSnapshot`, and
optionally a `MatchResult` — bringing together the profile, AI matching, and
LLM provider subsystems built in earlier milestones.

Every document is a **draft**: nothing here submits, sends, or applies to
anything. See docs/DOCUMENT_GENERATION.md for the human-review workflow,
validation strategy, and export format.
"""

from job_automation.documents.application_answer_generator import ApplicationAnswerGenerator
from job_automation.documents.cover_letter_generator import CoverLetterGenerator
from job_automation.documents.document_models import (
    DocumentStatus,
    DocumentType,
    DocumentValidationIssue,
    GeneratedDocument,
)
from job_automation.documents.document_repository import DocumentRepository
from job_automation.documents.document_service import DocumentService
from job_automation.documents.document_validator import DocumentValidator
from job_automation.documents.export_manager import ExportManager
from job_automation.documents.supporting_statement_generator import SupportingStatementGenerator

__all__ = [
    "ApplicationAnswerGenerator",
    "CoverLetterGenerator",
    "DocumentStatus",
    "DocumentType",
    "DocumentValidationIssue",
    "GeneratedDocument",
    "DocumentRepository",
    "DocumentService",
    "DocumentValidator",
    "ExportManager",
    "SupportingStatementGenerator",
]
