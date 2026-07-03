"""
The component every scraper will interact with the most: opening/closing
pages, navigating, and "safe" element interactions that degrade to a logged
warning + False/None instead of raising, since scraping loops should survive
one bad selector rather than crash the whole run.

Composes RetryManager (retries transient navigation failures), ScreenshotManager
(auto-captures on navigation failure, click/type failure, and unexpected
dialogs), and an optional RateLimiter (paced navigation, human-like timing).
None of these are reimplemented here — this class only orchestrates them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from job_automation.core.browser_config import BrowserConfig
from job_automation.core.browser_exceptions import PageNavigationError, RetryExhaustedError
from job_automation.core.rate_limiter import RateLimiter
from job_automation.core.retry_manager import RetryManager
from job_automation.core.screenshot_manager import ScreenshotManager
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Dialog, Locator, Page


class PageManager:
    def __init__(
        self,
        config: BrowserConfig,
        retry_manager: RetryManager,
        screenshot_manager: ScreenshotManager,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._retry_manager = retry_manager
        self._screenshot_manager = screenshot_manager
        self._rate_limiter = rate_limiter

    def open_page(self, context: "BrowserContext") -> "Page":
        page = context.new_page()
        page.set_default_navigation_timeout(self._config.navigation_timeout_ms)
        page.set_default_timeout(self._config.action_timeout_ms)
        page.on("dialog", self._handle_unexpected_dialog)
        logger.info("New page opened")
        return page

    def close_page(self, page: "Page") -> None:
        try:
            page.close()
            logger.info("Page closed")
        except Exception as exc:
            logger.warning("Error while closing page: {}", exc)

    def navigate(self, page: "Page", url: str, *, wait_until: str = "load") -> None:
        """Navigate with rate limiting and retry-on-transient-failure. Raises
        PageNavigationError (via RetryExhaustedError) if every attempt fails,
        after capturing a screenshot of the page's last state."""
        if self._rate_limiter is not None:
            self._rate_limiter.wait()

        def _go() -> None:
            try:
                page.goto(url, wait_until=wait_until)
            except PlaywrightTimeoutError as exc:
                raise PageNavigationError(f"Timed out navigating to {url}: {exc}") from exc
            except Exception as exc:
                raise PageNavigationError(f"Failed to navigate to {url}: {exc}") from exc

        try:
            self._retry_manager.execute(_go, operation_name=f"navigate:{url}")
        except RetryExhaustedError:
            self._screenshot_manager.capture(page, reason="navigation_failed")
            raise

        logger.info("Navigated to {}", url)

    def safe_click(self, page: "Page", selector: str, *, timeout: int | None = None) -> bool:
        try:
            page.click(selector, timeout=timeout or self._config.action_timeout_ms)
            logger.debug("Clicked '{}'", selector)
            return True
        except Exception as exc:
            logger.warning("Click failed for '{}': {}", selector, exc)
            self._screenshot_manager.capture(page, reason="click_failed")
            return False

    def safe_type(
        self, page: "Page", selector: str, text: str, *, delay_ms: int = 50, clear_first: bool = True
    ) -> bool:
        try:
            if clear_first:
                page.fill(selector, "")
            page.type(selector, text, delay=delay_ms)
            logger.debug("Typed into '{}'", selector)
            return True
        except Exception as exc:
            logger.warning("Typing failed for '{}': {}", selector, exc)
            self._screenshot_manager.capture(page, reason="type_failed")
            return False

    def set_input_files(self, page: "Page", selector: str, files: "Path | list[Path]") -> bool:
        try:
            paths = [str(files)] if isinstance(files, Path) else [str(f) for f in files]
            page.set_input_files(selector, paths)
            logger.debug("Set input files for '{}': {}", selector, paths)
            return True
        except Exception as exc:
            logger.warning("Setting input files failed for '{}': {}", selector, exc)
            self._screenshot_manager.capture(page, reason="upload_failed")
            return False

    def safe_scroll(
        self, page: "Page", selector: str | None = None, *, to_bottom: bool = False
    ) -> bool:
        try:
            if to_bottom:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif selector is not None:
                page.locator(selector).scroll_into_view_if_needed()
            else:
                raise ValueError("Either selector or to_bottom=True must be given")
            logger.debug("Scrolled ({})", selector if selector else "to bottom")
            return True
        except Exception as exc:
            logger.warning("Scroll failed: {}", exc)
            return False

    def element_exists(self, page: "Page", selector: str, *, timeout_ms: int = 3000) -> bool:
        try:
            page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
            return True
        except PlaywrightTimeoutError:
            return False

    def wait_for_selector(
        self,
        page: "Page",
        selector: str,
        *,
        state: str = "visible",
        timeout_ms: int | None = None,
    ) -> "Locator | None":
        try:
            page.wait_for_selector(
                selector, state=state, timeout=timeout_ms or self._config.action_timeout_ms
            )
            return page.locator(selector)
        except PlaywrightTimeoutError:
            logger.warning("Timed out waiting for selector '{}' (state={})", selector, state)
            return None

    def _handle_unexpected_dialog(self, dialog: "Dialog") -> None:
        """Auto-capture and dismiss dialogs (alert/confirm/prompt/beforeunload)
        that no calling code was expecting to handle — otherwise Playwright
        blocks indefinitely waiting for a response."""
        logger.warning("Unexpected dialog appeared: type={} message={!r}", dialog.type, dialog.message)
        try:
            self._screenshot_manager.capture(dialog.page, reason="unexpected_dialog")
        except Exception as exc:
            logger.warning("Failed to screenshot unexpected dialog: {}", exc)
        dialog.dismiss()
