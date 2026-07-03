"""
Scheduled task: once a day, at each user's own configured hour
(`NotificationPreferences.daily_digest_hour`, UTC), publish a
`DAILY_DIGEST` event summarizing new jobs/matches since yesterday.

Runs on an hourly interval (`settings.scheduler_digest_check_interval_seconds`)
rather than a single daily `CronTrigger`, since every user can pick a
different hour — an hourly scan checking "is it this user's hour, and have
we not already sent today" is what actually lets each user's choice take
effect, at the cost of a scan that's a no-op for most users on most runs.
`NotificationPreferences.last_daily_digest_sent_date` is the guard against
sending twice if this task somehow runs more than once within the same
hour.

Publishes the event (and so creates the in-app notification) for every
eligible active user regardless of their `email_daily_digest` flag — that
flag only controls whether `EmailNotificationProvider` additionally queues
an email; the in-app digest itself isn't an opt-in/out concept split from
"is it time yet."
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.user import User
from job_automation.notifications.digest_stats import compute_stats
from job_automation.notifications.event_bus import event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.notifications.notification_preferences_service import (
    NotificationPreferencesService,
)
from job_automation.utils.helpers import utc_now
from job_automation.utils.logger import logger


def run(session: Session) -> dict:
    now = utc_now()
    today = now.date()
    current_hour = now.hour

    users = list(session.scalars(select(User).where(User.is_active.is_(True))))
    prefs_service = NotificationPreferencesService(session)

    digests_sent = 0
    for user in users:
        prefs = prefs_service.get_or_create(user.id)
        if prefs.daily_digest_hour != current_hour:
            continue
        if prefs.last_daily_digest_sent_date == today:
            continue

        stats = compute_stats(session, user.id, since=now - timedelta(days=1))
        event_bus.publish(
            Event(
                event_type=EventType.DAILY_DIGEST,
                user_id=user.id,
                payload={"stats": stats},
            ),
            session,
        )
        prefs.last_daily_digest_sent_date = today
        digests_sent += 1

    logger.info("send_daily_digest: {} digest(s) sent", digests_sent)
    return {"digests_sent": digests_sent}
