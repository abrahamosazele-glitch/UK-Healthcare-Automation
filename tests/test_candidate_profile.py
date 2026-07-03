"""
Tests for the Candidate Profile Intelligence subsystem, run entirely
against local fixtures (tests/fixtures/profile/) — covers parsing
(JSON/YAML/Markdown/CV text), normalization (skills taxonomy), validation,
preference loading, and repository persistence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.cv_parser import CVParser
from job_automation.profile.employment_history import (
    EmploymentEntry,
    detect_gaps,
    healthcare_experience,
    is_healthcare_role,
    total_years_of_experience,
)
from job_automation.profile.preference_manager import CandidatePreferences, PreferenceManager
from job_automation.profile.profile_loader import (
    DOCXProfileLoader,
    PDFProfileLoader,
    get_loader_for_path,
)
from job_automation.profile.profile_repository import ProfileRepository
from job_automation.profile.profile_service import ProfileService
from job_automation.profile.profile_validator import ProfileValidator
from job_automation.profile.skills_extractor import SkillsExtractor
from job_automation.database.models import User

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "profile"


# --- Loaders: JSON / YAML / Markdown -----------------------------------------


def test_json_loader_parses_fixture_into_complete_profile() -> None:
    path = FIXTURES_DIR / "candidate_profile.json"
    profile = CandidateProfile.from_dict(get_loader_for_path(path).load(path))

    assert profile.personal_information.full_name == "Jane Doe"
    assert len(profile.employment_history) == 2
    assert len(profile.education) == 2
    assert profile.visa_status.right_to_work_uk is True
    assert profile.preferences.preferred_nhs_band == "Band 5"
    assert "ward" in profile.keywords


def test_yaml_loader_produces_an_equivalent_profile_to_json() -> None:
    json_path = FIXTURES_DIR / "candidate_profile.json"
    yaml_path = FIXTURES_DIR / "candidate_profile.yaml"

    json_profile = CandidateProfile.from_dict(get_loader_for_path(json_path).load(json_path))
    yaml_profile = CandidateProfile.from_dict(get_loader_for_path(yaml_path).load(yaml_path))

    assert json_profile == yaml_profile


def test_markdown_loader_parses_all_sections() -> None:
    path = FIXTURES_DIR / "candidate_profile.md"
    profile = CandidateProfile.from_dict(get_loader_for_path(path).load(path))

    assert profile.personal_information.full_name == "Jane Doe"
    assert profile.personal_information.email == "jane.doe@example.com"

    assert len(profile.employment_history) == 2
    first_job = profile.employment_history[0]
    assert first_job.job_title == "Healthcare Assistant"
    assert first_job.employer == "Example NHS Foundation Trust"
    assert first_job.start_date == "2022-01-01"
    # Markdown has no null concept for an ongoing role — "Present" is kept
    # as the literal end_date text, unlike the JSON fixture's explicit null.
    assert first_job.end_date == "Present"

    assert len(profile.education) == 2
    assert profile.certificates[0].name == "DBS Check"
    assert profile.certificates[0].expiry_date == "2026-01-01"

    assert profile.professional_registrations[0].body == "NMC"
    assert profile.professional_registrations[0].registration_number == "12A1234B"

    assert profile.languages[0].language == "English"
    assert profile.languages[0].proficiency == "native"

    assert profile.visa_status.right_to_work_uk is True
    assert profile.visa_status.sponsorship_required is False

    assert profile.preferences.preferred_locations == ("London", "Manchester")
    assert profile.preferences.max_travel_distance_miles == 15
    assert profile.preferences.preferred_salary_min == 25000

    assert "Progress to a senior healthcare assistant role" in profile.career_goals
    assert profile.availability == "4 weeks notice"
    assert "dementia care" in profile.keywords


def test_get_loader_for_path_raises_for_unknown_extension() -> None:
    with pytest.raises(ValueError):
        get_loader_for_path(Path("profile.exe"))


def test_pdf_and_docx_loaders_are_future_interfaces_not_yet_implemented() -> None:
    with pytest.raises(NotImplementedError):
        PDFProfileLoader().load(Path("anything.pdf"))
    with pytest.raises(NotImplementedError):
        DOCXProfileLoader().load(Path("anything.docx"))


# --- CV parser ----------------------------------------------------------------


def test_cv_parser_extracts_structured_data_from_a_real_cv() -> None:
    text = (FIXTURES_DIR / "sample_cv.txt").read_text(encoding="utf-8")
    result = CVParser().parse(text)

    assert len(result.employment_history) == 2
    assert result.employment_history[0].job_title == "Healthcare Assistant"
    assert result.employment_history[0].employer == "Example NHS Foundation Trust"

    assert len(result.education) == 2
    assert result.education[0].qualification == "BTEC Level 3 Health and Social Care"

    assert len(result.certificates) == 2
    assert result.certificates[0].name == "DBS Check"
    assert result.certificates[0].issued_date == "January 2023"
    assert result.certificates[0].expiry_date == "January 2026"

    # Explicit skills, normalized...
    assert "communication" in result.skills
    assert "medication" in result.skills
    # ...plus skills only mentioned in prose responsibilities, not the
    # explicit skills list, picked up by scanning the whole document.
    assert "moving_and_handling" in result.skills
    assert "safeguarding" in result.skills
    assert "dementia" in result.skills
    assert "nhs_experience" in result.skills


# --- Skills extractor (normalization) ----------------------------------------


def test_skills_extractor_normalizes_known_taxonomy_terms() -> None:
    extractor = SkillsExtractor()
    normalized = extractor.normalize(["Communication", "Manual handling", "Medication administration"])
    assert normalized == ["communication", "moving_and_handling", "medication"]


def test_skills_extractor_keeps_unrecognized_skills_as_is() -> None:
    extractor = SkillsExtractor()
    normalized = extractor.normalize(["Advanced phlebotomy"])
    assert normalized == ["advanced phlebotomy"]


def test_skills_extractor_deduplicates() -> None:
    extractor = SkillsExtractor()
    normalized = extractor.normalize(["Communication", "communication", "COMMUNICATION"])
    assert normalized == ["communication"]


def test_skills_extractor_finds_taxonomy_terms_in_prose() -> None:
    extractor = SkillsExtractor()
    found = extractor.extract_from_text(
        "Worked in a busy NHS ward providing dementia care and supporting safeguarding processes."
    )
    assert "nhs_experience" in found
    assert "dementia" in found
    assert "safeguarding" in found


# --- Employment history analysis ---------------------------------------------


def test_is_healthcare_role_detects_nhs_and_care_employers() -> None:
    assert is_healthcare_role(EmploymentEntry(job_title="Assistant", employer="Example NHS Trust"))
    assert is_healthcare_role(EmploymentEntry(job_title="Carer", employer="Example Care Home Ltd"))
    assert not is_healthcare_role(EmploymentEntry(job_title="Retail Assistant", employer="Example Supermarket"))


def test_healthcare_experience_filters_employment_history() -> None:
    entries = (
        EmploymentEntry(job_title="Healthcare Assistant", employer="Example NHS Trust"),
        EmploymentEntry(job_title="Retail Assistant", employer="Example Supermarket"),
    )
    filtered = healthcare_experience(entries)
    assert len(filtered) == 1
    assert filtered[0].employer == "Example NHS Trust"


def test_total_years_of_experience_sums_durations() -> None:
    entries = (
        EmploymentEntry(job_title="A", start_date="2020-01-01", end_date="2021-01-01"),
        EmploymentEntry(job_title="B", start_date="2021-01-01", end_date="2022-01-01"),
    )
    assert total_years_of_experience(entries) == pytest.approx(2.0, abs=0.1)


def test_detect_gaps_finds_a_significant_gap_between_roles() -> None:
    entries = (
        EmploymentEntry(job_title="A", start_date="2018-01-01", end_date="2019-01-01"),
        EmploymentEntry(job_title="B", start_date="2020-06-01", end_date="2021-01-01"),
    )
    gaps = detect_gaps(entries)
    assert len(gaps) == 1
    assert gaps[0].after.job_title == "A"
    assert gaps[0].before.job_title == "B"


def test_detect_gaps_ignores_a_short_gap() -> None:
    entries = (
        EmploymentEntry(job_title="A", start_date="2018-01-01", end_date="2019-01-01"),
        EmploymentEntry(job_title="B", start_date="2019-02-01", end_date="2020-01-01"),
    )
    assert detect_gaps(entries) == []


# --- Validation ----------------------------------------------------------------


def test_validator_flags_missing_sections_on_an_empty_profile() -> None:
    profile = CandidateProfile(personal_information=PersonalInformation(full_name="Empty Profile"))
    issues = ProfileValidator().validate(profile)

    fields = {issue.field for issue in issues}
    assert "skills" in fields
    assert "employment_history" in fields
    assert "education" in fields
    assert "certificates" in fields


def test_validator_recommends_common_healthcare_certificates() -> None:
    from job_automation.profile.certificate_parser import Certificate

    # A non-empty certificate list that's still missing the commonly-
    # expected ones — distinct from an empty list, which short-circuits to
    # a single generic "no certificates" message instead of this check.
    profile = CandidateProfile(
        personal_information=PersonalInformation(full_name="Test"),
        certificates=(Certificate(name="Food Hygiene Certificate"),),
    )
    issues = ProfileValidator().validate(profile)
    messages = " ".join(issue.message for issue in issues)
    assert "dbs" in messages.lower()


def test_validator_flags_conflicting_visa_information() -> None:
    from job_automation.profile.candidate_profile import VisaStatus

    profile = CandidateProfile(
        personal_information=PersonalInformation(full_name="Test"),
        visa_status=VisaStatus(right_to_work_uk=True, sponsorship_required=True),
    )
    issues = ProfileValidator().validate(profile)
    assert any(issue.severity == "error" and issue.field == "visa_status" for issue in issues)


def test_validator_flags_expired_but_active_registration() -> None:
    from job_automation.profile.candidate_profile import ProfessionalRegistration

    profile = CandidateProfile(
        personal_information=PersonalInformation(full_name="Test"),
        professional_registrations=(
            ProfessionalRegistration(
                body="NMC", registration_number="12A1234B", expiry_date="2000-01-01", status="active"
            ),
        ),
    )
    issues = ProfileValidator().validate(profile)
    assert any(issue.severity == "error" and "expired" in issue.message.lower() for issue in issues)


def test_validator_does_not_flag_a_complete_well_formed_profile() -> None:
    path = FIXTURES_DIR / "candidate_profile.json"
    profile = CandidateProfile.from_dict(get_loader_for_path(path).load(path))
    issues = ProfileValidator().validate(profile)
    errors = [issue for issue in issues if issue.severity == "error"]
    assert errors == []


# --- Preference manager --------------------------------------------------------


def test_preference_manager_location_matching() -> None:
    manager = PreferenceManager()
    preferences = CandidatePreferences(preferred_locations=("London",))
    assert manager.matches_location(preferences, "Central London") is True
    assert manager.matches_location(preferences, "Manchester") is False
    assert manager.matches_location(CandidatePreferences(), "Anywhere") is True  # no preference


def test_preference_manager_salary_and_visa() -> None:
    manager = PreferenceManager()
    preferences = CandidatePreferences(preferred_salary_min=25000, visa_sponsorship_required=True)

    assert manager.meets_salary_expectation(preferences, 26000) is True
    assert manager.meets_salary_expectation(preferences, 20000) is False
    assert manager.satisfies_visa_requirement(preferences, sponsorship_offered=True) is True
    assert manager.satisfies_visa_requirement(preferences, sponsorship_offered=False) is False
    assert manager.satisfies_visa_requirement(CandidatePreferences(), sponsorship_offered=False) is True


# --- Repository / service persistence -----------------------------------------


def test_profile_repository_saves_and_loads(db_session: Session) -> None:
    user = User(email="candidate@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    path = FIXTURES_DIR / "candidate_profile.json"
    profile = CandidateProfile.from_dict(get_loader_for_path(path).load(path))

    repository = ProfileRepository(db_session)
    repository.save(profile, user_id=user.id, source_format="json")
    db_session.commit()

    loaded = repository.load(user.id)
    assert loaded == profile


def test_profile_repository_save_updates_not_duplicates(db_session: Session) -> None:
    user = User(email="candidate2@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    repository = ProfileRepository(db_session)
    profile_v1 = CandidateProfile(personal_information=PersonalInformation(full_name="Jane Doe"))
    record_v1 = repository.save(profile_v1, user_id=user.id)
    db_session.commit()

    profile_v2 = CandidateProfile(personal_information=PersonalInformation(full_name="Jane D. Smith"))
    record_v2 = repository.save(profile_v2, user_id=user.id)
    db_session.commit()

    assert record_v1.id == record_v2.id
    assert repository.load(user.id).personal_information.full_name == "Jane D. Smith"


def test_profile_service_loads_validates_and_saves_end_to_end(db_session: Session) -> None:
    user = User(email="candidate3@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    service = ProfileService(db_session)
    profile = service.load_and_save_from_file(FIXTURES_DIR / "candidate_profile.json", user_id=user.id)
    db_session.commit()

    assert profile.personal_information.full_name == "Jane Doe"
    assert service.get(user.id) == profile

    issues = service.validate(profile)
    assert all(issue.severity != "error" for issue in issues)
