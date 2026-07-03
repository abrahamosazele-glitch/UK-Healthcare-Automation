"""
Scheduled task: flush the `EmailOutboxRecord` queue via real SMTP.

This is the entire "asynchronous" half of email delivery —
`EmailNotificationProvider` only ever enqueues a row (a cheap, synchronous
DB insert happening inline with whatever triggered the notification: a job
import, an AI match, a scheduler run); this task, running on its own
interval (`settings.scheduler_send_emails_interval_seconds`, a couple of
minutes by default), is the only thing that ever calls `EmailService` /
opens an SMTP connection. A slow or unreachable mail server therefore
never blocks the request/task that created the notification in the first
place — it only delays how soon the *email* goes out.

One `EmailService.send()` call (its own SMTP connection) per row, not a
shared connection for the whole batch — so a single bad row (bad
recipient address, a transient SMTP error) can't affect whether any of the
others in the same run succeed, and each row's status reflects exactly
what happened to it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.email_outbox_record import EmailOutboxRecord
from job_automation.notifications.email_service import EmailService, EmailServiceError
from job_automation.utils.helpers import utc_now
from job_automation.utils.logger import logger

#: A generous ceiling per run — this task runs every couple of minutes, so
#: even a burst of notifications drains within a few runs rather than
#: requiring one run to process an unbounded queue.
BATCH_SIZE = 25
#: After this many failed attempts, a row is marked "failed" and no longer
#: retried — matches `settings.scheduler_task_max_attempts`'s default so a
#: permanently-undeliverable email doesn't retry forever.
MAX_ATTEMPTS = 3


def run(session: Session) -> dict:
    pending = list(
        session.scalars(
            select(EmailOutboxRecord)
            .where(EmailOutboxRecord.status == "pending")
            .order_by(EmailOutboxRecord.created_at)
            .limit(BATCH_SIZE)
        )
    )

    email_service = EmailService()
    sent = 0
    failed = 0
    for record in pending:
        try:
            email_service.send(to_email=record.to_email, subject=record.subject, html_body=record.body_html)
        except EmailServiceError as exc:
            record.attempts += 1
            record.error_message = str(exc)
            if record.attempts >= MAX_ATTEMPTS:
                record.status = "failed"
                failed += 1
                logger.error("Email to {} permanently failed after {} attempts: {}", record.to_email, record.attempts, exc)
            else:
                logger.warning("Email to {} failed (attempt {}/{}): {}", record.to_email, record.attempts, MAX_ATTEMPTS, exc)
        else:
            record.status = "sent"
            record.sent_at = utc_now()
            sent += 1

    session.flush()
    logger.info("send_pending_emails: {} sent, {} failed, {} still pending", sent, failed, len(pending) - sent - failed)
    return {"sent": sent, "failed": failed, "remaining_after_this_run": len(pending) - sent - failed}
