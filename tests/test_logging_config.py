"""
Tests for `config.logging_config.setup_logging` — added for the Production
Readiness milestone's logging hardening: structured JSON on stdout (not
colorized stderr) in production, and `diagnose=False` in production so an
exception traceback never dumps local variable values (a real secret-leak
risk once real API keys/user data reach this app).

Each test resets loguru's global state (`logger.remove()` +
`logging_config._configured = False`) before and after, since
`setup_logging()` is deliberately idempotent/module-global and importing
`job_automation.web.app` anywhere in the suite already calls it once for
real.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from loguru import logger

from job_automation.config import logging_config
from job_automation.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_logging_state() -> Iterator[None]:
    logger.remove()
    logging_config._configured = False
    try:
        yield
    finally:
        logger.remove()
        logging_config._configured = False


def test_development_logs_plain_colorized_text_to_stderr(monkeypatch, capsys) -> None:
    monkeypatch.setattr(settings, "environment", "development")
    logging_config.setup_logging()
    logger.info("hello from development")

    captured = capsys.readouterr()
    assert "hello from development" in captured.err
    with pytest.raises(json.JSONDecodeError):
        json.loads(captured.err.strip().splitlines()[-1])


def test_production_logs_structured_json_to_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    logging_config.setup_logging()
    logger.info("hello from production")

    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)  # must not raise: production sink is JSON, one object per line
    assert payload["record"]["message"] == "hello from production"


def test_production_does_not_leak_local_variables_on_exception(monkeypatch, capsys) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    logging_config.setup_logging()

    def _boom() -> None:
        api_key_that_must_not_leak = "sk-super-secret-value"  # noqa: F841
        raise ValueError("boom")

    try:
        _boom()
    except ValueError:
        logger.exception("something broke")

    captured = capsys.readouterr()
    assert "sk-super-secret-value" not in captured.out


def test_setup_logging_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "development")
    logging_config.setup_logging()
    handlers_after_first_call = dict(logger._core.handlers)
    logging_config.setup_logging()
    assert dict(logger._core.handlers).keys() == handlers_after_first_call.keys()
