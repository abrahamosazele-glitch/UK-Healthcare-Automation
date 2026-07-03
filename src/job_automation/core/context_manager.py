"""
Creates and configures `BrowserContext` objects: viewport, user agent, proxy,
download permissions, and optionally restoring a previously-saved storage
state (cookies + localStorage) so a login doesn't have to happen every run.

Stateless with respect to which `Browser` it uses — `create_context()` takes
the `Browser` as a parameter rather than holding one, since `BrowserManager`
owns that object's lifecycle, not this class. `SessionManager` builds on top
of this for the higher-level "does a saved session exist / is it still
valid" logic; this class only knows the mechanics of creating a context and
reading/writing its storage state.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from job_automation.core.browser_config import BrowserConfig
from job_automation.core.browser_exceptions import ContextCreationError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext


class ContextManager:
    def __init__(self, config: BrowserConfig) -> None:
        self._config = config

    def create_context(
        self,
        browser: "Browser",
        *,
        storage_state: Path | None = None,
    ) -> "BrowserContext":
        """Create a new context configured from BrowserConfig. If
        `storage_state` is given and exists, the context starts already
        authenticated with those cookies/localStorage."""
        kwargs: dict[str, object] = {
            "viewport": self._config.viewport,
            "user_agent": self._config.user_agent,
            "accept_downloads": True,
        }
        if storage_state is not None and storage_state.exists():
            kwargs["storage_state"] = str(storage_state)
        if self._config.proxy is not None:
            kwargs["proxy"] = self._config.proxy

        try:
            context = browser.new_context(**kwargs)
        except Exception as exc:
            raise ContextCreationError(f"Failed to create browser context: {exc}") from exc

        context.set_default_navigation_timeout(self._config.navigation_timeout_ms)
        context.set_default_timeout(self._config.action_timeout_ms)

        logger.info(
            "New browser context created (storage_state={})",
            storage_state if storage_state and storage_state.exists() else None,
        )
        return context

    def save_storage_state(self, context: "BrowserContext", path: Path) -> None:
        """Persist the context's cookies/localStorage to `path`, creating
        parent directories as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(path))
        logger.info("Storage state saved to {}", path)

    def close_context(self, context: "BrowserContext") -> None:
        try:
            context.close()
            logger.info("Browser context closed")
        except Exception as exc:
            logger.warning("Error while closing context: {}", exc)
