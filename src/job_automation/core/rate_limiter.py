"""
Randomized delay between actions, to avoid hammering a site with
perfectly-uniform request timing (a common bot signal).

The min/max range itself provides the "jitter" / human-like variation — there
is no separate fixed-delay-plus-jitter split, since a fixed delay with jitter
added on top is mathematically equivalent to sampling uniformly over a wider
range, and one range is simpler to reason about and configure than two knobs.
"""

from __future__ import annotations

import random
import time

from job_automation.utils.logger import logger


class RateLimiter:
    def __init__(self, min_delay_seconds: float = 1.0, max_delay_seconds: float = 3.0) -> None:
        if min_delay_seconds < 0 or max_delay_seconds < min_delay_seconds:
            raise ValueError("Require 0 <= min_delay_seconds <= max_delay_seconds")
        self._min_delay = min_delay_seconds
        self._max_delay = max_delay_seconds

    def wait(self) -> float:
        """Sleep for a random duration in [min_delay, max_delay] and return it."""
        delay = random.uniform(self._min_delay, self._max_delay)
        logger.debug("Rate limiting: sleeping {:.2f}s", delay)
        time.sleep(delay)
        return delay
