"""
Scraper for Reed.co.uk healthcare/care listings.

Will subclass `job_automation.scrapers.base.BaseScraper` (site_name =
"reed") against Reed's public job search API where possible (Reed offers a
free API with registration, preferable to HTML scraping), falling back to
BaseSearch/BaseParser/BasePaginator against the HTML site otherwise. Not
implemented yet.
"""
