"""
Reusable scraper framework — infrastructure only, no site-specific logic.
Every future NHS Jobs / TRAC / Indeed / Reed / CareHome scraper is expected
to subclass and compose these rather than reimplementing search/parse/
pagination/login/application handling. See docs/SCRAPER_FRAMEWORK.md.
"""

from job_automation.scrapers.base.base_application import ApplicationAnswer, BaseApplication
from job_automation.scrapers.base.base_login import BaseLogin
from job_automation.scrapers.base.base_paginator import BasePaginator
from job_automation.scrapers.base.base_parser import BaseParser, ParsedJob
from job_automation.scrapers.base.base_scraper import BaseScraper
from job_automation.scrapers.base.base_search import BaseSearch, SearchCriteria
from job_automation.scrapers.base.scraper_config import ScraperConfig
from job_automation.scrapers.base.scraper_exceptions import (
    ApplicationSubmissionError,
    LoginError,
    ParsingError,
    PaginationError,
    ScraperError,
    ScraperNotFoundError,
    ScraperRegistrationError,
    SearchError,
)
from job_automation.scrapers.base.scraper_registry import ScraperRegistry

__all__ = [
    "ApplicationAnswer",
    "BaseApplication",
    "BaseLogin",
    "BasePaginator",
    "BaseParser",
    "ParsedJob",
    "BaseScraper",
    "BaseSearch",
    "SearchCriteria",
    "ScraperConfig",
    "ApplicationSubmissionError",
    "LoginError",
    "ParsingError",
    "PaginationError",
    "ScraperError",
    "ScraperNotFoundError",
    "ScraperRegistrationError",
    "SearchError",
    "ScraperRegistry",
]
