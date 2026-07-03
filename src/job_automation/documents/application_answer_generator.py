"""
Generates a short answer to a specific application question.

Enforces `max_words` as a hard post-processing truncation, not just a
prompt instruction — LLMs don't reliably obey word-count instructions, and a
"short answer" field on a real application form often has a hard character/
word limit, so silently exceeding it would produce an unusable draft.
"""

from __future__ import annotations

from job_automation.ai.cache import AIResponseCache, complete_with_cache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.documents.document_models import DocumentType, GeneratedDocument
from job_automation.documents.prompt_builder import (
    build_application_answer_system_prompt,
    build_application_answer_user_prompt,
)
from job_automation.profile.candidate_profile import CandidateProfile


class ApplicationAnswerGenerator:
    def __init__(self, llm_provider: LLMProvider, *, cache: AIResponseCache | None = None) -> None:
        self._llm_provider = llm_provider
        self._cache = cache

    def generate(
        self,
        profile: CandidateProfile,
        job: JobSnapshot,
        question: str,
        match_result: MatchResult | None = None,
        *,
        max_words: int = 150,
    ) -> GeneratedDocument:
        system_prompt = build_application_answer_system_prompt()
        user_prompt = build_application_answer_user_prompt(profile, job, question, match_result, max_words=max_words)
        content = complete_with_cache(
            self._llm_provider,
            self._cache,
            kind=DocumentType.APPLICATION_ANSWER.value,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=400,
        )
        return GeneratedDocument(
            document_type=DocumentType.APPLICATION_ANSWER,
            content=self._enforce_word_limit(content.strip(), max_words),
            question=question,
            job_title=job.title,
            employer=job.employer,
        )

    def _enforce_word_limit(self, content: str, max_words: int) -> str:
        words = content.split()
        if len(words) <= max_words:
            return content
        return " ".join(words[:max_words]).rstrip(",;:") + "..."
