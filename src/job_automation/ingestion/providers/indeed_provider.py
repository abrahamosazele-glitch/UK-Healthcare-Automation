"""
`JobProvider` stub for Indeed UK.

Indeed's Terms of Service prohibit automated scraping of its search
results, and its anti-bot measures (JS challenges, IP-based rate limiting)
make an unofficial scraper unreliable even where technically possible.
Indeed does not offer a comparable public jobseeker API to Reed's. Per this
milestone's explicit scope decision, this provider is interface-only: the
`JobProvider` contract (`source_name`, `fetch_jobs()`) is fully implemented
so it slots into the registry/orchestrator/tests exactly like a real
provider, but `fetch_jobs()` always raises `NotImplementedError` rather
than attempting a live request.

To make this provider real in future, replace the body of `fetch_jobs()`
with a call to a compliant data source — e.g. Indeed's official Publisher/
XML feed program (requires a partner agreement) or a licensed third-party
job-board aggregation API — normalizing its response into `ParsedJob`s and
persisting via `JobIngestionService`, the same pattern every other real
provider in this package already follows.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats


class IndeedProvider(JobProvider):
    source_name = "indeed"

    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        raise NotImplementedError(
            "IndeedProvider has no compliant data source configured. Indeed's Terms of "
            "Service prohibit automated scraping and it offers no public jobseeker API "
            "comparable to Reed's — wire this up against an official partner feed "
            "(Indeed Publisher/XML program) or a licensed aggregator API before enabling "
            "this provider."
        )
