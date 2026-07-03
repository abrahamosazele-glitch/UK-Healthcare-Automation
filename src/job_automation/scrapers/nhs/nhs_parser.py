"""
Parses NHS Jobs listings in two phases, matching how the real site actually
exposes data: a search-results card only ever shows a summary (title,
employer, location, salary, band, contract type, working pattern,
closing/posted date, a reference number, and a link to the full advert) —
full description/requirements/benefits/employer link only appear on the
job's own advert page. `parse()` handles the first phase (required by
`BaseParser`'s contract); `parse_detail()` is an NHS-specific second phase
that enriches an already-parsed `ParsedJob` after navigating to its
`job_url`.

**Compliance note**: the CSS selectors below target markup written for
this milestone's local fixtures (`tests/fixtures/nhs/`), designed to look
like a realistic GOV.UK Design System-style results page — they are
**not verified against the live jobs.nhs.uk DOM** (no live inspection was
performed for this milestone; see docs/NHS_SCRAPER.md's "Known
limitations"). Confirm and adjust selectors against the real site before
this scraper is ever pointed at production.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from job_automation.scrapers.base import BaseParser, ParsedJob
from job_automation.scrapers.base.scraper_exceptions import ParsingError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class NHSParser(BaseParser):
    #: Selector for one job card on a search-results page. Shared with
    #: nhs_search.py (both need to know what "a result" looks like) rather
    #: than duplicating the literal string in two places.
    CARD_SELECTOR = "li.search-result"

    def parse(self, element: "Locator") -> ParsedJob:
        title = self._text(element, ".search-result__title")
        if not title:
            reference = element.get_attribute("data-reference") or "<unknown>"
            raise ParsingError(f"NHS job card {reference} has no .search-result__title")

        job_url: str | None = None
        title_link = element.locator(".search-result__title a")
        if title_link.count():
            job_url = title_link.get_attribute("href")

        return ParsedJob(
            title=title,
            employer=self._text(element, ".search-result__employer"),
            location=self._text(element, ".search-result__location"),
            salary=self._text(element, ".search-result__salary"),
            band=self._text(element, ".search-result__band"),
            contract_type=self._text(element, ".search-result__contract"),
            hours=self._text(element, ".search-result__pattern"),
            closing_date=self._parse_date_field(element, ".search-result__closing-date", "Closing date:"),
            posted_date=self._parse_date_field(element, ".search-result__posted-date", "Posted date:"),
            job_url=job_url,
            reference_number=self._reference(element),
        )

    def parse_detail(self, page: "Page", job: ParsedJob) -> ParsedJob:
        """Enrich a summary-parsed ParsedJob with fields only available on
        its own advert page. Mutates and returns `job` for convenient
        chaining; raises ParsingError only if the page doesn't look like a
        job advert at all (missing description is tolerated as many NHS
        adverts vary in structure — only a summary's missing title is fatal)."""
        description = self._page_text(page, ".job-detail__description")
        if description is None:
            raise ParsingError(f"Job detail page for {job.job_url!r} has no .job-detail__description")

        job.description = description
        job.requirements = self._page_list(page, ".job-detail__requirements li")
        job.benefits = self._page_list(page, ".job-detail__benefits li")

        employer_link = page.locator(".job-detail__employer-website")
        if employer_link.count():
            job.employer_url = employer_link.get_attribute("href")

        return job

    def _reference(self, element: "Locator") -> str | None:
        text = self._text(element, ".search-result__reference")
        if text is None:
            return element.get_attribute("data-reference")
        return text.split(":", 1)[-1].strip() if ":" in text else text.strip()

    def _parse_date_field(self, element: "Locator", selector: str, prefix: str) -> date | None:
        text = self._text(element, selector)
        if not text:
            return None
        cleaned = text.split(":", 1)[-1].strip() if ":" in text else text.replace(prefix, "").strip()
        try:
            return datetime.strptime(cleaned, "%d %B %Y").date()
        except ValueError:
            logger.warning("Could not parse date {!r} from {}", cleaned, selector)
            return None

    @staticmethod
    def _text(element: "Locator", selector: str) -> str | None:
        sub = element.locator(selector)
        if sub.count() == 0:
            return None
        text = sub.text_content()
        return text.strip() if text else None

    @staticmethod
    def _page_text(page: "Page", selector: str) -> str | None:
        locator = page.locator(selector)
        if locator.count() == 0:
            return None
        text = locator.text_content()
        return text.strip() if text else None

    @staticmethod
    def _page_list(page: "Page", selector: str) -> list[str]:
        locator = page.locator(selector)
        return [(locator.nth(i).text_content() or "").strip() for i in range(locator.count())]
