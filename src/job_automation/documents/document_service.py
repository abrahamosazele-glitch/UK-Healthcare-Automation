"""
The main entry point for document generation: generate -> validate ->
assign a review status -> persist, plus the human-review workflow
(approve/reject) and export. Composes every other module in this package so
callers don't wire generators/validator/repository/export manager
themselves — the same orchestration role `MatchingService` plays for the AI
matching engine and `ProfileService` plays for the profile subsystem.

Every generated document is saved as a draft (`DocumentStatus.DRAFT` or
`NEEDS_REVIEW` if validation found something) — never auto-approved, and
nothing in this class submits anything anywhere. `approve()`/`reject()` are
the only way a document's status changes after generation, and both require
an explicit caller action (a human decision), which is what "include a
human-review workflow" means here.

Publishes a `DOCUMENT_GENERATED` event (never calls `NotificationService`
directly — see `notifications.events`'s module docstring) once per
successfully generated document, from the single `_validate_and_save()`
choke point all three `generate_*()` methods funnel through — covering
manual regeneration (`routes/documents.py`) and scheduled generation
(`scheduler.tasks.generate_draft_documents`) with one hook.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from job_automation.ai.cache import AIResponseCache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
from job_automation.documents.application_answer_generator import ApplicationAnswerGenerator
from job_automation.documents.career_insight_generator import CareerInsightGenerator
from job_automation.documents.cover_letter_generator import CoverLetterGenerator
from job_automation.documents.document_models import DocumentStatus, DocumentType, GeneratedDocument
from job_automation.documents.document_repository import DocumentRepository
from job_automation.documents.document_validator import DocumentValidator
from job_automation.documents.export_manager import ExportManager
from job_automation.documents.interview_prep_generator import InterviewPrepGenerator
from job_automation.documents.skills_gap_generator import SkillsGapGenerator
from job_automation.documents.supporting_statement_generator import SupportingStatementGenerator
from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.profile.candidate_profile import CandidateProfile
from job_automation.utils.logger import logger


class DocumentService:
    def __init__(
        self,
        session: Session,
        llm_provider: LLMProvider,
        *,
        repository: DocumentRepository | None = None,
        validator: DocumentValidator | None = None,
        export_manager: ExportManager | None = None,
        supporting_statement_generator: SupportingStatementGenerator | None = None,
        cover_letter_generator: CoverLetterGenerator | None = None,
        application_answer_generator: ApplicationAnswerGenerator | None = None,
        interview_prep_generator: InterviewPrepGenerator | None = None,
        skills_gap_generator: SkillsGapGenerator | None = None,
        career_insight_generator: CareerInsightGenerator | None = None,
        cache: AIResponseCache | None = None,
        event_bus: EventBus = event_bus,
    ) -> None:
        self._session = session
        self._repository = repository or DocumentRepository(session)
        self._validator = validator or DocumentValidator()
        self._export_manager = export_manager or ExportManager()
        self._supporting_statement_generator = supporting_statement_generator or SupportingStatementGenerator(
            llm_provider, cache=cache
        )
        self._cover_letter_generator = cover_letter_generator or CoverLetterGenerator(llm_provider, cache=cache)
        self._application_answer_generator = application_answer_generator or ApplicationAnswerGenerator(
            llm_provider, cache=cache
        )
        self._interview_prep_generator = interview_prep_generator or InterviewPrepGenerator(
            llm_provider, cache=cache
        )
        self._skills_gap_generator = skills_gap_generator or SkillsGapGenerator(llm_provider, cache=cache)
        self._career_insight_generator = career_insight_generator or CareerInsightGenerator(
            llm_provider, cache=cache
        )
        self._event_bus = event_bus

    def generate_supporting_statement(
        self,
        profile: CandidateProfile,
        job: JobSnapshot,
        match_result: MatchResult | None = None,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
    ) -> GeneratedDocumentRecord:
        document = self._supporting_statement_generator.generate(profile, job, match_result)
        return self._validate_and_save(document, profile, user_id=user_id, job_id=job_id)

    def generate_cover_letter(
        self,
        profile: CandidateProfile,
        job: JobSnapshot,
        match_result: MatchResult | None = None,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
    ) -> GeneratedDocumentRecord:
        document = self._cover_letter_generator.generate(profile, job, match_result)
        return self._validate_and_save(document, profile, user_id=user_id, job_id=job_id)

    def generate_application_answer(
        self,
        profile: CandidateProfile,
        job: JobSnapshot,
        question: str,
        match_result: MatchResult | None = None,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
        max_words: int = 150,
    ) -> GeneratedDocumentRecord:
        document = self._application_answer_generator.generate(
            profile, job, question, match_result, max_words=max_words
        )
        return self._validate_and_save(document, profile, user_id=user_id, job_id=job_id)

    def generate_interview_prep(
        self,
        profile: CandidateProfile,
        job: JobSnapshot,
        match_result: MatchResult | None = None,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
        interview_type: str | None = None,
        interview_stage: str | None = None,
    ) -> GeneratedDocumentRecord:
        document = self._interview_prep_generator.generate(
            profile, job, match_result, interview_type=interview_type, interview_stage=interview_stage
        )
        return self._validate_and_save(document, profile, user_id=user_id, job_id=job_id)

    def generate_skills_gap_analysis(
        self,
        profile: CandidateProfile,
        job: JobSnapshot,
        match_result: MatchResult | None = None,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
    ) -> GeneratedDocumentRecord:
        document = self._skills_gap_generator.generate(profile, job, match_result)
        return self._validate_and_save(document, profile, user_id=user_id, job_id=job_id)

    def generate_career_insight(
        self,
        profile: CandidateProfile,
        job: JobSnapshot,
        match_result: MatchResult | None = None,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
    ) -> GeneratedDocumentRecord:
        document = self._career_insight_generator.generate(profile, job, match_result)
        return self._validate_and_save(document, profile, user_id=user_id, job_id=job_id)

    def approve(self, document_id: uuid.UUID, *, review_notes: str | None = None) -> GeneratedDocumentRecord:
        record = self._get_or_raise(document_id)
        logger.info("Document {} approved", document_id)
        return self._repository.update_status(record, DocumentStatus.APPROVED.value, review_notes=review_notes)

    def reject(self, document_id: uuid.UUID, *, review_notes: str | None = None) -> GeneratedDocumentRecord:
        record = self._get_or_raise(document_id)
        logger.info("Document {} rejected", document_id)
        return self._repository.update_status(record, DocumentStatus.REJECTED.value, review_notes=review_notes)

    def list_for_review(self, user_id: uuid.UUID) -> list[GeneratedDocumentRecord]:
        """Every draft for this user still awaiting a human decision."""
        return [
            record
            for record in self._repository.list_for_user(user_id)
            if record.status in (DocumentStatus.DRAFT.value, DocumentStatus.NEEDS_REVIEW.value)
        ]

    def export(
        self, document_id: uuid.UUID, *, formats: tuple[str, ...] = ("markdown", "txt")
    ) -> dict[str, Path]:
        record = self._get_or_raise(document_id)
        document = _record_to_document(record)
        paths = self._export_manager.export_all(document, formats=formats)
        logger.info("Exported document {} to {}", document_id, list(paths.values()))
        return paths

    def _validate_and_save(
        self,
        document: GeneratedDocument,
        profile: CandidateProfile,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None,
    ) -> GeneratedDocumentRecord:
        issues = self._validator.validate(document, profile)
        document.validation_issues = issues
        document.status = DocumentStatus.NEEDS_REVIEW if issues else DocumentStatus.DRAFT

        if issues:
            logger.warning(
                "Generated {} has {} validation issue(s) — marked for review",
                document.document_type.value,
                len(issues),
            )
        else:
            logger.info("Generated {} — no validation issues", document.document_type.value)

        record = self._repository.create(
            user_id=user_id,
            job_id=job_id,
            document_type=document.document_type.value,
            status=document.status.value,
            content=document.content,
            question=document.question,
            validation_issues=[issue.to_dict() for issue in issues] or None,
        )
        self._event_bus.publish(
            Event(
                event_type=EventType.DOCUMENT_GENERATED,
                payload={
                    "document_id": str(record.id),
                    "document_type": record.document_type,
                    "status": record.status,
                    "job_id": str(job_id) if job_id else None,
                },
                user_id=user_id,
            ),
            self._session,
        )
        return record

    def _get_or_raise(self, document_id: uuid.UUID) -> GeneratedDocumentRecord:
        record = self._repository.get(document_id)
        if record is None:
            raise ValueError(f"No generated document found with id {document_id}")
        return record


def _record_to_document(record: GeneratedDocumentRecord) -> GeneratedDocument:
    """Recover job_title/employer from the `job` relationship when present,
    so a document exported after being reloaded from the database (not
    fresh off a generator, which sets these directly) still gets a
    meaningful export filename and markdown header rather than falling back
    to the generic document-type name."""
    job_title = record.job.title if record.job is not None else None
    employer = record.job.employer.name if record.job is not None else None
    return GeneratedDocument(
        document_type=DocumentType(record.document_type),
        content=record.content,
        status=DocumentStatus(record.status),
        question=record.question,
        job_title=job_title,
        employer=employer,
    )
