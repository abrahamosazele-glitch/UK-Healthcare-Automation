"""
Parses a real, prose-style CV document (plain text or Markdown) into
partial profile data — distinct from `profile_loader.py`, which loads a
file *already* in this system's own structured schema (JSON/YAML/
Markdown-with-known-headings). `CVParser` is for the messier case: a
candidate's actual CV, with free-form section headings and prose bullet
points, that has to be split into sections and interpreted heuristically.

Section detection matches common heading variants ("Experience"/"Work
History"/"Employment", "Education"/"Qualifications", "Skills") case-
insensitively. Delegates the actual per-section parsing to the same
`EmploymentHistoryParser`/`EducationParser`/`CertificateParser` that
`profile_loader.py`'s Markdown loader also uses — one implementation of
each per-section format, not two. This is a genuine, working implementation
for reasonably-well-structured CVs (headings on their own line), not a
placeholder — see docs/CANDIDATE_PROFILE.md for documented limitations (it
will not extract anything sensible from a heavily-designed, columnar, or
image-based CV layout, which is a fundamentally different problem from text
parsing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from job_automation.profile.certificate_parser import Certificate, CertificateParser
from job_automation.profile.education_parser import EducationEntry, EducationParser
from job_automation.profile.employment_history import EmploymentEntry, EmploymentHistoryParser
from job_automation.profile.skills_extractor import SkillsExtractor

_SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "experience": ("experience", "work history", "employment history", "employment"),
    "education": ("education", "qualifications", "academic background"),
    "skills": ("skills", "key skills", "core skills"),
    "certificates": ("certificates", "certifications", "training"),
}

_HEADING_LINE_RE = re.compile(r"^#{0,3}\s*([A-Za-z &]+?)\s*:?\s*$")
_BULLET_RE = re.compile(r"^[\-\*•]\s*")


@dataclass
class CVParseResult:
    employment_history: list[EmploymentEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    certificates: list[Certificate] = field(default_factory=list)


class CVParser:
    def __init__(
        self,
        *,
        employment_history_parser: EmploymentHistoryParser | None = None,
        education_parser: EducationParser | None = None,
        certificate_parser: CertificateParser | None = None,
        skills_extractor: SkillsExtractor | None = None,
    ) -> None:
        self._employment_history_parser = employment_history_parser or EmploymentHistoryParser()
        self._education_parser = education_parser or EducationParser()
        self._certificate_parser = certificate_parser or CertificateParser()
        self._skills_extractor = skills_extractor or SkillsExtractor()

    def parse(self, text: str) -> CVParseResult:
        sections = self._split_sections(text)

        employment_history = self._employment_history_parser.parse(sections.get("experience", ""))
        education = self._education_parser.parse(sections.get("education", ""))
        certificates = self._certificate_parser.parse(sections.get("certificates", ""))

        explicit_skills = self._parse_skill_list(sections.get("skills", ""))
        skills = self._skills_extractor.normalize(explicit_skills)
        # Also scan the whole document for taxonomy terms mentioned in prose
        # (job responsibilities, a personal statement) that wouldn't appear
        # in an explicit skills list.
        for tag in self._skills_extractor.extract_from_text(text):
            if tag not in skills:
                skills.append(tag)

        return CVParseResult(
            employment_history=employment_history,
            education=education,
            skills=skills,
            certificates=certificates,
        )

    def _split_sections(self, text: str) -> dict[str, str]:
        """Split the document into named sections by matching each line
        against known heading variants. Text before the first recognized
        heading is discarded (typically contact details / a title) — that's
        not something this parser is responsible for extracting."""
        heading_to_key = {
            variant: key for key, variants in _SECTION_HEADINGS.items() for variant in variants
        }

        sections: dict[str, list[str]] = {}
        current_key: str | None = None
        for line in text.splitlines():
            heading_match = _HEADING_LINE_RE.match(line.strip())
            if heading_match:
                candidate_heading = heading_match.group(1).strip().lower()
                if candidate_heading in heading_to_key:
                    current_key = heading_to_key[candidate_heading]
                    sections.setdefault(current_key, [])
                    continue
            if current_key is not None:
                sections[current_key].append(line)

        return {key: "\n".join(lines).strip() for key, lines in sections.items()}

    def _parse_skill_list(self, text: str) -> list[str]:
        skills: list[str] = []
        for raw_line in text.splitlines():
            line = _BULLET_RE.sub("", raw_line).strip()
            if not line:
                continue
            # Support comma-separated skills on one line, as well as one per line.
            skills.extend(part.strip() for part in line.split(",") if part.strip())
        return skills
