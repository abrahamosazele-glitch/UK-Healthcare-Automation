"""
NHS Jobs scraper: composes NHSSearch, NHSPaginator, and NHSParser (all built
on job_automation.scrapers.base / job_automation.core) with
JobIngestionService (database layer) to search, paginate, parse, and
persist listings in one run.

Owns its own `ScrapeStats` rather than reusing something from
`scrapers.base` — no other scraper needs identical stats tracking yet; if a
second site scraper needs the same shape later, promote this to
`scrapers/base/` at that point rather than speculatively generalizing now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from job_automation.database.services import JobIngestionService
from job_automation.scrapers.base import BaseScraper, ParsedJob, ScraperConfig, SearchCriteria
from job_automation.scrapers.nhs.nhs_parser import NHSParser
from job_automation.scrapers.nhs.nhs_search import NHSPaginator, NHSSearch
from job_automation.scrapers.nhs.nhs_urls import NHS_BASE_URL, SEARCH_RESULTS_PATH, resolve_url
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from job_automation.core.browser_manager import BrowserManager
    from job_automation.core.context_manager import ContextManager
    from job_automation.core.download_manager import DownloadManager
    from job_automation.core.page_manager import PageManager
    from job_automation.core.rate_limiter import RateLimiter
    from job_automation.core.retry_manager import RetryManager
    from job_automation.core.screenshot_manager import ScreenshotManager
    from job_automation.core.session_manager import SessionManager


@dataclass
class ScrapeStats:
    pages_visited: int = 0
    total_pages_reported: int | None = None
    jobs_parsed: int = 0
    jobs_skipped: int = 0
    jobs_inserted: int = 0
    jobs_updated: int = 0
    jobs_failed: int = 0


class NHSScraper(BaseScraper):
    site_name = "nhs_jobs"

    def __init__(
        self,
        config: ScraperConfig,
        criteria: SearchCriteria,
        session: Session,
        *,
        base_url: str = NHS_BASE_URL,
        search_path: str = SEARCH_RESULTS_PATH,
        ingestion_service: JobIngestionService | None = None,
        browser_manager: "BrowserManager | None" = None,
        context_manager: "ContextManager | None" = None,
        page_manager: "PageManager | None" = None,
        session_manager: "SessionManager | None" = None,
        retry_manager: "RetryManager | None" = None,
        rate_limiter: "RateLimiter | None" = None,
        screenshot_manager: "ScreenshotManager | None" = None,
        download_manager: "DownloadManager | None" = None,
    ) -> None:
        super().__init__(
            config,
            browser_manager=browser_manager,
            context_manager=context_manager,
            page_manager=page_manager,
            session_manager=session_manager,
            retry_manager=retry_manager,
            rate_limiter=rate_limiter,
            screenshot_manager=screenshot_manager,
            download_manager=download_manager,
        )
        self._criteria = criteria
        self.search_flow = NHSSearch(self.page_manager, base_url=base_url, search_path=search_path)
        self.paginator = NHSPaginator(self.page_manager, max_pages=self.config.max_pages)
        self.parser = NHSParser()
        self.ingestion = ingestion_service or JobIngestionService(session, source_site=self.site_name)
        self.stats = ScrapeStats()

    def scrape(self) -> list[ParsedJob]:
        logger.info(
            "NHS Jobs search started: keywords={} location={}",
            self._criteria.keywords,
            self._criteria.location,
        )
        self.search_flow.search(self.page, self._criteria)

        all_jobs: list[ParsedJob] = []
        while True:
            self.stats.pages_visited += 1
            total_pages = self.paginator.total_pages(self.page)
            if total_pages is not None:
                self.stats.total_pages_reported = total_pages
            logger.info(
                "Visiting NHS Jobs results page {}{}",
                self.paginator.current_page,
                f" of {total_pages}" if total_pages else "",
            )

            cards = self.page.locator(NHSParser.CARD_SELECTOR).all()
            page_jobs = self.parser.parse_all(cards)
            self.stats.jobs_skipped += len(cards) - len(page_jobs)
            self.stats.jobs_parsed += len(page_jobs)

            for job in page_jobs:
                self._enrich_and_persist(job)
            all_jobs.extend(page_jobs)

            if not self.paginator.next(self.page):
                break

        logger.info(
            "NHS Jobs search completed: pages_visited={} jobs_parsed={} jobs_inserted={} "
            "jobs_updated={} jobs_skipped={} jobs_failed={}",
            self.stats.pages_visited,
            self.stats.jobs_parsed,
            self.stats.jobs_inserted,
            self.stats.jobs_updated,
            self.stats.jobs_skipped,
            self.stats.jobs_failed,
        )
        return all_jobs

    def _enrich_and_persist(self, job: ParsedJob) -> None:
        """Visit the job's own advert page for detail fields, persist it,
        and always navigate back to the results listing afterward —
        regardless of whether this job succeeded or failed — so the next
        card/page continues from a known-good state."""
        navigated_to_detail = False
        try:
            if job.job_url:
                self.page_manager.navigate(self.page, resolve_url(job.job_url, current_url=self.page.url))
                navigated_to_detail = True
                self.parser.parse_detail(self.page, job)

            result = self.ingestion.save_parsed_job(job)
            if result.created:
                self.stats.jobs_inserted += 1
                logger.info("Inserted: {}", job.title)
            else:
                self.stats.jobs_updated += 1
                logger.info("Updated: {}", job.title)
        except Exception as exc:
            self.stats.jobs_failed += 1
            logger.error("Failed to process job {!r}: {}", job.title, exc)
            self.screenshot_manager.capture(self.page, reason="nhs_job_processing_failed")
        finally:
            if navigated_to_detail:
                try:
                    self.page.go_back(wait_until="load")
                except Exception as exc:
                    logger.warning("Failed to return to NHS Jobs results listing: {}", exc)
