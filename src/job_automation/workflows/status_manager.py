"""
Performs a validated status transition on an `ApplicationWorkflowRecord`:
checks the move is allowed (`application_workflow.ApplicationWorkflow`),
records it in `workflow_status_history` (via `WorkflowRepository`), and
updates the record's current status. Kept separate from
`application_workflow.py` (the rules) and from `review_service.py` (the
specific business meaning of an approve/reject decision, which is built on
top of this).

Publishes a `WORKFLOW_UPDATED` event (never calls `NotificationService`
directly — see `notifications.events`'s module docstring) from this one
choke point — every workflow transition in the whole app funnels through
`transition()`, whether it's the automatic `NEW_MATCH`/`REJECTED` ->
`DOCUMENTS_GENERATED` advance or an explicit human approve/reject/
mark-applied decision, so this single hook covers all of them.
"""

from __future__ import annotations

from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.workflows.application_workflow import ApplicationWorkflow
from job_automation.workflows.workflow_models import WorkflowStatus
from job_automation.workflows.workflow_repository import WorkflowRepository
from job_automation.utils.logger import logger


class StatusManager:
    def __init__(self, repository: WorkflowRepository, *, event_bus: EventBus = event_bus) -> None:
        self._repository = repository
        self._event_bus = event_bus

    def transition(self, workflow, target: WorkflowStatus, *, note: str | None = None):
        current = WorkflowStatus(workflow.status)
        ApplicationWorkflow.validate_transition(current, target)

        self._repository.add_status_history(
            workflow, from_status=current.value, to_status=target.value, note=note
        )
        updated = self._repository.update_status(workflow, target.value)
        logger.info("Workflow {} transitioned {} -> {}", workflow.id, current.value, target.value)
        self._event_bus.publish(
            Event(
                event_type=EventType.WORKFLOW_UPDATED,
                payload={
                    "workflow_id": str(workflow.id),
                    "job_id": str(workflow.job_id),
                    "from_status": current.value,
                    "to_status": target.value,
                },
                user_id=workflow.user_id,
            ),
            self._repository.session,
        )
        return updated
