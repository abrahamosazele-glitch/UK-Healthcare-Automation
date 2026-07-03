"""
The main entry point for the candidate-profile subsystem: load a profile
from a file (any supported format), validate it, and persist it — composing
`profile_loader`, `profile_validator`, and `profile_repository` so callers
don't need to wire those together themselves.

Every dependency is constructor-injected (the loader lookup, the validator,
the repository), consistent with dependency-injection patterns used
throughout this project (`BaseScraper`, `MatchingEngine`, etc.) — this
class contains only orchestration, no logic of its own.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from job_automation.profile.candidate_profile import CandidateProfile
from job_automation.profile.profile_loader import get_loader_for_path
from job_automation.profile.profile_repository import ProfileRepository
from job_automation.profile.profile_validator import ProfileValidator, ValidationIssue
from job_automation.utils.logger import logger


class ProfileService:
    def __init__(
        self,
        session: Session,
        *,
        repository: ProfileRepository | None = None,
        validator: ProfileValidator | None = None,
    ) -> None:
        self._repository = repository or ProfileRepository(session)
        self._validator = validator or ProfileValidator()

    def load_from_file(self, path: Path) -> CandidateProfile:
        """Load a candidate profile from `path`, picking the loader by file
        extension (see `profile_loader.get_loader_for_path`)."""
        loader = get_loader_for_path(path)
        data = loader.load(path)
        profile = CandidateProfile.from_dict(data)
        logger.info("Loaded candidate profile from {} ({})", path, type(loader).__name__)
        return profile

    def validate(self, profile: CandidateProfile) -> list[ValidationIssue]:
        issues = self._validator.validate(profile)
        if issues:
            logger.warning("Profile validation found {} issue(s)", len(issues))
        return issues

    def save(
        self, profile: CandidateProfile, *, user_id: uuid.UUID, source_format: str | None = None
    ) -> CandidateProfile:
        self._repository.save(profile, user_id=user_id, source_format=source_format)
        logger.info("Saved candidate profile for user {}", user_id)
        return profile

    def load_and_save_from_file(self, path: Path, *, user_id: uuid.UUID) -> CandidateProfile:
        """Convenience: load, then persist immediately — the common case
        for a script or one-off import. Does not validate automatically;
        call `validate()` explicitly if the caller wants to inspect issues
        before deciding whether to save."""
        profile = self.load_from_file(path)
        self.save(profile, user_id=user_id, source_format=path.suffix.lstrip(".").lower())
        return profile

    def get(self, user_id: uuid.UUID) -> CandidateProfile | None:
        return self._repository.load(user_id)
