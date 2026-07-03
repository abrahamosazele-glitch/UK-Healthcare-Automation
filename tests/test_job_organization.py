"""
Tests for the Job Management milestone's `job_automation.job_organization`
package (save/favourite/hide/archive flags, the Kanban pipeline state
machine, notes/rating/tags/checklist tracking details, reminders) and the
`AnalyticsService.job_organization_summary()` extension it feeds.

Notification-side effects (pipeline transitions and due reminders create a
`Notification` row) are covered here too, since "every transition must
create a notification" is an explicit requirement — not left to a separate
notifications test file, since this is the producer side of that contract.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.analytics.analytics_models import NamedCount
from job_automation.analytics.analytics_service import AnalyticsService
from job_automation.database.models.employer import Employer
from job_automation.database.models.job import Job
from job_automation.database.models.notification import Notification
from job_automation.database.models.user import User
from job_automation.job_organization.job_organization_models import (
    InvalidStageTransitionError,
    JobPipeline,
    JobPriority,
    PipelineStage,
    ReminderType,
)
from job_automation.job_organization.job_organization_service import JobOrganizationService
from job_automation.job_organization.reminder_service import ReminderService


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


# --- JobPipeline state machine -------------------------------------------


def test_pipeline_allows_documented_happy_path_transitions() -> None:
    assert JobPipeline.can_transition(PipelineStage.NEW, PipelineStage.INTERESTED)
    assert JobPipeline.can_transition(PipelineStage.INTERESTED, PipelineStage.DOCUMENTS_READY)
    assert JobPipeline.can_transition(PipelineStage.DOCUMENTS_READY, PipelineStage.APPLIED)
    assert JobPipeline.can_transition(PipelineStage.APPLIED, PipelineStage.INTERVIEW)
    assert JobPipeline.can_transition(PipelineStage.INTERVIEW, PipelineStage.OFFER)


def test_pipeline_rejects_skipping_stages() -> None:
    assert not JobPipeline.can_transition(PipelineStage.NEW, PipelineStage.APPLIED)
    with pytest.raises(InvalidStageTransitionError):
        JobPipeline.validate_transition(PipelineStage.NEW, PipelineStage.APPLIED)


def test_pipeline_rejected_is_terminal() -> None:
    assert JobPipeline.is_terminal(PipelineStage.REJECTED)
    assert JobPipeline.allowed_next_stages(PipelineStage.REJECTED) == frozenset()


# --- Organization flags ---------------------------------------------------


def test_save_favourite_hide_archive_restore_round_trip(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)

    saved = service.save(user_id=user.id, job_id=job.id)
    assert saved.is_saved is True
    db_session.commit()

    favourited = service.favourite(user_id=user.id, job_id=job.id)
    assert favourited.is_saved is True and favourited.is_favourite is True
    db_session.commit()

    hidden = service.hide(user_id=user.id, job_id=job.id)
    assert hidden.is_hidden is True
    db_session.commit()

    unhidden = service.unhide(user_id=user.id, job_id=job.id)
    assert unhidden.is_hidden is False
    db_session.commit()

    archived = service.archive(user_id=user.id, job_id=job.id)
    assert archived.is_archived is True
    db_session.commit()

    restored = service.restore(user_id=user.id, job_id=job.id)
    assert restored.is_archived is False
    db_session.commit()


def test_favourite_creates_exactly_one_saved_job_row_when_called_repeatedly(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    service.favourite(user_id=user.id, job_id=job.id)
    db_session.commit()
    service.favourite(user_id=user.id, job_id=job.id)
    db_session.commit()

    from job_automation.database.models.saved_job import SavedJob

    rows = db_session.scalars(
        select(SavedJob).where(SavedJob.user_id == user.id, SavedJob.job_id == job.id)
    ).all()
    assert len(rows) == 1


# --- Kanban pipeline transitions + notifications --------------------------


def test_update_stage_valid_transition_publishes_notification(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    updated = service.update_stage(user_id=user.id, job_id=job.id, target_stage=PipelineStage.INTERESTED)
    db_session.commit()

    assert updated.pipeline_stage == PipelineStage.INTERESTED.value

    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert len(notifications) == 1
    assert notifications[0].type == "pipeline_stage_updated"


def test_update_stage_invalid_transition_raises_and_creates_no_notification(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    with pytest.raises(InvalidStageTransitionError):
        service.update_stage(user_id=user.id, job_id=job.id, target_stage=PipelineStage.OFFER)
    db_session.commit()

    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert notifications == []


def test_flag_toggles_do_not_create_notifications(db_session: Session) -> None:
    """Documented scope decision: only pipeline transitions notify, not
    save/favourite/hide/archive flag toggles."""
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    service.save(user_id=user.id, job_id=job.id)
    service.favourite(user_id=user.id, job_id=job.id)
    service.hide(user_id=user.id, job_id=job.id)
    service.archive(user_id=user.id, job_id=job.id)
    db_session.commit()

    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert notifications == []


# --- Tracking details: notes/rating/priority/deadline/tags/checklist -----


def test_update_details_validates_personal_rating_range(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    with pytest.raises(ValueError):
        service.update_details(user_id=user.id, job_id=job.id, personal_rating=6)
    with pytest.raises(ValueError):
        service.update_details(user_id=user.id, job_id=job.id, personal_rating=0)


def test_update_details_persists_notes_rating_priority_deadline(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    deadline = date.today() + timedelta(days=14)
    updated = service.update_details(
        user_id=user.id,
        job_id=job.id,
        notes="Great fit for my ICU experience.",
        personal_rating=4,
        priority=JobPriority.HIGH,
        deadline=deadline,
    )
    db_session.commit()

    assert updated.notes == "Great fit for my ICU experience."
    assert updated.personal_rating == 4
    assert updated.priority == JobPriority.HIGH.value
    assert updated.deadline == deadline


def test_set_tags_replaces_full_list_and_strips_blanks(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    service.set_tags(user_id=user.id, job_id=job.id, tags=["urgent", "  ", "icu"])
    db_session.commit()
    first = service.get_for_user_and_job(user_id=user.id, job_id=job.id)
    assert first.tags == ["urgent", "icu"]

    service.set_tags(user_id=user.id, job_id=job.id, tags=["night-shift"])
    db_session.commit()
    second = service.get_for_user_and_job(user_id=user.id, job_id=job.id)
    assert second.tags == ["night-shift"]


def test_checklist_add_toggle_remove(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = JobOrganizationService(db_session)
    service.add_checklist_item(user_id=user.id, job_id=job.id, label="Update CV")
    service.add_checklist_item(user_id=user.id, job_id=job.id, label="Request reference")
    db_session.commit()

    saved_job = service.get_for_user_and_job(user_id=user.id, job_id=job.id)
    assert saved_job.checklist == [
        {"label": "Update CV", "done": False},
        {"label": "Request reference", "done": False},
    ]

    service.toggle_checklist_item(user_id=user.id, job_id=job.id, index=0)
    db_session.commit()
    saved_job = service.get_for_user_and_job(user_id=user.id, job_id=job.id)
    assert saved_job.checklist[0]["done"] is True

    service.remove_checklist_item(user_id=user.id, job_id=job.id, index=1)
    db_session.commit()
    saved_job = service.get_for_user_and_job(user_id=user.id, job_id=job.id)
    assert len(saved_job.checklist) == 1

    with pytest.raises(IndexError):
        service.toggle_checklist_item(user_id=user.id, job_id=job.id, index=5)


# --- Reminders --------------------------------------------------------------


def test_create_reminder_and_list_upcoming_for_user(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = ReminderService(db_session)
    remind_at = datetime.now(timezone.utc) + timedelta(days=2)
    service.create_reminder(
        user_id=user.id, job_id=job.id, reminder_type=ReminderType.DEADLINE, remind_at=remind_at
    )
    db_session.commit()

    upcoming = service.list_upcoming_for_user(user.id)
    assert len(upcoming) == 1
    assert upcoming[0].reminder_type == ReminderType.DEADLINE.value


def test_process_due_reminders_fires_only_past_due_ones_and_notifies(db_session: Session) -> None:
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = ReminderService(db_session)
    past_due = service.create_reminder(
        user_id=user.id,
        job_id=job.id,
        reminder_type=ReminderType.INTERVIEW,
        remind_at=datetime.now(timezone.utc) - timedelta(hours=1),
        message="Interview tomorrow morning.",
    )
    future = service.create_reminder(
        user_id=user.id,
        job_id=job.id,
        reminder_type=ReminderType.DOCUMENTS_NEEDED,
        remind_at=datetime.now(timezone.utc) + timedelta(days=5),
    )
    db_session.commit()

    result = service.process_due_reminders()
    db_session.commit()

    assert result == {"reminders_processed": 1}
    db_session.refresh(past_due)
    db_session.refresh(future)
    assert past_due.is_sent is True
    assert future.is_sent is False

    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert len(notifications) == 1
    assert notifications[0].type == "reminder_due"


def test_delete_reminder_enforces_ownership(db_session: Session) -> None:
    owner = _make_user("owner@example.com")
    stranger = _make_user("stranger@example.com")
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([owner, stranger, employer, job])
    db_session.commit()

    service = ReminderService(db_session)
    reminder = service.create_reminder(
        user_id=owner.id,
        job_id=job.id,
        reminder_type=ReminderType.REFERENCE_REQUEST,
        remind_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.commit()

    with pytest.raises(ValueError):
        service.delete_reminder(reminder.id, user_id=stranger.id)

    service.delete_reminder(reminder.id, user_id=owner.id)
    db_session.commit()
    assert service.list_upcoming_for_user(owner.id) == []


# --- Analytics: job_organization_summary ------------------------------------


def test_job_organization_summary_counts_flags_and_stages(db_session: Session) -> None:
    user = _make_user()
    employer_a = Employer(name="Alpha Trust")
    employer_b = Employer(name="Beta Trust")
    job1 = _make_job(employer_a, "REF-1")
    job2 = _make_job(employer_a, "REF-2")
    job3 = _make_job(employer_b, "REF-3")
    db_session.add_all([user, employer_a, employer_b, job1, job2, job3])
    db_session.commit()

    org = JobOrganizationService(db_session)
    org.favourite(user_id=user.id, job_id=job1.id)  # favourite -> Alpha Trust
    org.save(user_id=user.id, job_id=job2.id)
    org.archive(user_id=user.id, job_id=job3.id)
    org.update_stage(user_id=user.id, job_id=job1.id, target_stage=PipelineStage.INTERESTED)
    org.update_stage(user_id=user.id, job_id=job1.id, target_stage=PipelineStage.DOCUMENTS_READY)
    org.update_stage(user_id=user.id, job_id=job1.id, target_stage=PipelineStage.APPLIED)
    org.update_stage(user_id=user.id, job_id=job1.id, target_stage=PipelineStage.INTERVIEW)
    org.update_details(user_id=user.id, job_id=job2.id, deadline=date.today() + timedelta(days=5))
    db_session.commit()

    reminder_service = ReminderService(db_session)
    reminder_service.create_reminder(
        user_id=user.id,
        job_id=job2.id,
        reminder_type=ReminderType.DEADLINE,
        remind_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    db_session.commit()

    summary = AnalyticsService(db_session).job_organization_summary(user.id)

    # get_or_create() sets is_saved=True on first creation, so every job
    # that got a SavedJob row at all (via favourite/save/archive) counts.
    assert summary.jobs_saved == 3
    assert summary.jobs_favourited == 1
    assert summary.jobs_archived == 1
    assert summary.applications == 1  # job1 reached INTERVIEW (>= applied)
    assert summary.interviews == 1
    assert summary.offers == 0
    assert summary.rejected == 0
    assert summary.upcoming_deadlines == 1
    assert summary.upcoming_reminders == 1
    assert summary.favourite_employers == (NamedCount(name="Alpha Trust", count=1),)
    stage_lookup = {row.stage: row.count for row in summary.stage_counts}
    assert stage_lookup[PipelineStage.INTERVIEW.value] == 1
    assert stage_lookup[PipelineStage.NEW.value] == 2  # job2 and job3 never moved off "new"
    assert sum(stage_lookup.values()) == 3


# --- SavedJobRepository.map_by_job_id ---------------------------------------


def test_map_by_job_id_returns_empty_dict_for_no_job_ids(db_session: Session) -> None:
    from job_automation.job_organization.saved_job_repository import SavedJobRepository

    user = _make_user()
    db_session.add(user)
    db_session.commit()

    assert SavedJobRepository(db_session).map_by_job_id(user.id, []) == {}


def test_map_by_job_id_only_returns_rows_that_exist(db_session: Session) -> None:
    from job_automation.job_organization.saved_job_repository import SavedJobRepository

    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    tracked_job = _make_job(employer, "REF-TRACKED")
    untracked_job = _make_job(employer, "REF-UNTRACKED")
    db_session.add_all([user, employer, tracked_job, untracked_job])
    db_session.commit()

    JobOrganizationService(db_session).save(user_id=user.id, job_id=tracked_job.id)
    db_session.commit()

    result = SavedJobRepository(db_session).map_by_job_id(user.id, [tracked_job.id, untracked_job.id])

    assert set(result.keys()) == {tracked_job.id}


# --- Regression: PipelineStage vs WorkflowStatus stay independent ----------


def test_pipeline_stage_and_workflow_status_do_not_collide_on_rejected(db_session: Session) -> None:
    """The central architecture decision of this milestone: `PipelineStage
    .REJECTED` ("the employer rejected the application," terminal) and
    `WorkflowStatus.REJECTED` ("a reviewer rejected the drafted document,"
    loops back to DOCUMENTS_GENERATED) are deliberately separate enums
    stored in separate tables. This test proves the two "rejected" states
    for the same (user, job) genuinely don't interfere with each other."""
    from job_automation.workflows.status_manager import StatusManager
    from job_automation.workflows.workflow_models import WorkflowStatus
    from job_automation.workflows.workflow_repository import WorkflowRepository
    from job_automation.workflows.workflow_service import WorkflowService

    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    workflow_repository = WorkflowRepository(db_session)
    workflow_service = WorkflowService(db_session, repository=workflow_repository)
    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id)
    StatusManager(workflow_repository).transition(workflow, WorkflowStatus.DOCUMENTS_GENERATED)
    workflow_service.submit_for_review(workflow)
    workflow_service.reject(workflow, reviewer_notes="Needs stronger evidence of DBS check")
    db_session.commit()

    org_service = JobOrganizationService(db_session)
    org_service.update_stage(user_id=user.id, job_id=job.id, target_stage=PipelineStage.INTERESTED)
    org_service.update_stage(user_id=user.id, job_id=job.id, target_stage=PipelineStage.REJECTED)
    db_session.commit()

    saved_job = org_service.get_for_user_and_job(user_id=user.id, job_id=job.id)
    db_session.refresh(workflow)

    # The document-review "rejected" is non-terminal (loops back), the
    # candidate's Kanban "rejected" is terminal — both true simultaneously,
    # proving they're independent state machines over independent data.
    assert workflow.status == WorkflowStatus.REJECTED.value
    assert saved_job.pipeline_stage == PipelineStage.REJECTED.value
    assert JobPipeline.is_terminal(PipelineStage.REJECTED) is True
    from job_automation.workflows.application_workflow import ApplicationWorkflow

    assert ApplicationWorkflow.is_terminal(WorkflowStatus.REJECTED) is False


