"""
Persists and reloads authenticated sessions (cookies + localStorage) per
named site, so future login flows only need to authenticate once per
`session_max_age_hours` window instead of on every run.

Expiration is time-based by default (the session file's mtime vs.
`BrowserConfig.session_max_age_hours`) — deliberately simple, since this
module is site-agnostic and can't know what a "logged out" page looks like
for any particular site. A specific scraper can layer a stricter check on
top later (e.g. navigate to a known page and look for a login form) without
changing this class; see docs/BROWSER_FRAMEWORK.md's extension points.

Builds on ContextManager (composition) rather than duplicating context
creation/storage-state logic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from job_automation.core.browser_config import BrowserConfig
from job_automation.core.browser_exceptions import SessionExpiredError
from job_automation.core.context_manager import ContextManager
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext


class SessionManager:
    def __init__(self, config: BrowserConfig, context_manager: ContextManager) -> None:
        self._config = config
        self._context_manager = context_manager
        self._config.session_dir.mkdir(parents=True, exist_ok=True)

    def session_path(self, session_name: str) -> Path:
        return self._config.session_dir / f"{session_name}.json"

    def has_valid_session(self, session_name: str) -> bool:
        """True if a session file exists and is younger than
        `session_max_age_hours`."""
        path = self.session_path(session_name)
        if not path.exists():
            return False

        age_seconds = time.time() - path.stat().st_mtime
        max_age_seconds = self._config.session_max_age_hours * 3600
        if age_seconds > max_age_seconds:
            logger.info(
                "Session '{}' expired ({:.1f}h old, max {}h)",
                session_name,
                age_seconds / 3600,
                self._config.session_max_age_hours,
            )
            return False
        return True

    def load_session(self, browser: "Browser", session_name: str) -> "BrowserContext":
        """Create a context restored from the named session. Raises
        SessionExpiredError if no valid session file exists — the caller
        must fall back to logging in fresh and calling save_session()."""
        if not self.has_valid_session(session_name):
            raise SessionExpiredError(f"No valid session found for {session_name!r}")

        context = self._context_manager.create_context(
            browser, storage_state=self.session_path(session_name)
        )
        logger.info("Session '{}' restored", session_name)
        return context

    def save_session(self, context: "BrowserContext", session_name: str) -> Path:
        """Persist the context's current cookies/localStorage under this
        session name, e.g. right after a successful login."""
        path = self.session_path(session_name)
        self._context_manager.save_storage_state(context, path)
        return path

    def clear_session(self, session_name: str) -> None:
        path = self.session_path(session_name)
        if path.exists():
            path.unlink()
            logger.info("Session '{}' cleared", session_name)
