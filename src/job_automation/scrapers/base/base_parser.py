"""
Converts one job listing's markup into a strongly-typed `ParsedJob`.

`parse()` is abstract — every site marks up a job card differently, so there
is no generic selector that works everywhere. `parse_all()` is concrete: it's
the reusable resilience policy (skip and log a bad card instead of aborting
the whole page), which is genuinely generic regardless of site.

Operates on Playwright `Locator` objects rather than raw HTML strings/
BeautifulSoup, staying consistent with the rest of the framework being
Playwright-native (see job_automation.core). A concrete parser is free to
call `.inner_html()` and hand off to another parsing library internally if a
site's markup makes that easier — `parse()`'s contract only fixes the input
(a `Locator` scoped to one listing) and output (`ParsedJob`), not how the
extraction happens inside.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Sequence

from job_automation.scrapers.base.scraper_exceptions import ParsingError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Locator


@dataclass
class ParsedJob:
    """One parsed job listing. Only `title` is required — different sites
    expose different subsets of these fields (e.g. NHS AfC `band` has no
    equivalent on a generic care-sector job board)."""

    title: str
    employer: str | None = None
    employer_url: str | None = None
    salary: str | None = None
    band: str | None = None
    location: str | None = None
    contract_type: str | None = None
    hours: str | None = None
    visa_sponsorship: bool | None = None
    posted_date: date | None = None
    closing_date: date | None = None
    job_url: str | None = None
    reference_number: str | None = None
    description: str | None = None
    requirements: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)


class BaseParser(ABC):
    @abstractmethod
    def parse(self, element: "Locator") -> ParsedJob:
        """Extract one listing's fields from its DOM element. Raise
        `ParsingError` if a required field (title) is missing or malformed —
        do not return a `ParsedJob` with an empty title."""

    def parse_all(self, elements: Sequence["Locator"]) -> list[ParsedJob]:
        """Parse every element, skipping (and logging) individual failures
        rather than letting one malformed card abort the whole page."""
        results: list[ParsedJob] = []
        for index, element in enumerate(elements):
            try:
                results.append(self.parse(element))
            except ParsingError as exc:
                logger.warning("Skipping unparseable job listing at index {}: {}", index, exc)
        return results
