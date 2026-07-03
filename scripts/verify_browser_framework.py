"""
Verification script for the job_automation.core browser automation framework.

Wires the components together the way a future scraper would (this is
deliberately not hidden behind a facade class — the framework's public
surface is the 10 components themselves, composed explicitly by the caller):
BrowserManager launches Chromium, ContextManager creates a configured
context, PageManager opens a page and navigates to https://example.com,
ScreenshotManager captures the result, and everything is torn back down in
reverse order. Exits non-zero if any step fails or shutdown isn't clean.
"""

from __future__ import annotations

import sys

from job_automation.config.logging_config import setup_logging
from job_automation.config.settings import settings
from job_automation.core import (
    BrowserConfig,
    BrowserManager,
    ContextManager,
    PageManager,
    RateLimiter,
    RetryManager,
    ScreenshotManager,
)
from job_automation.utils.logger import logger


def main() -> int:
    setup_logging()
    config = BrowserConfig.from_settings(settings)

    retry_manager = RetryManager(
        max_retries=config.max_retries,
        base_delay_seconds=config.retry_base_delay_seconds,
        max_delay_seconds=config.retry_max_delay_seconds,
    )
    rate_limiter = RateLimiter(
        min_delay_seconds=config.rate_limit_min_delay_seconds,
        max_delay_seconds=config.rate_limit_max_delay_seconds,
    )
    screenshot_manager = ScreenshotManager(config)
    context_manager = ContextManager(config)
    page_manager = PageManager(config, retry_manager, screenshot_manager, rate_limiter)

    browser_manager = BrowserManager(config, retry_manager)

    logger.info("=== Browser framework verification starting ===")

    with browser_manager as bm:
        assert bm.is_running, "BrowserManager should report running after start()"

        context = context_manager.create_context(bm.browser)
        page = page_manager.open_page(context)

        page_manager.navigate(page, "https://example.com")
        title = page.title()
        logger.info("Page title after navigation: {!r}", title)

        screenshot_path = screenshot_manager.capture(page, reason="verification_success")
        assert screenshot_path is not None, "Screenshot capture should have succeeded"
        assert screenshot_path.exists(), f"Screenshot file missing: {screenshot_path}"
        assert screenshot_path.stat().st_size > 0, "Screenshot file is empty"

        page_manager.close_page(page)
        context_manager.close_context(context)

    assert not browser_manager.is_running, "BrowserManager should report stopped after __exit__"

    logger.info("=== Browser framework verification PASSED ===")
    logger.info("Screenshot saved at: {}", screenshot_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
