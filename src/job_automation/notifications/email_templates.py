"""
Renders `(subject, html_body)` for each of the eight email-eligible
notification types (see docs/EMAIL_NOTIFICATIONS.md). Pure functions —
no SQLAlchemy, no SMTP — taking the already-persisted `Notification` row
`EmailNotificationProvider` is deciding whether to email, so every
template reuses the exact title/message/metadata `notification_listeners
.py` already built rather than re-deriving its own copy of that logic.

Two shapes cover all eight types:
- `_render_generic()` — one fact, one sentence (new job imported, high
  match, interview reminder, closing soon, document generated, scheduler
  status). Six of the eight types share this.
- `_render_digest()` — a small table of counts for a period ("today" /
  "this week"), used by both the daily digest and weekly summary — the
  only difference between them is which period label and which numbers
  `scheduler.tasks.send_daily_digest`/`send_weekly_summary` compute.
"""

from __future__ import annotations

from job_automation.database.models.notification import Notification
from job_automation.notifications.notification_models import NotificationType

_BRAND = "UK Healthcare Job Automation"


def _base_html(preheader: str, body_inner: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;color:#1f2933;">
  <div style="display:none;max-height:0;overflow:hidden;">{preheader}</div>
  <div style="max-width:560px;margin:0 auto;padding:24px 16px;">
    <div style="background:#0d6efd;color:#ffffff;padding:16px 20px;border-radius:8px 8px 0 0;">
      <strong>{_BRAND}</strong>
    </div>
    <div style="background:#ffffff;padding:20px;border-radius:0 0 8px 8px;border:1px solid #e5e7eb;border-top:none;">
      {body_inner}
    </div>
    <p style="color:#9aa5b1;font-size:12px;margin-top:16px;">
      You're receiving this because it's enabled in your notification settings.
      Manage what you get emailed about at /settings/notifications.
    </p>
  </div>
</body>
</html>"""


def _render_generic(notification: Notification) -> tuple[str, str]:
    body = f"""\
<h2 style="margin-top:0;font-size:18px;">{notification.title}</h2>
<p style="font-size:15px;line-height:1.5;">{notification.message}</p>"""
    return notification.title, _base_html(notification.message, body)


def _render_digest(notification: Notification, *, period_label: str) -> tuple[str, str]:
    stats: dict = (notification.metadata_ or {}).get("stats", {})
    rows = "".join(
        f'<tr><td style="padding:6px 0;border-bottom:1px solid #f0f0f0;">{label}</td>'
        f'<td style="padding:6px 0;border-bottom:1px solid #f0f0f0;text-align:right;'
        f'font-weight:bold;">{value}</td></tr>'
        for label, value in stats.items()
    )
    body = f"""\
<h2 style="margin-top:0;font-size:18px;">{notification.title}</h2>
<p style="font-size:15px;line-height:1.5;">{notification.message}</p>
<table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:12px;">
  {rows or '<tr><td style="padding:6px 0;">Nothing new ' + period_label + '.</td></tr>'}
</table>"""
    return notification.title, _base_html(notification.message, body)


_RENDERERS = {
    NotificationType.JOB_IMPORTED.value: _render_generic,
    NotificationType.NEW_HIGH_MATCH_JOB.value: _render_generic,
    NotificationType.INTERVIEW_REMINDER_DUE.value: _render_generic,
    NotificationType.JOB_CLOSING_SOON.value: _render_generic,
    NotificationType.DOCUMENT_GENERATED.value: _render_generic,
    NotificationType.SCHEDULER_TASK_FINISHED.value: _render_generic,
    NotificationType.DAILY_DIGEST.value: lambda n: _render_digest(n, period_label="today"),
    NotificationType.WEEKLY_SUMMARY.value: lambda n: _render_digest(n, period_label="this week"),
}

#: Only these eight types are ever emailed — see `EmailNotificationProvider
#: .EMAIL_ELIGIBLE_TYPES`, which this deliberately mirrors (checked there
#: independently so a type missing here fails loudly via `render()` rather
#: than silently sending a blank email).
EMAIL_TEMPLATE_TYPES = frozenset(_RENDERERS)


def render(notification: Notification) -> tuple[str, str]:
    """Returns `(subject, html_body)` for `notification`. Raises `KeyError`
    for a type with no template — every caller (`EmailNotificationProvider`)
    checks `EMAIL_TEMPLATE_TYPES` first, so this should never actually be
    hit in practice; it's a loud failure, not a silent blank email, if that
    invariant is ever broken."""
    return _RENDERERS[notification.type](notification)
