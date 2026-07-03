"""
NHS Jobs search execution and pagination.

`NHSPaginator` lives here rather than in its own file — this milestone's
file list is exactly `nhs_urls.py`/`nhs_login.py`/`nhs_search.py`/
`nhs_parser.py`/`nhs_scraper.py`, with no separate paginator module, and
pagination is closely tied to search results (there's nothing to paginate
without a search), so colocating them is a reasonable reading of that list
rather than introducing a 6th file.

Selectors here follow the same fixture-driven approach as the rest of this
package — see nhs_parser.py's module docstring for why.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from job_automation.core.page_manager import PageManager
from job_automation.scrapers.base import BasePaginator, BaseSearch, SearchCriteria
from job_automation.scrapers.base.scraper_exceptions import SearchError
from job_automation.scrapers.nhs.nhs_parser import NHSParser
from job_automation.scrapers.nhs.nhs_urls import NHS_BASE_URL, SEARCH_RESULTS_PATH, build_search_url
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Page

NEXT_PAGE_SELECTOR = "a.search-results__next-page"
PREVIOUS_PAGE_SELECTOR = "a.search-results__previous-page"
PAGE_INFO_SELECTOR = ".search-results__page-info"
_PAGE_INFO_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


def build_nhs_search_criteria(
    *,
    keywords: list[str] | None = None,
    location: str | None = None,
    distance: str | None = None,
    salary_min: str | None = None,
    band: str | None = None,
    contract_type: str | None = None,
    working_pattern: str | None = None,
    visa_sponsorship: bool | None = None,
    sort_by: str | None = None,
) -> SearchCriteria:
    """Typed convenience constructor for NHS Jobs search criteria, so callers
    don't need to know the raw `SearchCriteria.filters` key names that
    `nhs_urls.build_search_url()` expects."""
    filters: dict[str, str] = {}
    if distance:
        filters["distance"] = distance
    if salary_min:
        filters["salary_min"] = salary_min
    if band:
        filters["band"] = band
    if contract_type:
        filters["contract_type"] = contract_type
    if working_pattern:
        filters["working_pattern"] = working_pattern
    if visa_sponsorship is not None:
        filters["visa_sponsorship"] = "true" if visa_sponsorship else "false"

    return SearchCriteria(keywords=keywords or [], location=location, filters=filters, sort_by=sort_by)


class NHSSearch(BaseSearch):
    def __init__(
        self,
        page_manager: PageManager,
        *,
        base_url: str = NHS_BASE_URL,
        search_path: str = SEARCH_RESULTS_PATH,
    ) -> None:
        super().__init__(page_manager)
        self.base_url = base_url
        self._search_path = search_path

    def build_search_url(self, criteria: SearchCriteria) -> str:
        return build_search_url(criteria, base_url=self.base_url, search_path=self._search_path)

    def execute_search(self, page: "Page", criteria: SearchCriteria) -> None:
        if not self._page_manager.element_exists(page, NHSParser.CARD_SELECTOR, timeout_ms=3000):
            raise SearchError("No NHS Jobs results appeared after navigating to the search URL")


class NHSPaginator(BasePaginator):
    def has_next_page(self, page: "Page") -> bool:
        return self._page_manager.element_exists(page, NEXT_PAGE_SELECTOR, timeout_ms=1000)

    def go_to_next_page(self, page: "Page") -> None:
        self._page_manager.safe_click(page, NEXT_PAGE_SELECTOR)
        page.wait_for_load_state("load")

    def has_previous_page(self, page: "Page") -> bool:
        return self._page_manager.element_exists(page, PREVIOUS_PAGE_SELECTOR, timeout_ms=1000)

    def go_to_previous_page(self, page: "Page") -> None:
        self._page_manager.safe_click(page, PREVIOUS_PAGE_SELECTOR)
        page.wait_for_load_state("load")

    def total_pages(self, page: "Page") -> int | None:
        """Best-effort read of a "Page X of Y" indicator, for logging/stats
        ("page count") — returns None if the page doesn't show one."""
        if not self._page_manager.element_exists(page, PAGE_INFO_SELECTOR, timeout_ms=1000):
            return None
        text = page.locator(PAGE_INFO_SELECTOR).text_content() or ""
        match = _PAGE_INFO_RE.search(text)
        if not match:
            logger.warning("Could not parse page count from {!r}", text)
            return None
        return int(match.group(2))
