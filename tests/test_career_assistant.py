"""
Tests for the AI Career Assistant module: the deterministic, zero-LLM-cost
`CareerAssistantService` (score explanations, CV suggestions, interview
readiness), the optional real-LLM `CareerInsightGenerator` (no real API
calls — `FakeLLMProvider`, same pattern as tests/test_document_generation.py),
and `DocumentService.generate_career_insight()`'s wiring.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.career_assistant.career_assistant_models import ReadinessLevel
from job_automation.career_assistant.career_assistant_service import CareerAssistantService
from job_automation.database.models import User
from job_automation.documents.career_insight_generator import CareerInsightGenerator
from job_automation.documents.document_models import DocumentType
from job_automation.documents.document_service import DocumentService
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation


class FakeLLMProvider(LLMProvider):
    def __init__(self, response: str = "## How you match\nYou're a strong candidate.") -> None:
        self.calls: list[tuple[str, str]] = []
        self._response = response

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self._response


def _sample_job(*, employer: str | None = "Example NHS Foundation Trust") -> JobSnapshot:
    return JobSnapshot(title="Healthcare Assistant", employer=employer)


def _match_result(
    *,
    overall_score: float = 76.0,
    confidence_score: float = 55.0,
    category_scores: dict | None = None,
    strengths: list | None = None,
    weaknesses: list | None = None,
    missing_requirements: list | None = None,
    recommended_actions: list | None = None,
) -> MatchResult:
    return MatchResult(
        overall_score=overall_score,
        confidence_score=confidence_score,
        category_scores=category_scores if category_scores is not None else {"skills": 36.0, "experience": 100.0},
        matched_keywords=[],
        strengths=strengths if strengths is not None else ["Strong care background"],
        weaknesses=weaknesses if weaknesses is not None else ["No NMC registration"],
        missing_requirements=missing_requirements if missing_requirements is not None else ["NVQ Level 3"],
        recommended_actions=recommended_actions if recommended_actions is not None else ["Highlight DBS check"],
        used_llm=False,
    )


# --- CareerAssistantService: category insights ----------------------------------


@pytest.mark.parametrize(
    "score,expected_label",
    [(95.0, "Strong"), (80.0, "Strong"), (79.0, "Good"), (60.0, "Good"), (59.0, "Moderate"), (40.0, "Moderate"), (39.0, "Weak"), (0.0, "Weak")],
)
def test_category_score_bucket_boundaries(score: float, expected_label: str) -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(category_scores={"skills": score}), _sample_job()
    )
    assert insight.category_insights[0].label == expected_label


def test_category_insights_follow_match_categories_order_not_dict_order() -> None:
    # Deliberately out-of-order dict — output must still follow
    # ai.matching_models.MATCH_CATEGORIES's canonical order.
    scores = {"employer_quality": 90.0, "skills": 50.0, "experience": 70.0}
    insight = CareerAssistantService().build_insight(_match_result(category_scores=scores), _sample_job())
    assert [c.category for c in insight.category_insights] == ["skills", "experience", "employer_quality"]


def test_category_insights_only_include_categories_present_in_match_result() -> None:
    insight = CareerAssistantService().build_insight(_match_result(category_scores={"skills": 50.0}), _sample_job())
    assert len(insight.category_insights) == 1


# --- CareerAssistantService: summary ---------------------------------------------


def test_summary_mentions_job_title_and_employer() -> None:
    insight = CareerAssistantService().build_insight(_match_result(), _sample_job())
    assert "Healthcare Assistant" in insight.summary
    assert "Example NHS Foundation Trust" in insight.summary
    assert "76%" in insight.summary


def test_summary_handles_missing_employer_gracefully() -> None:
    insight = CareerAssistantService().build_insight(_match_result(), _sample_job(employer=None))
    assert " at None" not in insight.summary


def test_summary_calls_out_strong_and_weak_categories() -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(category_scores={"skills": 20.0, "experience": 95.0}), _sample_job()
    )
    assert "Experience" in insight.summary
    assert "Skills" in insight.summary


# --- CareerAssistantService: CV suggestions --------------------------------------


def test_cv_suggestions_prioritize_missing_requirements_highest() -> None:
    insight = CareerAssistantService().build_insight(_match_result(), _sample_job())
    priorities = [s.priority for s in insight.cv_suggestions]
    assert priorities[0] == "high"
    assert "NVQ Level 3" in insight.cv_suggestions[0].suggestion


def test_cv_suggestions_empty_when_match_has_no_gaps() -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(weaknesses=[], missing_requirements=[], recommended_actions=[]), _sample_job()
    )
    assert insight.cv_suggestions == ()


def test_cv_suggestions_capped_at_five() -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(
            missing_requirements=[f"req{i}" for i in range(10)],
            weaknesses=[],
            recommended_actions=[],
        ),
        _sample_job(),
    )
    assert len(insight.cv_suggestions) == 5


# --- CareerAssistantService: interview readiness ---------------------------------


def test_interview_readiness_high_score_no_gaps_is_ready() -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(overall_score=95.0, confidence_score=90.0, missing_requirements=[]), _sample_job()
    )
    assert insight.interview_readiness.level == ReadinessLevel.READY
    assert insight.interview_readiness.readiness_score > 80


def test_interview_readiness_low_score_many_gaps_is_significant_gaps() -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(
            overall_score=20.0, confidence_score=20.0, missing_requirements=["a", "b", "c", "d", "e"]
        ),
        _sample_job(),
    )
    assert insight.interview_readiness.level == ReadinessLevel.SIGNIFICANT_GAPS
    assert insight.interview_readiness.readiness_score == 0.0  # floored, never negative


def test_interview_readiness_focus_areas_prefer_missing_requirements_over_weaknesses() -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(missing_requirements=["NVQ Level 3"], weaknesses=["No NMC registration"]), _sample_job()
    )
    assert insight.interview_readiness.focus_areas == ("NVQ Level 3",)


def test_interview_readiness_falls_back_to_weaknesses_when_no_missing_requirements() -> None:
    insight = CareerAssistantService().build_insight(
        _match_result(missing_requirements=[], weaknesses=["No NMC registration"]), _sample_job()
    )
    assert insight.interview_readiness.focus_areas == ("No NMC registration",)


def test_interview_readiness_reasoning_is_readable() -> None:
    insight = CareerAssistantService().build_insight(_match_result(), _sample_job())
    reasoning = insight.interview_readiness.reasoning
    assert "76%" in reasoning
    assert "55%" in reasoning
    assert "interview readiness" in reasoning.lower()


# --- CareerInsightGenerator (optional real-LLM narrative) ------------------------


def test_career_insight_generator_returns_document() -> None:
    provider = FakeLLMProvider("## How you match\nYou're well suited to this ward-based role.")
    generator = CareerInsightGenerator(provider)

    document = generator.generate(
        CandidateProfile(personal_information=PersonalInformation(full_name="Jane Doe")),
        _sample_job(),
        _match_result(),
    )

    assert document.document_type == DocumentType.CAREER_INSIGHT
    assert "How you match" in document.content
    system_prompt, user_prompt = provider.calls[0]
    assert "CRITICAL RULES" in system_prompt
    assert "Jane Doe" in user_prompt
    assert "Healthcare Assistant" in user_prompt


# --- DocumentService wiring -------------------------------------------------------


def test_document_service_generates_career_insight(db_session: Session) -> None:
    user = User(email="career-assistant@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    provider = FakeLLMProvider("## How you match\nGreat alignment overall.")
    service = DocumentService(db_session, provider)

    record = service.generate_career_insight(
        CandidateProfile(personal_information=PersonalInformation(full_name="Jane Doe")),
        _sample_job(),
        _match_result(),
        user_id=user.id,
    )
    db_session.commit()

    assert record.document_type == DocumentType.CAREER_INSIGHT.value
    assert "How you match" in record.content
