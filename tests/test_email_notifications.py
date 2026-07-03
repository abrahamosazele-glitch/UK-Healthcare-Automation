"""
Tests for the Real Email Notification Delivery milestone: `EmailService`
(SMTP, mocked — no real network call), `email_templates` (all eight
types), `NotificationPreferencesService`, `EmailNotificationProvider`
(the core decision logic — per-type opt-out, quiet hours, the AI-match
threshold, fan-out for system-wide notifications, preferred-email
override), the `send_pending_emails`/`send_daily_digest`/
`send_weekly_summary` scheduled tasks, and the notification
settings/history web pages.
"""

from __future__ import annotations

import smtplib
import uuid
from datetime import datetime, timedelta
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_automation.config.settings import settings
from job_automation.database import models  # noqa: F401 - registers models on Base.metadata
from job_automation.database.base import Base
from job_automation.database.models import Employer, Job, JobMatch, User
from job_automation.database.models.email_outbox_record import EmailOutboxRecord
from job_automation.database.models.notification import Notification
from job_automation.database.models.notification_preferences import NotificationPreferences
from job_automation.notifications import email_templates
from job_automation.notifications import notification_providers as notification_providers_module
from job_automation.notifications.email_service import EmailService, EmailServiceError
from job_automation.notifications.notification_models import NotificationSeverity, NotificationType
from job_automation.notifications.notification_preferences_service import (
    NotificationPreferencesService,
)
from job_automation.notifications.notification_providers import EmailNotificationProvider
from job_automation.notifications.notification_repository import NotificationRepository
from job_automation.notifications.notification_service import NotificationService
from job_automation.scheduler.tasks import send_daily_digest, send_pending_emails, send_weekly_summary
from job_automation.web.app import app, get_current_user, get_db_session

pytestmark = pytest.mark.filterwarnings("ignore")


def _seed_user(session: Session, *, email: str = "candidate@example.com", is_active: bool = True) -> User:
    user = User(email=email, full_name="Test Candidate", hashed_password="unused", is_active=is_active)
    session.add(user)
    session.flush()
    return user


def _seed_job(session: Session, *, title: str = "Healthcare Assistant") -> Job:
    employer = Employer(name="Example NHS Trust")
    job = Job(
        employer=employer,
        title=title,
        source_site="nhs_jobs",
        external_id=str(uuid.uuid4()),
        url="https://example.com/job",
    )
    session.add_all([employer, job])
    session.flush()
    return job


def _create_notification(
    session: Session, *, user_id, type: NotificationType, metadata: dict | None = None
) -> Notification:
    return NotificationRepository(session).create(
        user_id=user_id,
        type=type,
        title="Test title",
        message="Test message",
        severity=NotificationSeverity.INFO,
        source="test",
        metadata=metadata,
    )


# --- EmailService ------------------------------------------------------------


def test_email_service_raises_when_smtp_host_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    with pytest.raises(EmailServiceError, match="SMTP is not configured"):
        EmailService().send(to_email="a@example.com", subject="s", html_body="<p>hi</p>")


def test_email_service_raises_when_no_sender_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from_email", None)
    monkeypatch.setattr(settings, "smtp_username", None)
    with pytest.raises(EmailServiceError, match="No sender address"):
        EmailService().send(to_email="a@example.com", subject="s", html_body="<p>hi</p>")


def test_email_service_sends_via_smtp_with_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "me@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "smtp_from_email", None)
    monkeypatch.setattr(settings, "smtp_use_starttls", True)

    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        EmailService().send(to_email="candidate@example.com", subject="Hello", html_body="<p>Hi</p>")

    mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("me@gmail.com", "app-password")
    mock_server.sendmail.assert_called_once()
    args, _ = mock_server.sendmail.call_args
    assert args[0] == "me@gmail.com"
    assert args[1] == ["candidate@example.com"]
    assert "Hello" in args[2]


def test_email_service_wraps_smtp_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_username", "me@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "smtp_from_email", None)

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
        with pytest.raises(EmailServiceError, match="Failed to send email"):
            EmailService().send(to_email="a@example.com", subject="s", html_body="<p>hi</p>")


# --- email_templates -----------------------------------------------------------


