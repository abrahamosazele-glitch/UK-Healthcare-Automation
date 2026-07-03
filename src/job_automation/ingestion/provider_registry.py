"""
Maps a source name to its `JobProvider` class.

A plain dict, not an auto-registering class registry like `scrapers.base
.ScraperRegistry` — that one exists because scrapers can be defined in
arbitrary third-party-feeling modules and still need to be discoverable
(`ScraperRegistry.get("nhs_jobs")` from a scheduler task that doesn't import
`NHSScraper` directly). There are exactly five providers, all defined in
this same package, imported directly wherever they're needed
(`ingestion_orchestrator.py`, tests) — a five-line dict is simpler and
exactly as correct as reimplementing the same auto-registration machinery
a second time for a much smaller, fully-known set.
"""

from __future__ import annotations

from job_automation.ingestion.job_provider import JobProvider
from job_automation.ingestion.providers.indeed_provider import IndeedProvider
from job_automation.ingestion.providers.nhs_provider import NHSProvider
from job_automation.ingestion.providers.reed_provider import ReedProvider
from job_automation.ingestion.providers.totaljobs_provider import TotalJobsProvider
from job_automation.ingestion.providers.trac_provider import TracProvider

PROVIDER_REGISTRY: dict[str, type[JobProvider]] = {
    NHSProvider.source_name: NHSProvider,
    TracProvider.source_name: TracProvider,
    ReedProvider.source_name: ReedProvider,
    IndeedProvider.source_name: IndeedProvider,
    TotalJobsProvider.source_name: TotalJobsProvider,
}


def get_provider(source_name: str) -> JobProvider:
    """Construct the provider registered under `source_name`. Raises
    `KeyError` for an unknown name — callers (the ingestion orchestrator,
    tests) get a plain, immediate error rather than a provider silently
    resolving to `None`."""
    try:
        provider_cls = PROVIDER_REGISTRY[source_name]
    except KeyError:
        raise KeyError(
            f"No job provider registered as {source_name!r}. Registered: {sorted(PROVIDER_REGISTRY)}"
        ) from None
    return provider_cls()
