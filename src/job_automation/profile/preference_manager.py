"""
Candidate job-search preferences: the `CandidatePreferences` entity plus
`PreferenceManager`, which provides the actual matching logic (does a given
location/salary/pattern satisfy these preferences) rather than leaving every
caller to reimplement that comparison.

This is deliberately independent of `job_automation.ai.rule_engine`, which
already does similar location/salary/working-pattern comparisons for job
*matching*. The two are not merged in this milestone (see
docs/CANDIDATE_PROFILE.md) — this class exists so preference logic has a
single home within the candidate-profile subsystem itself, usable by
`profile_validator.py` or a future feature without depending on the AI
matching package.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CandidatePreferences:
    preferred_locations: tuple[str, ...] = field(default_factory=tuple)
    max_travel_distance_miles: float | None = None
    preferred_employers: tuple[str, ...] = field(default_factory=tuple)
    preferred_contract_type: str | None = None
    preferred_hours: str | None = None
    preferred_salary_min: float | None = None
    preferred_nhs_band: str | None = None
    preferred_working_pattern: str | None = None
    remote_preference: str | None = None  # e.g. "on-site", "hybrid", "remote", "no preference"
    visa_sponsorship_required: bool | None = None

    def to_dict(self) -> dict:
        return {
            "preferred_locations": list(self.preferred_locations),
            "max_travel_distance_miles": self.max_travel_distance_miles,
            "preferred_employers": list(self.preferred_employers),
            "preferred_contract_type": self.preferred_contract_type,
            "preferred_hours": self.preferred_hours,
            "preferred_salary_min": self.preferred_salary_min,
            "preferred_nhs_band": self.preferred_nhs_band,
            "preferred_working_pattern": self.preferred_working_pattern,
            "remote_preference": self.remote_preference,
            "visa_sponsorship_required": self.visa_sponsorship_required,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CandidatePreferences":
        return cls(
            preferred_locations=tuple(data.get("preferred_locations", [])),
            max_travel_distance_miles=data.get("max_travel_distance_miles"),
            preferred_employers=tuple(data.get("preferred_employers", [])),
            preferred_contract_type=data.get("preferred_contract_type") or None,
            preferred_hours=data.get("preferred_hours") or None,
            preferred_salary_min=data.get("preferred_salary_min"),
            preferred_nhs_band=data.get("preferred_nhs_band") or None,
            preferred_working_pattern=data.get("preferred_working_pattern") or None,
            remote_preference=data.get("remote_preference") or None,
            visa_sponsorship_required=data.get("visa_sponsorship_required"),
        )


class PreferenceManager:
    def matches_location(self, preferences: CandidatePreferences, location: str | None) -> bool:
        if not preferences.preferred_locations:
            return True  # no preference expressed = compatible with anything
        if not location:
            return False
        location_lower = location.lower()
        return any(
            preferred.lower() in location_lower or location_lower in preferred.lower()
            for preferred in preferences.preferred_locations
        )

    def matches_employer(self, preferences: CandidatePreferences, employer: str | None) -> bool:
        if not preferences.preferred_employers:
            return True
        if not employer:
            return False
        employer_lower = employer.lower()
        return any(preferred.lower() in employer_lower for preferred in preferences.preferred_employers)

    def meets_salary_expectation(self, preferences: CandidatePreferences, salary: float | None) -> bool:
        if preferences.preferred_salary_min is None:
            return True
        if salary is None:
            return False
        return salary >= preferences.preferred_salary_min

    def matches_working_pattern(self, preferences: CandidatePreferences, working_pattern: str | None) -> bool:
        if not preferences.preferred_working_pattern:
            return True
        if not working_pattern:
            return False
        preferred_lower = preferences.preferred_working_pattern.lower()
        actual_lower = working_pattern.lower()
        return preferred_lower in actual_lower or actual_lower in preferred_lower

    def satisfies_visa_requirement(self, preferences: CandidatePreferences, sponsorship_offered: bool | None) -> bool:
        if not preferences.visa_sponsorship_required:
            return True  # sponsorship not needed -> any job qualifies
        return bool(sponsorship_offered)
