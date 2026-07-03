"""
Loguru logging setup shared by the whole app.

Configures two sinks on the loguru default logger: a console sink at
`settings.log_level`, and a rotating file sink under `settings.log_dir`
(always DEBUG, so a run can be diagnosed after the fact even if the console
was set to INFO). `setup_logging()` is idempotent — safe to call more than
once (e.g. once from a script's entry point, once from a test fixture)
without duplicating sinks.

**Production (`settings.environment == "production"`) differs in two ways:**
1. The console sink writes structured JSON (`serialize=True`) to stdout
   instead of colorized plain text to stderr — Railway/Render/Docker all
   capture stdout and hand it to a log viewer/search index that wants one
   JSON object per line, not ANSI color codes meant for a human terminal.
2. Both sinks disable `diagnose` — loguru's default (`diagnose=True`) prints
   local variable values inline in an exception traceback, which is a real
   secret-leak risk once real traffic (real API keys passed as function
   arguments, real user data) reaches this app. `backtrace` (which sink to
   walk beyond the try/except frame) stays on; only the variable dump is
   the production-unsafe part.
"""

from __future__ import annotations

import sys

from loguru import logger

from job_automation.config.settings import settings

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return

    logger.remove()  # drop loguru's default stderr sink so we control format/level

    is_production = settings.environment == "production"

    if is_production:
        logger.add(sys.stdout, level=settings.log_level, serialize=True, diagnose=False)
    else:
        logger.add(
            sys.stderr,
            level=settings.log_level,
            colorize=True,
            format=(
                "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
            ),
        )

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        settings.log_dir / "app.log",
        level="DEBUG",
        rotation="1 day",
        retention="14 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        diagnose=not is_production,
    )

    _configured = True
