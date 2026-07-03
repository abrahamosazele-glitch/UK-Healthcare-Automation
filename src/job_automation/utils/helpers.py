"""Small generic utilities used by scrapers and the persistence layer."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

_SALARY_NUMBER_RE = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")


def utc_now() -> datetime:
    """Naive UTC "now" — not `datetime.now(timezone.utc)`. SQLite has no
    real timezone-aware storage: a value written with `tzinfo` set comes
    back naive on the next read regardless, and comparing a naive column
    against an aware Python value raises `TypeError: can't compare
    offset-naive and offset-aware datetimes` (hit directly in the
    Background Scheduler milestone's `cleanup_old_logs` bulk delete).
    Promoted here from `scheduler.scheduler_models` once the notifications
    subsystem needed the identical helper — naive UTC throughout matches
    how the rest of this codebase already handles timestamps (e.g.
    `core.screenshot_manager`, `documents.export_manager` both use naive
    `datetime.now()`)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def compute_content_hash(title: str, employer: str | None, location: str | None) -> str:
    """Hash of normalized (title, employer, location), used to catch the same
    role re-posted under a new external_id — see Job.content_hash."""
    normalized = "|".join((part or "").strip().lower() for part in (title, employer, location))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_salary_range(text: str | None) -> tuple[float | None, float | None, str | None]:
    """Parse a free-text UK salary string into (min, max, period).

    Handles the common formats seen on UK healthcare job boards:
    "£22,816 - £24,336 per annum", "£29,970 to £36,483 pro rata",
    "£13.50 - £15.00 an hour", "£13.50 per hour". Returns (None, None, None)
    if no £ amount is found — callers should keep the raw text alongside
    this (see Job.salary_raw) since real-world formatting varies more than
    any regex fully covers.
    """
    if not text:
        return None, None, None

    numbers = [float(match.replace(",", "")) for match in _SALARY_NUMBER_RE.findall(text)]
    if not numbers:
        return None, None, None

    salary_min = numbers[0]
    salary_max = numbers[1] if len(numbers) > 1 else numbers[0]

    lowered = text.lower()
    if "hour" in lowered:
        period = "per hour"
    elif "annum" in lowered or "year" in lowered:
        period = "per year"
    elif "week" in lowered:
        period = "per week"
    elif "day" in lowered:
        period = "per day"
    elif "session" in lowered:
        period = "per session"
    else:
        period = None

    if "pro rata" in lowered:
        period = f"{period} (pro rata)" if period else "pro rata"

    return salary_min, salary_max, period
