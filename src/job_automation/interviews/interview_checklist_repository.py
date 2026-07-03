"""
Persists `InterviewChecklistItem` rows. Pure data access, following the
same repository pattern as every other repository in this project.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.interview_checklist_item import InterviewChecklistItem


class InterviewChecklistRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, interview_id: uuid.UUID, label: str) -> InterviewChecklistItem:
        item = InterviewChecklistItem(interview_id=interview_id, label=label)
        self._session.add(item)
        self._session.flush()
        return item

    def create_many(self, *, interview_id: uuid.UUID, labels: list[str]) -> list[InterviewChecklistItem]:
        items = [InterviewChecklistItem(interview_id=interview_id, label=label) for label in labels]
        self._session.add_all(items)
        self._session.flush()
        return items

    def get(self, item_id: uuid.UUID) -> InterviewChecklistItem | None:
        return self._session.get(InterviewChecklistItem, item_id)

    def list_for_interview(self, interview_id: uuid.UUID) -> list[InterviewChecklistItem]:
        return list(
            self._session.scalars(
                select(InterviewChecklistItem)
                .where(InterviewChecklistItem.interview_id == interview_id)
                .order_by(InterviewChecklistItem.created_at)
            )
        )

    def update(self, item: InterviewChecklistItem, **fields) -> InterviewChecklistItem:
        for key, value in fields.items():
            setattr(item, key, value)
        self._session.flush()
        return item

    def delete(self, item: InterviewChecklistItem) -> None:
        self._session.delete(item)
        self._session.flush()
