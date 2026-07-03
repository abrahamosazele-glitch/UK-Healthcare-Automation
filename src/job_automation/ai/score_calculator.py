"""
Combines rule-based and LLM-based category scores into one `MatchResult`.

Two independent weightings are used:
- `CATEGORY_WEIGHTS` — how much each of the 8 categories contributes to the
  single `overall_score` (skills and experience weighted highest, since
  they're the strongest predictors of a genuine fit for a healthcare role;
  visa sponsorship and employer quality lowest, since they're binary/coarse
  signals rather than nuanced fit indicators).
- `RULE_WEIGHT` / `LLM_WEIGHT` — within *each* category, how much the
  deterministic rule score vs. the LLM's semantic score counts. The LLM is
  weighted higher (0.6 vs 0.4) because it can recognize equivalent skills
  phrased differently, which substring matching cannot — but the rule score
  still anchors the result so one LLM call producing an outlier score
  doesn't swing the outcome entirely.

When no LLM analysis is available (no provider configured, or the call
failed), every category falls back to 100% rule-based, and strengths/
weaknesses/missing_requirements/recommended_actions are derived from the
rule scores themselves via simple thresholds — the pipeline always produces
a complete, usable result, never an empty "no data" response.
"""

from __future__ import annotations

from job_automation.ai.matching_models import MATCH_CATEGORIES, LLMAnalysis, MatchResult

CATEGORY_WEIGHTS: dict[str, float] = {
    "skills": 0.25,
    "experience": 0.15,
    "qualifications": 0.10,
    "location": 0.15,
    "salary": 0.15,
    "working_pattern": 0.10,
    "visa_sponsorship": 0.05,
    "employer_quality": 0.05,
}
assert abs(sum(CATEGORY_WEIGHTS.values()) - 1.0) < 1e-9, "CATEGORY_WEIGHTS must sum to 1.0"

RULE_WEIGHT = 0.4
LLM_WEIGHT = 0.6

_STRENGTH_THRESHOLD = 75.0
_WEAKNESS_THRESHOLD = 40.0

_CATEGORY_LABELS = {
    "skills": "Skills",
    "experience": "Experience",
    "qualifications": "Qualifications",
    "location": "Location",
    "salary": "Salary",
    "working_pattern": "Working pattern",
    "visa_sponsorship": "Visa sponsorship",
    "employer_quality": "Employer",
}


class ScoreCalculator:
    def calculate(
        self,
        rule_scores: dict[str, float],
        llm_analysis: LLMAnalysis | None,
        *,
        matched_keywords: list[str],
    ) -> MatchResult:
        combined_scores = self._combine_category_scores(rule_scores, llm_analysis)
        overall = round(sum(combined_scores[c] * CATEGORY_WEIGHTS[c] for c in MATCH_CATEGORIES), 1)
        confidence = self._confidence(rule_scores, llm_analysis)

        if llm_analysis is not None:
            strengths = llm_analysis.strengths
            weaknesses = llm_analysis.weaknesses
            missing_requirements = llm_analysis.missing_requirements
            recommended_actions = llm_analysis.recommended_actions
        else:
            strengths, weaknesses = self._fallback_strengths_and_weaknesses(rule_scores)
            missing_requirements = []
            recommended_actions = self._fallback_recommended_actions(rule_scores)

        return MatchResult(
            overall_score=overall,
            confidence_score=confidence,
            category_scores=combined_scores,
            matched_keywords=matched_keywords,
            strengths=strengths,
            weaknesses=weaknesses,
            missing_requirements=missing_requirements,
            recommended_actions=recommended_actions,
            used_llm=llm_analysis is not None,
        )

    def _combine_category_scores(
        self, rule_scores: dict[str, float], llm_analysis: LLMAnalysis | None
    ) -> dict[str, float]:
        combined = {}
        for category in MATCH_CATEGORIES:
            rule_score = rule_scores.get(category, 50.0)
            if llm_analysis is not None and category in llm_analysis.category_scores:
                llm_score = llm_analysis.category_scores[category]
                combined[category] = round(rule_score * RULE_WEIGHT + llm_score * LLM_WEIGHT, 1)
            else:
                combined[category] = rule_score
        return combined

    def _confidence(self, rule_scores: dict[str, float], llm_analysis: LLMAnalysis | None) -> float:
        """Confidence reflects how much the assessment can be trusted: rule-
        only results are capped lower (substring matching alone is weak
        evidence); when the LLM is used, confidence is reduced by how much
        it disagreed with the rule-based score per category — large
        disagreement suggests at least one of the two signals is unreliable
        for this particular job/candidate pair."""
        if llm_analysis is None:
            return 55.0

        differences = [
            abs(rule_scores.get(category, 50.0) - llm_analysis.category_scores[category])
            for category in MATCH_CATEGORIES
            if category in llm_analysis.category_scores
        ]
        if not differences:
            return 55.0
        average_difference = sum(differences) / len(differences)
        # 0 average difference -> 95 confidence; 100 average difference (total
        # disagreement) -> floor of 40.
        confidence = 95.0 - (average_difference * 0.55)
        return round(max(40.0, min(95.0, confidence)), 1)

    def _fallback_strengths_and_weaknesses(
        self, rule_scores: dict[str, float]
    ) -> tuple[list[str], list[str]]:
        strengths = [
            f"{_CATEGORY_LABELS[c]} looks like a strong match"
            for c in MATCH_CATEGORIES
            if rule_scores.get(c, 50.0) >= _STRENGTH_THRESHOLD
        ]
        weaknesses = [
            f"{_CATEGORY_LABELS[c]} may not be a good match"
            for c in MATCH_CATEGORIES
            if rule_scores.get(c, 50.0) <= _WEAKNESS_THRESHOLD
        ]
        return strengths, weaknesses

    def _fallback_recommended_actions(self, rule_scores: dict[str, float]) -> list[str]:
        if rule_scores.get("skills", 50.0) <= _WEAKNESS_THRESHOLD:
            return ["Review the job description closely before applying — the listed skills may not align well."]
        return ["Review the full job listing before applying."]
