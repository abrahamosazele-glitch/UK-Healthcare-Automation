"""
Execution record for one scraper run against one source site. Standalone by
design (no relationships) — it's an operational log, not domain data tied to
a user or job.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from job_automation.database.base import Base
from job_automation.database.enums import ScraperRunStatus, sa_enum
from job_automation.database.mixins import TimestampMixin, UUIDPKMixin


class ScraperRun(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "scraper_runs"

    source_site: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[ScraperRunStatus] = mapped_column(
        sa_enum(ScraperRunStatus), default=ScraperRunStatus.RUNNING, nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime())
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_jobs_saved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<ScraperRun {self.source_site} status={self.status}>"
