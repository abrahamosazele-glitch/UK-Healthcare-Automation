"""
Convenience re-export of the configured Loguru logger.

Lets other modules do `from job_automation.utils.logger import logger` without
each one needing to know about config.logging_config's setup_logging() call.
Sinks are configured once by whichever entry point (a script, a test fixture)
calls `setup_logging()` first; modules that just log don't need to care.
"""

from loguru import logger

__all__ = ["logger"]
