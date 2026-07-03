"""
URL construction for NHS Jobs (jobs.nhs.uk).

**Compliance note**: per this milestone's constraints, no further live-site
inspection was performed beyond what was already gathered before this
constraint was put in place (the public homepage and its Terms and
Conditions, checked only to confirm scraping permissibility before any code
was written). The query parameter names below are a best-effort based on
typical GOV.UK Design System search-service URL conventions and NHS Jobs'
general public structure — they are **not verified against the live DOM**
and should be confirmed/corrected before this scraper is ever pointed at
production. See docs/NHS_SCRAPER.md's "Known limitations" section.

`base_url` and `search_path` are parameters (not hardcoded) specifically so
tests can point the exact same URL-building logic at a local fixture server
instead of the real site — the query-string construction is fully exercised
either way; only the host/path differ.
"""

from __future__ import annotations

from urllib.parse import urlencode, urljoin

from job_automation.scrapers.base import SearchCriteria

NHS_BASE_URL = "https://www.jobs.nhs.uk"
SEARCH_RESULTS_PATH = "/candidate/search/results"
LOGIN_PATH = "/candidate/login"
JOB_DETAIL_PATH_PREFIX = "/candidate/jobadvert/"


def build_search_url(
    criteria: SearchCriteria,
    *,
    base_url: str = NHS_BASE_URL,
    search_path: str = SEARCH_RESULTS_PATH,
) -> str:
    """Build a search-results URL from generic SearchCriteria.

    Expected `criteria.filters` keys (all optional): "distance" (miles),
    "salary_min", "band", "contract_type", "working_pattern",
    "visa_sponsorship" ("true"/"false"). See nhs_search.py's
    `build_nhs_search_criteria()` for a typed constructor that fills these
    in correctly rather than requiring callers to know the raw key names.
    """
    params: dict[str, str] = {}
    if criteria.keywords:
        params["keyword"] = " ".join(criteria.keywords)
    if criteria.location:
        params["location"] = criteria.location

    filters = criteria.filters
    if filters.get("distance"):
        params["distance"] = filters["distance"]
    if filters.get("salary_min"):
        params["salary"] = filters["salary_min"]
    if filters.get("band"):
        params["band"] = filters["band"]
    if filters.get("contract_type"):
        params["contractType"] = filters["contract_type"]
    if filters.get("working_pattern"):
        params["workingPattern"] = filters["working_pattern"]
    if filters.get("visa_sponsorship"):
        params["sponsorship"] = filters["visa_sponsorship"]

    if criteria.sort_by:
        params["sort"] = criteria.sort_by

    url = f"{base_url}{search_path}"
    query = urlencode(params)
    return f"{url}?{query}" if query else url


def login_url(*, base_url: str = NHS_BASE_URL) -> str:
    return f"{base_url}{LOGIN_PATH}"


def resolve_url(href: str, *, current_url: str) -> str:
    """Resolve a possibly-relative href (as seen in scraped markup) against
    the page it was found on — using `urljoin` rather than naively prefixing
    a base URL, since a relative href is relative to the *current page's*
    path, not necessarily the site root (e.g. a link on
    `/candidate/search/results` pointing to `../jobadvert/123` only
    resolves correctly relative to that page). Absolute hrefs pass through
    unchanged, which `urljoin` already does correctly."""
    return urljoin(current_url, href)
