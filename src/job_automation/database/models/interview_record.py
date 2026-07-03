"""
One scheduled interview for one candidate.

Named `InterviewRecord`, not `Interview` — this codebase already has an
unused `Interview` model (`database.models.interview.Interview`, table
`interviews`) tied to the original pre-workflow scaffold's `Application`/
`CV`/`CoverLetter` models, none of which are wired to any route, service,
or test. Reusing that name/table would either collide or silently
resurrect dead code; the `...Record` suffix is this codebase's established
disambiguation convention for exactly this situation (see
`ApplicationWorkflowRecord`, `CandidateProfileRecord`,
`GeneratedDocumentRecord` — every one of them avoids colliding with an
older/domain-layer name the same way).

`application_workflow_id` is nullable: an interview can exist before any
formal `ApplicationWorkflowRecord` does (e.g. an informal chat with a
recruiter who reached out directly, logged before a candidate has even
decided to formally apply) — the same reasoning `SavedJob`'s "New"/
"Interested" stages don't require a workflow to exist yet. When set, the
interview module offers *explicit*, user-clicked actions to advance that
workflow's status (see `interview_service.py`) — never automatic.

`interview_type`/`interview_stage`/`status` are plain `String` columns,
not `sa_enum(...)` — same reasoning as `SavedJob.pipeline_stage`: the
canonical enums live in `interviews.interview_models`, not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.application_workflow_record import (
        ApplicationWorkflowRecord,
    )
    from job_automation.database.models.employer import Employer
    from job_automation.database.models.employer_contact import EmployerContact
    from job_automation.database.models.interview_checklist_item import InterviewChecklistItem
    from job_automation.database.models.interview_note import InterviewNote
    from job_automation.database.models.interview_reminder import InterviewReminder
    from job_automation.database.models.job import Job
    from job_automation.database.models.user import User


class InterviewRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "interview_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: a job posting being removed shouldn't delete
    # the historical record that an interview happened for it.
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    application_workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("application_workflows.id", ondelete="SET NULL"), index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employer_contacts.id", ondelete="SET NULL"), index=True
    )

    interview_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    interview_stage: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(500))
    meeting_link: Mapped[str | None] = mapped_column(String(1000))
    interviewer_names: Mapped[list[str] | None] = mapped_column(JSON)
    #: Which `ReminderOffset` values should have a reminder generated —
    #: e.g. `["seven_days", "one_day"]`. Read by `InterviewService
    #: .schedule()`/`.reschedule()` to (re)create `InterviewReminder` rows;
    #: not read anywhere else, so it's a settings field, not duplicated
    #: derived data.
    reminder_offsets: Mapped[list[str] | None] = mapped_column(JSON)

    #: Free-form summary of what happened (kept distinct from the
    #: structured, categorized `InterviewNote` timeline — this is a quick
    #: overview field, matching `SavedJob.notes`'s role for jobs).
    outcome: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="interviews")
    employer: Mapped["Employer"] = relationship(back_populates="interviews")
    job: Mapped["Job | None"] = relationship()
    application_workflow: Mapped["ApplicationWorkflowRecord | None"] = relationship()
    contact: Mapped["EmployerContact | None"] = relationship()
    checklist_items: Mapped[list["InterviewChecklistItem"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan", passive_deletes=True,
        order_by="InterviewChecklistItem.created_at",
    )
    interview_notes: Mapped[list["InterviewNote"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan", passive_deletes=True,
        order_by="InterviewNote.created_at",
    )
    reminders: Mapped[list["InterviewReminder"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan", passive_deletes=True,
        order_by="InterviewReminder.remind_at",
    )

    def __repr__(self) -> str:
        return f"<InterviewRecord employer={self.employer_id} status={self.status} at={self.scheduled_at}>"
