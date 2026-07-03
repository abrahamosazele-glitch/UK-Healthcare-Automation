"""
End-to-end tests for authentication itself: registration, login, logout,
protected-route redirects, and cross-user data isolation — deliberately
using **no** `get_current_user`/`get_current_api_user` dependency
overrides, unlike `test_web_dashboard.py`. Every request here goes through
the real `SessionMiddleware` + real signed cookies + real `AuthService` +
real bcrypt hashing, exactly as a real browser would. This is the one file
in the suite that actually proves login/logout work, not just that the
rest of the app behaves correctly once already authenticated.
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
from job_automation.database.models import User
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.profile_service import ProfileService
from job_automation.web.app import app, get_db_session


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Same `StaticPool` / `check_same_thread=False` in-memory database as
    `test_web_dashboard.py` — required because `TestClient` runs request
    dependencies in a worker thread. See that file's fixture docstring."""
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


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Deliberately overrides only `get_db_session` — `get_current_user`/
    `get_current_api_user` are left as the real, session-cookie-based
    implementations, since proving those work for real is this file's
    entire purpose."""
    app.dependency_overrides[get_db_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _register(client: TestClient, *, email: str, password: str, full_name: str = "Test User", **extra):
    data = {"full_name": full_name, "email": email, "password": password, "confirm_password": password}
    data.update(extra)
    return client.post("/register", data=data, follow_redirects=False)


def _login(client: TestClient, *, email: str, password: str, next: str = ""):
    return client.post("/login", data={"email": email, "password": password, "next": next}, follow_redirects=False)


# --- Registration --------------------------------------------------------------


def test_register_creates_user_and_logs_in_immediately(client: TestClient, db_session: Session) -> None:
    response = _register(client, email="alice@example.com", password="AlicePassword123", full_name="Alice Alpha")

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"

    user = db_session.scalars(select(User).where(User.email == "alice@example.com")).first()
    assert user is not None
    assert user.full_name == "Alice Alpha"
    # A real bcrypt hash, never the plaintext password.
    assert user.hashed_password != "AlicePassword123"
    assert user.hashed_password.startswith("$2b$")

    # The registration response's session cookie is immediately usable —
    # no separate login step required after registering.
    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 200
    assert "Alice Alpha" in dashboard_response.text


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    _register(client, email="dup@example.com", password="FirstPassword123")

    response = _register(client, email="dup@example.com", password="SecondPassword123")
    assert response.status_code == 200  # re-renders the form, not a redirect
    assert "already registered" in response.text.lower()


def test_register_rejects_short_password(client: TestClient) -> None:
    response = _register(client, email="short@example.com", password="short1")
    assert response.status_code == 200
    assert "at least 8 characters" in response.text.lower()


def test_register_rejects_mismatched_passwords(client: TestClient) -> None:
    response = _register(
        client, email="mismatch@example.com", password="CorrectPassword1", confirm_password="DifferentPassword1"
    )
    assert response.status_code == 200
    assert "do not match" in response.text.lower()


def test_register_page_renders(client: TestClient) -> None:
    response = client.get("/register")
    assert response.status_code == 200
    assert "{%" not in response.text
    assert "{{" not in response.text


# --- Login / logout -------------------------------------------------------------


def test_login_with_correct_credentials_succeeds(client: TestClient) -> None:
    _register(client, email="bob@example.com", password="BobPassword123", full_name="Bob Beta")
    client.cookies.clear()  # prove login itself works, not just registration's auto-login

    response = _login(client, email="bob@example.com", password="BobPassword123")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"

    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 200
    assert "Bob Beta" in dashboard_response.text


def test_login_with_wrong_password_fails(client: TestClient) -> None:
    _register(client, email="carol@example.com", password="CarolPassword123")
    client.cookies.clear()

    response = _login(client, email="carol@example.com", password="WrongPassword")
    assert response.status_code == 200  # re-renders the login form
    assert "invalid email or password" in response.text.lower()

    # No session was established — still redirected away from the dashboard.
    dashboard_response = client.get("/dashboard", follow_redirects=False)
    assert dashboard_response.status_code == 303
    assert dashboard_response.headers["location"].startswith("/login")


def test_login_with_unknown_email_fails_with_same_message_as_wrong_password(client: TestClient) -> None:
    response = _login(client, email="nobody@example.com", password="Whatever123")
    assert response.status_code == 200
    assert "invalid email or password" in response.text.lower()


def test_login_redirects_to_next_after_success(client: TestClient) -> None:
    _register(client, email="dana@example.com", password="DanaPassword123")
    client.cookies.clear()

    response = _login(client, email="dana@example.com", password="DanaPassword123", next="/settings")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"


def test_login_rejects_open_redirect_via_next(client: TestClient) -> None:
    _register(client, email="erin@example.com", password="ErinPassword123")
    client.cookies.clear()

    response = _login(client, email="erin@example.com", password="ErinPassword123", next="//evil.example.com/steal")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_logout_clears_session_and_protects_dashboard_again(client: TestClient) -> None:
    _register(client, email="frank@example.com", password="FrankPassword123")
    assert client.get("/dashboard").status_code == 200

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"

    after_logout = client.get("/dashboard", follow_redirects=False)
    assert after_logout.status_code == 303
    assert after_logout.headers["location"].startswith("/login")


# --- Protected routes ------------------------------------------------------------


PROTECTED_PAGES = [
    "/dashboard",
    "/jobs",
    "/matches",
    "/documents",
    "/workflow",
    "/applications",
    "/candidate",
    "/analytics",
    "/settings",
]


@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_every_protected_page_redirects_to_login_when_unauthenticated(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path}"


PROTECTED_API_ROUTES = [
    "/api/dashboard/summary",
    "/api/dashboard/analytics",
    "/api/dashboard/candidate-profile",
    "/api/jobs",
    "/api/jobs/matches/all",
    "/api/documents",
    "/api/workflow",
    "/api/workflow/applications",
]


@pytest.mark.parametrize("path", PROTECTED_API_ROUTES)
def test_every_protected_api_route_returns_401_when_unauthenticated(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_root_redirects_to_login_when_unauthenticated(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert response.headers["location"] == "/login"


def test_root_redirects_to_dashboard_when_authenticated(client: TestClient) -> None:
    _register(client, email="grace@example.com", password="GracePassword123")
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert response.headers["location"] == "/dashboard"


# --- Cross-user data isolation ---------------------------------------------------


def test_two_users_see_only_their_own_candidate_profile(client: TestClient, db_session: Session) -> None:
    _register(client, email="alice2@example.com", password="AlicePassword123", full_name="Alice Alpha")
    alice = db_session.scalars(select(User).where(User.email == "alice2@example.com")).first()
    ProfileService(db_session).save(
        CandidateProfile(personal_information=PersonalInformation(full_name="Alice Alpha")),
        user_id=alice.id,
    )
    db_session.commit()

    alice_profile = client.get("/api/dashboard/candidate-profile").json()
    assert alice_profile["profile"]["personal_information"]["full_name"] == "Alice Alpha"

    client.cookies.clear()
    _register(client, email="heidi@example.com", password="HeidiPassword123", full_name="Heidi Hidden")

    # A brand-new user has no candidate profile yet — Alice's is never leaked.
    heidi_profile = client.get("/api/dashboard/candidate-profile").json()
    assert heidi_profile["profile"] is None


def test_two_users_have_independent_and_isolated_workflows_and_documents(client: TestClient) -> None:
    _register(client, email="ivan@example.com", password="IvanPassword123")
    assert client.get("/api/workflow").json() == []
    assert client.get("/api/documents").json() == []

    client.cookies.clear()
    _register(client, email="judy@example.com", password="JudyPassword123")
    assert client.get("/api/workflow").json() == []
    assert client.get("/api/documents").json() == []


def test_session_cookie_is_httponly(client: TestClient) -> None:
    response = _register(client, email="mallory@example.com", password="MalloryPassword123")
    set_cookie = response.headers.get("set-cookie", "")
    assert "session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
