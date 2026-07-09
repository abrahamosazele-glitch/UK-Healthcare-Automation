"""
`JobProvider` stub for individual employer/company career pages.

Unlike every other provider in this package, "company career pages" isn't
one site with one DOM shape — it's potentially hundreds of independent
employer websites (individual care homes, domiciliary care agencies,
NHS-adjacent private providers), each with its own markup, its own
robots.txt, and its own Terms of Service. There is no single scraper that
can responsibly cover "company career pages" in general the way
`NHSScraper`/`TracScraper` cover one named site each.

This provider is therefore a stub, not a real implementation: `fetch_jobs()`
always raises `NotImplementedError`. To make a *specific* employer's career
page real:

1. Check that employer's `robots.txt` and Terms of Service actually permit
   automated access to their careers/vacancies pages (the same check this
   codebase's NHS/Trac scrapers were built against — see
   docs/JOB_INGESTION.md's compliance notes).
2. Write a dedicated scraper module under `scrapers/<employer_slug>/`
   following the exact `BaseScraper`/`BaseSearch`/`BaseParser`/
   `BasePaginator` composition `scrapers/nhs/` and `scrapers/trac/` already
   use — reuse that framework rather than writing bespoke Playwright code.
3. Add a `JobProvider` subclass for that one employer (not a generic
   "CompanyCareerPageProvider" instance) with its own `source_name`, and
   register it in `provider_registry.py`.
4. Apply the same rate-limiting (`core.rate_limiter.RateLimiter`) every
   other scraper already uses — never increase request frequency to "catch
   up" on a large careers page.

This class exists only so a career-page source shows up in the registry as
a clearly-disabled placeholder (for discoverability and tests), not as a
generic multi-employer scraper — there is deliberately no `base_url`
parameter here, since one doesn't generalize across arbitrary employer
sites.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats


class CompanyCareerPageProvider(JobProvider):
    source_name = "company_career_pages"

    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        raise NotImplementedError(
            "CompanyCareerPageProvider is a placeholder, not a generic scraper — there is no "
            "single 'company career pages' site to fetch from. Each employer needs its own "
            "robots.txt/Terms-of-Service check and its own dedicated JobProvider subclass "
            "(see this module's docstring for the steps) before it can run."
        )
