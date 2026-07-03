"""
Persisted candidate profile — one row per user.

Named `CandidateProfileRecord`, not `CandidateProfile`, to avoid colliding
with the domain dataclass of that name in `job_automation.profile
.candidate_profile` — this is the ORM/persistence side, that is the pure
domain model. The whole structured profile (personal info, education,
employment history, skills, certificates, professional registrations,
languages, visa status, preferences, career goals, availability, keywords)
is stored as one JSON blob rather than a dozen additional tables: it's a
single-user, document-like structure that evolves in shape over time
(mirrors the same reasoning as `Job.requirements`/`JobMatch.analysis`).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_automation.database.base import Base
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from job_automation.database.models.user import User


class CandidateProfileRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "candidate_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    #: Which loader produced this ("json"/"yaml"/"markdown") — kept for
    #: traceability/debugging, not used by any query.
    source_format: Mapped[str | None] = mapped_column(String(20))

    user: Mapped["User"] = relationship(back_populates="candidate_profile")

    def __repr__(self) -> str:
        return f"<CandidateProfileRecord user={self.user_id}>"
