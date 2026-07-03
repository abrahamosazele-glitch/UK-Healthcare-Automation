"""
Central registry of every scraper class, keyed by site name.

Two ways scrapers get in here:
1. **Automatic**: `BaseScraper.__init_subclass__` calls `register()` for
   every concrete (non-abstract) subclass that declares a `site_name` —
   defining `class NHSJobsScraper(BaseScraper): site_name = "nhs_jobs"` is
   enough; there is no separate "add it to the registry" step to forget.
2. **Manual**: `register()`/`unregister()` are still public, for tests or
   for registering a scraper under an additional alias.

Kept as a classmethod-based registry (a module-level singleton in effect)
rather than an instantiable class — there is exactly one registry for the
process, and nothing is gained by allowing multiple instances.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from job_automation.scrapers.base.scraper_exceptions import (
    ScraperNotFoundError,
    ScraperRegistrationError,
)
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from job_automation.scrapers.base.base_scraper import BaseScraper


class ScraperRegistry:
    _scrapers: ClassVar[dict[str, type["BaseScraper"]]] = {}

    @classmethod
    def register(cls, name: str, scraper_cls: type["BaseScraper"]) -> None:
        existing = cls._scrapers.get(name)
        if existing is not None and existing is not scraper_cls:
            raise ScraperRegistrationError(
                f"Cannot register {scraper_cls.__name__!r} as {name!r}: "
                f"already registered to {existing.__name__!r}"
            )
        cls._scrapers[name] = scraper_cls
        logger.info("Scraper registered: '{}' -> {}", name, scraper_cls.__name__)

    @classmethod
    def unregister(cls, name: str) -> None:
        removed = cls._scrapers.pop(name, None)
        if removed is not None:
            logger.info("Scraper unregistered: '{}'", name)

    @classmethod
    def get(cls, name: str) -> type["BaseScraper"]:
        try:
            return cls._scrapers[name]
        except KeyError:
            raise ScraperNotFoundError(
                f"No scraper registered as {name!r}. Registered: {cls.list()}"
            ) from None

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._scrapers)
