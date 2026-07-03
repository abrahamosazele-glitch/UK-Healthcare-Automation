# Real Email Notification Delivery

Turns the notification subsystem's `EmailNotificationProvider` (previously
a placeholder that always raised `NotImplementedError` — see
docs/NOTIFICATIONS.md) into a real SMTP-backed channel, with per-user
control over what gets emailed, when, and where.

## Setup: Gmail SMTP

No third-party email API — plain SMTP, `smtplib`/`email.mime` (stdlib).
Tested against Gmail's SMTP server; any standard SMTP server works with
the same four settings.

1. Enable 2-Step Verification on the Google Account you want to send
   from (required before Google will issue an App Password):
   https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords —
   choose "Mail" as the app. Google gives you a 16-character password.
   **This is not your normal Gmail password** — your real account
   password will be rejected by Gmail's SMTP server for this purpose.
3. Set in `.env`:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=youraddress@gmail.com
   SMTP_PASSWORD=the16charapppassword
   SMTP_FROM_EMAIL=youraddress@gmail.com
   SMTP_FROM_NAME=UK Healthcare Job Automation
   SMTP_USE_STARTTLS=true
   ```
4. Restart the app. Leave `SMTP_HOST` blank to run without real email —
   every notification still appears in-app; only the email side is
   skipped (`EmailService` raises a clear error the moment it's actually
   asked to send, same "degrade, don't crash at import time" pattern as
   `AnthropicProvider`/`ReedProvider`'s missing-key handling — see
   `config/settings.py`'s `smtp_*` fields).

Any other SMTP provider (Outlook, a transactional email service's SMTP
endpoint, a self-hosted mail server) works the same way — just change
`SMTP_HOST`/`SMTP_PORT`/`SMTP_USE_STARTTLS` to match it.

## Architecture: why sending is asynchronous

`EmailNotificationProvider.send()` (called by `NotificationService
.create()`, exactly like the pre-existing `InAppNotificationProvider`)
never opens an SMTP connection itself. It only decides *whether* a given
notification should become an email for a given user, and if so, inserts
one `EmailOutboxRecord` row — a plain, synchronous DB write, no slower
than any other insert already happening in that request/task.

The `send_pending_emails` scheduled task (every
`SCHEDULER_SEND_EMAILS_INTERVAL_SECONDS`, 2 minutes by default) is the
only thing that ever calls `EmailService`/opens a real SMTP connection —
on its own schedule, decoupled from whatever triggered the notification.
This is the entire mechanism behind "imports stay fast": a job import
that fans out ten notification emails to ten users enqueues ten cheap
rows and returns immediately; a slow or unreachable mail server only
delays how soon those emails actually go out, never the import itself.

One `EmailService.send()` call — its own SMTP connection — per queued
row, not a shared connection for a whole batch. A single bad row (a bad
address, a transient SMTP hiccup) therefore can't affect whether any of
the others in the same run succeed, and each row's `status`/`attempts`/
`error_message` reflects exactly what happened to it. A row that fails
`send_pending_emails.MAX_ATTEMPTS` (3) times in a row is marked
`"failed"` and never retried again; anything short of that stays
`"pending"` for the next run to retry.

## The eight notification templates

`notifications/email_templates.py` renders `(subject, html_body)` for
exactly the eight `NotificationType` values below — the same types
`notification_listeners.py` already creates in-app notifications for
(six pre-existing, two new). Two are new event types added for this
milestone (`DAILY_DIGEST`, `WEEKLY_SUMMARY`); the other six reuse events
that already existed:

| Template | `NotificationType` | Publisher |
|---|---|---|
| New jobs imported | `job_imported` | `ingestion_orchestrator`/scheduled `import_provider_jobs` |
| High AI match | `new_high_match_job` | `ingestion.auto_match_service` |
| Interview reminders | `interview_reminder_due` | `scheduler.tasks.send_due_interview_reminders` |
| Closing soon reminders | `job_closing_soon` | `scheduler.tasks.check_closing_soon_jobs` |
| Daily job digest | `daily_digest` | `scheduler.tasks.send_daily_digest` (new) |
| Weekly summary | `weekly_summary` | `scheduler.tasks.send_weekly_summary` (new) |
| Scheduler success/failure | `scheduler_task_finished` | every scheduled task, via `SchedulerService` |
| Document generation completed | `document_generated` | `documents.document_service` |

Two rendering shapes cover all eight: `_render_generic()` (one fact, one
sentence — six of the eight types) and `_render_digest()` (a small table
of counts for a period — the daily digest and weekly summary, which
differ only in which period `notifications.digest_stats.compute_stats()`
measures "new" against).

## Notification settings (`/notifications/settings`)

One `NotificationPreferences` row per user (created lazily on first
visit/decision — every field has a sensible default, so a user who never
visits this page still gets identical behavior to a freshly-created row):

- **Per-type email toggles** — each of the eight above, independently.
  Disabling one only stops the *email*; the in-app notification (bell
  icon, `/notifications`) is unaffected either way.
- **Quiet hours** (UTC, wraps past midnight, e.g. 22→7) — suppresses the
  six real-time/reactive email types. Never suppresses the daily digest
  or weekly summary, which already fire at a time the user explicitly
  chose (suppressing those too would risk a user with overlapping quiet
  hours never receiving their digest at all).
- **Daily digest hour** (UTC, 0-23) — also when the weekly summary sends
  (on Mondays) — one time-of-day control, not two, since the milestone
  only asked for the digest to be configurable.
- **AI match threshold** — independent of, and can only ever raise the
  bar above, `settings.job_ingestion_high_match_threshold` (which gates
  whether `NEW_HIGH_MATCH_JOB` fires at all, and so whether the in-app
  notification exists in the first place). Setting this lower than the
  global threshold has no additional effect — the event this reads
  `match_score` from simply never fires below it.
- **Preferred email** — defaults to the account's login email; set to
  deliver notification emails somewhere else instead.

## Notification history (`/notifications/history`)

Every `EmailOutboxRecord` for the current user — pending/sent/failed,
subject, type, when queued, when sent (or the failure reason) — the
audit trail for "did this actually get emailed."

## Testing

`tests/test_email_notifications.py` — 40 tests, no real SMTP connection
ever opened (`smtplib.SMTP` mocked):

- **`EmailService`**: raises a clear error with no `SMTP_HOST`/no sender
  address; a successful send opens STARTTLS and logs in with the
  configured credentials; an `SMTPException`/`OSError` is wrapped in
  `EmailServiceError`.
- **`email_templates`**: every one of the eight types renders a non-empty
  subject and HTML body containing the notification's own title; a type
  outside the eight raises `KeyError` (never silently renders a blank
  email); the digest template shows "Nothing new" when stats are empty.
- **`NotificationPreferencesService`**: lazy creation with defaults;
  idempotent (a second call returns the same row); every field updates.
- **`EmailNotificationProvider`** (the core decision logic): enqueues for
  an eligible type with default preferences; a disabled per-type toggle
  suppresses it; the AI-match threshold suppresses a high-match email
  below it but not above; quiet hours (including the midnight-wrap case)
  suppress the six reactive types but never the daily digest; a
  `preferred_email` override is used instead of the login address; a
  system-wide notification (`JOB_IMPORTED`/`SCHEDULER_TASK_FINISHED`,
  `user_id=None`) fans out to every active, opted-in user and skips
  inactive/opted-out ones; an internal error never propagates out of
  `send()` (the same discipline `InAppNotificationProvider` follows
  trivially).
- **`send_pending_emails`**: marks a successful send `"sent"` with a
  timestamp; a failure increments `attempts` and stays `"pending"` below
  `MAX_ATTEMPTS`, becomes `"failed"` at it; already-sent rows are never
  re-sent.
- **`send_daily_digest`/`send_weekly_summary`**: only fire at the user's
  configured hour; never send twice in the same day/week
  (`last_daily_digest_sent_date`/`last_weekly_summary_sent_week`); the
  weekly task only fires on Mondays; `digest_stats.compute_stats()`
  correctly counts recent jobs/matches/high-matches for one user.
- **Web routes**: the settings page renders and saves every field
  correctly (including an unchecked checkbox correctly clearing a
  previously-`True` flag); the history page shows only the current
  user's own emails, never another user's.

Not verified against a real Gmail account in this environment (no
outbound internet access here — see docs/JOB_INGESTION.md's "Manual live
verification" section for how that was established). Before relying on
this in production: configure `SMTP_*` per the setup section above, run
the app, trigger any notification (e.g. log in, then wait for
`send_pending_emails` to run, or trigger it via the Scheduler page's "Run
now"), and confirm a real email arrives.

## Environment variables

See `.env.example`'s "Email delivery" section for the full list with
inline documentation: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`, `SMTP_USE_STARTTLS`,
plus `SCHEDULER_SEND_EMAILS_INTERVAL_SECONDS` (how often the outbox
flushes) and `SCHEDULER_DIGEST_CHECK_INTERVAL_SECONDS` (how often the
digest/summary hour-check runs).

## Known limitations

- **No unsubscribe link / List-Unsubscribe header** — every email links
  back to `/notifications/settings` in its footer, but there's no
  one-click unsubscribe. Acceptable for a personal-use tool sending to an
  account the user themselves configured; would need addressing before
  ever sending to anyone else.
- **Plain HTML only, no plain-text alternative part** — simpler, at a
  small cost to spam-filter friendliness/very old mail clients. Not
  expected to matter for Gmail-to-Gmail or similarly modern delivery.
- **`send_daily_digest`/`send_weekly_summary` run hourly, not at the
  exact minute** — a user's chosen hour is honored within whatever
  `SCHEDULER_DIGEST_CHECK_INTERVAL_SECONDS` currently is (an hour by
  default), not to the minute.
