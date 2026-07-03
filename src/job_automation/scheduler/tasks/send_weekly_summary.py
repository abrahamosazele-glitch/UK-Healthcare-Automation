"""
Scheduled task: once a week (Mondays), at each user's own configured hour
(`NotificationPreferences.daily_digest_hour` — reused rather than adding a
second, separate "weekly summary hour" control the milestone never asked
for), publish a `WEEKLY_SUMMARY` event summarizing the last 7 days.

Same hourly-scan shape as `send_daily_digest` — see that task's module
docstring for why an hourly check (not a single weekly `CronTrigger`) is
what actually lets a per-user hour choice take effect.
`NotificationPreferences.last_weekly_summary_sent_week` (an ISO
"YYYY-Www" string) is the once-per-week guard, the weekly equivalent of
`last_daily_digest_sent_date`.
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


def _iso_week_key(now) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def run(session: Session) -> dict:
    now = utc_now()
    current_hour = now.hour

    if now.weekday() != 0:  # Monday only
        return {"summaries_sent": 0, "reason": "not Monday"}

    week_key = _iso_week_key(now)
    users = list(session.scalars(select(User).where(User.is_active.is_(True))))
    prefs_service = NotificationPreferencesService(session)

    summaries_sent = 0
    for user in users:
        prefs = prefs_service.get_or_create(user.id)
        if prefs.daily_digest_hour != current_hour:
            continue
        if prefs.last_weekly_summary_sent_week == week_key:
            continue

        stats = compute_stats(session, user.id, since=now - timedelta(days=7))
        event_bus.publish(
            Event(
                event_type=EventType.WEEKLY_SUMMARY,
                user_id=user.id,
                payload={"stats": stats},
            ),
            session,
        )
        prefs.last_weekly_summary_sent_week = week_key
        summaries_sent += 1

    logger.info("send_weekly_summary: {} summary(ies) sent", summaries_sent)
    return {"summaries_sent": summaries_sent}
