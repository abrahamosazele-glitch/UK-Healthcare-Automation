"""
Documents list and review pages, plus manual generation triggers.
Regeneration and first-time generation both funnel through the exact same
`DocumentService.generate_*()` methods — these routes only figure out which
generator to call and assemble the `JobSnapshot`/`CandidateProfile`/
`MatchResult` inputs from already-stored data; neither talks to an LLM
provider or builds a prompt directly.

`get_llm_provider()` (defined in `web.app`, imported here) is a FastAPI
dependency (not a plain helper call) so tests can override it with
`FakeLLMProvider` via `app.dependency_overrides[get_llm_provider]` —
exactly the pattern already used throughout this project's own test suite
(`test_ai_matching.py`, `test_document_generation.py`), just wired through
FastAPI's DI instead of a constructor argument.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_automation.ai.cache import AIResponseCache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.database.models.job_match import JobMatch
from job_automation.database.models.user import User
from job_automation.database.repositories.job_repository import JobRepository
from job_automation.documents.document_models import DocumentType
from job_automation.documents.document_repository import DocumentRepository
from job_automation.documents.document_service import DocumentService
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.profile_service import ProfileService
from job_automation.web.app import (
    get_ai_response_cache,
    get_current_user,
    get_db_session,
    get_llm_provider,
    templates,
)

router = APIRouter()


def _profile_or_fallback(session: Session, current_user: User) -> CandidateProfile:
    return ProfileService(session).get(current_user.id) or CandidateProfile(
        personal_information=PersonalInformation(full_name=current_user.full_name)
    )


@router.get("/documents", response_class=HTMLResponse)
def documents_list(
    request: Request,
    status: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    documents = DocumentRepository(session).list_for_user(current_user.id)
    if status:
        documents = [document for document in documents if document.status == status]
    return templates.TemplateResponse(
        request,
        "documents.html",
        {
            "active_page": "documents",
            "current_user": current_user,
            "documents": documents,
            "status_filter": status,
        },
    )


@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_review(
    document_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    document = DocumentRepository(session).get(document_id)
    if document is None or document.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    return templates.TemplateResponse(
        request,
        "document_review.html",
        {
            "active_page": "documents",
            "current_user": current_user,
            "document": document,
            "job": document.job,
        },
    )


@router.post("/documents/{document_id}/regenerate")
def regenerate_document(
    document_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    cache: AIResponseCache = Depends(get_ai_response_cache),
) -> RedirectResponse:
    document = DocumentRepository(session).get(document_id)
    if document is None or document.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.job is None:
        raise HTTPException(status_code=400, detail="Cannot regenerate a document with no linked job")

    profile = _profile_or_fallback(session, current_user)
    job_snapshot = JobSnapshot.from_job(document.job)
    match_result = _match_result_for(session, document.job.id, current_user.id)

    service = DocumentService(session, llm_provider, cache=cache)
    if document.document_type == "supporting_statement":
        new_document = service.generate_supporting_statement(
            profile, job_snapshot, match_result, user_id=current_user.id, job_id=document.job.id
        )
    elif document.document_type == "cover_letter":
        new_document = service.generate_cover_letter(
            profile, job_snapshot, match_result, user_id=current_user.id, job_id=document.job.id
        )
    else:
        new_document = service.generate_application_answer(
            profile,
            job_snapshot,
            document.question or "",
            match_result,
            user_id=current_user.id,
            job_id=document.job.id,
        )
    if document.workflow_id is not None:
        from job_automation.workflows.workflow_repository import WorkflowRepository

        workflow_repository = WorkflowRepository(session)
        workflow = workflow_repository.get(document.workflow_id)
        if workflow is not None:
            workflow_repository.link_document(new_document, workflow)

    return RedirectResponse(url=f"/documents/{new_document.id}", status_code=303)


@router.post("/jobs/{job_id}/documents/generate")
def generate_document(
    job_id: uuid.UUID,
    document_type: str = Form(...),
    question: str | None = Form(None),
    max_words: int = Form(150),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    cache: AIResponseCache = Depends(get_ai_response_cache),
) -> RedirectResponse:
    """Manual first-time generation trigger — the counterpart to
    `regenerate_document` for a job with no document of this type yet.
    Lives on the job detail page next to the AI match card, since that's
    where a candidate decides "I want a document for this job." Requires an
    explicit click; nothing here runs automatically."""
    job = JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        kind = DocumentType(document_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown document type {document_type!r}") from exc

    profile = _profile_or_fallback(session, current_user)
    job_snapshot = JobSnapshot.from_job(job)
    match_result = _match_result_for(session, job_id, current_user.id)

    service = DocumentService(session, llm_provider, cache=cache)
    if kind == DocumentType.SUPPORTING_STATEMENT:
        document = service.generate_supporting_statement(
            profile, job_snapshot, match_result, user_id=current_user.id, job_id=job_id
        )
    elif kind == DocumentType.COVER_LETTER:
        document = service.generate_cover_letter(
            profile, job_snapshot, match_result, user_id=current_user.id, job_id=job_id
        )
    elif kind == DocumentType.APPLICATION_ANSWER:
        if not question:
            raise HTTPException(status_code=400, detail="question is required for an application answer")
        document = service.generate_application_answer(
            profile, job_snapshot, question, match_result, user_id=current_user.id, job_id=job_id, max_words=max_words
        )
    elif kind == DocumentType.SKILLS_GAP_ANALYSIS:
        document = service.generate_skills_gap_analysis(
            profile, job_snapshot, match_result, user_id=current_user.id, job_id=job_id
        )
    elif kind == DocumentType.INTERVIEW_PREP:
        # Unlike `routes/interviews.py`'s `generate_interview_prep`, this
        # doesn't require an `InterviewRecord` to already exist —
        # `DocumentService.generate_interview_prep()` only ever needed
        # `job_id`, not an interview; added for the Job Ingestion Service
        # milestone's "high-match job" notification, which links here
        # before any interview has been scheduled.
        document = service.generate_interview_prep(
            profile, job_snapshot, match_result, user_id=current_user.id, job_id=job_id
        )
    elif kind == DocumentType.CAREER_INSIGHT:
        document = service.generate_career_insight(
            profile, job_snapshot, match_result, user_id=current_user.id, job_id=job_id
        )
    else:
        raise HTTPException(status_code=400, detail=f"{kind.value} cannot be generated from a job page")

    return RedirectResponse(url=f"/documents/{document.id}", status_code=303)


def _match_result_for(session: Session, job_id: uuid.UUID, user_id: uuid.UUID) -> MatchResult | None:
    match = session.query(JobMatch).filter_by(job_id=job_id, user_id=user_id).first()
    if match is None or not match.analysis:
        return None
    return MatchResult.from_dict(match.analysis, fallback_overall_score=float(match.match_score))
