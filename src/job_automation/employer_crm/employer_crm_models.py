"""
Value objects for the employer CRM subsystem. Deliberately dependency-free
— same design as `job_organization.job_organization_models`,
`workflows.workflow_models`: pure enums, no SQLAlchemy, no other package
imports. The canonical `EmployerType`/`ActivityEntryType`/
`CommunicationChannel` values live here; the ORM side
(`database.models.employer.Employer`, `.employer_activity_log_entry
.EmployerActivityLogEntry`) stores `.value` as a plain string, for the
same reason `SavedJob` doesn't import `PipelineStage`.
"""

from __future__ import annotations

import enum


class EmployerType(str, enum.Enum):
    NHS_TRUST = "nhs_trust"
    CARE_HOME = "care_home"
    AGENCY = "agency"
    RECRUITMENT_AGENCY = "recruitment_agency"
    OTHER = "other"


class ActivityEntryType(str, enum.Enum):
    NOTE = "note"
    COMMUNICATION = "communication"


class CommunicationChannel(str, enum.Enum):
    EMAIL = "email"
    PHONE = "phone"
    IN_PERSON = "in_person"
    VIDEO_CALL = "video_call"
    OTHER = "other"
