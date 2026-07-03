"""
Tests for the background scheduler subsystem: successful runs, failures,
retry-then-succeed, per-task locking/skipping, status history, each of the
5 task functions individually, and the dashboard page/API. No real LLM
calls (`SchedulerFakeLLMProvider` throughout, never `AnthropicProvider`),
no live scraping (the fixture importer reads a local JSON file), and the
periodic APScheduler trigger is never started here — every test calls
`SchedulerService.run_task()` directly or via `TestClient`, matching how
the milestone's own "Run now" button works.
"""

from __future__ import annotations

import contextlib
import json
from datetime import timedelta
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_automation.auth.auth_service import AuthService
from job_automation.core.retry_manager import RetryManager
from job_automation.database import models  # noqa: F401 - registers models on Base.metadata
from job_automation.database.base import Base
from job_automation.database.models import Employer, GeneratedDocumentRecord, Job, JobMatch, User
from job_automation.database.models.scheduler_task_run_record import SchedulerTaskRunRecord
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.profile_service import ProfileService
from job_automation.scheduler.scheduler_models import TaskDefinition, TaskStatus, utc_now
from job_automation.scheduler.scheduler_repository import SchedulerRepository
from job_automation.scheduler.scheduler_service import SchedulerService
from job_automation.scheduler.task_registry import TASK_REGISTRY
from job_automation.scheduler.tasks import (
    cleanup_old_logs,
    generate_draft_documents,
    import_fixture_jobs,
    run_ai_matching,
    update_workflow_statuses,
)
from job_automation.web.app import app, get_current_api_user, get_current_user, get_db_session, get_scheduler_service

FAST_RETRY = lambda max_attempts: RetryManager(  # noqa: E731
    max_retries=max_attempts, base_delay_seconds=0.01, max_delay_seconds=0.02
)


# --- Shared fixtures -------------------------------------------------------------


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _session_factory(session: Session):
    return lambda: contextlib.nullcontext(session)


def _seed_user_with_profile(db_session: Session, *, skills: tuple[str, ...] = ("care", "communication")) -> User:
    user = AuthService(db_session).register(email="scheduler@example.com", password="SchedulerPass123", full_name="Scheduler User")
    db_session.commit()
    ProfileService(db_session).save(
        CandidateProfile(personal_information=PersonalInformation(full_name="Scheduler User"), skills=skills),
        user_id=user.id,
    )
    db_session.commit()
    return user


def _seed_job(db_session: Session, *, external_id: str = "SCHED-1", title: str = "Healthcare Assistant") -> Job:
    employer = Employer(name="Scheduler Test Trust")
    job = Job(
        employer=employer, title=title, source_site="test", external_id=external_id,
        url=f"https://example.com/{external_id}", location="London",
    )
    db_session.add_all([employer, job])
    db_session.flush()
    return job


# --- SchedulerService: locking, retries, status history -----------------------------


def test_run_task_success_records_full_history(db_session: Session) -> None:
    calls = {"n": 0}

    def succeeds(session: Session) -> dict:
        calls["n"] += 1
        return {"processed": 3}

    registry = {"t": TaskDefinition(name="t", description="d", func=succeeds, interval_seconds=60, max_attempts=3)}
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry=registry, retry_manager_factory=FAST_RETRY)

    run = service.run_task("t", triggered_by="manual")

    assert run.status == TaskStatus.SUCCESS
    assert run.attempt == 1
    assert run.result_summary == {"processed": 3}
    assert run.error_message is None
    assert run.finished_at is not None
    assert run.finished_at >= run.started_at
    assert calls["n"] == 1


def test_run_task_retries_then_succeeds(db_session: Session) -> None:
    calls = {"n": 0}

    def flaky(session: Session) -> dict:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"transient failure #{calls['n']}")
        return {"ok": True}

    registry = {"t": TaskDefinition(name="t", description="d", func=flaky, interval_seconds=60, max_attempts=3)}
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry=registry, retry_manager_factory=FAST_RETRY)

    run = service.run_task("t")

    assert run.status == TaskStatus.SUCCESS
    assert run.attempt == 3
    assert run.result_summary == {"ok": True}
    assert calls["n"] == 3


