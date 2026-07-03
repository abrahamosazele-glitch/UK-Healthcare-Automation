"""
Value objects for the interview & calendar management subsystem.
Deliberately dependency-free — same design as `job_organization
.job_organization_models`, `workflows.workflow_models`: pure
dataclasses/enums, no SQLAlchemy, no other package imports. The canonical
`InterviewType`/`InterviewStage`/`InterviewStatus`/`ReminderOffset`/
`NoteCategory` values live here; the ORM side
(`database.models.interview_record.InterviewRecord`,
`.interview_note.InterviewNote`, `.interview_reminder.InterviewReminder`)
stores `.value` as a plain string, for the same reason `SavedJob` doesn't
import `PipelineStage`.

## `interview_type` vs `interview_stage`

The milestone brief lists "Second Interview"/"Final Interview" under
*interview types* alongside format-based types (phone/video/face-to-face/
etc.), then separately requires an `interview_stage` field. Implemented
literally rather than resolved by inventing a type/format split the brief
didn't ask for: `InterviewType` has exactly the 8 listed values (including
`SECOND_INTERVIEW`/`FINAL_INTERVIEW`), and `InterviewStage` is a distinct,
free-er field for where this interview sits in a multi-round process
("first_round", "second_round", ...) — useful when, say, a "Video
Interview" *type* is also the candidate's "second_round" *stage*. The two
fields answer different questions (what kind of session was it vs. which
round of the process) and can genuinely disagree in the data.

## Why `InterviewLifecycle` exists but is looser than `JobPipeline`

Reuses the same *pattern* as `job_organization.job_organization_models
.JobPipeline` and `workflows.application_workflow.ApplicationWorkflow` (a
frozen `ALLOWED_TRANSITIONS` dict + validate/raise static methods) for
consistency — but the interview status set isn't a strict linear Kanban:
`CANCELLED`/`MISSED` can interrupt from more than one state, and
`RESCHEDULED` is a deliberately transient waypoint back to `SCHEDULED`
(see `interview_service.py`'s `reschedule()`), not a resting state. The
milestone doesn't ask for as rigid a pipeline as the Job Management
Kanban board did, so this is intentionally a looser state machine.
"""

from __future__ import annotations

import enum
from datetime import timedelta


class InterviewType(str, enum.Enum):
    PHONE = "phone"
    VIDEO = "video"
    FACE_TO_FACE = "face_to_face"
    ASSESSMENT_CENTRE = "assessment_centre"
    PRACTICAL_ASSESSMENT = "practical_assessment"
    INFORMAL_CHAT = "informal_chat"
    SECOND_INTERVIEW = "second_interview"
    FINAL_INTERVIEW = "final_interview"


class InterviewStage(str, enum.Enum):
    FIRST_ROUND = "first_round"
    SECOND_ROUND = "second_round"
    THIRD_ROUND = "third_round"
    FINAL_ROUND = "final_round"
    ASSESSMENT = "assessment"
    OFFER_STAGE = "offer_stage"


class InterviewStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    UPCOMING = "upcoming"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"
    MISSED = "missed"
    OFFER_RECEIVED = "offer_received"
    REJECTED = "rejected"
    WAITING_DECISION = "waiting_decision"


class ReminderOffset(str, enum.Enum):
    SEVEN_DAYS = "seven_days"
    THREE_DAYS = "three_days"
    ONE_DAY = "one_day"
    TWO_HOURS = "two_hours"
    THIRTY_MINUTES = "thirty_minutes"

    @property
    def timedelta(self) -> timedelta:
        return _OFFSET_DURATIONS[self]


_OFFSET_DURATIONS: dict[ReminderOffset, timedelta] = {
    ReminderOffset.SEVEN_DAYS: timedelta(days=7),
    ReminderOffset.THREE_DAYS: timedelta(days=3),
    ReminderOffset.ONE_DAY: timedelta(days=1),
    ReminderOffset.TWO_HOURS: timedelta(hours=2),
    ReminderOffset.THIRTY_MINUTES: timedelta(minutes=30),
}


