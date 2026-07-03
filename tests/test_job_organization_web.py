"""
Integration tests for the Job Management milestone's HTML routes
(`routes/job_organization.py`, `routes/board.py`) and JSON API
(`api/job_organization_api.py`) — exercised through a real `TestClient`,
the same decoupled-from-auth pattern `test_web_dashboard.py` already
establishes (`get_current_user`/`get_current_api_user` overridden to "the
most-recently-seeded test user").
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_automation.database import models  # noqa: F401 - registers models on Base.metadata
from job_automation.database.base import Base
from job_automation.database.models import Employer, Job, User
from job_automation.database.models.notification import Notification
from job_automation.database.models.saved_job import SavedJob
from job_automation.job_organization.job_organization_models import PipelineStage
from job_automation.web.app import app, get_current_api_user, get_current_user, get_db_session


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _most_recently_seeded_user(db_session: Session) -> User:
    user = db_session.scalars(select(User).order_by(User.created_at.desc())).first()
    assert user is not None
    return user


@pytest.fixture
def client(db_session: Session):
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _most_recently_seeded_user(db_session)
    app.dependency_overrides[get_current_api_user] = lambda: _most_recently_seeded_user(db_session)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_user_and_job(db_session: Session) -> tuple[User, Job]:
    user = User(email="candidate@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    employer = Employer(name="Example NHS Trust")
    job = Job(
        employer=employer,
        title="Healthcare Assistant",
        location="London",
        source_site="nhs_jobs",
        external_id="REF-1",
        url="https://example.com/job/1",
        is_active=True,
    )
    db_session.add_all([user, employer, job])
    db_session.commit()
    return user, job


# --- HTML routes: flags -----------------------------------------------------


def test_flag_route_favourites_a_job_and_redirects(client: TestClient, db_session: Session) -> None:
    user, job = _seed_user_and_job(db_session)

    response = client.post(f"/jobs/{job.id}/flag", data={"action": "favourite"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{job.id}"
    saved_job = db_session.scalars(select(SavedJob).where(SavedJob.user_id == user.id)).first()
    assert saved_job is not None and saved_job.is_favourite is True


def test_flag_route_rejects_unknown_action(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(f"/jobs/{job.id}/flag", data={"action": "nonsense"}, follow_redirects=False)

    assert response.status_code == 400


def test_flag_route_honours_safe_next_redirect(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(
        f"/jobs/{job.id}/flag", data={"action": "save", "next": "/board"}, follow_redirects=False
    )

    assert response.headers["location"] == "/board"


def test_flag_route_rejects_offsite_next_redirect(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(
        f"/jobs/{job.id}/flag", data={"action": "save", "next": "//evil.example.com"}, follow_redirects=False
    )

    assert response.headers["location"] == f"/jobs/{job.id}"


# --- HTML routes: pipeline stage ---------------------------------------------


def test_stage_route_valid_transition_creates_notification(client: TestClient, db_session: Session) -> None:
    user, job = _seed_user_and_job(db_session)

    response = client.post(
        f"/jobs/{job.id}/stage", data={"target_stage": PipelineStage.INTERESTED.value}, follow_redirects=False
    )

    assert response.status_code == 303
    saved_job = db_session.scalars(select(SavedJob).where(SavedJob.user_id == user.id)).first()
    assert saved_job.pipeline_stage == PipelineStage.INTERESTED.value
    notifications = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert len(notifications) == 1


def test_stage_route_invalid_transition_returns_400(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(
        f"/jobs/{job.id}/stage", data={"target_stage": PipelineStage.OFFER.value}, follow_redirects=False
    )

    assert response.status_code == 400


def test_stage_route_rejects_unknown_stage_name(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(f"/jobs/{job.id}/stage", data={"target_stage": "not-a-stage"}, follow_redirects=False)

    assert response.status_code == 400


# --- HTML routes: details/tags/checklist ------------------------------------


def test_details_route_persists_notes_rating_deadline(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    deadline_str = (date.today() + timedelta(days=10)).isoformat()

    response = client.post(
        f"/jobs/{job.id}/details",
        data={"notes": "Strong candidate fit.", "personal_rating": "5", "priority": "high", "deadline": deadline_str},
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved_job = db_session.scalars(select(SavedJob)).first()
    assert saved_job.notes == "Strong candidate fit."
    assert saved_job.personal_rating == 5
    assert saved_job.priority == "high"
    assert saved_job.deadline.isoformat() == deadline_str


def test_details_route_rejects_out_of_range_rating(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(
        f"/jobs/{job.id}/details", data={"personal_rating": "9"}, follow_redirects=False
    )

    assert response.status_code == 400


def test_tags_route_replaces_full_list(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    client.post(f"/jobs/{job.id}/tags", data={"tags": "urgent, icu"}, follow_redirects=False)

    saved_job = db_session.scalars(select(SavedJob)).first()
    assert saved_job.tags == ["urgent", "icu"]


def test_checklist_add_toggle_remove_routes(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    client.post(f"/jobs/{job.id}/checklist", data={"label": "Update CV"}, follow_redirects=False)
    saved_job = db_session.scalars(select(SavedJob)).first()
    assert saved_job.checklist == [{"label": "Update CV", "done": False}]

    client.post(f"/jobs/{job.id}/checklist/0/toggle", follow_redirects=False)
    db_session.refresh(saved_job)
    assert saved_job.checklist[0]["done"] is True

    client.post(f"/jobs/{job.id}/checklist/0/remove", follow_redirects=False)
    db_session.refresh(saved_job)
    assert not saved_job.checklist  # emptied list is stored as None, not []


def test_checklist_toggle_out_of_range_returns_404(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(f"/jobs/{job.id}/checklist/3/toggle", follow_redirects=False)

    assert response.status_code == 404


# --- HTML routes: reminders --------------------------------------------------


def test_reminder_create_and_delete_routes(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    remind_at = (datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None).isoformat()

    response = client.post(
        f"/jobs/{job.id}/reminders",
        data={"reminder_type": "deadline", "remind_at": remind_at, "message": "Submit application"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    saved_job = db_session.scalars(select(SavedJob)).first()
    assert len(saved_job.reminders) == 1
    reminder_id = saved_job.reminders[0].id

    delete_response = client.post(
        f"/reminders/{reminder_id}/delete", data={"job_id": str(job.id)}, follow_redirects=False
    )
    assert delete_response.status_code == 303
    db_session.refresh(saved_job)
    assert saved_job.reminders == []


# --- JSON API -----------------------------------------------------------------


def test_api_flag_and_get_saved_job(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(f"/api/job-organization/{job.id}/flag", json={"action": "favourite"})
    assert response.status_code == 200
    body = response.json()
    assert body["is_favourite"] is True
    assert body["job_title"] == "Healthcare Assistant"

    get_response = client.get(f"/api/job-organization/{job.id}")
    assert get_response.status_code == 200
    assert get_response.json()["is_favourite"] is True


def test_api_get_saved_job_404s_before_any_tracking_exists(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.get(f"/api/job-organization/{job.id}")

    assert response.status_code == 404


def test_api_stage_transition_invalid_returns_400(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)

    response = client.post(f"/api/job-organization/{job.id}/stage", json={"target_stage": "offer"})

    assert response.status_code == 400


def test_api_reminders_create_and_list_upcoming(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    remind_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    create_response = client.post(
        f"/api/job-organization/{job.id}/reminders",
        json={"reminder_type": "interview", "remind_at": remind_at},
    )
    assert create_response.status_code == 200

    list_response = client.get("/api/job-organization/reminders/upcoming")
    assert list_response.status_code == 200
    reminders = list_response.json()
    assert len(reminders) == 1
    assert reminders[0]["reminder_type"] == "interview"


# --- /jobs list + /api/jobs: user-scoped filters -----------------------------


def test_jobs_list_page_excludes_hidden_job_by_default(client: TestClient, db_session: Session) -> None:
    user, job = _seed_user_and_job(db_session)
    client.post(f"/jobs/{job.id}/flag", data={"action": "hide"}, follow_redirects=False)

    response = client.get("/jobs")

    assert response.status_code == 200
    assert job.title not in response.text


def test_jobs_list_page_favourite_filter(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    employer2 = Employer(name="Other Trust")
    other_job = Job(
        employer=employer2,
        title="Support Worker",
        source_site="nhs_jobs",
        external_id="REF-OTHER",
        url="https://example.com/job/other",
        is_active=True,
    )
    db_session.add(employer2)
    db_session.add(other_job)
    db_session.commit()

    client.post(f"/jobs/{job.id}/flag", data={"action": "favourite"}, follow_redirects=False)

    response = client.get("/jobs", params={"favourite": "true"})

    assert response.status_code == 200
    assert job.title in response.text
    assert other_job.title not in response.text


def test_api_jobs_list_supports_new_filters(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    job.salary_min = 20000
    job.salary_max = 25000
    db_session.commit()

    response = client.get("/api/jobs", params={"max_salary": 100000, "keywords": "Healthcare"})

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert str(job.id) in ids


def test_api_jobs_list_excludes_archived_unless_requested(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    client.post(f"/api/job-organization/{job.id}/flag", json={"action": "archive"})

    default_response = client.get("/api/jobs")
    assert str(job.id) not in [row["id"] for row in default_response.json()]

    archived_response = client.get("/api/jobs", params={"archived": "true"})
    assert str(job.id) in [row["id"] for row in archived_response.json()]


def test_job_detail_page_renders_with_saved_job_context(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    client.post(f"/jobs/{job.id}/flag", data={"action": "save"}, follow_redirects=False)

    response = client.get(f"/jobs/{job.id}")

    assert response.status_code == 200


# --- Board page ---------------------------------------------------------------


def test_board_page_renders_and_groups_by_stage(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    client.post(
        f"/jobs/{job.id}/stage", data={"target_stage": PipelineStage.INTERESTED.value}, follow_redirects=False
    )

    response = client.get("/board")

    assert response.status_code == 200
    assert job.title in response.text


def test_board_stage_move_honours_next_redirect(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    client.post(
        f"/jobs/{job.id}/stage", data={"target_stage": PipelineStage.INTERESTED.value}, follow_redirects=False
    )

    response = client.post(
        f"/jobs/{job.id}/stage",
        data={"target_stage": PipelineStage.DOCUMENTS_READY.value, "next": "/board"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/board"


# --- Dashboard page -------------------------------------------------------------


def test_dashboard_page_renders_job_organization_section(client: TestClient, db_session: Session) -> None:
    _, job = _seed_user_and_job(db_session)
    client.post(f"/jobs/{job.id}/flag", data={"action": "favourite"}, follow_redirects=False)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Job organization" in response.text