def test_run_task_records_failure_after_exhausting_retries(db_session: Session) -> None:
    def always_fails(session: Session) -> dict:
        raise ValueError("permanent failure")

    registry = {"t": TaskDefinition(name="t", description="d", func=always_fails, interval_seconds=60, max_attempts=2)}
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry=registry, retry_manager_factory=FAST_RETRY)

    run = service.run_task("t")

    assert run.status == TaskStatus.FAILED
    assert run.attempt == 2
    assert "permanent failure" in run.error_message
    assert run.finished_at is not None


def test_run_task_skips_when_already_running(db_session: Session) -> None:
    def noop(session: Session) -> dict:
        return {}

    registry = {"t": TaskDefinition(name="t", description="d", func=noop, interval_seconds=60, max_attempts=1)}
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry=registry, retry_manager_factory=FAST_RETRY)

    service._locks["t"].acquire()
    try:
        run = service.run_task("t", triggered_by="schedule")
    finally:
        service._locks["t"].release()

    assert run.status == TaskStatus.SKIPPED
    assert run.attempt == 0
    assert "already running" in run.error_message

    # Releasing the lock lets a subsequent run proceed normally.
    run2 = service.run_task("t")
    assert run2.status == TaskStatus.SUCCESS


def test_run_task_rolls_back_session_between_retry_attempts(db_session: Session) -> None:
    """A failed attempt must not leave the session in a state that poisons
    the next retry attempt — regression coverage for the explicit
    `session.rollback()` in `SchedulerService._run_locked`."""
    attempts = {"n": 0}

    def writes_then_fails_once(session: Session) -> dict:
        attempts["n"] += 1
        employer = Employer(name=f"Attempt {attempts['n']}")
        session.add(employer)
        session.flush()
        if attempts["n"] == 1:
            raise RuntimeError("fail after a partial write")
        return {"attempt": attempts["n"]}

    registry = {"t": TaskDefinition(name="t", description="d", func=writes_then_fails_once, interval_seconds=60, max_attempts=2)}
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry=registry, retry_manager_factory=FAST_RETRY)

    run = service.run_task("t")
    assert run.status == TaskStatus.SUCCESS
    assert run.attempt == 2


def test_run_task_unknown_name_raises_key_error(db_session: Session) -> None:
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry={})
    with pytest.raises(KeyError):
        service.run_task("not_a_real_task")


def test_get_history_orders_most_recent_first(db_session: Session) -> None:
    def noop(session: Session) -> dict:
        return {}

    registry = {"t": TaskDefinition(name="t", description="d", func=noop, interval_seconds=60, max_attempts=1)}
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry=registry, retry_manager_factory=FAST_RETRY)

    first = service.run_task("t")
    second = service.run_task("t")

    history = service.get_history(task_name="t")
    assert [run.id for run in history[:2]] == [second.id, first.id]


def test_get_latest_per_task_returns_one_row_per_task(db_session: Session) -> None:
    def noop(session: Session) -> dict:
        return {}

    registry = {
        "a": TaskDefinition(name="a", description="d", func=noop, interval_seconds=60, max_attempts=1),
        "b": TaskDefinition(name="b", description="d", func=noop, interval_seconds=60, max_attempts=1),
    }
    service = SchedulerService(session_factory=_session_factory(db_session), task_registry=registry, retry_manager_factory=FAST_RETRY)
    service.run_task("a")
    service.run_task("a")
    service.run_task("b")

    latest = service.get_latest_per_task()
    assert set(latest.keys()) == {"a", "b"}


# --- Real task registry sanity -------------------------------------------------


def test_task_registry_has_the_expected_registered_tasks() -> None:
    """The original 5 Background Scheduler milestone tasks, plus
    `send_due_reminders` (Job Management), `send_due_interview_reminders`
    (Interview & Calendar Management), `import_provider_jobs`/
    `check_closing_soon_jobs` (Job Ingestion Service), and
    `send_pending_emails`/`send_daily_digest`/`send_weekly_summary` (Real
    Email Notification Delivery)."""
    assert set(TASK_REGISTRY.keys()) == {
        "import_fixture_jobs",
        "run_ai_matching",
        "generate_draft_documents",
        "update_workflow_statuses",
        "cleanup_old_logs",
        "send_due_reminders",
        "send_due_interview_reminders",
        "import_provider_jobs",
        "check_closing_soon_jobs",
        "send_pending_emails",
        "send_daily_digest",
        "send_weekly_summary",
    }


# --- import_fixture_jobs --------------------------------------------------------