class NoteCategory(str, enum.Enum):
    QUESTIONS_ASKED = "questions_asked"
    MY_ANSWERS = "my_answers"
    RECRUITER_FEEDBACK = "recruiter_feedback"
    THINGS_TO_IMPROVE = "things_to_improve"
    SALARY_DISCUSSED = "salary_discussed"
    NEXT_STEPS = "next_steps"
    GENERAL = "general"


#: Seeded onto every newly-scheduled interview by
#: `InterviewService.schedule()` — the exact list the milestone brief
#: specifies. A candidate can add/remove items beyond these defaults.
DEFAULT_CHECKLIST_ITEMS: tuple[str, ...] = (
    "Research employer",
    "Review job description",
    "Review CV",
    "Prepare STAR examples",
    "Prepare questions",
    "Prepare documents",
    "Prepare uniform",
    "Test camera & microphone",
    "Print directions",
    "Travel plan",
)


class InvalidInterviewStatusTransitionError(Exception):
    """Raised when a requested interview status transition isn't allowed."""


#: `RESCHEDULED` is reachable from every non-terminal, already-booked
#: state and always leads straight back to `SCHEDULED` (see
#: `InterviewService.reschedule()`) — it's a waypoint, not a resting
#: state. `CANCELLED`/`OFFER_RECEIVED`/`REJECTED` are terminal.
ALLOWED_INTERVIEW_TRANSITIONS: dict[InterviewStatus, frozenset[InterviewStatus]] = {
    InterviewStatus.SCHEDULED: frozenset(
        {InterviewStatus.UPCOMING, InterviewStatus.CANCELLED, InterviewStatus.RESCHEDULED}
    ),
    InterviewStatus.UPCOMING: frozenset(
        {
            InterviewStatus.COMPLETED,
            InterviewStatus.CANCELLED,
            InterviewStatus.MISSED,
            InterviewStatus.RESCHEDULED,
        }
    ),
    InterviewStatus.RESCHEDULED: frozenset({InterviewStatus.SCHEDULED}),
    InterviewStatus.COMPLETED: frozenset(
        {InterviewStatus.OFFER_RECEIVED, InterviewStatus.REJECTED, InterviewStatus.WAITING_DECISION}
    ),
    InterviewStatus.WAITING_DECISION: frozenset({InterviewStatus.OFFER_RECEIVED, InterviewStatus.REJECTED}),
    InterviewStatus.MISSED: frozenset({InterviewStatus.RESCHEDULED, InterviewStatus.CANCELLED}),
    InterviewStatus.CANCELLED: frozenset(),
    InterviewStatus.OFFER_RECEIVED: frozenset(),
    InterviewStatus.REJECTED: frozenset(),
}

#: Statuses `InterviewService.reschedule()` will act on — anything still
#: "in play" that a new date/time makes sense for.
RESCHEDULABLE_STATUSES: frozenset[InterviewStatus] = frozenset(
    {InterviewStatus.SCHEDULED, InterviewStatus.UPCOMING, InterviewStatus.MISSED, InterviewStatus.RESCHEDULED}
)


class InterviewLifecycle:
    @staticmethod
    def allowed_next_statuses(current: InterviewStatus) -> frozenset[InterviewStatus]:
        return ALLOWED_INTERVIEW_TRANSITIONS.get(current, frozenset())

    @staticmethod
    def can_transition(current: InterviewStatus, target: InterviewStatus) -> bool:
        return target in InterviewLifecycle.allowed_next_statuses(current)

    @staticmethod
    def validate_transition(current: InterviewStatus, target: InterviewStatus) -> None:
        if not InterviewLifecycle.can_transition(current, target):
            raise InvalidInterviewStatusTransitionError(
                f"Cannot move from {current.value!r} to {target.value!r}. "
                f"Allowed: {sorted(s.value for s in InterviewLifecycle.allowed_next_statuses(current))}"
            )

    @staticmethod
    def is_terminal(status: InterviewStatus) -> bool:
        return not InterviewLifecycle.allowed_next_statuses(status)
