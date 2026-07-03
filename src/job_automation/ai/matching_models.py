"""
Value objects shared across the AI matching engine.

Everything here is a plain, dependency-free dataclass — no SQLAlchemy, no
Playwright, no LLM SDK imports. This is deliberate: `MatchingEngine` and
everything it composes (`RuleEngine`, `ScoreCalculator`, prompt building)
should be testable and usable without a database session or a live LLM
connection. `JobSnapshot.from_job()` is the one bridge to the ORM, kept as a
single classmethod rather than letting `Job` (the SQLAlchemy model) leak
into the engine's own logic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_automation.database.models.job import Job

#: The 8 scoring categories every rule-based and LLM-based score covers —
#: defined once here and imported by rule_engine.py, prompt_builder.py, and
#: score_calculator.py, rather than repeating this list in each.
MATCH_CATEGORIES = (
    "skills",
    "experience",
    "qualifications",
    "location",
    "salary",
    "working_pattern",
    "visa_sponsorship",
    "employer_quality",
)


@dataclass(frozen=True)
class ExperienceEntry:
    job_title: str
    employer: str | None = None
    responsibilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class EducationEntry:
    qualification: str
    awarding_body: str | None = None
    year: str | None = None


@dataclass(frozen=True)
class CandidateProfile:
    """A structured candidate profile. Built by `ai.profile_builder`, not
    constructed by hand in application code — see that module for why."""

    skills: tuple[str, ...] = ()
    experience: tuple[ExperienceEntry, ...] = ()
    education: tuple[EducationEntry, ...] = ()
    certificates: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    preferred_salary_min: float | None = None
    preferred_band: str | None = None
    visa_sponsorship_required: bool | None = None
    working_pattern_preference: str | None = None
    keywords: tuple[str, ...] = ()

    def to_prompt_text(self) -> str:
        """Render as a plain-text block for inclusion in an LLM prompt."""
        lines = [
            f"Skills: {', '.join(self.skills) or 'none listed'}",
            f"Certificates: {', '.join(self.certificates) or 'none listed'}",
            f"Keywords: {', '.join(self.keywords) or 'none listed'}",
        ]
        if self.experience:
            lines.append("Experience:")
            lines.extend(
                f"  - {entry.job_title}" + (f" at {entry.employer}" if entry.employer else "")
                for entry in self.experience
            )
        if self.education:
            lines.append("Education:")
            lines.extend(f"  - {entry.qualification}" for entry in self.education)
        lines.append(f"Preferred locations: {', '.join(self.preferred_locations) or 'no preference'}")
        lines.append(
            f"Preferred minimum salary: {self.preferred_salary_min if self.preferred_salary_min else 'no preference'}"
        )
        lines.append(f"Preferred NHS band: {self.preferred_band or 'no preference'}")
        lines.append(f"Preferred working pattern: {self.working_pattern_preference or 'no preference'}")
        lines.append(
            "Visa sponsorship required: "
            + (
                "unspecified"
                if self.visa_sponsorship_required is None
                else ("yes" if self.visa_sponsorship_required else "no")
            )
        )
        return "\n".join(lines)

    def fingerprint(self) -> str:
        """Stable hash of everything that could change the outcome of a
        match — used as half of the cache key in `ai.cache`."""
        return hashlib.sha256(self.to_prompt_text().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JobSnapshot:
    """A DB-independent copy of the job fields the matching engine needs.
    Constructed from a real `Job` row via `from_job()`, but the engine
    itself only ever sees this type — never the ORM model."""

    title: str
    employer: str | None = None
    location: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    band: str | None = None
    contract_type: str | None = None
    working_pattern: str | None = None
    visa_sponsorship: bool | None = None
    description: str | None = None
    requirements: tuple[str, ...] = ()
    benefits: tuple[str, ...] = ()
    content_hash: str | None = None

    @classmethod
    def from_job(cls, job: "Job") -> "JobSnapshot":
        return cls(
            title=job.title,
            employer=job.employer.name if job.employer else None,
            location=job.location,
            salary_min=float(job.salary_min) if job.salary_min is not None else None,
            salary_max=float(job.salary_max) if job.salary_max is not None else None,
            band=job.band,
            contract_type=job.contract_type,
            working_pattern=job.working_pattern,
            visa_sponsorship=job.visa_sponsorship,
            description=job.description,
            requirements=tuple(job.requirements or ()),
            benefits=tuple(job.benefits or ()),
            content_hash=job.content_hash,
        )

    def to_prompt_text(self) -> str:
        lines = [
            f"Title: {self.title}",
            f"Employer: {self.employer or 'unknown'}",
            f"Location: {self.location or 'unknown'}",
            f"Band: {self.band or 'not specified'}",
            f"Contract type: {self.contract_type or 'not specified'}",
            f"Working pattern: {self.working_pattern or 'not specified'}",
            "Visa sponsorship offered: "
            + (
                "unspecified"
                if self.visa_sponsorship is None
                else ("yes" if self.visa_sponsorship else "no")
            ),
        ]
        if self.salary_min is not None or self.salary_max is not None:
            lines.append(f"Salary: {self.salary_min or '?'} - {self.salary_max or '?'}")
        if self.description:
            lines.append(f"Description: {self.description}")
        if self.requirements:
            lines.append("Requirements: " + "; ".join(self.requirements))
        if self.benefits:
            lines.append("Benefits: " + "; ".join(self.benefits))
        return "\n".join(lines)

    def content_fingerprint(self) -> str:
        """Stable identity for caching purposes — prefers content_hash
        (already computed by JobIngestionService) but falls back to hashing
        the prompt text for a JobSnapshot built without one (e.g. in tests)."""
        if self.content_hash:
            return self.content_hash
        return hashlib.sha256(self.to_prompt_text().encode("utf-8")).hexdigest()


@dataclass
class LLMAnalysis:
    """The parsed, structured result of one LLM call — see
    `ai.prompt_builder.parse_response()` for how raw text becomes this."""

    category_scores: dict[str, float]
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "category_scores": self.category_scores,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "missing_requirements": self.missing_requirements,
            "recommended_actions": self.recommended_actions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LLMAnalysis":
        return cls(
            category_scores=dict(data.get("category_scores", {})),
            strengths=list(data.get("strengths", [])),
            weaknesses=list(data.get("weaknesses", [])),
            missing_requirements=list(data.get("missing_requirements", [])),
            recommended_actions=list(data.get("recommended_actions", [])),
        )


@dataclass
class MatchResult:
    """The final, complete output of `MatchingEngine.evaluate()` — what
    `ai.matching_service` persists into `JobMatch` (overall_score ->
    `match_score`, everything else -> the `analysis` JSON column)."""

    overall_score: float
    confidence_score: float
    category_scores: dict[str, float]
    matched_keywords: list[str]
    strengths: list[str]
    weaknesses: list[str]
    missing_requirements: list[str]
    recommended_actions: list[str]
    used_llm: bool

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "confidence_score": self.confidence_score,
            "category_scores": self.category_scores,
            "matched_keywords": self.matched_keywords,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "missing_requirements": self.missing_requirements,
            "recommended_actions": self.recommended_actions,
            "used_llm": self.used_llm,
        }

    @classmethod
    def from_dict(cls, data: dict, *, fallback_overall_score: float = 0.0) -> "MatchResult":
        """The inverse of `to_dict()` — rebuilds a `MatchResult` from
        `JobMatch.analysis` (exactly what `to_dict()` produced when the
        match was first computed). `fallback_overall_score` covers analysis
        blobs saved before a field existed rather than raising on old data;
        pass `float(job_match.match_score)` (the always-present column)
        when calling this from a `JobMatch` row, the same defensive pattern
        originally written inline in `web/routes/documents.py` and reused
        by `scheduler.tasks.generate_draft_documents` — extracted here so
        both call sites parse a match's analysis identically instead of
        maintaining two copies of the same `.get(..., default)` logic."""
        return cls(
            overall_score=data.get("overall_score", fallback_overall_score),
            confidence_score=data.get("confidence_score", 0.0),
            category_scores=data.get("category_scores", {}),
            matched_keywords=data.get("matched_keywords", []),
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            missing_requirements=data.get("missing_requirements", []),
            recommended_actions=data.get("recommended_actions", []),
            used_llm=data.get("used_llm", False),
        )
