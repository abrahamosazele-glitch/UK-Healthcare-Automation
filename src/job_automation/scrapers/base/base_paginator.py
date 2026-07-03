"""
Reusable pagination: next page, previous page, page detection, last page.

`has_next_page`/`go_to_next_page`/`has_previous_page`/`go_to_previous_page`
are abstract — every site's pagination controls differ (a "Next" link, a
page-number list, infinite scroll). `is_last_page()` and the `next()`/
`previous()` template methods are concrete: they combine the site-specific
detection with the generic `max_pages` safety cap from `ScraperConfig`, so a
scraper can never loop forever even if a site's markup changes in a way that
breaks `has_next_page()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from job_automation.core.page_manager import PageManager
from job_automation.scrapers.base.scraper_exceptions import PaginationError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Page


class BasePaginator(ABC):
    def __init__(self, page_manager: PageManager, max_pages: int = 10) -> None:
        self._page_manager = page_manager
        self._max_pages = max_pages
        self._current_page = 1

    @property
    def current_page(self) -> int:
        return self._current_page

    @abstractmethod
    def has_next_page(self, page: "Page") -> bool:
        """Site-specific: does a usable 'next page' control exist?"""

    @abstractmethod
    def go_to_next_page(self, page: "Page") -> None:
        """Site-specific: click/navigate to the next page of results."""

    @abstractmethod
    def has_previous_page(self, page: "Page") -> bool:
        """Site-specific: does a usable 'previous page' control exist?"""

    @abstractmethod
    def go_to_previous_page(self, page: "Page") -> None:
        """Site-specific: click/navigate to the previous page of results."""

    def is_last_page(self, page: "Page") -> bool:
        """True if either the site reports no next page, or the configured
        `max_pages` safety cap has been reached."""
        if self._current_page >= self._max_pages:
            return True
        return not self.has_next_page(page)

    def next(self, page: "Page") -> bool:
        """Advance to the next page. Returns False without doing anything if
        already on the last page (natural or max_pages-capped); raises
        `PaginationError` if the site-specific navigation step itself fails."""
        if self.is_last_page(page):
            logger.info(
                "Pagination stopped at page {} (last page or max_pages={} reached)",
                self._current_page,
                self._max_pages,
            )
            return False
        try:
            self.go_to_next_page(page)
        except Exception as exc:
            raise PaginationError(f"Failed to advance from page {self._current_page}: {exc}") from exc
        self._current_page += 1
        logger.info("Advanced to page {}", self._current_page)
        return True

    def previous(self, page: "Page") -> bool:
        """Go back to the previous page. Returns False if already on page 1."""
        if not self.has_previous_page(page):
            return False
        try:
            self.go_to_previous_page(page)
        except Exception as exc:
            raise PaginationError(f"Failed to go back from page {self._current_page}: {exc}") from exc
        self._current_page = max(1, self._current_page - 1)
        logger.info("Returned to page {}", self._current_page)
        return True

    def reset(self) -> None:
        self._current_page = 1
