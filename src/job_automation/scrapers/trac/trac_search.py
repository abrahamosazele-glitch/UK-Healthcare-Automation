"""
Trac Jobs search execution and pagination — mirrors
`scrapers.nhs.nhs_search` exactly (see that module's docstring for why
`TracPaginator` is colocated here rather than in its own file).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from job_automation.core.page_manager import PageManager
from job_automation.scrapers.base import BasePaginator, BaseSearch, SearchCriteria
from job_automation.scrapers.base.scraper_exceptions import SearchError
from job_automation.scrapers.trac.trac_parser import TracParser
from job_automation.scrapers.trac.trac_urls import SEARCH_RESULTS_PATH, TRAC_BASE_URL, build_search_url
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Page

NEXT_PAGE_SELECTOR = "a.trac-pagination__next"
PREVIOUS_PAGE_SELECTOR = "a.trac-pagination__previous"
PAGE_INFO_SELECTOR = ".trac-pagination__page-info"
_PAGE_INFO_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


def build_trac_search_criteria(
    *,
    keywords: list[str] | None = None,
    location: str | None = None,
    band: str | None = None,
    contract_type: str | None = None,
    working_pattern: str | None = None,
    sort_by: str | None = None,
) -> SearchCriteria:
    """Typed convenience constructor for Trac Jobs search criteria — same
    role as `nhs_search.build_nhs_search_criteria()`."""
    filters: dict[str, str] = {}
    if band:
        filters["band"] = band
    if contract_type:
        filters["contract_type"] = contract_type
    if working_pattern:
        filters["working_pattern"] = working_pattern

    return SearchCriteria(keywords=keywords or [], location=location, filters=filters, sort_by=sort_by)


class TracSearch(BaseSearch):
    def __init__(
        self,
        page_manager: PageManager,
        *,
        base_url: str = TRAC_BASE_URL,
        search_path: str = SEARCH_RESULTS_PATH,
    ) -> None:
        super().__init__(page_manager)
        self.base_url = base_url
        self._search_path = search_path

    def build_search_url(self, criteria: SearchCriteria) -> str:
        return build_search_url(criteria, base_url=self.base_url, search_path=self._search_path)

    def execute_search(self, page: "Page", criteria: SearchCriteria) -> None:
        if not self._page_manager.element_exists(page, TracParser.CARD_SELECTOR, timeout_ms=3000):
            raise SearchError("No Trac Jobs results appeared after navigating to the search URL")


class TracPaginator(BasePaginator):
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
        """Best-effort read of a "Page X of Y" indicator — see
        NHSPaginator.total_pages()'s identical reasoning."""
        if not self._page_manager.element_exists(page, PAGE_INFO_SELECTOR, timeout_ms=1000):
            return None
        text = page.locator(PAGE_INFO_SELECTOR).text_content() or ""
        match = _PAGE_INFO_RE.search(text)
        if not match:
            logger.warning("Could not parse page count from {!r}", text)
            return None
        return int(match.group(2))
