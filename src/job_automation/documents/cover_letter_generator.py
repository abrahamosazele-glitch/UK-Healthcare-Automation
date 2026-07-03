"""
Generates a UK healthcare-sector cover letter.

See `supporting_statement_generator.py`'s module docstring — same
constructor-injected `LLMProvider` pattern, no rule-based fallback.
"""

from __future__ import annotations

from job_automation.ai.cache import AIResponseCache, complete_with_cache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.documents.document_models import DocumentType, GeneratedDocument
from job_automation.documents.prompt_builder import (
    build_cover_letter_system_prompt,
    build_cover_letter_user_prompt,
)
from job_automation.profile.candidate_profile import CandidateProfile


class CoverLetterGenerator:
    def __init__(self, llm_provider: LLMProvider, *, cache: AIResponseCache | None = None) -> None:
        self._llm_provider = llm_provider
        self._cache = cache

    def generate(
        self, profile: CandidateProfile, job: JobSnapshot, match_result: MatchResult | None = None
    ) -> GeneratedDocument:
        system_prompt = build_cover_letter_system_prompt()
        user_prompt = build_cover_letter_user_prompt(profile, job, match_result)
        content = complete_with_cache(
            self._llm_provider,
            self._cache,
            kind=DocumentType.COVER_LETTER.value,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=800,
        )
        return GeneratedDocument(
            document_type=DocumentType.COVER_LETTER,
            content=content.strip(),
            job_title=job.title,
            employer=job.employer,
        )
