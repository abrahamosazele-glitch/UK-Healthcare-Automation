"""
NHS Jobs candidate login.

**Not exercised by this milestone's tests** — the required fixtures for
this milestone are search-results and job-detail pages only (browsing NHS
Jobs doesn't require an account), so there's no login fixture to verify
this against. The selectors below are a best-effort based on typical
GOV.UK Design System login form conventions and are **unverified against
the live DOM** — confirm and adjust before this is ever used for real
(e.g. to save searches or check application status), see
docs/NHS_SCRAPER.md's "Known limitations".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_automation.core.session_manager import SessionManager
from job_automation.core.page_manager import PageManager
from job_automation.scrapers.base import BaseLogin
from job_automation.scrapers.base.scraper_exceptions import LoginError
from job_automation.scrapers.nhs.nhs_urls import NHS_BASE_URL, login_url

if TYPE_CHECKING:
    from playwright.sync_api import Page


class NHSLogin(BaseLogin):
    def __init__(
        self,
        site_name: str,
        page_manager: PageManager,
        session_manager: SessionManager,
        *,
        base_url: str = NHS_BASE_URL,
    ) -> None:
        super().__init__(site_name, page_manager, session_manager)
        self._base_url = base_url

    def login(self, page: "Page", *, username: str, password: str) -> None:
        self._page_manager.navigate(page, login_url(base_url=self._base_url))
        if not self._page_manager.safe_type(page, "#username", username):
            raise LoginError("Could not fill in the NHS Jobs username/email field")
        if not self._page_manager.safe_type(page, "#password", password):
            raise LoginError("Could not fill in the NHS Jobs password field")
        if not self._page_manager.safe_click(page, "button[type='submit']"):
            raise LoginError("Could not submit the NHS Jobs login form")
        if not self.session_valid(page):
            raise LoginError("NHS Jobs login form submitted but session does not look authenticated")

    def logout(self, page: "Page") -> None:
        self._page_manager.safe_click(page, "a[href*='logout']")

    def session_valid(self, page: "Page") -> bool:
        # Best-effort: an authenticated NHS Jobs candidate account typically
        # shows an account menu / "Sign out" link instead of "Sign in".
        return self._page_manager.element_exists(page, "a[href*='logout']", timeout_ms=2000)