@pytest.mark.parametrize("notification_type", sorted(email_templates.EMAIL_TEMPLATE_TYPES))
def test_render_produces_nonempty_subject_and_html_for_every_eligible_type(
    db_session: Session, notification_type: str
) -> None:
    user = _seed_user(db_session)
    notification = _create_notification(
        db_session,
        user_id=user.id,
        type=NotificationType(notification_type),
        metadata={"match_score": 85, "stats": {"New jobs discovered": 3}},
    )
    subject, html = email_templates.render(notification)
    assert subject
    assert "<html" in html.lower()
    assert notification.title in html


def test_render_raises_key_error_for_ineligible_type(db_session: Session) -> None:
    user = _seed_user(db_session)
    notification = _create_notification(db_session, user_id=user.id, type=NotificationType.MATCH_COMPLETED)
    with pytest.raises(KeyError):
        email_templates.render(notification)


def test_digest_template_shows_nothing_new_when_stats_empty(db_session: Session) -> None:
    user = _seed_user(db_session)
    notification = _create_notification(
        db_session, user_id=user.id, type=NotificationType.DAILY_DIGEST, metadata={"stats": {}}
    )
    _subject, html = email_templates.render(notification)
    assert "Nothing new" in html


# --- NotificationPreferencesService ---------------------------------------------


def test_get_or_create_creates_defaults_then_returns_same_row(db_session: Session) -> None:
    user = _seed_user(db_session)
    service = NotificationPreferencesService(db_session)
    prefs = service.get_or_create(user.id)
    assert prefs.email_new_jobs_imported is True
    assert prefs.email_scheduler_status is False  # noisy/operational — off by default
    assert prefs.daily_digest_hour == 8
    assert prefs.ai_match_threshold == 80.0
    assert prefs.preferred_email is None

    again = service.get_or_create(user.id)
    assert again.id == prefs.id


def test_update_persists_every_field(db_session: Session) -> None:
    user = _seed_user(db_session)
    service = NotificationPreferencesService(db_session)
    updated = service.update(
        user.id,
        email_new_jobs_imported=False,
        email_high_match=True,
        email_interview_reminders=False,
        email_closing_soon=True,
        email_daily_digest=False,
        email_weekly_summary=True,
        email_scheduler_status=True,
        email_document_generated=False,
        quiet_hours_start=22,
        quiet_hours_end=7,
        daily_digest_hour=6,
        ai_match_threshold=90.0,
        preferred_email="other@example.com",
    )
    assert updated.email_new_jobs_imported is False
    assert updated.quiet_hours_start == 22
    assert updated.quiet_hours_end == 7
    assert updated.ai_match_threshold == 90.0
    assert updated.preferred_email == "other@example.com"


# --- EmailNotificationProvider: core decision logic -----------------------------


def test_enqueues_email_for_an_eligible_type_with_defaults(db_session: Session) -> None:
    user = _seed_user(db_session)
    notification = _create_notification(db_session, user_id=user.id, type=NotificationType.DOCUMENT_GENERATED)

    EmailNotificationProvider(db_session).send(notification)

    rows = db_session.scalars(select(EmailOutboxRecord)).all()
    assert len(rows) == 1
    assert rows[0].to_email == user.email
    assert rows[0].status == "pending"
    assert rows[0].notification_type == "document_generated"


def test_does_not_enqueue_when_type_disabled_in_preferences(db_session: Session) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).update(
        user.id,
        email_new_jobs_imported=True,
        email_high_match=True,
        email_interview_reminders=True,
        email_closing_soon=True,
        email_daily_digest=True,
        email_weekly_summary=True,
        email_scheduler_status=False,
        email_document_generated=False,  # disabled
        quiet_hours_start=None,
        quiet_hours_end=None,
        daily_digest_hour=8,
        ai_match_threshold=80.0,
        preferred_email=None,
    )
    notification = _create_notification(db_session, user_id=user.id, type=NotificationType.DOCUMENT_GENERATED)

    EmailNotificationProvider(db_session).send(notification)

    assert db_session.scalars(select(EmailOutboxRecord)).all() == []


def test_high_match_email_respects_per_user_ai_threshold(db_session: Session) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).get_or_create(user.id)
    prefs = db_session.scalar(select(NotificationPreferences).where(NotificationPreferences.user_id == user.id))
    prefs.ai_match_threshold = 90.0
    db_session.flush()

    below_threshold = _create_notification(
        db_session, user_id=user.id, type=NotificationType.NEW_HIGH_MATCH_JOB, metadata={"match_score": 85}
    )
    EmailNotificationProvider(db_session).send(below_threshold)
    assert db_session.scalars(select(EmailOutboxRecord)).all() == []

    above_threshold = _create_notification(
        db_session, user_id=user.id, type=NotificationType.NEW_HIGH_MATCH_JOB, metadata={"match_score": 95}
    )
    EmailNotificationProvider(db_session).send(above_threshold)
    assert len(db_session.scalars(select(EmailOutboxRecord)).all()) == 1


