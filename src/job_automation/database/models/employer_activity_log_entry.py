"""
One entry in a candidate's activity timeline for an employer — either a
free-form note or a logged communication (a call, email, or meeting).
Combined into one table with an `entry_type` discriminator rather than two
separate tables (`EmployerNote`/`EmployerCommunication`): both are
timestamped, append-only, user-authored entries about the same
relationship, and a combined timeline is also the natural UI for a CRM
profile page (notes and communications interleaved chronologically, not
shown in two disconnected lists). `channel`/`contact_id` are only ever set
when `entry_type == "communication"`.

`occurred_at` (not just `created_at`) exists because a candidate logging a
phone call from yesterday needs to record *when the call happened*, not
just when they typed up the note — the two can differ.

User-scoped, like `EmployerContact`: this is one candidate's own activity
log, not a shared record visible to other users of the same employer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.employer import Employer
    from job_automation.database.models.employer_contact import EmployerContact
    from job_automation.database.models.user import User


class EmployerActivityLogEntry(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "employer_activity_log"

    employer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: deleting the contact shouldn't delete the
    # historical record that a communication happened.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employer_contacts.id", ondelete="SET NULL"), index=True
    )
    #: `ActivityEntryType.value` ("note" | "communication") — plain String,
    #: canonical enum lives in `employer_crm.employer_crm_models`, same
    #: reasoning as `SavedJob.pipeline_stage`.
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: `CommunicationChannel.value`, only set when entry_type == "communication".
    channel: Mapped[str | None] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)

    employer: Mapped["Employer"] = relationship(back_populates="activity_log")
    user: Mapped["User"] = relationship(back_populates="employer_activity_log")
    contact: Mapped["EmployerContact | None"] = relationship()

    def __repr__(self) -> str:
        return f"<EmployerActivityLogEntry {self.entry_type} employer={self.employer_id} user={self.user_id}>"
