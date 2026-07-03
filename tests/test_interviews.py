"""
Tests for the Interview & Calendar Management milestone's
`job_automation.interviews` package (scheduling, status lifecycle,
checklist, notes, reminders, explicit workflow sync) and the
`AnalyticsService` interview analytics it feeds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.analytics.analytics_service import AnalyticsService
from job_automation.database.models.employer import Employer
from job_automation.database.models.job import Job
from job_automation.database.models.notification import Notification
from job_automation.database.models.user import User
from job_automation.interviews.interview_models import (
    InterviewLifecycle,
    InterviewStatus,
    InterviewType,
    InvalidInterviewStatusTransitionError,
    NoteCategory,
    ReminderOffset,
)
from job_automation.interviews.interview_service import InterviewService
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


def _future_naive(days: int = 3) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(tzinfo=None)


def _advance_workflow_to_applied(session: Session, *, user: User, job: Job):
    service = WorkflowService(session)
    workflow = service.start_workflow(user_id=user.id, job_id=job.id)
    StatusManager(service._repository).transition(workflow, WorkflowStatus.DOCUMENTS_GENERATED)
    service.submit_for_review(workflow)
    service.approve(workflow)
    service.mark_ready_to_apply(workflow)
    service.mark_applied(workflow)
    return workflow


# --- InterviewLifecycle state machine ----------------------------------------


def test_lifecycle_allows_documented_happy_path() -> None:
    assert InterviewLifecycle.can_transition(InterviewStatus.SCHEDULED, InterviewStatus.UPCOMING)
    assert InterviewLifecycle.can_transition(InterviewStatus.UPCOMING, InterviewStatus.COMPLETED)
    assert InterviewLifecycle.can_transition(InterviewStatus.COMPLETED, InterviewStatus.OFFER_RECEIVED)
    assert InterviewLifecycle.can_transition(InterviewStatus.WAITING_DECISION, InterviewStatus.REJECTED)


def test_lifecycle_rejects_skipping_straight_to_offer() -> None:
    assert not InterviewLifecycle.can_transition(InterviewStatus.SCHEDULED, InterviewStatus.OFFER_RECEIVED)
    with pytest.raises(InvalidInterviewStatusTransitionError):
        InterviewLifecycle.validate_transition(InterviewStatus.SCHEDULED, InterviewStatus.OFFER_RECEIVED)


def test_lifecycle_terminal_states() -> None:
    assert InterviewLifecycle.is_terminal(InterviewStatus.CANCELLED)
    assert InterviewLifecycle.is_terminal(InterviewStatus.OFFER_RECEIVED)
    assert InterviewLifecycle.is_terminal(InterviewStatus.REJECTED)
    assert not InterviewLifecycle.is_terminal(InterviewStatus.SCHEDULED)


# --- Scheduling ---------------------------------------------------------------


def test_schedule_seeds_default_checklist_and_reminders(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id,
        employer_id=employer.id,
        interview_type=InterviewType.VIDEO,
        scheduled_at=_future_naive(),
        reminder_offsets=[ReminderOffset.ONE_DAY, ReminderOffset.THIRTY_MINUTES],
    )
    db_session.commit()

    checklist = service.list_checklist(interview.id, user_id=user.id)
    assert len(checklist) == 10
    assert all(not item.is_complete for item in checklist)

    reminders = service.list_reminders(interview.id, user_id=user.id)
    assert {r.offset for r in reminders} == {"one_day", "thirty_minutes"}
    assert interview.status == InterviewStatus.SCHEDULED.value


def test_schedule_publishes_notification(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    InterviewService(db_session).schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert len(notifications) == 1
    assert notifications[0].type == "interview_status_updated"


def test_schedule_normalizes_aware_datetime_to_naive_utc(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    aware = datetime.now(timezone.utc) + timedelta(days=5)
    interview = InterviewService(db_session).schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=aware
    )
    db_session.commit()

    assert interview.scheduled_at.tzinfo is None
    assert interview.scheduled_at == aware.replace(tzinfo=None)


# --- Status transitions --------------------------------------------------------


def test_update_status_valid_transition_publishes_notification(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    updated = service.update_status(interview.id, user_id=user.id, target_status=InterviewStatus.UPCOMING)
    db_session.commit()

    assert updated.status == InterviewStatus.UPCOMING.value
    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert len(notifications) == 2  # one for schedule(), one for this transition


def test_update_status_invalid_transition_raises_and_creates_no_extra_notification(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    with pytest.raises(InvalidInterviewStatusTransitionError):
        service.update_status(interview.id, user_id=user.id, target_status=InterviewStatus.OFFER_RECEIVED)
    db_session.commit()

    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert len(notifications) == 1  # only the original schedule() notification


def test_update_status_with_outcome_persists_outcome_text(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    service.update_status(interview.id, user_id=user.id, target_status=InterviewStatus.UPCOMING)
    service.update_status(interview.id, user_id=user.id, target_status=InterviewStatus.COMPLETED)
    updated = service.update_status(
        interview.id, user_id=user.id, target_status=InterviewStatus.OFFER_RECEIVED, outcome="Offered 26k, start in March"
    )
    db_session.commit()

    assert updated.outcome == "Offered 26k, start in March"


def test_update_status_enforces_ownership(db_session: Session) -> None:
    owner = _make_user("owner@example.com")
    stranger = _make_user("stranger@example.com")
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([owner, stranger, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=owner.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    with pytest.raises(ValueError):
        service.update_status(interview.id, user_id=stranger.id, target_status=InterviewStatus.UPCOMING)


# --- Rescheduling ---------------------------------------------------------------


def test_reschedule_resets_status_and_regenerates_reminders(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    original_time = _future_naive(days=2)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=original_time,
        reminder_offsets=[ReminderOffset.ONE_DAY],
    )
    db_session.commit()
    original_reminder_id = service.list_reminders(interview.id, user_id=user.id)[0].id

    new_time = _future_naive(days=9)
    updated = service.reschedule(interview.id, user_id=user.id, new_scheduled_at=new_time)
    db_session.commit()

    assert updated.status == InterviewStatus.SCHEDULED.value
    assert updated.scheduled_at == new_time
    reminders = service.list_reminders(interview.id, user_id=user.id)
    assert len(reminders) == 1
    assert reminders[0].id != original_reminder_id
    assert reminders[0].remind_at == new_time - ReminderOffset.ONE_DAY.timedelta


def test_reschedule_keeps_already_sent_reminders(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE,
        scheduled_at=_future_naive(days=2), reminder_offsets=[ReminderOffset.ONE_DAY],
    )
    db_session.commit()
    reminder = service.list_reminders(interview.id, user_id=user.id)[0]
    service._reminders.mark_sent(reminder)
    db_session.commit()

    service.reschedule(interview.id, user_id=user.id, new_scheduled_at=_future_naive(days=10))
    db_session.commit()

    reminders = service.list_reminders(interview.id, user_id=user.id)
    assert len(reminders) == 2  # the old sent one + the freshly generated one
    assert sum(1 for r in reminders if r.is_sent) == 1


def test_reschedule_rejects_terminal_status(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    service.update_status(interview.id, user_id=user.id, target_status=InterviewStatus.CANCELLED)
    db_session.commit()

    with pytest.raises(InvalidInterviewStatusTransitionError):
        service.reschedule(interview.id, user_id=user.id, new_scheduled_at=_future_naive(days=20))


# --- Checklist -------------------------------------------------------------------


def test_checklist_add_toggle_remove(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    item = service.add_checklist_item(interview.id, user_id=user.id, label="Research salary bands")
    db_session.commit()
    assert item.is_complete is False

    toggled = service.toggle_checklist_item(item.id, user_id=user.id)
    db_session.commit()
    assert toggled.is_complete is True

    service.remove_checklist_item(item.id, user_id=user.id)
    db_session.commit()
    remaining_labels = [i.label for i in service.list_checklist(interview.id, user_id=user.id)]
    assert "Research salary bands" not in remaining_labels


def test_checklist_completion_percent(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()
    items = service.list_checklist(interview.id, user_id=user.id)
    for item in items[:5]:
        service.toggle_checklist_item(item.id, user_id=user.id)
    db_session.commit()

    assert service.checklist_completion_percent(interview.id, user_id=user.id) == 50.0


def test_toggle_checklist_item_unknown_raises(db_session: Session) -> None:
    import uuid

    user = _make_user()
    db_session.add(user)
    db_session.commit()

    with pytest.raises(ValueError):
        InterviewService(db_session).toggle_checklist_item(uuid.uuid4(), user_id=user.id)


# --- Notes -------------------------------------------------------------------------


def test_notes_add_list_filter_remove(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    service.add_note(interview.id, user_id=user.id, category=NoteCategory.QUESTIONS_ASKED, body="Tell me about yourself")
    note2 = service.add_note(interview.id, user_id=user.id, category=NoteCategory.SALARY_DISCUSSED, body="Discussed £26k")
    db_session.commit()

    all_notes = service.list_notes(interview.id, user_id=user.id)
    assert len(all_notes) == 2

    salary_notes = service.list_notes(interview.id, user_id=user.id, category=NoteCategory.SALARY_DISCUSSED)
    assert len(salary_notes) == 1
    assert salary_notes[0].body == "Discussed £26k"

    service.remove_note(note2.id, user_id=user.id)
    db_session.commit()
    assert len(service.list_notes(interview.id, user_id=user.id)) == 1


# --- Reminders / scheduler task ----------------------------------------------------


def test_process_due_reminders_fires_only_past_due_and_notifies(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    # A larger offset means an *earlier* remind_at for the same
    # scheduled_at, so with the interview only 2 days out, "seven days
    # before" is already in the past (due) while "one day before" is not.
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE,
        scheduled_at=_future_naive(days=2),
        reminder_offsets=[ReminderOffset.ONE_DAY, ReminderOffset.SEVEN_DAYS],
    )
    db_session.commit()

    result = service.process_due_reminders()
    db_session.commit()

    assert result == {"reminders_processed": 1}
    reminders = {r.offset: r.is_sent for r in service.list_reminders(interview.id, user_id=user.id)}
    assert reminders["seven_days"] is True
    assert reminders["one_day"] is False

    notifications = db_session.scalars(
        select(Notification).where(Notification.user_id == user.id, Notification.type == "interview_reminder_due")
    ).all()
    assert len(notifications) == 1


# --- Workflow sync (explicit only) --------------------------------------------------


def test_sync_workflow_status_mark_interview(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    workflow = _advance_workflow_to_applied(db_session, user=user, job=job)
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.VIDEO, scheduled_at=_future_naive(),
        job_id=job.id, application_workflow_id=workflow.id,
    )
    db_session.commit()

    service.sync_workflow_status(interview.id, user_id=user.id, action="mark_interview")
    db_session.commit()
    db_session.refresh(workflow)

    assert workflow.status == WorkflowStatus.INTERVIEW.value


def test_sync_workflow_status_requires_linked_workflow(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    with pytest.raises(ValueError):
        service.sync_workflow_status(interview.id, user_id=user.id, action="mark_interview")


def test_sync_workflow_status_never_happens_automatically(db_session: Session) -> None:
    """Regression: scheduling/updating an interview's status must never,
    by itself, touch the linked ApplicationWorkflowRecord's status."""
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    workflow = _advance_workflow_to_applied(db_session, user=user, job=job)
    db_session.commit()
    status_before = workflow.status

    service = InterviewService(db_session)
    interview = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.VIDEO, scheduled_at=_future_naive(),
        job_id=job.id, application_workflow_id=workflow.id,
    )
    service.update_status(interview.id, user_id=user.id, target_status=InterviewStatus.UPCOMING)
    service.update_status(interview.id, user_id=user.id, target_status=InterviewStatus.COMPLETED)
    db_session.commit()
    db_session.refresh(workflow)

    assert workflow.status == status_before