@pytest.mark.parametrize(
    "current_hour,start,end,expect_suppressed",
    [
        (23, 22, 7, True),   # wraps past midnight, inside window
        (3, 22, 7, True),    # wraps past midnight, inside window
        (10, 22, 7, False),  # outside window
        (5, 1, 5, False),    # exclusive end boundary
        (12, None, None, False),  # no quiet hours configured
    ],
)
def test_quiet_hours_suppress_reactive_types(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, current_hour: int, start, end, expect_suppressed: bool
) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).update(
        user.id,
        email_new_jobs_imported=True,
        email_high_match=True,
        email_interview_reminders=True,
        email_closing_soon=True,
        email_daily_digest=True,
        email_weekly_summary=True,
        email_scheduler_status=True,
        email_document_generated=True,
        quiet_hours_start=start,
        quiet_hours_end=end,
        daily_digest_hour=8,
        ai_match_threshold=80.0,
        preferred_email=None,
    )
    monkeypatch.setattr(
        notification_providers_module, "utc_now", lambda: datetime(2026, 1, 1, current_hour, 0, 0)
    )
    notification = _create_notification(db_session, user_id=user.id, type=NotificationType.DOCUMENT_GENERATED)

    EmailNotificationProvider(db_session).send(notification)

    rows = db_session.scalars(select(EmailOutboxRecord)).all()
    assert (len(rows) == 0) is expect_suppressed


def test_quiet_hours_never_suppress_daily_digest(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).update(
        user.id,
        email_new_jobs_imported=True,
        email_high_match=True,
        email_interview_reminders=True,
        email_closing_soon=True,
        email_daily_digest=True,
        email_weekly_summary=True,
        email_scheduler_status=True,
        email_document_generated=True,
        quiet_hours_start=0,
        quiet_hours_end=23,  # nearly the entire day is "quiet"
        daily_digest_hour=8,
        ai_match_threshold=80.0,
        preferred_email=None,
    )
    monkeypatch.setattr(notification_providers_module, "utc_now", lambda: datetime(2026, 1, 1, 8, 0, 0))
    notification = _create_notification(
        db_session, user_id=user.id, type=NotificationType.DAILY_DIGEST, metadata={"stats": {}}
    )

    EmailNotificationProvider(db_session).send(notification)

    assert len(db_session.scalars(select(EmailOutboxRecord)).all()) == 1


def test_preferred_email_overrides_login_email(db_session: Session) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).update(
        user.id,
        email_new_jobs_imported=True,
        email_high_match=True,
        email_interview_reminders=True,
        email_closing_soon=True,
        email_daily_digest=True,
        email_weekly_summary=True,
        email_scheduler_status=True,
        email_document_generated=True,
        quiet_hours_start=None,
        quiet_hours_end=None,
        daily_digest_hour=8,
        ai_match_threshold=80.0,
        preferred_email="notifications@elsewhere.example",
    )
    notification = _create_notification(db_session, user_id=user.id, type=NotificationType.DOCUMENT_GENERATED)

    EmailNotificationProvider(db_session).send(notification)

    row = db_session.scalars(select(EmailOutboxRecord)).one()
    assert row.to_email == "notifications@elsewhere.example"


def test_system_wide_notification_fans_out_to_every_active_opted_in_user(db_session: Session) -> None:
    _seed_user(db_session, email="a@example.com")
    active_opted_out = _seed_user(db_session, email="b@example.com")
    NotificationPreferencesService(db_session).update(
        active_opted_out.id,
        email_new_jobs_imported=False,
        email_high_match=True,
        email_interview_reminders=True,
        email_closing_soon=True,
        email_daily_digest=True,
        email_weekly_summary=True,
        email_scheduler_status=True,
        email_document_generated=True,
        quiet_hours_start=None,
        quiet_hours_end=None,
        daily_digest_hour=8,
        ai_match_threshold=80.0,
        preferred_email=None,
    )
    inactive = _seed_user(db_session, email="c@example.com", is_active=False)
    db_session.flush()

    # A system-wide notification has no single user_id — JOB_IMPORTED/
    # SCHEDULER_TASK_FINISHED are always created this way.
    notification = NotificationRepository(db_session).create(
        user_id=None,
        type=NotificationType.JOB_IMPORTED,
        title="New jobs imported",
        message="5 new jobs",
        severity=NotificationSeverity.INFO,
        source="test",
    )

    EmailNotificationProvider(db_session).send(notification)

    rows = db_session.scalars(select(EmailOutboxRecord)).all()
    assert {row.to_email for row in rows} == {"a@example.com"}
    assert active_opted_out.email not in {row.to_email for row in rows}
    assert inactive.email not in {row.to_email for row in rows}


