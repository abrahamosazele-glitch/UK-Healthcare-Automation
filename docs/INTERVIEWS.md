# Interview & Calendar Management

`job_automation.interviews` gives candidates a full interview-planning
system: schedule an interview (with a seeded preparation checklist and
configurable reminders), track it through a status lifecycle, log
categorized notes before/during/after, and see it on a month/week/day
calendar. It integrates with — but never automatically drives — the
existing Application Workflow, Employer CRM, Notifications, and Dashboard
subsystems.

**No Anthropic API, no live scraping, no automatic application
submission, no deployment, no real email/SMS, no Outlook/Google Calendar
integration, no automatic interview scheduling.** Every constraint carried
over from prior milestones plus this milestone's own explicit list.

## Architecture decision: `InterviewRecord`, not `Interview`

This codebase already has an `Interview` model
(`database.models.interview.Interview`, table `interviews`) — but it
belongs to the original pre-workflow scaffold, tied to a `CV`/`CoverLetter`/
`Application` object graph that nothing in this application actually uses
(no route, no service, no test touches it; the real "application" concept
has been `ApplicationWorkflowRecord` since the Application Workflow
Management milestone). Reusing that name or table would either collide or
silently resurrect dead code.

The new model is named `InterviewRecord` (table `interview_records`) —
this codebase's established disambiguation convention for exactly this
situation. `ApplicationWorkflowRecord`, `CandidateProfileRecord`, and
`GeneratedDocumentRecord` all exist for the identical reason: a `...Record`
suffix on the *current*, real implementation, leaving an old/unused name
alone rather than deleting or repurposing code outside this milestone's
scope.

## Architecture decision: `interview_type` vs. `interview_stage`

The milestone brief lists "Second Interview"/"Final Interview" as
*interview types*, alongside format-based types (phone, video,
face-to-face, assessment centre, practical assessment, informal chat), and
separately requires an `interview_stage` field. Rather than inventing a
type/format split the brief didn't ask for, both are implemented
literally: `InterviewType` has exactly the 8 listed values, and
`InterviewStage` is a distinct, free-er field for which *round* of a
multi-round process this interview represents (`first_round`,
`second_round`, `third_round`, `final_round`, `assessment`, `offer_stage`).
The two fields answer different questions — what kind of session was it,
vs. which round of the process — and can genuinely disagree in the data
(a "Video Interview" *type* can also be the candidate's "second_round"
*stage*).

## Architecture decision: workflow integration is opt-in and always explicit

