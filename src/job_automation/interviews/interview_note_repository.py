"""
Persists `InterviewNote` rows. Pure data access, following the same
repository pattern as every other repository in this project.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.interview_note import InterviewNote


class InterviewNoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, interview_id: uuid.UUID, category: str, body: str) -> InterviewNote:
        note = InterviewNote(interview_id=interview_id, category=category, body=body)
        self._session.add(note)
        self._session.flush()
        return note

    def get(self, note_id: uuid.UUID) -> InterviewNote | None:
        return self._session.get(InterviewNote, note_id)

    def list_for_interview(self, interview_id: uuid.UUID, *, category: str | None = None) -> list[InterviewNote]:
        stmt = select(InterviewNote).where(InterviewNote.interview_id == interview_id)
        if category is not None:
            stmt = stmt.where(InterviewNote.category == category)
        return list(self._session.scalars(stmt.order_by(InterviewNote.created_at.desc())))

    def delete(self, note: InterviewNote) -> None:
        self._session.delete(note)
        self._session.flush()
