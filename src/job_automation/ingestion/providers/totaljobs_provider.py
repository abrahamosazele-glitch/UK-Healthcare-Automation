"""
`JobProvider` stub for TotalJobs.

Same reasoning as `indeed_provider.py`: TotalJobs' Terms of Service
prohibit automated scraping, it runs anti-bot protections, and it has no
public jobseeker API. This provider implements the full `JobProvider`
contract so it slots into the registry/orchestrator/tests like a real
provider, but `fetch_jobs()` always raises `NotImplementedError` instead of
attempting a live request.

To make this provider real in future, replace `fetch_jobs()`'s body with a
call to a compliant data source (a TotalJobs partner/feed agreement, or a
licensed third-party aggregator API), normalizing its response into
`ParsedJob`s and persisting via `JobIngestionService`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats


class TotalJobsProvider(JobProvider):
    source_name = "totaljobs"

    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        raise NotImplementedError(
            "TotalJobsProvider has no compliant data source configured. TotalJobs' Terms "
            "of Service prohibit automated scraping and it offers no public jobseeker "
            "API — wire this up against an official partner feed or a licensed aggregator "
            "API before enabling this provider."
        )
