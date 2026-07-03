"""
Owns the Playwright driver process and the Chromium `Browser` instance —
the outermost lifecycle of any browser automation session.

Uses Playwright's *sync* API deliberately: the rest of this project
(SQLAlchemy sessions, scripts) is synchronous, and introducing asyncio here
would force it through every scraper and the scheduler for no benefit at
current scale. If high-concurrency scraping becomes a real requirement later,
an `AsyncBrowserManager` can be added alongside this one without touching
callers that don't need it.

Usable as a context manager:

    with BrowserManager(config) as bm:
        browser = bm.browser
        ...
    # browser and the Playwright driver are both closed on exit, even on error
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.sync_api import sync_playwright

from job_automation.core.browser_config import BrowserConfig
from job_automation.core.browser_exceptions import BrowserAutomationError, BrowserLaunchError
from job_automation.core.retry_manager import RetryManager
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright


class BrowserManager:
    def __init__(self, config: BrowserConfig, retry_manager: RetryManager | None = None) -> None:
        self._config = config
        self._retry_manager = retry_manager or RetryManager(
            max_retries=config.max_retries,
            base_delay_seconds=config.retry_base_delay_seconds,
            max_delay_seconds=config.retry_max_delay_seconds,
        )
        self._playwright: "Playwright | None" = None
        self._browser: "Browser | None" = None

    @property
    def browser(self) -> "Browser":
        if self._browser is None:
            raise BrowserAutomationError("Browser is not running — call start() first")
        return self._browser

    @property
    def is_running(self) -> bool:
        return self._browser is not None

    def __enter__(self) -> "BrowserManager":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    def start(self) -> "Browser":
        """Launch Playwright and Chromium, retrying transient launch
        failures. Idempotent: returns the existing browser if already
        running instead of launching a second one."""
        if self._browser is not None:
            return self._browser

        logger.info("Starting Playwright driver")
        self._playwright = sync_playwright().start()

        def _launch() -> "Browser":
            try:
                return self._playwright.chromium.launch(  # type: ignore[union-attr]
                    headless=self._config.headless,
                    slow_mo=self._config.slow_mo_ms,
                )
            except Exception as exc:
                raise BrowserLaunchError(f"Failed to launch Chromium: {exc}") from exc

        self._browser = self._retry_manager.execute(_launch, operation_name="browser_launch")
        logger.info(
            "Browser launched (headless={}, slow_mo={}ms)",
            self._config.headless,
            self._config.slow_mo_ms,
        )
        return self._browser

    def stop(self) -> None:
        """Close the browser and stop the Playwright driver. Never raises —
        cleanup failures are logged, since a broken shutdown must not mask
        whatever the caller was doing before it."""
        if self._browser is not None:
            try:
                self._browser.close()
                logger.info("Browser closed")
            except Exception as exc:
                logger.warning("Error while closing browser: {}", exc)
            finally:
                self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
                logger.info("Playwright driver stopped")
            except Exception as exc:
                logger.warning("Error while stopping Playwright driver: {}", exc)
            finally:
                self._playwright = None
