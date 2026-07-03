"""
Reusable searching: keyword search, location search, filter building, and
sorting are all captured in the typed `SearchCriteria` value object so a
scraper always works with the same shape regardless of site. Translating
that into an actual query string or form submission is abstract, since every
job board does it differently (query params vs. a multi-step search form).

Pagination is deliberately *not* handled here — `BaseSearch` gets the first
page of results showing; `BasePaginator` takes over from there. Keeping them
separate avoids one class doing two jobs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from job_automation.core.page_manager import PageManager
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Page


@dataclass(frozen=True)
class SearchCriteria:
    keywords: list[str] = field(default_factory=list)
    location: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    sort_by: str | None = None


class BaseSearch(ABC):
    def __init__(self, page_manager: PageManager) -> None:
        self._page_manager = page_manager

    @abstractmethod
    def build_search_url(self, criteria: SearchCriteria) -> str:
        """Translate `criteria` into a site-specific search URL (e.g. query
        params). If the site requires filling in a form instead of a URL,
        return a sensible base/landing URL and do the form-filling in
        `execute_search()`."""

    @abstractmethod
    def execute_search(self, page: "Page", criteria: SearchCriteria) -> None:
        """Perform whatever site-specific steps (form filling, clicking a
        search button, waiting for results) are needed so matching results
        are visible on `page` by the time this returns."""

    def search(self, page: "Page", criteria: SearchCriteria) -> None:
        """Template method: navigate to the search URL, then run the
        site-specific execution step."""
        url = self.build_search_url(criteria)
        self._page_manager.navigate(page, url)
        self.execute_search(page, criteria)
        logger.info(
            "Search executed: keywords={} location={} filters={} sort_by={}",
            criteria.keywords,
            criteria.location,
            criteria.filters,
            criteria.sort_by,
        )
