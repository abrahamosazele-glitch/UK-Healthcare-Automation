"""
Repository layer: pure data-access classes, one per aggregate root, wrapping
a SQLAlchemy `Session`. No business logic (deduplication decisions, field
mapping from scraper/AI output) lives here — that's `database.services`
(scraper persistence) or `job_automation.ai.matching_service` (AI matching
persistence). This package didn't exist before the NHS Jobs scraper
milestone; it's grown scoped to exactly what each milestone needed, rather
than speculatively covering every model up front.
"""

from job_automation.database.repositories.employer_repository import EmployerRepository
from job_automation.database.repositories.job_match_repository import JobMatchRepository
from job_automation.database.repositories.job_repository import JobRepository

__all__ = ["EmployerRepository", "JobMatchRepository", "JobRepository"]
