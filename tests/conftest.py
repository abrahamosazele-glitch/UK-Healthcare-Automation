"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_automation.database.base import Base
from job_automation.database import models  # noqa: F401 - registers models on Base.metadata

from tests.fixture_server import serve_fixture_site

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A fresh in-memory SQLite database per test — never touches
    data/jobs.db, and every test starts from an empty schema."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def browser() -> Iterator[Browser]:
    # Deliberately function-scoped, not session-scoped: Playwright's sync API
    # does not support more than one sync_playwright() instance running at
    # once in the same thread. A session-scoped browser left open here would
    # collide with BaseScraper's own self-managed BrowserManager in any test
    # that runs a full scraper (e.g. test_nhs_scraper_full_run_...), which
    # starts and stops its own sync_playwright() instance per test.
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=True)
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    browser_page = browser.new_page()
    yield browser_page
    browser_page.close()


@pytest.fixture(scope="session")
def nhs_fixture_url() -> Iterator[str]:
    """Serves tests/fixtures/nhs/ over local HTTP for the duration of the
    test session — never touches the real jobs.nhs.uk. Requests under
    `/candidate/jobadvert/<reference>` (the real site's job-detail URL
    shape) are rewritten to the one shared `job_detail.html` fixture, so
    search-result hrefs can use the real path structure and exercise the
    scraper's real reference-derivation logic end-to-end."""
    with serve_fixture_site(
        FIXTURES_DIR / "nhs", path_prefix_map={"/candidate/jobadvert/": "/job_detail.html"}
    ) as base_url:
        yield base_url


@pytest.fixture(scope="session")
def trac_fixture_url() -> Iterator[str]:
    """Serves tests/fixtures/trac/ over local HTTP for the duration of the
    test session — never touches a real trac.jobs tenant site."""
    with serve_fixture_site(FIXTURES_DIR / "trac") as base_url:
        yield base_url
