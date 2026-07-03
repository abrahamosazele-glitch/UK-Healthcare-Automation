"""
Builds a structured `CandidateProfile` for the matching engine.

Reads primarily from the candidate profile JSON file (`settings
.candidate_profile_path`, scaffolded back in the very first project
milestone specifically for this purpose) rather than from the database.
There is currently no `CandidateProfile`-shaped table — the `User` model
holds only account/contact fields, and the richer preference data this
milestone needs (skills, experience, education, preferred salary/band/
location/working pattern, keywords) has no schema of its own. Rather than
add a large new table for a single-user personal-automation tool, the JSON
file already used for CV/cover-letter generation is extended and reused
here — see `data/candidate_profile.example.json` for the fields this reads.

Optionally enriched with DB-stored `Certificate` rows when a session and
user_id are supplied, so certificates added via the database (rather than
hand-edited into the JSON file) are still picked up.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.ai.matching_models import CandidateProfile, EducationEntry, ExperienceEntry
from job_automation.database.models.certificate import Certificate
from job_automation.profile.candidate_profile import CandidateProfile as RichCandidateProfile


def build_candidate_profile(
    profile_path: Path,
    *,
    session: Session | None = None,
    user_id: uuid.UUID | None = None,
) -> CandidateProfile:
    """Build a CandidateProfile from the JSON file at `profile_path`. If
    `session` and `user_id` are both given, also merges in any Certificate
    names stored in the database for that user that aren't already listed
    in the JSON file's `certifications`."""
    data = json.loads(profile_path.read_text(encoding="utf-8"))

    experience = tuple(
        ExperienceEntry(
            job_title=entry.get("job_title", ""),
            employer=entry.get("employer") or None,
            responsibilities=tuple(entry.get("responsibilities", [])),
        )
        for entry in data.get("work_experience", [])
        if entry.get("job_title")
    )
    education = tuple(
        EducationEntry(
            qualification=entry.get("name", ""),
            awarding_body=entry.get("awarding_body") or None,
            year=entry.get("year") or None,
        )
        for entry in data.get("qualifications", [])
        if entry.get("name")
    )

    certificates = list(data.get("certifications", []))
    if session is not None and user_id is not None:
        db_certificate_names = session.scalars(
            select(Certificate.name).where(Certificate.user_id == user_id)
        ).all()
        for name in db_certificate_names:
            if name not in certificates:
                certificates.append(name)

    right_to_work_uk = data.get("right_to_work_uk")
    visa_sponsorship_required = data.get("visa_sponsorship_required")
    if visa_sponsorship_required is None and right_to_work_uk is not None:
        # Not explicitly set in the JSON — infer from right_to_work_uk
        # (already collected for CV/cover-letter purposes) rather than
        # asking the user to enter the same fact twice.
        visa_sponsorship_required = not right_to_work_uk

    return CandidateProfile(
        skills=tuple(data.get("skills", [])),
        experience=experience,
        education=education,
        certificates=tuple(certificates),
        preferred_locations=tuple(data.get("preferred_locations", [])),
        preferred_salary_min=data.get("preferred_salary_min"),
        preferred_band=data.get("preferred_band"),
        visa_sponsorship_required=visa_sponsorship_required,
        working_pattern_preference=data.get("working_pattern_preference"),
        keywords=tuple(data.get("keywords", [])),
    )


def to_ai_profile(profile: RichCandidateProfile) -> CandidateProfile:
    """Adapts the richer, per-user, DB-backed `profile.candidate_profile
    .CandidateProfile` (Candidate Profile Intelligence milestone) into the
    lighter `ai.matching_models.CandidateProfile` `MatchingEngine` expects.
    Shared by `scheduler.tasks.run_ai_matching` (background, fake provider)
    and the manual "re-run with AI" dashboard trigger (real provider) —
    the one adapter between the two representations, not duplicated."""
    return CandidateProfile(
        skills=profile.skills,
        experience=tuple(
            ExperienceEntry(job_title=entry.job_title, employer=entry.employer, responsibilities=entry.responsibilities)
            for entry in profile.employment_history
        ),
        education=tuple(
            EducationEntry(qualification=entry.qualification, awarding_body=entry.awarding_body, year=entry.year)
            for entry in profile.education
        ),
        certificates=tuple(certificate.name for certificate in profile.certificates),
        preferred_locations=profile.preferences.preferred_locations,
        preferred_salary_min=profile.preferences.preferred_salary_min,
        preferred_band=profile.preferences.preferred_nhs_band,
        visa_sponsorship_required=profile.preferences.visa_sponsorship_required,
        working_pattern_preference=profile.preferences.preferred_working_pattern,
        keywords=profile.keywords,
    )
