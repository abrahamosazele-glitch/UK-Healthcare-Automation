"""
Tests for the Employer & Application CRM milestone's
`job_automation.employer_crm` package (favourite/visa notes, departments,
recruiter contacts, activity timeline), `EmployerRepository`'s search/
filter extension, and `AnalyticsService`'s employer outcome analytics.

The key regression test here (`test_employer_outcome_rejections_come_from_
saved_job_not_workflow_status`) proves the CRM's "rejections" count is
sourced from `SavedJob.pipeline_stage == PipelineStage.REJECTED`, not
`WorkflowStatus.REJECTED` — the same "two different things named
'rejected'" distinction the Job Management milestone established.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from job_automation.analytics.analytics_service import AnalyticsService
from job_automation.database.models.employer import Employer
from job_automation.database.models.job import Job
from job_automation.database.models.user import User
from job_automation.database.repositories.employer_repository import EmployerFilter, EmployerRepository
from job_automation.employer_crm.employer_crm_models import ActivityEntryType, CommunicationChannel
from job_automation.employer_crm.employer_crm_service import EmployerCrmService
from job_automation.job_organization.job_organization_models import PipelineStage
from job_automation.job_organization.job_organization_service import JobOrganizationService
from job_automation.workflows.status_manager import StatusManager
from job_automation.workflows.workflow_models import WorkflowStatus
from job_automation.workflows.workflow_service import WorkflowService


def _make_user(email: str = "candidate@example.com") -> User:
    return User(email=email, full_name="Test Candidate", hashed_password="unused")


def _make_job(employer: Employer, external_id: str = "REF-1") -> Job:
    return Job(
        employer=employer,
        title="Healthcare Assistant",
        location="London",
        source_site="nhs_jobs",
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        is_active=True,
    )


def _advance_workflow_to_interview(session: Session, *, user: User, job: Job):
    """Drives an ApplicationWorkflowRecord all the way to INTERVIEW via the
    real, validated state machine — the only way `applied`/`interview` end
    up in WorkflowStatusHistoryRecord."""
    service = WorkflowService(session)
    workflow = service.start_workflow(user_id=user.id, job_id=job.id)
    StatusManager(service._repository).transition(workflow, WorkflowStatus.DOCUMENTS_GENERATED)
    service.submit_for_review(workflow)
    service.approve(workflow)
    service.mark_ready_to_apply(workflow)
    service.mark_applied(workflow)
    service.mark_interview(workflow)
    return workflow


# --- Favourite / visa notes --------------------------------------------------


def test_favourite_unfavourite_round_trip(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    favourited = service.favourite(user_id=user.id, employer_id=employer.id)
    assert favourited.is_favourite is True
    db_session.commit()

    unfavourited = service.unfavourite(user_id=user.id, employer_id=employer.id)
    assert unfavourited.is_favourite is False
    db_session.commit()


def test_favourite_creates_exactly_one_profile_row_when_called_repeatedly(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    service.favourite(user_id=user.id, employer_id=employer.id)
    db_session.commit()
    service.favourite(user_id=user.id, employer_id=employer.id)
    db_session.commit()

    from job_automation.database.models.employer_profile import EmployerProfile
    from sqlalchemy import select

    rows = db_session.scalars(
        select(EmployerProfile).where(EmployerProfile.user_id == user.id, EmployerProfile.employer_id == employer.id)
    ).all()
    assert len(rows) == 1


def test_update_visa_notes_persists_and_can_be_cleared(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    service.update_visa_notes(
        user_id=user.id, employer_id=employer.id, notes="Historically sponsors Tier 2 visas"
    )
    db_session.commit()
    profile = service.get_profile(user_id=user.id, employer_id=employer.id)
    assert profile.visa_sponsorship_notes == "Historically sponsors Tier 2 visas"

    service.update_visa_notes(user_id=user.id, employer_id=employer.id, notes="")
    db_session.commit()
    profile = service.get_profile(user_id=user.id, employer_id=employer.id)
    assert profile.visa_sponsorship_notes is None


def test_list_favourite_employer_ids(db_session: Session) -> None:
    user = _make_user()
    employer_a = Employer(name="Alpha Trust")
    employer_b = Employer(name="Beta Trust")
    db_session.add_all([user, employer_a, employer_b])
    db_session.commit()

    service = EmployerCrmService(db_session)
    service.favourite(user_id=user.id, employer_id=employer_a.id)
    db_session.commit()

    assert service.list_favourite_employer_ids(user.id) == {employer_a.id}


# --- Departments (shared reference data) ------------------------------------


def test_add_list_remove_department(db_session: Session) -> None:
    employer = Employer(name="Example NHS Trust")
    db_session.add(employer)
    db_session.commit()

    service = EmployerCrmService(db_session)
    department = service.add_department(employer_id=employer.id, name="Emergency Department", location="London")
    db_session.commit()

    departments = service.list_departments(employer.id)
    assert [d.name for d in departments] == ["Emergency Department"]
    assert departments[0].location == "London"

    service.remove_department(department.id)
    db_session.commit()
    assert service.list_departments(employer.id) == []


def test_remove_nonexistent_department_raises(db_session: Session) -> None:
    import uuid

    service = EmployerCrmService(db_session)
    with pytest.raises(ValueError):
        service.remove_department(uuid.uuid4())


# --- Recruiter contacts (per-candidate) -------------------------------------


def test_add_list_remove_contact(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    department = service.add_department(employer_id=employer.id, name="ICU")
    db_session.commit()
    contact = service.add_contact(
        user_id=user.id,
        employer_id=employer.id,
        name="Jane Recruiter",
        role="Recruitment Lead",
        email="jane@example.com",
        department_id=department.id,
    )
    db_session.commit()

    contacts = service.list_contacts(user_id=user.id, employer_id=employer.id)
    assert len(contacts) == 1
    assert contacts[0].department_id == department.id

    service.remove_contact(contact.id, user_id=user.id)
    db_session.commit()
    assert service.list_contacts(user_id=user.id, employer_id=employer.id) == []


def test_contacts_are_isolated_per_user(db_session: Session) -> None:
    owner = _make_user("owner@example.com")
    stranger = _make_user("stranger@example.com")
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([owner, stranger, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    service.add_contact(user_id=owner.id, employer_id=employer.id, name="Owner's contact")
    db_session.commit()

    assert service.list_contacts(user_id=stranger.id, employer_id=employer.id) == []


def test_remove_contact_enforces_ownership(db_session: Session) -> None:
    owner = _make_user("owner2@example.com")
    stranger = _make_user("stranger2@example.com")
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([owner, stranger, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    contact = service.add_contact(user_id=owner.id, employer_id=employer.id, name="Owner's contact")
    db_session.commit()

    with pytest.raises(ValueError):
        service.remove_contact(contact.id, user_id=stranger.id)


# --- Activity timeline: notes + communication history -----------------------


def test_add_note_and_communication_appear_in_combined_timeline(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    contact = service.add_contact(user_id=user.id, employer_id=employer.id, name="Jane Recruiter")
    db_session.commit()
    service.add_note(user_id=user.id, employer_id=employer.id, body="Applied via NHS Jobs portal")
    service.add_communication(
        user_id=user.id,
        employer_id=employer.id,
        channel=CommunicationChannel.PHONE,
        body="Confirmed interview slot",
        contact_id=contact.id,
    )
    db_session.commit()

    timeline = service.list_activity(user_id=user.id, employer_id=employer.id)
    assert {entry.entry_type for entry in timeline} == {"note", "communication"}

    notes_only = service.list_activity(user_id=user.id, employer_id=employer.id, entry_type=ActivityEntryType.NOTE)
    assert len(notes_only) == 1
    assert notes_only[0].body == "Applied via NHS Jobs portal"

    communications_only = service.list_activity(
        user_id=user.id, employer_id=employer.id, entry_type=ActivityEntryType.COMMUNICATION
    )
    assert len(communications_only) == 1
    assert communications_only[0].channel == CommunicationChannel.PHONE.value
    assert communications_only[0].contact_id == contact.id


def test_communication_can_be_backdated(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    service = EmployerCrmService(db_session)
    entry = service.add_communication(
        user_id=user.id, employer_id=employer.id, channel=CommunicationChannel.EMAIL,
        body="Sent CV", occurred_at=yesterday,
    )
    db_session.commit()

    assert entry.occurred_at.date() == yesterday.date()


def test_remove_activity_entry_enforces_ownership(db_session: Session) -> None:
    owner = _make_user("owner3@example.com")
    stranger = _make_user("stranger3@example.com")
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([owner, stranger, employer])
    db_session.commit()

    service = EmployerCrmService(db_session)
    entry = service.add_note(user_id=owner.id, employer_id=employer.id, body="Private note")
    db_session.commit()

    with pytest.raises(ValueError):
        service.remove_activity_entry(entry.id, user_id=stranger.id)

    service.remove_activity_entry(entry.id, user_id=owner.id)
    db_session.commit()
    assert service.list_activity(user_id=owner.id, employer_id=employer.id) == []


# --- EmployerRepository.search() --------------------------------------------


def test_employer_search_by_name_and_type(db_session: Session) -> None:
    employer_a = Employer(name="Alpha NHS Trust", employer_type="nhs_trust")
    employer_b = Employer(name="Beta Care Home", employer_type="care_home")
    db_session.add_all([employer_a, employer_b])
    db_session.commit()

    repo = EmployerRepository(db_session)
    assert [e.name for e in repo.search(EmployerFilter(search="Alpha"))] == ["Alpha NHS Trust"]
    assert [e.name for e in repo.search(EmployerFilter(employer_type="care_home"))] == ["Beta Care Home"]


def test_employer_search_favourite_only_requires_user_scope(db_session: Session) -> None:
    user = _make_user()
    employer_a = Employer(name="Alpha Trust")
    employer_b = Employer(name="Beta Trust")
    db_session.add_all([user, employer_a, employer_b])
    db_session.commit()

    EmployerCrmService(db_session).favourite(user_id=user.id, employer_id=employer_a.id)
    db_session.commit()

    repo = EmployerRepository(db_session)
    favourites = repo.search(EmployerFilter(user_id=user.id, favourite_only=True))
    assert [e.name for e in favourites] == ["Alpha Trust"]

    # Without favourite_only, both employers still show — the join is opt-in.
    everyone = repo.search(EmployerFilter())
    assert {e.name for e in everyone} == {"Alpha Trust", "Beta Trust"}


def test_employer_search_sort_descending(db_session: Session) -> None:
    employer_a = Employer(name="Alpha Trust")
    employer_z = Employer(name="Zulu Trust")
    db_session.add_all([employer_a, employer_z])
    db_session.commit()

    repo = EmployerRepository(db_session)
    descending = repo.search(EmployerFilter(sort_descending=True))
    assert [e.name for e in descending] == ["Zulu Trust", "Alpha Trust"]


# --- AnalyticsService: employer outcome summaries ---------------------------


def test_employer_outcome_summary_counts_applications_interviews(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    _advance_workflow_to_interview(db_session, user=user, job=job)
    db_session.commit()

    summary = AnalyticsService(db_session).employer_outcome_summary(user.id, employer.id)

    assert summary.applications_sent == 1
    assert summary.interviews == 1
    assert summary.offers == 0
    assert summary.rejections == 0
    assert summary.interview_rate == 100.0
    assert summary.offer_rate == 0.0


def test_employer_outcome_summary_for_untouched_employer_is_all_zero(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Untouched Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    summary = AnalyticsService(db_session).employer_outcome_summary(user.id, employer.id)

    assert summary.employer_name == "Untouched Trust"
    assert summary.applications_sent == 0
    assert summary.interview_rate == 0.0


def test_employer_outcome_rejections_come_from_saved_job_not_workflow_status(db_session: Session) -> None:
    """The key architectural regression test: `WorkflowStatus.REJECTED`
    (a reviewer rejecting a drafted document) must NOT be counted as an
    employer rejection. `SavedJob.pipeline_stage == PipelineStage.REJECTED`
    is the only real signal for "the employer rejected the candidate"."""
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job1 = _make_job(employer, "REF-1")
    job2 = _make_job(employer, "REF-2")
    db_session.add_all([user, employer, job1, job2])
    db_session.commit()

    # job1: a document gets reviewer-rejected (WorkflowStatus.REJECTED) —
    # this must NOT count as an employer rejection.
    workflow_service = WorkflowService(db_session)
    workflow1 = workflow_service.start_workflow(user_id=user.id, job_id=job1.id)
    StatusManager(workflow_service._repository).transition(workflow1, WorkflowStatus.DOCUMENTS_GENERATED)
    workflow_service.submit_for_review(workflow1)
    workflow_service.reject(workflow1, reviewer_notes="Needs stronger evidence")
    db_session.commit()

    # job2: the candidate's personal Kanban board says the employer
    # rejected them — this IS the real rejection signal.
    org_service = JobOrganizationService(db_session)
    org_service.update_stage(user_id=user.id, job_id=job2.id, target_stage=PipelineStage.INTERESTED)
    org_service.update_stage(user_id=user.id, job_id=job2.id, target_stage=PipelineStage.REJECTED)
    db_session.commit()

    summary = AnalyticsService(db_session).employer_outcome_summary(user.id, employer.id)

    assert summary.rejections == 1
    assert summary.applications_sent == 0  # job1 never reached "applied"


def test_list_employer_outcome_summaries_sorted_by_applications_desc(db_session: Session) -> None:
    user = _make_user()
    busy_employer = Employer(name="Busy Trust")
    quiet_employer = Employer(name="Quiet Trust")
    busy_job1 = _make_job(busy_employer, "REF-BUSY-1")
    busy_job2 = _make_job(busy_employer, "REF-BUSY-2")
    quiet_job = _make_job(quiet_employer, "REF-QUIET")
    db_session.add_all([user, busy_employer, quiet_employer, busy_job1, busy_job2, quiet_job])
    db_session.commit()

    _advance_workflow_to_interview(db_session, user=user, job=busy_job1)
    db_session.commit()

    workflow_service = WorkflowService(db_session)
    workflow2 = workflow_service.start_workflow(user_id=user.id, job_id=busy_job2.id)
    StatusManager(workflow_service._repository).transition(workflow2, WorkflowStatus.DOCUMENTS_GENERATED)
    workflow_service.submit_for_review(workflow2)
    workflow_service.approve(workflow2)
    workflow_service.mark_ready_to_apply(workflow2)
    workflow_service.mark_applied(workflow2)
    db_session.commit()

    quiet_workflow = workflow_service.start_workflow(user_id=user.id, job_id=quiet_job.id)
    StatusManager(workflow_service._repository).transition(quiet_workflow, WorkflowStatus.DOCUMENTS_GENERATED)
    workflow_service.submit_for_review(quiet_workflow)
    workflow_service.approve(quiet_workflow)
    workflow_service.mark_ready_to_apply(quiet_workflow)
    workflow_service.mark_applied(quiet_workflow)
    db_session.commit()

    summaries = AnalyticsService(db_session).list_employer_outcome_summaries(user.id)

    assert [s.employer_name for s in summaries] == ["Busy Trust", "Quiet Trust"]
    assert summaries[0].applications_sent == 2
    assert summaries[1].applications_sent == 1
