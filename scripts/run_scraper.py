"""
CLI entry point to run all scrapers once and save new jobs.

Will call scrapers.scraper_registry.run_all(), pass results through
deduplication.duplicate_checker.filter_new_jobs(), and persist via
database.db_manager.save_job(). Suitable for manual runs or wiring into
Windows Task Scheduler / cron for a daily cadence.
"""