def test_email_provider_never_raises_on_internal_error(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _seed_user(db_session)
    notification = _create_notification(db_session, user_id=user.id, type=NotificationType.DOCUMENT_GENERATED)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(NotificationPreferencesService, "get_or_create", _boom)
    EmailNotificationProvider(db_session).send(notification)  # must not raise


# --- Full NotificationService -> EmailOutboxRecord integration ------------------


def test_notification_service_create_enqueues_email_end_to_end(db_session: Session) -> None:
    user = _seed_user(db_session)
    NotificationService(db_session).create(
        user_id=user.id,
        type=NotificationType.INTERVIEW_REMINDER_DUE,
        title="Interview reminder",
        message="Your interview is in 30 minutes.",
        source="interviews",
    )
    row = db_session.scalars(select(EmailOutboxRecord)).one()
    assert row.to_email == user.email
    assert row.notification_type == "interview_reminder_due"


# --- send_pending_emails ---------------------------------------------------------


def _seed_outbox_row(session: Session, *, user_id=None, status: str = "pending", attempts: int = 0) -> EmailOutboxRecord:
    row = EmailOutboxRecord(
        user_id=user_id,
        to_email="candidate@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        notification_type="document_generated",
        status=status,
        attempts=attempts,
    )
    session.add(row)
    session.flush()
    return row


def test_send_pending_emails_marks_successful_sends(db_session: Session) -> None:
    user = _seed_user(db_session)
    _seed_outbox_row(db_session, user_id=user.id)

    with patch.object(EmailService, "send", return_value=None) as mock_send:
        result = send_pending_emails.run(db_session)

    mock_send.assert_called_once()
    row = db_session.scalars(select(EmailOutboxRecord)).one()
    assert row.status == "sent"
    assert row.sent_at is not None
    assert result == {"sent": 1, "failed": 0, "remaining_after_this_run": 0}


def test_send_pending_emails_retries_then_fails_permanently(db_session: Session) -> None:
    user = _seed_user(db_session)
    row = _seed_outbox_row(db_session, user_id=user.id, attempts=send_pending_emails.MAX_ATTEMPTS - 1)

    with patch.object(EmailService, "send", side_effect=EmailServiceError("smtp down")):
        result = send_pending_emails.run(db_session)

    db_session.refresh(row)
    assert row.status == "failed"
    assert row.attempts == send_pending_emails.MAX_ATTEMPTS
    assert "smtp down" in row.error_message
    assert result == {"sent": 0, "failed": 1, "remaining_after_this_run": 0}


def test_send_pending_emails_leaves_row_pending_before_max_attempts(db_session: Session) -> None:
    user = _seed_user(db_session)
    row = _seed_outbox_row(db_session, user_id=user.id, attempts=0)

    with patch.object(EmailService, "send", side_effect=EmailServiceError("transient")):
        send_pending_emails.run(db_session)

    db_session.refresh(row)
    assert row.status == "pending"
    assert row.attempts == 1


def test_send_pending_emails_ignores_already_sent_rows(db_session: Session) -> None:
    user = _seed_user(db_session)
    _seed_outbox_row(db_session, user_id=user.id, status="sent")

    with patch.object(EmailService, "send") as mock_send:
        result = send_pending_emails.run(db_session)

    mock_send.assert_not_called()
    assert result == {"sent": 0, "failed": 0, "remaining_after_this_run": 0}


# --- send_daily_digest / send_weekly_summary ------------------------------------


def test_send_daily_digest_fires_only_at_configured_hour(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).get_or_create(user.id)  # daily_digest_hour defaults to 8

    monkeypatch.setattr(send_daily_digest, "utc_now", lambda: datetime(2026, 1, 1, 9, 0, 0))
    result = send_daily_digest.run(db_session)
    assert result == {"digests_sent": 0}

    monkeypatch.setattr(send_daily_digest, "utc_now", lambda: datetime(2026, 1, 1, 8, 0, 0))
    result = send_daily_digest.run(db_session)
    assert result == {"digests_sent": 1}

    notification = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).one()
    assert notification.type == "daily_digest"


