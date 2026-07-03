"""
Live production verification runbook — run this ONLY on a machine with real
internet access (this project's own sandbox has none; see
docs/JOB_INGESTION.md's "Manual live verification" section for how that was
discovered). Everything else in this codebase is verified against local
fixtures/mocks; this script is the one place that talks to real NHS Jobs,
Trac Jobs, and Reed, and writes real rows into whatever `DATABASE_URL`
your `.env` currently points at.

What it does, in order:
  1. Prints the exact database and provider list it's about to use, and
     refuses to do anything unless you pass --yes — this writes real data,
     possibly to a real production database, and should never run by
     accident.
  2. A quick reachability check per configured provider (plain HTTP GET,
     short timeout) — separates "site unreachable" from "site reachable
     but the scraper's selectors are stale," which is a much faster
     diagnosis than reading a full scrape failure.
  3. Runs the exact same `scheduler.tasks.import_provider_jobs.run()`
     function the daily scheduled task and the dashboard's manual "Import
     now" button both call — real ingestion, real auto-match, real
     JOB_IMPORTED notification. Not a reimplementation of that logic.
  4. Prints a final report: per-provider stats, any provider errors, and
     the resulting total row counts by source, so you can compare against
     what the dashboard shows next.

Usage:
    python scripts/verify_live_production.py --yes
    python scripts/verify_live_production.py --yes --providers nhs_jobs,reed
    python scripts/verify_live_production.py --yes --skip-match

Before running:
  - NHS Jobs: no configuration needed, but check jobs.nhs.uk's current
    robots.txt/terms before scraping, and expect to update
    scrapers/nhs/nhs_parser.py's selectors if the site has changed.
  - Trac Jobs: set TRAC_JOBS_BASE_URL in .env to one real trust's Trac
    Jobs site (e.g. https://example-trust.trac.jobs) — there is no single
    national Trac Jobs search the way NHS Jobs has one. Same
    robots.txt/selector caveat as above.
  - Reed: set REED_API_KEY in .env (register at
    https://www.reed.co.uk/developers/jobseeker).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from sqlalchemy import func, select

from job_automation.config.logging_config import setup_logging
from job_automation.config.settings import settings
from job_automation.database.db_manager import get_session
from job_automation.database.models import Job
from job_automation.utils.logger import logger

PROVIDER_REACHABILITY_URLS = {
    "nhs_jobs": "https://www.jobs.nhs.uk",
    "trac_jobs": settings.trac_jobs_base_url,
    "reed": "https://www.reed.co.uk/api/1.0/search",
}


def check_reachability(provider_names: list[str]) -> None:
    logger.info("--- Reachability check ---")
    for name in provider_names:
        url = PROVIDER_REACHABILITY_URLS.get(name)
        if not url:
            logger.warning("{}: no URL configured to check (see TRAC_JOBS_BASE_URL) — skipping", name)
            continue
        try:
            response = httpx.get(url, timeout=10.0, follow_redirects=True)
            logger.info("{}: reachable ({} -> HTTP {})", name, url, response.status_code)
        except httpx.HTTPError as exc:
            logger.error("{}: UNREACHABLE ({} -> {})", name, url, exc)


def print_job_counts_by_source() -> None:
    with get_session() as session:
        rows = session.execute(select(Job.source_site, func.count()).group_by(Job.source_site)).all()
    logger.info("--- Jobs currently in the database, by source ---")
    if not rows:
        logger.info("(no jobs in the database)")
    for source_site, count in rows:
        logger.info("{}: {}", source_site, count)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--yes", action="store_true", help="Required. Confirms you intend to write real data to DATABASE_URL."
    )
    parser.add_argument(
        "--providers",
        default=None,
        help="Comma-separated provider list to run (default: settings.job_ingestion_providers).",
    )
    parser.add_argument(
        "--skip-match", action="store_true", help="Skip AI auto-matching of newly imported jobs (no Anthropic spend)."
    )
    args = parser.parse_args()

    providers = args.providers.split(",") if args.providers else list(settings.job_ingestion_providers)

    print(f"DATABASE_URL   = {settings.database_url}")
    print(f"Providers      = {providers}")
    print(f"Skip AI match  = {args.skip_match}")
    if not args.yes:
        print("\nRefusing to proceed without --yes (this writes real data). Re-run with --yes to continue.")
        return 1

    setup_logging()
    logger.info("=== Live production verification starting ===")

    check_reachability(providers)

    if args.skip_match:
        from job_automation.ingestion.ingestion_orchestrator import run_ingestion

        with get_session() as session:
            result = run_ingestion(session, providers=providers)
            summary = result.to_summary_dict()
    else:
        from job_automation.scheduler.tasks import import_provider_jobs

        with get_session() as session:
            summary = import_provider_jobs.run(session)

    logger.info("--- Ingestion summary ---")
    for key, value in summary.items():
        logger.info("{}: {}", key, value)

    print_job_counts_by_source()

    logger.info("=== Live production verification finished ===")
    logger.info(
        "Next: open the dashboard in a browser and confirm the Jobs page, "
        "Dashboard job-ingestion widgets, and AI matches all reflect this run."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
