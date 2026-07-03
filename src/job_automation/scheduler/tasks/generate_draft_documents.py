"""
Scheduled task: draft a supporting statement for every sufficiently-strong
`JobMatch` that doesn't already have one, using `SchedulerFakeLLMProvider`
— never a real Anthropic call.

**Never auto-approves anything.** `DocumentService.generate_supporting_statement()`
always produces a `DRAFT`/`NEEDS_REVIEW` document awaiting a human decision
(see docs/DOCUMENT_GENERATION.md) — this task calls that exact method
unchanged, so a document this task creates requires the same explicit
`approve()`/`reject()` action from the dashboard as one a human requested
directly. Attaching the draft to a workflow via `WorkflowService
.attach_document()` only ever advances `NEW_MATCH`/`REJECTED` ->
`DOCUMENTS_GENERATED` — the one transition that subsystem already treats as
automatic (see docs/APPLICATION_WORKFLOW.md) — never `APPROVED`,
`READY_TO_APPLY`, or `APPLIED`.

Only drafts a *supporting statement* (not a cover letter or application
answer) — those need job-specific questions/context this background task
has no way to originate; a human still starts those from the dashboard.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.config.settings import settings
from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
from job_automation.database.models.job_match import JobMatch
from job_automation.documents.document_models import DocumentType
from job_automation.documents.document_service import DocumentService
from job_automation.profile.profile_service import ProfileService
from job_automation.scheduler.fake_llm_provider import SchedulerFakeLLMProvider
from job_automation.utils.logger import logger
from job_automation.workflows.workflow_service import WorkflowService


def run(session: Session) -> dict:
    profile_service = ProfileService(session)
    document_service = DocumentService(session, SchedulerFakeLLMProvider())
    workflow_service = WorkflowService(session)

    drafted = 0
    skipped_already_drafted = 0
    skipped_no_profile = 0

    all_matches = session.scalars(select(JobMatch)).all()
    matches = [m for m in all_matches if m.match_score >= settings.scheduler_document_score_threshold]
    skipped_below_threshold = len(all_matches) - len(matches)

    for match in matches:
        if _already_has_supporting_statement(session, job_id=match.job_id, user_id=match.user_id):
            skipped_already_drafted += 1
            continue

        profile = profile_service.get(match.user_id)
        if profile is None:
            skipped_no_profile += 1
            continue

        job_snapshot = JobSnapshot.from_job(match.job)
        match_result = MatchResult.from_dict(match.analysis or {}, fallback_overall_score=float(match.match_score))

        document = document_service.generate_supporting_statement(
            profile, job_snapshot, match_result, user_id=match.user_id, job_id=match.job_id
        )
        workflow = workflow_service.start_workflow(
            user_id=match.user_id, job_id=match.job_id, job_match_id=match.id
        )
        workflow_service.attach_document(workflow, document)
        drafted += 1

    logger.info(
        "generate_draft_documents: {} drafted, {} already drafted, {} below threshold, {} no profile",
        drafted,
        skipped_already_drafted,
        skipped_below_threshold,
        skipped_no_profile,
    )
    return {
        "documents_drafted": drafted,
        "skipped_already_drafted": skipped_already_drafted,
        "skipped_below_threshold": skipped_below_threshold,
        "skipped_no_profile": skipped_no_profile,
    }


def _already_has_supporting_statement(session: Session, *, job_id, user_id) -> bool:
    existing = session.scalars(
        select(GeneratedDocumentRecord).where(
            GeneratedDocumentRecord.job_id == job_id,
            GeneratedDocumentRecord.user_id == user_id,
            GeneratedDocumentRecord.document_type == DocumentType.SUPPORTING_STATEMENT.value,
        )
    ).first()
    return existing is not None
