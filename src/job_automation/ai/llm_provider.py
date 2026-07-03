"""
Provider abstraction for LLM-backed semantic analysis.

`LLMProvider` is deliberately minimal — one method, raw text in and out.
`MatchingEngine` never sees a provider-specific SDK type or response shape;
it only ever calls `complete(system_prompt=..., user_prompt=...)` and hands
the returned string to `ai.prompt_builder.parse_response()`. This is what
lets a second provider (OpenAI, etc.) be added later — implement this one
method against the new SDK, inject it wherever `AnthropicProvider` is
injected today, and nothing in `MatchingEngine`/`RuleEngine`/
`ScoreCalculator` changes.

Exceptions reuse `job_automation.core.browser_exceptions.TransientError` as
the retryability marker rather than inventing a second one — a rate limit or
timeout from an LLM API is conceptually the same kind of "worth retrying"
signal as a flaky page load, and `job_automation.core.retry_manager
.RetryManager` (already built, not reimplemented here) only needs the one
marker to know what to retry regardless of where the failure came from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from job_automation.core.browser_exceptions import TransientError


class LLMProviderError(Exception):
    """Base class for every exception raised by an LLMProvider."""


class LLMTransientError(LLMProviderError, TransientError):
    """A retryable provider failure: rate limit, timeout, connection error."""


class LLMResponseError(LLMProviderError):
    """The provider responded, but its content couldn't be used (e.g. not
    valid JSON where structured output was requested). Not transient —
    retrying the identical prompt will very likely fail the same way."""


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        """Return the raw text completion for the given prompts. Implementations
        must raise `LLMTransientError` for retryable failures (rate limits,
        timeouts) so `RetryManager` can act on them, and let any other
        provider-specific failure propagate as a plain `LLMProviderError`."""
