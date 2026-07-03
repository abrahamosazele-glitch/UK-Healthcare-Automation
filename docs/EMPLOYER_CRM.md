# Employer & Application CRM

`job_automation.employer_crm` turns `Employer` from a plain reference
table (name/website/contact details) into a full personal CRM: favourite
employers, research their NHS Trust/department/location structure, build a
recruiter contact book, log notes and communication history, and see
success-rate analytics (applications sent, interviews, offers, rejections)
per employer.

**No Anthropic API, no live scraping, no deployment, no automatic
application submission, no real email/SMS.** Every constraint carried
over from prior milestones. CRM mutations (favouriting, notes, contacts)
deliberately publish no notifications — see "Known limitations."

## Architecture decision: where "rejections" data comes from

This mirrors the Job Management milestone's central decision and is worth
stating explicitly, since it would be an easy correctness bug to get
wrong. The CRM's success-rate analytics need four numbers per employer:
applications sent, interviews, offers, and rejections. Three of them have
one obvious source; the fourth does not.

- **Applications sent / interviews / offers** come from
  `WorkflowStatusHistoryRecord` — the same authoritative source
  `AnalyticsService.dashboard_summary()`/`build_report()` already use for
  these counts account-wide. A transition to `WorkflowStatus.APPLIED`/
  `INTERVIEW`/`OFFER` genuinely means what it says: the candidate applied,
  was interviewed, or got an offer.
- **Rejections** deliberately do **not** come from
  `WorkflowStatus.REJECTED`. As documented in `job_organization_models.py`
  (Job Management milestone), that value means "a human reviewer rejected
  the AI-drafted document — loop back to `DOCUMENTS_GENERATED` and
  regenerate." It is non-terminal, mid-pipeline, and has nothing to do
  with the employer. Using it here would silently miscount document-review
  churn as employer rejections — a real correctness bug, not a cosmetic
  one. Instead, rejections come from `SavedJob.pipeline_stage ==
  PipelineStage.REJECTED` — the *only* place this schema actually records
  "the employer rejected this candidate's application" (a terminal state
  in the personal Kanban board). `AnalyticsService.list_employer_outcome_
  summaries()` queries both sources and combines them per employer;
  `test_employer_outcome_rejections_come_from_saved_job_not_workflow_status`
  (`tests/test_employer_crm.py`) proves a `WorkflowStatus.REJECTED` event
  contributes zero to an employer's rejection count while a
  `PipelineStage.REJECTED` move contributes exactly one.

Everything else in this milestone reuses existing architecture directly:
the repository/service pattern, `AnalyticsService` (extended, not
duplicated — no separate `EmployerAnalyticsService` was created), and the
existing `JobFilter`/`JobRepository.search()` extension pattern applied
again to `EmployerFilter`/`EmployerRepository.search()`.

## New database tables

Migration `161f6cd3b296` (head; parent `e9c269d9e55f`). `Employer` also
gained a new `employer_type` column (plain `String`, canonical
`EmployerType` enum in `employer_crm.employer_crm_models`).

### `employer_profiles`

One row per `(employer_id, user_id)` — a candidate's personal relationship
with an employer. The employer-level counterpart to `SavedJob`.

| Column | Type | Notes |
|---|---|---|
| `employer_id`, `user_id` | FK, `ondelete="CASCADE"` | unique together |
| `is_favourite` | Boolean, indexed | |
| `visa_sponsorship_notes` | Text, nullable | free-form candidate research |

### `employer_departments`

Shared reference data (**not** user-scoped) — an NHS Trust's departmental
structure is a fact about the organization, not about any one candidate.

| Column | Type | Notes |
|---|---|---|
| `employer_id` | FK, `ondelete="CASCADE"` | |
| `name` | String(255), not null | e.g. "Emergency Department" |
| `location` | String(255), nullable | e.g. "London - Main Hospital Site" |

### `employer_contacts`

One candidate's personal recruiter contact book — **not** a shared
directory. Two candidates applying to the same Trust build independent
contact lists.

| Column | Type | Notes |
|---|---|---|
| `employer_id`, `user_id` | FK, `ondelete="CASCADE"` | |
| `department_id` | FK, `ondelete="SET NULL"`, nullable | optional link to a department |
| `name` | String(255), not null | |
| `role`, `email`, `phone`, `notes` | nullable | |

### `employer_activity_log`

One candidate's combined notes + communication-history timeline for an
employer. A single table with an `entry_type` discriminator
(`"note"` \| `"communication"`) rather than two separate tables — both are
timestamped, append-only, user-authored entries about the same
relationship, and a combined chronological timeline is also the natural
CRM profile-page UI (matches why `JobReminder` got its own table instead
of a JSON blob: independently meaningful, timestamped entries, but here
two *kinds* of entry share one timeline rather than needing two).

| Column | Type | Notes |
|---|---|---|
| `employer_id`, `user_id` | FK, `ondelete="CASCADE"` | |
| `contact_id` | FK, `ondelete="SET NULL"`, nullable | who the communication was with |
| `entry_type` | String(20), indexed | `"note"` or `"communication"` |
| `channel` | String(20), nullable | only set for communications |
| `body` | Text, not null | |
| `occurred_at` | DateTime(timezone=True), indexed | when it happened, not just when it was logged — communications can be backdated |

## Services created

