"""
Employment history: the `EmploymentEntry` domain entity plus analysis
functions (total experience, healthcare-role detection, gap detection).

Kept as functions operating on `Sequence[EmploymentEntry]` rather than
methods on `CandidateProfile` itself — this is a distinct responsibility
(analyzing a work history) from the `CandidateProfile` aggregate's job of
just holding the data, and it lets `profile_validator.py` reuse
`detect_gaps()`/`is_healthcare_role()` without needing a whole profile.

Dates are kept as plain strings (`YYYY-MM-DD` expected, but not enforced at
the dataclass level) rather than `datetime.date`, since real-world CVs and
hand-edited profile files often have partial dates ("2022", "March 2022") —
`_parse_date()` here is the one place that tries to make sense of them for
gap/duration calculations, tolerating what it can't parse rather than
raising, since a badly-formatted date shouldn't crash the whole analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

_BULLET_RE = re.compile(r"^[\-\*•]\s*")
_TITLE_LINE_START_RE = re.compile(r"^[A-Z]")
_TRAILING_DATES_RE = re.compile(r"\(([^)]+)\)\s*$")

_HEALTHCARE_KEYWORDS = (
    "nhs",
    "trust",
    "hospital",
    "care home",
    "care ltd",
    "healthcare",
    "clinic",
    "surgery",
    "hospice",
    "ward",
)

_YEAR_RE = re.compile(r"(19|20)\d{2}")
_GAP_WARNING_THRESHOLD_DAYS = 182  # ~6 months


@dataclass(frozen=True)
class EmploymentEntry:
    job_title: str
    employer: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None  # None/empty = current role
    responsibilities: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "job_title": self.job_title,
            "employer": self.employer,
            "location": self.location,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "responsibilities": list(self.responsibilities),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EmploymentEntry":
        return cls(
            job_title=data.get("job_title", ""),
            employer=data.get("employer") or None,
            location=data.get("location") or None,
            start_date=data.get("start_date") or None,
            end_date=data.get("end_date") or None,
            responsibilities=tuple(data.get("responsibilities", [])),
        )


@dataclass(frozen=True)
class EmploymentGap:
    after: EmploymentEntry
    before: EmploymentEntry
    approximate_days: int


def is_healthcare_role(entry: EmploymentEntry) -> bool:
    """Heuristic: does this entry's employer/title/responsibilities suggest
    a healthcare setting? Used to derive "Healthcare Experience" as a
    filtered view of employment history rather than a separately
    maintained (and easily out-of-sync) list."""
    haystack = " ".join(
        [entry.employer or "", entry.job_title, " ".join(entry.responsibilities)]
    ).lower()
    return any(keyword in haystack for keyword in _HEALTHCARE_KEYWORDS)


def healthcare_experience(entries: Sequence[EmploymentEntry]) -> list[EmploymentEntry]:
    return [entry for entry in entries if is_healthcare_role(entry)]


def total_years_of_experience(entries: Sequence[EmploymentEntry]) -> float:
    """Sum of each entry's approximate duration in years. Entries whose
    dates can't be parsed at all contribute 0 rather than raising — a
    badly-formatted date shouldn't prevent scoring the rest of the profile."""
    total_days = 0
    for entry in entries:
        start = _parse_date(entry.start_date)
        end = _parse_date(entry.end_date) or date.today()
        if start is not None and end >= start:
            total_days += (end - start).days
    return round(total_days / 365.25, 1)


def detect_gaps(entries: Sequence[EmploymentEntry]) -> list[EmploymentGap]:
    """Sort entries by start date and flag any gap longer than ~6 months
    between the end of one role and the start of the next. Entries with
    unparseable dates are skipped (can't be placed on the timeline), not
    treated as errors here — `profile_validator.py` reports missing dates
    separately."""
    dated_entries = [(entry, _parse_date(entry.start_date)) for entry in entries]
    dated_entries = [(entry, start) for entry, start in dated_entries if start is not None]
    dated_entries.sort(key=lambda pair: pair[1])

    gaps: list[EmploymentGap] = []
    for (previous_entry, _), (next_entry, next_start) in zip(dated_entries, dated_entries[1:]):
        previous_end = _parse_date(previous_entry.end_date) or date.today()
        gap_days = (next_start - previous_end).days
        if gap_days > _GAP_WARNING_THRESHOLD_DAYS:
            gaps.append(EmploymentGap(after=previous_entry, before=next_entry, approximate_days=gap_days))
    return gaps


def _parse_date(value: str | None) -> date | None:
    """Best-effort date parsing: full ISO date, or falls back to just the
    year (treated as 1 January) if that's all that's given."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    match = _YEAR_RE.search(value)
    if match:
        return date(int(match.group()), 1, 1)
    return None


class EmploymentHistoryParser:
    """Parses free-text employment history into `EmploymentEntry` objects.
    Expected format per entry: a title line ("Job Title, Employer (Start –
    End)" or "Job Title - Employer (Start - End)"), followed by zero or more
    bulleted responsibility lines, e.g.:

        Healthcare Assistant, Example NHS Trust (2022 - Present)
        - Provided patient care and support on a busy ward
        - Assisted with moving and handling of patients

    Shared by `cv_parser.py` (parsing a real CV's experience section) and
    `profile_loader.py`'s Markdown loader (parsing an "Employment History"
    section) — one implementation, not two."""

    def parse(self, text: str) -> list[EmploymentEntry]:
        entries: list[EmploymentEntry] = []
        current_title_line: str | None = None
        current_responsibilities: list[str] = []

        def flush() -> None:
            if current_title_line is not None:
                entries.append(self._parse_title_line(current_title_line, current_responsibilities))

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            is_new_entry = not raw_line.startswith((" ", "\t", "-", "*", "•")) and _TITLE_LINE_START_RE.match(
                line
            )
            if is_new_entry:
                flush()
                current_title_line = line
                current_responsibilities = []
            elif current_title_line is not None:
                current_responsibilities.append(_BULLET_RE.sub("", line).strip())
        flush()

        return entries

    def _parse_title_line(self, line: str, responsibilities: list[str]) -> EmploymentEntry:
        dates: str | None = None
        remainder = line
        date_match = _TRAILING_DATES_RE.search(line)
        if date_match:
            dates = date_match.group(1)
            remainder = line[: date_match.start()].strip()

        start_date = end_date = None
        if dates:
            # Require the separator to be padded by spaces so it doesn't
            # match a hyphen *inside* an ISO date like "2022-01-01" — only
            # " - " (a real range separator) should split the string, not
            # every "-" character.
            for separator in (" – ", " - ", " to "):
                if separator in dates:
                    start_date, end_date = (part.strip() for part in dates.split(separator, 1))
                    break
            else:
                start_date = dates.strip()

        for separator in (" - ", " – ", " — ", ", "):
            if separator in remainder:
                job_title, employer = remainder.split(separator, 1)
                return EmploymentEntry(
                    job_title=job_title.strip(),
                    employer=employer.strip() or None,
                    start_date=start_date,
                    end_date=end_date,
                    responsibilities=tuple(responsibilities),
                )
        return EmploymentEntry(
            job_title=remainder.strip(),
            start_date=start_date,
            end_date=end_date,
            responsibilities=tuple(responsibilities),
        )
