"""
Builds and saves the daily summary report.

Will implement `generate_daily_report(date) -> Path`: query db_manager for
jobs scraped, new jobs saved, and applications created/updated that day,
render report_templates/daily_report_template.html via Jinja2 (and/or a CSV
export via pandas), save under data/reports/<date>.html, and optionally email
it to REPORT_RECIPIENT_EMAIL from config.settings.
"""
