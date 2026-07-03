"""
Trac Jobs scraper — the second site-specific implementation built on
`job_automation.scrapers.base` and `job_automation.core`, added for the Job
Ingestion Service milestone. Mirrors `scrapers.nhs` structurally; see
docs/JOB_INGESTION.md for architecture and known limitations (selectors are
fixture-verified only, not verified against a live trac.jobs tenant site).
"""

from job_automation.scrapers.trac.trac_parser import TracParser
from job_automation.scrapers.trac.trac_scraper import ScrapeStats, TracScraper
from job_automation.scrapers.trac.trac_search import TracPaginator, TracSearch, build_trac_search_criteria
from job_automation.scrapers.trac.trac_urls import TRAC_BASE_URL, build_search_url, resolve_url

__all__ = [
    "TracParser",
    "TracScraper",
    "ScrapeStats",
    "TracPaginator",
    "TracSearch",
    "build_trac_search_criteria",
    "TRAC_BASE_URL",
    "build_search_url",
    "resolve_url",
]
