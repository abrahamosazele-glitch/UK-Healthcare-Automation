"""
Reads/writes one user's `NotificationPreferences` row — created lazily on
first access (`get_or_create()`) rather than at registration, since every
field already has a sensible default (see that model's docstring).

Used by `EmailNotificationProvider` (read-only, deciding whether to email)
and `web.routes.notification_settings` (read + write, the settings page).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.notification_preferences import NotificationPreferences


class NotificationPreferencesService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, user_id: uuid.UUID) -> NotificationPreferences:
        prefs = self._session.scalar(
            select(NotificationPreferences).where(NotificationPreferences.user_id == user_id)
        )
        if prefs is None:
            prefs = NotificationPreferences(user_id=user_id)
            self._session.add(prefs)
            self._session.flush()
        return prefs

    def update(
        self,
        user_id: uuid.UUID,
        *,
        email_new_jobs_imported: bool,
        email_high_match: bool,
        email_interview_reminders: bool,
        email_closing_soon: bool,
        email_daily_digest: bool,
        email_weekly_summary: bool,
        email_scheduler_status: bool,
        email_document_generated: bool,
        quiet_hours_start: int | None,
        quiet_hours_end: int | None,
        daily_digest_hour: int,
        ai_match_threshold: float,
        preferred_email: str | None,
    ) -> NotificationPreferences:
        prefs = self.get_or_create(user_id)
        prefs.email_new_jobs_imported = email_new_jobs_imported
        prefs.email_high_match = email_high_match
        prefs.email_interview_reminders = email_interview_reminders
        prefs.email_closing_soon = email_closing_soon
        prefs.email_daily_digest = email_daily_digest
        prefs.email_weekly_summary = email_weekly_summary
        prefs.email_scheduler_status = email_scheduler_status
        prefs.email_document_generated = email_document_generated
        prefs.quiet_hours_start = quiet_hours_start
        prefs.quiet_hours_end = quiet_hours_end
        prefs.daily_digest_hour = daily_digest_hour
        prefs.ai_match_threshold = ai_match_threshold
        prefs.preferred_email = preferred_email or None
        self._session.flush()
        return prefs
