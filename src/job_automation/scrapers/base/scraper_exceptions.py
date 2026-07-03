"""
Exception hierarchy for the scraper framework.

`ScraperError` extends `job_automation.core.browser_exceptions
.BrowserAutomationError` rather than starting a parallel hierarchy — a
scraper failure *is* a browser-automation failure at a higher level, and
reusing the base means a single `except BrowserAutomationError` still
catches everything, and reuses `TransientError` as the one source of truth
for "is this worth retrying" across the whole app (see
job_automation.core.browser_exceptions).
"""

from __future__ import annotations

from job_automation.core.browser_exceptions import BrowserAutomationError, TransientError


class ScraperError(BrowserAutomationError):
    """Base class for every exception raised by `job_automation.scrapers`."""


class LoginError(ScraperError):
    """
    Raised when a login attempt fails. Not transient by default — most
    causes (wrong credentials, changed form layout, account locked) won't
    resolve on retry, and retrying a login endpoint repeatedly risks
    triggering the site's own bot/abuse defenses.
    """


class SearchError(ScraperError, TransientError):
    """Raised when performing a search fails — treated as transient (a slow
    results page, a dropped request) since search rarely fails permanently."""


class ParsingError(ScraperError):
    """
    Raised when raw markup can't be converted into a typed `ParsedJob`. Not
    transient — a changed page structure needs a code update, not a retry.
    """


class PaginationError(ScraperError, TransientError):
    """Raised when navigating between result pages fails."""


class ApplicationSubmissionError(ScraperError):
    """Raised when an automated application step (upload, answer, submit,
    confirm) fails."""


class ScraperNotFoundError(ScraperError):
    """Raised by `ScraperRegistry.get()` when no scraper is registered under
    the given name."""


class ScraperRegistrationError(ScraperError):
    """Raised when registering a scraper under a name already claimed by a
    different scraper class."""
