"""
CLI entry point to generate today's report.

Will call reports.daily_report.generate_daily_report(date.today()). Intended
to run at the end of each day, after run_scraper.py and
generate_applications.py, e.g. as the last step of a scheduled task chain.
"""
