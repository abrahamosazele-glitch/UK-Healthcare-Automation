"""
Integration tests for the Interview & Calendar Management milestone's HTML
routes (`routes/interviews.py`, `routes/calendar.py`) and JSON API
(`api/interviews_api.py`) — exercised through a real `TestClient`, the
same decoupled-from-auth pattern established by `test_web_dashboard.py`/
`test_employer_crm_web.py` (`get_current_user`/`get_current_api_user`
overridden to "the most-recently-seeded test user"). Also covers the
dashboard's new Interview widgets section, the Employer profile page's
interview history section, and the Applications page's interview column —
the cross-module integration points this milestone explicitly requires.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_automation.database import models  # noqa: F401 - registers models on Base.metadata
from job_automation.database.base import Base
from job_automation.database.models import Employer, Job, User
from job_automation.database.models.interview_record import InterviewRecord
from job_automation.web.app import app, get_current_api_user, get_current_user, get_db_session
from job_automation.workflows.status_manager import StatusManager
from job_automation.workflows.workflow_models import WorkflowStatus
from job_automation.workflows.workflow_service import WorkflowService


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


def _seed_user_and_employer(db_session: Session) -> tuple[User, Employer]:
    user = User(email="candidate@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    employer = Employer(name="Riverside NHS Foundation Trust")
    db_session.add_all([user, employer])
    db_session.commit()
    return user, employer


def _future_local_str(days: int = 3) -> str:
    when = datetime.now(timezone.utc) + timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M")


# --- HTML: schedule form + detail page --------------------------------------


def test_new_interview_form_renders_with_preselected_employer(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.get("/interviews/new", params={"employer_id": str(employer.id)})

    assert response.status_code == 200
    assert employer.name in response.text


def test_create_interview_schedules_and_redirects(client: TestClient, db_session: Session) -> None:
    user, employer = _seed_user_and_employer(db_session)

    response = client.post(
        "/interviews/new",
        data={
            "employer_id": str(employer.id),
            "interview_type": "video",
            "scheduled_at": _future_local_str(),
            "reminder_offsets": ["one_day", "thirty_minutes"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    interview = db_session.scalars(select(InterviewRecord).where(InterviewRecord.user_id == user.id)).first()
    assert interview is not None
    assert interview.interview_type == "video"
    assert response.headers["location"] == f"/interviews/{interview.id}"


def test_interview_detail_page_renders(client: TestClient, db_session: Session) -> None:
    user, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )
    interview = db_session.scalars(select(InterviewRecord)).first()

    response = client.get(f"/interviews/{interview.id}")

    assert response.status_code == 200
    assert "Preparation checklist" in response.text
    assert "Research employer" in response.text


def test_interview_detail_404s_for_unknown_interview(client: TestClient, db_session: Session) -> None:
    _seed_user_and_employer(db_session)

    response = client.get("/interviews/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


# --- HTML: status/reschedule -------------------------------------------------


def test_status_transition_route(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )
    interview = db_session.scalars(select(InterviewRecord)).first()

    response = client.post(
        f"/interviews/{interview.id}/status", data={"target_status": "upcoming"}, follow_redirects=False
    )

    assert response.status_code == 303
    db_session.refresh(interview)
    assert interview.status == "upcoming"


def test_status_transition_route_rejects_invalid_move(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )
    interview = db_session.scalars(select(InterviewRecord)).first()

    response = client.post(
        f"/interviews/{interview.id}/status", data={"target_status": "offer_received"}, follow_redirects=False
    )

    assert response.status_code == 400


def test_reschedule_route(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )
    interview = db_session.scalars(select(InterviewRecord)).first()
    new_time = _future_local_str(days=10)

    response = client.post(
        f"/interviews/{interview.id}/reschedule", data={"new_scheduled_at": new_time}, follow_redirects=False
    )

    assert response.status_code == 303
    db_session.refresh(interview)
    assert interview.status == "scheduled"


# --- HTML: checklist/notes ------------------------------------------------------


def test_checklist_toggle_and_remove_routes(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )
    interview = db_session.scalars(select(InterviewRecord)).first()

    add_response = client.post(
        f"/interviews/{interview.id}/checklist", data={"label": "Confirm parking"}, follow_redirects=False
    )
    assert add_response.status_code == 303
    assert "Confirm parking" in client.get(f"/interviews/{interview.id}").text


def test_notes_add_and_delete_routes(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )
    interview = db_session.scalars(select(InterviewRecord)).first()

    client.post(
        f"/interviews/{interview.id}/notes",
        data={"category": "questions_asked", "body": "Tell me about a difficult patient"},
        follow_redirects=False,
    )
    response = client.get(f"/interviews/{interview.id}")
    assert "Tell me about a difficult patient" in response.text


# --- Calendar ------------------------------------------------------------------


def test_calendar_month_view_shows_scheduled_interview(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    scheduled_str = _future_local_str(days=3)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": scheduled_str},
        follow_redirects=False,
    )
    target_date = scheduled_str.split("T")[0]

    response = client.get("/calendar", params={"view": "month", "date_str": target_date})

    assert response.status_code == 200
    assert employer.name in response.text


def test_calendar_day_view(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    scheduled_str = _future_local_str(days=1)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": scheduled_str},
        follow_redirects=False,
    )
    target_date = scheduled_str.split("T")[0]

    response = client.get("/calendar", params={"view": "day", "date_str": target_date})

    assert response.status_code == 200
    assert employer.name in response.text


def test_calendar_week_view(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.get("/calendar", params={"view": "week"})

    assert response.status_code == 200


# --- Dashboard integration -------------------------------------------------------


def test_dashboard_renders_interview_widgets(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "phone", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Interviews this week" in response.text
    assert "Next interview in" in response.text
    assert "Preparation completion" in response.text


# --- Employer integration ---------------------------------------------------------


def test_employer_detail_page_shows_interview_history(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(
        "/interviews/new",
        data={"employer_id": str(employer.id), "interview_type": "video", "scheduled_at": _future_local_str()},
        follow_redirects=False,
    )

    response = client.get(f"/employers/{employer.id}")

    assert response.status_code == 200
    assert "Interview history" in response.text
    assert "Video" in response.text


# --- Application integration (workflow sync) --------------------------------------


def test_application_page_shows_schedule_link_and_workflow_sync(client: TestClient, db_session: Session) -> None:
    user, employer = _seed_user_and_employer(db_session)
    job = Job(
        employer=employer, title="Healthcare Assistant", source_site="s", external_id="1",
        url="https://example.com/1", is_active=True,
    )
    db_session.add(job)
    db_session.commit()

    workflow_service = WorkflowService(db_session)
    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id)
    StatusManager(workflow_service._repository).transition(workflow, WorkflowStatus.DOCUMENTS_GENERATED)
    workflow_service.submit_for_review(workflow)
    workflow_service.approve(workflow)
    workflow_service.mark_ready_to_apply(workflow)
    workflow_service.mark_applied(workflow)
    db_session.commit()

    applications_response = client.get("/applications")
    assert "Schedule" in applications_response.text

    create_response = client.post(
        "/interviews/new",
        data={
            "employer_id": str(employer.id),
            "interview_type": "phone",
            "scheduled_at": _future_local_str(),
            "job_id": str(job.id),
            "application_workflow_id": str(workflow.id),
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 303
    interview_id = create_response.headers["location"].rsplit("/", 1)[-1]

    sync_response = client.post(
        f"/interviews/{interview_id}/workflow-sync", data={"action": "mark_interview"}, follow_redirects=False
    )
    assert sync_response.status_code == 303
    db_session.refresh(workflow)
    assert workflow.status == "interview"

    applications_response = client.get("/applications")
    assert "Scheduled" in applications_response.text


# --- JSON API ------------------------------------------------------------------------


def test_api_schedule_and_get_interview(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.post(
        "/api/interviews",
        json={
            "employer_id": str(employer.id),
            "interview_type": "video",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["interview_type"] == "video"

    get_response = client.get(f"/api/interviews/{body['id']}")
    assert get_response.status_code == 200


def test_api_status_and_reschedule(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    create_response = client.post(
        "/api/interviews",
        json={
            "employer_id": str(employer.id),
            "interview_type": "phone",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        },
    )
    interview_id = create_response.json()["id"]

    status_response = client.post(f"/api/interviews/{interview_id}/status", json={"target_status": "upcoming"})
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "upcoming"

    bad_status_response = client.post(
        f"/api/interviews/{interview_id}/status", json={"target_status": "offer_received"}
    )
    assert bad_status_response.status_code == 400

    reschedule_response = client.post(
        f"/api/interviews/{interview_id}/reschedule",
        json={"new_scheduled_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()},
    )
    assert reschedule_response.status_code == 200
    assert reschedule_response.json()["status"] == "scheduled"


def test_api_checklist_and_notes_crud(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    create_response = client.post(
        "/api/interviews",
        json={
            "employer_id": str(employer.id),
            "interview_type": "phone",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        },
    )
    interview_id = create_response.json()["id"]

    checklist_response = client.get(f"/api/interviews/{interview_id}/checklist")
    assert len(checklist_response.json()) == 10

    add_item_response = client.post(f"/api/interviews/{interview_id}/checklist", json={"label": "Confirm parking"})
    item_id = add_item_response.json()["id"]

    toggle_response = client.post(f"/api/interviews/{interview_id}/checklist/{item_id}/toggle")
    assert toggle_response.json()["is_complete"] is True

    delete_response = client.delete(f"/api/interviews/{interview_id}/checklist/{item_id}")
    assert delete_response.status_code == 200

    note_response = client.post(f"/api/interviews/{interview_id}/notes", json={"category": "general", "body": "Went well"})
    assert note_response.status_code == 200
    notes_response = client.get(f"/api/interviews/{interview_id}/notes")
    assert len(notes_response.json()) == 1


def test_api_reminders_list(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    create_response = client.post(
        "/api/interviews",
        json={
            "employer_id": str(employer.id),
            "interview_type": "phone",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "reminder_offsets": ["one_day"],
        },
    )
    interview_id = create_response.json()["id"]

    reminders_response = client.get(f"/api/interviews/{interview_id}/reminders")
    assert len(reminders_response.json()) == 1
    assert reminders_response.json()[0]["offset"] == "one_day"


def test_api_workflow_sync_requires_linked_workflow(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    create_response = client.post(
        "/api/interviews",
        json={
            "employer_id": str(employer.id),
            "interview_type": "phone",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        },
    )
    interview_id = create_response.json()["id"]

    response = client.post(f"/api/interviews/{interview_id}/workflow-sync", json={"action": "mark_interview"})

    assert response.status_code == 400
