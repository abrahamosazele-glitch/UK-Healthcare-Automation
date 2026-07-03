"""
Generates an NHS-style supporting statement.

Requires an `LLMProvider` (constructor-injected, reusing
`job_automation.ai.llm_provider.LLMProvider` — not a new abstraction).
Unlike `ai.matching_engine.MatchingEngine`, there is no rule-based fallback
here: scoring a job against a profile deterministically is possible without
an LLM, but drafting genuine prose is not — an `LLMProvider` is required,
not optional.
"""

from __future__ import annotations

from job_automation.ai.cache import AIResponseCache, complete_with_cache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.documents.document_models import DocumentType, GeneratedDocument
from job_automation.documents.prompt_builder import (
    build_supporting_statement_system_prompt,
    build_supporting_statement_user_prompt,
)
from job_automation.profile.candidate_profile import CandidateProfile


class SupportingStatementGenerator:
    def __init__(self, llm_provider: LLMProvider, *, cache: AIResponseCache | None = None) -> None:
        self._llm_provider = llm_provider
        self._cache = cache

    def generate(
        self, profile: CandidateProfile, job: JobSnapshot, match_result: MatchResult | None = None
    ) -> GeneratedDocument:
        system_prompt = build_supporting_statement_system_prompt()
        user_prompt = build_supporting_statement_user_prompt(profile, job, match_result)
        content = complete_with_cache(
            self._llm_provider,
            self._cache,
            kind=DocumentType.SUPPORTING_STATEMENT.value,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1500,
        )
        return GeneratedDocument(
            document_type=DocumentType.SUPPORTING_STATEMENT,
            content=content.strip(),
            job_title=job.title,
            employer=job.employer,
        )
