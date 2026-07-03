"""
Generic retry executor with exponential backoff, used by BrowserManager
(launch retries) and PageManager (navigation retries).

Kept as a standalone class (composition, not a decorator baked into every
method) so any component can reuse the same backoff/logging policy by taking
a `RetryManager` in its constructor and calling `.execute(...)`.
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from job_automation.core.browser_exceptions import RetryExhaustedError, TransientError
from job_automation.utils.logger import logger

T = TypeVar("T")

_DEFAULT_RETRY_ON: tuple[type[Exception], ...] = (TransientError,)


class RetryManager:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
    ) -> None:
        self._max_retries = max_retries
        self._base_delay = base_delay_seconds
        self._max_delay = max_delay_seconds

    def execute(
        self,
        func: Callable[[], T],
        *,
        operation_name: str,
        retry_on: tuple[type, ...] = _DEFAULT_RETRY_ON,
    ) -> T:
        """Call `func()`, retrying on `retry_on` exceptions with exponential
        backoff + jitter. Raises `RetryExhaustedError` (chained to the last
        underlying exception) if every attempt fails. Any exception not
        matching `retry_on` propagates immediately, unretried.

        Matches via `isinstance(exc, retry_on)` inside a broad `except
        Exception`, rather than `except retry_on`, deliberately: `TransientError`
        (the default marker) is a plain mixin, not itself a `BaseException`
        subclass — `except (TransientError,):` raises `TypeError: catching
        classes that do not inherit from BaseException` the moment a real
        exception needs to be matched against it, since Python requires every
        member of an `except` tuple to itself derive from `BaseException`.
        `isinstance()` has no such restriction, so this preserves
        `TransientError`'s original design as a marker mixin rather than
        forcing it to also become an exception type."""
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return func()
            except Exception as exc:
                if not isinstance(exc, retry_on):
                    raise
                last_exc = exc
                if attempt == self._max_retries:
                    break
                delay = self._compute_delay(attempt)
                logger.warning(
                    "Retry {}/{} for '{}' after {}: {} — sleeping {:.1f}s",
                    attempt,
                    self._max_retries,
                    operation_name,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                time.sleep(delay)

        logger.error("All {} attempts failed for '{}'", self._max_retries, operation_name)
        raise RetryExhaustedError(
            f"Operation {operation_name!r} failed after {self._max_retries} attempts"
        ) from last_exc

    def _compute_delay(self, attempt: int) -> float:
        exponential = self._base_delay * (2 ** (attempt - 1))
        capped = min(exponential, self._max_delay)
        jitter = random.uniform(0, capped * 0.25)
        return capped + jitter