# --- Dashboard helper -----------------------------------------------------------------


def test_average_upcoming_preparation_completion(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    interview1 = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.VIDEO, scheduled_at=_future_naive(days=4)
    )
    db_session.commit()

    items1 = service.list_checklist(interview1.id, user_id=user.id)
    for item in items1:  # 100% complete
        service.toggle_checklist_item(item.id, user_id=user.id)
    # interview2 left at 0%
    db_session.commit()

    assert service.average_upcoming_preparation_completion(user.id) == 50.0


# --- Analytics ------------------------------------------------------------------------


def test_interview_analytics_summary_counts_and_rates(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    db_session.add_all([user, employer])
    db_session.commit()

    service = InterviewService(db_session)
    service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    completed_with_offer = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.VIDEO, scheduled_at=_future_naive(days=2)
    )
    cancelled = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive(days=3)
    )
    db_session.commit()

    service.update_status(completed_with_offer.id, user_id=user.id, target_status=InterviewStatus.UPCOMING)
    service.update_status(completed_with_offer.id, user_id=user.id, target_status=InterviewStatus.COMPLETED)
    service.update_status(completed_with_offer.id, user_id=user.id, target_status=InterviewStatus.OFFER_RECEIVED)
    service.update_status(cancelled.id, user_id=user.id, target_status=InterviewStatus.CANCELLED)
    db_session.commit()

    summary = AnalyticsService(db_session).interview_analytics_summary(user.id)

    assert summary.scheduled == 1  # `upcoming` is still in SCHEDULED
    assert summary.completed == 1  # completed_with_offer reached offer_received
    assert summary.cancelled == 1
    assert summary.offer_conversion_rate == 100.0
    assert summary.interview_success_rate == 50.0  # 1 completed out of (1 completed + 1 cancelled)
    assert summary.most_successful_employer == "Example NHS Trust"


