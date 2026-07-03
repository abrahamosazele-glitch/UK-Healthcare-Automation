"""
Reusable methods for future automated applications: upload CV, upload cover
letter, answer questions, submit, confirm.

Each step is abstract — every application form has different upload
controls, different questionnaire layouts, and a different way of
confirming success. `apply()` is the one concrete piece: the orchestration
order (upload CV -> upload cover letter -> answer questions -> submit ->
confirm) is the same regardless of site, so it's a template method here
rather than being reimplemented per scraper.

This class only prepares the *reusable shape* of an application flow, per
this milestone's scope — no real site's application form is implemented
against it yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from job_automation.core.download_manager import DownloadManager
from job_automation.core.page_manager import PageManager
from job_automation.scrapers.base.scraper_exceptions import ApplicationSubmissionError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Page


@dataclass(frozen=True)
class ApplicationAnswer:
    question: str
    answer: str


class BaseApplication(ABC):
    def __init__(self, page_manager: PageManager, download_manager: DownloadManager | None = None) -> None:
        self._page_manager = page_manager
        self._download_manager = download_manager

    @abstractmethod
    def upload_cv(self, page: "Page", cv_path: Path) -> bool:
        """Site-specific: locate the CV upload control and attach the file.
        Return True on success, False on failure (do not raise for a normal
        "control not found" case — let `apply()` decide what that means)."""

    @abstractmethod
    def upload_cover_letter(self, page: "Page", cover_letter_path: Path) -> bool:
        """Site-specific: locate the cover letter upload control and attach
        the file."""

    @abstractmethod
    def answer_questions(self, page: "Page", answers: Sequence[ApplicationAnswer]) -> None:
        """Site-specific: fill in application questionnaire fields."""

    @abstractmethod
    def submit(self, page: "Page") -> None:
        """Site-specific: submit the completed application."""

    @abstractmethod
    def confirm_submission(self, page: "Page") -> bool:
        """Site-specific: verify a confirmation indicator appears after
        submission. Returns True if confirmed, False if uncertain."""

    def apply(
        self,
        page: "Page",
        *,
        cv_path: Path,
        cover_letter_path: Path | None = None,
        answers: Sequence[ApplicationAnswer] = (),
    ) -> bool:
        """Template method: upload -> answer -> submit -> confirm, in that
        order, regardless of site. Raises `ApplicationSubmissionError` if any
        step before confirmation fails; returns whatever `confirm_submission`
        reports (True/False) otherwise."""
        try:
            if not self.upload_cv(page, cv_path):
                raise ApplicationSubmissionError(f"CV upload failed for {cv_path}")

            if cover_letter_path is not None and not self.upload_cover_letter(page, cover_letter_path):
                raise ApplicationSubmissionError(f"Cover letter upload failed for {cover_letter_path}")

            if answers:
                self.answer_questions(page, answers)

            self.submit(page)
        except ApplicationSubmissionError:
            raise
        except Exception as exc:
            raise ApplicationSubmissionError(f"Application flow failed: {exc}") from exc

        confirmed = self.confirm_submission(page)
        logger.info("Application {}", "confirmed" if confirmed else "submitted but not confirmed")
        return confirmed
