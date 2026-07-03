"""
Tests for the Trac Jobs scraper, run entirely against local fixtures
(tests/fixtures/trac/) — no live trac.jobs access. Mirrors
test_nhs_scraper.py's structure exactly; see that file's module docstring
for what's covered (parser field extraction/malformed-card skipping,
detail-page enrichment, pagination, duplicate detection, full scraper run).
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
from job_automation.scrapers.trac import TracParser, TracScraper, build_trac_search_criteria
from job_automation.scrapers.trac.trac_search import TracPaginator


def _page_manager(config: BrowserConfig) -> PageManager:
    retry_manager = RetryManager(max_retries=config.max_retries)
    screenshot_manager = ScreenshotManager(config)
    rate_limiter = RateLimiter(min_delay_seconds=0.0, max_delay_seconds=0.01)
    return PageManager(config, retry_manager, screenshot_manager, rate_limiter)


# --- Parser -------------------------------------------------------------


def test_trac_parser_extracts_fields_and_skips_malformed_card(page: Page, trac_fixture_url: str) -> None:
    page.goto(f"{trac_fixture_url}/search_page_1.html")
    cards = page.locator(TracParser.CARD_SELECTOR).all()
    assert len(cards) == 3, "fixture has 3 cards, including 1 deliberately malformed"

    parser = TracParser()
    jobs = parser.parse_all(cards)

    assert len(jobs) == 2, "the malformed card (missing title) must be skipped, not raised"
    titles = {job.title for job in jobs}
    assert titles == {"Staff Nurse - Emergency Department", "Domiciliary Care Assistant"}

    nurse = next(job for job in jobs if job.title == "Staff Nurse - Emergency Department")
    assert nurse.employer == "Example Foundation Trust"
    assert nurse.location == "Leeds"
    assert nurse.salary == "£31,049 - £37,796 per annum"
    assert nurse.band == "Band 5"
    assert nurse.contract_type == "Permanent"
    assert nurse.hours == "Full-time"
    assert nurse.reference_number == "TRAC-0001"
    assert nurse.job_url == "vacancy_detail.html?ref=TRAC-0001"
    assert nurse.closing_date == date(2026, 8, 12)
    assert nurse.posted_date == date(2026, 7, 1)

    carer = next(job for job in jobs if job.title == "Domiciliary Care Assistant")
    assert carer.band == "Band 3"
    assert carer.contract_type == "Fixed term: 12 months"


def test_trac_parser_detail_enriches_parsed_job(page: Page, trac_fixture_url: str) -> None:
    page.goto(f"{trac_fixture_url}/vacancy_detail.html")
    parser = TracParser()
    job = ParsedJob(title="Staff Nurse - Emergency Department", job_url="vacancy_detail.html")

    parser.parse_detail(page, job)

    assert job.description and "compassionate" in job.description
    assert "NMC registration (where applicable to role)" in job.requirements
    assert "NHS Pension Scheme" in job.benefits
    assert job.employer_url == "https://www.example-foundation-trust.nhs.uk"
    assert job.visa_sponsorship is True


# --- Pagination -----------------------------------------------------------


def test_trac_paginator_detects_next_and_last_page_and_page_count(page: Page, trac_fixture_url: str) -> None:
    page_manager = _page_manager(BrowserConfig())
    paginator = TracPaginator(page_manager, max_pages=10)

    page.goto(f"{trac_fixture_url}/search_page_1.html")
    assert paginator.has_next_page(page) is True
    assert paginator.total_pages(page) == 2

    advanced = paginator.next(page)
    assert advanced is True
    assert paginator.current_page == 2
    assert page.url.endswith("search_page_2.html")

    assert paginator.has_next_page(page) is False, "page 2 is the true last page"
    assert paginator.next(page) is False, "next() must not advance past the last page"
    assert paginator.current_page == 2


def test_trac_paginator_respects_max_pages_safety_cap(page: Page, trac_fixture_url: str) -> None:
    page_manager = _page_manager(BrowserConfig())
    paginator = TracPaginator(page_manager, max_pages=1)

    page.goto(f"{trac_fixture_url}/search_page_1.html")
    assert paginator.is_last_page(page) is True, "max_pages=1 must stop pagination on page 1"
    assert paginator.next(page) is False


# --- Duplicate detection / persistence ------------------------------------


def test_job_ingestion_service_inserts_then_updates_trac_job_without_duplicating(db_session: Session) -> None:
    service = JobIngestionService(db_session, source_site="trac_jobs")
    parsed = ParsedJob(
        title="Staff Nurse - Emergency Department",
        employer="Example Foundation Trust",
        location="Leeds",
        salary="£31,049 - £37,796 per annum",
        band="Band 5",
        job_url="https://example-trust.trac.jobs/vacancy/TRAC-0001",
        reference_number="TRAC-0001",
        requirements=["NMC registration"],
        benefits=["NHS Pension Scheme"],
    )

    result1 = service.save_parsed_job(parsed)
    db_session.commit()
    assert result1.created is True
    assert result1.job.salary_min == 31049
    assert result1.job.salary_max == 37796
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1

    parsed.title = "Staff Nurse - Emergency Department (Updated)"
    result2 = service.save_parsed_job(parsed)
    db_session.commit()
    assert result2.created is False
    assert result2.job.id == result1.job.id
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1


# --- Full scraper run -------------------------------------------------------


def test_trac_scraper_full_run_persists_jobs_and_reports_statistics(
    db_session: Session, trac_fixture_url: str
) -> None:
    config = ScraperConfig(browser=BrowserConfig(headless=True), max_pages=10)
    criteria = build_trac_search_criteria(keywords=["Staff Nurse"], location="Leeds")

    with TracScraper(
        config, criteria, db_session, base_url=trac_fixture_url, search_path="/search_page_1.html"
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

    nurse = db_session.scalars(select(Job).where(Job.external_id == "TRAC-0001")).first()
    assert nurse is not None
    assert nurse.description is not None
    assert nurse.requirements
    assert nurse.benefits
    assert nurse.visa_sponsorship is True
    assert nurse.employer.name == "Example Foundation Trust"

    # Running the same search again must update the 3 existing jobs, not
    # duplicate them.
    with TracScraper(
        config, criteria, db_session, base_url=trac_fixture_url, search_path="/search_page_1.html"
    ) as scraper_again:
        scraper_again.run()
        db_session.commit()

    assert scraper_again.stats.jobs_inserted == 0
    assert scraper_again.stats.jobs_updated == 3
    assert db_session.scalar(select(func.count()).select_from(Job)) == 3
