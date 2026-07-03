"""
Service layer: business logic that sits above the repositories, orchestrating
more than one of them and/or mapping data from outside the database layer
(here, a scraper's `ParsedJob`) onto the ORM models. Kept separate from
`repositories/` so those stay pure data-access with no knowledge of scraper
output shapes.
"""

from job_automation.database.services.job_ingestion_service import JobIngestionService, JobSaveResult

__all__ = ["JobIngestionService", "JobSaveResult"]
