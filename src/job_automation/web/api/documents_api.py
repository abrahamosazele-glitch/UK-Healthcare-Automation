"""
JSON API for generated documents: list, approve, reject, export. Every
mutation goes through `DocumentService` — this file never touches
`DocumentRepository` or `GeneratedDocumentRecord` directly for writes,
only for the read/list endpoint's serialization.

Approve/reject are POST endpoints, not GET, specifically because they
mutate state (a document's review status) — matching the "never
auto-approve" requirement: these only fire on an explicit click (see
`document_card.html`'s `hx-post`), never as a side effect of loading a page.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from job_automation.ai.llm_provider import LLMProvider
from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
from job_automation.database.models.user import User
from job_automation.documents.document_repository import DocumentRepository
from job_automation.documents.document_service import DocumentService
from job_automation.documents.export_manager import ExportManager
from job_automation.web.app import get_current_api_user, get_db_session

router = APIRouter(prefix="/api/documents", tags=["documents"])


class _NeverCalledLLMProvider(LLMProvider):
    """`DocumentService`'s constructor requires an `LLMProvider` even though
    approve/reject/export never call one (only `generate_*()` does — see
    `routes/documents.py`'s `get_llm_provider()`, used there instead, for
    the one action that actually needs a real, configured provider). Rather
    than requiring a working Anthropic API key just to approve a document —
    which would be a real functional bug, not a hypothetical one — this
    stub satisfies the constructor and would raise clearly if it were ever
    actually invoked, which it never is."""

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        raise AssertionError(
            "_NeverCalledLLMProvider.complete() was called — this should be unreachable for "
            "approve/reject/export, none of which generate text."
        )


@router.get("")
def list_documents(
    status: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> list[dict]:
    documents = DocumentRepository(session).list_for_user(current_user.id)
    if status:
        documents = [document for document in documents if document.status == status]
    return [_document_to_dict(document) for document in documents]


@router.post("/{document_id}/approve", response_class=HTMLResponse)
def approve_document(
    document_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> HTMLResponse:
    document = _owned_document_or_404(session, document_id, current_user)
    service = DocumentService(session, llm_provider=_NeverCalledLLMProvider())
    updated = service.approve(document.id)
    return HTMLResponse(_status_badge_html(updated.status))


@router.post("/{document_id}/reject", response_class=HTMLResponse)
def reject_document(
    document_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> HTMLResponse:
    _owned_document_or_404(session, document_id, current_user)
    service = DocumentService(session, llm_provider=_NeverCalledLLMProvider())
    updated = service.reject(document_id)
    return HTMLResponse(_status_badge_html(updated.status))


@router.get("/{document_id}/export")
def export_document(
    document_id: uuid.UUID,
    format: str = "markdown",
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_api_user),
) -> FileResponse:
    _owned_document_or_404(session, document_id, current_user)
    if format not in ("markdown", "txt"):
        raise HTTPException(status_code=400, detail="format must be 'markdown' or 'txt'")

    service = DocumentService(session, llm_provider=_NeverCalledLLMProvider(), export_manager=ExportManager())
    paths = service.export(document_id, formats=(format,))
    path = paths[format]
    return FileResponse(path, filename=path.name)


def _owned_document_or_404(session: Session, document_id: uuid.UUID, user: User) -> GeneratedDocumentRecord:
    document = DocumentRepository(session).get(document_id)
    if document is None or document.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _document_to_dict(document: GeneratedDocumentRecord) -> dict:
    return {
        "id": str(document.id),
        "document_type": document.document_type,
        "status": document.status,
        "question": document.question,
        "content": document.content,
        "validation_issues": document.validation_issues,
        "job_id": str(document.job_id) if document.job_id else None,
        "created_at": document.created_at.isoformat(),
    }


def _status_badge_html(status: str) -> str:
    label = status.replace("_", " ").title()
    colors = {
        "draft": "secondary", "needs_review": "warning", "approved": "success", "rejected": "danger",
    }
    return f'<span class="badge bg-{colors.get(status, "secondary")} status-badge">{label}</span>'
