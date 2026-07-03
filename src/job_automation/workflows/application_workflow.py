"""
The application workflow state machine: which `WorkflowStatus` transitions
are valid. Pure domain logic — no database, no I/O — kept separate from
`status_manager.py`, which actually *performs* a transition (persisting it
and recording history); this module only knows the rules.

`CLOSED` is reachable from every non-terminal status (a candidate can
abandon a job application at any stage) and is itself terminal. `REJECTED`
loops back to `DOCUMENTS_GENERATED` — rejection means "regenerate the
documents and try again," not "this job is dead" (that's what `CLOSED` is
for). Every other transition follows the natural forward journey: a match
gets documents generated, submitted for review, approved, marked ready,
manually applied to, interviewed, and offered.
"""

from __future__ import annotations

from job_automation.workflows.workflow_models import WorkflowStatus


class InvalidTransitionError(Exception):
    """Raised when a requested status transition isn't allowed."""


ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.NEW_MATCH: frozenset({WorkflowStatus.DOCUMENTS_GENERATED, WorkflowStatus.CLOSED}),
    WorkflowStatus.DOCUMENTS_GENERATED: frozenset({WorkflowStatus.NEEDS_REVIEW, WorkflowStatus.CLOSED}),
    WorkflowStatus.NEEDS_REVIEW: frozenset(
        {WorkflowStatus.APPROVED, WorkflowStatus.REJECTED, WorkflowStatus.CLOSED}
    ),
    WorkflowStatus.APPROVED: frozenset({WorkflowStatus.READY_TO_APPLY, WorkflowStatus.CLOSED}),
    WorkflowStatus.REJECTED: frozenset({WorkflowStatus.DOCUMENTS_GENERATED, WorkflowStatus.CLOSED}),
    WorkflowStatus.READY_TO_APPLY: frozenset({WorkflowStatus.APPLIED, WorkflowStatus.CLOSED}),
    WorkflowStatus.APPLIED: frozenset({WorkflowStatus.INTERVIEW, WorkflowStatus.CLOSED}),
    WorkflowStatus.INTERVIEW: frozenset({WorkflowStatus.OFFER, WorkflowStatus.CLOSED}),
    WorkflowStatus.OFFER: frozenset({WorkflowStatus.CLOSED}),
    WorkflowStatus.CLOSED: frozenset(),
}


class ApplicationWorkflow:
    @staticmethod
    def allowed_next_statuses(current: WorkflowStatus) -> frozenset[WorkflowStatus]:
        return ALLOWED_TRANSITIONS.get(current, frozenset())

    @staticmethod
    def can_transition(current: WorkflowStatus, target: WorkflowStatus) -> bool:
        return target in ApplicationWorkflow.allowed_next_statuses(current)

    @staticmethod
    def validate_transition(current: WorkflowStatus, target: WorkflowStatus) -> None:
        if not ApplicationWorkflow.can_transition(current, target):
            raise InvalidTransitionError(
                f"Cannot transition from {current.value!r} to {target.value!r}. "
                f"Allowed: {sorted(s.value for s in ApplicationWorkflow.allowed_next_statuses(current))}"
            )

    @staticmethod
    def is_terminal(status: WorkflowStatus) -> bool:
        return not ApplicationWorkflow.allowed_next_statuses(status)
