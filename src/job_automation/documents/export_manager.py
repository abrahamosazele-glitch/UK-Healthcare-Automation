"""
Exports a `GeneratedDocument` to disk as Markdown and/or plain text — the
last step before a human actually reads a draft outside the database.

Follows the same pattern as `core.download_manager.DownloadManager`:
directory-per-category, collision-safe naming, real files written to
`data/documents/<document_type>/`.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from job_automation.documents.document_models import GeneratedDocument

DEFAULT_EXPORT_ROOT = Path(__file__).resolve().parents[3] / "data" / "documents"

_UNSAFE_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


class ExportManager:
    def __init__(self, export_root: Path = DEFAULT_EXPORT_ROOT) -> None:
        self._export_root = export_root

    def export_markdown(self, document: GeneratedDocument, *, filename_hint: str | None = None) -> Path:
        return self._export(document, extension="md", filename_hint=filename_hint, formatter=self._as_markdown)

    def export_txt(self, document: GeneratedDocument, *, filename_hint: str | None = None) -> Path:
        return self._export(document, extension="txt", filename_hint=filename_hint, formatter=self._as_txt)

    def export_all(
        self, document: GeneratedDocument, *, formats: tuple[str, ...] = ("markdown", "txt"), filename_hint: str | None = None
    ) -> dict[str, Path]:
        exporters = {"markdown": self.export_markdown, "txt": self.export_txt}
        return {fmt: exporters[fmt](document, filename_hint=filename_hint) for fmt in formats}

    def _export(self, document, *, extension: str, filename_hint: str | None, formatter) -> Path:
        directory = self._export_root / document.document_type.value
        directory.mkdir(parents=True, exist_ok=True)
        path = self._unique_path(directory, self._filename(document, filename_hint, extension))
        path.write_text(formatter(document), encoding="utf-8")
        return path

    def _filename(self, document: GeneratedDocument, filename_hint: str | None, extension: str) -> str:
        hint = filename_hint or document.job_title or document.document_type.value
        slug = _UNSAFE_CHARS.sub("_", hint).strip("_") or "document"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{document.document_type.value}_{slug}_{timestamp}.{extension}"

    def _unique_path(self, directory: Path, filename: str) -> Path:
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        counter = 1
        while True:
            candidate = directory / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _as_markdown(self, document: GeneratedDocument) -> str:
        title = document.document_type.value.replace("_", " ").title()
        lines = [f"# {title}"]
        if document.job_title:
            lines.append(f"**Role:** {document.job_title}" + (f" at {document.employer}" if document.employer else ""))
        if document.question:
            lines.append(f"**Question:** {document.question}")
        lines.append(f"**Status:** {document.status.value}")
        lines.append("")
        lines.append(document.content)
        if document.validation_issues:
            lines.append("")
            lines.append("## Review notes")
            lines.extend(f"- ({issue.severity}) {issue.message}" for issue in document.validation_issues)
        return "\n".join(lines) + "\n"

    def _as_txt(self, document: GeneratedDocument) -> str:
        lines = []
        if document.job_title:
            lines.append(f"Role: {document.job_title}" + (f" at {document.employer}" if document.employer else ""))
        if document.question:
            lines.append(f"Question: {document.question}")
        lines.append(f"Status: {document.status.value}")
        lines.append("")
        lines.append(document.content)
        return "\n".join(lines) + "\n"