```
src/job_automation/employer_crm/
├── employer_crm_models.py          — EmployerType, ActivityEntryType, CommunicationChannel
├── employer_profile_repository.py  — EmployerProfileRepository (pure data access)
├── employer_department_repository.py
├── employer_contact_repository.py
├── employer_activity_repository.py
└── employer_crm_service.py         — EmployerCrmService (the orchestrator)
```

One `EmployerCrmService` (not four separate services) wraps all four
repositories: favourite/unfavourite, `update_visa_notes()`, department
add/list/remove, contact add/list/remove (ownership-checked), and activity
add_note/add_communication/list/remove (ownership-checked). Unlike Job
Management's `JobOrganizationService`/`ReminderService` split (justified
there because reminders have an independent scheduler-driven lifecycle),
every CRM entity here only ever changes in response to a candidate viewing
and editing an employer's profile page — one cohesive use case, so one
service.

`AnalyticsService` (existing, not duplicated) gained
`employer_outcome_summary(user_id, employer_id)` and
`list_employer_outcome_summaries(user_id)`, plus a new
`EmployerOutcomeSummary` dataclass in `analytics_models.py`.
`EmployerRepository` (existing) gained `get()`, `list_all()`, and
`search()`/`EmployerFilter`, following the exact `JobFilter`/
`JobRepository.search()` pattern from the Job Management milestone: an
optional `user_id` triggers a `LEFT OUTER JOIN` onto `employer_profiles`
so `favourite_only` can be applied, and every pre-existing method/behavior
is untouched.

## Screens implemented

- **`/employers`** — search (by name) and filter (by employer type,
  favourite-only) list page, sortable by name or recently-added, each row
  showing a favourite star and job count.
- **`/employers/{id}`** — the full profile page: employer info, a
  favourite button, success-rate analytics (applications sent/interviews/
  offers/rejections plus interview/offer rate percentages), a visa
  sponsorship notes editor, departments (add/remove), recruiter contacts
  (add/remove, with an optional department link), and a combined
  notes+communications activity timeline (add note, log a communication
  with channel/contact/backdated timestamp, delete either).
- **`/dashboard`** — a new "Employer CRM" section: employers-tracked and
  favourite-employer stat cards, and a "top employers by applications
  sent" table reusing `list_employer_outcome_summaries()` as-is (already
  sorted by application volume).
- **`api/employers_api.py`** (new) — JSON equivalents of every
  mutation/read above, under `/api/employers`.

No "create employer" UI: `Employer` rows are reference data populated by
the job-ingestion pipeline (`EmployerRepository.get_or_create()`), the
same reasoning `/jobs` never grew a "create job" form.

## Testing

- **`tests/test_employer_crm.py`** (19 tests) — favourite/unfavourite
  round-trip and idempotency; visa notes set/clear; departments add/list/
  remove and remove-nonexistent error; contacts add/list/remove with
  per-user isolation and ownership enforcement; the combined activity
  timeline (notes vs. communications, filtering, backdating, ownership
  enforcement); `EmployerRepository.search()` (name, type, favourite-only,
  sorting, and a regression proving the join is opt-in); and
  `AnalyticsService`'s employer outcome analytics including the
  rejections-source regression test and multi-employer sort-by-volume
  ordering.
- **`tests/test_employer_crm_web.py`** (19 tests) — every HTML route
  (list/search, profile page, flag, visa notes, departments, contacts,
  notes/communications) and JSON API endpoint via a real `TestClient`; the
  dashboard's new CRM section; 404s for unknown employers; 400s for
  unknown flag actions/communication channels; 404 on double-delete.

Full suite: 311 tests (was 273), zero regressions.

Manually verified end-to-end in a live browser session: favouriting
"Riverside NHS Foundation Trust" from its profile page, adding an
"Emergency Department", adding a recruiter contact linked to that
department, logging both a note and a phone communication (confirming
both appear correctly badged in the combined timeline with correct
timestamps), and confirming the profile page's analytics widgets and the
dashboard's "Employer CRM" table both showed the same real numbers (1
application, 1 interview, 100% interview rate) computed from an earlier
test workflow.

## Known limitations

- **No "create employer" UI** — employers arrive via the job-ingestion
  pipeline only (see above).
- **CRM mutations publish no notifications.** Unlike Job Management's
  pipeline transitions ("every transition must create a notification" was
  explicit there), nothing in this milestone's spec asks for CRM actions
  to notify anyone. Favouriting an employer or logging a call is passive
  record-keeping, not a pipeline event — added notifications here would be
  speculative, not requested.
- **Departments are shared, not per-candidate** — if one candidate adds
  "Emergency Department" for a Trust, every other candidate sees it too
  (matches how the Trust's actual departmental structure is a fact about
  the organization). Contacts and activity log entries, by contrast, are
  strictly per-candidate.
- **No recurring/scheduled reminders tied to CRM activity** — logging a
  communication doesn't offer to also set a `JobReminder`; the two systems
  aren't cross-wired in this milestone.
- **Employer type is free-text-backed, not validated against NHS ODS
  data** — `employer_type` is one of a small enum (`nhs_trust`,
  `care_home`, `agency`, `recruitment_agency`, `other`) set manually or by
  the ingestion pipeline; there's no lookup against an authoritative NHS
  Trust registry (that would require live external data access, out of
  scope per this milestone's constraints).
