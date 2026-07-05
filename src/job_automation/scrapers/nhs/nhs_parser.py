"""
Parses NHS Jobs listings in two phases, matching how the real site actually
exposes data: a search-results card only ever shows a summary (title,
employer, location, salary, contract type, working pattern, closing/posted
date, and a link to the full advert) — full description/requirements/
benefits/employer link only appear on the job's own advert page. `parse()`
handles the first phase (required by `BaseParser`'s contract); `parse_detail()`
is an NHS-specific second phase that enriches an already-parsed `ParsedJob`
after navigating to its `job_url`.

**Verification status**: `parse()`'s selectors below are verified against
the real `jobs.nhs.uk/candidate/search/results` DOM — confirmed via two
independent captures (a manually-saved real search-results page, and a
Playwright `page.content()` render of the same search) that agreed on every
selector used here. Real cards carry `data-test="..."` attributes (an
nhsuk-frontend/govuk-frontend convention, deliberately provided for
automated testing) rather than the invented `.search-result__*` BEM classes
this file previously used.

Notable real-DOM findings baked into `parse()`:
- No search-result card exposes an NHS AfC "band" — confirmed absent
  across every sampled card (10/10). `ParsedJob.band` is simply left unset
  here; it has no search-results-page source. (It may still appear on a
  job's own advert page — see `parse_detail()`'s note below.)
- No card exposes a standalone reference-number text field. The reference
  is embedded in the job's own URL path (`/candidate/jobadvert/<reference>`)
  and is derived from there instead of a fixture-only `data-reference`
  attribute.
- Employer and location are *not* separate elements — both live inside one
  `data-test="search-result-location"` block: the employer is the `<h3>`'s
  own text, and the location is a nested `<div>` whose text carries a
  literal `"The area below is where the role is located: "` prefix that
  must be stripped.
- Real markup has heavy internal whitespace/newlines from templating
  (e.g. salary text splits "£16 to £18" and "an hour" across lines) —
  `_clean_text()` collapses this instead of just `.strip()`-ing it.
- Job URLs in the live DOM are relative (`/candidate/jobadvert/...`), not
  absolute — `nhs_urls.resolve_url()` already handles both via `urljoin`.

**`parse_detail()` is NOT yet updated**: no real job-advert-page HTML has
been captured yet (only search-results pages, which is a different page
type — a page saved from a job's own URL turned out to be another copy of
the search-results page). It still targets the original fixture-only
`.job-detail__*` classes and is not verified against the live DOM. Update
this method and its fixture together once a real
`/candidate/jobadvert/<reference>` page has been inspected.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import TYPE_CHECKING

from job_automation.scrapers.base import BaseParser, ParsedJob
from job_automation.scrapers.base.scraper_exceptions import ParsingError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

_REFERENCE_RE = re.compile(r"/candidate/jobadvert/([^/?#]+)")


class NHSParser(BaseParser):
    #: Selector for one job card on a search-results page. Shared with
    #: nhs_search.py (both need to know what "a result" looks like) rather
    #: than duplicating the literal string in two places.
    CARD_SELECTOR = 'li[data-test="search-result"]'

    def parse(self, element: "Locator") -> ParsedJob:
        title_link = element.locator('a[data-test="search-result-job-title"]')
        if title_link.count() == 0:
            raise ParsingError("NHS job card has no title link (a[data-test='search-result-job-title'])")

        title = self._clean_text(title_link.text_content())
        if not title:
            raise ParsingError("NHS job card's title link has no text")

        job_url = title_link.get_attribute("href")
        employer, location = self._employer_and_location(element)

        return ParsedJob(
            title=title,
            employer=employer,
            location=location,
            salary=self._text(element, 'li[data-test="search-result-salary"] strong'),
            contract_type=self._text(element, 'li[data-test="search-result-jobType"] strong'),
            hours=self._text(element, 'li[data-test="search-result-workingPattern"] strong'),
            closing_date=self._parse_date_field(element, 'li[data-test="search-result-closingDate"] strong'),
            posted_date=self._parse_date_field(element, 'li[data-test="search-result-publicationDate"] strong'),
            job_url=job_url,
            reference_number=self._reference(job_url),
        )

    def parse_detail(self, page: "Page", job: ParsedJob) -> ParsedJob:
        """Enrich a summary-parsed ParsedJob with fields only available on
        its own advert page. Mutates and returns `job` for convenient
        chaining; raises ParsingError only if the page doesn't look like a
        job advert at all (missing description is tolerated as many NHS
        adverts vary in structure — only a summary's missing title is fatal).

        Still targets fixture-only selectors — see module docstring."""
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

    def _employer_and_location(self, element: "Locator") -> tuple[str | None, str | None]:
        """Employer and location share one block: the employer is the
        `<h3>`'s own text, the location is a nested `<div>` whose text must
        have its `"The area below is where the role is located: "` prefix
        stripped."""
        heading = element.locator('div[data-test="search-result-location"] h3')
        if heading.count() == 0:
            return None, None

        full_text = heading.text_content() or ""
        nested = heading.locator("div.location-font-size")
        nested_text = nested.text_content() or "" if nested.count() else ""

        employer_source = full_text.replace(nested_text, "") if nested_text else full_text
        employer = self._clean_text(employer_source)

        location = None
        cleaned_nested = self._clean_text(nested_text)
        if cleaned_nested:
            location = cleaned_nested.split(":", 1)[-1].strip() if ":" in cleaned_nested else cleaned_nested

        return employer, location

    @staticmethod
    def _reference(job_url: str | None) -> str | None:
        if not job_url:
            return None
        match = _REFERENCE_RE.search(job_url)
        return match.group(1) if match else None

    def _parse_date_field(self, element: "Locator", selector: str) -> date | None:
        text = self._text(element, selector)
        if not text:
            return None
        try:
            return datetime.strptime(text, "%d %B %Y").date()
        except ValueError:
            logger.warning("Could not parse date {!r} from {}", text, selector)
            return None

    @staticmethod
    def _text(element: "Locator", selector: str) -> str | None:
        sub = element.locator(selector)
        if sub.count() == 0:
            return None
        return NHSParser._clean_text(sub.text_content())

    @staticmethod
    def _clean_text(text: str | None) -> str | None:
        """Real NHS markup has heavy internal whitespace/newlines from
        templating — collapse runs of whitespace instead of just
        `.strip()`-ing, e.g. so salary text split across lines becomes
        "£16 to £18 an hour" rather than keeping the embedded newlines."""
        if not text:
            return None
        collapsed = " ".join(text.split())
        return collapsed or None

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
