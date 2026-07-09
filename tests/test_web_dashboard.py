"""
Integration tests for the web dashboard's *features* (pages, APIs,
filtering, documents, workflow, analytics) — deliberately decoupled from
authentication mechanics, the same way the rest of this suite already
decouples LLM calls (`FakeLLMProvider`) from whatever it's actually
testing. `get_current_user`/`get_current_api_user` are overridden here to
resolve to "the most-recently-seeded test user" (replicating this
project's pre-authentication-milestone convenience, now scoped to tests
only) rather than requiring every single test in this file to perform a
real register/login round trip just to reach the page under test.

The real authentication mechanics — registration, login, logout, session
cookies, protected-route redirects, and cross-user data isolation — are
covered end-to-end, with **no** dependency overrides, in
`tests/test_authentication.py`.

Runs against the same in-memory `db_session` fixture pattern
(`tests/conftest.py`) used by the rest of this suite, never the real
`data/jobs.db`. No real LLM calls: `get_llm_provider` (the one dependency
that would need a real Anthropic key) is overridden with a
`FakeLLMProvider`, the same pattern already used in `test_ai_matching.py` /
`test_document_generation.py` / `test_application_workflow.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_automation.ai.cache import AIResponseCache, MatchCache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot
from job_automation.database import models  # noqa: F401 - registers models on Base.metadata
from job_automation.database.base import Base
from job_automation.database.models import Employer, Job, JobMatch, User
from job_automation.documents.document_service import DocumentService
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.profile_service import ProfileService
from job_automation.web.app import (
    app,
    get_ai_response_cache,
    get_current_api_user,
    get_current_user,
    get_db_session,
    get_llm_provider,
    get_match_cache,
)
from job_automation.workflows.workflow_service import WorkflowService


class FakeLLMProvider(LLMProvider):
    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        return "I hold a valid DBS Check and relevant care experience."


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Overrides `conftest.py`'s `db_session` for this module only:
    `TestClient` runs each request's dependencies in a worker thread, and
    the default in-memory-SQLite fixture's connection is single-thread-only
    (fine for every other test file, which never crosses a thread
    boundary). `StaticPool` + `check_same_thread=False` shares the one
    in-memory connection safely across threads for these tests."""
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
    """SQLite's `CURRENT_TIMESTAMP` only has second resolution — a test that
    seeds a second `User` to act as "someone else" must set its `created_at`
    explicitly (later than the first) rather than relying on real-time
    ordering, or the two rows can tie and this resolves arbitrarily. See
    `test_documents_api_404s_for_other_users_document` for the pattern."""
    user = db_session.scalars(select(User).order_by(User.created_at.desc())).first()
    assert user is not None, "test must seed a User (e.g. via _seed_user_job_match) before this dependency fires"
    return user


