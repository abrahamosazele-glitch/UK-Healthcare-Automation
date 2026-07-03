"""
The matching pipeline: rule-based scoring + semantic LLM analysis + weighted
score calculation, combined into one `evaluate()` call.

Every dependency (`RuleEngine`, `ScoreCalculator`, `LLMProvider`,
`MatchCache`) is constructor-injected. `llm_provider` and `cache` are
optional — `MatchingEngine` works correctly (rule-only) with neither
configured, which is what makes "the architecture should allow additional
providers later without changing the matching engine" true: swapping
`AnthropicProvider` for a future `OpenAIProvider` only changes what's passed
into this constructor, never this class's code.
"""

from __future__ import annotations

from job_automation.ai.cache import MatchCache
from job_automation.ai.llm_provider import LLMProvider, LLMProviderError
from job_automation.ai.matching_models import CandidateProfile, JobSnapshot, LLMAnalysis, MatchResult
from job_automation.ai.prompt_builder import build_system_prompt, build_user_prompt, parse_response
from job_automation.ai.rule_engine import RuleEngine
from job_automation.ai.score_calculator import ScoreCalculator
from job_automation.utils.logger import logger


class MatchingEngine:
    def __init__(
        self,
        *,
        rule_engine: RuleEngine | None = None,
        score_calculator: ScoreCalculator | None = None,
        llm_provider: LLMProvider | None = None,
        cache: MatchCache | None = None,
    ) -> None:
        self._rule_engine = rule_engine or RuleEngine()
        self._score_calculator = score_calculator or ScoreCalculator()
        self._llm_provider = llm_provider
        self._cache = cache

    def evaluate(self, candidate: CandidateProfile, job: JobSnapshot) -> MatchResult:
        rule_scores = self._rule_engine.score(candidate, job)
        matched_keywords = self._rule_engine.matched_keywords(candidate, job)

        llm_analysis = self._get_llm_analysis(candidate, job) if self._llm_provider is not None else None

        result = self._score_calculator.calculate(
            rule_scores, llm_analysis, matched_keywords=matched_keywords
        )
        logger.info(
            "Evaluated {!r}: overall={} confidence={} (llm={})",
            job.title,
            result.overall_score,
            result.confidence_score,
            result.used_llm,
        )
        return result

    def _get_llm_analysis(self, candidate: CandidateProfile, job: JobSnapshot) -> LLMAnalysis | None:
        cache_key = None
        if self._cache is not None:
            cache_key = MatchCache.compute_key(candidate, job)
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.info("Cache hit for LLM analysis of {!r}", job.title)
                return cached

        assert self._llm_provider is not None  # only called when a provider is configured
        try:
            raw = self._llm_provider.complete(
                system_prompt=build_system_prompt(),
                user_prompt=build_user_prompt(candidate, job),
            )
            analysis = parse_response(raw)
        except LLMProviderError as exc:
            logger.error("LLM analysis failed for {!r}, falling back to rule-only scoring: {}", job.title, exc)
            return None

        if self._cache is not None and cache_key is not None:
            self._cache.set(cache_key, analysis)
        return analysis
