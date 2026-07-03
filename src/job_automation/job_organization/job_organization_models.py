"""
Value objects for the job organization subsystem: the personal Kanban
pipeline, priority, and reminder types. Deliberately dependency-free — same
design as `workflows.workflow_models`, `scheduler.scheduler_models`,
`notifications.notification_models`: pure dataclasses/enums, no
SQLAlchemy imports. The canonical `PipelineStage`/`JobPriority`/
`ReminderType` values live here; the ORM side (`database.models.saved_job
.SavedJob`, `database.models.job_reminder.JobReminder`) stores `.value` as
a plain string, for the same reason `ApplicationWorkflowRecord` doesn't
import `WorkflowStatus`.

## Why `PipelineStage` is a new enum, not a reuse of `WorkflowStatus`

This was the central architecture decision of the Job Management milestone
(see docs/JOB_MANAGEMENT.md for the full writeup). `workflows.workflow_models
.WorkflowStatus` already has 10 values and a validated state machine, and
at first glance looks like a ready-made Kanban backbone. It was deliberately
**not** reused, for two concrete reasons:

1. **A real semantic collision on "rejected."** `WorkflowStatus.REJECTED`
   means "a human reviewer rejected the AI-drafted document — loop back
   and regenerate" (see `application_workflow.py`: it transitions back to
   `DOCUMENTS_GENERATED`, not to a terminal state). This milestone's
   "Rejected" Kanban column means "the employer rejected the candidate's
   application" — a terminal, unrelated-to-document-review outcome.
   Reusing the same enum value for both meanings would be a genuine
   correctness bug (two different real-world events indistinguishable by
   their stored status), not just a naming inconvenience.
2. **A job can be tracked before any workflow/match exists.** The
   requested pipeline's first two stages ("New," "Interested") describe a
   candidate's personal interest in a job they may have found manually —
   before any `JobMatch` was computed or any `ApplicationWorkflowRecord`
   was started. Modelling that on `ApplicationWorkflowRecord` would mean
   either creating one prematurely (dragging in the unrelated
   document-generation-and-review gate — `NEEDS_REVIEW`/`APPROVED`/
   `READY_TO_APPLY` — for a job the candidate hasn't decided to pursue
   yet) or leaving `PipelineStage` orphaned from any real row until much
   later, which defeats the purpose of an early "Interested" stage.

`PipelineStage` is therefore a separate, simpler, purpose-built state
machine for personal job tracking. It reuses the exact same *pattern* as
`ApplicationWorkflow` (a frozen `ALLOWED_TRANSITIONS` dict + a
validate/raise helper) — deliberately, for consistency — without reusing
its *code*, since the two state machines protect genuinely different
invariants (document-review gating vs. personal progress tracking).
"""

from __future__ import annotations

import enum


class PipelineStage(str, enum.Enum):
    #: The candidate hasn't engaged with this job yet beyond it existing
    #: in the shared job list (or being AI-matched).
    NEW = "new"
    #: The candidate has expressed interest — saved/favourited it, or
    #: manually moved it forward — but nothing else has happened yet.
    INTERESTED = "interested"
    #: A supporting statement/cover letter/answer has been drafted for
    #: this job (see `notifications.events.EventType.DOCUMENT_GENERATED`
    #: for the same underlying signal in the document-generation system).
    DOCUMENTS_READY = "documents_ready"
    #: The candidate has manually applied (recorded, never triggered
    #: automatically — same "no automatic submission" rule as
    #: `WorkflowService.mark_applied()`).
    APPLIED = "applied"
    #: Invited to interview.
    INTERVIEW = "interview"
    #: An offer has been made.
    OFFER = "offer"
    #: Terminal — the employer rejected the application (or the candidate
    #: declined/withdrew). Distinct from `WorkflowStatus.REJECTED` — see
    #: this module's docstring.
    REJECTED = "rejected"


class JobPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReminderType(str, enum.Enum):
    DEADLINE = "deadline"
    INTERVIEW = "interview"
    DOCUMENTS_NEEDED = "documents_needed"
    REFERENCE_REQUEST = "reference_request"


class InvalidStageTransitionError(Exception):
    """Raised when a requested pipeline stage transition isn't allowed."""


#: Mirrors `workflows.application_workflow.ALLOWED_TRANSITIONS`'s shape and
#: "terminal state reachable from anywhere non-terminal" design (there,
#: `CLOSED`; here, `REJECTED`) — deliberately the same pattern, not the
#: same code (see this module's docstring for why they're separate).
ALLOWED_STAGE_TRANSITIONS: dict[PipelineStage, frozenset[PipelineStage]] = {
    PipelineStage.NEW: frozenset({PipelineStage.INTERESTED, PipelineStage.REJECTED}),
    PipelineStage.INTERESTED: frozenset({PipelineStage.DOCUMENTS_READY, PipelineStage.REJECTED}),
    PipelineStage.DOCUMENTS_READY: frozenset({PipelineStage.APPLIED, PipelineStage.REJECTED}),
    PipelineStage.APPLIED: frozenset({PipelineStage.INTERVIEW, PipelineStage.REJECTED}),
    PipelineStage.INTERVIEW: frozenset({PipelineStage.OFFER, PipelineStage.REJECTED}),
    PipelineStage.OFFER: frozenset({PipelineStage.REJECTED}),
    PipelineStage.REJECTED: frozenset(),
}


class JobPipeline:
    @staticmethod
    def allowed_next_stages(current: PipelineStage) -> frozenset[PipelineStage]:
        return ALLOWED_STAGE_TRANSITIONS.get(current, frozenset())

    @staticmethod
    def can_transition(current: PipelineStage, target: PipelineStage) -> bool:
        return target in JobPipeline.allowed_next_stages(current)

    @staticmethod
    def validate_transition(current: PipelineStage, target: PipelineStage) -> None:
        if not JobPipeline.can_transition(current, target):
            raise InvalidStageTransitionError(
                f"Cannot move from {current.value!r} to {target.value!r}. "
                f"Allowed: {sorted(s.value for s in JobPipeline.allowed_next_stages(current))}"
            )

    @staticmethod
    def is_terminal(stage: PipelineStage) -> bool:
        return not JobPipeline.allowed_next_stages(stage)
