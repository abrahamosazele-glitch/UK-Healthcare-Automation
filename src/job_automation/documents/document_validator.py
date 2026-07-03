"""
Detects likely-unsupported claims in a generated document by cross-checking
it against the candidate's actual profile — the deterministic backstop for
when `prompt_builder`'s grounding-rules instruction isn't enough on its own
(LLMs do sometimes ignore instructions).

Every check here is a real, independent, explainable rule — deliberately
simple keyword/number cross-referencing rather than another LLM call (which
would be slower, cost more, and itself be a thing that needs verifying).
This mirrors the same design choice made in `ai.rule_engine` and
`profile.skills_extractor`: deterministic and explainable now, with a
semantic LLM-based check being a possible future layer (see
docs/DOCUMENT_GENERATION.md's extension points), not built into this
baseline.
"""

from __future__ import annotations

import re

from job_automation.documents.document_models import DocumentValidationIssue, GeneratedDocument
from job_automation.profile.candidate_profile import CandidateProfile
from job_automation.profile.employment_history import total_years_of_experience

#: Certificates/training commonly claimed on UK healthcare application
#: documents — checked for presence in the text without a matching entry in
#: the candidate's actual `certificates` list.
_KNOWN_CERTIFICATES = (
    "dbs check",
    "manual handling",
    "basic life support",
    "safeguarding certificate",
    "food hygiene",
    "first aid",
)

#: UK healthcare professional regulators — checked the same way against
#: `professional_registrations`.
_KNOWN_REGISTRATION_BODIES = ("nmc", "hcpc", "gmc")

_YEARS_CLAIM_RE = re.compile(r"(\d+)\+?\s*years?", re.IGNORECASE)
#: Tolerance for the years-of-experience cross-check — total_years_of_experience()
#: is itself an approximation (partial/unparseable dates), so a small
#: overshoot isn't flagged.
_YEARS_TOLERANCE = 1.0


class DocumentValidator:
    def validate(self, document: GeneratedDocument, profile: CandidateProfile) -> list[DocumentValidationIssue]:
        issues: list[DocumentValidationIssue] = []
        issues.extend(self._check_certificates(document.content, profile))
        issues.extend(self._check_registration_bodies(document.content, profile))
        issues.extend(self._check_years_of_experience(document.content, profile))
        return issues

    def _check_certificates(self, content: str, profile: CandidateProfile) -> list[DocumentValidationIssue]:
        content_lower = content.lower()
        owned = {cert.name.lower() for cert in profile.certificates}
        issues = []
        for known in _KNOWN_CERTIFICATES:
            if known in content_lower and not any(known in name or name in known for name in owned):
                issues.append(
                    DocumentValidationIssue(
                        severity="warning",
                        message=f"Document mentions '{known}' but it is not listed in the candidate's certificates.",
                        claim=known,
                    )
                )
        return issues

    def _check_registration_bodies(self, content: str, profile: CandidateProfile) -> list[DocumentValidationIssue]:
        content_lower = content.lower()
        owned = {reg.body.lower() for reg in profile.professional_registrations}
        issues = []
        for body in _KNOWN_REGISTRATION_BODIES:
            if re.search(rf"\b{body}\b", content_lower) and body not in owned:
                issues.append(
                    DocumentValidationIssue(
                        severity="error",
                        message=f"Document references {body.upper()} registration, which is not on the candidate's profile.",
                        claim=body,
                    )
                )
        return issues

    def _check_years_of_experience(self, content: str, profile: CandidateProfile) -> list[DocumentValidationIssue]:
        actual_years = total_years_of_experience(profile.employment_history)
        issues = []
        for match in _YEARS_CLAIM_RE.finditer(content):
            claimed_years = int(match.group(1))
            if claimed_years > actual_years + _YEARS_TOLERANCE:
                issues.append(
                    DocumentValidationIssue(
                        severity="warning",
                        message=(
                            f"Document claims '{match.group(0)}' but the candidate's profile shows "
                            f"approximately {actual_years} years of experience."
                        ),
                        claim=match.group(0),
                    )
                )
        return issues
