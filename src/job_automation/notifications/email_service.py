"""
Real SMTP email delivery — the one place in this codebase that opens a
network connection to send mail. Plain `smtplib`/`email.mime`, no
third-party email API/SDK: every setting needed (`settings.smtp_*`) maps
directly onto a standard SMTP connection, which is all Gmail (or any other
provider) needs. See docs/EMAIL_NOTIFICATIONS.md for setup, including
Gmail's App Password requirement.

Deliberately synchronous and un-queued in this class itself — `send()`
performs one real SMTP round trip (its own connection, not shared/pooled)
and returns/raises immediately. What keeps a job import or scheduled task
from blocking on that round trip is architectural, not inside this class:
`EmailNotificationProvider` never calls `EmailService` directly, it only
ever writes an `EmailOutboxRecord`; `scheduler.tasks.send_pending_emails`
is the only caller of `EmailService.send()`, running on its own schedule
and updating each queued row's status independently — see that task's
module docstring for why one connection per email (not one per batch) is
the right trade-off here.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from job_automation.config.settings import settings
from job_automation.utils.logger import logger


class EmailServiceError(Exception):
    """Raised when an email genuinely cannot be sent — missing SMTP
    configuration, or the SMTP server itself rejecting the connection/
    authentication/message."""


class EmailService:
    def send(self, *, to_email: str, subject: str, html_body: str) -> None:
        if not settings.smtp_host:
            raise EmailServiceError(
                "SMTP is not configured — set SMTP_HOST (and SMTP_USERNAME/SMTP_PASSWORD for "
                "an authenticated server like Gmail) in .env. See docs/EMAIL_NOTIFICATIONS.md."
            )

        from_email = settings.smtp_from_email or settings.smtp_username
        if not from_email:
            raise EmailServiceError(
                "No sender address configured — set SMTP_FROM_EMAIL or SMTP_USERNAME in .env."
            )

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{settings.smtp_from_name} <{from_email}>"
        message["To"] = to_email
        message.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                if settings.smtp_use_starttls:
                    server.starttls()
                if settings.smtp_username and settings.smtp_password:
                    server.login(settings.smtp_username, settings.smtp_password)
                server.sendmail(from_email, [to_email], message.as_string())
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailServiceError(f"Failed to send email via {settings.smtp_host}: {exc}") from exc

        logger.info("Sent email {!r} to {}", subject, to_email)
