"""
Shared helper for providers whose underlying search only accepts one
location per query (NHS Jobs, Trac Jobs) — runs one search per configured
location and aggregates the results, rather than joining every location
into a single query string. Joining was the original (wrong) approach: a
site's location filter narrows to one place/postcode/radius, it doesn't
accept a list, so `", ".join(settings.scrape_locations)` was never a real
search term any of these sites understood — just a string that happened
not to error.

Reed doesn't use this: it already loops over `(keyword, location)` pairs
itself (see `reed_provider.py`), since its API takes keyword and location
as two independent parameters in the same request rather than requiring a
separate page load per location the way a browser-driven search does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from job_automation.ingestion.job_provider import ProviderRunStats
from job_automation.utils.logger import logger


def run_per_location(
    source_name: str,
    locations: Iterable[str] | None,
    run_one: Callable[[str | None], ProviderRunStats],
) -> ProviderRunStats:
    """Call `run_one(location)` once per location in `locations` (or once
    with `location=None` if none are configured), summing every field of
    the returned `ProviderRunStats` into one aggregate result.

    A single location's search failing doesn't abort the rest — logged and
    skipped, the same "one bad unit doesn't stop the whole run" tolerance
    `ingestion_orchestrator.run_ingestion()` already applies across
    providers, applied here one level down across locations within a
    single provider."""
    location_list = list(locations) if locations else [None]
    total = ProviderRunStats(source=source_name)

    for location in location_list:
        try:
            stats = run_one(location)
        except Exception as exc:
            logger.error("{} search for location {!r} failed: {}", source_name, location, exc)
            continue

        total.jobs_seen += stats.jobs_seen
        total.jobs_created += stats.jobs_created
        total.jobs_updated += stats.jobs_updated
        total.jobs_failed += stats.jobs_failed
        total.newly_created_job_ids.extend(stats.newly_created_job_ids)

    return total
