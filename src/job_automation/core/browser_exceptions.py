"""
Exception hierarchy for the browser automation framework.

Every component in `core/` catches foreign exceptions (Playwright's own
`Error`/`TimeoutError`, OS-level I/O errors, etc.) at the point of contact and
re-raises one of these instead — nothing above that boundary (RetryManager,
PageManager callers, future scrapers) needs to know Playwright's exception
types.

`TransientError` is a marker mixin, not a concrete exception: it identifies
which failures are worth retrying (a flaky launch, a slow page, a dropped
navigation) versus permanent ones a retry can't fix (a selector that doesn't
exist, an expired login session). `RetryManager` defaults to retrying only
`TransientError` subclasses.
"""

from __future__ import annotations


class BrowserAutomationError(Exception):
    """Base class for every exception raised by `job_automation.core`."""


class TransientError:
    """Marker mixin: failures of this kind are worth retrying."""


class BrowserLaunchError(BrowserAutomationError, TransientError):
    """Raised when launching Playwright or the Chromium browser fails."""


class ContextCreationError(BrowserAutomationError, TransientError):
    """Raised when creating a browser context fails."""


class PageNavigationError(BrowserAutomationError, TransientError):
    """Raised when navigating a page to a URL fails or times out."""


class DownloadError(BrowserAutomationError, TransientError):
    """Raised when saving or verifying a downloaded file fails."""


class ElementNotFoundError(BrowserAutomationError):
    """
    Raised when a required element can't be found. Not transient — a missing
    selector almost always means the page structure differs from what the
    caller expected, which a retry will not fix.
    """


class SessionExpiredError(BrowserAutomationError):
    """
    Raised when a persisted session doesn't exist or has expired. Not
    transient — the caller needs to re-authenticate, not retry.
    """


class RetryExhaustedError(BrowserAutomationError):
    """
    Raised by RetryManager when every retry attempt has failed. Chains the
    final underlying exception via `raise ... from last_exception`.
    """
