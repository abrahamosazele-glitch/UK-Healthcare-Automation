"""
Tests for `config.settings.Settings` — added for the Production Readiness
milestone's fail-fast production config validation. Every test constructs
`Settings` directly with `_env_file=None` so it's never affected by this
machine's real `.env` (which may itself set ENVIRONMENT/real secrets).
"""

from __future__ import annotations

import pytest

from job_automation.config.settings import Settings


def test_development_is_unaffected_by_insecure_defaults() -> None:
    """The default environment never blocks startup, regardless of what
    else is left at its default — this is what keeps a fresh `git clone` +
    `copy .env.example .env` runnable out of the box."""
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.session_secret_key == "dev-insecure-secret-key-change-me"
    assert settings.session_cookie_secure is False


def test_production_raises_when_secret_key_is_still_the_dev_default() -> None:
    with pytest.raises(Exception, match="SESSION_SECRET_KEY is still the insecure development default"):
        Settings(environment="production", session_cookie_secure=True, _env_file=None)


def test_production_raises_when_cookie_is_not_secure() -> None:
    with pytest.raises(Exception, match="SESSION_COOKIE_SECURE must be true"):
        Settings(environment="production", session_secret_key="a-real-random-secret", _env_file=None)


def test_production_raises_with_both_errors_listed_together() -> None:
    with pytest.raises(Exception) as exc_info:
        Settings(environment="production", _env_file=None)
    message = str(exc_info.value)
    assert "SESSION_SECRET_KEY" in message
    assert "SESSION_COOKIE_SECURE" in message


def test_production_succeeds_with_a_secure_configuration() -> None:
    settings = Settings(
        environment="production",
        session_secret_key="a-real-random-secret",
        session_cookie_secure=True,
        _env_file=None,
    )
    assert settings.environment == "production"


def test_invalid_environment_value_is_rejected() -> None:
    with pytest.raises(Exception):
        Settings(environment="staging", _env_file=None)  # type: ignore[arg-type]
