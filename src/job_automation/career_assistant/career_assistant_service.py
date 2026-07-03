"""
Builds a `CareerInsight` from an already-computed `MatchResult` — no LLM
call, no database session, no network access. Every number and sentence
here is derived from data the matching engine already produced (see
`ai.matching_models.MatchResult`), so this can run on every page render
that shows a job match, at zero cost — the same "instantaneous, always
correct, never a placeholder" bar `AnalyticsService`'s rule-based
summaries already meet.

Kept as a single class with plain methods (no constructor dependencies)
rather than a set of bare functions, matching this codebase's established
`*Service` naming/shape (`MatchingService`, `ProfileService`,
`DocumentService`) even though nothing here needs injecting.
"""

from __future__ import annotations

from job_automation.ai.matching_models import JobSnapshot, MATCH_CATEGORIES, MatchResult
from job_automation.career_assistant.career_assistant_models import (
    CareerInsight,
    CategoryInsight,
    CVSuggestion,
    InterviewReadiness,
    ReadinessLevel,
)

#: Score buckets shared by category explanations and the overall summary —
#: (minimum score, label, adjective) in descending order, first match wins.
_SCORE_BUCKETS: tuple[tuple[float, str, str], ...] = (
    (80.0, "Strong", "excellent alignment"),
    (60.0, "Good", "good alignment"),
    (40.0, "Moderate", "a moderate gap"),
    (0.0, "Weak", "a significant gap"),
)

#: `MATCH_CATEGORIES` values are snake_case keys shared with the matching
#: engine's internals — this is the one place they're turned into the
#: display names a candidate should actually read.
_CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "skills": "Skills",
    "experience": "Experience",
    "qualifications": "Qualifications",
    "location": "Location",
    "salary": "Salary",
    "working_pattern": "Working pattern",
    "visa_sponsorship": "Visa sponsorship",
    "employer_quality": "Employer quality",
}

#: Interview readiness formula weights — overall match matters most,
#: confidence (how much of the score rests on real signal vs. defaults)
#: second, and each unaddressed missing requirement chips away at
#: readiness on top of both. Weights sum to 1.0 before the missing-
#: requirements penalty is applied.
_READINESS_OVERALL_WEIGHT = 0.6
_READINESS_CONFIDENCE_WEIGHT = 0.4
_READINESS_PENALTY_PER_MISSING_REQUIREMENT = 8.0
_READINESS_MAX_PENALTY = 40.0

_READINESS_LEVEL_THRESHOLDS: tuple[tuple[float, ReadinessLevel], ...] = (
    (80.0, ReadinessLevel.READY),
    (60.0, ReadinessLevel.ALMOST_READY),
    (40.0, ReadinessLevel.NEEDS_PREPARATION),
    (0.0, ReadinessLevel.SIGNIFICANT_GAPS),
)

#: How many CV suggestions to surface at once — enough to be useful,
#: few enough to actually get read and acted on.
_MAX_CV_SUGGESTIONS = 5


def _bucket_for(score: float) -> tuple[str, str]:
    for minimum, label, adjective in _SCORE_BUCKETS:
        if score >= minimum:
            return label, adjective
    return _SCORE_BUCKETS[-1][1], _SCORE_BUCKETS[-1][2]  # pragma: no cover - 0.0 floor always matches above


def _category_display_name(category: str) -> str:
    return _CATEGORY_DISPLAY_NAMES.get(category, category.replace("_", " ").title())


