"""
Validates a `CandidateProfile`, flagging missing data and internal
contradictions the candidate should probably resolve before this profile is
used for AI document generation.

Every check is a real, independent rule (not a placeholder) but
deliberately conservative: this reports *possible* issues for a human to
review, not hard validation errors that would block anything — a candidate
profile with gaps is still usable, just less complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from job_automation.profile.candidate_profile import CandidateProfile
from job_automation.profile.employment_history import detect_gaps

_RECOMMENDED_HEALTHCARE_CERTIFICATES = ("dbs", "manual handling", "basic life support")


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    severity: str  # "error" | "warning"
    message: str


class ProfileValidator:
    def validate(self, profile: CandidateProfile) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        issues.extend(self._check_skills(profile))
        issues.extend(self._check_employment_history(profile))
        issues.extend(self._check_qualifications(profile))
        issues.extend(self._check_certificates(profile))
        issues.extend(self._check_conflicts(profile))
        return issues

    def _check_skills(self, profile: CandidateProfile) -> list[ValidationIssue]:
        if not profile.skills:
            return [ValidationIssue("skills", "warning", "No skills listed on the profile.")]
        return []

    def _check_employment_history(self, profile: CandidateProfile) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not profile.employment_history:
            issues.append(
                ValidationIssue("employment_history", "warning", "No employment history listed.")
            )
            return issues

        for index, entry in enumerate(profile.employment_history):
            if not entry.employer:
                issues.append(
                    ValidationIssue(
                        f"employment_history[{index}]",
                        "warning",
                        f"'{entry.job_title}' has no employer listed.",
                    )
                )
            if not entry.start_date:
                issues.append(
                    ValidationIssue(
                        f"employment_history[{index}]",
                        "warning",
                        f"'{entry.job_title}' has no start date listed.",
                    )
                )

        for gap in detect_gaps(profile.employment_history):
            issues.append(
                ValidationIssue(
                    "employment_history",
                    "warning",
                    f"Gap of approximately {gap.approximate_days} days between "
                    f"'{gap.after.job_title}' and '{gap.before.job_title}'.",
                )
            )
        return issues

    def _check_qualifications(self, profile: CandidateProfile) -> list[ValidationIssue]:
        if not profile.education:
            return [ValidationIssue("education", "warning", "No qualifications listed.")]
        return []

    def _check_certificates(self, profile: CandidateProfile) -> list[ValidationIssue]:
        if not profile.certificates:
            return [ValidationIssue("certificates", "warning", "No certificates listed.")]

        certificate_names = " ".join(cert.name.lower() for cert in profile.certificates)
        issues = [
            ValidationIssue(
                "certificates",
                "warning",
                f"No '{expected}' certificate found — commonly required for UK healthcare roles.",
            )
            for expected in _RECOMMENDED_HEALTHCARE_CERTIFICATES
            if expected not in certificate_names
        ]
        return issues

    def _check_conflicts(self, profile: CandidateProfile) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        visa = profile.visa_status
        if visa.right_to_work_uk is True and visa.sponsorship_required is True:
            issues.append(
                ValidationIssue(
                    "visa_status",
                    "error",
                    "right_to_work_uk is True but sponsorship_required is also True — contradictory.",
                )
            )

        today = date.today()
        for index, registration in enumerate(profile.professional_registrations):
            if registration.status and registration.status.lower() == "active" and registration.expiry_date:
                try:
                    expiry = date.fromisoformat(registration.expiry_date)
                except ValueError:
                    continue
                if expiry < today:
                    issues.append(
                        ValidationIssue(
                            f"professional_registrations[{index}]",
                            "error",
                            f"{registration.body} registration is marked 'active' but expired on "
                            f"{registration.expiry_date}.",
                        )
                    )

        return issues
