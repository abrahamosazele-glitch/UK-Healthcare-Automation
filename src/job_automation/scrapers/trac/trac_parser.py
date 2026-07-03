"""
Parses Trac Jobs listings in two phases — same "summary card, then detail
page" split as `scrapers.nhs.nhs_parser`, since Trac's public vacancy
microsites follow the same general pattern (a search-results list with
brief cards, full description/requirements/benefits only on each vacancy's
own page).

**Compliance note**: the CSS selectors below target markup written for
this milestone's local fixtures (`tests/fixtures/trac/`) — a realistic but
not-live-verified approximation of a Trac Jobs tenant site's structure. See
docs/JOB_INGESTION.md's "Known limitations".
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from job_automation.scrapers.base import BaseParser, ParsedJob
from job_automation.scrapers.base.scraper_exceptions import ParsingError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class TracParser(BaseParser):
    #: Selector for one job card on a search-results page. Shared with
    #: trac_search.py, same reasoning as NHSParser.CARD_SELECTOR.
    CARD_SELECTOR = "li.trac-vacancy"

    def parse(self, element: "Locator") -> ParsedJob:
        title = self._text(element, ".trac-vacancy__title")
        if not title:
            reference = element.get_attribute("data-vacancy-id") or "<unknown>"
            raise ParsingError(f"Trac Jobs card {reference} has no .trac-vacancy__title")

        job_url: str | None = None
        title_link = element.locator(".trac-vacancy__title a")
        if title_link.count():
            job_url = title_link.get_attribute("href")

        return ParsedJob(
            title=title,
            employer=self._text(element, ".trac-vacancy__employer"),
            location=self._text(element, ".trac-vacancy__location"),
            salary=self._text(element, ".trac-vacancy__salary"),
            band=self._text(element, ".trac-vacancy__band"),
            contract_type=self._text(element, ".trac-vacancy__contract"),
            hours=self._text(element, ".trac-vacancy__pattern"),
            closing_date=self._parse_date_field(element, ".trac-vacancy__closing-date", "Closing date:"),
            posted_date=self._parse_date_field(element, ".trac-vacancy__posted-date", "Posted date:"),
            job_url=job_url,
            reference_number=self._reference(element),
        )

    def parse_detail(self, page: "Page", job: ParsedJob) -> ParsedJob:
        """Enrich a summary-parsed ParsedJob with fields only on its own
        vacancy page. Mutates and returns `job`; raises `ParsingError` only
        if the page doesn't look like a vacancy advert at all."""
        description = self._page_text(page, ".vacancy-detail__description")
        if description is None:
            raise ParsingError(f"Vacancy detail page for {job.job_url!r} has no .vacancy-detail__description")

        job.description = description
        job.requirements = self._page_list(page, ".vacancy-detail__requirements li")
        job.benefits = self._page_list(page, ".vacancy-detail__benefits li")

        employer_link = page.locator(".vacancy-detail__employer-website")
        if employer_link.count():
            job.employer_url = employer_link.get_attribute("href")

        sponsorship_badge = page.locator(".vacancy-detail__visa-sponsorship")
        if sponsorship_badge.count():
            job.visa_sponsorship = True

        return job

    def _reference(self, element: "Locator") -> str | None:
        text = self._text(element, ".trac-vacancy__reference")
        if text is None:
            return element.get_attribute("data-vacancy-id")
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
