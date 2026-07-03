"""
Scheduled task: ensure every `JobMatch` has a corresponding
`ApplicationWorkflowRecord` — the baseline bookkeeping step "update
workflow statuses" can safely mean for a background task with no human in
the loop.

**Deliberately does not drive any workflow transition beyond creating it
at `NEW_MATCH`.** `WorkflowService.start_workflow()` is idempotent per
(user, job) (returns the existing workflow unchanged if one already
exists — see docs/APPLICATION_WORKFLOW.md), so running this task
repeatedly never resets or disturbs a workflow a human has already moved
forward (reviewed, approved, applied, etc.). This task intentionally does
**not** call `submit_for_review()`, `approve()`, `reject()`,
`mark_ready_to_apply()`, `mark_applied()`, or `close()` — every one of
those represents a decision only a human makes (see
`WorkflowService`'s own module docstring: "no method on this class
submits, sends, or applies to anything" without an explicit human call).
Automatically driving those from a schedule would violate this project's
standing "never auto-approve" / "no automatic application submission"
rules just as much as building real auto-apply would.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models.job_match import JobMatch
from job_automation.utils.logger import logger
from job_automation.workflows.workflow_repository import WorkflowRepository
from job_automation.workflows.workflow_service import WorkflowService


def run(session: Session) -> dict:
    repository = WorkflowRepository(session)
    service = WorkflowService(session, repository=repository)

    created = 0
    already_existed = 0

    for match in session.scalars(select(JobMatch)):
        existing = repository.find_by_job_and_user(match.job_id, match.user_id)
        if existing is not None:
            already_existed += 1
            continue
        service.start_workflow(user_id=match.user_id, job_id=match.job_id, job_match_id=match.id)
        created += 1

    logger.info("update_workflow_statuses: {} workflows created, {} already existed", created, already_existed)
    return {"workflows_created": created, "workflows_already_existed": already_existed}
