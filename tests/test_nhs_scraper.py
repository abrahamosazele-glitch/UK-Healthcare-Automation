"""
Tests for the NHS Jobs scraper, run entirely against local fixtures
(tests/fixtures/nhs/) — no live jobs.nhs.uk access, per this milestone's
compliance requirement.

Covers: parser field extraction and malformed-card skipping, detail-page
enrichment, pagination (next/last page detection, total page count),
duplicate detection + insert/update persistence, and a full scraper run
end-to-end.
"""

from __future__ import annotations

from datetime import date

from playwright.sync_api import Page
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_automation.core.browser_config import BrowserConfig
from job_automation.core.page_manager import PageManager
from job_automation.core.rate_limiter import RateLimiter
from job_automation.core.retry_manager import RetryManager
from job_automation.core.screenshot_manager import ScreenshotManager
from job_automation.database.models.job import Job
from job_automation.database.services import JobIngestionService
from job_automation.scrapers.base import ParsedJob, ScraperConfig
from job_automation.scrapers.nhs import NHSParser, NHSScraper, build_nhs_search_criteria
from job_automation.scrapers.nhs.nhs_search import NHSPaginator


def _page_manager(config: BrowserConfig) -> PageManager:
    retry_manager = RetryManager(max_retries=config.max_retries)
    screenshot_manager = ScreenshotManager(config)
    rate_limiter = RateLimiter(min_delay_seconds=0.0, max_delay_seconds=0.01)
    return PageManager(config, retry_manager, screenshot_manager, rate_limiter)


# --- Parser -------------------------------------------------------------


def test_nhs_parser_extracts_fields_and_skips_malformed_card(page: Page, nhs_fixture_url: str) -> None:
    page.goto(f"{nhs_fixture_url}/search_page_1.html")
    cards = page.locator(NHSParser.CARD_SELECTOR).all()
    assert len(cards) == 3, "fixture has 3 cards, including 1 deliberately malformed"

    parser = NHSParser()
    jobs = parser.parse_all(cards)

    assert len(jobs) == 2, "the malformed card (missing title) must be skipped, not raised"
    titles = {job.title for job in jobs}
    assert titles == {"Registered Nurse - Adult Ward", "Healthcare Assistant - Nights"}

    nurse = next(job for job in jobs if job.title == "Registered Nurse - Adult Ward")
    assert nurse.employer == "Example NHS Foundation Trust"
    assert nurse.location == "London"
    assert nurse.salary == "£29,970 - £36,483 per annum"
    assert nurse.band is None, "real search-result cards never expose an AfC band"
    assert nurse.contract_type == "Permanent"
    assert nurse.hours == "Full-time, Part-time, Flexible working"
    assert nurse.reference_number == "C9123-26-0001"
    assert nurse.job_url == "/candidate/jobadvert/C9123-26-0001"
    assert nurse.closing_date == date(2026, 8, 15)
    assert nurse.posted_date == date(2026, 7, 1)

    hca = next(job for job in jobs if job.title == "Healthcare Assistant - Nights")
    assert hca.band is None
    assert hca.contract_type == "Fixed term: 12 months"


def test_nhs_parser_detail_enriches_parsed_job(page: Page, nhs_fixture_url: str) -> None:
    page.goto(f"{nhs_fixture_url}/job_detail.html")
    parser = NHSParser()
    job = ParsedJob(title="Registered Nurse - Adult Ward", job_url="job_detail.html")

    parser.parse_detail(page, job)

    assert job.description and "compassionate" in job.description
    assert "NMC registration (where applicable to role)" in job.requirements
    assert "NHS Pension Scheme" in job.benefits
    assert job.employer_url == "https://www.example-trust.nhs.uk"


# --- Pagination -----------------------------------------------------------


def test_nhs_paginator_detects_next_and_last_page_and_page_count(page: Page, nhs_fixture_url: str) -> None:
    page_manager = _page_manager(BrowserConfig())
    paginator = NHSPaginator(page_manager, max_pages=10)

    page.goto(f"{nhs_fixture_url}/search_page_1.html")
    assert paginator.has_next_page(page) is True
    assert paginator.total_pages(page) == 2

    advanced = paginator.next(page)
    assert advanced is True
    assert paginator.current_page == 2
    assert page.url.endswith("search_page_2.html")

    assert paginator.has_next_page(page) is False, "page 2 is the true last page"
    assert paginator.next(page) is False, "next() must not advance past the last page"
    assert paginator.current_page == 2


def test_nhs_paginator_respects_max_pages_safety_cap(page: Page, nhs_fixture_url: str) -> None:
    page_manager = _page_manager(BrowserConfig())
    paginator = NHSPaginator(page_manager, max_pages=1)

    page.goto(f"{nhs_fixture_url}/search_page_1.html")
    assert paginator.is_last_page(page) is True, "max_pages=1 must stop pagination on page 1"
    assert paginator.next(page) is False


# --- Duplicate detection / persistence ------------------------------------


