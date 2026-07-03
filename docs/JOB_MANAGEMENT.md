# Job Management

`job_automation.job_organization` turns the dashboard into a personal job
management platform: save/favourite/hide/archive any job, track it through
a Kanban pipeline, attach notes/ratings/priority/deadlines/tags/checklists,
and set reminders — all layered on top of the existing job data without
touching the AI matching or application workflow subsystems.

**No Anthropic API, no live NHS scraping, no TRAC integration, no
deployment, no automatic application submission, no email/SMS/push
notifications** — every constraint carried over from prior milestones.
Every pipeline transition and every due reminder generates a real in-app
notification via the existing event bus; nothing else does.

## Architecture decision: `PipelineStage` is a new enum, not a reuse of `WorkflowStatus`

This was the central design decision of the milestone, and worth stating
up front since "reuse existing architecture, don't create duplicate
services" was an explicit instruction. `workflows.workflow_models
.WorkflowStatus` already has 10 values and a validated state machine, and
at first glance looks like a ready-made Kanban backbone. It was
deliberately **not** reused, for two concrete reasons:

1. **A real semantic collision on "Rejected."** `WorkflowStatus.REJECTED`
   means "a human reviewer rejected the AI-drafted document — loop back to
   `DOCUMENTS_GENERATED` and regenerate" (a mid-pipeline, non-terminal,
   document-review-specific outcome — see `application_workflow.py`'s
   `ALLOWED_TRANSITIONS`). This milestone's "Rejected" Kanban column means
   "the employer rejected the candidate's application" — a terminal,
   completely different real-world event. Storing both meanings under the
   identical enum value would be a genuine correctness bug (two different
   real-world facts indistinguishable by their stored status), not merely
   a naming inconvenience. `test_pipeline_stage_and_workflow_status_do_not
   _collide_on_rejected` (`tests/test_job_organization.py`) proves both
   "rejected" states can be true for the same (user, job) at once, with
   different terminality, without interfering.
2. **A job can be tracked before any `JobMatch`/`ApplicationWorkflowRecord`
   exists.** The pipeline's first two stages ("New," "Interested") describe
   a candidate's personal interest in a job possibly found manually, before
   any AI match or workflow was started. Modelling this on
   `ApplicationWorkflowRecord` would either force premature creation of a
   workflow row (dragging in the unrelated document-review gate —
   `NEEDS_REVIEW`/`APPROVED`/`READY_TO_APPLY` — for a job the candidate
   hasn't decided to pursue) or leave "Interested" with nothing to attach
   to.

`PipelineStage` + `JobPipeline` (`job_organization_models.py`) therefore
reuse the exact same *pattern* as `ApplicationWorkflow` (a frozen
`ALLOWED_TRANSITIONS`-style dict + `allowed_next_stages`/`can_transition`/
`validate_transition`/`is_terminal` static methods, raising a dedicated
exception) — for consistency and recognizability — without reusing its
*code*, since the two state machines protect genuinely different
invariants. Everything else in this milestone maximizes real reuse: the
repository/service pattern, the event bus + notification system, the
scheduler task pattern, and an additive extension of the existing
`JobRepository.search()`/`JobFilter` rather than a parallel search
mechanism.

## New database tables

Migration `e9c269d9e55f` (head; parent `bfbc84b1875c`).

### `saved_jobs`

One row per `(user_id, job_id)` — a candidate's personal tracking state for
one job. Created on first save/favourite/hide/stage-change via
`SavedJobRepository.get_or_create()`, never eagerly for every job.

| Column | Type | Notes |
|---|---|---|
| `job_id`, `user_id` | FK, `ondelete="CASCADE"` | unique together |
| `is_saved`, `is_favourite`, `is_hidden`, `is_archived` | Boolean, indexed | organization flags |
| `pipeline_stage` | String(30), indexed | `PipelineStage.value`, not null |
| `notes` | Text | |
| `personal_rating` | Integer | 1–5, validated in the service layer |
| `priority` | String(20) | `JobPriority.value` |
| `deadline` | Date | |
| `interview_date` | DateTime(timezone=True) | |
| `tags` | JSON `list[str]` | full-replacement, not incremental |
| `checklist` | JSON `list[{"label": str, "done": bool}]` | |

Deliberately separate from `JobMatch` (an AI-computed score — a job can be
tracked without ever being matched) and from `ApplicationWorkflowRecord`
(the document-review state machine — see the architecture decision above).
`tags`/`checklist` are JSON blobs on the parent row, not child tables —
matching this project's existing "JSON for an evolving, per-parent-row
blob" convention (`Job.requirements`, `JobMatch.analysis`), reserved for
data that's never queried independently of its parent.

### `job_reminders`

One row per reminder, belonging to a `saved_jobs` row.

| Column | Type | Notes |
|---|---|---|
| `saved_job_id` | FK, `ondelete="CASCADE"` | |
| `reminder_type` | String(30) | `ReminderType.value` |
| `remind_at` | DateTime(timezone=True), indexed, not null | |
| `message` | Text, nullable | |
| `is_sent` | Boolean, indexed, default False | |

An independent table (not a JSON blob like `tags`/`checklist`) because
reminders **are** queried independently of their parent — `list_due()`
scans across every user's reminders for the scheduler task, which a JSON
column on `saved_jobs` couldn't do without a full table scan.

## Services created

```
src/job_automation/job_organization/
├── job_organization_models.py   — PipelineStage, JobPriority, ReminderType,
│                                   JobPipeline (state machine), InvalidStageTransitionError
├── saved_job_repository.py      — SavedJobRepository (pure data access)
├── job_organization_service.py  — JobOrganizationService (the orchestrator)
├── reminder_repository.py       — ReminderRepository (pure data access)
└── reminder_service.py          — ReminderService (the orchestrator)
```

- **`JobOrganizationService`** — save/unsave, favourite/unfavourite,
  hide/unhide, archive/restore, `update_stage()` (validates via
  `JobPipeline.validate_transition()`, then publishes
  `PIPELINE_STAGE_UPDATED`), `update_details()` (notes/rating/priority/
  deadline/interview date — rating validated 1–5), `set_tags()` (full
  replacement), `add_checklist_item()`/`toggle_checklist_item()`/
  `remove_checklist_item()`. **Flag toggles and detail edits deliberately
  do not publish notifications** — only pipeline transitions do (see
  "Known limitations").
- **`ReminderService`** — `create_reminder()` (calls `SavedJobRepository
  .get_or_create()` first, so setting a reminder on an untouched job
  silently starts tracking it), `list_for_job()`, `list_upcoming_for_user()`,
  `delete_reminder()` (ownership-checked), `process_due_reminders()`
  (publishes `REMINDER_DUE` per due reminder, then marks it sent —
  genuinely reusable business logic, wrapped by the scheduler task the same
  way `run_ai_matching.py` wraps `MatchingService`).

Both take a constructor-injectable `event_bus` parameter defaulting to the
shared singleton, matching every other event-publishing service in this
app (`StatusManager`, `DocumentService`, `AuthService`, `SchedulerService`).

### Notifications and the scheduler

Two new `EventType`/`NotificationType` pairs, mirroring 1:1 as this
project's convention requires: `PIPELINE_STAGE_UPDATED` (severity `error`
if the target stage is `rejected`, else `success`) and `REMINDER_DUE`
(severity `warning`). Two new listener functions in
`notification_listeners.py` — neither service imports `NotificationService`
directly.

A sixth scheduled task, `send_due_reminders` (interval:
`settings.scheduler_reminders_interval_seconds`, default 15 minutes),
registered in `scheduler.task_registry.TASK_REGISTRY` exactly like the
existing five — same `TaskDefinition`/`run(session) -> dict` shape, same
manual "Run now" + periodic-trigger paths.

### Search/filter extension

`JobRepository.search()`/`JobFilter` (`database/repositories/job_repository.py`)
were extended additively, not replaced: `max_salary`, `remote`,
`closing_soon`, `expired`, `keywords` (a synonym search field for the
dashboard's dedicated "Keywords" box — functionally identical to `search`
today), and `user_id`-scoped `saved_only`/`favourite_only`/`archived_only`/
`pipeline_stage` filters, applied via a `LEFT OUTER JOIN` onto `saved_jobs`
only when `user_id` is set. Every pre-existing filter still behaves
identically with `user_id=None`. Two new sortable columns: `employer`,
`band` (Newest/Oldest/salary/deadline were already supported).

`routes/jobs.py`'s `/jobs` list now always sets `filters.user_id` to the
logged-in user, so hidden/archived jobs are excluded from the main list by
default — "Hide" only means something if hidden jobs actually disappear
from view.

## Screens implemented

- **`/jobs`** — extended with Keywords, Max salary, Status (pipeline
  stage), Remote/Closing soon/Expired/Saved/Favourite/Archived checkboxes,
  and Employer A-Z/Band sort options. Each card
  (`components/job_card.html`) now shows a pipeline-stage badge and
  Save/Favourite/Hide/Archive buttons (plain form POSTs, full page
  reload — consistent with every other mutation in this dashboard).
- **`/jobs/{id}`** — a new "Organization" panel: flag buttons, a
  "Move to: <next stage>" button per stage `JobPipeline.allowed_next_stages()`
  actually permits (never every stage — invalid transitions aren't offered
  in the UI at all), the notes/rating/priority/deadline/interview-date
  form, a tags editor, a checklist (add/toggle/remove), and a reminders
  list (add/delete).
- **`/board`** (new) — the Kanban board: every non-hidden, non-archived
  tracked job for the current user, grouped into one column per
  `PipelineStage`, with a "move forward" button per card. **Button-based,
  not drag-and-drop** — see "Known limitations."
- **`/dashboard`** — a new "Job organization" section: Jobs saved,
  Applications, Interviews, Offers, Rejected, Favourited, Upcoming
  deadlines, Upcoming reminders stat cards; a pipeline-stage bar chart
  (Chart.js, matching the existing Analytics page's chart pattern); a
  Favourite employers list; a "Job pipeline activity" feed that reuses
  `NotificationService.list_notifications()` rather than deriving a
  second activity feed (pipeline transitions and due reminders are already
  real notifications).
- **`api/job_organization_api.py`** (new) — JSON equivalents of every
  mutation/read above, under `/api/job-organization`.
- **`api/jobs_api.py`, `routes/jobs.py`** — extended with the same new
  filter query parameters as the HTML page.
- **`api/dashboard_api.py`** — new `GET /api/dashboard/job-organization`
  endpoint (backs the dashboard's pipeline-stage chart).

## Testing

- **`tests/test_job_organization.py`** (21 tests) — the `JobPipeline` state
  machine (valid/invalid transitions, terminal check); save/favourite/
  hide/archive/restore round-trips and idempotency; pipeline transitions
  publish exactly one notification, invalid ones publish none, flag
  toggles publish none; notes/rating validation/priority/deadline;
  tags full-replacement; checklist add/toggle/remove including
  out-of-range `IndexError`; reminders create/list/process-due/delete with
  ownership enforcement; `AnalyticsService.job_organization_summary()`;
  `SavedJobRepository.map_by_job_id()`; a regression test proving
  `PipelineStage.REJECTED` and `WorkflowStatus.REJECTED` coexist
  independently for the same job; cascade-delete regression tests for
  `Job`/`User` deletion (using a dedicated fixture with SQLite's
  `PRAGMA foreign_keys=ON` enabled, since `passive_deletes=True` relies on
  the database enforcing the cascade, and the shared test fixture doesn't
  turn that pragma on).
- **`tests/test_job_search.py`** (10 tests) — every new `JobFilter` field,
  the two new sortable columns, user-scoped hidden/archived exclusion, the
  "archived-only" restore view, and a regression test proving the
  unscoped (`user_id=None`) call shape is unaffected by the new join.
- **`tests/test_job_organization_web.py`** (25 tests) — every HTML route
  (flags, stage transitions with the safe `?next=` redirect, details,
  tags, checklist, reminders) and JSON API endpoint via a real
  `TestClient`; the `/jobs` list's new filters and hidden-by-default
  behavior; the `/board` page; the dashboard's new section.

Full suite: 273 tests, zero regressions (up from 217 at the start of this
milestone).

Manually verified end-to-end in a live browser session: favouriting a job
from the Jobs list, moving it through Interested → Documents Ready on both
the job detail page and the Board page (confirming the `?next=/board`
redirect returns to the Board rather than the job detail page), adding a
checklist item, and confirming each pipeline transition produced exactly
one real notification (bell count incremented, message text correct).

## Known limitations

- **The Kanban board is button-based, not drag-and-drop.** A "move to
  <next stage>" button per card satisfies "Kanban-style workflow" without
  the added surface area of a JS drag library, client-side state
  reconciliation, and a PATCH-style endpoint — genuinely out of scope for
  "don't add unnecessary complexity." The underlying `POST /jobs/{id}/stage`
  endpoint is drag-and-drop-ready if a future milestone wants to wire one
  up.
- **Flag toggles and detail edits (notes/rating/tags/etc.) don't generate
  notifications** — only pipeline-stage transitions and due reminders do.
  The milestone's requirement was "every transition must create a
  notification"; toggling a checkbox or editing a note isn't a pipeline
  transition, and notifying on every keystroke-adjacent edit would make
  the notification feed noise rather than signal.
- **`keywords` and `search` are functionally identical today** — both
  OR-match against title/description. They're kept as separate `JobFilter`
  fields (matching the milestone's spec, which lists them as distinct
  search inputs) so a future version can differentiate them (e.g.
  `keywords` matching against `Job.requirements`/`Job.benefits`) without
  a breaking field rename.
- **Invalid pipeline-stage transitions surface as a plain 400 error page**
  on the HTML route (not an inline flash message) — acceptable since the
  UI only ever renders buttons for stages `JobPipeline.allowed_next_stages()`
  actually permits; reaching an invalid transition requires manually
  crafting a request.
- **No drag-and-drop, no real-time board updates, no per-user notification
  preferences** — same "not built this milestone" scope as prior
  milestones' extension-points lists.
