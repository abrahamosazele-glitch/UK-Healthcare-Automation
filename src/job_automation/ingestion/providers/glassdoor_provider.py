"""
`JobProvider` stub for Glassdoor.

Same reasoning as `indeed_provider.py`: Glassdoor's Terms of Service
prohibit automated scraping of its listings and it runs anti-bot
protections; it has no public jobseeker search API a job-import tool can
register for (its official API surface is employer-review/employer-branding
data, not a job-search feed). This provider implements the full
`JobProvider` contract so it slots into the registry/orchestrator/tests
like a real provider, but `fetch_jobs()` always raises `NotImplementedError`
instead of attempting a live request.

To make this provider real in future, replace `fetch_jobs()`'s body with a
call to a compliant data source (an official Glassdoor partner/feed
agreement, if one becomes available, or a licensed third-party aggregator
API), normalizing its response into `ParsedJob`s and persisting via
`JobIngestionService`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats


class GlassdoorProvider(JobProvider):
    source_name = "glassdoor"

    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        raise NotImplementedError(
            "GlassdoorProvider has no compliant data source configured. Glassdoor's Terms "
            "of Service prohibit automated scraping and it offers no public jobseeker "
            "search API — wire this up against an official partner feed or a licensed "
            "aggregator API before enabling this provider."
        )