# --- Regression: cascade deletes -------------------------------------------
#
# `SavedJob`/`JobReminder`'s relationships use `passive_deletes=True` (see
# their model docstrings), which means SQLAlchemy relies entirely on the
# database's own `ON DELETE CASCADE` rather than deleting children in
# Python — so this only actually cascades if SQLite's FK enforcement is on.
# `db_manager.py` turns that on for the app's real engine; the shared
# `conftest.py` in-memory `db_session` fixture does not, so these two tests
# build their own engine with `PRAGMA foreign_keys=ON` rather than relying
# on (or changing) the shared fixture other tests depend on.


@pytest.fixture
def fk_enforced_session():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from job_automation.database.base import Base

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_deleting_job_cascades_to_saved_job_and_reminders(fk_enforced_session: Session) -> None:
    db_session = fk_enforced_session
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    JobOrganizationService(db_session).save(user_id=user.id, job_id=job.id)
    ReminderService(db_session).create_reminder(
        user_id=user.id,
        job_id=job.id,
        reminder_type=ReminderType.DEADLINE,
        remind_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.commit()

    from job_automation.database.models.job_reminder import JobReminder
    from job_automation.database.models.saved_job import SavedJob

    db_session.delete(job)
    db_session.commit()

    assert db_session.scalars(select(SavedJob)).all() == []
    assert db_session.scalars(select(JobReminder)).all() == []


def test_deleting_user_cascades_to_saved_job_and_reminders(fk_enforced_session: Session) -> None:
    db_session = fk_enforced_session
    user = _make_user()
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer)
    db_session.add_all([user, employer, job])
    db_session.commit()

    JobOrganizationService(db_session).save(user_id=user.id, job_id=job.id)
    ReminderService(db_session).create_reminder(
        user_id=user.id,
        job_id=job.id,
        reminder_type=ReminderType.DEADLINE,
        remind_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.commit()

    from job_automation.database.models.job_reminder import JobReminder
    from job_automation.database.models.saved_job import SavedJob

    db_session.delete(user)
    db_session.commit()

    assert db_session.scalars(select(SavedJob)).all() == []
    assert db_session.scalars(select(JobReminder)).all() == []
