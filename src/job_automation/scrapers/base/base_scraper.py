"""
The composition root for a single scraper: owns browser lifecycle, logging,
retry integration, rate limiting, session loading, error handling, and
cleanup. Every concrete scraper subclasses this and implements `scrape()`.

Deliberately does **not** hold `BaseLogin`/`BaseSearch`/`BaseParser`/
`BasePaginator`/`BaseApplication` instances itself — forcing all five into
this constructor would make it an 8+ parameter god-object, and not every
scraper needs all five (a site with no login wall has no `BaseLogin`; a
scraper not yet doing automated applications has no `BaseApplication`).
Instead, a concrete scraper composes whichever of those it needs in its own
`__init__`, using `self.page_manager`/`self.session_manager` (exposed as
properties here) as their shared dependency. `BaseScraper`'s single
responsibility is the browser session lifecycle around whatever the
subclass's `scrape()` does with it.

Automatically registers every concrete subclass with `ScraperRegistry` via
`__init_subclass__` — see that module for why.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from job_automation.core.browser_exceptions import SessionExpiredError
from job_automation.core.browser_manager import BrowserManager
from job_automation.core.context_manager import ContextManager
from job_automation.core.download_manager import DownloadManager
from job_automation.core.page_manager import PageManager
from job_automation.core.rate_limiter import RateLimiter
from job_automation.core.retry_manager import RetryManager
from job_automation.core.screenshot_manager import ScreenshotManager
from job_automation.core.session_manager import SessionManager
from job_automation.scrapers.base.base_parser import ParsedJob
from job_automation.scrapers.base.scraper_config import ScraperConfig
from job_automation.scrapers.base.scraper_exceptions import ScraperError
from job_automation.scrapers.base.scraper_registry import ScraperRegistry
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page


class BaseScraper(ABC):
    #: Concrete subclasses set this to a unique site key (e.g. "nhs_jobs").
    #: Required for auto-registration — see __init_subclass__ below.
    site_name: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Only concrete (fully-implemented) subclasses with a declared
        # site_name self-register — this correctly excludes BaseScraper
        # itself and any intermediate abstract subclass a site family might
        # introduce (e.g. a shared NHSTrustScraper base with no site_name).
        if cls.site_name and not inspect.isabstract(cls):
            ScraperRegistry.register(cls.site_name, cls)

    def __init__(
        self,
        config: ScraperConfig,
        *,
        browser_manager: BrowserManager | None = None,
        context_manager: ContextManager | None = None,
        page_manager: PageManager | None = None,
        session_manager: SessionManager | None = None,
        retry_manager: RetryManager | None = None,
        rate_limiter: RateLimiter | None = None,
        screenshot_manager: ScreenshotManager | None = None,
        download_manager: DownloadManager | None = None,
    ) -> None:
        if not self.site_name:
            raise ScraperError(f"{type(self).__name__} must define a class-level `site_name`")

        self._config = config
        browser_config = config.browser

        # Every dependency is injectable (for testing/mocking) but defaults
        # to a real implementation built from config, so a typical concrete
        # scraper doesn't need to wire eight objects together itself.
        self._retry_manager = retry_manager or RetryManager(
            max_retries=browser_config.max_retries,
            base_delay_seconds=browser_config.retry_base_delay_seconds,
            max_delay_seconds=browser_config.retry_max_delay_seconds,
        )
        self._rate_limiter = rate_limiter or RateLimiter(
            min_delay_seconds=browser_config.rate_limit_min_delay_seconds,
            max_delay_seconds=browser_config.rate_limit_max_delay_seconds,
        )
        self._screenshot_manager = screenshot_manager or ScreenshotManager(browser_config)
        self._download_manager = download_manager or DownloadManager(browser_config)
        self._context_manager = context_manager or ContextManager(browser_config)
        self._page_manager = page_manager or PageManager(
            browser_config, self._retry_manager, self._screenshot_manager, self._rate_limiter
        )
        self._browser_manager = browser_manager or BrowserManager(browser_config, self._retry_manager)
        self._session_manager = session_manager or SessionManager(browser_config, self._context_manager)

        self._context: "BrowserContext | None" = None
        self._page: "Page | None" = None

    # --- dependency access for subclasses composing Base* components ---

    @property
    def page_manager(self) -> PageManager:
        return self._page_manager

    @property
    def session_manager(self) -> SessionManager:
        return self._session_manager

    @property
    def download_manager(self) -> DownloadManager:
        return self._download_manager

    @property
    def screenshot_manager(self) -> ScreenshotManager:
        return self._screenshot_manager

    @property
    def retry_manager(self) -> RetryManager:
        return self._retry_manager

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    @property
    def config(self) -> ScraperConfig:
        return self._config

    @property
    def page(self) -> "Page":
        if self._page is None:
            raise ScraperError("Scraper page not initialized — call start() or use as a context manager")
        return self._page

    @property
    def context(self) -> "BrowserContext":
        if self._context is None:
            raise ScraperError("Scraper context not initialized — call start() or use as a context manager")
        return self._context

    @property
    def is_running(self) -> bool:
        return self._browser_manager.is_running

    # --- lifecycle ---

    def __enter__(self) -> "BaseScraper":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    def start(self) -> None:
        """Launch the browser, restore a saved session if one is valid, and
        open a page. Session loading is generic here (SessionManager only
        knows time-based expiry); a subclass with a stricter validity check
        should verify it early in `scrape()` via its BaseLogin.session_valid()
        and fall back to logging in again if it returns False."""
        logger.info("Starting scraper '{}'", self.site_name)
        self._browser_manager.start()

        restored_context: "BrowserContext | None" = None
        if self._session_manager.has_valid_session(self.site_name):
            try:
                restored_context = self._session_manager.load_session(
                    self._browser_manager.browser, self.site_name
                )
            except SessionExpiredError:
                restored_context = None

        self._context = restored_context or self._context_manager.create_context(
            self._browser_manager.browser
        )
        self._page = self._page_manager.open_page(self._context)

    def stop(self) -> None:
        """Tear down page, context, and browser, in that order. Never
        raises — each step is independently guarded by the component it
        delegates to (PageManager/ContextManager/BrowserManager already
        swallow and log their own cleanup failures)."""
        logger.info("Stopping scraper '{}'", self.site_name)
        if self._page is not None:
            self._page_manager.close_page(self._page)
            self._page = None
        if self._context is not None:
            self._context_manager.close_context(self._context)
            self._context = None
        self._browser_manager.stop()

    def run(self) -> list[ParsedJob]:
        """Entry point: ensures the browser lifecycle runs around
        `scrape()`, even if this scraper is used outside a `with` block.
        Captures a screenshot and re-raises on failure, so a scraping run
        always leaves a diagnostic trail instead of silently losing context
        on the way up the stack."""
        started_here = not self.is_running
        if started_here:
            self.start()
        try:
            results = self.scrape()
            logger.info("Scraper '{}' finished: {} listings", self.site_name, len(results))
            return results
        except Exception as exc:
            logger.error("Scraper '{}' failed: {}", self.site_name, exc)
            if self._page is not None:
                self._screenshot_manager.capture(self._page, reason=f"{self.site_name}_run_failed")
            raise
        finally:
            if started_here:
                self.stop()

    @abstractmethod
    def scrape(self) -> list[ParsedJob]:
        """Subclasses implement the actual search -> paginate -> parse flow
        here, using `self.page` and whatever BaseLogin/BaseSearch/BaseParser/
        BasePaginator/BaseApplication instances they composed in their own
        __init__."""
