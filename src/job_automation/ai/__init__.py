"""
AI-powered job matching engine. Evaluates jobs already stored in the
database against a candidate profile — no scraping, no automatic
application submission lives here (see docs/AI_MATCHING.md).

Note: `ai_client.py`, `cv_generator.py`, `cover_letter_generator.py`, and
`prompts.py` are a separate, still-unbuilt feature (CV/cover-letter
generation) scaffolded in an earlier milestone — not re-exported here to
keep this package's public surface focused on matching.
"""

from job_automation.ai.anthropic_provider import AnthropicProvider
from job_automation.ai.cache import MatchCache
from job_automation.ai.llm_provider import LLMProvider, LLMProviderError, LLMResponseError, LLMTransientError
from job_automation.ai.matching_engine import MatchingEngine
from job_automation.ai.matching_models import (
    MATCH_CATEGORIES,
    CandidateProfile,
    EducationEntry,
    ExperienceEntry,
    JobSnapshot,
    LLMAnalysis,
    MatchResult,
)
from job_automation.ai.matching_service import MatchingService
from job_automation.ai.profile_builder import build_candidate_profile
from job_automation.ai.rule_engine import RuleEngine
from job_automation.ai.score_calculator import ScoreCalculator

__all__ = [
    "AnthropicProvider",
    "MatchCache",
    "LLMProvider",
    "LLMProviderError",
    "LLMResponseError",
    "LLMTransientError",
    "MatchingEngine",
    "MATCH_CATEGORIES",
    "CandidateProfile",
    "EducationEntry",
    "ExperienceEntry",
    "JobSnapshot",
    "LLMAnalysis",
    "MatchResult",
    "MatchingService",
    "build_candidate_profile",
    "RuleEngine",
    "ScoreCalculator",
]
