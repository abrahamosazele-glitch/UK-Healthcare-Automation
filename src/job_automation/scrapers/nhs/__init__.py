"""
NHS Jobs scraper — the first site-specific implementation built on
`job_automation.scrapers.base` and `job_automation.core`. See
docs/NHS_SCRAPER.md for architecture, search/parser/pagination flow, and
known limitations (selectors here are not verified against the live site —
this milestone only verifies against local fixtures, per an explicit
compliance requirement; see that doc for why).
"""

from job_automation.scrapers.nhs.nhs_login import NHSLogin
from job_automation.scrapers.nhs.nhs_parser import NHSParser
from job_automation.scrapers.nhs.nhs_scraper import NHSScraper, ScrapeStats
from job_automation.scrapers.nhs.nhs_search import NHSPaginator, NHSSearch, build_nhs_search_criteria
from job_automation.scrapers.nhs.nhs_urls import NHS_BASE_URL, build_search_url, login_url, resolve_url

__all__ = [
    "NHSLogin",
    "NHSParser",
    "NHSScraper",
    "ScrapeStats",
    "NHSPaginator",
    "NHSSearch",
    "build_nhs_search_criteria",
    "NHS_BASE_URL",
    "build_search_url",
    "login_url",
    "resolve_url",
]
