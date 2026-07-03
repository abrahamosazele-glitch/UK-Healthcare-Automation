"""
Anthropic Claude implementation of LLMProvider.

Retries are handled by `job_automation.core.retry_manager.RetryManager` —
the same class `BrowserManager`/`PageManager` already use for browser
retries — rather than a second bespoke retry loop just for LLM calls. The
Anthropic SDK's own transient exceptions (rate limits, timeouts, connection
errors) are translated to `LLMTransientError` at the point of contact, so
`RetryManager` doesn't need to import or know about the `anthropic` package.

**Timeout** is passed straight to the SDK client (`anthropic.Anthropic(...,
timeout=...)`), not reimplemented here — the SDK already raises
`APITimeoutError` when it fires, which is caught and retried exactly like
any other transient failure below.

**Cost/token logging**: every real completion logs the model, input/output
token counts, and an estimated USD cost (from `settings.anthropic_*_cost_
per_million_usd` — approximate, not billing-accurate; see that setting's
docstring) via `logger.info`. This is deliberately a log line, not a new
database table: it's an operational/debugging concern (loguru already
rotates `logs/app.log`), not analytics data a page needs to query, so it
doesn't warrant new persistence.
"""

from __future__ import annotations

import anthropic

from job_automation.ai.llm_provider import LLMProvider, LLMProviderError, LLMTransientError
from job_automation.core.retry_manager import RetryManager
from job_automation.utils.logger import logger

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_TIMEOUT_SECONDS = 60.0


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retry_manager: RetryManager | None = None,
        input_cost_per_million_usd: float = 0.0,
        output_cost_per_million_usd: float = 0.0,
    ) -> None:
        if not api_key:
            raise LLMProviderError(
                "AnthropicProvider requires an API key (settings.anthropic_api_key is not set). "
                "Set ANTHROPIC_API_KEY in your .env file — see .env.example."
            )
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self._retry_manager = retry_manager or RetryManager(max_retries=3)
        self._input_cost_per_million_usd = input_cost_per_million_usd
        self._output_cost_per_million_usd = output_cost_per_million_usd

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        def _call():
            try:
                return self._client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
            except (anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
                raise LLMTransientError(f"Transient Anthropic API failure: {exc}") from exc
            except anthropic.APIError as exc:
                raise LLMProviderError(f"Anthropic API error: {exc}") from exc

        response = self._retry_manager.execute(_call, operation_name=f"anthropic:{self._model}")

        text_blocks = [block.text for block in response.content if block.type == "text"]
        if not text_blocks:
            raise LLMProviderError("Anthropic response contained no text content")
        result = "".join(text_blocks)

        self._log_usage(response)
        logger.debug("Anthropic completion received ({} chars)", len(result))
        return result

    def _log_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        estimated_cost_usd = (
            input_tokens * self._input_cost_per_million_usd / 1_000_000
            + output_tokens * self._output_cost_per_million_usd / 1_000_000
        )
        logger.info(
            "Anthropic usage: model={} input_tokens={} output_tokens={} estimated_cost_usd={:.5f}",
            self._model,
            input_tokens,
            output_tokens,
            estimated_cost_usd,
        )
