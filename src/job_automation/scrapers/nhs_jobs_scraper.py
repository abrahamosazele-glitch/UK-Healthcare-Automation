"""
Scraper for NHS Jobs (jobs.nhs.uk).

Will subclass `job_automation.scrapers.base.BaseScraper` (site_name =
"nhs_jobs") and compose site-specific BaseSearch/BaseParser/BasePaginator
implementations (results are rendered client-side, so likely no login is
needed for search, only for later application submission via BaseLogin/
BaseApplication). Extracts listings matching the configured healthcare role
keywords and locations. Not implemented yet.
"""
