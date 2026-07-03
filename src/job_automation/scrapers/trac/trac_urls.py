"""
URL construction for Trac Jobs (trac.jobs) — the recruitment platform many
individual NHS trusts run their own vacancy microsites on (e.g.
`example-trust.trac.jobs`), rather than one central site like jobs.nhs.uk.

**Compliance note**: same disclaimer as `scrapers.nhs.nhs_urls` — the query
parameter names below are a best-effort based on Trac Jobs' typical public
search-page structure, **not verified against a live trac.jobs tenant site**
(no live inspection was performed; see docs/JOB_INGESTION.md's "Known
limitations"). Confirm/correct before pointing this at production.

`base_url`/`search_path` are parameters, not hardcoded, for the same
reason `nhs_urls.py`'s are: tests point the exact same URL-building logic
at a local fixture server instead of a real tenant site.
"""

from __future__ import annotations

from urllib.parse import urlencode, urljoin

from job_automation.scrapers.base import SearchCriteria

#: A generic placeholder — a real deployment configures one `TracScraper`
#: instance per NHS trust tenant (each trust's own `*.trac.jobs` subdomain),
#: passed in explicitly rather than hardcoded here.
TRAC_BASE_URL = "https://example-trust.trac.jobs"
SEARCH_RESULTS_PATH = "/vacancies"
JOB_DETAIL_PATH_PREFIX = "/vacancy/"


def build_search_url(
    criteria: SearchCriteria,
    *,
    base_url: str = TRAC_BASE_URL,
    search_path: str = SEARCH_RESULTS_PATH,
) -> str:
    """Build a search-results URL from generic SearchCriteria.

    Expected `criteria.filters` keys (all optional): "band", "contract_type",
    "working_pattern", "closing_within_days"."""
    params: dict[str, str] = {}
    if criteria.keywords:
        params["q"] = " ".join(criteria.keywords)
    if criteria.location:
        params["location"] = criteria.location

    filters = criteria.filters
    if filters.get("band"):
        params["band"] = filters["band"]
    if filters.get("contract_type"):
        params["contract"] = filters["contract_type"]
    if filters.get("working_pattern"):
        params["pattern"] = filters["working_pattern"]
    if filters.get("closing_within_days"):
        params["closingWithinDays"] = filters["closing_within_days"]

    if criteria.sort_by:
        params["sort"] = criteria.sort_by

    url = f"{base_url}{search_path}"
    query = urlencode(params)
    return f"{url}?{query}" if query else url


def resolve_url(href: str, *, current_url: str) -> str:
    """Resolve a possibly-relative href against the page it was found on —
    see `nhs_urls.resolve_url()` for the identical reasoning."""
    return urljoin(current_url, href)
