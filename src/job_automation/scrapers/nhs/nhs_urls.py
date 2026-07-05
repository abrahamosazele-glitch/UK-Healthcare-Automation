"""
URL construction for NHS Jobs (jobs.nhs.uk).

Query parameter names/values below are verified against the live
`/candidate/search/results` page (inspected via a manually-saved real
search-results page and a Playwright `page.content()` capture of the same
search — both agreed on every parameter and value used here).

There is **no `sponsorship`/visa filter anywhere on the real search form**
(confirmed by inspecting the full rendered filter form) — the previous
`visa_sponsorship` filter here was invented and has been removed.

Other confirmed real parameters not modeled here (`jobReference`,
`employer`, `staffGroup`, `payRange`, `covidJobsOnly`) are left out because
nothing in this codebase currently needs them — add them the same way as
the existing filters if a caller needs them later.

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
    "salary_from"/"salary_to", "pay_band" (real site values like "BAND_5",
    not "Band 5"), "contract_type" (e.g. "Permanent"), "working_pattern"
    (real site slugs like "full-time", "part-time"). See nhs_search.py's
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
    if filters.get("salary_from"):
        params["salaryFrom"] = filters["salary_from"]
    if filters.get("salary_to"):
        params["salaryTo"] = filters["salary_to"]
    if filters.get("pay_band"):
        params["payBand"] = filters["pay_band"]
    if filters.get("contract_type"):
        params["contractType"] = filters["contract_type"]
    if filters.get("working_pattern"):
        params["workingPattern"] = filters["working_pattern"]

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
    unchanged, which `urljoin` already does correctly.

    Confirmed necessary: real search-result cards use relative hrefs
    (e.g. `/candidate/jobadvert/E0023-26155?...`), not absolute ones."""
    return urljoin(current_url, href)