def test_import_fixture_jobs_creates_jobs_from_local_json(db_session: Session, tmp_path) -> None:
    fixture = tmp_path / "jobs.json"
    fixture.write_text(json.dumps([
        {"title": "Test Role", "employer": "Test Employer", "job_url": "https://example.com/1", "reference_number": "REF-1"},
    ]))
    import job_automation.config.settings as settings_module
    original_path = settings_module.settings.scheduler_fixture_jobs_path
    settings_module.settings.scheduler_fixture_jobs_path = fixture
    try:
        result = import_fixture_jobs.run(db_session)
    finally:
        settings_module.settings.scheduler_fixture_jobs_path = original_path

    assert result == {"jobs_seen": 1, "jobs_created": 1, "jobs_updated": 0}
    job = db_session.scalars(select(Job).where(Job.title == "Test Role")).first()
    assert job is not None


def test_import_fixture_jobs_is_idempotent(db_session: Session) -> None:
    first = import_fixture_jobs.run(db_session)
    db_session.commit()
    second = import_fixture_jobs.run(db_session)

    assert first["jobs_created"] > 0
    assert second["jobs_created"] == 0
    assert second["jobs_updated"] == first["jobs_created"]


def test_import_fixture_jobs_raises_clearly_when_fixture_missing(db_session: Session, tmp_path) -> None:
    import job_automation.config.settings as settings_module
    original_path = settings_module.settings.scheduler_fixture_jobs_path
    settings_module.settings.scheduler_fixture_jobs_path = tmp_path / "does_not_exist.json"
    try:
        with pytest.raises(FileNotFoundError):
            import_fixture_jobs.run(db_session)
    finally:
        settings_module.settings.scheduler_fixture_jobs_path = original_path


# --- run_ai_matching -------------------------------------------------------------


def test_run_ai_matching_evaluates_jobs_with_fake_llm_provider(db_session: Session) -> None:
    user = _seed_user_with_profile(db_session)
    _seed_job(db_session)
    db_session.commit()

    result = run_ai_matching.run(db_session)
    db_session.commit()

    assert result == {"users_processed": 1, "users_skipped_no_profile": 0, "matches_evaluated": 1}
    match = db_session.scalars(select(JobMatch).where(JobMatch.user_id == user.id)).first()
    assert match is not None
    assert match.analysis["used_llm"] is True  # proves SchedulerFakeLLMProvider was genuinely used


def test_run_ai_matching_skips_users_without_a_saved_profile(db_session: Session) -> None:
    AuthService(db_session).register(email="noprofile@example.com", password="NoProfilePass123", full_name="No Profile")
    db_session.commit()
    _seed_job(db_session)
    db_session.commit()

    result = run_ai_matching.run(db_session)

    assert result == {"users_processed": 0, "users_skipped_no_profile": 1, "matches_evaluated": 0}


# --- generate_draft_documents ----------------------------------------------------


def _seed_match(db_session: Session, *, user: User, job: Job, score: float) -> JobMatch:
    match = JobMatch(
        job=job, user=user, match_score=score,
        analysis={"overall_score": score, "confidence_score": 60.0, "category_scores": {}, "matched_keywords": [],
                  "strengths": [], "weaknesses": [], "missing_requirements": [], "recommended_actions": [], "used_llm": False},
    )
    db_session.add(match)
    db_session.flush()
    return match


def test_generate_draft_documents_only_drafts_above_threshold(db_session: Session) -> None:
    user = _seed_user_with_profile(db_session)
    strong_job = _seed_job(db_session, external_id="STRONG", title="Strong Match")
    weak_job = _seed_job(db_session, external_id="WEAK", title="Weak Match")
    db_session.commit()
    _seed_match(db_session, user=user, job=strong_job, score=80.0)
    _seed_match(db_session, user=user, job=weak_job, score=10.0)
    db_session.commit()

    result = generate_draft_documents.run(db_session)
    db_session.commit()

    assert result["documents_drafted"] == 1
    assert result["skipped_below_threshold"] == 1
    documents = db_session.scalars(select(GeneratedDocumentRecord)).all()
    assert len(documents) == 1
    assert documents[0].job_id == strong_job.id


