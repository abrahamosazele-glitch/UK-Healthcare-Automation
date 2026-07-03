"""
Interview management pages: the interview list, the "schedule a new
interview" form, and an interview's full detail page (status transitions,
preparation checklist, categorized notes, reminders, and explicit
workflow-sync actions).

Mutations are plain HTML form POSTs that redirect back to the detail page
— the same "full page reload, no HTMX partial swap" pattern already used
throughout this dashboard. `api/interviews_api.py` is the separate JSON
equivalent for programmatic callers.

`/interviews/new` accepts optional `employer_id`/`job_id`/
`application_workflow_id` query parameters so "Schedule interview" links
from an employer's profile page, a job's detail page, or an application
row can pre-fill the form — manually re-selecting an employer from a
giant unfiltered dropdown every time would be poor UX for the common case
of scheduling an interview *for* something the candidate is already
looking at.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_automation.ai.cache import AIResponseCache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.database.models.job_match import JobMatch
from job_automation.database.models.user import User
from job_automation.database.repositories.employer_repository import EmployerRepository
from job_automation.documents.document_service import DocumentService
from job_automation.employer_crm.employer_contact_repository import EmployerContactRepository
from job_automation.interviews.interview_models import (
    InterviewStage,
    InterviewStatus,
    InterviewType,
    InvalidInterviewStatusTransitionError,
    NoteCategory,
    ReminderOffset,
)
from job_automation.interviews.interview_service import InterviewService
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


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime {value!r}") from exc


def _parse_offsets(values: list[str]) -> list[ReminderOffset]:
    try:
        return [ReminderOffset(value) for value in values if value]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_names(value: str | None) -> list[str] | None:
    if not value:
        return None
    names = [name.strip() for name in value.split(",") if name.strip()]
    return names or None


@router.get("/interviews", response_class=HTMLResponse)
def interviews_list(
    request: Request,
    status: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    service = InterviewService(session)
    parsed_status = InterviewStatus(status) if status else None
    interviews = service.list_for_user(current_user.id, status=parsed_status)
    return templates.TemplateResponse(
        request,
        "interviews.html",
        {
            "active_page": "interviews",
            "current_user": current_user,
            "interviews": interviews,
            "status_filter": status,
            "interview_statuses": list(InterviewStatus),
        },
    )


@router.get("/interviews/new", response_class=HTMLResponse)
def new_interview_form(
    request: Request,
    employer_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    application_workflow_id: uuid.UUID | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    employers = EmployerRepository(session).list_all()
    contacts = (
        EmployerContactRepository(session).list_for_employer(user_id=current_user.id, employer_id=employer_id)
        if employer_id
        else []
    )
    return templates.TemplateResponse(
        request,
        "interview_form.html",
        {
            "active_page": "interviews",
            "current_user": current_user,
            "employers": employers,
            "contacts": contacts,
            "preselected_employer_id": employer_id,
            "preselected_job_id": job_id,
            "preselected_application_workflow_id": application_workflow_id,
            "interview_types": list(InterviewType),
            "interview_stages": list(InterviewStage),
            "reminder_offsets": list(ReminderOffset),
        },
    )


@router.post("/interviews/new")
def create_interview(
    employer_id: uuid.UUID = Form(...),
    interview_type: str = Form(...),
    scheduled_at: str = Form(...),
    job_id: str | None = Form(None),
    application_workflow_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    interview_stage: str | None = Form(None),
    duration_minutes: int | None = Form(None),
    timezone: str | None = Form(None),
    location: str | None = Form(None),
    meeting_link: str | None = Form(None),
    interviewer_names: str | None = Form(None),
    reminder_offsets: list[str] = Form(default_factory=list),
    notes: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        kind = InterviewType(interview_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown interview type {interview_type!r}") from exc

    interview = InterviewService(session).schedule(
        user_id=current_user.id,
        employer_id=employer_id,
        interview_type=kind,
        scheduled_at=_parse_datetime(scheduled_at),
        job_id=uuid.UUID(job_id) if job_id else None,
        application_workflow_id=uuid.UUID(application_workflow_id) if application_workflow_id else None,
        contact_id=uuid.UUID(contact_id) if contact_id else None,
        interview_stage=interview_stage or None,
        duration_minutes=duration_minutes,
        timezone=timezone or None,
        location=location or None,
        meeting_link=meeting_link or None,
        interviewer_names=_parse_names(interviewer_names),
        reminder_offsets=_parse_offsets(reminder_offsets) or None,
        notes=notes or None,
    )
    return RedirectResponse(url=f"/interviews/{interview.id}", status_code=303)


@router.get("/interviews/{interview_id}", response_class=HTMLResponse)
def interview_detail(
    interview_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    service = InterviewService(session)
    try:
        interview = service.get(interview_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")

    from job_automation.interviews.interview_models import InterviewLifecycle

    current_status = InterviewStatus(interview.status)
    return templates.TemplateResponse(
        request,
        "interview_detail.html",
        {
            "active_page": "interviews",
            "current_user": current_user,
            "interview": interview,
            "checklist": service.list_checklist(interview_id, user_id=current_user.id),
            "notes": service.list_notes(interview_id, user_id=current_user.id),
            "reminders": service.list_reminders(interview_id, user_id=current_user.id),
            "completion_percent": service.checklist_completion_percent(interview_id, user_id=current_user.id),
            "next_statuses": sorted(
                InterviewLifecycle.allowed_next_statuses(current_status), key=lambda s: s.value
            ),
            "note_categories": list(NoteCategory),
            "has_linked_workflow": interview.application_workflow_id is not None,
        },
    )


@router.post("/interviews/{interview_id}/generate-prep")
def generate_interview_prep(
    interview_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    cache: AIResponseCache = Depends(get_ai_response_cache),
) -> RedirectResponse:
    """Manual trigger for AI-generated interview preparation notes — shown
    on the interview detail page only when the interview has a linked job
    (a `JobSnapshot` needs a job listing; an interview with no `job_id`
    has nothing to prepare notes against). Reuses `DocumentService` exactly
    like every other document type — the result is a reviewable draft on
    `/documents/{id}`, not something injected back into the interview
    record automatically."""
    try:
        interview = InterviewService(session).get(interview_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.job is None:
        raise HTTPException(status_code=400, detail="This interview has no linked job to prepare for")

    profile = ProfileService(session).get(current_user.id) or CandidateProfile(
        personal_information=PersonalInformation(full_name=current_user.full_name)
    )
    job_snapshot = JobSnapshot.from_job(interview.job)
    match = session.query(JobMatch).filter_by(job_id=interview.job.id, user_id=current_user.id).first()
    match_result = (
        MatchResult.from_dict(match.analysis, fallback_overall_score=float(match.match_score))
        if match is not None and match.analysis
        else None
    )

    service = DocumentService(session, llm_provider, cache=cache)
    document = service.generate_interview_prep(
        profile,
        job_snapshot,
        match_result,
        user_id=current_user.id,
        job_id=interview.job.id,
        interview_type=interview.interview_type,
        interview_stage=interview.interview_stage,
    )
    return RedirectResponse(url=f"/documents/{document.id}", status_code=303)


@router.post("/interviews/{interview_id}/status")
def update_interview_status(
    interview_id: uuid.UUID,
    target_status: str = Form(...),
    outcome: str | None = Form(None),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        status_value = InterviewStatus(target_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown status {target_status!r}") from exc
    try:
        InterviewService(session).update_status(
            interview_id, user_id=current_user.id, target_status=status_value, outcome=outcome or None
        )
    except InvalidInterviewStatusTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/reschedule")
def reschedule_interview(
    interview_id: uuid.UUID,
    new_scheduled_at: str = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        InterviewService(session).reschedule(
            interview_id, user_id=current_user.id, new_scheduled_at=_parse_datetime(new_scheduled_at)
        )
    except InvalidInterviewStatusTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/workflow-sync")
def sync_workflow(
    interview_id: uuid.UUID,
    action: str = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        InterviewService(session).sync_workflow_status(interview_id, user_id=current_user.id, action=action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/checklist")
def add_checklist_item(
    interview_id: uuid.UUID,
    label: str = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    InterviewService(session).add_checklist_item(interview_id, user_id=current_user.id, label=label)
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/checklist/{item_id}/toggle")
def toggle_checklist_item(
    interview_id: uuid.UUID,
    item_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        InterviewService(session).toggle_checklist_item(item_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/checklist/{item_id}/remove")
def remove_checklist_item(
    interview_id: uuid.UUID,
    item_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        InterviewService(session).remove_checklist_item(item_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/notes")
def add_note(
    interview_id: uuid.UUID,
    category: str = Form(...),
    body: str = Form(...),
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        kind = NoteCategory(category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown note category {category!r}") from exc
    InterviewService(session).add_note(interview_id, user_id=current_user.id, category=kind, body=body)
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/notes/{note_id}/delete")
def remove_note(
    interview_id: uuid.UUID,
    note_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        InterviewService(session).remove_note(note_id, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/interviews/{interview_id}", status_code=303)
