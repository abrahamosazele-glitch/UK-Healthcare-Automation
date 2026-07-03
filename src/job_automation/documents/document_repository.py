"""
Persists `GeneratedDocumentRecord` rows — pure data access, no business
logic (deciding a document's status, mapping a `GeneratedDocument` onto
columns) — that's `document_service.py`'s job. Follows the same repository
pattern as `database.repositories`/`profile.profile_repository`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.generated_document_record import GeneratedDocumentRecord


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, **fields: Any) -> GeneratedDocumentRecord:
        record = GeneratedDocumentRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    def get(self, document_id: uuid.UUID) -> GeneratedDocumentRecord | None:
        return self._session.get(GeneratedDocumentRecord, document_id)

    def list_for_user(self, user_id: uuid.UUID) -> list[GeneratedDocumentRecord]:
        return list(
            self._session.scalars(
                select(GeneratedDocumentRecord)
                .where(GeneratedDocumentRecord.user_id == user_id)
                .order_by(GeneratedDocumentRecord.created_at.desc())
            )
        )

    def update_status(
        self, record: GeneratedDocumentRecord, status: str, *, review_notes: str | None = None
    ) -> GeneratedDocumentRecord:
        record.status = status
        if review_notes is not None:
            record.review_notes = review_notes
        self._session.flush()
        return record
