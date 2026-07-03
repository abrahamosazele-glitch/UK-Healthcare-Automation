"""
Value objects for the AI Career Assistant. Deliberately dependency-free —
same design as `ai.matching_models`/`documents.document_models`: pure
dataclasses/enums, no SQLAlchemy, no LLM SDK imports, so
`CareerAssistantService` is testable and usable without a database
session or a live LLM connection.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ReadinessLevel(str, enum.Enum):
    """How prepared the candidate appears for an interview at this job,
    predicted from the same match data already computed — see
    `career_assistant_service._build_interview_readiness()` for the
    scoring formula."""

    READY = "ready"
    ALMOST_READY = "almost_ready"
    NEEDS_PREPARATION = "needs_preparation"
    SIGNIFICANT_GAPS = "significant_gaps"

    @property
    def display_label(self) -> str:
        return {
            ReadinessLevel.READY: "Interview ready",
            ReadinessLevel.ALMOST_READY: "Almost ready",
            ReadinessLevel.NEEDS_PREPARATION: "Needs preparation",
            ReadinessLevel.SIGNIFICANT_GAPS: "Significant preparation needed",
        }[self]


@dataclass(frozen=True)
class CategoryInsight:
    """One `MATCH_CATEGORIES` entry (see `ai.matching_models`), translated
    from a bare 0-100 number into a display name, a strength/weakness
    label, and a one-line plain-English explanation."""

    category: str
    score: float
    label: str
    explanation: str


@dataclass(frozen=True)
class CVSuggestion:
    """One actionable suggestion for improving the candidate's CV/profile
    specifically for this job — derived from the match's own
    weaknesses/missing_requirements/recommended_actions, reframed as
    something the candidate can actually go and do."""

    priority: str  # "high" | "medium" | "low"
    suggestion: str


@dataclass(frozen=True)
class InterviewReadiness:
    readiness_score: float
    level: ReadinessLevel
    reasoning: str
    focus_areas: tuple[str, ...] = ()


@dataclass(frozen=True)
class CareerInsight:
    """The complete, always-available (zero-LLM-cost) Career Assistant
    output for one job match — everything
    `career_assistant_service.CareerAssistantService.build_insight()`
    computes, rendered by `components/career_assistant_panel.html`."""

    job_title: str
    employer: str | None
    overall_score: float
    summary: str
    category_insights: tuple[CategoryInsight, ...]
    strengths: tuple[str, ...]
    missing_skills: tuple[str, ...]
    cv_suggestions: tuple[CVSuggestion, ...]
    interview_readiness: InterviewReadiness
