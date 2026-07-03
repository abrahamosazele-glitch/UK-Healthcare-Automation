"""
A complete, working (but non-production) scraper built entirely on
`job_automation.scrapers.base`, used to verify the framework end-to-end
without touching any real job website. Runs against the static fixture site
in `tests/fixtures/dummy_site/`, served locally over HTTP by
`tests/dummy_site_server.py`.

Exercises every Base* class with a real (if trivial) implementation — no
placeholders: DummyLogin actually fills and submits a form, DummySearch
actually navigates and checks for results, DummyParser actually extracts
every `ParsedJob` field from real HTML, DummyPaginator actually clicks
between three real pages (stopping correctly at the true last page and, in a
separate config, at the `max_pages` safety cap), and DummyApplication
actually uploads real files and fills a real form.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from job_automation.scrapers.base import (
    ApplicationAnswer,
    BaseApplication,
    BaseLogin,
    BasePaginator,
    BaseParser,
    BaseScraper,
    BaseSearch,
    ParsedJob,
    ScraperConfig,
    SearchCriteria,
)
from job_automation.scrapers.base.scraper_exceptions import (
    ApplicationSubmissionError,
    LoginError,
    ParsingError,
    SearchError,
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class DummyParser(BaseParser):
    def parse(self, element: "Locator") -> ParsedJob:
        title = self._text(element, ".job-title")
        if not title:
            reference = element.get_attribute("data-reference") or "<unknown>"
            raise ParsingError(f"Job card {reference} has no .job-title")

        visa_text = self._text(element, ".visa")
        closing_text = self._text(element, ".closing-date")
        closing_date = None
        if closing_text:
            try:
                closing_date = date.fromisoformat(closing_text)
            except ValueError:
                closing_date = None

        requirements_locator = element.locator(".requirements li")
        requirements = [
            (requirements_locator.nth(i).text_content() or "").strip()
            for i in range(requirements_locator.count())
        ]

        return ParsedJob(
            title=title,
            employer=self._text(element, ".employer"),
            salary=self._text(element, ".salary"),
            band=self._text(element, ".band"),
            location=self._text(element, ".location"),
            contract_type=self._text(element, ".contract"),
            hours=self._text(element, ".hours"),
            visa_sponsorship=None if visa_text is None else visa_text.strip().lower() == "yes",
            closing_date=closing_date,
            job_url=element.locator(".job-link").get_attribute("href")
            if element.locator(".job-link").count()
            else None,
            reference_number=element.get_attribute("data-reference"),
            description=self._text(element, ".description"),
            requirements=requirements,
        )

    @staticmethod
    def _text(element: "Locator", selector: str) -> str | None:
        sub = element.locator(selector)
        if sub.count() == 0:
            return None
        text = sub.text_content()
        return text.strip() if text else None


class DummySearch(BaseSearch):
    def __init__(self, page_manager, base_url: str) -> None:
        super().__init__(page_manager)
        self._base_url = base_url.rstrip("/")

    def build_search_url(self, criteria: SearchCriteria) -> str:
        return f"{self._base_url}/page1.html"

    def execute_search(self, page: "Page", criteria: SearchCriteria) -> None:
        # The fixture site has no real query-string search to submit against
        # — this stands in for whatever a real site needs (submitting a
        # form, waiting for an AJAX results panel) by confirming results
        # actually rendered.
        if not self._page_manager.element_exists(page, ".job-card", timeout_ms=2000):
            raise SearchError("No results appeared after navigating to the search URL")


class DummyPaginator(BasePaginator):
    def has_next_page(self, page: "Page") -> bool:
        return self._page_manager.element_exists(page, "a.next-page", timeout_ms=1000)

    def go_to_next_page(self, page: "Page") -> None:
        self._page_manager.safe_click(page, "a.next-page")
        page.wait_for_load_state("load")

    def has_previous_page(self, page: "Page") -> bool:
        return self._current_page > 1

    def go_to_previous_page(self, page: "Page") -> None:
        page.go_back()
        page.wait_for_load_state("load")


class DummyLogin(BaseLogin):
    def __init__(self, site_name: str, page_manager, session_manager, base_url: str) -> None:
        super().__init__(site_name, page_manager, session_manager)
        self._base_url = base_url.rstrip("/")

    def login(self, page: "Page", *, username: str, password: str) -> None:
        self._page_manager.navigate(page, f"{self._base_url}/login.html")
        if not self._page_manager.safe_type(page, "#username", username):
            raise LoginError("Could not fill in the username field")
        if not self._page_manager.safe_type(page, "#password", password):
            raise LoginError("Could not fill in the password field")
        if not self._page_manager.safe_click(page, "#login-btn"):
            raise LoginError("Could not submit the login form")
        if not self.session_valid(page):
            raise LoginError("Login form submitted but session does not look authenticated")

    def logout(self, page: "Page") -> None:
        self._page_manager.safe_click(page, "#logout-link")

    def session_valid(self, page: "Page") -> bool:
        return self._page_manager.element_exists(page, "#welcome-banner", timeout_ms=2000)


class DummyApplication(BaseApplication):
    def upload_cv(self, page: "Page", cv_path: Path) -> bool:
        return self._page_manager.set_input_files(page, "#cv-upload", cv_path)

    def upload_cover_letter(self, page: "Page", cover_letter_path: Path) -> bool:
        return self._page_manager.set_input_files(page, "#cover-letter-upload", cover_letter_path)

    def answer_questions(self, page: "Page", answers: Sequence[ApplicationAnswer]) -> None:
        for index, answer in enumerate(answers):
            if not self._page_manager.safe_type(page, f"#answer-{index}", answer.answer):
                raise ApplicationSubmissionError(f"Could not answer question {index}: {answer.question!r}")

    def submit(self, page: "Page") -> None:
        if not self._page_manager.safe_click(page, "#submit-btn"):
            raise ApplicationSubmissionError("Could not click the submit button")

    def confirm_submission(self, page: "Page") -> bool:
        return self._page_manager.element_exists(page, "#confirmation", timeout_ms=2000)


class DummyScraper(BaseScraper):
    site_name = "dummy"

    def __init__(self, config: ScraperConfig, base_url: str, **kwargs: object) -> None:
        super().__init__(config, **kwargs)
        self.base_url = base_url.rstrip("/")
        self.login_flow = DummyLogin(self.site_name, self.page_manager, self.session_manager, self.base_url)
        self.search_flow = DummySearch(self.page_manager, self.base_url)
        self.paginator = DummyPaginator(self.page_manager, max_pages=self.config.max_pages)
        self.parser = DummyParser()
        self.application_flow = DummyApplication(self.page_manager, self.download_manager)

    def scrape(self) -> list[ParsedJob]:
        criteria = SearchCriteria(keywords=["Healthcare Assistant"], location="London")
        self.search_flow.search(self.page, criteria)

        all_jobs: list[ParsedJob] = []
        while True:
            cards = self.page.locator(".job-card").all()
            all_jobs.extend(self.parser.parse_all(cards))
            if not self.paginator.next(self.page):
                break
        return all_jobs
