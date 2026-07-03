"""
Reusable Playwright browser automation framework — infrastructure only, no
site-specific logic. Every future scraper, login flow, and application
workflow is expected to compose these classes rather than talking to
Playwright directly. See docs/BROWSER_FRAMEWORK.md for the full design.
"""

from job_automation.core.browser_config import BrowserConfig
from job_automation.core.browser_exceptions import (
    BrowserAutomationError,
    BrowserLaunchError,
    ContextCreationError,
    DownloadError,
    ElementNotFoundError,
    PageNavigationError,
    RetryExhaustedError,
    SessionExpiredError,
    TransientError,
)
from job_automation.core.browser_manager import BrowserManager
from job_automation.core.context_manager import ContextManager
from job_automation.core.download_manager import DownloadManager
from job_automation.core.page_manager import PageManager
from job_automation.core.rate_limiter import RateLimiter
from job_automation.core.retry_manager import RetryManager
from job_automation.core.screenshot_manager import ScreenshotManager
from job_automation.core.session_manager import SessionManager

__all__ = [
    "BrowserConfig",
    "BrowserAutomationError",
    "BrowserLaunchError",
    "ContextCreationError",
    "DownloadError",
    "ElementNotFoundError",
    "PageNavigationError",
    "RetryExhaustedError",
    "SessionExpiredError",
    "TransientError",
    "BrowserManager",
    "ContextManager",
    "DownloadManager",
    "PageManager",
    "RateLimiter",
    "RetryManager",
    "ScreenshotManager",
    "SessionManager",
]