def test_generate_draft_documents_skips_matches_that_already_have_one(db_session: Session) -> None:
    user = _seed_user_with_profile(db_session)
    job = _seed_job(db_session)
    db_session.commit()
    _seed_match(db_session, user=user, job=job, score=90.0)
    db_session.commit()

    first = generate_draft_documents.run(db_session)
    db_session.commit()
    second = generate_draft_documents.run(db_session)

    assert first["documents_drafted"] == 1
    assert second["documents_drafted"] == 0
    assert second["skipped_already_drafted"] == 1


def test_generate_draft_documents_never_auto_approves(db_session: Session) -> None:
    """Regression guard for this milestone's central safety invariant: a
    drafted document must always require human review, and its workflow
    must only ever reach DOCUMENTS_GENERATED, never further."""
    user = _seed_user_with_profile(db_session)
    job = _seed_job(db_session)
    db_session.commit()
    _seed_match(db_session, user=user, job=job, score=90.0)
    db_session.commit()

    generate_draft_documents.run(db_session)
    db_session.commit()

    document = db_session.scalars(select(GeneratedDocumentRecord)).first()
    assert document.status in ("draft", "needs_review")  # never "approved"

    from job_automation.workflows.workflow_repository import WorkflowRepository
    workflow = WorkflowRepository(db_session).find_by_job_and_user(job.id, user.id)
    assert workflow.status == "documents_generated"


# --- update_workflow_statuses ----------------------------------------------------


def test_update_workflow_statuses_creates_missing_workflows(db_session: Session) -> None:
    user = _seed_user_with_profile(db_session)
    job = _seed_job(db_session)
    db_session.commit()
    _seed_match(db_session, user=user, job=job, score=50.0)
    db_session.commit()

    result = update_workflow_statuses.run(db_session)
    db_session.commit()

    assert result == {"workflows_created": 1, "workflows_already_existed": 0}

    result2 = update_workflow_statuses.run(db_session)
    assert result2 == {"workflows_created": 0, "workflows_already_existed": 1}


# --- cleanup_old_logs -------------------------------------------------------------


def test_cleanup_old_logs_deletes_only_old_finished_runs(db_session: Session) -> None:
    # Naive UTC throughout, matching `scheduler_models.utc_now()` — SQLite
    # has no real timezone-aware storage, so mixing in an aware datetime
    # here would hit the same `TypeError: can't compare offset-naive and
    # offset-aware datetimes` this fixed in `SchedulerRepository` itself.
    repo = SchedulerRepository(db_session)
    old_finished = repo.create("old_task", status=TaskStatus.RUNNING, triggered_by="manual", max_attempts=1)
    repo.mark_success(old_finished, attempt=1, result_summary={})
    old_finished.finished_at = utc_now() - timedelta(days=100)

    recent_finished = repo.create("recent_task", status=TaskStatus.RUNNING, triggered_by="manual", max_attempts=1)
    repo.mark_success(recent_finished, attempt=1, result_summary={})

    still_running = repo.create("stuck_task", status=TaskStatus.RUNNING, triggered_by="manual", max_attempts=1)
    still_running.started_at = utc_now() - timedelta(days=100)
    db_session.commit()

    # Captured before the delete — SQLAlchemy expires attributes on commit,
    # and re-fetching a deleted row's `.id` afterward raises
    # `ObjectDeletedError` rather than returning a stale value.
    old_finished_id = old_finished.id
    recent_finished_id = recent_finished.id
    still_running_id = still_running.id

    result = cleanup_old_logs.run(db_session)
    db_session.commit()

    assert result["deleted"] == 1
    remaining_ids = {run.id for run in db_session.scalars(select(SchedulerTaskRunRecord))}
    assert old_finished_id not in remaining_ids
    assert recent_finished_id in remaining_ids
    assert still_running_id in remaining_ids  # never deletes a still-running row


# --- Dashboard page + API --------------------------------------------------------


@pytest.fixture
def dashboard_db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _most_recent_user(session: Session) -> User:
    user = session.scalars(select(User).order_by(User.created_at.desc())).first()
    assert user is not None
    return user


