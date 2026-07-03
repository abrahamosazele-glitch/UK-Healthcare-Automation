"""
Caches LLM analyses to avoid repeated calls for identical (candidate, job)
pairs.

Caches `LLMAnalysis` specifically, not the whole `MatchResult` — rule-based
scores are free and deterministic, so there's no reason to cache them;
caching only the expensive/slow/costly part (the LLM call) is both simpler
and more correct (a rule-engine change takes effect immediately on the next
run, without needing a cache bust).

Backed by JSON files on disk (under `data/cache/ai_matches/` by default)
rather than an in-memory dict, specifically because "avoid repeated LLM
calls" implies persistence *across process runs*, not just within one — an
in-memory cache would be empty again on every script invocation. The key
includes `prompt_builder.PROMPT_VERSION`, so changing the prompt
automatically invalidates old cached analyses instead of silently reusing
answers to a question that's no longer being asked.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_models import CandidateProfile, JobSnapshot, LLMAnalysis
from job_automation.ai.prompt_builder import PROMPT_VERSION
from job_automation.utils.logger import logger

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache" / "ai_matches"


class MatchCache:
    def __init__(self, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_key(candidate: CandidateProfile, job: JobSnapshot) -> str:
        fingerprint = f"{candidate.fingerprint()}|{job.content_fingerprint()}|{PROMPT_VERSION}"
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    def get(self, key: str) -> LLMAnalysis | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return LLMAnalysis.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to read cached LLM analysis {}: {}", key, exc)
            return None

    def set(self, key: str, analysis: LLMAnalysis) -> None:
        path = self._path_for(key)
        try:
            path.write_text(json.dumps(analysis.to_dict(), indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write cached LLM analysis {}: {}", key, exc)

    def clear(self) -> None:
        for path in self._dir.glob("*.json"):
            path.unlink()

    def _path_for(self, key: str) -> Path:
        return self._dir / f"{key}.json"


#: Separate from `ai_matches/` — `AIResponseCache` stores plain generated
#: text (documents, interview prep, skills-gap analyses), `MatchCache`
#: stores structured `LLMAnalysis` objects. Different shapes, different
#: subdirectories, same on-disk-JSON-per-key design.
DEFAULT_RESPONSE_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache" / "ai_responses"


class AIResponseCache:
    """Caches raw LLM completions to avoid repeated (and billed) Anthropic
    calls for an unchanged (kind, system prompt, user prompt) triple —
    used by the `documents` package's five generators (supporting
    statement, cover letter, application answer, interview prep, skills
    gap analysis), added for the Anthropic AI Integration milestone.

    Keyed on the exact rendered prompts, not on separately-computed
    candidate/job fingerprints like `MatchCache` — the user prompt already
    fully encodes the profile, job, match context, and any extra input
    (a question, an interview type), so hashing it directly is simpler and
    exactly as correct as hashing its ingredients separately would be.
    """

    def __init__(self, cache_dir: Path = DEFAULT_RESPONSE_CACHE_DIR) -> None:
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_key(kind: str, system_prompt: str, user_prompt: str) -> str:
        fingerprint = f"{kind}|{system_prompt}|{user_prompt}"
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read cached AI response {}: {}", key, exc)
            return None

    def set(self, key: str, content: str) -> None:
        path = self._path_for(key)
        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write cached AI response {}: {}", key, exc)

    def clear(self) -> None:
        for path in self._dir.glob("*.txt"):
            path.unlink()

    def _path_for(self, key: str) -> Path:
        return self._dir / f"{key}.txt"


def complete_with_cache(
    llm_provider: "LLMProvider",
    cache: AIResponseCache | None,
    *,
    kind: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> str:
    """Shared cache-check/call/cache-set wrapper around
    `LLMProvider.complete()`, used identically by all five `documents`
    package generators rather than each repeating the same six lines.
    `cache=None` (the default everywhere) makes this behave exactly like
    calling `llm_provider.complete()` directly — caching is strictly
    additive, never a behavior change for existing callers that don't
    pass one."""
    key = None
    if cache is not None:
        key = AIResponseCache.compute_key(kind, system_prompt, user_prompt)
        cached = cache.get(key)
        if cached is not None:
            logger.info("Cache hit for {} generation", kind)
            return cached

    content = llm_provider.complete(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens)

    if cache is not None and key is not None:
        cache.set(key, content)
    return content
