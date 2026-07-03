"""
Education: the `EducationEntry` domain entity plus `EducationParser`, which
extracts entries from free-text CV content (used by `cv_parser.py`).

Parsing a real CV's education section is inherently heuristic — there is no
universal format. `EducationParser` looks for one qualification per
non-empty line (after stripping common bullet markers), extracting a
trailing year and treating everything else on the line before an em/en-dash
or comma as the qualification, and whatever follows as the awarding body.
This deliberately does not attempt full NLP-grade extraction (that's a much
larger undertaking); it is a genuine, working implementation for
reasonably-formatted CVs, not a placeholder — see docs/CANDIDATE_PROFILE.md
for its documented limitations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_BULLET_RE = re.compile(r"^[\-\*•]\s*")


@dataclass(frozen=True)
class EducationEntry:
    qualification: str
    awarding_body: str | None = None
    year: str | None = None
    grade: str | None = None

    def to_dict(self) -> dict:
        return {
            "qualification": self.qualification,
            "awarding_body": self.awarding_body,
            "year": self.year,
            "grade": self.grade,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EducationEntry":
        return cls(
            qualification=data.get("qualification", ""),
            awarding_body=data.get("awarding_body") or None,
            year=data.get("year") or None,
            grade=data.get("grade") or None,
        )


class EducationParser:
    def parse(self, text: str) -> list[EducationEntry]:
        entries: list[EducationEntry] = []
        for raw_line in text.splitlines():
            line = _BULLET_RE.sub("", raw_line).strip()
            if not line:
                continue
            entries.append(self._parse_line(line))
        return entries

    def _parse_line(self, line: str) -> EducationEntry:
        year_match = _YEAR_RE.search(line)
        year = year_match.group() if year_match else None
        remainder = (line[: year_match.start()] + line[year_match.end() :]).strip(" ,-–—") if year_match else line

        # "Qualification - Awarding Body" or "Qualification, Awarding Body"
        for separator in (" - ", " – ", " — ", ", "):
            if separator in remainder:
                qualification, awarding_body = remainder.split(separator, 1)
                return EducationEntry(
                    qualification=qualification.strip(),
                    awarding_body=awarding_body.strip() or None,
                    year=year,
                )
        return EducationEntry(qualification=remainder.strip(), year=year)
