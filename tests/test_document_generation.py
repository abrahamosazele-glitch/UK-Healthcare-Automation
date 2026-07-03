"""
Tests for the Document Generation Intelligence subsystem. No real LLM calls
are made — `FakeLLMProvider` (same pattern as tests/test_ai_matching.py)
returns canned prose, so generation, validation, export, and persistence
are all verified deterministically.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from job_automation.ai.cache import AIResponseCache, complete_with_cache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.database.models import Employer, Job, User
from job_automation.documents.application_answer_generator import ApplicationAnswerGenerator
from job_automation.documents.cover_letter_generator import CoverLetterGenerator
from job_automation.documents.document_models import DocumentStatus, DocumentType, GeneratedDocument
from job_automation.documents.document_repository import DocumentRepository
from job_automation.documents.document_service import DocumentService
from job_automation.documents.document_validator import DocumentValidator
from job_automation.documents.export_manager import ExportManager
from job_automation.documents.interview_prep_generator import InterviewPrepGenerator
from job_automation.documents.skills_gap_generator import SkillsGapGenerator
from job_automation.documents.supporting_statement_generator import SupportingStatementGenerator
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.certificate_parser import Certificate
from job_automation.profile.employment_history import EmploymentEntry


class FakeLLMProvider(LLMProvider):
    def __init__(self, response: str = "Generated document content.") -> None:
        self.calls: list[tuple[str, str]] = []
        self._response = response

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self._response


def _sample_profile() -> CandidateProfile:
    return CandidateProfile(
        personal_information=PersonalInformation(full_name="Jane Doe"),
        employment_history=(
            EmploymentEntry(
                job_title="Healthcare Assistant",
                employer="Example NHS Foundation Trust",
                start_date="2022-01-01",
                responsibilities=("Provided patient care on a busy ward",),
            ),
        ),
        skills=("wound care", "patient_care"),
        certificates=(Certificate(name="DBS Check"),),
    )


def _sample_job() -> JobSnapshot:
    return JobSnapshot(
        title="Healthcare Assistant",
        employer="Example NHS Foundation Trust",
        description="Provide compassionate care on a busy ward.",
        requirements=("NVQ Level 2",),
    )


def _sample_match_result() -> MatchResult:
    return MatchResult(
        overall_score=85.0,
        confidence_score=75.0,
        category_scores={},
        matched_keywords=["wound care"],
        strengths=["Strong clinical skill overlap"],
        weaknesses=[],
        missing_requirements=[],
        recommended_actions=[],
        used_llm=False,
    )


# --- Generation ---------------------------------------------------------------


def test_supporting_statement_generator_builds_grounded_prompts_and_returns_document() -> None:
    provider = FakeLLMProvider("I am excited to apply, drawing on my ward experience.")
    generator = SupportingStatementGenerator(provider)

    document = generator.generate(_sample_profile(), _sample_job(), _sample_match_result())

    assert document.document_type == DocumentType.SUPPORTING_STATEMENT
    assert document.job_title == "Healthcare Assistant"
    assert document.employer == "Example NHS Foundation Trust"
    assert "ward experience" in document.content

    system_prompt, user_prompt = provider.calls[0]
    assert "CRITICAL RULES" in system_prompt
    assert "safeguarding" in system_prompt.lower()
    assert "Jane Doe" in user_prompt
    assert "Healthcare Assistant" in user_prompt
    assert "Strong clinical skill overlap" in user_prompt  # match_result context included


def test_cover_letter_generator_returns_document() -> None:
    provider = FakeLLMProvider("Dear Hiring Manager, I am writing to apply...")
    generator = CoverLetterGenerator(provider)

    document = generator.generate(_sample_profile(), _sample_job())

    assert document.document_type == DocumentType.COVER_LETTER
    assert "Dear Hiring Manager" in document.content


def test_application_answer_generator_respects_word_limit() -> None:
    long_response = " ".join(f"word{i}" for i in range(200))
    provider = FakeLLMProvider(long_response)
    generator = ApplicationAnswerGenerator(provider)

    document = generator.generate(
        _sample_profile(), _sample_job(), "Why do you want this role?", max_words=20
    )

    assert document.document_type == DocumentType.APPLICATION_ANSWER
    assert document.question == "Why do you want this role?"
    word_count = len(document.content.rstrip(".").split())
    assert word_count <= 21  # 20 words + the "..." truncation marker counted as one token


def test_application_answer_generator_leaves_short_answers_untouched() -> None:
    provider = FakeLLMProvider("A short, genuine answer.")
    generator = ApplicationAnswerGenerator(provider)

    document = generator.generate(_sample_profile(), _sample_job(), "Why this role?", max_words=150)

    assert document.content == "A short, genuine answer."


# --- Interview prep / skills-gap analysis (Anthropic AI Integration) -----------


def test_interview_prep_generator_returns_document_with_interview_context() -> None:
    provider = FakeLLMProvider("## Likely questions\nTell me about your ward experience.")
    generator = InterviewPrepGenerator(provider)

    document = generator.generate(
        _sample_profile(), _sample_job(), _sample_match_result(), interview_type="video", interview_stage="first_stage"
    )

    assert document.document_type == DocumentType.INTERVIEW_PREP
    assert "Likely questions" in document.content
    system_prompt, user_prompt = provider.calls[0]
    assert "video" in user_prompt
    assert "first_stage".replace("_", " ") in user_prompt


def test_skills_gap_generator_returns_document() -> None:
    provider = FakeLLMProvider("## Gaps\nNo NMC registration listed.")
    generator = SkillsGapGenerator(provider)

    document = generator.generate(_sample_profile(), _sample_job(), _sample_match_result())

    assert document.document_type == DocumentType.SKILLS_GAP_ANALYSIS
    assert "Gaps" in document.content


def test_complete_with_cache_is_a_no_op_pass_through_when_cache_is_none() -> None:
    provider = FakeLLMProvider("fresh content")
    result = complete_with_cache(
        provider, None, kind="supporting_statement", system_prompt="s", user_prompt="u", max_tokens=100
    )
    assert result == "fresh content"
    assert len(provider.calls) == 1


def test_complete_with_cache_hits_on_identical_kind_and_prompts(tmp_path: Path) -> None:
    cache = AIResponseCache(cache_dir=tmp_path)
    provider = FakeLLMProvider("first response")

    first = complete_with_cache(
        provider, cache, kind="cover_letter", system_prompt="s", user_prompt="u", max_tokens=100
    )
    provider._response = "a different response"  # would be returned if the cache were bypassed
    second = complete_with_cache(
        provider, cache, kind="cover_letter", system_prompt="s", user_prompt="u", max_tokens=100
    )

    assert first == second == "first response"
    assert len(provider.calls) == 1


def test_complete_with_cache_misses_for_a_different_kind(tmp_path: Path) -> None:
    cache = AIResponseCache(cache_dir=tmp_path)
    provider = FakeLLMProvider("response")

    complete_with_cache(provider, cache, kind="cover_letter", system_prompt="s", user_prompt="u", max_tokens=100)
    complete_with_cache(provider, cache, kind="skills_gap_analysis", system_prompt="s", user_prompt="u", max_tokens=100)

    assert len(provider.calls) == 2  # same prompts, different "kind" -> different cache key


def test_generators_accept_cache_and_avoid_a_second_llm_call(tmp_path: Path) -> None:
    """Every one of the five generators funnels through `complete_with_cache`
    — this verifies that wiring for the two newest ones (the three existing
    generators' identical behavior is exercised via `test_matching_engine
    _caches_llm_analysis_...`-style coverage in `test_ai_matching.py` for the
    match-cache path, and directly below via `complete_with_cache` itself)."""
    cache = AIResponseCache(cache_dir=tmp_path)
    provider = FakeLLMProvider("Cached interview prep content.")
    generator = InterviewPrepGenerator(provider, cache=cache)

    generator.generate(_sample_profile(), _sample_job())
    generator.generate(_sample_profile(), _sample_job())

    assert len(provider.calls) == 1  # second call was served from cache


# --- Validation (unsupported claims) ------------------------------------------


def test_validator_flags_uncertified_claim() -> None:
    profile = _sample_profile()  # only has "DBS Check"
    document = GeneratedDocument(
        document_type=DocumentType.SUPPORTING_STATEMENT,
        content="I hold a valid Manual Handling certificate and provide excellent care.",
    )
    issues = DocumentValidator().validate(document, profile)
    assert any("manual handling" in issue.message.lower() for issue in issues)


def test_validator_flags_unregistered_professional_body() -> None:
    profile = _sample_profile()  # no professional_registrations
    document = GeneratedDocument(
        document_type=DocumentType.COVER_LETTER,
        content="As an NMC registered nurse, I bring strong clinical expertise.",
    )
    issues = DocumentValidator().validate(document, profile)
    assert any(issue.severity == "error" and "nmc" in issue.message.lower() for issue in issues)


def test_validator_flags_inflated_years_of_experience() -> None:
    profile = _sample_profile()  # ~2-3 years based on a single 2022-01-01 start date
    document = GeneratedDocument(
        document_type=DocumentType.SUPPORTING_STATEMENT,
        content="With over 15 years of experience in healthcare, I am well suited to this role.",
    )
    issues = DocumentValidator().validate(document, profile)
    assert any("15 years" in (issue.claim or "") for issue in issues)


def test_validator_does_not_flag_genuinely_supported_content() -> None:
    profile = _sample_profile()
    document = GeneratedDocument(
        document_type=DocumentType.SUPPORTING_STATEMENT,
        content="I hold a valid DBS Check and have provided patient care on a busy ward.",
    )
    issues = DocumentValidator().validate(document, profile)
    assert issues == []


# --- Export --------------------------------------------------------------------


def test_export_manager_writes_markdown_and_txt(tmp_path: Path) -> None:
    manager = ExportManager(export_root=tmp_path)
    document = GeneratedDocument(
        document_type=DocumentType.COVER_LETTER,
        content="Dear Hiring Manager, ...",
        job_title="Healthcare Assistant",
        employer="Example NHS Foundation Trust",
    )

    paths = manager.export_all(document)

    assert paths["markdown"].exists()
    assert paths["txt"].exists()
    assert paths["markdown"].suffix == ".md"
    markdown_text = paths["markdown"].read_text(encoding="utf-8")
    assert "# Cover Letter" in markdown_text
    assert "Example NHS Foundation Trust" in markdown_text
    assert "Dear Hiring Manager" in markdown_text

    txt_text = paths["txt"].read_text(encoding="utf-8")
    assert "Dear Hiring Manager" in txt_text


def test_export_manager_includes_validation_issues_in_markdown(tmp_path: Path) -> None:
    from job_automation.documents.document_models import DocumentValidationIssue

    manager = ExportManager(export_root=tmp_path)
    document = GeneratedDocument(
        document_type=DocumentType.SUPPORTING_STATEMENT,
        content="Some content.",
        status=DocumentStatus.NEEDS_REVIEW,
        validation_issues=[DocumentValidationIssue(severity="warning", message="Test issue")],
    )
    path = manager.export_markdown(document)
    text = path.read_text(encoding="utf-8")
    assert "Review notes" in text
    assert "Test issue" in text


def test_export_manager_avoids_filename_collisions(tmp_path: Path) -> None:
    manager = ExportManager(export_root=tmp_path)
    document = GeneratedDocument(document_type=DocumentType.COVER_LETTER, content="A", job_title="Nurse")

    first = manager.export_markdown(document, filename_hint="fixed-name")
    second = manager.export_markdown(document, filename_hint="fixed-name")

    assert first != second
    assert first.exists() and second.exists()


# --- Persistence / service ------------------------------------------------------


def test_document_repository_create_get_update(db_session: Session) -> None:
    user = User(email="candidate@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    repository = DocumentRepository(db_session)
    record = repository.create(
        user_id=user.id,
        document_type=DocumentType.SUPPORTING_STATEMENT.value,
        status=DocumentStatus.DRAFT.value,
        content="Some content",
    )
    db_session.commit()

    assert repository.get(record.id) is not None
    updated = repository.update_status(record, DocumentStatus.APPROVED.value, review_notes="Good")
    db_session.commit()
    assert updated.status == "approved"
    assert updated.review_notes == "Good"


def test_document_service_generates_validates_and_saves(db_session: Session) -> None:
    user = User(email="candidate2@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    provider = FakeLLMProvider("I hold a valid DBS Check and provided patient care on a busy ward.")
    service = DocumentService(db_session, provider)

    record = service.generate_supporting_statement(
        _sample_profile(), _sample_job(), _sample_match_result(), user_id=user.id
    )
    db_session.commit()

    assert record.status == DocumentStatus.DRAFT.value  # no validation issues for this grounded content
    assert record.validation_issues is None


def test_document_service_marks_needs_review_when_validation_flags_something(db_session: Session) -> None:
    user = User(email="candidate3@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    provider = FakeLLMProvider("I am NMC registered with 20 years of experience.")
    service = DocumentService(db_session, provider)

    record = service.generate_cover_letter(_sample_profile(), _sample_job(), user_id=user.id)
    db_session.commit()

    assert record.status == DocumentStatus.NEEDS_REVIEW.value
    assert record.validation_issues is not None
    assert len(record.validation_issues) >= 1


def test_document_service_approve_and_reject_workflow(db_session: Session) -> None:
    user = User(email="candidate4@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    provider = FakeLLMProvider("Clean, grounded content with a DBS Check mention.")
    service = DocumentService(db_session, provider)

    record1 = service.generate_supporting_statement(_sample_profile(), _sample_job(), user_id=user.id)
    record2 = service.generate_cover_letter(_sample_profile(), _sample_job(), user_id=user.id)
    db_session.commit()

    assert len(service.list_for_review(user.id)) == 2

    approved = service.approve(record1.id, review_notes="Looks great")
    rejected = service.reject(record2.id, review_notes="Needs more detail")
    db_session.commit()

    assert len(service.list_for_review(user.id)) == 0
    assert approved.status == DocumentStatus.APPROVED.value
    assert rejected.status == DocumentStatus.REJECTED.value
    assert rejected.review_notes == "Needs more detail"


def test_document_service_generates_interview_prep(db_session: Session) -> None:
    user = User(email="candidate6@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    provider = FakeLLMProvider("## Likely questions\nWhat drew you to this ward?")
    service = DocumentService(db_session, provider)

    record = service.generate_interview_prep(
        _sample_profile(), _sample_job(), _sample_match_result(), user_id=user.id, interview_type="video"
    )
    db_session.commit()

    assert record.document_type == DocumentType.INTERVIEW_PREP.value
    assert "Likely questions" in record.content


def test_document_service_generates_skills_gap_analysis(db_session: Session) -> None:
    user = User(email="candidate7@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()

    provider = FakeLLMProvider("## Gaps\nNo formal wound care certificate on file.")
    service = DocumentService(db_session, provider)

    record = service.generate_skills_gap_analysis(_sample_profile(), _sample_job(), user_id=user.id)
    db_session.commit()

    assert record.document_type == DocumentType.SKILLS_GAP_ANALYSIS.value
    assert "Gaps" in record.content


def test_document_service_export_recovers_job_title_from_relationship(
    db_session: Session, tmp_path: Path
) -> None:
    user = User(email="candidate5@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    employer = Employer(name="Example NHS Foundation Trust")
    job = Job(
        employer=employer,
        title="Healthcare Assistant",
        source_site="nhs_jobs",
        external_id="REF-1",
        url="https://example.com/job/1",
    )
    db_session.add_all([user, employer, job])
    db_session.flush()

    provider = FakeLLMProvider("Some cover letter content.")
    service = DocumentService(db_session, provider, export_manager=ExportManager(export_root=tmp_path))

    record = service.generate_cover_letter(_sample_profile(), _sample_job(), user_id=user.id, job_id=job.id)
    db_session.commit()

    paths = service.export(record.id)
    assert "Healthcare_Assistant" in paths["markdown"].name


def test_document_service_export_raises_for_unknown_document(db_session: Session) -> None:
    provider = FakeLLMProvider()
    service = DocumentService(db_session, provider)
    with pytest.raises(ValueError):
        service.approve(uuid.uuid4())
