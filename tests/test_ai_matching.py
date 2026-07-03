"""
Tests for the AI matching engine. No real Anthropic API calls are made —
`FakeLLMProvider` (a minimal `LLMProvider` implementation returning a canned
response) verifies the provider abstraction and the matching pipeline's use
of it, exactly as the framework's provider abstraction is meant to allow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.ai.anthropic_provider import AnthropicProvider
from job_automation.ai.cache import MatchCache
from job_automation.ai.llm_provider import LLMProvider, LLMProviderError, LLMResponseError
from job_automation.ai.matching_engine import MatchingEngine
from job_automation.ai.matching_models import (
    MATCH_CATEGORIES,
    CandidateProfile,
    ExperienceEntry,
    JobSnapshot,
    LLMAnalysis,
)
from job_automation.ai.matching_service import MatchingService
from job_automation.ai.profile_builder import build_candidate_profile
from job_automation.ai.prompt_builder import build_system_prompt, build_user_prompt, parse_response
from job_automation.ai.rule_engine import RuleEngine
from job_automation.ai.score_calculator import ScoreCalculator
from job_automation.database.models import Certificate, Employer, Job, JobMatch, User


class FakeLLMProvider(LLMProvider):
    """Minimal LLMProvider test double — proves the matching engine only
    depends on the abstract interface, never a concrete provider."""

    def __init__(self, response: dict | None = None, *, raise_error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._response = response
        self._raise_error = raise_error

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self._raise_error is not None:
            raise self._raise_error
        return json.dumps(self._response)


def _sample_candidate() -> CandidateProfile:
    return CandidateProfile(
        skills=("wound care", "patient assessment"),
        experience=(ExperienceEntry(job_title="Healthcare Assistant", employer="Example Trust"),),
        certificates=("NVQ Level 2",),
        preferred_locations=("London",),
        preferred_salary_min=22000,
        working_pattern_preference="Full-time",
        visa_sponsorship_required=False,
        keywords=("ward",),
    )


def _sample_job(**overrides: object) -> JobSnapshot:
    defaults = dict(
        title="Healthcare Assistant",
        employer="Example NHS Foundation Trust",
        location="London",
        salary_min=22816.0,
        salary_max=24336.0,
        working_pattern="Full-time",
        visa_sponsorship=False,
        description="Wound care and patient assessment duties on a busy ward.",
        requirements=("NVQ Level 2 in Health and Social Care",),
        content_hash="job-hash-1",
    )
    defaults.update(overrides)
    return JobSnapshot(**defaults)  # type: ignore[arg-type]


def _fake_llm_response(**category_overrides: float) -> dict:
    scores = {category: 70.0 for category in MATCH_CATEGORIES}
    scores.update(category_overrides)
    return {
        "category_scores": scores,
        "strengths": ["Good clinical skill overlap"],
        "weaknesses": ["Limited seniority evidence"],
        "missing_requirements": ["NMC PIN not confirmed"],
        "recommended_actions": ["Tailor CV to emphasise wound care experience"],
    }


# --- Rule engine ------------------------------------------------------------


def test_rule_engine_scores_all_categories_with_no_data() -> None:
    engine = RuleEngine()
    scores = engine.score(CandidateProfile(), JobSnapshot(title="Any Job"))
    assert set(scores) == set(MATCH_CATEGORIES)
    # No candidate preferences/data at all -> every category should be a
    # neutral "can't tell" or "no preference expressed" score, never 0.
    assert all(score > 0 for score in scores.values())


def test_rule_engine_rewards_real_overlap_and_penalizes_mismatch() -> None:
    engine = RuleEngine()
    candidate = _sample_candidate()

    good_match = engine.score(candidate, _sample_job())
    assert good_match["skills"] > 50
    assert good_match["location"] == 100.0
    assert good_match["salary"] == 100.0
    assert good_match["visa_sponsorship"] == 100.0  # not required -> non-issue

    bad_match = engine.score(
        candidate,
        _sample_job(location="Glasgow", salary_min=15000, salary_max=15000, description="unrelated role"),
    )
    assert bad_match["location"] < good_match["location"]
    assert bad_match["salary"] < good_match["salary"]


def test_rule_engine_visa_sponsorship_scoring() -> None:
    engine = RuleEngine()
    candidate = CandidateProfile(visa_sponsorship_required=True)

    assert engine.score(candidate, _sample_job(visa_sponsorship=True))["visa_sponsorship"] == 100.0
    assert engine.score(candidate, _sample_job(visa_sponsorship=False))["visa_sponsorship"] == 0.0
    assert engine.score(candidate, _sample_job(visa_sponsorship=None))["visa_sponsorship"] == 50.0


def test_rule_engine_matched_keywords() -> None:
    engine = RuleEngine()
    candidate = _sample_candidate()
    matched = engine.matched_keywords(candidate, _sample_job())
    assert "wound care" in matched
    assert "patient assessment" in matched
    assert "ward" in matched


# --- Prompt builder ----------------------------------------------------------


def test_prompt_builder_separates_system_and_user_prompts() -> None:
    system = build_system_prompt()
    user = build_user_prompt(_sample_candidate(), _sample_job())

    assert "JSON" in system
    assert "category_scores" in system
    assert "Candidate profile" in user
    assert "Job listing" in user
    assert system != user  # genuinely separate, not the same text twice


def test_prompt_builder_parses_valid_response() -> None:
    raw = json.dumps(_fake_llm_response(skills=85.0))
    analysis = parse_response(raw)
    assert analysis.category_scores["skills"] == 85.0
    assert analysis.strengths == ["Good clinical skill overlap"]


def test_prompt_builder_tolerates_markdown_code_fence() -> None:
    raw = "```json\n" + json.dumps(_fake_llm_response()) + "\n```"
    analysis = parse_response(raw)
    assert set(analysis.category_scores) == set(MATCH_CATEGORIES)


def test_prompt_builder_rejects_malformed_response() -> None:
    with pytest.raises(LLMResponseError):
        parse_response("this is not json")

    with pytest.raises(LLMResponseError):
        parse_response(json.dumps({"category_scores": {"skills": 70.0}}))  # missing categories


# --- Score calculator --------------------------------------------------------


def test_score_calculator_blends_rule_and_llm_scores() -> None:
    calculator = ScoreCalculator()
    rule_scores = {category: 50.0 for category in MATCH_CATEGORIES}
    llm_analysis = LLMAnalysis(category_scores={category: 100.0 for category in MATCH_CATEGORIES})

    result = calculator.calculate(rule_scores, llm_analysis, matched_keywords=["wound care"])

    # 50 * 0.4 + 100 * 0.6 = 80 for every category -> overall should be 80.
    assert result.overall_score == 80.0
    assert result.used_llm is True
    assert result.strengths == []  # LLM's own (empty in this fixture) strengths are used verbatim


def test_score_calculator_falls_back_to_rule_only_without_llm() -> None:
    calculator = ScoreCalculator()
    rule_scores = {category: 90.0 for category in MATCH_CATEGORIES}

    result = calculator.calculate(rule_scores, None, matched_keywords=[])

    assert result.overall_score == 90.0
    assert result.used_llm is False
    assert len(result.strengths) > 0  # fallback strengths generated from rule scores
    assert result.confidence_score < 60  # rule-only confidence is capped low


def test_score_calculator_confidence_drops_with_rule_llm_disagreement() -> None:
    calculator = ScoreCalculator()
    rule_scores = {category: 20.0 for category in MATCH_CATEGORIES}
    agreeing_llm = LLMAnalysis(category_scores={category: 25.0 for category in MATCH_CATEGORIES})
    disagreeing_llm = LLMAnalysis(category_scores={category: 95.0 for category in MATCH_CATEGORIES})

    agreeing_result = calculator.calculate(rule_scores, agreeing_llm, matched_keywords=[])
    disagreeing_result = calculator.calculate(rule_scores, disagreeing_llm, matched_keywords=[])

    assert agreeing_result.confidence_score > disagreeing_result.confidence_score


# --- Provider abstraction ----------------------------------------------------


def test_anthropic_provider_is_an_llm_provider() -> None:
    assert issubclass(AnthropicProvider, LLMProvider)


def test_anthropic_provider_requires_an_api_key() -> None:
    with pytest.raises(LLMProviderError):
        AnthropicProvider(api_key=None)
    with pytest.raises(LLMProviderError):
        AnthropicProvider(api_key="")


# --- Provider abstraction + caching (via MatchingEngine) ---------------------


def test_matching_engine_uses_injected_provider_abstraction(tmp_path: Path) -> None:
    provider = FakeLLMProvider(response=_fake_llm_response(skills=90.0))
    engine = MatchingEngine(llm_provider=provider, cache=MatchCache(cache_dir=tmp_path))

    result = engine.evaluate(_sample_candidate(), _sample_job())

    assert result.used_llm is True
    assert len(provider.calls) == 1
    system_prompt, user_prompt = provider.calls[0]
    assert "JSON" in system_prompt
    assert "Healthcare Assistant" in user_prompt


def test_matching_engine_caches_llm_analysis_to_avoid_repeated_calls(tmp_path: Path) -> None:
    provider = FakeLLMProvider(response=_fake_llm_response())
    cache = MatchCache(cache_dir=tmp_path)
    engine = MatchingEngine(llm_provider=provider, cache=cache)
    candidate = _sample_candidate()
    job = _sample_job()

    first = engine.evaluate(candidate, job)
    second = engine.evaluate(candidate, job)

    assert len(provider.calls) == 1, "second evaluate() of the same (candidate, job) must hit the cache"
    assert first.overall_score == second.overall_score


def test_matching_engine_cache_misses_for_a_different_job(tmp_path: Path) -> None:
    provider = FakeLLMProvider(response=_fake_llm_response())
    engine = MatchingEngine(llm_provider=provider, cache=MatchCache(cache_dir=tmp_path))
    candidate = _sample_candidate()

    engine.evaluate(candidate, _sample_job(content_hash="job-hash-1"))
    engine.evaluate(candidate, _sample_job(content_hash="job-hash-2"))

    assert len(provider.calls) == 2


def test_matching_engine_falls_back_gracefully_when_llm_fails(tmp_path: Path) -> None:
    provider = FakeLLMProvider(raise_error=LLMProviderError("simulated provider outage"))
    engine = MatchingEngine(llm_provider=provider, cache=MatchCache(cache_dir=tmp_path))

    result = engine.evaluate(_sample_candidate(), _sample_job())

    assert result.used_llm is False
    assert result.overall_score > 0  # still a complete, usable result


# --- Matching service / persistence ------------------------------------------


def test_matching_service_inserts_then_updates_job_match(db_session: Session) -> None:
    user = User(email="candidate@example.com", full_name="Test Candidate", hashed_password="unused-in-these-tests")
    employer = Employer(name="Example NHS Foundation Trust")
    job = Job(
        employer=employer,
        title="Healthcare Assistant",
        location="London",
        source_site="nhs_jobs",
        external_id="REF-1",
        url="https://example.com/job/1",
        description="Wound care and patient assessment duties.",
        requirements=["NVQ Level 2"],
        salary_min=22816,
        salary_max=24336,
        is_active=True,
    )
    db_session.add_all([user, employer, job])
    db_session.commit()

    service = MatchingService(db_session, MatchingEngine())
    candidate = _sample_candidate()

    match1 = service.evaluate_job(job, candidate, user_id=user.id)
    db_session.commit()
    assert match1.match_score > 0
    assert match1.analysis is not None
    assert "category_scores" in match1.analysis
    assert db_session.scalar(select(JobMatch).where(JobMatch.id == match1.id)) is not None

    match2 = service.evaluate_job(job, candidate, user_id=user.id)
    db_session.commit()
    assert match2.id == match1.id, "re-evaluating the same (job, user) must update, not duplicate"

    total_matches = len(db_session.scalars(select(JobMatch)).all())
    assert total_matches == 1


def test_matching_service_evaluates_only_active_jobs(db_session: Session) -> None:
    user = User(email="candidate2@example.com", full_name="Test Candidate 2", hashed_password="unused-in-these-tests")
    employer = Employer(name="Example NHS Foundation Trust")
    active_job = Job(
        employer=employer,
        title="Healthcare Assistant",
        source_site="nhs_jobs",
        external_id="REF-ACTIVE",
        url="https://example.com/job/active",
        is_active=True,
    )
    inactive_job = Job(
        employer=employer,
        title="Support Worker",
        source_site="nhs_jobs",
        external_id="REF-INACTIVE",
        url="https://example.com/job/inactive",
        is_active=False,
    )
    db_session.add_all([user, employer, active_job, inactive_job])
    db_session.commit()

    service = MatchingService(db_session, MatchingEngine())
    matches = service.evaluate_active_jobs(_sample_candidate(), user_id=user.id)
    db_session.commit()

    assert len(matches) == 1
    assert matches[0].job_id == active_job.id


# --- Profile builder ----------------------------------------------------------


def _write_profile_json(tmp_path: Path, **overrides: object) -> Path:
    data = {
        "skills": ["wound care"],
        "work_experience": [{"job_title": "Healthcare Assistant", "employer": "Example Trust"}],
        "qualifications": [{"name": "NVQ Level 2", "awarding_body": "City & Guilds", "year": "2022"}],
        "certifications": ["DBS Check"],
        "right_to_work_uk": True,
        "preferred_locations": ["London"],
        "preferred_salary_min": 22000,
        "preferred_band": "Band 2",
        "working_pattern_preference": "Full-time",
        "keywords": ["ward"],
    }
    data.update(overrides)
    path = tmp_path / "candidate_profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_profile_builder_reads_json_file(tmp_path: Path) -> None:
    profile_path = _write_profile_json(tmp_path)
    profile = build_candidate_profile(profile_path)

    assert profile.skills == ("wound care",)
    assert profile.experience[0].job_title == "Healthcare Assistant"
    assert profile.education[0].qualification == "NVQ Level 2"
    assert "DBS Check" in profile.certificates
    assert profile.preferred_locations == ("London",)
    assert profile.preferred_salary_min == 22000
    assert profile.preferred_band == "Band 2"


def test_profile_builder_infers_visa_sponsorship_from_right_to_work(tmp_path: Path) -> None:
    profile_path = _write_profile_json(tmp_path, right_to_work_uk=False, visa_sponsorship_required=None)
    profile = build_candidate_profile(profile_path)
    assert profile.visa_sponsorship_required is True  # no right to work -> sponsorship needed

    profile_path_explicit = _write_profile_json(
        tmp_path, right_to_work_uk=False, visa_sponsorship_required=False
    )
    profile_explicit = build_candidate_profile(profile_path_explicit)
    assert profile_explicit.visa_sponsorship_required is False  # explicit value takes precedence


def test_profile_builder_merges_database_certificates(db_session: Session, tmp_path: Path) -> None:
    user = User(email="candidate3@example.com", full_name="Test Candidate 3", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()
    db_session.add(Certificate(user_id=user.id, name="Manual Handling"))
    db_session.commit()

    profile_path = _write_profile_json(tmp_path)
    profile = build_candidate_profile(profile_path, session=db_session, user_id=user.id)

    assert "DBS Check" in profile.certificates  # from the JSON file
    assert "Manual Handling" in profile.certificates  # merged in from the database
