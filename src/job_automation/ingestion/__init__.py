"""
Job Ingestion Service — provider-level orchestration sitting above
`job_automation.scrapers`.

`job_automation.scrapers.base` already provides the low-level machinery for
a single Playwright-driven site (`BaseScraper`/`BaseSearch`/`BaseParser`/
`BasePaginator`) plus generic persistence (`database.services
.JobIngestionService`, dedup included). This package adds one more layer
on top: a `JobProvider` per job board — the unit the rest of the app
(scheduler, dashboard, tests) actually reasons about — some of which wrap
a Playwright scraper (NHS, Trac), some of which don't need one at all
(Reed, a plain JSON API), and two of which are interface-only stubs
(Indeed, TotalJobs) pending a compliant data source.

See docs/JOB_INGESTION.md for the full design.
"""

from job_automation.ingestion.auto_match_service import process_new_jobs
from job_automation.ingestion.ingestion_orchestrator import IngestionRunResult, run_ingestion
from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats
from job_automation.ingestion.provider_registry import PROVIDER_REGISTRY, get_provider

__all__ = [
    "JobProvider",
    "ProviderRunStats",
    "PROVIDER_REGISTRY",
    "get_provider",
    "IngestionRunResult",
    "run_ingestion",
    "process_new_jobs",
]
