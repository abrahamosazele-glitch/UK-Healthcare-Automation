"""
Python enums used as column types across the models, plus a small helper to
map them consistently.

Stored as plain VARCHAR (`native_enum=False`) rather than a native database
ENUM type. Native Postgres enums require an `ALTER TYPE` migration every time
a value is added or removed, which is disproportionate friction for values
like application status that are still expected to change early on. A
VARCHAR with the allowed values enforced at the application layer is the more
maintainable default here; it can be revisited later if a specific column
needs stricter DB-level enforcement.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


def sa_enum(enum_cls: type[enum.Enum], length: int = 30) -> SAEnum:
    return SAEnum(
        enum_cls,
        values_callable=lambda cls: [member.value for member in cls],
        native_enum=False,
        length=length,
    )


class JobType(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    ZERO_HOURS = "zero_hours"
    BANK = "bank"


class ApplicationStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class InterviewType(str, enum.Enum):
    PHONE = "phone"
    VIDEO = "video"
    IN_PERSON = "in_person"


class InterviewOutcome(str, enum.Enum):
    SCHEDULED = "scheduled"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class ScraperRunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class JobAlertFrequency(str, enum.Enum):
    INSTANT = "instant"
    DAILY = "daily"
    WEEKLY = "weekly"


class JobMatchStatus(str, enum.Enum):
    NEW = "new"
    VIEWED = "viewed"
    DISMISSED = "dismissed"
    APPLIED = "applied"
