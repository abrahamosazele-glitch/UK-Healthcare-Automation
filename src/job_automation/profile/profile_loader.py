"""
Loads a candidate profile file into the intermediate dict shape that
`CandidateProfile.from_dict()` expects — supporting JSON, YAML, and a
structured Markdown format now, with PDF and DOCX loader *interfaces*
defined for a future milestone (see docs/CANDIDATE_PROFILE.md's "Future PDF
parsing" section for why they raise `NotImplementedError` rather than being
implemented here: no PDF/DOCX-reading library is justified as a dependency
until that milestone actually needs one, and stubbing the interface now
means a future implementation slots in without any caller-side changes).

Every loader returns the *same* plain dict shape (matching
`CandidateProfile.to_dict()`'s output) — `ProfileLoader` is the one
abstraction every format converges on, so `ProfileService`/`ProfileRepository`
never need to know which format a given profile came from.

This is a different concern from `cv_parser.py`: `ProfileLoader`s read a
file *already* in this system's own structured schema; `CVParser` reads a
real, prose-style CV that was never written with this schema in mind.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path

import yaml

from job_automation.profile.certificate_parser import CertificateParser
from job_automation.profile.employment_history import EmploymentHistoryParser

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_KEY_VALUE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 ]*?)\s*:\s*(.*)$")
_BULLET_RE = re.compile(r"^[\-\*•]\s*")

#: Markdown section headings -> the dict key each maps to.
_SECTION_KEYS = {
    "personal information": "personal_information",
    "education": "education",
    "employment history": "employment_history",
    "skills": "skills",
    "certificates": "certificates",
    "professional registrations": "professional_registrations",
    "languages": "languages",
    "visa status": "visa_status",
    "preferences": "preferences",
    "career goals": "career_goals",
    "availability": "availability",
    "keywords": "keywords",
}

#: Which top-level keys hold a flat bullet list of strings, vs. a key-value
#: block, vs. entries needing their own line-parser.
_FLAT_LIST_SECTIONS = {"skills", "career_goals", "keywords"}
_KEY_VALUE_SECTIONS = {"personal_information", "visa_status", "preferences"}

#: Which key-value keys should become a comma-split list rather than a
#: scalar string.
_LIST_VALUED_KEYS = {"preferred_locations", "preferred_employers"}
#: Which key-value keys should be interpreted as a boolean.
_BOOLEAN_KEYS = {"right_to_work_uk", "sponsorship_required", "visa_sponsorship_required"}
#: Which key-value keys should be interpreted as a number.
_NUMERIC_KEYS = {"preferred_salary_min", "max_travel_distance_miles"}


class ProfileLoader(ABC):
    @abstractmethod
    def load(self, path: Path) -> dict:
        """Parse `path` into a dict matching `CandidateProfile.to_dict()`'s
        shape. Raise a clear exception (not return partial/garbage data) if
        the file can't be parsed at all."""


class JSONProfileLoader(ProfileLoader):
    def load(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))


class YAMLProfileLoader(ProfileLoader):
    def load(self, path: Path) -> dict:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}


