"""
Scheduled task: import job listings from a local JSON fixture file —
**never** a live website. Reuses `JobIngestionService` (built for the NHS
scraper milestone) exactly as-is: this task's only job is turning fixture
JSON rows into `ParsedJob`s, the same generic type every real scraper
(NHS, and any future site) already produces.

`settings.scheduler_fixture_jobs_path` points at
`data/fixtures/local_jobs.json` by default — a small, committed, synthetic
dataset (not scraped from anywhere) that exists purely so this task has
safe, deterministic, offline data to import on every run. This is
deliberately simpler than re-running `NHSScraper` against the local
fixture HTTP server (`tests/fixtures/nhs/` + Playwright): a scheduled
background task recurring every few minutes shouldn't launch a full
browser process each time, and the NHS scraper's own mechanics are already
verified by `tests/test_nhs_scraper.py` — this task only needs to prove
"scheduled ingestion into the existing pipeline works," not re-verify
scraping itself.

Publishes a `JOB_IMPORTED` event with the run's summary (never calls
`NotificationService` directly — see `notifications.events`'s module
docstring), using the shared `event_bus` singleton directly for the same
reason `run_ai_matching.py` does: this is a plain function, not a service
class, so there's no constructor to inject through. The listener that
turns this into a notification (`notification_listeners
._on_job_imported`) is what decides a no-op run (`created == updated == 0`)
isn't worth surfacing — this task always publishes, keeping that judgment
call in exactly one place.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from job_automation.config.settings import settings
from job_automation.database.services.job_ingestion_service import JobIngestionService
from job_automation.notifications.event_bus import event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.scrapers.base.base_parser import ParsedJob
from job_automation.utils.logger import logger

SOURCE_SITE = "local_fixture"


def run(session: Session) -> dict:
    path = settings.scheduler_fixture_jobs_path
    if not path.exists():
        raise FileNotFoundError(f"Fixture jobs file not found: {path}")

    rows = json.loads(path.read_text(encoding="utf-8"))
    service = JobIngestionService(session, source_site=SOURCE_SITE)

    created = 0
    updated = 0
    for row in rows:
        parsed = _row_to_parsed_job(row)
        result = service.save_parsed_job(parsed)
        if result.created:
            created += 1
        else:
            updated += 1

    logger.info("import_fixture_jobs: {} seen, {} created, {} updated", len(rows), created, updated)
    summary = {"jobs_seen": len(rows), "jobs_created": created, "jobs_updated": updated}
    event_bus.publish(Event(event_type=EventType.JOB_IMPORTED, payload=summary), session)
    return summary


def _row_to_parsed_job(row: dict) -> ParsedJob:
    return ParsedJob(
        title=row["title"],
        employer=row.get("employer"),
        employer_url=row.get("employer_url"),
        salary=row.get("salary"),
        band=row.get("band"),
        location=row.get("location"),
        contract_type=row.get("contract_type"),
        hours=row.get("hours"),
        visa_sponsorship=row.get("visa_sponsorship"),
        posted_date=_parse_date(row.get("posted_date")),
        closing_date=_parse_date(row.get("closing_date")),
        job_url=row.get("job_url"),
        reference_number=row.get("reference_number"),
        description=row.get("description"),
        requirements=row.get("requirements", []),
        benefits=row.get("benefits", []),
    )


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
