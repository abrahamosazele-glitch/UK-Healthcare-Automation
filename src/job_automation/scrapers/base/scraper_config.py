"""
Configuration for the scraper framework.

Composes `job_automation.core.browser_config.BrowserConfig` rather than
duplicating its fields — timeouts, headless mode, retry count, and
download/screenshot directories are all *browser* concerns already fully
specified there. `ScraperConfig` only adds what's genuinely specific to
scraping: a pagination safety cap. Mirrors `BrowserConfig`'s own shape
(frozen `pydantic.BaseModel`, `.from_settings()` factory, works standalone
with defaults for testing).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from job_automation.core.browser_config import BrowserConfig


class ScraperConfig(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    browser: BrowserConfig = BrowserConfig()

    # Hard safety cap on how many result pages BasePaginator will traverse,
    # independent of whatever "last page" detection a site's own DOM offers —
    # protects against an infinite loop if a site's pagination markup changes
    # in a way that breaks has_next_page().
    max_pages: int = 10

    @classmethod
    def from_settings(cls, settings: object, browser_config: BrowserConfig | None = None) -> "ScraperConfig":
        return cls(
            browser=browser_config or BrowserConfig.from_settings(settings),
            max_pages=settings.scraper_max_pages,
        )