def test_employer_interview_stats(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    other_employer = Employer(name="Other Trust")
    db_session.add_all([user, employer, other_employer])
    db_session.commit()

    service = InterviewService(db_session)
    service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    service.schedule(
        user_id=user.id, employer_id=other_employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive()
    )
    db_session.commit()

    stats = AnalyticsService(db_session).employer_interview_stats(user.id, employer.id)

    assert stats.total_interviews == 1
    assert stats.completed_interviews == 0


def test_average_days_application_to_interview(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    workflow = _advance_workflow_to_applied(db_session, user=user, job=job)
    db_session.commit()

    scheduled_at = datetime.combine(workflow.created_at.date() + timedelta(days=6), datetime.min.time())
    InterviewService(db_session).schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.VIDEO, scheduled_at=scheduled_at,
        job_id=job.id, application_workflow_id=workflow.id,
    )
    db_session.commit()

    summary = AnalyticsService(db_session).interview_analytics_summary(user.id)
    assert summary.average_days_application_to_interview == 6.0


def test_average_interviews_before_offer(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    workflow = _advance_workflow_to_applied(db_session, user=user, job=job)
    db_session.commit()

    service = InterviewService(db_session)
    first = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.PHONE, scheduled_at=_future_naive(days=1),
        application_workflow_id=workflow.id,
    )
    second = service.schedule(
        user_id=user.id, employer_id=employer.id, interview_type=InterviewType.VIDEO, scheduled_at=_future_naive(days=8),
        application_workflow_id=workflow.id,
    )
    db_session.commit()

    service.update_status(first.id, user_id=user.id, target_status=InterviewStatus.UPCOMING)
    service.update_status(first.id, user_id=user.id, target_status=InterviewStatus.COMPLETED)
    service.update_status(second.id, user_id=user.id, target_status=InterviewStatus.UPCOMING)
    service.update_status(second.id, user_id=user.id, target_status=InterviewStatus.COMPLETED)
    service.update_status(second.id, user_id=user.id, target_status=InterviewStatus.OFFER_RECEIVED)
    db_session.commit()

    summary = AnalyticsService(db_session).interview_analytics_summary(user.id)
    assert summary.average_interviews_before_offer == 2.0
