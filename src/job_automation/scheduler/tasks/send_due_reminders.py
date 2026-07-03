"""
Scheduled task: publish a `REMINDER_DUE` event for every `JobReminder`
whose `remind_at` has passed and mark it sent.

Thin wrapper — the actual logic lives in
`job_organization.reminder_service.ReminderService.process_due_reminders()`,
which is genuinely reusable business logic, not scheduler-specific (the
same separation `run_ai_matching.py`/`generate_draft_documents.py` already
use for `MatchingService`/`DocumentService`).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.job_organization.reminder_service import ReminderService


def run(session: Session) -> dict:
    return ReminderService(session).process_due_reminders()
