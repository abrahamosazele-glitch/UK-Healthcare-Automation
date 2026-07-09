"""
`JobProvider` adapter over the existing `scrapers.nhs.NHSScraper` — this
provider does no fetching/normalizing/persisting itself; `NHSScraper.run()`
already does all three (search → paginate → parse → enrich → persist via
`JobIngestionService`, dedup included), built for the NHS Jobs scraper
milestone. Adding this adapter is purely about presenting NHS Jobs at the
`JobProvider` level the rest of the ingestion package (the orchestrator,
the scheduled task, auto-matching) reasons about, without duplicating
anything `NHSScraper` already does correctly.

`source_name = "nhs_jobs"` matches `NHSScraper.site_name` exactly — both
identify the same `Job.source_site` value.

Runs one search per `settings.scrape_locations` entry via
`ingestion.multi_location.run_per_location()` rather than joining every
location into one query string — NHS Jobs' `location` filter narrows to
one place, it isn't a list. A single `JobIngestionService` (and its
`created_job_ids` tracking) is shared across every location's run so
dedup/stats stay correct across the whole provider run, not just within
one location.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.database.services import JobIngestionService
from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats
from job_automation.ingestion.multi_location import run_per_location
from job_automation.scrapers.base import ScraperConfig
from job_automation.scrapers.nhs import NHSScraper, build_nhs_search_criteria


class NHSProvider(JobProvider):
    source_name = "nhs_jobs"

    def __init__(self, *, base_url: str | None = None, search_path: str | None = None) -> None:
        # Optional overrides so tests can point this provider at a local
        # fixture server instead of the real jobs.nhs.uk host — the exact
        # same mechanism `NHSScraper`'s own constructor already exposes.
        self._base_url = base_url
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
            criteria = build_nhs_search_criteria(keywords=settings.scrape_keywords, location=location)
            with NHSScraper(config, criteria, session, ingestion_service=ingestion, **kwargs) as scraper:
                scraper.run()
            return ProviderRunStats(
                source=self.source_name,
                jobs_seen=scraper.stats.jobs_parsed,
                jobs_created=scraper.stats.jobs_inserted,
                jobs_updated=scraper.stats.jobs_updated,
                jobs_failed=scraper.stats.jobs_failed,
            )

        result = run_per_location(self.source_name, settings.scrape_locations, _run_one)
        # `ingestion` is shared across every location's run, so its
        # `created_job_ids` already reflects the whole provider run —
        # simpler and more accurate than merging per-location lists that
        # `_run_one()` never populates.
        result.newly_created_job_ids = list(ingestion.created_job_ids)
        return result
