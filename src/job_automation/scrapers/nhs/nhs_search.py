"""
NHS Jobs search execution and pagination.

`NHSPaginator` lives here rather than in its own file — this milestone's
file list is exactly `nhs_urls.py`/`nhs_login.py`/`nhs_search.py`/
`nhs_parser.py`/`nhs_scraper.py`, with no separate paginator module, and
pagination is closely tied to search results (there's nothing to paginate
without a search), so colocating them is a reasonable reading of that list
rather than introducing a 6th file.

Selectors here are verified against the real `nhsuk-pagination` markup —
see nhs_parser.py's module docstring for how this was confirmed.
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

NEXT_PAGE_SELECTOR = 'a[data-test="search-next-page"]'
#: No `data-test` value for "previous" was directly observable (only page 1
#: of the real site was captured, where "previous" is empty), so this
#: targets the pagination container's "previous" list item instead of
#: guessing a data-test string — directly confirmed empty/absent on page 1,
#: and expected to hold an `<a>` on later pages by symmetry with "next".
PREVIOUS_PAGE_SELECTOR = "li.nhsuk-pagination-item--previous a"
#: Lives inside both the "previous" and "next" links
#: (`<span class="nhsuk-pagination__page">Page X of Y</span>`) whenever
#: they're present — confirmed against the live site that a middle page
#: (both links visible) has *two* matching spans, not one, so `total_pages()`
#: must never assume a single match (see its own docstring for why `.first`
#: is safe here: both spans report the same page count).
PAGE_INFO_SELECTOR = "span.nhsuk-pagination__page"
_PAGE_INFO_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


def build_nhs_search_criteria(
    *,
    keywords: list[str] | None = None,
    location: str | None = None,
    distance: str | None = None,
    salary_from: str | None = None,
    salary_to: str | None = None,
    pay_band: str | None = None,
    contract_type: str | None = None,
    working_pattern: str | None = None,
    sort_by: str | None = None,
) -> SearchCriteria:
    """Typed convenience constructor for NHS Jobs search criteria, so callers
    don't need to know the raw `SearchCriteria.filters` key names that
    `nhs_urls.build_search_url()` expects."""
    filters: dict[str, str] = {}
    if distance:
        filters["distance"] = distance
    if salary_from:
        filters["salary_from"] = salary_from
    if salary_to:
        filters["salary_to"] = salary_to
    if pay_band:
        filters["pay_band"] = pay_band
    if contract_type:
        filters["contract_type"] = contract_type
    if working_pattern:
        filters["working_pattern"] = working_pattern

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
        ("page count") only — never allowed to abort scraping. Returns
        None if the page doesn't show one, if more than one is present in
        a way that still can't be read, or if anything else about reading
        it fails.

        `PAGE_INFO_SELECTOR` legitimately matches *two* elements on any
        page where both "previous" and "next" links are visible (each
        carries its own copy of the same "Page X of Y" text) — `.first`
        reads whichever appears first in the DOM; since both report the
        same total, which one is read doesn't matter. Without `.first`,
        Playwright's strict mode raises on a multi-match locator, which
        previously propagated out of this method and aborted the whole
        scrape after page 1 — this method must never let that (or any
        other) exception escape, since pagination metadata is for logging
        only."""
        try:
            locator = page.locator(PAGE_INFO_SELECTOR).first
            if locator.count() == 0:
                return None
            text = locator.text_content() or ""
        except Exception as exc:
            logger.warning("Could not read NHS Jobs page count: {}", exc)
            return None

        match = _PAGE_INFO_RE.search(text)
        if not match:
            logger.warning("Could not parse page count from {!r}", text)
            return None
        return int(match.group(2))