def test_job_ingestion_service_inserts_then_updates_without_duplicating(db_session: Session) -> None:
    service = JobIngestionService(db_session, source_site="nhs_jobs")
    parsed = ParsedJob(
        title="Registered Nurse - Adult Ward",
        employer="Example NHS Foundation Trust",
        location="London",
        salary="£29,970 - £36,483 per annum",
        band="Band 5",
        job_url="https://www.jobs.nhs.uk/candidate/jobadvert/C9123-26-0001",
        reference_number="C9123-26-0001",
        requirements=["NMC registration"],
        benefits=["NHS Pension Scheme"],
    )

    result1 = service.save_parsed_job(parsed)
    db_session.commit()
    assert result1.created is True
    assert result1.job.salary_min == 29970
    assert result1.job.salary_max == 36483
    assert result1.job.salary_period == "per year"
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1

    # Same reference number seen again, with an updated title -> update, not a new row.
    parsed.title = "Registered Nurse - Adult Ward (Updated)"
    result2 = service.save_parsed_job(parsed)
    db_session.commit()
    assert result2.created is False
    assert result2.job.id == result1.job.id
    assert result2.job.title == "Registered Nurse - Adult Ward (Updated)"
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1

    # Duplicate detection also works via URL alone, with no reference number.
    same_job_by_url_only = ParsedJob(
        title="Registered Nurse - Adult Ward (Updated)",
        job_url="https://www.jobs.nhs.uk/candidate/jobadvert/C9123-26-0001",
    )
    result3 = service.save_parsed_job(same_job_by_url_only)
    db_session.commit()
    assert result3.created is False
    assert result3.job.id == result1.job.id
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1


# --- Full scraper run -------------------------------------------------------


def test_nhs_scraper_full_run_persists_jobs_and_reports_statistics(
    db_session: Session, nhs_fixture_url: str
) -> None:
    """Detail enrichment explicitly opted into here (`enrich_details=True`)
    to keep exercising the navigate/parse_detail/go-back mechanics — it's
    off by default in production until parse_detail() is verified against
    the real DOM (see test_nhs_scraper_skips_detail_enrichment_by_default)."""
    config = ScraperConfig(browser=BrowserConfig(headless=True), max_pages=10)
    criteria = build_nhs_search_criteria(keywords=["Registered Nurse"], location="London")

    with NHSScraper(
        config,
        criteria,
        db_session,
        base_url=nhs_fixture_url,
        search_path="/search_page_1.html",
        enrich_details=True,
    ) as scraper:
        jobs = scraper.run()
        db_session.commit()

        assert len(jobs) == 3, "2 valid jobs on page 1 + 1 valid job on page 2"
        assert scraper.stats.pages_visited == 2
        assert scraper.stats.total_pages_reported == 2
        assert scraper.stats.jobs_parsed == 3
        assert scraper.stats.jobs_skipped == 1
        assert scraper.stats.jobs_inserted == 3
        assert scraper.stats.jobs_updated == 0
        assert scraper.stats.jobs_failed == 0

    assert not scraper.is_running

    assert db_session.scalar(select(func.count()).select_from(Job)) == 3

    nurse = db_session.scalars(select(Job).where(Job.external_id == "C9123-26-0001")).first()
    assert nurse is not None
    assert nurse.description is not None
    assert nurse.requirements
    assert nurse.benefits
    assert nurse.employer.name == "Example NHS Foundation Trust"

    # Running the same search again must update the 3 existing jobs, not
    # duplicate them.
    with NHSScraper(
        config,
        criteria,
        db_session,
        base_url=nhs_fixture_url,
        search_path="/search_page_1.html",
        enrich_details=True,
    ) as scraper_again:
        scraper_again.run()
        db_session.commit()

    assert scraper_again.stats.jobs_inserted == 0
    assert scraper_again.stats.jobs_updated == 3
    assert db_session.scalar(select(func.count()).select_from(Job)) == 3


def test_nhs_scraper_skips_detail_enrichment_by_default(db_session: Session, nhs_fixture_url: str) -> None:
    """`enrich_details` defaults to False: jobs must still import
    successfully using only search-results-page fields, with no navigation
    to a job's own advert page at all."""
    config = ScraperConfig(browser=BrowserConfig(headless=True), max_pages=10)
    criteria = build_nhs_search_criteria(keywords=["Registered Nurse"], location="London")

    with NHSScraper(
        config, criteria, db_session, base_url=nhs_fixture_url, search_path="/search_page_1.html"
    ) as scraper:
        assert scraper.enrich_details is False
        jobs = scraper.run()
        db_session.commit()

        assert len(jobs) == 3
        assert scraper.stats.jobs_inserted == 3
        assert scraper.stats.jobs_failed == 0

    nurse = db_session.scalars(select(Job).where(Job.external_id == "C9123-26-0001")).first()
    assert nurse is not None
    assert nurse.title == "Registered Nurse - Adult Ward"
    assert nurse.salary_min == 29970
    assert nurse.description is None
    assert nurse.requirements is None
    assert nurse.benefits is None
