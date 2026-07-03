"""
Computes the "ready to apply" checklist for a workflow: which required
documents exist, whether they've been approved, and whether the candidate's
profile has the basics (certificates, visa status) in place.

Reuses `documents.document_models.DocumentType`/`DocumentStatus` (the
canonical enums from the document-generation subsystem) rather than
duplicating them — this is legitimate application-layer reuse (not a
database-layer concern, so it doesn't touch the "database models only
import database.*" invariant used elsewhere in this project).

"Track document versions" falls out of `GeneratedDocumentRecord.workflow_id`
(every regeneration is a new row — see that model's docstring): this
service always looks at the *latest* row per document type, so an older,
superseded draft doesn't count against the checklist once a newer one
exists.
"""

from __future__ import annotations

from typing import Sequence

from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
from job_automation.documents.document_models import DocumentStatus, DocumentType
from job_automation.profile.candidate_profile import CandidateProfile
from job_automation.workflows.workflow_models import ApplicationChecklist, ChecklistItem


class ChecklistService:
    def build_checklist(
        self, profile: CandidateProfile, documents: Sequence[GeneratedDocumentRecord]
    ) -> ApplicationChecklist:
        latest_by_type = self._latest_per_type(documents)

        items = [
            self._document_item(latest_by_type, DocumentType.SUPPORTING_STATEMENT, "Supporting statement"),
            self._document_item(latest_by_type, DocumentType.COVER_LETTER, "Cover letter"),
            self._certificates_item(profile),
            self._visa_status_item(profile),
            self._all_documents_approved_item(latest_by_type),
        ]
        return ApplicationChecklist(items=tuple(items))

    def _latest_per_type(
        self, documents: Sequence[GeneratedDocumentRecord]
    ) -> dict[str, GeneratedDocumentRecord]:
        """Assumes `documents` is already ordered oldest-first (guaranteed by
        `WorkflowRepository.get_documents()`) — uses `>=` rather than `>` so
        that later entries in the sequence win ties, since SQLite's
        `CURRENT_TIMESTAMP` (used for `created_at`) only has second
        resolution and two documents generated in quick succession (as in
        tests, and plausibly in a fast regenerate-after-rejection flow) can
        share an identical timestamp."""
        latest: dict[str, GeneratedDocumentRecord] = {}
        for document in documents:
            existing = latest.get(document.document_type)
            if existing is None or document.created_at >= existing.created_at:
                latest[document.document_type] = document
        return latest

    def _document_item(
        self, latest_by_type: dict[str, GeneratedDocumentRecord], document_type: DocumentType, label: str
    ) -> ChecklistItem:
        document = latest_by_type.get(document_type.value)
        if document is None:
            return ChecklistItem(name=label, is_complete=False, detail=f"No {label.lower()} generated yet")
        if document.status != DocumentStatus.APPROVED.value:
            return ChecklistItem(
                name=label,
                is_complete=False,
                detail=f"{label} exists but is not yet approved (status: {document.status})",
            )
        return ChecklistItem(name=label, is_complete=True)

    def _certificates_item(self, profile: CandidateProfile) -> ChecklistItem:
        if not profile.certificates:
            return ChecklistItem(
                name="Certificates on file", is_complete=False, detail="No certificates listed on profile"
            )
        return ChecklistItem(name="Certificates on file", is_complete=True)

    def _visa_status_item(self, profile: CandidateProfile) -> ChecklistItem:
        if profile.visa_status.right_to_work_uk is None:
            return ChecklistItem(
                name="Visa status confirmed", is_complete=False, detail="right_to_work_uk is not set on profile"
            )
        return ChecklistItem(name="Visa status confirmed", is_complete=True)

    def _all_documents_approved_item(self, latest_by_type: dict[str, GeneratedDocumentRecord]) -> ChecklistItem:
        if not latest_by_type:
            return ChecklistItem(
                name="All documents approved", is_complete=False, detail="No documents generated yet"
            )
        unapproved = [d for d in latest_by_type.values() if d.status != DocumentStatus.APPROVED.value]
        if unapproved:
            return ChecklistItem(
                name="All documents approved",
                is_complete=False,
                detail=f"{len(unapproved)} document(s) awaiting approval",
            )
        return ChecklistItem(name="All documents approved", is_complete=True)
