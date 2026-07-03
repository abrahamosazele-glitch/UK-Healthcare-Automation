"""
Immutable configuration object for every `core/` component.

Deliberately a plain `pydantic.BaseModel`, not a `BaseSettings` subclass —
`core/` should not know how to read `.env` or environment variables itself.
Instead, `BrowserConfig.from_settings()` builds one from the app's existing
`job_automation.config.settings.Settings`. Every component in this package
takes a `BrowserConfig` in its constructor rather than importing the global
`settings` object directly, so each one can be unit-tested with a hand-built
config and has no hidden dependency on process environment or a singleton.

All fields have sane defaults, so `BrowserConfig()` also works standalone
(e.g. in tests) without going through `Settings`/`.env` at all.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class BrowserConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # --- Launch ---
    headless: bool = True
    slow_mo_ms: int = 0

    # --- Timeouts (Playwright uses milliseconds) ---
    navigation_timeout_ms: int = 30_000
    action_timeout_ms: int = 10_000

    # --- Context ---
    viewport_width: int = 1280
    viewport_height: int = 800
    user_agent: str = "Mozilla/5.0 (compatible; UKHealthcareJobBot/1.0)"
    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None

    # --- Storage locations ---
    download_dir: Path = _PROJECT_ROOT / "data" / "downloads"
    screenshot_dir: Path = _PROJECT_ROOT / "data" / "screenshots"
    session_dir: Path = _PROJECT_ROOT / "data" / "sessions"
    session_max_age_hours: int = 24

    # --- Retry ---
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 30.0

    # --- Rate limiting ---
    rate_limit_min_delay_seconds: float = 1.0
    rate_limit_max_delay_seconds: float = 3.0

    @property
    def viewport(self) -> dict[str, int]:
        return {"width": self.viewport_width, "height": self.viewport_height}

    @property
    def proxy(self) -> dict[str, str] | None:
        if not self.proxy_server:
            return None
        proxy: dict[str, str] = {"server": self.proxy_server}
        if self.proxy_username:
            proxy["username"] = self.proxy_username
        if self.proxy_password:
            proxy["password"] = self.proxy_password
        return proxy

    @classmethod
    def from_settings(cls, settings: object) -> "BrowserConfig":
        """Build from `job_automation.config.settings.settings` (typed loosely
        as `object` to avoid a hard import dependency on the Settings class)."""
        return cls(
            headless=settings.browser_headless,
            slow_mo_ms=settings.browser_slow_mo_ms,
            navigation_timeout_ms=settings.browser_navigation_timeout_ms,
            action_timeout_ms=settings.browser_action_timeout_ms,
            viewport_width=settings.browser_viewport_width,
            viewport_height=settings.browser_viewport_height,
            user_agent=settings.user_agent,
            proxy_server=settings.browser_proxy_server,
            proxy_username=settings.browser_proxy_username,
            proxy_password=settings.browser_proxy_password,
            download_dir=settings.browser_download_dir,
            screenshot_dir=settings.browser_screenshot_dir,
            session_dir=settings.browser_session_dir,
            session_max_age_hours=settings.browser_session_max_age_hours,
            max_retries=settings.browser_max_retries,
            retry_base_delay_seconds=settings.browser_retry_base_delay_seconds,
            retry_max_delay_seconds=settings.browser_retry_max_delay_seconds,
            rate_limit_min_delay_seconds=settings.browser_rate_limit_min_delay_seconds,
            rate_limit_max_delay_seconds=settings.browser_rate_limit_max_delay_seconds,
        )
