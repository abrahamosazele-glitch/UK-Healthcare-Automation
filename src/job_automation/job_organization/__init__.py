"""
Personal job organization: save/favourite/hide/archive a job, track it
through a simple Kanban pipeline, and attach notes/rating/priority/
deadline/interview date/tags/checklist/reminders to it.

Deliberately separate from `job_automation.workflows` (the AI
document-generation-and-review state machine) — see
`job_organization_models.py`'s module docstring for the full reasoning,
and docs/JOB_MANAGEMENT.md for the milestone-level summary.

- `job_organization_models.py` — `PipelineStage`, `JobPriority`,
  `ReminderType`, `JobPipeline` (the stage-transition validator):
  dependency-free value objects, same convention as every other
  `*_models.py` file in this project.
- `saved_job_repository.py` — `SavedJobRepository`: pure data access for
  `SavedJob` rows.
- `job_organization_service.py` — `JobOrganizationService`: the
  orchestrator (save/favourite/hide/archive/restore, pipeline
  transitions, tracking-detail edits, checklist management).
- `reminder_repository.py` — `ReminderRepository`: pure data access for
  `JobReminder` rows.
- `reminder_service.py` — `ReminderService`: create/list reminders, and
  `process_due_reminders()` (published as `REMINDER_DUE` events, consumed
  by `scheduler.tasks.send_due_reminders`).
"""

from job_automation.job_organization.job_organization_models import (
    ALLOWED_STAGE_TRANSITIONS,
    InvalidStageTransitionError,
    JobPipeline,
    JobPriority,
    PipelineStage,
    ReminderType,
)
from job_automation.job_organization.job_organization_service import JobOrganizationService
from job_automation.job_organization.reminder_service import ReminderService

__all__ = [
    "ALLOWED_STAGE_TRANSITIONS",
    "InvalidStageTransitionError",
    "JobOrganizationService",
    "JobPipeline",
    "JobPriority",
    "PipelineStage",
    "ReminderService",
    "ReminderType",
]