def test_send_daily_digest_does_not_double_send_same_day(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).get_or_create(user.id)
    monkeypatch.setattr(send_daily_digest, "utc_now", lambda: datetime(2026, 1, 1, 8, 0, 0))

    first = send_daily_digest.run(db_session)
    second = send_daily_digest.run(db_session)

    assert first == {"digests_sent": 1}
    assert second == {"digests_sent": 0}


def test_send_weekly_summary_only_fires_on_monday(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).get_or_create(user.id)

    # 2026-01-06 is a Tuesday.
    monkeypatch.setattr(send_weekly_summary, "utc_now", lambda: datetime(2026, 1, 6, 8, 0, 0))
    assert send_weekly_summary.run(db_session) == {"summaries_sent": 0, "reason": "not Monday"}

    # 2026-01-05 is a Monday.
    monkeypatch.setattr(send_weekly_summary, "utc_now", lambda: datetime(2026, 1, 5, 8, 0, 0))
    assert send_weekly_summary.run(db_session) == {"summaries_sent": 1}

    notification = db_session.scalars(select(Notification).where(Notification.user_id == user.id)).one()
    assert notification.type == "weekly_summary"


def test_digest_stats_reflect_recent_jobs_and_matches(db_session: Session) -> None:
    user = _seed_user(db_session)
    NotificationPreferencesService(db_session).get_or_create(user.id)
    job = _seed_job(db_session)
    db_session.add(JobMatch(job=job, user=user, match_score=95.0))
    db_session.flush()

    from job_automation.notifications.digest_stats import compute_stats

    stats = compute_stats(db_session, user.id, since=datetime.utcnow() - timedelta(days=1))
    assert stats["New jobs discovered"] == 1
    assert stats["New matches for you"] == 1
    assert any(v == 1 for k, v in stats.items() if k.startswith("High-scoring matches"))


# --- Web routes ------------------------------------------------------------------


@pytest.fixture
def notification_settings_db_session() -> Iterator[Session]:
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
def settings_client(notification_settings_db_session: Session) -> Iterator[TestClient]:
    user = _seed_user(notification_settings_db_session)
    app.dependency_overrides[get_db_session] = lambda: notification_settings_db_session
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        client.__user__ = user  # type: ignore[attr-defined]
        yield client
    app.dependency_overrides.clear()


def test_notification_settings_page_renders_with_defaults(settings_client: TestClient) -> None:
    response = settings_client.get("/notifications/settings")
    assert response.status_code == 200
    assert "Notification Settings" in response.text


def test_notification_settings_page_saves_changes(
    settings_client: TestClient, notification_settings_db_session: Session
) -> None:
    user = settings_client.__user__  # type: ignore[attr-defined]
    response = settings_client.post(
        "/notifications/settings",
        data={
            "email_new_jobs_imported": "true",
            "email_high_match": "true",
            "daily_digest_hour": "6",
            "ai_match_threshold": "90",
            "preferred_email": "other@example.com",
            "quiet_hours_start": "22",
            "quiet_hours_end": "7",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    prefs = notification_settings_db_session.scalar(
        select(NotificationPreferences).where(NotificationPreferences.user_id == user.id)
    )
    assert prefs.email_document_generated is False  # unchecked box -> False
    assert prefs.daily_digest_hour == 6
    assert prefs.ai_match_threshold == 90.0
    assert prefs.preferred_email == "other@example.com"
    assert prefs.quiet_hours_start == 22
    assert prefs.quiet_hours_end == 7


def test_notification_history_page_shows_only_current_users_emails(
    settings_client: TestClient, notification_settings_db_session: Session
) -> None:
    user = settings_client.__user__  # type: ignore[attr-defined]
    other_user = _seed_user(notification_settings_db_session, email="other@example.com")
    notification_settings_db_session.add_all(
        [
            EmailOutboxRecord(
                user_id=user.id, to_email=user.email, subject="Mine", body_html="<p>x</p>",
                notification_type="document_generated", status="sent",
            ),
            EmailOutboxRecord(
                user_id=other_user.id, to_email=other_user.email, subject="Not mine", body_html="<p>x</p>",
                notification_type="document_generated", status="sent",
            ),
        ]
    )
    notification_settings_db_session.commit()

    response = settings_client.get("/notifications/history")
    assert response.status_code == 200
    assert "Mine" in response.text
    assert "Not mine" not in response.text
