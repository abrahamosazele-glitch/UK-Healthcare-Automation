"""
Scheduled task: publish an `INTERVIEW_REMINDER_DUE` event for every
`InterviewReminder` whose `remind_at` has passed and mark it sent.

Thin wrapper — the actual logic lives in
`interviews.interview_service.InterviewService.process_due_reminders()`,
which is genuinely reusable business logic, not scheduler-specific (the
same separation `send_due_reminders.py` already uses for
`ReminderService`).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.interviews.interview_service import InterviewService


def run(session: Session) -> dict:
    return InterviewService(session).process_due_reminders()
