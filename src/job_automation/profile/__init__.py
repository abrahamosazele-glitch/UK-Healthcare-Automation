"""
Candidate Profile Intelligence: the complete structured representation of a
candidate, intended as the single source of truth for future AI document
generation (CVs, cover letters, supporting statements — none of which are
built yet; see docs/CANDIDATE_PROFILE.md).

Not to be confused with `job_automation.ai.matching_models.CandidateProfile`
— a separate, lighter-weight value object used only by the AI matching
engine. See this package's `candidate_profile.py` module docstring for why
the two coexist.
"""

from job_automation.profile.candidate_profile import (
    CandidateProfile,
    LanguageProficiency,
    PersonalInformation,
    ProfessionalRegistration,
    VisaStatus,
)
from job_automation.profile.certificate_parser import Certificate, CertificateParser
from job_automation.profile.cv_parser import CVParseResult, CVParser
from job_automation.profile.education_parser import EducationEntry, EducationParser
from job_automation.profile.employment_history import (
    EmploymentEntry,
    EmploymentGap,
    EmploymentHistoryParser,
    detect_gaps,
    healthcare_experience,
    is_healthcare_role,
    total_years_of_experience,
)
from job_automation.profile.preference_manager import CandidatePreferences, PreferenceManager
from job_automation.profile.profile_loader import (
    DOCXProfileLoader,
    JSONProfileLoader,
    MarkdownProfileLoader,
    PDFProfileLoader,
    ProfileLoader,
    YAMLProfileLoader,
    get_loader_for_path,
)
from job_automation.profile.profile_repository import ProfileRepository
from job_automation.profile.profile_service import ProfileService
from job_automation.profile.profile_validator import ProfileValidator, ValidationIssue
from job_automation.profile.skills_extractor import SKILL_TAXONOMY, SkillsExtractor

__all__ = [
    "CandidateProfile",
    "LanguageProficiency",
    "PersonalInformation",
    "ProfessionalRegistration",
    "VisaStatus",
    "Certificate",
    "CertificateParser",
    "CVParseResult",
    "CVParser",
    "EducationEntry",
    "EducationParser",
    "EmploymentEntry",
    "EmploymentGap",
    "EmploymentHistoryParser",
    "detect_gaps",
    "healthcare_experience",
    "is_healthcare_role",
    "total_years_of_experience",
    "CandidatePreferences",
    "PreferenceManager",
    "DOCXProfileLoader",
    "JSONProfileLoader",
    "MarkdownProfileLoader",
    "PDFProfileLoader",
    "ProfileLoader",
    "YAMLProfileLoader",
    "get_loader_for_path",
    "ProfileRepository",
    "ProfileService",
    "ProfileValidator",
    "ValidationIssue",
    "SKILL_TAXONOMY",
    "SkillsExtractor",
]
