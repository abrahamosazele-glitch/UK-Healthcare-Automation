"""
`JobProvider` adapter over `scrapers.trac.TracScraper` — same reasoning as
`nhs_provider.py`: `TracScraper.run()` already fetches, normalizes, and
persists (dedup included) in one call, so this adapter only presents Trac
Jobs at the `JobProvider` level, nothing more.

Runs one search per `settings.scrape_locations` entry via
`ingestion.multi_location.run_per_location()` rather than joining every
location into one query string — same reasoning as `nhs_provider.py`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.database.services import JobIngestionService
from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats
from job_automation.ingestion.multi_location import run_per_location
from job_automation.scrapers.base import ScraperConfig
from job_automation.scrapers.trac import TracScraper, build_trac_search_criteria


class TracProvider(JobProvider):
    source_name = "trac_jobs"

    def __init__(self, *, base_url: str | None = None, search_path: str | None = None) -> None:
        # `get_provider("trac_jobs")` constructs this with no arguments, so
        # the registry-driven path (the scheduled task, the manual "Import
        # now" button) only ever sees a real trust's site if one is
        # configured via `settings.trac_jobs_base_url` — see that field's
        # docstring for why there's no single default the way NHS Jobs has.
        self._base_url = base_url if base_url is not None else settings.trac_jobs_base_url
        self._search_path = search_path

    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        config = ScraperConfig.from_settings(settings)
        ingestion = JobIngestionService(session, source_site=self.source_name)

        kwargs = {}
        if self._base_url is not None:
            kwargs["base_url"] = self._base_url
        if self._search_path is not None:
            kwargs["search_path"] = self._search_path

        def _run_one(location: str | None) -> ProviderRunStats:
            criteria = build_trac_search_criteria(keywords=settings.scrape_keywords, location=location)
            with TracScraper(config, criteria, session, ingestion_service=ingestion, **kwargs) as scraper:
                scraper.run()
            return ProviderRunStats(
                source=self.source_name,
                jobs_seen=scraper.stats.jobs_parsed,
                jobs_created=scraper.stats.jobs_inserted,
                jobs_updated=scraper.stats.jobs_updated,
                jobs_failed=scraper.stats.jobs_failed,
            )

        result = run_per_location(self.source_name, settings.scrape_locations, _run_one)
        result.newly_created_job_ids = list(ingestion.created_job_ids)
        return result
