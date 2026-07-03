"""
Tests for the Application Workflow Management subsystem: status
transitions, the approve/reject review workflow, the application
checklist, audit logging, and persistence. No real LLM calls, no browser
automation, no scraping — documents are generated via a `FakeLLMProvider`
(same pattern as tests/test_ai_matching.py and tests/test_document_generation.py).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot
from job_automation.database.models import Employer, Job, JobMatch, User
from job_automation.documents.document_service import DocumentService
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation, VisaStatus
from job_automation.profile.certificate_parser import Certificate
from job_automation.workflows.application_workflow import ApplicationWorkflow, InvalidTransitionError
from job_automation.workflows.checklist_service import ChecklistService
from job_automation.workflows.workflow_models import WorkflowStatus
from job_automation.workflows.workflow_service import WorkflowService


class FakeLLMProvider(LLMProvider):
    def __init__(self, response: str = "I hold a valid DBS Check and relevant experience.") -> None:
        self._response = response

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        return self._response


def _sample_profile(**overrides: object) -> CandidateProfile:
    defaults = dict(
        personal_information=PersonalInformation(full_name="Jane Doe"),
        certificates=(Certificate(name="DBS Check"),),
        visa_status=VisaStatus(right_to_work_uk=True),
    )
    defaults.update(overrides)
    return CandidateProfile(**defaults)  # type: ignore[arg-type]


def _sample_job() -> JobSnapshot:
    return JobSnapshot(title="Healthcare Assistant", employer="Example NHS Trust")


def _setup_user_job(db_session: Session) -> tuple[User, Job, JobMatch]:
    user = User(email="candidate@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    employer = Employer(name="Example NHS Trust")
    job = Job(
        employer=employer,
        title="Healthcare Assistant",
        source_site="nhs_jobs",
        external_id="REF-1",
        url="https://example.com/job/1",
    )
    job_match = JobMatch(job=job, user=user, match_score=85.0)
    db_session.add_all([user, employer, job, job_match])
    db_session.flush()
    return user, job, job_match


# --- State machine rules (pure, no DB) ----------------------------------------


def test_application_workflow_allows_the_documented_forward_journey() -> None:
    journey = [
        (WorkflowStatus.NEW_MATCH, WorkflowStatus.DOCUMENTS_GENERATED),
        (WorkflowStatus.DOCUMENTS_GENERATED, WorkflowStatus.NEEDS_REVIEW),
        (WorkflowStatus.NEEDS_REVIEW, WorkflowStatus.APPROVED),
        (WorkflowStatus.APPROVED, WorkflowStatus.READY_TO_APPLY),
        (WorkflowStatus.READY_TO_APPLY, WorkflowStatus.APPLIED),
        (WorkflowStatus.APPLIED, WorkflowStatus.INTERVIEW),
        (WorkflowStatus.INTERVIEW, WorkflowStatus.OFFER),
        (WorkflowStatus.OFFER, WorkflowStatus.CLOSED),
    ]
    for current, target in journey:
        ApplicationWorkflow.validate_transition(current, target)  # must not raise


def test_application_workflow_allows_rejection_to_loop_back() -> None:
    ApplicationWorkflow.validate_transition(WorkflowStatus.NEEDS_REVIEW, WorkflowStatus.REJECTED)
    ApplicationWorkflow.validate_transition(WorkflowStatus.REJECTED, WorkflowStatus.DOCUMENTS_GENERATED)


def test_application_workflow_allows_closing_from_any_non_terminal_status() -> None:
    for status in WorkflowStatus:
        if status is WorkflowStatus.CLOSED:
            continue
        assert ApplicationWorkflow.can_transition(status, WorkflowStatus.CLOSED)


def test_application_workflow_closed_is_terminal() -> None:
    assert ApplicationWorkflow.is_terminal(WorkflowStatus.CLOSED)
    assert ApplicationWorkflow.allowed_next_statuses(WorkflowStatus.CLOSED) == frozenset()


def test_application_workflow_rejects_invalid_transitions() -> None:
    with pytest.raises(InvalidTransitionError):
        ApplicationWorkflow.validate_transition(WorkflowStatus.NEW_MATCH, WorkflowStatus.APPROVED)
    with pytest.raises(InvalidTransitionError):
        ApplicationWorkflow.validate_transition(WorkflowStatus.CLOSED, WorkflowStatus.NEW_MATCH)
    with pytest.raises(InvalidTransitionError):
        ApplicationWorkflow.validate_transition(WorkflowStatus.APPLIED, WorkflowStatus.APPROVED)


# --- Checklist -----------------------------------------------------------------


def test_checklist_flags_missing_documents_and_profile_gaps() -> None:
    incomplete_profile = CandidateProfile(personal_information=PersonalInformation(full_name="Jane Doe"))
    checklist = ChecklistService().build_checklist(incomplete_profile, documents=[])

    assert checklist.is_complete is False
    missing_names = {item.name for item in checklist.missing_items}
    assert "Supporting statement" in missing_names
    assert "Cover letter" in missing_names
    assert "Certificates on file" in missing_names
    assert "Visa status confirmed" in missing_names


def test_checklist_uses_latest_document_version_not_an_older_rejected_one(db_session: Session) -> None:
    from job_automation.database.models import GeneratedDocumentRecord

    user, job, _ = _setup_user_job(db_session)
    older = GeneratedDocumentRecord(
        user_id=user.id, document_type="supporting_statement", status="rejected", content="old draft"
    )
    db_session.add(older)
    db_session.flush()
    newer = GeneratedDocumentRecord(
        user_id=user.id, document_type="supporting_statement", status="approved", content="new draft"
    )
    db_session.add(newer)
    db_session.flush()

    checklist = ChecklistService().build_checklist(_sample_profile(), documents=[older, newer])
    supporting_statement_item = next(i for i in checklist.items if i.name == "Supporting statement")
    assert supporting_statement_item.is_complete is True  # the newer, approved version wins


# --- Full service journey (persistence) ---------------------------------------


def test_workflow_service_start_workflow_is_idempotent_per_job_and_user(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    service = WorkflowService(db_session)

    first = service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    second = service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)

    assert first.id == second.id
    assert first.status == WorkflowStatus.NEW_MATCH.value


def test_attach_document_advances_new_match_to_documents_generated(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    doc_service = DocumentService(db_session, FakeLLMProvider())
    workflow_service = WorkflowService(db_session)

    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    document = doc_service.generate_supporting_statement(
        _sample_profile(), _sample_job(), user_id=user.id, job_id=job.id
    )
    db_session.commit()

    updated = workflow_service.attach_document(workflow, document)
    assert updated.status == WorkflowStatus.DOCUMENTS_GENERATED.value
    assert document.workflow_id == workflow.id


def test_full_workflow_journey_from_match_to_closed(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    doc_service = DocumentService(db_session, FakeLLMProvider())
    workflow_service = WorkflowService(db_session)

    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    document = doc_service.generate_supporting_statement(
        _sample_profile(), _sample_job(), user_id=user.id, job_id=job.id
    )
    db_session.commit()

    workflow = workflow_service.attach_document(workflow, document)
    workflow = workflow_service.submit_for_review(workflow)
    assert workflow.status == WorkflowStatus.NEEDS_REVIEW.value

    workflow = workflow_service.approve(workflow, reviewer_notes="Looks good")
    assert workflow.status == WorkflowStatus.APPROVED.value

    workflow = workflow_service.mark_ready_to_apply(workflow)
    assert workflow.status == WorkflowStatus.READY_TO_APPLY.value

    workflow = workflow_service.mark_applied(workflow, note="Applied manually via NHS Jobs")
    assert workflow.status == WorkflowStatus.APPLIED.value

    workflow = workflow_service.mark_interview(workflow)
    workflow = workflow_service.mark_offer(workflow)
    workflow = workflow_service.close(workflow, reason="Accepted the offer")
    db_session.commit()

    assert workflow.status == WorkflowStatus.CLOSED.value

    history = workflow_service.get_status_history(workflow.id)
    transitions = [(entry.from_status, entry.to_status) for entry in history]
    assert transitions == [
        (None, "new_match"),
        ("new_match", "documents_generated"),
        ("documents_generated", "needs_review"),
        ("needs_review", "approved"),
        ("approved", "ready_to_apply"),
        ("ready_to_apply", "applied"),
        ("applied", "interview"),
        ("interview", "offer"),
        ("offer", "closed"),
    ]


def test_rejection_loops_back_to_documents_generated(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    doc_service = DocumentService(db_session, FakeLLMProvider())
    workflow_service = WorkflowService(db_session)

    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    document = doc_service.generate_supporting_statement(
        _sample_profile(), _sample_job(), user_id=user.id, job_id=job.id
    )
    db_session.commit()
    workflow = workflow_service.attach_document(workflow, document)
    workflow = workflow_service.submit_for_review(workflow)

    workflow = workflow_service.reject(workflow, reviewer_notes="Needs more detail")
    assert workflow.status == WorkflowStatus.REJECTED.value

    # Regenerate and resubmit — the loop the state machine explicitly allows.
    second_document = doc_service.generate_supporting_statement(
        _sample_profile(), _sample_job(), user_id=user.id, job_id=job.id
    )
    db_session.commit()
    workflow = workflow_service.attach_document(workflow, second_document)
    assert workflow.status == WorkflowStatus.DOCUMENTS_GENERATED.value

    # Both document versions are still linked to this workflow — nothing is lost.
    linked_documents = workflow_service.get_documents(workflow.id)
    assert len(linked_documents) == 2


def test_workflow_service_prevents_invalid_transitions(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    workflow_service = WorkflowService(db_session)
    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)

    with pytest.raises(InvalidTransitionError):
        workflow_service.mark_applied(workflow)  # can't apply straight from NEW_MATCH


# --- Audit log -------------------------------------------------------------------


def test_audit_log_records_every_significant_action(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    doc_service = DocumentService(db_session, FakeLLMProvider())
    workflow_service = WorkflowService(db_session)

    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    document = doc_service.generate_supporting_statement(
        _sample_profile(), _sample_job(), user_id=user.id, job_id=job.id
    )
    db_session.commit()
    workflow = workflow_service.attach_document(workflow, document)
    workflow = workflow_service.submit_for_review(workflow)
    workflow_service.approve(workflow, reviewer_notes="Good to go")
    db_session.commit()

    actions = [entry.action for entry in workflow_service.get_audit_log(workflow.id)]
    assert actions == ["workflow_started", "document_attached", "submitted_for_review", "approved"]

    approved_entry = next(e for e in workflow_service.get_audit_log(workflow.id) if e.action == "approved")
    assert approved_entry.details["reviewer_notes"] == "Good to go"


def test_audit_log_persists_independently_of_status_history(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    workflow_service = WorkflowService(db_session)
    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    db_session.commit()

    audit_entries = workflow_service.get_audit_log(workflow.id)
    status_entries = workflow_service.get_status_history(workflow.id)
    assert len(audit_entries) == 1  # "workflow_started"
    assert len(status_entries) == 1  # None -> new_match


# --- Repository / persistence details -----------------------------------------


def test_workflow_repository_list_for_user_orders_most_recent_first(db_session: Session) -> None:
    user, job, job_match = _setup_user_job(db_session)
    employer2 = Employer(name="Another NHS Trust")
    job2 = Job(
        employer=employer2, title="Support Worker", source_site="nhs_jobs", external_id="REF-2", url="https://x/2"
    )
    db_session.add(employer2)
    db_session.add(job2)
    db_session.flush()

    workflow_service = WorkflowService(db_session)
    first = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    second = workflow_service.start_workflow(user_id=user.id, job_id=job2.id)
    # SQLite's CURRENT_TIMESTAMP (used for created_at) only has second
    # resolution, so two rows created in quick succession in a test can tie
    # — set explicit, clearly-ordered timestamps rather than relying on
    # real-time creation order to make this assertion deterministic.
    first.created_at = datetime(2026, 1, 1, 12, 0, 0)
    second.created_at = datetime(2026, 1, 1, 12, 0, 1)
    db_session.commit()

    workflows = workflow_service.list_for_user(user.id)
    assert [w.id for w in workflows] == [second.id, first.id]


def test_generated_document_record_workflow_id_set_null_on_workflow_deletion(db_session: Session) -> None:
    from job_automation.database.models import ApplicationWorkflowRecord, GeneratedDocumentRecord

    user, job, job_match = _setup_user_job(db_session)
    doc_service = DocumentService(db_session, FakeLLMProvider())
    workflow_service = WorkflowService(db_session)

    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=job_match.id)
    document = doc_service.generate_supporting_statement(
        _sample_profile(), _sample_job(), user_id=user.id, job_id=job.id
    )
    db_session.commit()
    workflow_service.attach_document(workflow, document)
    db_session.commit()

    workflow_row = db_session.get(ApplicationWorkflowRecord, workflow.id)
    db_session.delete(workflow_row)
    db_session.commit()

    reloaded_document = db_session.get(GeneratedDocumentRecord, document.id)
    assert reloaded_document is not None  # the draft itself survives
    assert reloaded_document.workflow_id is None  # just disassociated
