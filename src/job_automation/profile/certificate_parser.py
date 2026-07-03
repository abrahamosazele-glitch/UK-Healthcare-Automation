"""
Certificates: the `Certificate` domain entity plus `CertificateParser`,
which extracts entries from free-text CV content (used by `cv_parser.py`).

Looks for explicit "issued <date>" / "expires <date>" phrases (common on UK
healthcare CVs listing DBS checks, Manual Handling, Basic Life Support
certificates with validity periods) and falls back to treating a single
trailing year as the issue date when no explicit phrasing is found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BULLET_RE = re.compile(r"^[\-\*•]\s*")
# The captured date text allows "-" (so ISO dates like "2023-01-01" aren't
# truncated at their first hyphen) but the lookahead still stops at a comma
# or the next keyword, so it never runs into the following field.
_ISSUED_RE = re.compile(r"issued\s*:?\s*([A-Za-z0-9 ,\-]+?)(?=,|expires|$)", re.IGNORECASE)
_EXPIRES_RE = re.compile(r"expires?\s*:?\s*([A-Za-z0-9 ,\-]+?)(?=,|issued|$)", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass(frozen=True)
class Certificate:
    name: str
    issuing_body: str | None = None
    issued_date: str | None = None
    expiry_date: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "issuing_body": self.issuing_body,
            "issued_date": self.issued_date,
            "expiry_date": self.expiry_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Certificate":
        return cls(
            name=data.get("name", ""),
            issuing_body=data.get("issuing_body") or None,
            issued_date=data.get("issued_date") or None,
            expiry_date=data.get("expiry_date") or None,
        )


class CertificateParser:
    def parse(self, text: str) -> list[Certificate]:
        certificates: list[Certificate] = []
        for raw_line in text.splitlines():
            line = _BULLET_RE.sub("", raw_line).strip()
            if not line:
                continue
            certificates.append(self._parse_line(line))
        return certificates

    def _parse_line(self, line: str) -> Certificate:
        issued_match = _ISSUED_RE.search(line)
        expires_match = _EXPIRES_RE.search(line)

        issued_date = issued_match.group(1).strip() if issued_match else None
        expiry_date = expires_match.group(1).strip() if expires_match else None

        # Whatever's left before the first parenthesis/dash/comma is the name.
        name = re.split(r"[\(\-–—,]", line, maxsplit=1)[0].strip()

        if issued_date is None and expiry_date is None:
            year_match = _YEAR_RE.search(line)
            if year_match:
                issued_date = year_match.group()

        return Certificate(name=name, issued_date=issued_date, expiry_date=expiry_date)
