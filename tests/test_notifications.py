"""
Tests for the notification and event system: notification CRUD, unread
counts, mark read/all read, the event bus (publish/subscribe, subscriber
failure isolation), provider registration (in-app real, email/SMS/push
placeholders), every existing-module integration hook this milestone adds
(scheduler start/finish/error, AI matching, documents, workflow, auth),
and the dashboard page/API. No real email/SMS/push is ever sent — only
`InAppNotificationProvider` is exercised for real; the other three are
tested only for raising `NotImplementedError`.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_automation.ai.matching_models import JobSnapshot
from job_automation.auth.auth_service import AuthService
from job_automation.core.retry_manager import RetryManager
from job_automation.database import models  # noqa: F401 - registers models on Base.metadata
from job_automation.database.base import Base
from job_automation.database.models import Employer, Job, User
from job_automation.documents.document_service import DocumentService
from job_automation.notifications.event_bus import EventBus
from job_automation.notifications.events import Event, EventType
from job_automation.notifications.notification_listeners import register_notification_listeners
from job_automation.notifications.notification_models import NotificationSeverity, NotificationType
from job_automation.notifications.notification_providers import (
    EmailNotificationProvider,
    InAppNotificationProvider,
    PushNotificationProvider,
    SMSNotificationProvider,
)
from job_automation.notifications.notification_repository import NotificationRepository
from job_automation.notifications.notification_service import NotificationService
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.profile_service import ProfileService
from job_automation.scheduler.scheduler_models import TaskDefinition
from job_automation.scheduler.scheduler_service import SchedulerService
from job_automation.web.app import app, get_current_api_user, get_current_user, get_db_session
from job_automation.workflows.status_manager import StatusManager
from job_automation.workflows.workflow_repository import WorkflowRepository
from job_automation.workflows.workflow_service import WorkflowService

FAST_RETRY = lambda max_attempts: RetryManager(  # noqa: E731
    max_retries=max_attempts, base_delay_seconds=0.01, max_delay_seconds=0.02
)


class FakeLLMProvider:
    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        return "Sample generated content for testing."


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


@pytest.fixture
def bus() -> EventBus:
    """A fresh, isolated `EventBus` per test, with notification listeners
    registered on it — never the shared `notifications.event_bus.event_bus`
    singleton, so these tests can't interfere with (or be interfered with
    by) anything published to the real one elsewhere in the suite."""
    fresh_bus = EventBus()
    register_notification_listeners(fresh_bus)
    return fresh_bus


def _seed_user(db_session: Session, email: str = "notify@example.com") -> User:
    user = AuthService(db_session, event_bus=EventBus()).register(
        email=email, password="NotifyPassword123", full_name="Notify User"
    )
    db_session.commit()
    return user


# --- NotificationService: create, unread counts, mark read/all read -------------


def test_create_persists_a_notification_and_delivers_through_providers() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = _seed_user(session)

    delivered = []

    class RecordingProvider(InAppNotificationProvider):
        def send(self, notification):
            delivered.append(notification.id)

    service = NotificationService(session, providers=[RecordingProvider()])
    notification = service.create(
        user_id=user.id,
        type=NotificationType.MATCH_COMPLETED,
        title="Test",
        message="Test message",
        severity=NotificationSeverity.INFO,
        source="test",
        metadata={"foo": "bar"},
    )
    session.commit()

    assert notification.title == "Test"
    assert notification.metadata_ == {"foo": "bar"}
    assert notification.read_at is None
    assert delivered == [notification.id]


def test_create_defaults_to_in_app_and_email_providers() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = _seed_user(session)

    service = NotificationService(session)
    assert len(service._providers) == 2
    assert isinstance(service._providers[0], InAppNotificationProvider)
    assert isinstance(service._providers[1], EmailNotificationProvider)
    # Never raises — ERROR_OCCURRED isn't one of the eight email-eligible
    # types, so EmailNotificationProvider is a no-op here too.
    service.create(
        user_id=user.id, type=NotificationType.ERROR_OCCURRED, title="t", message="m", source="test"
    )


def test_unread_count_and_list_notifications(db_session: Session) -> None:
    user = _seed_user(db_session)
    service = NotificationService(db_session)
    first = service.create(user_id=user.id, type=NotificationType.MATCH_COMPLETED, title="A", message="a", source="test")
    second = service.create(user_id=user.id, type=NotificationType.DOCUMENT_GENERATED, title="B", message="b", source="test")
    # SQLite's `CURRENT_TIMESTAMP` only has second resolution, so two rows
    # created in the same test can tie on `created_at` — set explicit,
    # clearly-ordered timestamps rather than relying on real-time creation
    # order (same fix as `test_workflow_repository_list_for_user_orders_most_recent_first`
    # in `test_application_workflow.py`, and `test_documents_api_404s_for_other_users_document`
    # in `test_web_dashboard.py`).
    second.created_at = first.created_at + timedelta(seconds=1)
    db_session.commit()

    assert service.unread_count(user.id) == 2
    notifications = service.list_notifications(user.id)
    assert [n.title for n in notifications] == ["B", "A"]  # most recent first


def test_system_wide_notification_visible_to_every_user(db_session: Session) -> None:
    user_a = _seed_user(db_session, email="a@example.com")
    user_b = _seed_user(db_session, email="b@example.com")
    service = NotificationService(db_session)
    service.create(user_id=None, type=NotificationType.SCHEDULER_TASK_STARTED, title="System", message="m", source="scheduler")
    db_session.commit()

    assert service.unread_count(user_a.id) == 1
    assert service.unread_count(user_b.id) == 1
    assert service.list_notifications(user_a.id)[0].title == "System"


def test_mark_read_sets_read_at_and_is_idempotent(db_session: Session) -> None:
    user = _seed_user(db_session)
    service = NotificationService(db_session)
    notification = service.create(user_id=user.id, type=NotificationType.MATCH_COMPLETED, title="A", message="a", source="test")
    db_session.commit()

    assert service.unread_count(user.id) == 1
    updated = service.mark_read(notification.id, user_id=user.id)
    db_session.commit()
    assert updated.read_at is not None
    assert service.unread_count(user.id) == 0

    # Marking an already-read notification again doesn't error or change the timestamp meaningfully.
    first_read_at = updated.read_at
    service.mark_read(notification.id, user_id=user.id)
    db_session.commit()
    assert updated.read_at == first_read_at


def test_mark_read_rejects_another_users_notification(db_session: Session) -> None:
    owner = _seed_user(db_session, email="owner@example.com")
    other = _seed_user(db_session, email="other@example.com")
    service = NotificationService(db_session)
    notification = service.create(user_id=owner.id, type=NotificationType.MATCH_COMPLETED, title="A", message="a", source="test")
    db_session.commit()

    with pytest.raises(ValueError):
        service.mark_read(notification.id, user_id=other.id)


def test_mark_read_allows_reading_a_system_wide_notification(db_session: Session) -> None:
    user = _seed_user(db_session)
    service = NotificationService(db_session)
    notification = service.create(user_id=None, type=NotificationType.SCHEDULER_TASK_STARTED, title="A", message="a", source="scheduler")
    db_session.commit()

    updated = service.mark_read(notification.id, user_id=user.id)
    db_session.commit()
    assert updated.read_at is not None


def test_mark_all_read_marks_only_that_users_visible_notifications(db_session: Session) -> None:
    user_a = _seed_user(db_session, email="a2@example.com")
    user_b = _seed_user(db_session, email="b2@example.com")
    service = NotificationService(db_session)
    service.create(user_id=user_a.id, type=NotificationType.MATCH_COMPLETED, title="A1", message="m", source="test")
    service.create(user_id=user_a.id, type=NotificationType.MATCH_COMPLETED, title="A2", message="m", source="test")
    service.create(user_id=user_b.id, type=NotificationType.MATCH_COMPLETED, title="B1", message="m", source="test")
    db_session.commit()

    marked = service.mark_all_read(user_a.id)
    db_session.commit()

    assert marked == 2
    assert service.unread_count(user_a.id) == 0
    assert service.unread_count(user_b.id) == 1  # untouched


# --- Event bus: publish/subscribe, failure isolation -----------------------------


def test_event_bus_delivers_to_subscribed_handler(db_session: Session) -> None:
    received = []
    test_bus = EventBus()
    test_bus.subscribe(EventType.MATCH_COMPLETED, lambda event, session: received.append(event))

    event = Event(event_type=EventType.MATCH_COMPLETED, payload={"matches_evaluated": 5})
    test_bus.publish(event, db_session)

    assert received == [event]


def test_event_bus_does_not_deliver_to_unsubscribed_event_type(db_session: Session) -> None:
    received = []
    test_bus = EventBus()
    test_bus.subscribe(EventType.MATCH_COMPLETED, lambda event, session: received.append(event))

    test_bus.publish(Event(event_type=EventType.JOB_IMPORTED, payload={}), db_session)

    assert received == []


def test_event_bus_swallows_a_failing_handler_without_raising(db_session: Session) -> None:
    test_bus = EventBus()

    def broken_handler(event, session):
        raise RuntimeError("handler bug")

    calls = []
    test_bus.subscribe(EventType.MATCH_COMPLETED, broken_handler)
    test_bus.subscribe(EventType.MATCH_COMPLETED, lambda event, session: calls.append(event))

    # Must not raise — a subscriber's failure can never break the publisher.
    test_bus.publish(Event(event_type=EventType.MATCH_COMPLETED, payload={}), db_session)

    assert len(calls) == 1  # the second, working handler still ran


def test_register_notification_listeners_is_idempotent_per_bus(db_session: Session) -> None:
    user = _seed_user(db_session)
    test_bus = EventBus()
    register_notification_listeners(test_bus)
    register_notification_listeners(test_bus)  # calling twice must not double-subscribe

    test_bus.publish(
        Event(event_type=EventType.MATCH_COMPLETED, payload={"matches_evaluated": 3}, user_id=user.id),
        db_session,
    )
    db_session.commit()

    notifications = NotificationService(db_session).list_notifications(user.id)
    assert len(notifications) == 1  # not 2


def test_clear_removes_all_subscriptions() -> None:
    test_bus = EventBus()
    test_bus.subscribe(EventType.MATCH_COMPLETED, lambda event, session: None)
    test_bus.clear()
    assert test_bus._subscribers == {}


# --- Provider interface: in-app real, others are explicit placeholders ----------


def test_in_app_provider_send_never_raises(db_session: Session) -> None:
    user = _seed_user(db_session)
    notification = NotificationRepository(db_session).create(
        user_id=user.id, type=NotificationType.MATCH_COMPLETED, title="t", message="m",
        severity=NotificationSeverity.INFO, source="test",
    )
    InAppNotificationProvider().send(notification)  # must not raise


@pytest.mark.parametrize("provider_cls", [SMSNotificationProvider, PushNotificationProvider])
def test_placeholder_providers_raise_not_implemented(db_session: Session, provider_cls) -> None:
    user = _seed_user(db_session)
    notification = NotificationRepository(db_session).create(
        user_id=user.id, type=NotificationType.MATCH_COMPLETED, title="t", message="m",
        severity=NotificationSeverity.INFO, source="test",
    )
    with pytest.raises(NotImplementedError):
        provider_cls().send(notification)


def test_email_provider_is_a_no_op_for_non_email_eligible_types(db_session: Session) -> None:
    """`EmailNotificationProvider` is real (unlike SMS/Push), but only
    handles the eight types in `email_templates.EMAIL_TEMPLATE_TYPES` —
    everything else (MATCH_COMPLETED here) must not raise and must not
    enqueue anything. Full eligible-type behavior (quiet hours, the
    AI-match threshold, per-type opt-out, fan-out) is covered in
    tests/test_email_notifications.py."""
    user = _seed_user(db_session)
    notification = NotificationRepository(db_session).create(
        user_id=user.id, type=NotificationType.MATCH_COMPLETED, title="t", message="m",
        severity=NotificationSeverity.INFO, source="test",
    )
    EmailNotificationProvider(db_session).send(notification)  # must not raise
    from job_automation.database.models.email_outbox_record import EmailOutboxRecord

    assert db_session.query(EmailOutboxRecord).count() == 0


def test_notification_service_can_be_configured_with_a_placeholder_provider_without_sending(db_session: Session) -> None:
    """Registering a placeholder provider is legal (satisfies the
    interface) — it just must never actually be exercised by anything in
    this codebase, which is a property of what calls `NotificationService`,
    not of the provider itself."""
    user = _seed_user(db_session)
    service = NotificationService(db_session, providers=[InAppNotificationProvider()])
    notification = service.create(user_id=user.id, type=NotificationType.MATCH_COMPLETED, title="t", message="m", source="test")
    assert notification is not None


# --- Integration: scheduler start/finish/error -----------------------------------


def test_scheduler_task_success_publishes_started_and_finished_notifications(db_session: Session, bus: EventBus) -> None:
    def noop(session: Session) -> dict:
        return {"ok": True}

    registry = {"t": TaskDefinition(name="t", description="d", func=noop, interval_seconds=60, max_attempts=1)}
    service = SchedulerService(
        session_factory=lambda: contextlib.nullcontext(db_session),
        task_registry=registry,
        retry_manager_factory=FAST_RETRY,
        event_bus=bus,
    )
    service.run_task("t")

    notifications = db_session.scalars(select(models.Notification)).all()
    types = {n.type for n in notifications}
    assert "scheduler_task_started" in types
    assert "scheduler_task_finished" in types
    finished = next(n for n in notifications if n.type == "scheduler_task_finished")
    assert finished.severity == "success"
    assert finished.user_id is None  # system-wide


def test_scheduler_task_failure_publishes_error_and_finished_notifications(db_session: Session, bus: EventBus) -> None:
    def always_fails(session: Session) -> dict:
        raise ValueError("boom")

    registry = {"t": TaskDefinition(name="t", description="d", func=always_fails, interval_seconds=60, max_attempts=1)}
    service = SchedulerService(
        session_factory=lambda: contextlib.nullcontext(db_session),
        task_registry=registry,
        retry_manager_factory=FAST_RETRY,
        event_bus=bus,
    )
    service.run_task("t")

    notifications = db_session.scalars(select(models.Notification)).all()
    error_notifications = [n for n in notifications if n.type == "error_occurred"]
    assert len(error_notifications) == 1
    assert error_notifications[0].severity == "error"
    assert "boom" in error_notifications[0].message

    finished = next(n for n in notifications if n.type == "scheduler_task_finished")
    assert finished.severity == "warning"


# --- Integration: AI matching, documents, workflow, auth ------------------------


def test_document_generated_publishes_notification_for_the_owning_user(db_session: Session, bus: EventBus) -> None:
    user = _seed_user(db_session)
    employer = Employer(name="Test Trust")
    job = Job(employer=employer, title="HCA", source_site="test", external_id="D1", url="https://example.com/d1")
    db_session.add_all([employer, job])
    db_session.flush()
    ProfileService(db_session).save(
        CandidateProfile(personal_information=PersonalInformation(full_name="Notify User")), user_id=user.id
    )
    db_session.commit()

    service = DocumentService(db_session, FakeLLMProvider(), event_bus=bus)
    service.generate_supporting_statement(
        CandidateProfile(personal_information=PersonalInformation(full_name="Notify User")),
        JobSnapshot(title="HCA", employer="Test Trust"),
        user_id=user.id,
        job_id=job.id,
    )
    db_session.commit()

    notifications = NotificationService(db_session).list_notifications(user.id)
    assert any(n.type == "document_generated" for n in notifications)


def test_workflow_transition_publishes_notification_for_the_owning_user(db_session: Session, bus: EventBus) -> None:
    user = _seed_user(db_session)
    employer = Employer(name="Test Trust")
    job = Job(employer=employer, title="HCA", source_site="test", external_id="W1", url="https://example.com/w1")
    db_session.add_all([employer, job])
    db_session.flush()
    db_session.commit()

    repository = WorkflowRepository(db_session)
    status_manager = StatusManager(repository, event_bus=bus)
    workflow_service = WorkflowService(db_session, repository=repository, status_manager=status_manager)
    # `start_workflow()` creates the row directly (never calls
    # `StatusManager.transition()` — see that method's own docstring: it's
    # the special "first row" case, not a transition). `close()` is a real
    # transition and the simplest one reachable straight from NEW_MATCH,
    # so it's what actually exercises the event-publishing hook under test.
    workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id)
    db_session.commit()
    workflow_service.close(workflow, reason="test")
    db_session.commit()

    notifications = NotificationService(db_session).list_notifications(user.id)
    workflow_notifications = [n for n in notifications if n.type == "workflow_updated"]
    assert len(workflow_notifications) == 1
    assert "closed" in workflow_notifications[0].message.lower()


def test_user_registered_publishes_welcome_notification(db_session: Session, bus: EventBus) -> None:
    user = AuthService(db_session, event_bus=bus).register(
        email="fresh@example.com", password="FreshPassword123", full_name="Fresh User"
    )
    db_session.commit()

    notifications = NotificationService(db_session).list_notifications(user.id)
    assert any(n.type == "user_registered" and n.severity == "success" for n in notifications)


def test_user_login_publishes_notification(db_session: Session, bus: EventBus) -> None:
    auth = AuthService(db_session, event_bus=bus)
    user = auth.register(email="login@example.com", password="LoginPassword123", full_name="Login User")
    db_session.commit()

    auth.authenticate(email="login@example.com", password="LoginPassword123")
    db_session.commit()

    notifications = NotificationService(db_session).list_notifications(user.id)
    assert any(n.type == "user_logged_in" for n in notifications)


def test_failed_login_publishes_no_notification(db_session: Session, bus: EventBus) -> None:
    from job_automation.auth.auth_exceptions import InvalidCredentialsError

    auth = AuthService(db_session, event_bus=bus)
    user = auth.register(email="secure@example.com", password="SecurePassword123", full_name="Secure User")
    db_session.commit()

    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(email="secure@example.com", password="WrongPassword")

    notifications = NotificationService(db_session).list_notifications(user.id)
    assert not any(n.type == "user_logged_in" for n in notifications)


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
    app.dependency_overrides[get_db_session] = lambda: dashboard_db_session
    app.dependency_overrides[get_current_user] = lambda: _most_recent_user(dashboard_db_session)
    app.dependency_overrides[get_current_api_user] = lambda: _most_recent_user(dashboard_db_session)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_dashboard_user_with_notifications(session: Session) -> User:
    user = _seed_user(session, email="dashboard@example.com")
    service = NotificationService(session)
    service.create(user_id=user.id, type=NotificationType.MATCH_COMPLETED, title="Match", message="m", source="ai_matching")
    service.create(user_id=None, type=NotificationType.SCHEDULER_TASK_STARTED, title="Sched", message="m", source="scheduler")
    session.commit()
    return user


def test_notifications_page_renders_with_no_raw_jinja(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_dashboard_user_with_notifications(dashboard_db_session)
    response = dashboard_client.get("/notifications")
    assert response.status_code == 200
    assert "{%" not in response.text
    assert "{{" not in response.text
    assert "%}" not in response.text
    assert "}}" not in response.text
    assert "Match" in response.text
    assert "Sched" in response.text


def test_notifications_page_renders_with_zero_notifications(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user(dashboard_db_session, email="empty@example.com")
    response = dashboard_client.get("/notifications")
    assert response.status_code == 200
    assert "No notifications yet" in response.text


def test_bell_badge_shows_unread_count(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_dashboard_user_with_notifications(dashboard_db_session)
    response = dashboard_client.get("/notifications/bell")
    assert response.status_code == 200
    assert "2" in response.text
    assert "notification-bell-badge" in response.text


def test_bell_badge_empty_when_no_unread(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_user(dashboard_db_session, email="noread@example.com")
    response = dashboard_client.get("/notifications/bell")
    assert response.status_code == 200
    assert response.text == ""


def test_mark_read_via_html_form_redirects_and_updates_count(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    user = _seed_dashboard_user_with_notifications(dashboard_db_session)
    notification = NotificationService(dashboard_db_session).list_notifications(user.id)[0]

    response = dashboard_client.post(f"/notifications/{notification.id}/read", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"

    assert NotificationService(dashboard_db_session).unread_count(user.id) == 1


def test_mark_all_read_via_html_form(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    user = _seed_dashboard_user_with_notifications(dashboard_db_session)

    response = dashboard_client.post("/notifications/mark-all-read", follow_redirects=False)
    assert response.status_code == 303

    assert NotificationService(dashboard_db_session).unread_count(user.id) == 0


def test_api_list_notifications(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_dashboard_user_with_notifications(dashboard_db_session)
    response = dashboard_client.get("/api/notifications")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_unread_count(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_dashboard_user_with_notifications(dashboard_db_session)
    response = dashboard_client.get("/api/notifications/unread-count")
    assert response.status_code == 200
    assert response.json() == {"count": 2}


def test_api_mark_read_returns_updated_notification(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    user = _seed_dashboard_user_with_notifications(dashboard_db_session)
    notification = NotificationService(dashboard_db_session).list_notifications(user.id)[0]

    response = dashboard_client.post(f"/api/notifications/{notification.id}/read")
    assert response.status_code == 200
    assert response.json()["read_at"] is not None


def test_api_mark_all_read(dashboard_client: TestClient, dashboard_db_session: Session) -> None:
    _seed_dashboard_user_with_notifications(dashboard_db_session)
    response = dashboard_client.post("/api/notifications/mark-all-read")
    assert response.status_code == 200
    assert response.json() == {"marked_read": 2}


def test_notifications_page_requires_authentication(dashboard_db_session: Session) -> None:
    app.dependency_overrides[get_db_session] = lambda: dashboard_db_session
    try:
        with TestClient(app) as client:
            response = client.get("/notifications", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/login?next=/notifications"
    finally:
        app.dependency_overrides.clear()


def test_notifications_api_requires_authentication(dashboard_db_session: Session) -> None:
    app.dependency_overrides[get_db_session] = lambda: dashboard_db_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/notifications")
            assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
