"""
`CandidateProfile` — the complete structured representation of a candidate,
and the aggregate root of this subsystem.

Composes entities owned by their own dedicated modules
(`EmploymentEntry` from `employment_history.py`, `EducationEntry` from
`education_parser.py`, `Certificate` from `certificate_parser.py`,
`CandidatePreferences` from `preference_manager.py`) plus the remaining
top-level sections that don't have a dedicated parsing/analysis module of
their own: personal information, professional registration, languages, visa
status, career goals, availability, and keywords.

Not to be confused with `job_automation.ai.matching_models.CandidateProfile`
— a separate, deliberately lighter-weight value object used only by the AI
matching engine. This class is the richer "single source of truth for all
future AI document generation" (CVs, cover letters, supporting statements)
that this milestone builds; see docs/CANDIDATE_PROFILE.md for why the two
coexist rather than being merged in this milestone.

Every nested entity has `to_dict()`/`from_dict()` so the whole profile
round-trips through JSON cleanly — this is what `profile_repository.py`
persists as `CandidateProfileRecord.data`, and what `profile_loader.py`'s
three format loaders all converge on regardless of source format.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from job_automation.profile.certificate_parser import Certificate
from job_automation.profile.education_parser import EducationEntry
from job_automation.profile.employment_history import EmploymentEntry, healthcare_experience
from job_automation.profile.preference_manager import CandidatePreferences


@dataclass(frozen=True)
class PersonalInformation:
    full_name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    personal_statement: str | None = None

    def to_dict(self) -> dict:
        return {
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "address": self.address,
            "personal_statement": self.personal_statement,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalInformation":
        return cls(
            full_name=data.get("full_name", ""),
            email=data.get("email") or None,
            phone=data.get("phone") or None,
            address=data.get("address") or None,
            personal_statement=data.get("personal_statement") or None,
        )


@dataclass(frozen=True)
class ProfessionalRegistration:
    """UK healthcare professional registration — NMC (nursing/midwifery),
    HCPC (allied health professions), GMC (doctors), etc."""

    body: str
    registration_number: str
    expiry_date: str | None = None
    status: str | None = None  # e.g. "active", "lapsed", "pending"

    def to_dict(self) -> dict:
        return {
            "body": self.body,
            "registration_number": self.registration_number,
            "expiry_date": self.expiry_date,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProfessionalRegistration":
        return cls(
            body=data.get("body", ""),
            registration_number=data.get("registration_number", ""),
            expiry_date=data.get("expiry_date") or None,
            status=data.get("status") or None,
        )


@dataclass(frozen=True)
class LanguageProficiency:
    language: str
    proficiency: str | None = None  # e.g. "native", "fluent", "conversational"

    def to_dict(self) -> dict:
        return {"language": self.language, "proficiency": self.proficiency}

    @classmethod
    def from_dict(cls, data: dict) -> "LanguageProficiency":
        return cls(language=data.get("language", ""), proficiency=data.get("proficiency") or None)


@dataclass(frozen=True)
class VisaStatus:
    right_to_work_uk: bool | None = None
    visa_type: str | None = None
    sponsorship_required: bool | None = None

    def to_dict(self) -> dict:
        return {
            "right_to_work_uk": self.right_to_work_uk,
            "visa_type": self.visa_type,
            "sponsorship_required": self.sponsorship_required,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VisaStatus":
        return cls(
            right_to_work_uk=data.get("right_to_work_uk"),
            visa_type=data.get("visa_type") or None,
            sponsorship_required=data.get("sponsorship_required"),
        )


@dataclass(frozen=True)
class CandidateProfile:
    personal_information: PersonalInformation
    education: tuple[EducationEntry, ...] = field(default_factory=tuple)
    employment_history: tuple[EmploymentEntry, ...] = field(default_factory=tuple)
    skills: tuple[str, ...] = field(default_factory=tuple)
    certificates: tuple[Certificate, ...] = field(default_factory=tuple)
    professional_registrations: tuple[ProfessionalRegistration, ...] = field(default_factory=tuple)
    languages: tuple[LanguageProficiency, ...] = field(default_factory=tuple)
    visa_status: VisaStatus = field(default_factory=VisaStatus)
    preferences: CandidatePreferences = field(default_factory=CandidatePreferences)
    career_goals: tuple[str, ...] = field(default_factory=tuple)
    availability: str | None = None
    keywords: tuple[str, ...] = field(default_factory=tuple)

    @property
    def healthcare_experience(self) -> tuple[EmploymentEntry, ...]:
        """Derived, not stored — see `employment_history.is_healthcare_role()`.
        Keeping this as a computed view (rather than a second list a caller
        could forget to update) means it can never drift out of sync with
        `employment_history`."""
        return tuple(healthcare_experience(self.employment_history))

    def to_dict(self) -> dict:
        return {
            "personal_information": self.personal_information.to_dict(),
            "education": [entry.to_dict() for entry in self.education],
            "employment_history": [entry.to_dict() for entry in self.employment_history],
            "skills": list(self.skills),
            "certificates": [cert.to_dict() for cert in self.certificates],
            "professional_registrations": [reg.to_dict() for reg in self.professional_registrations],
            "languages": [lang.to_dict() for lang in self.languages],
            "visa_status": self.visa_status.to_dict(),
            "preferences": self.preferences.to_dict(),
            "career_goals": list(self.career_goals),
            "availability": self.availability,
            "keywords": list(self.keywords),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CandidateProfile":
        """The single place every loader (JSON/YAML/Markdown) and the
        repository converge on to build a `CandidateProfile` from a plain
        dict — see profile_loader.py's module docstring for why loaders
        only produce this intermediate shape rather than building the
        dataclass themselves."""
        return cls(
            personal_information=PersonalInformation.from_dict(data.get("personal_information", {})),
            education=tuple(EducationEntry.from_dict(e) for e in data.get("education", [])),
            employment_history=tuple(
                EmploymentEntry.from_dict(e) for e in data.get("employment_history", [])
            ),
            skills=tuple(data.get("skills", [])),
            certificates=tuple(Certificate.from_dict(c) for c in data.get("certificates", [])),
            professional_registrations=tuple(
                ProfessionalRegistration.from_dict(r) for r in data.get("professional_registrations", [])
            ),
            languages=tuple(LanguageProficiency.from_dict(entry) for entry in data.get("languages", [])),
            visa_status=VisaStatus.from_dict(data.get("visa_status", {})),
            preferences=CandidatePreferences.from_dict(data.get("preferences", {})),
            career_goals=tuple(data.get("career_goals", [])),
            availability=data.get("availability") or None,
            keywords=tuple(data.get("keywords", [])),
        )
