"""
`JobProvider` for Reed.co.uk, via Reed's public jobseeker API
(https://www.reed.co.uk/developers/jobseeker) — not HTML scraping. Reed
explicitly documents and supports this API for exactly this use case, so it
carries none of the ToS risk `IndeedProvider`/`TotalJobsProvider` are
stubbed out over.

**Compliance note**: like `scrapers.nhs.nhs_urls`'s own disclaimer, the
request/response shape below (endpoint, query parameter names, JSON field
names) is Reed's long-published, stable public API contract, but has not
been exercised against the live API in this environment (no outbound
internet access here — see docs/JOB_INGESTION.md's "Verification" section).
Confirm field names against a real response (or Reed's own API docs) the
first time this runs against production.

No Playwright/`BaseScraper` needed — this is a plain authenticated JSON
GET, so `httpx` (already a dependency) is used directly rather than
composing browser-automation machinery a REST API call doesn't need.
Authentication is HTTP Basic Auth with the API key as the username and an
empty password, exactly as Reed's docs specify.
"""

from __future__ import annotations

from datetime import date, datetime

import httpx

from job_automation.config.settings import settings
from job_automation.database.services import JobIngestionService
from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats
from job_automation.scrapers.base import ParsedJob
from job_automation.utils.logger import logger

REED_SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
#: Reed caps a single search response at 100 results; this provider fetches
#: one page per (keyword, location) combination from `settings
#: .scrape_keywords`/`scrape_locations` rather than paginating further with
#: `resultsToSkip` — a reasonable scope limit for now, not a technical
#: ceiling (see docs/JOB_INGESTION.md's "Known limitations").
RESULTS_PER_SEARCH = 100


class ReedProviderError(Exception):
    """Raised when `ReedProvider` cannot run at all — a missing API key or
    an API-level failure, distinct from a single listing failing to parse
    (which is tallied in `ProviderRunStats.jobs_failed` instead)."""


class ReedProvider(JobProvider):
    source_name = "reed"

    def __init__(self, *, api_key: str | None = None, http_client: httpx.Client | None = None) -> None:
        # `api_key=None` (the default everywhere) reads from settings at
        # call time, not construction time — so a provider constructed
        # once by `provider_registry.get_provider()` before `.env` is
        # configured still sees a key added later, the same "read only
        # when actually needed" behavior `get_llm_provider()` has.
        self._api_key_override = api_key
        self._client = http_client

    def fetch_jobs(self, session) -> ProviderRunStats:
        api_key = self._api_key_override if self._api_key_override is not None else settings.reed_api_key
        if not api_key:
            raise ReedProviderError(
                "ReedProvider requires an API key (settings.reed_api_key is not set). "
                "Set REED_API_KEY in your .env file — see .env.example. Register for a "
                "free key at https://www.reed.co.uk/developers/jobseeker."
            )

        ingestion = JobIngestionService(session, source_site=self.source_name)
        stats = ProviderRunStats(source=self.source_name)

        client = self._client or httpx.Client(auth=(api_key, ""), timeout=30.0)
        try:
            for keyword in settings.scrape_keywords:
                for location in settings.scrape_locations or [None]:
                    self._fetch_one_search(client, keyword, location, ingestion, stats)
        finally:
            if self._client is None:
                client.close()

        stats.newly_created_job_ids = list(ingestion.created_job_ids)
        logger.info(
            "Reed ingestion: {} seen, {} created, {} updated, {} failed",
            stats.jobs_seen,
            stats.jobs_created,
            stats.jobs_updated,
            stats.jobs_failed,
        )
        return stats

    def _fetch_one_search(
        self,
        client: httpx.Client,
        keyword: str,
        location: str | None,
        ingestion: JobIngestionService,
        stats: ProviderRunStats,
    ) -> None:
        params = {"keywords": keyword, "resultsToTake": RESULTS_PER_SEARCH}
        if location:
            params["locationName"] = location

        try:
            response = client.get(REED_SEARCH_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ReedProviderError(f"Reed API request failed for keyword {keyword!r}: {exc}") from exc

        for row in data.get("results", []):
            stats.jobs_seen += 1
            try:
                parsed = _row_to_parsed_job(row)
                result = ingestion.save_parsed_job(parsed)
                if result.created:
                    stats.jobs_created += 1
                else:
                    stats.jobs_updated += 1
            except Exception as exc:
                stats.jobs_failed += 1
                logger.error("Failed to process Reed job {!r}: {}", row.get("jobTitle"), exc)


def _row_to_parsed_job(row: dict) -> ParsedJob:
    return ParsedJob(
        title=row["jobTitle"],
        employer=row.get("employerName"),
        salary=_format_salary(row),
        location=row.get("locationName"),
        contract_type=_contract_type(row),
        hours=_working_pattern(row),
        job_url=row.get("jobUrl"),
        reference_number=str(row["jobId"]) if row.get("jobId") is not None else None,
        description=row.get("jobDescription"),
        closing_date=_parse_reed_date(row.get("expirationDate")),
        posted_date=_parse_reed_date(row.get("date")),
        # Reed's search API has no explicit visa-sponsorship flag —
        # `None` correctly means "unknown," not "no" (see ParsedJob's
        # docstring), rather than guessing.
        visa_sponsorship=None,
    )


def _format_salary(row: dict) -> str | None:
    minimum = row.get("minimumSalary")
    maximum = row.get("maximumSalary")
    currency = row.get("currency") or "GBP"
    if minimum is None and maximum is None:
        return None
    symbol = "£" if currency == "GBP" else currency + " "
    if minimum is not None and maximum is not None and minimum != maximum:
        return f"{symbol}{minimum:,.0f} - {symbol}{maximum:,.0f} per annum"
    value = minimum if minimum is not None else maximum
    return f"{symbol}{value:,.0f} per annum"


def _contract_type(row: dict) -> str | None:
    if row.get("contractType"):
        return str(row["contractType"])
    if row.get("permanent"):
        return "Permanent"
    if row.get("contract"):
        return "Contract"
    if row.get("temp"):
        return "Temporary"
    return None


def _working_pattern(row: dict) -> str | None:
    patterns = []
    if row.get("fullTime"):
        patterns.append("Full-time")
    if row.get("partTime"):
        patterns.append("Part-time")
    return ", ".join(patterns) or None


def _parse_reed_date(value: str | None) -> date | None:
    """Reed's API returns dates as "DD/MM/YYYY"."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        logger.warning("Could not parse Reed date {!r}", value)
        return None
