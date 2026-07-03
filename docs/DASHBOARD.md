# Web Dashboard

`src/job_automation/web/` is a FastAPI + Jinja2 + HTMX + Bootstrap 5
dashboard that lets a candidate browse matched jobs, review AI-generated
documents, track application workflows, and manage their profile through a
browser instead of the CLI/scripts used by every prior milestone.

**This package contains no business logic of its own.** Every route calls
into services and repositories built in earlier milestones —
`MatchingService`, `WorkflowService`, `DocumentService`, `ProfileService`,
`AnalyticsService`, `JobRepository`, `JobMatchRepository`,
`DocumentRepository`, `WorkflowRepository` — and nothing here duplicates
their rules. The two additions that *are* new logic (`AnalyticsService` and
`JobRepository.search()`) are called out explicitly below, since "reuse
everything" doesn't mean "add nothing."

**No automatic application submission**, same as every prior milestone: the
dashboard exposes `WorkflowService.mark_applied()`/etc. as explicit human
actions (a button click, a form submit) — nothing in `web/` calls them on a
schedule or in response to anything but a request a person made.

**Authentication.** As of the Authentication and User Accounts milestone,
this is a real multi-user system: every table was already keyed by
`user_id` (from the very first database milestone), and `get_current_user()`
now resolves that id from a signed session cookie instead of "the first
`User` row in the database." See [docs/AUTHENTICATION.md](AUTHENTICATION.md)
for the full design — registration, login, logout, session cookies,
protected routes, and per-user data isolation.

## Architecture

```
src/job_automation/web/
├── app.py                    — FastAPI app factory, shared dependencies, router registration
├── routes/                   — HTML page routes (Jinja2 templates, HTML form POSTs)
│   ├── dashboard.py             /dashboard
│   ├── jobs.py                  /jobs, /jobs/{id}
│   ├── matches.py                /matches
│   ├── documents.py              /documents, /documents/{id}, POST regenerate
│   ├── workflow.py               /workflow, /workflow/{id}
│   ├── applications.py           /applications
│   ├── candidate.py              /candidate, POST personal-information/visa-status
│   ├── analytics.py              /analytics (shell page; data loaded client-side)
│   └── settings.py               /settings, POST preferences
├── api/                       — JSON REST API (HTMX targets + programmatic access)
│   ├── dashboard_api.py          /api/dashboard/summary, /analytics, /candidate-profile
│   ├── jobs_api.py               /api/jobs, /api/jobs/{id}, /api/jobs/matches/all
│   ├── documents_api.py          /api/documents, approve/reject/export
│   └── workflow_api.py           /api/workflow, transitions, /api/workflow/applications
├── templates/                 — Jinja2 templates, one per page, extending base.html
│   └── components/               — reusable partials (navbar, cards, badges, timeline)
├── css/styles.css             — custom styles layered on Bootstrap 5 (CDN)
└── js/                         — dashboard.js (theme toggle), tables.js (sort), charts.js (Chart.js)
```

### Why 4 API files cover 8 conceptual resource areas

The milestone brief named 4 specific files (`dashboard_api.py`,
`jobs_api.py`, `documents_api.py`, `workflow_api.py`) but 8 conceptual
areas (dashboard, jobs, matches, documents, workflow, applications,
candidate, analytics). Rather than inventing extra files not on the list,
each conceptual area was placed with the resource it's inherently
scoped to:

- **Matches** live in `jobs_api.py` — a `JobMatch` is meaningless without
  the `Job` it scores, so `GET /api/jobs/matches/all` sits next to
  `GET /api/jobs`.
- **Applications** live in `workflow_api.py` — see below, an "application"
  *is* a workflow at a later stage, not a separate resource.
- **Candidate** profile reads live in `dashboard_api.py`
  (`/api/dashboard/candidate-profile`) since the only current JSON
  consumer is the dashboard summary view; profile *writes* go through the
  HTML form routes in `routes/candidate.py` and `routes/settings.py`
  instead (see "Why preference editing lives only in Settings" below).
- **Analytics** has no separate API file — `dashboard_api.py`'s
  `/api/dashboard/analytics` serves `js/charts.js` directly.

