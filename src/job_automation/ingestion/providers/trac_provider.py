"""
`JobProvider` adapter over `scrapers.trac.TracScraper` — same reasoning as
`nhs_provider.py`: `TracScraper.run()` already fetches, normalizes, and
persists (dedup included) in one call, so this adapter only presents Trac
Jobs at the `JobProvider` level, nothing more.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.database.services import JobIngestionService
from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats
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
        criteria = build_trac_search_criteria(
            keywords=settings.scrape_keywords, location=", ".join(settings.scrape_locations) or None
        )
        ingestion = JobIngestionService(session, source_site=self.source_name)

        kwargs = {}
        if self._base_url is not None:
            kwargs["base_url"] = self._base_url
        if self._search_path is not None:
            kwargs["search_path"] = self._search_path

        with TracScraper(config, criteria, session, ingestion_service=ingestion, **kwargs) as scraper:
            scraper.run()

        return ProviderRunStats(
            source=self.source_name,
            jobs_seen=scraper.stats.jobs_parsed,
            jobs_created=scraper.stats.jobs_inserted,
            jobs_updated=scraper.stats.jobs_updated,
            jobs_failed=scraper.stats.jobs_failed,
            newly_created_job_ids=list(ingestion.created_job_ids),
        )