@pytest.fixture
def dashboard_client(dashboard_db_session: Session) -> Iterator[TestClient]:
    # `get_scheduler_service` must also be overridden — `routes/scheduler.py`
    # /`api/scheduler_api.py` resolve `SchedulerService` through this
    # dependency specifically so tests can swap in one pointed at the
    # in-memory `dashboard_db_session`; overriding `get_db_session` alone
    # would have no effect on it (see that dependency's docstring in
    # `web/app.py`).
    test_scheduler_service = SchedulerService(
        session_factory=_session_factory(dashboard_db_session), retry_manager_factory=FAST_RETRY
    )
    app.dependency_overrides[get_db_session] = lambda: dashboard_db_session
    app.dependency_overrides[get_current_user] = lambda: _most_recent_user(dashboard_db_session)
    app.dependency_overrides[get_current_api_user] = lambda: _most_recent_user(dashboard_db_session)
    app.dependency_overrides[get_scheduler_service] = lambda: test_scheduler_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_scheduler_page_renders_with_no_history(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user_with_profile(dashboard_db_session)
    response = dashboard_client.get("/scheduler")
    assert response.status_code == 200
    assert "{%" not in response.text
    assert "{{" not in response.text
    assert "%}" not in response.text
    assert "}}" not in response.text
    assert "No runs yet" in response.text


def test_scheduler_page_renders_with_no_raw_jinja_when_tasks_and_history_are_populated(
    dashboard_client: TestClient, dashboard_db_session: Session
) -> None:
    """Regression test for a reported bug: raw template source
    (`{% for task in tasks %}`, `{{ task.name }}`, `{% endfor %}`,
    `{% endblock %}`) appearing in the browser instead of rendered HTML.
    Not reproducible against a real request to this route (confirmed via a
    live `uvicorn` process returning a `Content-Length` that matches the
    exact rendered byte count, zero raw tags) — this covers the populated
    case specifically, since the task table (`{% for task in tasks %}`)
    always has rows, unlike the history table which can be empty."""
    _seed_user_with_profile(dashboard_db_session)
    dashboard_client.post("/scheduler/cleanup_old_logs/run")  # populate both the task row's "last run" and history

    response = dashboard_client.get("/scheduler")
    assert response.status_code == 200
    assert "{%" not in response.text
    assert "{{" not in response.text
    assert "%}" not in response.text
    assert "}}" not in response.text
    # Real interpolated values must be present in place of the raw
    # expressions that would otherwise leak (`{{ task.name }}` etc.).
    assert "Import Fixture Jobs" in response.text
    assert "Cleanup Old Logs" in response.text


def test_scheduler_run_now_button_creates_a_history_row(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user_with_profile(dashboard_db_session)
    response = dashboard_client.post("/scheduler/cleanup_old_logs/run", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/scheduler"

    page = dashboard_client.get("/scheduler")
    assert "Cleanup Old Logs" in page.text
    assert "success" in page.text.lower() or "Success" in page.text


def test_scheduler_run_now_unknown_task_404s(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user_with_profile(dashboard_db_session)
    response = dashboard_client.post("/scheduler/not_a_real_task/run")
    assert response.status_code == 404


def test_scheduler_api_list_tasks(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user_with_profile(dashboard_db_session)
    response = dashboard_client.get("/api/scheduler/tasks")
    assert response.status_code == 200
    names = {task["name"] for task in response.json()}
    assert names == set(TASK_REGISTRY.keys())


def test_scheduler_api_run_now_returns_run_summary(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user_with_profile(dashboard_db_session)
    response = dashboard_client.post("/api/scheduler/cleanup_old_logs/run")
    assert response.status_code == 200
    body = response.json()
    assert body["task_name"] == "cleanup_old_logs"
    assert body["status"] == "success"


def test_scheduler_api_history_reflects_runs(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user_with_profile(dashboard_db_session)
    dashboard_client.post("/api/scheduler/cleanup_old_logs/run")
    response = dashboard_client.get("/api/scheduler/history")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_scheduler_page_requires_authentication(dashboard_db_session: Session) -> None:
    """Overrides only `get_db_session` (never the real `data/jobs.db`) and
    deliberately leaves `get_current_user`/`get_current_api_user`
    un-overridden — proves the scheduler page is protected exactly like
    every other dashboard route (see tests/test_authentication.py for the
    full protected-route sweep)."""
    app.dependency_overrides[get_db_session] = lambda: dashboard_db_session
    try:
        with TestClient(app) as client:
            response = client.get("/scheduler", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/login?next=/scheduler"
    finally:
        app.dependency_overrides.clear()


def test_scheduler_api_requires_authentication(dashboard_db_session: Session) -> None:
    app.dependency_overrides[get_db_session] = lambda: dashboard_db_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/scheduler/tasks")
            assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