class CareerAssistantService:
    def build_insight(self, match_result: MatchResult, job: JobSnapshot) -> CareerInsight:
        category_insights = self._build_category_insights(match_result)
        return CareerInsight(
            job_title=job.title,
            employer=job.employer,
            overall_score=match_result.overall_score,
            summary=self._build_summary(match_result, job, category_insights),
            category_insights=category_insights,
            strengths=tuple(match_result.strengths),
            missing_skills=tuple(match_result.missing_requirements),
            cv_suggestions=self._build_cv_suggestions(match_result),
            interview_readiness=self._build_interview_readiness(match_result),
        )

    def _build_category_insights(self, match_result: MatchResult) -> tuple[CategoryInsight, ...]:
        insights = []
        # Iterate MATCH_CATEGORIES (not match_result.category_scores) so the
        # display order is always the same, regardless of dict ordering in
        # whatever produced this MatchResult (a fresh rule-based match, an
        # LLM response, or a `JobMatch.analysis` blob loaded back from the
        # database).
        for category in MATCH_CATEGORIES:
            if category not in match_result.category_scores:
                continue
            score = float(match_result.category_scores[category])
            label, adjective = _bucket_for(score)
            insights.append(
                CategoryInsight(
                    category=category,
                    score=score,
                    label=label,
                    explanation=f"{_category_display_name(category)} shows {adjective} ({score:.0f}%).",
                )
            )
        return tuple(insights)

    def _build_summary(
        self, match_result: MatchResult, job: JobSnapshot, category_insights: tuple[CategoryInsight, ...]
    ) -> str:
        _label, adjective = _bucket_for(match_result.overall_score)
        employer_text = f" at {job.employer}" if job.employer else ""
        sentences = [
            f"Your overall match for {job.title}{employer_text} is {match_result.overall_score:.0f}% — {adjective}."
        ]

        ranked = sorted(category_insights, key=lambda insight: insight.score, reverse=True)
        strong = [insight for insight in ranked if insight.score >= 60][:2]
        weak = [insight for insight in ranked if insight.score < 60][-2:]

        if strong:
            names = " and ".join(_category_display_name(insight.category) for insight in strong)
            sentences.append(f"You scored particularly well on {names}, which line up well with this role.")
        if weak:
            names = " and ".join(_category_display_name(insight.category) for insight in weak)
            sentences.append(
                f"Your {names} score{'s' if len(weak) > 1 else ''} suggest{'' if len(weak) > 1 else 's'} "
                "some gaps against what this employer is asking for."
            )
        if not weak and match_result.missing_requirements:
            sentences.append(
                "There are still a few specific requirements from the job listing not yet reflected in your profile."
            )

        return " ".join(sentences)

    def _build_cv_suggestions(self, match_result: MatchResult) -> tuple[CVSuggestion, ...]:
        suggestions: list[CVSuggestion] = []
        seen: set[str] = set()

        def _add(priority: str, text: str) -> None:
            if text not in seen and len(suggestions) < _MAX_CV_SUGGESTIONS:
                seen.add(text)
                suggestions.append(CVSuggestion(priority=priority, suggestion=text))

        for requirement in match_result.missing_requirements:
            _add(
                "high",
                f"Add evidence of '{requirement}' to your CV if you have relevant experience — "
                "this job specifically asks for it.",
            )
        for weakness in match_result.weaknesses:
            _add("medium", f"Address this gap in your CV or cover letter: {weakness}")
        for action in match_result.recommended_actions:
            _add("low", action)

        return tuple(suggestions)

    def _build_interview_readiness(self, match_result: MatchResult) -> InterviewReadiness:
        penalty = min(
            _READINESS_MAX_PENALTY,
            len(match_result.missing_requirements) * _READINESS_PENALTY_PER_MISSING_REQUIREMENT,
        )
        raw_score = (
            match_result.overall_score * _READINESS_OVERALL_WEIGHT
            + match_result.confidence_score * _READINESS_CONFIDENCE_WEIGHT
            - penalty
        )
        readiness_score = max(0.0, min(100.0, raw_score))

        level = ReadinessLevel.SIGNIFICANT_GAPS
        for threshold, candidate_level in _READINESS_LEVEL_THRESHOLDS:
            if readiness_score >= threshold:
                level = candidate_level
                break

        reasoning_parts = [
            f"Based on a {match_result.overall_score:.0f}% overall match "
            f"({match_result.confidence_score:.0f}% confidence),"
        ]
        if match_result.missing_requirements:
            reasoning_parts.append(
                f"with {len(match_result.missing_requirements)} requirement"
                f"{'s' if len(match_result.missing_requirements) != 1 else ''} not yet evidenced,"
            )
        reasoning_parts.append(f"your interview readiness for this job is: {level.display_label.lower()}.")

        focus_areas = tuple((match_result.missing_requirements or match_result.weaknesses)[:3])

        return InterviewReadiness(
            readiness_score=readiness_score,
            level=level,
            reasoning=" ".join(reasoning_parts),
            focus_areas=focus_areas,
        )
