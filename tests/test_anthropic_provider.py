"""
Tests for `AnthropicProvider` — the real `LLMProvider` implementation added
for the Anthropic AI Integration milestone.

**No real API calls are ever made here.** `anthropic.Anthropic` is patched
at the class level so `AnthropicProvider` constructs a `MagicMock` instead
of a real SDK client; every test controls `.messages.create(...)`'s return
value or raised exception directly. This is the one place in the suite that
exercises `AnthropicProvider` itself — everywhere else (`test_web_dashboard
.py`, `test_document_generation.py`, `test_ai_matching.py`) substitutes a
`FakeLLMProvider` at the `LLMProvider` abstraction boundary instead, so
those tests never touch this class or the `anthropic` package at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from job_automation.ai.anthropic_provider import AnthropicProvider
from job_automation.ai.llm_provider import LLMProviderError
from job_automation.core.browser_exceptions import RetryExhaustedError
from job_automation.core.retry_manager import RetryManager


def _fast_retry_manager(max_retries: int = 3) -> RetryManager:
    # Zero-ish delays so retry tests don't actually sleep.
    return RetryManager(max_retries=max_retries, base_delay_seconds=0.001, max_delay_seconds=0.001)


def _text_response(text: str = "Generated content.", *, input_tokens: int = 100, output_tokens: int = 50):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _dummy_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _rate_limit_error() -> anthropic.RateLimitError:
    request = _dummy_request()
    response = httpx.Response(status_code=429, request=request)
    return anthropic.RateLimitError("rate limited", response=response, body=None)


def _bad_request_error() -> anthropic.BadRequestError:
    request = _dummy_request()
    response = httpx.Response(status_code=400, request=request)
    return anthropic.BadRequestError("bad request", response=response, body=None)


class TestConstruction:
    def test_missing_api_key_raises_clear_error_without_touching_the_sdk(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            with pytest.raises(LLMProviderError, match="requires an API key"):
                AnthropicProvider(None)
            mock_client_cls.assert_not_called()

    def test_empty_string_api_key_also_raises(self) -> None:
        with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY"):
            AnthropicProvider("")

    def test_constructs_sdk_client_with_configured_key_and_timeout(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            AnthropicProvider("sk-test-key", model="claude-sonnet-4-6", timeout_seconds=42.0)
            mock_client_cls.assert_called_once_with(api_key="sk-test-key", timeout=42.0)


class TestComplete:
    def test_returns_text_from_response(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _text_response("Hello, candidate.")
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider("sk-test-key", retry_manager=_fast_retry_manager())
            result = provider.complete(system_prompt="system", user_prompt="user", max_tokens=500)

            assert result == "Hello, candidate."
            mock_client.messages.create.assert_called_once_with(
                model=provider._model,
                max_tokens=500,
                system="system",
                messages=[{"role": "user", "content": "user"}],
            )

    def test_concatenates_multiple_text_blocks(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="Part one. "),
                    SimpleNamespace(type="text", text="Part two."),
                ],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider("sk-test-key", retry_manager=_fast_retry_manager())
            result = provider.complete(system_prompt="s", user_prompt="u")

            assert result == "Part one. Part two."

    def test_raises_when_response_has_no_text_content(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = SimpleNamespace(content=[], usage=None)
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider("sk-test-key", retry_manager=_fast_retry_manager())
            with pytest.raises(LLMProviderError, match="no text content"):
                provider.complete(system_prompt="s", user_prompt="u")

    def test_does_not_raise_when_usage_is_missing(self) -> None:
        """Cost/token logging must be best-effort — a response missing
        `.usage` should still return its text, not blow up the whole call
        over a logging concern."""
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")], usage=None
            )
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider("sk-test-key", retry_manager=_fast_retry_manager())
            assert provider.complete(system_prompt="s", user_prompt="u") == "ok"


class TestRetryAndErrorHandling:
    def test_transient_rate_limit_error_is_retried_then_succeeds(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = [_rate_limit_error(), _text_response("recovered")]
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider("sk-test-key", retry_manager=_fast_retry_manager(max_retries=2))
            result = provider.complete(system_prompt="s", user_prompt="u")

            assert result == "recovered"
            assert mock_client.messages.create.call_count == 2

    def test_transient_error_exhausting_retries_raises_retry_exhausted(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = _rate_limit_error()
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider("sk-test-key", retry_manager=_fast_retry_manager(max_retries=2))
            with pytest.raises(RetryExhaustedError):
                provider.complete(system_prompt="s", user_prompt="u")
            assert mock_client.messages.create.call_count == 2

    def test_non_transient_api_error_is_not_retried(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = _bad_request_error()
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider("sk-test-key", retry_manager=_fast_retry_manager(max_retries=3))
            with pytest.raises(LLMProviderError, match="Anthropic API error"):
                provider.complete(system_prompt="s", user_prompt="u")
            # Not retried: a non-transient error propagates on the first attempt.
            assert mock_client.messages.create.call_count == 1


class TestCostLogging:
    def test_logs_estimated_cost_from_configured_rates(self) -> None:
        with patch("job_automation.ai.anthropic_provider.anthropic.Anthropic") as mock_client_cls, patch(
            "job_automation.ai.anthropic_provider.logger"
        ) as mock_logger:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _text_response(input_tokens=1_000_000, output_tokens=1_000_000)
            mock_client_cls.return_value = mock_client

            provider = AnthropicProvider(
                "sk-test-key",
                retry_manager=_fast_retry_manager(),
                input_cost_per_million_usd=3.0,
                output_cost_per_million_usd=15.0,
            )
            provider.complete(system_prompt="s", user_prompt="u")

            usage_calls = [call for call in mock_logger.info.call_args_list if "Anthropic usage" in call.args[0]]
            assert len(usage_calls) == 1
            logged_cost = usage_calls[0].args[-1]
            assert logged_cost == pytest.approx(18.0)  # 1M in @ $3/M + 1M out @ $15/M