The brief is explicit: "No automatic workflow changes should occur
without explicit user actions." `InterviewService.sync_workflow_status()`
calls the *existing* `WorkflowService.mark_interview()`/`.mark_offer()`/
`.close()` methods — reused exactly as they already were, no new
transition logic — but only when a user clicks one of three dedicated
buttons on the interview detail page. Nothing in `schedule()`,
`update_status()`, or `reschedule()` ever calls it. This is the same
"never auto-advance" rule already established by `WorkflowService` itself
(`mark_applied()`'s docstring) and `SchedulerService` (`update_workflow_
statuses` only ever creates a workflow at `NEW_MATCH`, never advances one).

## New database tables

Migration `52ad230e88b2` (head; parent `161f6cd3b296`).

### `interview_records`

The main table — one row per scheduled interview.

| Column | Type | Notes |
|---|---|---|
| `user_id`, `employer_id` | FK, `ondelete="CASCADE"` | |
| `job_id` | FK jobs, `ondelete="SET NULL"`, nullable | optional — an interview can exist without a specific posted job |
| `application_workflow_id` | FK application_workflows, `ondelete="SET NULL"`, nullable | optional link enabling explicit workflow sync |
| `contact_id` | FK employer_contacts, `ondelete="SET NULL"`, nullable | the recruiter contact, if any |
| `interview_type`, `interview_stage`, `status` | String, indexed | plain strings — canonical enums live in `interviews.interview_models` |
| `scheduled_at` | DateTime(timezone=True), indexed | always normalized to naive UTC before storage (see below) |
| `duration_minutes`, `timezone`, `location`, `meeting_link` | nullable | |
| `interviewer_names` | JSON `list[str]`, nullable | |
| `reminder_offsets` | JSON `list[str]`, nullable | which `ReminderOffset`s to generate `InterviewReminder` rows for |
| `outcome`, `notes` | Text, nullable | quick-summary fields, distinct from the structured `InterviewNote` timeline |

### `interview_checklist_items`

Preparation checklist items — `interview_id` (FK, CASCADE), `label`,
`is_complete`. `InterviewService.schedule()` seeds exactly the 10 items the
milestone brief lists (Research employer, Review job description, Review
CV, Prepare STAR examples, Prepare questions, Prepare documents, Prepare
uniform, Test camera & microphone, Print directions, Travel plan); a
candidate can add/remove more.

### `interview_notes`

`interview_id` (FK, CASCADE), `category` (`NoteCategory.value`: questions
asked, my answers, recruiter feedback, things to improve, salary
discussed, next steps, general), `body`. Its own table, not a JSON blob —
matches this project's "own table for an append-only, independently
meaningful, timestamped log" convention (`WorkflowStatusHistoryRecord`,
`EmployerActivityLogEntry`).

### `interview_reminders`

`interview_id` (FK, CASCADE), `offset` (`ReminderOffset.value`: seven
days, three days, one day, two hours, thirty minutes before), `remind_at`,
`is_sent`. Directly mirrors `JobReminder`'s design from the Job Management
milestone.

## `scheduled_at` is always naive UTC

This codebase has hit real `TypeError`s before from mixing naive and
aware datetimes (see `utils.helpers.utc_now()`'s docstring). The HTML
form posts a timezone-*less* `datetime-local` value already, but the JSON
API accepts a `datetime` body field a client could send as
timezone-aware. `InterviewService._as_naive_utc()` normalizes every
incoming `scheduled_at`/`new_scheduled_at` once, at the point of
persistence, so every other computation (reminder math, "days from
application to interview," dashboard countdowns) can assume naive UTC
throughout without re-checking.

## Services created

```
src/job_automation/interviews/
├── interview_models.py               — InterviewType, InterviewStage, InterviewStatus,
│                                        ReminderOffset, NoteCategory, InterviewLifecycle
├── interview_repository.py           — InterviewRepository (pure data access)
├── interview_checklist_repository.py
├── interview_note_repository.py
├── interview_reminder_repository.py
└── interview_service.py              — InterviewService (the orchestrator)
```

`InterviewLifecycle` reuses the same *pattern* as `job_organization
.JobPipeline`/`workflows.application_workflow.ApplicationWorkflow` (a
frozen `ALLOWED_TRANSITIONS` dict + validate/raise static methods) — but
looser: `CANCELLED`/`MISSED` can interrupt from more than one state, and
`RESCHEDULED` is a deliberately transient waypoint back to `SCHEDULED`
(`InterviewService.reschedule()` validates against a separate
`RESCHEDULABLE_STATUSES` set, then jumps straight back to `SCHEDULED`,
rather than requiring two `validate_transition()` hops for what is
conceptually one action).

One `InterviewService`, not several, mirroring `EmployerCrmService`'s
"every entity here only changes via a candidate editing one thing" — every
mutation (schedule, reschedule, status update, checklist, notes,
reminders, workflow sync) belongs to managing one interview.

Publishes `INTERVIEW_STATUS_UPDATED` on scheduling and on every validated
status transition (never calls `NotificationService` directly). Checklist
toggles and note edits deliberately publish nothing — the same scope line
`JobOrganizationService` draws for flag toggles/detail edits.

### Reminders and the scheduler

A seventh scheduled task, `send_due_interview_reminders`
(interval: `settings.scheduler_interview_reminders_interval_seconds`,
default 5 minutes — deliberately shorter than the job-reminders task's 15
minutes, since interview reminders include a "30 minutes before" offset
that a 15-minute check interval could fire 15–40 minutes late for),
registered in `scheduler.task_registry.TASK_REGISTRY` exactly like the
existing six. `InterviewService.process_due_reminders()` publishes
`INTERVIEW_REMINDER_DUE` per due reminder and marks it sent — no real
email/SMS is ever sent, only an in-app `Notification` row (the only kind
this app has ever produced).

Rescheduling deletes and regenerates every *unsent* reminder from the new
date (already-sent reminders are historical and left alone) —
`InterviewReminderRepository.delete_unsent_for_interview()`.

## Analytics

`AnalyticsService` (existing, extended — no separate
`InterviewAnalyticsService` was created) gained:

- **`interview_analytics_summary(user_id)`** — account-wide: scheduled/
  completed/cancelled counts, offer conversion rate (offers ÷ completed),
  interview success rate (completed ÷ (completed + cancelled) — did
  booked interviews actually happen, distinct from whether they led to an
  offer), average days from application to interview, average interviews
  before an offer, and the employer with the most offers.
- **`employer_interview_stats(user_id, employer_id)`** — the same shape,
  scoped to one employer, plus "average interviews per application" (how
  many interview rounds a typical application at this employer takes).

Both are built from the real `InterviewRecord` table — a different,
richer data source than `EmployerOutcomeSummary.interviews` (which counts
*workflow-history transitions to "interview,"* established in the
Employer CRM milestone before `InterviewRecord` existed). Deliberately not
unified: `EmployerOutcomeSummary` stays exactly what it already was, and
every existing test asserting it keeps passing.

## Screens implemented

- **`/interviews`** — list with a status filter, each row showing
  employer, type/stage, scheduled time, and a status badge.
- **`/interviews/new`** — the schedule form: employer (dropdown), optional
  recruiter contact (scoped to the chosen employer when arriving from a
  contextual link), interview type/stage, date/time, duration, timezone,
  meeting link, location, interviewer names, reminder checkboxes, notes.
  Accepts optional `employer_id`/`job_id`/`application_workflow_id` query
  parameters so "Schedule interview" links from an employer profile, a job
  detail page, or an application row pre-fill the form.
- **`/interviews/{id}`** — full detail: info card, status-move buttons
  (only the statuses `InterviewLifecycle.allowed_next_statuses()` actually
  permits), a reschedule form, the explicit workflow-sync buttons (only
  shown when `application_workflow_id` is set), the preparation checklist
  with a live completion percentage, the categorized notes timeline, and
  the reminders list.
- **`/calendar`** — one route, three views (`?view=month|week|day`),
  colour-coded by status (reusing `components/status_badge.html`), every
  event linking straight to its interview detail page.
- **`/dashboard`** — a new "Interviews" section: interviews this week,
  next-interview countdown, preparation completion %, offer conversion
  rate, an upcoming-interviews list, and a recent-outcomes list.
- **`/employers/{id}`** — a new "Interview history" section: every
  interview with this employer, plus total/completed/cancelled/offer
  counts and average interviews per application. Recruiter interactions
  were already covered by the Employer CRM milestone's activity timeline —
  not duplicated here.
- **`/applications`** — a new "Interview" column: the real, live status of
  the linked `InterviewRecord` if one exists (distinct from the
  pre-existing "Interview date" column, which is a workflow-history proxy
  that predates real interview scheduling), or a "Schedule" link
  pre-filled with this application's employer/job/workflow if not.
- **`api/interviews_api.py`** (new) — JSON equivalents of every
  mutation/read above, under `/api/interviews`.

## Testing

- **`tests/test_interviews.py`** (26 tests) — the `InterviewLifecycle`
  state machine; scheduling (default checklist seeding, reminder
  generation, notification publishing, naive-UTC normalization of an
  aware input); status transitions (valid, invalid, ownership, outcome
  text); rescheduling (reminder regeneration, already-sent reminders kept,
  rejection from a terminal status); checklist/notes CRUD; due-reminder
  processing (only past-due reminders fire); explicit workflow sync
  (`mark_interview`/requires a linked workflow/never happens
  automatically — a dedicated regression test); the dashboard's average
  preparation-completion helper; and analytics (counts/rates, employer
  stats, average days to interview, average interviews before offer).
- **`tests/test_interviews_web.py`** (20 tests) — every HTML route
  (schedule form with pre-fill, detail page, status/reschedule, checklist/
  notes) and JSON API endpoint via a real `TestClient`; all three calendar
  views; the dashboard's Interview widgets section; the employer profile
  page's interview history section; and the applications page's
  "Schedule" link → schedule → explicit workflow-sync → status reflected
  back on the applications page, end to end.

Full suite: 357 tests (was 311), zero regressions.

Manually verified end-to-end in a live browser session: scheduled a video
interview for Riverside NHS Foundation Trust, confirmed the 10-item
checklist and two default reminders were created, toggled a checklist item
and watched the completion percentage update, moved the interview through
Scheduled → Upcoming, saw it appear correctly on the month and day
calendar views and on the dashboard's "Upcoming interviews"/countdown
widgets, saw it appear on the employer's profile page's new interview
history section, scheduled a second interview linked to a real
application via the Applications page's "Schedule" link, and confirmed
clicking "Mark application: Interview" updated that application's real
workflow status — all changes requiring an explicit click, none automatic.

## Known limitations

- **No Outlook/Google Calendar sync** — `/calendar` is this application's
  own view only; nothing pushes to or pulls from an external calendar
  (explicitly out of scope).
- **No automatic interview scheduling** — every interview is created by a
  candidate filling in the schedule form (or the equivalent API call);
  nothing in this codebase infers or auto-books an interview time.
- **Checklist/note edits publish no notifications** — only scheduling and
  status transitions do, the same scope line drawn for Job Management's
  flag toggles.
- **The recruiter-contact dropdown on `/interviews/new` is only populated
  when `employer_id` is already known** (arriving via a contextual link).
  A blank-slate visit to `/interviews/new` shows every employer but no
  contacts until one is chosen — a full dynamic dropdown would need
  client-side JS this milestone didn't add.
- **`interview_stage` has no validation against `interview_type`** — a
  candidate can freely record a "Phone" type interview at "Final Round"
  stage; the two fields are intentionally independent (see the
  architecture decision above), so nothing rejects unusual-but-valid
  combinations.
