"""
Verification script for the job_automation.scrapers.base scraper framework.

Runs DummyScraper (tests/dummy_scraper.py) against a local static fixture
site (tests/fixtures/dummy_site/, served over HTTP by
tests/fixture_server.py) — no real job website is touched. Checks:

  1. registration       — DummyScraper auto-registered itself on import;
                           manual register()/unregister()/get()/list() work.
  2. browser launch      — BaseScraper.start() actually launches a browser.
  3. search flow         — DummySearch navigates and confirms results loaded.
  4. parser               — every ParsedJob field is correctly extracted,
                           and a deliberately malformed card is skipped
                           rather than aborting the page.
  5. pagination           — DummyPaginator walks all 3 pages and stops at
                           the true last page; a second run with
                           max_pages=1 proves the safety cap works
                           independently of "last page" detection.
  6. cleanup              — BaseScraper reports not-running after __exit__.

Also exercises BaseLogin and BaseApplication (not in the required checklist,
but included since DummyScraper composes all five Base* classes and none of
them should be a placeholder).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# tests/ isn't part of the installed job_automation package (deliberately —
# DummyScraper is demo/verification code, not production scraper code), so
# it needs the project root on sys.path to be importable here.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_automation.config.logging_config import setup_logging
from job_automation.config.settings import settings
from job_automation.core.browser_config import BrowserConfig
from job_automation.scrapers.base import (
    ApplicationAnswer,
    ScraperConfig,
    ScraperRegistry,
)
from job_automation.scrapers.base.scraper_exceptions import ScraperNotFoundError
from job_automation.utils.logger import logger
from tests.dummy_scraper import DummyScraper
from tests.fixture_server import serve_fixture_site

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "dummy_site"
DUMMY_CV = FIXTURE_DIR / "dummy_cv.txt"
DUMMY_COVER_LETTER = FIXTURE_DIR / "dummy_cover_letter.txt"


def check_registration() -> None:
    logger.info("--- Checking registration ---")
    assert "dummy" in ScraperRegistry.list(), "DummyScraper should auto-register on import"
    assert ScraperRegistry.get("dummy") is DummyScraper

    ScraperRegistry.register("dummy_alias", DummyScraper)
    assert "dummy_alias" in ScraperRegistry.list()
    ScraperRegistry.unregister("dummy_alias")
    assert "dummy_alias" not in ScraperRegistry.list()

    try:
        ScraperRegistry.get("does_not_exist")
        raise AssertionError("Expected ScraperNotFoundError")
    except ScraperNotFoundError:
        pass
    logger.info("Registration checks passed")


def check_full_scrape(scraper_config: ScraperConfig, base_url: str) -> None:
    logger.info("--- Checking browser launch, search, parser, pagination, login, application ---")
    with DummyScraper(scraper_config, base_url=base_url) as scraper:
        assert scraper.is_running, "Scraper should report running after start()"

        results = scraper.run()

        # 2 (page1) + 2 valid of 3 (page2, one malformed skipped) + 1 (page3) = 5
        assert len(results) == 5, f"Expected 5 valid parsed jobs, got {len(results)}"
        assert scraper.paginator.current_page == 3, (
            f"Expected pagination to reach page 3 (true last page), got {scraper.paginator.current_page}"
        )

        first = results[0]
        assert first.title == "Healthcare Assistant"
        assert first.employer == "Example NHS Trust"
        assert first.band == "Band 2"
        assert first.location == "London"
        assert first.visa_sponsorship is False
        assert first.closing_date is not None and first.closing_date.isoformat() == "2026-08-01"
        assert first.reference_number == "REF-001"
        assert first.job_url == "job-ref-001.html"
        assert "NVQ Level 2 in Health and Social Care" in first.requirements

        second = results[1]
        assert second.visa_sponsorship is True, "REF-002 fixture specifies visa sponsorship = Yes"

        titles = [job.title for job in results]
        assert "Broken Listing Ltd" not in titles, "Malformed card (missing title) must be skipped, not parsed"
        logger.info("Parser + pagination checks passed ({} jobs across 3 pages)", len(results))

        # BaseLogin, exercised even though not in the required checklist.
        scraper.login_flow.login(scraper.page, username="demo_user", password="demo_pass")
        assert scraper.login_flow.session_valid(scraper.page)
        logger.info("Login checks passed")

        # BaseApplication, likewise exercised for completeness.
        scraper.page_manager.navigate(scraper.page, f"{base_url}/apply.html")
        confirmed = scraper.application_flow.apply(
            scraper.page,
            cv_path=DUMMY_CV,
            cover_letter_path=DUMMY_COVER_LETTER,
            answers=[ApplicationAnswer(question="Why do you want this role?", answer="Because I care.")],
        )
        assert confirmed, "Application should be confirmed after submission"
        logger.info("Application checks passed")

    assert not scraper.is_running, "Scraper should report not-running after __exit__ (cleanup)"
    logger.info("Cleanup check passed")


def check_max_pages_safety_cap(browser_config: BrowserConfig, base_url: str) -> None:
    logger.info("--- Checking max_pages safety cap (independent of natural last-page detection) ---")
    capped_config = ScraperConfig(browser=browser_config, max_pages=1)
    with DummyScraper(capped_config, base_url=base_url) as scraper:
        results = scraper.run()
        assert scraper.paginator.current_page == 1, "max_pages=1 should stop pagination after the first page"
        assert len(results) == 2, f"Expected only page1's 2 jobs, got {len(results)}"
    logger.info("max_pages safety cap check passed")


def main() -> int:
    setup_logging()
    browser_config = BrowserConfig.from_settings(settings)
    scraper_config = ScraperConfig.from_settings(settings, browser_config)

    logger.info("=== Scraper framework verification starting ===")

    check_registration()

    with serve_fixture_site(FIXTURE_DIR) as base_url:
        logger.info("Dummy site serving at {}", base_url)
        check_full_scrape(scraper_config, base_url)
        check_max_pages_safety_cap(browser_config, base_url)

    logger.info("=== Scraper framework verification PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