@pytest.fixture
def client(db_session: Session, tmp_path: Path):
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider()
    app.dependency_overrides[get_current_user] = lambda: _most_recently_seeded_user(db_session)
    app.dependency_overrides[get_current_api_user] = lambda: _most_recently_seeded_user(db_session)
    # Point the AI caches at a per-test tmp_path, not the real project's
    # `data/cache/` — same reasoning as the in-memory `db_session` override
    # above: these tests must never touch real on-disk project state.
    app.dependency_overrides[get_ai_response_cache] = lambda: AIResponseCache(cache_dir=tmp_path / "ai_responses")
    app.dependency_overrides[get_match_cache] = lambda: MatchCache(cache_dir=tmp_path / "ai_matches")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_user_job_match(db_session: Session) -> tuple[User, Job, JobMatch]:
    user = User(email="candidate@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    employer = Employer(name="Example NHS Trust")
    job = Job(
        employer=employer,
        title="Healthcare Assistant",
        source_site="nhs_jobs",
        external_id="REF-1",
        url="https://example.com/job/1",
        location="London",
        salary_min=22000,
        salary_max=24000,
        band="Band 2",
    )
    db_session.add_all([user, employer, job])
    db_session.flush()
    match = JobMatch(
        job=job,
        user=user,
        match_score=82.5,
        analysis={
            "overall_score": 82.5,
            "confidence_score": 60.0,
            "category_scores": {"skills": 90.0, "experience": 80.0},
            "matched_keywords": ["care", "NHS"],
            "strengths": ["Strong care background"],
            "weaknesses": ["No NMC registration"],
            "missing_requirements": [],
            "recommended_actions": ["Highlight DBS check"],
            "used_llm": False,
        },
    )
    db_session.add(match)
    ProfileService(db_session).save(
        CandidateProfile(personal_information=PersonalInformation(full_name="Jane Doe")),
        user_id=user.id,
    )
    db_session.commit()
    return user, job, match


# --- Health check (Production Readiness milestone) ---------------------------


def test_health_check_reports_ok_with_working_database(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["environment"] == "development"


def test_health_check_requires_no_authentication(db_session: Session) -> None:
    """Unlike every page in PAGE_ROUTES below, `/health` must work without
    a session cookie at all — a Docker/Railway/Render health checker has no
    way to log in. Deliberately overrides only `get_db_session` (so this
    still runs against the isolated in-memory test database, never the
    real `data/jobs.db`) and leaves `get_current_user`/`get_current_api_user`
    un-overridden, so a real anonymous request is exercised."""
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        with TestClient(app) as unauthenticated_client:
            response = unauthenticated_client.get("/health")
            assert response.status_code in (200, 503)  # never a redirect to /login
    finally:
        app.dependency_overrides.clear()


# --- Database diagnostics (Database Unification milestone) -------------------


def test_database_diagnostics_reports_job_counts_and_database_url(
    client: TestClient, db_session: Session
) -> None:
    user = User(email="diagnostics@example.com", full_name="Diagnostics Tester", hashed_password="unused-in-these-tests")
    employer = Employer(name="Riverside NHS Trust")
    db_session.add_all([user, employer])
    db_session.flush()
    db_session.add_all(
        [
            Job(employer=employer, title="Staff Nurse", source_site="nhs_jobs", external_id="D1", url="https://x/d1"),
            Job(employer=employer, title="HCA", source_site="nhs_jobs", external_id="D2", url="https://x/d2"),
            Job(employer=employer, title="Support Worker", source_site="reed", external_id="D3", url="https://x/d3"),
        ]
    )
    db_session.commit()

    response = client.get("/diagnostics/database")
    assert response.status_code == 200
    body = response.json()

    assert body["database_url"]
    assert body["total_jobs"] == 3
    assert body["jobs_by_source"]["nhs_jobs"] == 2
    assert body["jobs_by_source"]["reed"] == 1
    assert len(body["latest_jobs"]) == 3
    assert body["scheduler_task_run_count"] == 0


def test_database_diagnostics_requires_authentication(db_session: Session) -> None:
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        with TestClient(app) as unauthenticated_client:
            response = unauthenticated_client.get("/diagnostics/database")
            assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_unhandled_exception_returns_json_500_instead_of_crashing(client: TestClient) -> None:
    """Production logging hardening: a genuinely unexpected exception (not
    an `HTTPException`, not `NotAuthenticatedError`) must still produce a
    clean JSON 500 response — and, per `app.py`'s `_log_unhandled_exception`
    handler, get logged through loguru rather than silently vanishing into
    the stdlib logger Starlette's default `ServerErrorMiddleware` would
    otherwise use.

    Starlette's `ServerErrorMiddleware` always re-raises the original
    exception *after* invoking the registered handler and sending its
    response — deliberately, so a real ASGI server can still log/observe it
    even though the client already got a clean response. `TestClient`'s
    default `raise_server_exceptions=True` surfaces that re-raise as a test
    failure, so this test needs its own client with that off, to actually
    inspect the response a real browser/API client would receive."""

    def _boom() -> User:
        raise RuntimeError("simulated unexpected failure")

    app.dependency_overrides[get_current_user] = _boom
    with TestClient(app, raise_server_exceptions=False) as lenient_client:
        response = lenient_client.get("/dashboard")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


# --- Page routes render -----------------------------------------------------

PAGE_ROUTES = [
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


@pytest.mark.parametrize("path", PAGE_ROUTES)
def test_every_dashboard_page_renders(client: TestClient, db_session: Session, path: str) -> None:
    _seed_user_job_match(db_session)
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Every page must be rendered by Jinja2 (via `templates.TemplateResponse`),
    # never served as raw template source — unrendered `{% %}`/`{{ }}` syntax
    # reaching the browser means a route bypassed the templating engine.
    assert "{%" not in response.text
    assert "{{" not in response.text


# NOTE: "unauthenticated access redirects to /login" replaces this
# milestone's old "no user in the database -> 500" behavior. That real
# behavior (a genuine, un-overridden `get_current_user`/session check) is
# covered in `test_authentication.py`, not here — every test in this file
# uses an authenticated-user dependency override by design (see this
# module's docstring).


def test_settings_page_does_not_leak_raw_jinja_syntax(client: TestClient, db_session: Session) -> None:
    """Regression test for a reported frontend bug: the Settings page was
    suspected of rendering raw template source (`{% block content %}`,
    `{{ profile.preferences... }}`) instead of the interpolated HTML —
    which would happen if the route ever returned `FileResponse`/a raw
    `HTMLResponse(open(...).read())` instead of
    `templates.TemplateResponse()`, or if `settings.html` were served
    through a static file mount instead of Jinja2. Not reproducible against
    the current `routes/settings.py` (confirmed it already uses
    `templates.TemplateResponse`), but this guards against the class of bug
    regardless of cause."""
    _seed_user_job_match(db_session)
    response = client.get("/settings")
    assert response.status_code == 200
    assert "{%" not in response.text
    assert "{{" not in response.text
    assert "profile.preferences" not in response.text


def test_job_detail_page_renders(client: TestClient, db_session: Session) -> None:
    _, job, _ = _seed_user_job_match(db_session)
    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 200
    assert "Healthcare Assistant" in response.text


def test_job_detail_page_404s_for_unknown_job(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# --- AI Career Assistant panel (added for the AI Career Assistant milestone) ----


def test_job_detail_page_shows_career_assistant_panel_when_match_exists(
    client: TestClient, db_session: Session
) -> None:
    _, job, _ = _seed_user_job_match(db_session)
    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 200
    assert "{{" not in response.text and "{%" not in response.text
    assert "AI Career Assistant" in response.text
    assert "Interview readiness" in response.text
    assert "Suggested CV improvements" in response.text
    # From _seed_user_job_match's analysis: no missing_requirements, one
    # weakness — the CV suggestion should surface that weakness.
    assert "No NMC registration" in response.text


def test_job_detail_page_hides_career_assistant_panel_without_a_match(
    client: TestClient, db_session: Session
) -> None:
    _seed_user_job_match(db_session)  # seeds the user `client` resolves as current_user
    unmatched_job = Job(
        employer=Employer(name="Unmatched Trust"),
        title="Unmatched Job",
        source_site="nhs_jobs",
        external_id="REF-UNMATCHED",
        url="https://example.com/job/unmatched",
    )
    db_session.add(unmatched_job)
    db_session.commit()
    response = client.get(f"/jobs/{unmatched_job.id}")
    assert response.status_code == 200
    assert "AI Career Assistant" not in response.text


def test_generate_document_career_insight_creates_draft(client: TestClient, db_session: Session) -> None:
    _, job, _ = _seed_user_job_match(db_session)
    response = client.post(
        f"/jobs/{job.id}/documents/generate",
        data={"document_type": "career_insight"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    review_response = client.get(response.headers["location"])
    assert review_response.status_code == 200
    assert "{{" not in review_response.text and "{%" not in review_response.text
    assert "Career Insight" in review_response.text


# --- Job filtering ------------------------------------------------------------


def test_jobs_page_filters_by_search_term(client: TestClient, db_session: Session) -> None:
    user, _, _ = _seed_user_job_match(db_session)
    employer2 = Employer(name="Other Trust")
    other_job = Job(
        employer=employer2, title="Support Worker", source_site="reed", external_id="REF-2", url="https://x/2"
    )
    db_session.add_all([employer2, other_job])
    db_session.commit()

    response = client.get("/jobs", params={"search": "Healthcare"})
    assert response.status_code == 200
    assert "Healthcare Assistant" in response.text
    assert "Support Worker" not in response.text


def test_jobs_page_filters_by_source(client: TestClient, db_session: Session) -> None:
    """Regression test for the Job Ingestion Service milestone's `source`
    filter — `_seed_user_job_match` creates a "nhs_jobs"-sourced job."""
    user, _, _ = _seed_user_job_match(db_session)
    employer2 = Employer(name="Other Trust")
    other_job = Job(
        employer=employer2, title="Support Worker", source_site="reed", external_id="REF-2", url="https://x/2"
    )
    db_session.add_all([employer2, other_job])
    db_session.commit()

    response = client.get("/jobs", params={"source": "reed"})
    assert response.status_code == 200
    assert "Support Worker" in response.text
    assert "Healthcare Assistant" not in response.text


def test_jobs_page_with_all_filters_blank_returns_every_job(client: TestClient, db_session: Session) -> None:
    """Regression test: `<input type="number">` fields (min_salary/
    max_salary) always submit their name even when left blank (unlike a
    checkbox, which sends nothing when unchecked) — clicking "Filter" with
    every field empty sent `min_salary=&max_salary=`, which FastAPI
    rejected with a `422` for a `float | None` query parameter, and HTMX's
    `hx-select` swap then silently emptied the results panel — making a
    fully-blank filter submission look like "no jobs found" even though 5
    jobs existed. See `routes/jobs.py`'s `_parse_optional_float`."""
    _seed_user_job_match(db_session)  # 1 job ("Healthcare Assistant")
    employer = Employer(name="Demo NHS Trust")
    db_session.add(employer)
    db_session.flush()
    for i in range(4):  # 4 more -> 5 total, matching the real demo dataset's job count
        db_session.add(
            Job(
                employer=employer,
                title=f"Demo Job {i}",
                source_site="nhs_jobs",
                external_id=f"DEMO-{i}",
                url=f"https://example.com/job/demo-{i}",
            )
        )
    db_session.commit()

    # The exact querystring a browser sends when "Filter" is clicked with
    # every field at its default/blank state: text/number inputs and the
    # "Status" select's "Any" option submit as empty strings; unchecked
    # checkboxes (visa_sponsorship, remote, saved, favourite, archived,
    # closing_soon, expired) are simply absent.
    response = client.get(
        "/jobs",
        params={
            "search": "",
            "keywords": "",
            "location": "",
            "employer_name": "",
            "band": "",
            "min_salary": "",
            "max_salary": "",
            "employment_type": "",
            "pipeline_stage": "",
            "sort_by": "posted_date",
            "sort_descending": "true",
        },
    )

    assert response.status_code == 200
    assert "5 job(s)" in response.text
    assert "{{" not in response.text and "{%" not in response.text


def test_jobs_api_filters_by_minimum_salary(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/api/jobs", params={"min_salary": 100000})
    assert response.status_code == 200
    assert response.json() == []


def test_jobs_api_returns_matching_job(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/api/jobs", params={"min_salary": 20000})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Healthcare Assistant"


# --- Matches / analytics ------------------------------------------------------


def test_matches_api_returns_score_and_analysis(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/api/jobs/matches/all")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["match_score"] == 82.5
    assert data[0]["analysis"]["strengths"] == ["Strong care background"]


def test_dashboard_summary_api(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/api/dashboard/summary")
    assert response.status_code == 200


def test_dashboard_analytics_api(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/api/dashboard/analytics")
    assert response.status_code == 200
    body = response.json()
    assert "score_distribution" in body or isinstance(body, dict)


def test_job_ingestion_summary_api(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/api/dashboard/job-ingestion")
    assert response.status_code == 200
    body = response.json()
    assert body["jobs_discovered"] == 1
    assert body["jobs_by_source"][0]["name"] == "nhs_jobs"
    assert body["latest_jobs"][0]["title"] == "Healthcare Assistant"


def test_job_market_analytics_api(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/api/dashboard/job-market-analytics")
    assert response.status_code == 200
    body = response.json()
    assert body["jobs_by_source"][0]["name"] == "nhs_jobs"
    assert "jobs_by_salary_bucket" in body
    assert "jobs_over_time" in body


def test_ai_status_reflects_configuration_and_match_coverage(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_automation.analytics.analytics_service import AnalyticsService
    from job_automation.config.settings import settings

    user, _, _ = _seed_user_job_match(db_session)  # one JobMatch, analysis["used_llm"] == False

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    not_configured = AnalyticsService(db_session).ai_status(user.id)
    assert not_configured.configured is False
    assert not_configured.matches_total == 1
    assert not_configured.matches_with_ai == 0
    assert not_configured.ai_coverage_percent == 0.0

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-configured")
    monkeypatch.setattr(settings, "anthropic_model", "claude-sonnet-4-6")
    configured = AnalyticsService(db_session).ai_status(user.id)
    assert configured.configured is True
    assert configured.model == "claude-sonnet-4-6"


def test_dashboard_page_shows_ai_status(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "AI status" in response.text


# --- Candidate profile editing ------------------------------------------------


def test_candidate_profile_page_shows_saved_profile(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.get("/candidate")
    assert response.status_code == 200
    assert "Jane Doe" in response.text


def test_update_personal_information_persists_change(client: TestClient, db_session: Session) -> None:
    user, _, _ = _seed_user_job_match(db_session)
    response = client.post(
        "/candidate/personal-information",
        data={"full_name": "Jane A. Doe", "email": "jane@example.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    updated = ProfileService(db_session).get(user.id)
    assert updated.personal_information.full_name == "Jane A. Doe"
    assert updated.personal_information.email == "jane@example.com"


def test_update_preferences_via_settings(client: TestClient, db_session: Session) -> None:
    user, _, _ = _seed_user_job_match(db_session)
    response = client.post(
        "/settings/preferences",
        data={"preferred_locations": "London, Manchester", "preferred_salary_min": "25000"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    updated = ProfileService(db_session).get(user.id)
    assert updated.preferences.preferred_locations == ("London", "Manchester")
    assert updated.preferences.preferred_salary_min == 25000


# --- Documents: generate, approve, reject, export -----------------------------


def _generate_document(db_session: Session, user: User, job: Job):
    service = DocumentService(db_session, FakeLLMProvider())
    document = service.generate_supporting_statement(
        CandidateProfile(personal_information=PersonalInformation(full_name="Jane Doe")),
        JobSnapshot.from_job(job),
        user_id=user.id,
        job_id=job.id,
    )
    db_session.commit()
    return document


def test_documents_list_and_review_pages_render(client: TestClient, db_session: Session) -> None:
    user, job, _ = _seed_user_job_match(db_session)
    document = _generate_document(db_session, user, job)

    list_response = client.get("/documents")
    assert list_response.status_code == 200

    review_response = client.get(f"/documents/{document.id}")
    assert review_response.status_code == 200


def test_approve_document_via_api_never_requires_llm_provider(client: TestClient, db_session: Session) -> None:
    """Regression test: approve/reject/export must not depend on a
    configured LLM provider (`DocumentService`'s constructor requires one,
    but these actions never call it) — see `api/documents_api.py`'s
    `_NeverCalledLLMProvider`. This test does NOT override the real
    `get_llm_provider` dependency with a fake, to prove approval works even
    when no LLM is configured at all."""
    user, job, _ = _seed_user_job_match(db_session)
    document = _generate_document(db_session, user, job)

    response = client.post(f"/api/documents/{document.id}/approve")
    assert response.status_code == 200
    assert "Approved" in response.text


def test_reject_document_via_api(client: TestClient, db_session: Session) -> None:
    user, job, _ = _seed_user_job_match(db_session)
    document = _generate_document(db_session, user, job)

    response = client.post(f"/api/documents/{document.id}/reject")
    assert response.status_code == 200
    assert "Rejected" in response.text


def test_export_document_via_api(client: TestClient, db_session: Session) -> None:
    user, job, _ = _seed_user_job_match(db_session)
    document = _generate_document(db_session, user, job)

    response = client.get(f"/api/documents/{document.id}/export", params={"format": "txt"})
    assert response.status_code == 200


def test_documents_api_404s_for_other_users_document(client: TestClient, db_session: Session) -> None:
    user, job, _ = _seed_user_job_match(db_session)
    document = _generate_document(db_session, user, job)

    # Seeded *after* the document's owner, so the `client` fixture's
    # "most-recently-seeded user" override now resolves to this user for
    # the rest of the test — exercising the exact same ownership check
    # `get_current_api_user` + `_owned_document_or_404` enforce for two
    # real, independently-authenticated users (see
    # `test_authentication.py` for the full real-session version of this
    # same guarantee). SQLite's `CURRENT_TIMESTAMP` only has second
    # resolution, so two rows created in the same test can tie on
    # `created_at` — set an explicit, clearly-later timestamp rather than
    # relying on real-time creation order (same fix as
    # `test_workflow_repository_list_for_user_orders_most_recent_first` in
    # `test_application_workflow.py`).
    other_user = User(email="someone-else@example.com", full_name="Someone Else", hashed_password="unused-in-these-tests")
    db_session.add(other_user)
    db_session.flush()
    other_user.created_at = user.created_at + timedelta(seconds=1)
    db_session.commit()

    response = client.post(f"/api/documents/{document.id}/approve")
    assert response.status_code == 404


def test_regenerate_document_uses_fake_llm_and_redirects(client: TestClient, db_session: Session) -> None:
    user, job, _ = _seed_user_job_match(db_session)
    document = _generate_document(db_session, user, job)

    response = client.post(f"/documents/{document.id}/regenerate", follow_redirects=False)
    assert response.status_code == 303


# --- Anthropic AI Integration: manual generation/rematch triggers --------------
#
# All of these run against `client`'s `FakeLLMProvider` override — never a
# real Anthropic call — the same guarantee every other test in this file
# already relies on. `get_llm_provider`'s *real* (`AnthropicProvider`-
# constructing, key-validating) behavior is covered separately in
# `tests/test_anthropic_provider.py`, entirely with a mocked `anthropic` SDK
# client.


class _JSONFakeLLMProvider(LLMProvider):
    """`FakeLLMProvider` above returns free prose — correct for document
    generation, but `MatchingEngine`/`parse_response()` requires structured
    JSON, so a rematch test needs a provider that actually produces it."""

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        from job_automation.ai.matching_models import MATCH_CATEGORIES

        return json.dumps(
            {
                "category_scores": {category: 80.0 for category in MATCH_CATEGORIES},
                "strengths": ["Strong clinical background"],
                "weaknesses": [],
                "missing_requirements": [],
                "recommended_actions": [],
            }
        )


def test_rematch_with_ai_flips_used_llm_and_redirects_to_job(client: TestClient, db_session: Session) -> None:
    user, job, match = _seed_user_job_match(db_session)
    assert match.analysis["used_llm"] is False

    app.dependency_overrides[get_llm_provider] = lambda: _JSONFakeLLMProvider()
    response = client.post(f"/matches/{job.id}/rematch", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{job.id}"
    db_session.refresh(match)
    assert match.analysis["used_llm"] is True


def test_rematch_with_ai_404s_for_unknown_job(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.post(f"/matches/{uuid.uuid4()}/rematch")
    assert response.status_code == 404


def test_generate_document_supporting_statement_creates_draft_and_redirects(
    client: TestClient, db_session: Session
) -> None:
    _, job, _ = _seed_user_job_match(db_session)
    response = client.post(
        f"/jobs/{job.id}/documents/generate",
        data={"document_type": "supporting_statement"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/documents/")


def test_generate_document_skills_gap_analysis_creates_draft(client: TestClient, db_session: Session) -> None:
    _, job, _ = _seed_user_job_match(db_session)
    response = client.post(
        f"/jobs/{job.id}/documents/generate",
        data={"document_type": "skills_gap_analysis"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    review_response = client.get(response.headers["location"])
    assert review_response.status_code == 200
    assert "{{" not in review_response.text and "{%" not in review_response.text


def test_generate_document_application_answer_requires_a_question(client: TestClient, db_session: Session) -> None:
    _, job, _ = _seed_user_job_match(db_session)
    response = client.post(
        f"/jobs/{job.id}/documents/generate",
        data={"document_type": "application_answer"},
    )
    assert response.status_code == 400


def test_generate_document_rejects_unknown_document_type(client: TestClient, db_session: Session) -> None:
    _, job, _ = _seed_user_job_match(db_session)
    response = client.post(f"/jobs/{job.id}/documents/generate", data={"document_type": "not_a_real_type"})
    assert response.status_code == 400


def test_generate_document_404s_for_unknown_job(client: TestClient, db_session: Session) -> None:
    _seed_user_job_match(db_session)
    response = client.post(
        f"/jobs/{uuid.uuid4()}/documents/generate", data={"document_type": "supporting_statement"}
    )
    assert response.status_code == 404


def test_generate_interview_prep_requires_a_linked_job(client: TestClient, db_session: Session) -> None:
    from job_automation.interviews.interview_models import InterviewType
    from job_automation.interviews.interview_service import InterviewService
    from job_automation.utils.helpers import utc_now

    user, _, _ = _seed_user_job_match(db_session)
    employer = Employer(name="Another Trust")
    db_session.add(employer)
    db_session.flush()
    interview = InterviewService(db_session).schedule(
        user_id=user.id,
        employer_id=employer.id,
        interview_type=InterviewType.PHONE,
        scheduled_at=utc_now() + timedelta(days=2),
    )
    db_session.commit()

    response = client.post(f"/interviews/{interview.id}/generate-prep")
    assert response.status_code == 400


def test_generate_interview_prep_creates_draft_and_redirects(client: TestClient, db_session: Session) -> None:
    from job_automation.interviews.interview_models import InterviewType
    from job_automation.interviews.interview_service import InterviewService
    from job_automation.utils.helpers import utc_now

    user, job, _ = _seed_user_job_match(db_session)
    interview = InterviewService(db_session).schedule(
        user_id=user.id,
        employer_id=job.employer_id,
        interview_type=InterviewType.PHONE,
        job_id=job.id,
        scheduled_at=utc_now() + timedelta(days=2),
    )
    db_session.commit()

    response = client.post(f"/interviews/{interview.id}/generate-prep", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/documents/")


# --- Workflow transitions ------------------------------------------------------


def test_workflow_list_and_detail_pages_render(client: TestClient, db_session: Session) -> None:
    user, job, match = _seed_user_job_match(db_session)
    workflow = WorkflowService(db_session).start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
    db_session.commit()

    list_response = client.get("/workflow")
    assert list_response.status_code == 200

    detail_response = client.get(f"/workflow/{workflow.id}")
    assert detail_response.status_code == 200


def test_workflow_transition_api_rejects_invalid_transition(client: TestClient, db_session: Session) -> None:
    user, job, match = _seed_user_job_match(db_session)
    workflow = WorkflowService(db_session).start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
    db_session.commit()

    response = client.post(f"/api/workflow/{workflow.id}/transition", data={"action": "mark_applied"})
    assert response.status_code == 400


def test_workflow_transition_api_rejects_unknown_action(client: TestClient, db_session: Session) -> None:
    user, job, match = _seed_user_job_match(db_session)
    workflow = WorkflowService(db_session).start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
    db_session.commit()

    response = client.post(f"/api/workflow/{workflow.id}/transition", data={"action": "not_a_real_action"})
    assert response.status_code == 400


def test_workflow_transition_api_performs_valid_transition(client: TestClient, db_session: Session) -> None:
    user, job, match = _seed_user_job_match(db_session)
    workflow = WorkflowService(db_session).start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
    db_session.commit()

    response = client.post(
        f"/api/workflow/{workflow.id}/transition", data={"action": "close", "note": "Withdrawn"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "closed"


def test_workflow_api_list_and_detail(client: TestClient, db_session: Session) -> None:
    user, job, match = _seed_user_job_match(db_session)
    workflow = WorkflowService(db_session).start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
    db_session.commit()

    list_response = client.get("/api/workflow")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    detail_response = client.get(f"/api/workflow/{workflow.id}")
    assert detail_response.status_code == 200
    assert "status_history" in detail_response.json()


def test_applications_page_and_api_only_include_ready_to_apply_and_beyond(
    client: TestClient, db_session: Session
) -> None:
    user, job, match = _seed_user_job_match(db_session)
    service = WorkflowService(db_session)
    workflow = service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
    db_session.commit()

    page_response = client.get("/applications")
    assert page_response.status_code == 200

    api_response = client.get("/api/workflow/applications")
    assert api_response.status_code == 200
    assert api_response.json() == []  # NEW_MATCH isn't an "application" yet

    service.close(workflow, reason="test")
    db_session.commit()
    # CLOSED isn't reachable from NEW_MATCH in one call above only via close();
    # confirm it now counts as an application-stage workflow.
    api_response_after = client.get("/api/workflow/applications")
    assert api_response_after.status_code == 200
    assert len(api_response_after.json()) == 1