This was 4 files for this milestone's 8 areas specifically. The
Background Job Scheduler milestone later added a genuinely new 9th
conceptual area with its own dedicated `scheduler_api.py` — a background
task registry/run-history isn't a natural fit for any of the 4 existing
files the way matches/applications/analytics were, so a 5th file was the
right call there rather than forcing it into one of these. See
docs/BACKGROUND_SCHEDULER.md.

## Why there's no `ApplicationRepository`

"Application" isn't a distinct database table or business object in this
system. An `ApplicationWorkflowRecord` that has reached `READY_TO_APPLY` (or
later) *is* an application — the workflow subsystem built in the previous
milestone already tracks everything an applications view needs (status,
interview date, offer, notes), all derived from `WorkflowStatusHistoryRecord`
rather than duplicated into new columns. `routes/applications.py` defines
`build_application_rows(repository, user_id)` as the single place this
derivation happens (interview date = the `to_status == "interview"` history
entry's timestamp, `has_offer` = any `to_status == "offer"` entry exists,
latest note = the most recent history entry with a note); `api/workflow_api.py`'s
`GET /api/workflow/applications` imports and reuses that exact function
rather than re-deriving the same facts a second way.

## `AnalyticsService` — genuinely new backend logic

No analytics capability existed anywhere in this codebase before this
milestone, so `job_automation.analytics` (`analytics_models.py`,
`analytics_service.py`) is new, not reused. It queries `Job`, `Employer`,
`JobMatch`, `ApplicationWorkflowRecord`, `WorkflowStatusHistoryRecord`,
`WorkflowAuditLogRecord`, and `GeneratedDocumentRecord` directly (there was
no existing repository covering several of these query shapes — e.g. "top
employers by job count" or "match score distribution buckets" — and adding
one-off repository methods used by nothing but a single dashboard view
would have been over-engineering for this milestone). `AnalyticsService`
returns plain dataclasses (`DashboardSummary`, `ActivityItem`,
`UpcomingDeadline`, `AnalyticsReport`, etc.) — FastAPI's `jsonable_encoder`
serializes these natively, so `dashboard_api.py`'s endpoints return them
directly with no extra schema layer.

## `JobRepository.search()` — additive extension, not a rewrite

The Jobs page needs filtering/sorting the original repository didn't
support. Rather than modify any existing method, `JobFilter` (a frozen
dataclass: search, location, min_salary, band, employer_name,
visa_sponsorship, employment_type, sort_by, sort_descending) and
`JobRepository.search(filters)` were added purely additively — every
pre-existing method on `JobRepository` is untouched, verified by the full
existing test suite still passing unmodified.

## Why the Candidate Profile page and Settings page split preference editing

`CandidatePreferences` has 9 fields. The original brief described both an
editable Candidate Profile page *and* a Settings page with preferences —
if both pages had their own preferences form, submitting one would silently
clobber whatever fields the *other* form's HTML didn't include (a classic
partial-form-overwrite bug). Instead:

- **Settings** (`routes/settings.py`) is the single, complete editor for
  all 9 `CandidatePreferences` fields.
