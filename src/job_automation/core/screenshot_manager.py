"""
Captures screenshots on demand, and is meant to be called by other
components (PageManager, BrowserManager) whenever navigation fails, an
exception occurs, or an unexpected dialog appears — not just by end users.

Capture failures are swallowed and logged rather than raised: this is almost
always called from inside an `except` block, and a broken screenshot must
never hide the real error that triggered it.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from job_automation.core.browser_config import BrowserConfig
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Page

_UNSAFE_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


class ScreenshotManager:
    def __init__(self, config: BrowserConfig) -> None:
        self._dir = config.screenshot_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def capture(self, page: "Page", reason: str) -> Path | None:
        """Save a full-page screenshot named `<reason>_<timestamp>.png`.
        Returns the path, or None if the capture itself failed."""
        safe_reason = _UNSAFE_CHARS.sub("_", reason) or "screenshot"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self._dir / f"{safe_reason}_{timestamp}.png"

        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            logger.warning("Screenshot capture failed (reason={}): {}", reason, exc)
            return None

        logger.info("Screenshot captured: {} (reason={})", path, reason)
        return path
