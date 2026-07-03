"""
Handles the file-system side of a Playwright download: where it lands, what
it's named if that name is already taken, and confirming it actually arrived
intact. Playwright's own `Download` object is produced by the caller (via
`page.expect_download()`); this class only takes over once that object
exists — it has no navigation/click logic of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from job_automation.core.browser_config import BrowserConfig
from job_automation.core.browser_exceptions import DownloadError
from job_automation.utils.logger import logger

if TYPE_CHECKING:
    from playwright.sync_api import Download


class DownloadManager:
    def __init__(self, config: BrowserConfig) -> None:
        self._dir = config.download_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def save_download(self, download: "Download", *, filename: str | None = None) -> Path:
        """Save `download` into the configured download directory, avoiding
        overwriting an existing file of the same name, and verify it landed.
        Raises DownloadError if saving or verification fails."""
        name = filename or download.suggested_filename
        target = self._unique_path(name)

        try:
            download.save_as(target)
        except Exception as exc:
            raise DownloadError(f"Failed to save download {name!r}: {exc}") from exc

        self._verify(target)
        logger.info("Download saved: {}", target)
        return target

    def _unique_path(self, filename: str) -> Path:
        """`report.pdf` -> `report (1).pdf` -> `report (2).pdf` ... if taken."""
        candidate = self._dir / filename
        if not candidate.exists():
            return candidate

        stem, suffix = candidate.stem, candidate.suffix
        counter = 1
        while True:
            candidate = self._dir / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _verify(self, path: Path) -> None:
        if not path.exists():
            raise DownloadError(f"Download verification failed: {path} does not exist")
        if path.stat().st_size == 0:
            raise DownloadError(f"Download verification failed: {path} is empty")
