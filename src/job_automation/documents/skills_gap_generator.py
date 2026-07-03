"""
Generates a missing-skills / gap analysis for a candidate against a
specific job's requirements.

See `supporting_statement_generator.py`'s module docstring — same
constructor-injected `LLMProvider` pattern, no rule-based fallback. This is
deliberately a separate, more detailed deliverable from `MatchResult
.missing_requirements` (the terse list already produced by rule-based/LLM
job matching) — this generator produces prose explaining *why* something is
a gap and *how* to close it, not just a bare list.
"""

from __future__ import annotations

from job_automation.ai.cache import AIResponseCache, complete_with_cache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.documents.document_models import DocumentType, GeneratedDocument
from job_automation.documents.prompt_builder import (
    build_skills_gap_system_prompt,
    build_skills_gap_user_prompt,
)
from job_automation.profile.candidate_profile import CandidateProfile


class SkillsGapGenerator:
    def __init__(self, llm_provider: LLMProvider, *, cache: AIResponseCache | None = None) -> None:
        self._llm_provider = llm_provider
        self._cache = cache

    def generate(
        self, profile: CandidateProfile, job: JobSnapshot, match_result: MatchResult | None = None
    ) -> GeneratedDocument:
        system_prompt = build_skills_gap_system_prompt()
        user_prompt = build_skills_gap_user_prompt(profile, job, match_result)
        content = complete_with_cache(
            self._llm_provider,
            self._cache,
            kind=DocumentType.SKILLS_GAP_ANALYSIS.value,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1200,
        )
        return GeneratedDocument(
            document_type=DocumentType.SKILLS_GAP_ANALYSIS,
            content=content.strip(),
            job_title=job.title,
            employer=job.employer,
        )