class MarkdownProfileLoader(ProfileLoader):
    """Expects `## Section Name` headings matching `_SECTION_KEYS` (case-
    insensitive). See docs/CANDIDATE_PROFILE.md's profile schema section for
    a full example file, or `tests/fixtures/profile/candidate_profile.md`."""

    def __init__(
        self,
        *,
        employment_history_parser: EmploymentHistoryParser | None = None,
        certificate_parser: CertificateParser | None = None,
    ) -> None:
        self._employment_history_parser = employment_history_parser or EmploymentHistoryParser()
        self._certificate_parser = certificate_parser or CertificateParser()

    def load(self, path: Path) -> dict:
        sections = self._split_sections(path.read_text(encoding="utf-8"))
        data: dict = {}

        if "personal_information" in sections:
            data["personal_information"] = self._parse_key_value_block(sections["personal_information"])
        if "education" in sections:
            data["education"] = self._parse_education(sections["education"])
        if "employment_history" in sections:
            data["employment_history"] = [
                entry.to_dict() for entry in self._employment_history_parser.parse(sections["employment_history"])
            ]
        for flat_key in _FLAT_LIST_SECTIONS:
            if flat_key in sections:
                data[flat_key] = self._parse_bullet_list(sections[flat_key])
        if "certificates" in sections:
            data["certificates"] = [
                cert.to_dict() for cert in self._certificate_parser.parse(sections["certificates"])
            ]
        if "professional_registrations" in sections:
            data["professional_registrations"] = self._parse_registrations(sections["professional_registrations"])
        if "languages" in sections:
            data["languages"] = self._parse_languages(sections["languages"])
        if "visa_status" in sections:
            data["visa_status"] = self._parse_key_value_block(sections["visa_status"])
        if "preferences" in sections:
            data["preferences"] = self._parse_key_value_block(sections["preferences"])
        if "availability" in sections:
            data["availability"] = sections["availability"].strip() or None

        return data

    def _split_sections(self, text: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current_key: str | None = None
        for line in text.splitlines():
            heading_match = _HEADING_RE.match(line)
            if heading_match:
                heading = heading_match.group(1).strip().lower()
                current_key = _SECTION_KEYS.get(heading)
                if current_key is not None:
                    sections.setdefault(current_key, [])
                continue
            if current_key is not None:
                sections[current_key].append(line)
        return {key: "\n".join(lines).strip() for key, lines in sections.items()}

    def _parse_key_value_block(self, text: str) -> dict:
        result: dict = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _KEY_VALUE_RE.match(line)
            if not match:
                continue
            key = match.group(1).strip().lower().replace(" ", "_")
            value = match.group(2).strip()
            result[key] = self._coerce_value(key, value)
        return result

    def _coerce_value(self, key: str, value: str) -> object:
        if key in _BOOLEAN_KEYS:
            return value.strip().lower() in ("true", "yes")
        if key in _NUMERIC_KEYS:
            try:
                return float(value) if "." in value else int(value)
            except ValueError:
                return None
        if key in _LIST_VALUED_KEYS:
            return [item.strip() for item in value.split(",") if item.strip()]
        return value or None

    def _parse_bullet_list(self, text: str) -> list[str]:
        items: list[str] = []
        for raw_line in text.splitlines():
            line = _BULLET_RE.sub("", raw_line).strip()
            if line:
                items.append(line)
        return items

    def _parse_education(self, text: str) -> list[dict]:
        from job_automation.profile.education_parser import EducationParser

        return [entry.to_dict() for entry in EducationParser().parse(text)]

    def _parse_registrations(self, text: str) -> list[dict]:
        """Format: "<Body>: <number> (expires <date>, status: <status>)"."""
        registrations: list[dict] = []
        for raw_line in text.splitlines():
            line = _BULLET_RE.sub("", raw_line).strip()
            if not line or ":" not in line:
                continue
            body, remainder = line.split(":", 1)
            remainder = remainder.strip()
            expiry_match = re.search(r"expires?\s*:?\s*([^,\)]+)", remainder, re.IGNORECASE)
            status_match = re.search(r"status\s*:?\s*([^,\)]+)", remainder, re.IGNORECASE)
            number = re.split(r"\(", remainder, maxsplit=1)[0].strip()
            registrations.append(
                {
                    "body": body.strip(),
                    "registration_number": number,
                    "expiry_date": expiry_match.group(1).strip() if expiry_match else None,
                    "status": status_match.group(1).strip() if status_match else None,
                }
            )
        return registrations

    def _parse_languages(self, text: str) -> list[dict]:
        """Format: "<Language>: <proficiency>" per bullet line."""
        languages: list[dict] = []
        for raw_line in text.splitlines():
            line = _BULLET_RE.sub("", raw_line).strip()
            if not line:
                continue
            if ":" in line:
                language, proficiency = line.split(":", 1)
                languages.append({"language": language.strip(), "proficiency": proficiency.strip() or None})
            else:
                languages.append({"language": line, "proficiency": None})
        return languages


class PDFProfileLoader(ProfileLoader):
    """Future interface — not implemented in this milestone. No PDF-reading
    library (e.g. pdfplumber) is a project dependency yet; adding one is
    only justified once this loader is actually built. Implementing this
    later requires no changes to `ProfileService`/`ProfileRepository` or any
    other loader — that's the point of the shared `ProfileLoader` interface."""

    def load(self, path: Path) -> dict:
        raise NotImplementedError(
            "PDF profile loading is not implemented yet — see docs/CANDIDATE_PROFILE.md"
        )


class DOCXProfileLoader(ProfileLoader):
    """Future interface — not implemented in this milestone. See
    `PDFProfileLoader`'s docstring; the same reasoning applies with
    python-docx as the not-yet-justified dependency."""

    def load(self, path: Path) -> dict:
        raise NotImplementedError(
            "DOCX profile loading is not implemented yet — see docs/CANDIDATE_PROFILE.md"
        )


_LOADERS_BY_SUFFIX: dict[str, type[ProfileLoader]] = {
    ".json": JSONProfileLoader,
    ".yaml": YAMLProfileLoader,
    ".yml": YAMLProfileLoader,
    ".md": MarkdownProfileLoader,
    ".markdown": MarkdownProfileLoader,
    ".pdf": PDFProfileLoader,
    ".docx": DOCXProfileLoader,
}


def get_loader_for_path(path: Path) -> ProfileLoader:
    """Pick a loader by file extension. Raises `ValueError` for an
    unrecognized extension rather than guessing."""
    loader_cls = _LOADERS_BY_SUFFIX.get(path.suffix.lower())
    if loader_cls is None:
        raise ValueError(f"No ProfileLoader registered for file extension {path.suffix!r}")
    return loader_cls()
