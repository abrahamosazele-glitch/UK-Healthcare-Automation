"""
Runs every configured `JobProvider` in turn and aggregates the results —
the single entry point `scheduler.tasks.import_provider_jobs` (scheduled)
and any future manual "Import now" trigger both call, the same "one
orchestrator, multiple callers" role `SchedulerService.run_task()` plays
for scheduled tasks generally.

A provider that fails entirely (raises, rather than reporting per-job
failures in its own `ProviderRunStats`) does not abort the run — NHS Jobs
failing to load shouldn't prevent Reed from still being tried, the same
"one bad card doesn't stop the whole page" tolerance
`BaseParser.parse_all()` already applies at a smaller scale. This is
deliberately how `IndeedProvider`/`TotalJobsProvider`'s `NotImplementedError`
is handled too: recorded in `provider_errors`, not raised out of
`run_ingestion()` — running the configured provider list should still
succeed for the providers that can actually run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.ingestion.job_provider import ProviderRunStats
from job_automation.ingestion.provider_registry import get_provider
from job_automation.utils.logger import logger


@dataclass
class IngestionRunResult:
    provider_stats: dict[str, ProviderRunStats] = field(default_factory=dict)
    #: source_name -> error message, for providers that failed to run at
    #: all (a stub's `NotImplementedError`, a missing API key, a network
    #: failure) — distinct from `ProviderRunStats.jobs_failed`, which
    #: counts individual listings that failed within an otherwise-successful
    #: run.
    provider_errors: dict[str, str] = field(default_factory=dict)
    newly_created_job_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def jobs_seen(self) -> int:
        return sum(stats.jobs_seen for stats in self.provider_stats.values())

    @property
    def jobs_created(self) -> int:
        return sum(stats.jobs_created for stats in self.provider_stats.values())

    @property
    def jobs_updated(self) -> int:
        return sum(stats.jobs_updated for stats in self.provider_stats.values())

    @property
    def jobs_failed(self) -> int:
        return sum(stats.jobs_failed for stats in self.provider_stats.values())

    def to_summary_dict(self) -> dict:
        """A small JSON-serializable summary — what
        `scheduler.tasks.import_provider_jobs` returns as its
        `SchedulerTaskRunRecord.result_summary` and what the `JOB_IMPORTED`
        notification's payload carries."""
        return {
            "providers_run": sorted(self.provider_stats),
            "providers_failed": sorted(self.provider_errors),
            "jobs_seen": self.jobs_seen,
            "jobs_created": self.jobs_created,
            "jobs_updated": self.jobs_updated,
            "jobs_failed": self.jobs_failed,
        }


def run_ingestion(session: Session, *, providers: list[str] | None = None) -> IngestionRunResult:
    """Run every provider in `providers` (defaulting to
    `settings.job_ingestion_providers`) against `session`, aggregating
    stats and newly-created job IDs. Never raises for an individual
    provider's failure — see this module's docstring."""
    source_names = providers if providers is not None else settings.job_ingestion_providers
    result = IngestionRunResult()

    for source_name in source_names:
        try:
            provider = get_provider(source_name)
            stats = provider.fetch_jobs(session)
        except Exception as exc:
            logger.error("Job provider {!r} failed to run: {}", source_name, exc)
            result.provider_errors[source_name] = str(exc)
            continue

        result.provider_stats[source_name] = stats
        result.newly_created_job_ids.extend(stats.newly_created_job_ids)
        logger.info(
            "Provider {!r}: {} seen, {} created, {} updated, {} failed",
            source_name,
            stats.jobs_seen,
            stats.jobs_created,
            stats.jobs_updated,
            stats.jobs_failed,
        )

    return result
