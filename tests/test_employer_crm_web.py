"""
Integration tests for the Employer & Application CRM milestone's HTML
routes (`routes/employers.py`) and JSON API (`api/employers_api.py`) —
exercised through a real `TestClient`, the same decoupled-from-auth
pattern `test_web_dashboard.py`/`test_job_organization_web.py` already
establish (`get_current_user`/`get_current_api_user` overridden to "the
most-recently-seeded test user").
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_automation.database import models  # noqa: F401 - registers models on Base.metadata
from job_automation.database.base import Base
from job_automation.database.models import Employer, User
from job_automation.database.models.employer_contact import EmployerContact
from job_automation.database.models.employer_department import EmployerDepartment
from job_automation.database.models.employer_profile import EmployerProfile
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


def _seed_user_and_employer(db_session: Session) -> tuple[User, Employer]:
    user = User(email="candidate@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    employer = Employer(name="Riverside NHS Foundation Trust", employer_type="nhs_trust")
    db_session.add_all([user, employer])
    db_session.commit()
    return user, employer


# --- HTML: list/search page ---------------------------------------------------


def test_employers_list_page_renders(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.get("/employers")

    assert response.status_code == 200
    assert employer.name in response.text


def test_employers_list_page_search_filter(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    other = Employer(name="Meadow Care Group", employer_type="care_home")
    db_session.add(other)
    db_session.commit()

    response = client.get("/employers", params={"search": "Riverside"})

    assert employer.name in response.text
    assert other.name not in response.text


def test_employers_list_page_favourite_filter(client: TestClient, db_session: Session) -> None:
    user, employer = _seed_user_and_employer(db_session)
    other = Employer(name="Meadow Care Group")
    db_session.add(other)
    db_session.commit()

    client.post(f"/employers/{employer.id}/flag", data={"action": "favourite"}, follow_redirects=False)

    response = client.get("/employers", params={"favourite": "true"})

    assert employer.name in response.text
    assert other.name not in response.text


# --- HTML: profile page + flag/visa-notes -------------------------------------


def test_employer_detail_page_renders(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.get(f"/employers/{employer.id}")

    assert response.status_code == 200
    assert employer.name in response.text


def test_employer_detail_page_404s_for_unknown_employer(client: TestClient, db_session: Session) -> None:
    _seed_user_and_employer(db_session)

    response = client.get("/employers/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_flag_route_favourites_and_redirects(client: TestClient, db_session: Session) -> None:
    user, employer = _seed_user_and_employer(db_session)

    response = client.post(
        f"/employers/{employer.id}/flag", data={"action": "favourite"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/employers/{employer.id}"
    profile = db_session.scalars(select(EmployerProfile).where(EmployerProfile.user_id == user.id)).first()
    assert profile is not None and profile.is_favourite is True


def test_flag_route_rejects_unknown_action(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.post(f"/employers/{employer.id}/flag", data={"action": "bogus"}, follow_redirects=False)

    assert response.status_code == 400


def test_visa_notes_route_persists(client: TestClient, db_session: Session) -> None:
    user, employer = _seed_user_and_employer(db_session)

    response = client.post(
        f"/employers/{employer.id}/visa-notes", data={"notes": "Sponsors Tier 2 visas"}, follow_redirects=False
    )

    assert response.status_code == 303
    profile = db_session.scalars(select(EmployerProfile).where(EmployerProfile.user_id == user.id)).first()
    assert profile.visa_sponsorship_notes == "Sponsors Tier 2 visas"


# --- HTML: departments/contacts/activity --------------------------------------


def test_department_add_and_remove_routes(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    client.post(
        f"/employers/{employer.id}/departments",
        data={"name": "Emergency Department", "location": "London"},
        follow_redirects=False,
    )
    department = db_session.scalars(select(EmployerDepartment)).first()
    assert department is not None and department.name == "Emergency Department"

    response = client.post(
        f"/employers/{employer.id}/departments/{department.id}/delete", follow_redirects=False
    )
    assert response.status_code == 303
    assert db_session.scalars(select(EmployerDepartment)).first() is None


def test_contact_add_and_remove_routes(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    client.post(
        f"/employers/{employer.id}/contacts",
        data={"name": "Jane Recruiter", "role": "Recruitment Lead", "email": "jane@example.com"},
        follow_redirects=False,
    )
    contact = db_session.scalars(select(EmployerContact)).first()
    assert contact is not None and contact.name == "Jane Recruiter"

    response = client.post(f"/employers/{employer.id}/contacts/{contact.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert db_session.scalars(select(EmployerContact)).first() is None


def test_note_and_communication_routes_appear_in_timeline(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    client.post(f"/employers/{employer.id}/notes", data={"body": "Applied via portal"}, follow_redirects=False)
    client.post(
        f"/employers/{employer.id}/communications",
        data={"channel": "phone", "body": "Confirmed interview slot"},
        follow_redirects=False,
    )

    response = client.get(f"/employers/{employer.id}")
    assert "Applied via portal" in response.text
    assert "Confirmed interview slot" in response.text


def test_communication_route_rejects_unknown_channel(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.post(
        f"/employers/{employer.id}/communications",
        data={"channel": "carrier_pigeon", "body": "..."},
        follow_redirects=False,
    )

    assert response.status_code == 400


# --- Dashboard CRM section ----------------------------------------------------


def test_dashboard_page_renders_employer_crm_section(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)
    client.post(f"/employers/{employer.id}/flag", data={"action": "favourite"}, follow_redirects=False)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Employer CRM" in response.text


# --- JSON API ------------------------------------------------------------------


def test_api_list_and_get_employer(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    list_response = client.get("/api/employers")
    assert list_response.status_code == 200
    assert any(row["name"] == employer.name for row in list_response.json())

    get_response = client.get(f"/api/employers/{employer.id}")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["name"] == employer.name
    assert body["is_favourite"] is False
    assert "outcome" in body


def test_api_flag_and_visa_notes(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    flag_response = client.post(f"/api/employers/{employer.id}/flag", json={"action": "favourite"})
    assert flag_response.status_code == 200
    assert flag_response.json()["is_favourite"] is True

    notes_response = client.post(
        f"/api/employers/{employer.id}/visa-notes", json={"notes": "Sponsors visas"}
    )
    assert notes_response.status_code == 200
    assert notes_response.json()["visa_sponsorship_notes"] == "Sponsors visas"


def test_api_department_crud(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    create_response = client.post(
        f"/api/employers/{employer.id}/departments", json={"name": "ICU", "location": "Main site"}
    )
    assert create_response.status_code == 200
    department_id = create_response.json()["id"]

    list_response = client.get(f"/api/employers/{employer.id}/departments")
    assert len(list_response.json()) == 1

    delete_response = client.delete(f"/api/employers/{employer.id}/departments/{department_id}")
    assert delete_response.status_code == 200
    assert client.get(f"/api/employers/{employer.id}/departments").json() == []


def test_api_contact_crud_with_ownership(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    create_response = client.post(f"/api/employers/{employer.id}/contacts", json={"name": "Jane Recruiter"})
    assert create_response.status_code == 200
    contact_id = create_response.json()["id"]

    list_response = client.get(f"/api/employers/{employer.id}/contacts")
    assert len(list_response.json()) == 1

    delete_response = client.delete(f"/api/employers/{employer.id}/contacts/{contact_id}")
    assert delete_response.status_code == 200

    missing_response = client.delete(f"/api/employers/{employer.id}/contacts/{contact_id}")
    assert missing_response.status_code == 404


def test_api_activity_notes_and_communications(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    client.post(f"/api/employers/{employer.id}/notes", json={"body": "Note body"})
    client.post(
        f"/api/employers/{employer.id}/communications",
        json={"channel": "email", "body": "Sent CV"},
    )

    all_activity = client.get(f"/api/employers/{employer.id}/activity").json()
    assert len(all_activity) == 2

    notes_only = client.get(f"/api/employers/{employer.id}/activity", params={"entry_type": "note"}).json()
    assert len(notes_only) == 1
    assert notes_only[0]["body"] == "Note body"

    entry_id = notes_only[0]["id"]
    delete_response = client.delete(f"/api/employers/{employer.id}/activity/{entry_id}")
    assert delete_response.status_code == 200


def test_api_employer_outcome_reflects_analytics(client: TestClient, db_session: Session) -> None:
    _, employer = _seed_user_and_employer(db_session)

    response = client.get(f"/api/employers/{employer.id}")

    outcome = response.json()["outcome"]
    assert outcome["applications_sent"] == 0
    assert outcome["employer_name"] == employer.name
