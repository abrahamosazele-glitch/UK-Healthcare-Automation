"""
Reusable login workflow.

`login()`, `logout()`, and `session_valid()` are abstract — every site has a
different login form and a different way of signaling "you're logged in"
(a welcome banner, an account menu, a specific cookie). `restore_session()`
and `save_session()` are concrete: they only delegate to
`job_automation.core.session_manager.SessionManager`, which already owns
session persistence — there is nothing site-specific about *storing*
cookies, only about *proving* a session is authenticated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from abc import ABC, abstractmethod
from pathlib import Path

from job_automation.core.browser_exceptions import SessionExpiredError
from job_automation.core.page_manager import PageManager
from job_automation.core.session_manager import SessionManager
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page


class BaseLogin(ABC):
    def __init__(self, site_name: str, page_manager: PageManager, session_manager: SessionManager) -> None:
        self._site_name = site_name
        self._page_manager = page_manager
        self._session_manager = session_manager

    @abstractmethod
    def login(self, page: "Page", *, username: str, password: str) -> None:
        """Perform the site-specific login flow (navigate, fill credentials,
        submit). Raise `LoginError` if it fails."""

    @abstractmethod
    def logout(self, page: "Page") -> None:
        """Perform the site-specific logout flow."""

    @abstractmethod
    def session_valid(self, page: "Page") -> bool:
        """Site-specific check: does the currently loaded page indicate an
        authenticated session (e.g. an account menu or welcome element)?"""

    def restore_session(self, browser: "Browser") -> "BrowserContext | None":
        """Try to load a previously-saved session. Returns None (rather than
        raising) if none exists or it has expired — the caller's job is to
        fall back to `login()` in that case, this method's job is only to
        report whether that fallback is needed."""
        try:
            context = self._session_manager.load_session(browser, self._site_name)
        except SessionExpiredError:
            logger.info("No valid saved session for '{}' — login required", self._site_name)
            return None
        logger.info("Session restored for '{}'", self._site_name)
        return context

    def save_session(self, context: "BrowserContext") -> Path:
        """Persist the context's current cookies/localStorage, typically
        called right after a successful `login()`."""
        return self._session_manager.save_session(context, self._site_name)