- **Candidate Profile** (`routes/candidate.py` / `candidate_profile.html`)
  only *displays* preferences read-only, with a link to Settings, and owns
  editing for personal information and visa status instead (fields Settings
  doesn't touch).

This means no two forms in the dashboard ever submit overlapping subsets of
the same underlying dataclass.

## Known limitation: no notification preferences

The original milestone brief mentioned "notification preferences" as part
of Settings. This isn't implemented — there is no notification-sending
capability (email, SMS, push, etc.) anywhere in this codebase, so a toggle
controlling one would control nothing. Left out rather than faked with a
non-functional checkbox; a real implementation is a future extension point
(see below), not an oversight.

## The `_NeverCalledLLMProvider` pattern

`DocumentService`'s constructor requires an `LLMProvider` — but only its
`generate_*()` methods actually call one; `approve()`, `reject()`, and
`export()` never do. `routes/documents.py`'s `regenerate_document` and
`generate_document` routes (the latter added in the Anthropic AI
Integration milestone, for first-time generation rather than
regenerating an existing draft) correctly require a real, configured
provider (via `get_llm_provider()` — moved to `web/app.py` that same
milestone, alongside `get_ai_response_cache()`/`get_match_cache()`, so
every AI route across `routes/documents.py`, `routes/matches.py`, and
`routes/interviews.py` shares one implementation — which raises a clear
`503` if `ANTHROPIC_API_KEY` isn't set — regeneration genuinely can't work
without one). See [docs/ANTHROPIC_INTEGRATION.md](ANTHROPIC_INTEGRATION.md)
for the full design. But `api/documents_api.py`'s
approve/reject/export routes construct a `DocumentService` too, purely to
reach its `approve()`/`reject()`/`export()` methods, and requiring a
working Anthropic API key just to approve a document that's already been
generated would be a real functional bug — those actions would break in
any environment without one configured (including this one), for no
reason connected to what they actually do.

The fix: `api/documents_api.py` defines `_NeverCalledLLMProvider(LLMProvider)`,
whose `complete()` raises `AssertionError` if it's ever actually invoked,
and passes an instance of it into `DocumentService` for approve/reject/export
only. `test_approve_document_via_api_never_requires_llm_provider` in
`tests/test_web_dashboard.py` is a regression test for exactly this: it
approves a document *without* overriding the real `get_llm_provider`
dependency with a fake, proving approval succeeds even with no LLM
configured at all.

## Workflow transitions: JSON API exists, current UI is read-only

`api/workflow_api.py` maps HTTP actions directly onto `WorkflowService`'s
existing named methods (`submit_for_review`, `approve`, `reject`,
`mark_ready_to_apply`, `mark_applied`, `mark_interview`, `mark_offer`,
`close`) via `POST /api/workflow/{id}/transition` (form field `action`,
optional `note`). It adds no new business rules — `InvalidTransitionError`
from the existing state machine (`workflows/application_workflow.py`) is
caught and returned as a `400` with the same message the state machine
already produces. The Workflow page itself (`workflow.html`) is read-only —
a pipeline strip plus status-history timeline, matching the milestone's
page spec — so these endpoints are exercised directly by
`tests/test_web_dashboard.py` and are ready for a future UI (or another
client) to call, the same way a REST API commonly exposes more than the
current UI wires buttons for.

## Pages

| Page | Route | Notes |
|---|---|---|
| Dashboard | `/dashboard` | Summary stats, recent activity, upcoming deadlines, AI status card — all from `AnalyticsService`. |
| Jobs | `/jobs`, `/jobs/{id}` | HTMX filter form (search, location, employer, band, salary, visa, sort) backed by `JobRepository.search()`. |
| Matches | `/matches` | Score, per-category breakdown, strengths/weaknesses/recommended actions from `JobMatch.analysis`. |
| Documents | `/documents`, `/documents/{id}` | List with status filter tabs; review page with approve/reject/export/regenerate. |
| Workflow | `/workflow`, `/workflow/{id}` | List + visual pipeline/timeline for one workflow. |
| Applications | `/applications` | Workflows at `READY_TO_APPLY` or later, with derived interview date/offer/notes. |
| Candidate Profile | `/candidate` | Editable personal info + visa status; read-only preferences/employment/education/certificates. |
| Analytics | `/analytics` | Chart.js charts + rate stat cards, loaded client-side from `/api/dashboard/analytics`. |
| Settings | `/settings` | Full preferences editor; read-only AI/theme settings. |
| Scheduler | `/scheduler` | Added in the Background Job Scheduler milestone — registered tasks, last-run status, "Run now" buttons, run history. See [docs/BACKGROUND_SCHEDULER.md](BACKGROUND_SCHEDULER.md). |
| Notifications | `/notifications` | Added in the Notification & Event System milestone — every notification visible to the user, mark read/mark all read, navbar bell with a polling unread badge. See [docs/NOTIFICATIONS.md](NOTIFICATIONS.md). |
| Board | `/board` | Added in the Job Management milestone — Kanban view of `SavedJob.pipeline_stage`. |
| Employers | `/employers`, `/employers/{id}` | Added in the Employer & Application CRM milestone — search/filter list and a full CRM profile page. See [docs/EMPLOYER_CRM.md](EMPLOYER_CRM.md). |
| Interviews | `/interviews`, `/interviews/{id}`, `/interviews/new` | Added in the Interview & Calendar Management milestone — schedule/reschedule, status lifecycle, preparation checklist, categorized notes, reminders, explicit application-workflow sync. See [docs/INTERVIEWS.md](INTERVIEWS.md). |
| Calendar | `/calendar` | Added in the Interview & Calendar Management milestone — month/week/day views of scheduled interviews, colour-coded by status, one route switched by a `view` query parameter. See [docs/INTERVIEWS.md](INTERVIEWS.md). |

`/dashboard` itself gained a "Job organization" section (Job Management
milestone), an "Employer CRM" section (Employer & Application CRM
milestone), an "Interviews" section (Interview & Calendar Management
milestone: interviews this week, next-interview countdown, preparation
completion %, offer conversion rate, upcoming interviews, recent interview
outcomes), and an "AI status" card (Anthropic AI Integration milestone:
configured/not, model, and the percentage of the user's job matches
actually scored by a real LLM call — see
[docs/ANTHROPIC_INTEGRATION.md](ANTHROPIC_INTEGRATION.md)) — each a
genuinely additive block on the same page, not a rewrite of the original
stats row/recent-activity/upcoming-deadlines layout described below.

## Testing

`tests/test_web_dashboard.py` — 33 tests, using FastAPI's `TestClient`
against an in-memory SQLite database (never `data/jobs.db`) and a
`FakeLLMProvider` override (never a real Anthropic call):

- Every one of the 9 page routes renders `200` with real seeded data, and
  `/dashboard` fails clearly with `500` when no `User` exists yet (no
  silent empty-state masking a misconfigured environment).
- Job detail 404s for an unknown ID; job list/API filtering by search term
  and minimum salary.
- Matches API returns score and full analysis breakdown.
- Candidate personal-information and Settings preference edits persist
  correctly through `ProfileService`.
- Document generation, approve, reject, and export all work — including
  the `_NeverCalledLLMProvider` regression test described above — and
  approving/rejecting another user's document 404s.
- Regenerating a document redirects and uses the overridden
  `FakeLLMProvider`, never a real API call.
- Workflow transition API accepts valid transitions, rejects invalid ones
  and unknown action names with `400`, and workflow list/detail endpoints
  return status history.
- Applications API only includes workflows at `READY_TO_APPLY` or later —
  a fresh `NEW_MATCH` workflow doesn't show up until it reaches that stage.

Note: the in-memory `db_session` fixture this file uses is *not* the same
object as `tests/conftest.py`'s — `TestClient` runs request dependencies in
a worker thread, and plain in-memory SQLite connections are single-thread
only. This file defines its own `db_session` fixture using `StaticPool` +
`check_same_thread=False` to share one connection safely across threads;
every other test file is unaffected.

Verified manually end-to-end with `scripts/seed_demo_data.py` (realistic
candidate, 3 employers, 5 jobs, 5 AI matches spanning a score range, and 4
workflows at different stages) and a real `uvicorn` server: every page and
API endpoint above was hit with real HTTP requests, including approve/
reject/export and a workflow transition, confirming the dependency-override
pattern isn't the only thing making tests pass.

## Extension points

- **Authentication** — implemented in the following milestone; see
  [docs/AUTHENTICATION.md](AUTHENTICATION.md).
- **Deployment** — explicitly out of scope ("do not begin deployment").
  No Dockerfile, no production ASGI server config, no HTTPS/reverse-proxy
  setup was added.
- **Automatic application submission** — explicitly out of scope, same as
  every prior milestone; the dashboard only exposes existing
  human-triggered actions.
- **Notification preferences** — see "Known limitation" above; would need
  an actual notification-sending subsystem first.
- **Wiring workflow transition buttons into `workflow.html`** — the JSON
  API is ready (see above); adding buttons is a template-only change once
  there's a concrete need for triggering transitions from the browser
  rather than another client.
