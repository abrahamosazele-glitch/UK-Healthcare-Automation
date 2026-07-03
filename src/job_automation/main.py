"""
Top-level orchestration entry point.

Will wire the daily pipeline together in order:
  1. scrapers.*        -> fetch raw job listings from each configured site
  2. deduplication.*   -> filter out jobs already in the database
  3. database.db_manager -> persist new jobs
  4. ai.cv_generator / ai.cover_letter_generator -> produce tailored documents
     for each new matching job
  5. applications.application_tracker -> record application status
  6. reports.daily_report -> summarize the day's run

Intended to be invoked directly (`python -m job_automation.main`) or via
scheduler.job_scheduler for unattended daily runs.
"""
