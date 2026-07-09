"""
`JobProvider` stub for CV-Library.

Same reasoning as `indeed_provider.py`. CV-Library does offer a jobseeker
API/data-feed program, but it's a paid partner arrangement requiring a
signed agreement and issued credentials — not something this codebase can
wire up without an actual account. This provider implements the full
`JobProvider` contract so it slots into the registry/orchestrator/tests
like a real provider, but `fetch_jobs()` always raises `NotImplementedError`
instead of attempting a live request or scraping the site directly (which
its Terms of Service also prohibit).

To make this provider real: register for CV-Library's job-feed/API partner
program (see https://www.cv-library.co.uk/recruiter/), configure the issued
API key/feed URL via settings (following the same pattern as
`settings.reed_api_key`), and replace `fetch_jobs()`'s body with a call to
that feed, normalizing its response into `ParsedJob`s and persisting via
`JobIngestionService` — `reed_provider.py` is the closest existing template
for "a real HTTP-API-backed provider," not a Playwright scraper.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats


class CVLibraryProvider(JobProvider):
    source_name = "cv_library"

    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        raise NotImplementedError(
            "CVLibraryProvider has no compliant data source configured. CV-Library's "
            "jobseeker feed/API is a paid partner program requiring a signed agreement — "
            "register at https://www.cv-library.co.uk/recruiter/, configure the issued "
            "API key/feed URL, and wire this provider up against it before enabling it. "
            "Scraping the site directly is prohibited by its Terms of Service."
        )
