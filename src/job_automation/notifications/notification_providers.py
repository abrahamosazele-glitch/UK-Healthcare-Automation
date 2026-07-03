"""
The notification delivery-channel interface. `NotificationService.create()`
persists a `Notification` row (that row *is* the in-app notification —
the dashboard bell/page read it directly, see docs/NOTIFICATIONS.md) and
then hands it to every configured `NotificationProvider` to "deliver."

`InAppNotificationProvider` and `EmailNotificationProvider` are both real.
`EmailNotificationProvider` never sends anything itself — it only decides
*whether* an email should be sent (per-type preference, quiet hours, the
AI-match threshold) and, if so, enqueues an `EmailOutboxRecord`; the
`send_pending_emails` scheduled task is what actually calls
`EmailService`/SMTP. See docs/EMAIL_NOTIFICATIONS.md for the full design
and why sending is asynchronous.

`SMS`/`Push` remain placeholders — implemented as classes satisfying the
interface, not just mentioned in a docstring, but their `send()`
deliberately raises `NotImplementedError`, the same "real future
interface, not a silent no-op" pattern already used for
`profile.profile_loader`'s PDF/DOCX loaders.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.email_outbox_record import EmailOutboxRecord
from job_automation.database.models.notification import Notification
from job_automation.database.models.user import User
from job_automation.notifications import email_templates
from job_automation.notifications.notification_preferences_service import (
    NotificationPreferencesService,
)
from job_automation.utils.helpers import utc_now
from job_automation.utils.logger import logger


class NotificationProvider(ABC):
    @abstractmethod
    def send(self, notification: Notification) -> None:
        """Deliver `notification` through this channel. Raises on failure
        — `NotificationService.create()` does not currently catch
        provider errors (only `EventBus.publish()` catches *listener*
        errors); a provider that can fail in normal operation should
        catch and log internally rather than raising, the same
        discipline `InAppNotificationProvider` follows trivially by
        having nothing that can fail."""


class InAppNotificationProvider(NotificationProvider):
    """"Delivering" an in-app notification is a no-op: persisting the
    `Notification` row (already done by `NotificationService.create()`
    before any provider runs) *is* the delivery — the dashboard reads
    that row directly. This class exists so the provider interface has
    one concrete, exercised implementation, and so `NotificationService`
    has a sensible, safe default rather than requiring every caller to
    pass a provider list."""

    def send(self, notification: Notification) -> None:
        logger.debug("In-app notification {} ready: {}", notification.id, notification.title)


#: Maps a `NotificationType` value to the `NotificationPreferences` boolean
#: attribute that gates it — every type `email_templates.EMAIL_TEMPLATE_TYPES`
#: knows how to render has exactly one entry here.
_PREFERENCE_FLAG_BY_TYPE = {
    "job_imported": "email_new_jobs_imported",
    "new_high_match_job": "email_high_match",
    "interview_reminder_due": "email_interview_reminders",
    "job_closing_soon": "email_closing_soon",
    "document_generated": "email_document_generated",
    "scheduler_task_finished": "email_scheduler_status",
    "daily_digest": "email_daily_digest",
    "weekly_summary": "email_weekly_summary",
}

#: The digest/summary types are exempt from quiet hours — they already
#: fire at a time of day the user explicitly chose
#: (`NotificationPreferences.daily_digest_hour`), so suppressing them
#: again for quiet hours would just be a confusing way to never receive
#: the digest at all if the two settings happen to overlap.
_QUIET_HOURS_EXEMPT_TYPES = {"daily_digest", "weekly_summary"}


def _in_quiet_hours(current_hour: int, start: int | None, end: int | None) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= current_hour < end
    return current_hour >= start or current_hour < end  # wraps past midnight


class EmailNotificationProvider(NotificationProvider):
    """Decides whether `notification` should become a queued email, for
    every user it's relevant to — never sends anything itself (see this
    module's docstring). Constructed with the same `Session`
    `NotificationService` already has, since deciding "should this be
    emailed" requires reading that user's `NotificationPreferences` and,
    for a system-wide notification (`user_id is None` — `JOB_IMPORTED`,
    `SCHEDULER_TASK_FINISHED`), every active user's row."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def send(self, notification: Notification) -> None:
        if notification.type not in email_templates.EMAIL_TEMPLATE_TYPES:
            return  # not one of the eight email-eligible types — nothing to do

        try:
            for user_id in self._target_user_ids(notification):
                self._maybe_enqueue(notification, user_id)
        except Exception as exc:  # noqa: BLE001 - a provider must never break notification creation
            logger.error("EmailNotificationProvider failed for notification {}: {}", notification.id, exc)

    def _target_user_ids(self, notification: Notification) -> list[uuid.UUID]:
        if notification.user_id is not None:
            return [notification.user_id]
        # A system-wide notification (no single user it's "about") is
        # relevant to every active account — each user's own preferences
        # still decide whether they actually get emailed.
        return list(self._session.scalars(select(User.id).where(User.is_active.is_(True))))

    def _maybe_enqueue(self, notification: Notification, user_id: uuid.UUID) -> None:
        user = self._session.get(User, user_id)
        if user is None or not user.is_active:
            return

        prefs = NotificationPreferencesService(self._session).get_or_create(user_id)

        flag_name = _PREFERENCE_FLAG_BY_TYPE[notification.type]
        if not getattr(prefs, flag_name):
            return

        if notification.type == "new_high_match_job":
            score = (notification.metadata_ or {}).get("match_score", 0)
            if float(score) < float(prefs.ai_match_threshold):
                return

        if notification.type not in _QUIET_HOURS_EXEMPT_TYPES:
            current_hour = utc_now().hour
            if _in_quiet_hours(current_hour, prefs.quiet_hours_start, prefs.quiet_hours_end):
                return

        to_email = prefs.preferred_email or user.email
        subject, html_body = email_templates.render(notification)

        self._session.add(
            EmailOutboxRecord(
                user_id=user_id,
                notification_id=notification.id,
                to_email=to_email,
                subject=subject,
                body_html=html_body,
                notification_type=notification.type,
                status="pending",
            )
        )
        self._session.flush()


class SMSNotificationProvider(NotificationProvider):
    """Placeholder — SMS additionally needs a phone-number-verification
    decision (`User.phone` exists but is never validated as a real,
    deliverable number today)."""

    def send(self, notification: Notification) -> None:
        raise NotImplementedError(
            "SMS notifications are not implemented yet. See docs/NOTIFICATIONS.md's 'Extension points'."
        )


class PushNotificationProvider(NotificationProvider):
    """Placeholder — push requires a registered device/browser endpoint
    (Web Push subscription, FCM/APNs token) this codebase has no concept
    of yet; there being a web dashboard doesn't imply a push channel."""

    def send(self, notification: Notification) -> None:
        raise NotImplementedError(
            "Push notifications are not implemented yet. See docs/NOTIFICATIONS.md's 'Extension points'."
        )
