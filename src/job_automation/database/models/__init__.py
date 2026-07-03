"""
All ORM models, one per file. Importing this package (rather than an
individual model module) is what registers every model class on
`Base.metadata` and resolves the string-based `relationship()` targets across
files — anything that calls `Base.metadata.create_all()` or runs Alembic
autogenerate must import `job_automation.database.models` first.
"""

from job_automation.database.models.activity_log import ActivityLog
from job_automation.database.models.application import Application
from job_automation.database.models.application_workflow_record import ApplicationWorkflowRecord
from job_automation.database.models.candidate_profile_record import CandidateProfileRecord
from job_automation.database.models.certificate import Certificate
from job_automation.database.models.cover_letter import CoverLetter
from job_automation.database.models.cv import CV
from job_automation.database.models.employer import Employer
from job_automation.database.models.employer_activity_log_entry import EmployerActivityLogEntry
from job_automation.database.models.employer_contact import EmployerContact
from job_automation.database.models.employer_department import EmployerDepartment
from job_automation.database.models.email_outbox_record import EmailOutboxRecord
from job_automation.database.models.employer_profile import EmployerProfile
from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
from job_automation.database.models.interview import Interview
from job_automation.database.models.interview_checklist_item import InterviewChecklistItem
from job_automation.database.models.interview_note import InterviewNote
from job_automation.database.models.interview_record import InterviewRecord
from job_automation.database.models.interview_reminder import InterviewReminder
from job_automation.database.models.job import Job
from job_automation.database.models.job_alert import JobAlert
from job_automation.database.models.job_match import JobMatch
from job_automation.database.models.job_reminder import JobReminder
from job_automation.database.models.notification import Notification
from job_automation.database.models.notification_preferences import NotificationPreferences
from job_automation.database.models.saved_job import SavedJob
from job_automation.database.models.scheduler_task_run_record import SchedulerTaskRunRecord
from job_automation.database.models.scraper_run import ScraperRun
from job_automation.database.models.user import User
from job_automation.database.models.workflow_audit_log_record import WorkflowAuditLogRecord
from job_automation.database.models.workflow_status_history_record import (
    WorkflowStatusHistoryRecord,
)

__all__ = [
    "ActivityLog",
    "Application",
    "ApplicationWorkflowRecord",
    "CandidateProfileRecord",
    "Certificate",
    "CoverLetter",
    "CV",
    "EmailOutboxRecord",
    "Employer",
    "EmployerActivityLogEntry",
    "EmployerContact",
    "EmployerDepartment",
    "EmployerProfile",
    "GeneratedDocumentRecord",
    "Interview",
    "InterviewChecklistItem",
    "InterviewNote",
    "InterviewRecord",
    "InterviewReminder",
    "Job",
    "JobAlert",
    "JobMatch",
    "JobReminder",
    "Notification",
    "NotificationPreferences",
    "SavedJob",
    "SchedulerTaskRunRecord",
    "ScraperRun",
    "User",
    "WorkflowAuditLogRecord",
    "WorkflowStatusHistoryRecord",
]
