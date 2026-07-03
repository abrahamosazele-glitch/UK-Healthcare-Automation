"""
Normalizes free-text skill phrases onto a fixed UK healthcare skills
taxonomy, and can scan arbitrary text (a CV body, a job description) for
taxonomy terms.

The taxonomy is intentionally a flat set of canonical tags with a list of
synonym phrases each — not an ML/embedding-based classifier. This mirrors
the same design choice made in `job_automation.ai.rule_engine`: simple,
deterministic, explainable matching now, with semantic matching being a
job for an LLM step layered on top later if needed (see
docs/CANDIDATE_PROFILE.md's extension points) rather than built into this
normalization step.
"""

from __future__ import annotations

from typing import Iterable

#: Canonical tag -> phrases that should normalize to it. Order matters only
#: in that longer/more specific phrases should be checked before shorter
#: ones that might be substrings of them — see `_ORDERED_TAGS`.
SKILL_TAXONOMY: dict[str, tuple[str, ...]] = {
    "communication": ("communication", "communicating", "interpersonal skills"),
    "patient_care": ("patient care", "personal care", "caring for patients"),
    "safeguarding": ("safeguarding", "child protection", "adult protection"),
    "moving_and_handling": ("moving and handling", "manual handling"),
    "medication": ("medication", "medicine administration", "drug administration"),
    "observation": ("observation", "vital signs", "monitoring patients", "obs and monitoring"),
    "documentation": ("documentation", "record keeping", "care plans", "care planning"),
    "electronic_patient_records": (
        "electronic patient records",
        "electronic patient record",
        "epr",
        "electronic health record",
    ),
    "leadership": ("leadership", "supervising staff", "team leadership", "managing a team"),
    "nhs_experience": ("nhs experience", "nhs", "national health service"),
    "care_experience": ("care experience", "care home", "residential care", "care sector"),
    "mental_health": ("mental health", "psychiatric care", "psychiatric"),
    "learning_disability": ("learning disability", "learning disabilities"),
    "dementia": ("dementia care", "dementia", "alzheimer's", "alzheimers"),
    "community_care": ("community care", "home care", "domiciliary care"),
}

# Longest synonym phrases first, so e.g. "nhs experience" matches the
# nhs_experience tag before the bare substring "nhs" could match something
# else first.
_ORDERED_TAGS: list[tuple[str, str]] = sorted(
    ((phrase, tag) for tag, phrases in SKILL_TAXONOMY.items() for phrase in phrases),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


class SkillsExtractor:
    def normalize(self, raw_skills: Iterable[str]) -> list[str]:
        """Map each free-text skill phrase onto a taxonomy tag where one
        matches; phrases that don't match any known tag are kept as-is
        (lowercased, stripped) rather than dropped — an unrecognized skill
        is still real information, not noise."""
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in raw_skills:
            tag = self._match_tag(raw)
            value = tag or raw.strip().lower()
            if value and value not in seen:
                seen.add(value)
                normalized.append(value)
        return normalized

    def extract_from_text(self, text: str) -> list[str]:
        """Scan free text for any taxonomy phrase occurring anywhere in it —
        used by `cv_parser.py` to pick up skills mentioned in prose (job
        responsibilities, a personal statement) rather than only an
        explicit skills list."""
        lowered = text.lower()
        found: list[str] = []
        seen: set[str] = set()
        for phrase, tag in _ORDERED_TAGS:
            if phrase in lowered and tag not in seen:
                seen.add(tag)
                found.append(tag)
        return found

    def _match_tag(self, raw: str) -> str | None:
        lowered = raw.strip().lower()
        for phrase, tag in _ORDERED_TAGS:
            if phrase == lowered or phrase in lowered:
                return tag
        return None
