"""
One row per user: which notification types should be emailed, when quiet
hours apply, when the daily digest/weekly summary fire, the AI-match
threshold that gates the "high match" email specifically, and an optional
email address to use instead of `User.email`.

Created lazily on first access (`NotificationPreferencesService
.get_or_create()`) rather than at registration — every field has a
sensible default, so a user who never visits the notification settings
page still gets the same behavior a freshly-created row would give them.

All eight `email_*` flags gate the *email* channel only — the in-app
notification (`Notification`/`InAppNotificationProvider`) is unaffected by
any of this, matching `notification_providers.py`'s "each channel decides
independently whether to deliver" design.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.user import User


class NotificationPreferences(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    # --- Per-type email toggles (one per docs/EMAIL_NOTIFICATIONS.md
    # template) --- Scheduler success/failure defaults off: it's
    # operational noise for most users, unlike the other seven, which are
    # all directly about the candidate's own job search.
    email_new_jobs_imported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_high_match: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_interview_reminders: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_closing_soon: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_daily_digest: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_weekly_summary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_scheduler_status: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_document_generated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Quiet hours (UTC, 24h) --- Suppresses the six "reactive"/
    # real-time email types above (new job/high match/interview
    # reminder/closing soon/document generated/scheduler status) between
    # `quiet_hours_start` and `quiet_hours_end`; never suppresses the daily
    # digest or weekly summary, which already fire at a time the user
    # explicitly chose. `None`/`None` (the default) means no quiet hours.
    # A start greater than end (e.g. 22 -> 7) wraps past midnight.
    quiet_hours_start: Mapped[int | None] = mapped_column(Integer)
    quiet_hours_end: Mapped[int | None] = mapped_column(Integer)

    # --- Scheduled digest timing (UTC hour, 0-23) ---
    daily_digest_hour: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    #: `send_daily_digest` sets this after a successful send so an hourly
    #: check never sends the same day's digest twice.
    last_daily_digest_sent_date: Mapped[date | None] = mapped_column(Date)
    #: `send_weekly_summary` fires on Monday at `daily_digest_hour`; this
    #: stores the ISO year-week ("2026-W27") it last sent for, the weekly
    #: equivalent of `last_daily_digest_sent_date`.
    last_weekly_summary_sent_week: Mapped[str | None] = mapped_column(String(10))

    # --- High-match email threshold ---
    # Independent of `settings.job_ingestion_high_match_threshold` (which
    # gates whether `NEW_HIGH_MATCH_JOB` fires — and therefore whether an
    # in-app notification is created — at all): this can only ever raise
    # the bar further for the *email*, never lower it below the global
    # threshold, since the event this reads from simply never fires for a
    # lower score.
    ai_match_threshold: Mapped[float] = mapped_column(Numeric(5, 2), default=80.0, nullable=False)

    # --- Delivery address ---
    #: `None` (the default) falls back to `User.email` — set only to
    #: deliver notification emails somewhere other than the login address.
    preferred_email: Mapped[str | None] = mapped_column(String(255))

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<NotificationPreferences user={self.user_id}>"
