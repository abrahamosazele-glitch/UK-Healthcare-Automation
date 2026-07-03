# Background Job Scheduler

`src/job_automation/scheduler/` runs a fixed set of 5 safe, internal
automation tasks on a schedule (via APScheduler) or on demand (the
dashboard's "Run now" buttons), recording full status history for every
run.

**No Anthropic API key, no notifications, no deployment, no automatic
application submission, no live scraping.** Every task operates only on
local/fixture data and a fake LLM provider — all explicitly out of scope
this milestone, same as every constraint on prior milestones.

## Architecture

```
src/job_automation/scheduler/
├── scheduler_models.py     — TaskStatus, TaskDefinition, TaskRunSummary, utc_now()
├── scheduler_repository.py — SchedulerRepository (persists SchedulerTaskRunRecord rows)
├── task_registry.py        — TASK_REGISTRY: the 5 concrete tasks, by name
├── scheduler_service.py    — SchedulerService (locking, retries, history — the orchestrator)
├── fake_llm_provider.py    — SchedulerFakeLLMProvider (never AnthropicProvider)
├── job_scheduler.py        — APScheduler BackgroundScheduler bootstrap
└── tasks/
    ├── import_fixture_jobs.py
    ├── run_ai_matching.py
    ├── generate_draft_documents.py
    ├── update_workflow_statuses.py
    └── cleanup_old_logs.py
```

```
web/routes/scheduler.py  (HTML page, "Run now" form)  ─┐
web/api/scheduler_api.py (JSON, programmatic)          ├──> SchedulerService.run_task(name, triggered_by)
job_scheduler.py's periodic APScheduler trigger        ─┘        │
                                                                   ├──> per-task threading.Lock (skip if held)
                                                                   ├──> core.RetryManager.execute(task_func, retry_on=(Exception,))
                                                                   └──> SchedulerRepository (create/mark_success/mark_failed)
```

All three callers (manual HTML button, manual JSON API, periodic trigger)
go through the **same `SchedulerService` instance** — see "One shared
service instance" below — so a scheduled fire and a manual click can never
race into running the same task twice, and every run, however triggered,
lands in the same history table.

### Supersedes the original scaffold-era placeholder

The very first project milestone scaffolded `scheduler/job_scheduler.py`
with a docstring describing "an APScheduler-based daily job runner" for
"the full scrape -> apply -> report pipeline." That vague, pre-everything
design is now replaced with something concrete and safe: 5 specific,
named, local-data-only tasks, not a live pipeline runner — live scraping
and automatic application submission are both explicitly out of scope for
this and every other milestone, so the scaffold's original framing could
never have been implemented as originally described anyway.

## The 5 scheduled tasks

| Task | What it does | Reuses |
|---|---|---|
| `import_fixture_jobs` | Reads `data/fixtures/local_jobs.json` (committed, synthetic — never a live site) and ingests each row via `JobIngestionService.save_parsed_job()`. | `JobIngestionService`, `ParsedJob` (from the NHS scraper milestone) |
| `run_ai_matching` | Evaluates every active job against every active user's saved candidate profile, using `SchedulerFakeLLMProvider`. | `MatchingEngine`, `MatchingService` |
| `generate_draft_documents` | Drafts a supporting statement for matches scoring ≥ `scheduler_document_score_threshold` that don't already have one, and attaches it to a workflow. | `DocumentService`, `WorkflowService` |
| `update_workflow_statuses` | Ensures every `JobMatch` has a corresponding workflow record at `NEW_MATCH` — never drives any further transition. | `WorkflowService.start_workflow()` (idempotent) |
| `cleanup_old_logs` | Deletes finished `SchedulerTaskRunRecord` rows older than `scheduler_log_retention_days`. | `SchedulerRepository` |

### Why `import_fixture_jobs` reads a JSON file, not the NHS fixture HTTP server

The NHS Jobs scraper milestone already built and thoroughly tested
fixture-based scraping (`tests/fixtures/nhs/` + a local HTTP server +
Playwright). Re-running that full mechanism — launching a browser process
— on every scheduled fire (every 60 minutes by default, or whenever "Run
now" is clicked) would be slow and resource-heavy for what this milestone
actually needs to prove: that scheduled ingestion into the existing
pipeline works. `data/fixtures/local_jobs.json` is a small, committed,
synthetic dataset (4 healthcare listings, modeled on the same fields
`ParsedJob` already exposes) that this task reads and converts to
`ParsedJob`s directly — reusing `JobIngestionService` exactly as-is, the
same class every real scraper (NHS, and any future site) already uses.

### The `SchedulerFakeLLMProvider` has to answer two different callers

`MatchingEngine` and the document generators both only know about
`LLMProvider.complete()` — but they expect very different things back:

- Matching's `build_system_prompt()` asks for a `category_scores` JSON
  analysis. Returning plain prose here would make `parse_response()`
  raise `LLMResponseError`, which `MatchingEngine` already catches by
  silently falling back to rule-only scoring (`used_llm=False`). That
  would technically work, but "run AI matching using FakeLLMProvider"
  should genuinely exercise the LLM-blended scoring path, not silently
  skip it every time — so `SchedulerFakeLLMProvider.complete()` returns a
  valid, clearly-placeholder JSON analysis when it detects a matching
  prompt (checking for the literal string `"category_scores"`, which
  `build_system_prompt()` always includes).
- Document generation just wants prose to embed in a draft — returning
  obviously-fake, clearly-labeled text so nobody mistakes
  scheduler-generated content for real AI writing.

Verified directly: `run_ai_matching`'s test asserts
`match.analysis["used_llm"] is True`.

### `generate_draft_documents` and `update_workflow_statuses`: the "never auto-approve" boundary

This is the most safety-sensitive part of this milestone, since a
background task with no human present is exactly the kind of thing that
could accidentally cross into "automatic application submission" if not
deliberately constrained:

- `generate_draft_documents` calls `DocumentService.generate_supporting_statement()`
  **unchanged** — every document it creates is a `DRAFT`/`NEEDS_REVIEW`
  row awaiting the same explicit `approve()`/`reject()` a human-requested
  document requires. Attaching it to a workflow only ever advances
  `NEW_MATCH`/`REJECTED` -> `DOCUMENTS_GENERATED`, the one transition the
  Application Workflow subsystem already treats as automatic.
- `update_workflow_statuses` **only ever creates a workflow at `NEW_MATCH`**
  (via `WorkflowService.start_workflow()`, idempotent per user/job — safe
  to run repeatedly). It never calls `submit_for_review()`, `approve()`,
  `reject()`, `mark_ready_to_apply()`, `mark_applied()`, or `close()` —
  every one of those is a human decision, full stop.

`test_generate_draft_documents_never_auto_approves` is a direct regression
test for this: it asserts a scheduler-drafted document's status is never
`"approved"` and its workflow never advances past `"documents_generated"`.

### Why `cleanup_old_logs` targets `scheduler_task_runs`, not `app.log`

The application's physical log files already have their own
rotation/retention entirely handled by loguru
(`config/logging_config.py`: `rotation="1 day", retention="14 days"`,
configured back in the Browser Framework milestone). Duplicating that here
would just be a second, competing retention policy for the same files.
What loguru doesn't know about is `scheduler_task_runs` — a table this
milestone introduces, which grows by one row every task run and would
otherwise accumulate forever on a continuously running scheduler. That's
what "cleaning old logs" means concretely here. It only ever deletes rows
that have actually finished — a still-`RUNNING` row (a stuck/crashed run)
is left alone regardless of age, so a real problem stays visible instead
of being silently erased.

## Status, locking, and retries

### Status values

`TaskStatus`: `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`, `SKIPPED` (see
`scheduler_models.py`). In practice every run this service creates goes
straight to `RUNNING` (no meaningful gap where a row sits at `PENDING`).

### Locking — "the same task cannot run twice at the same time"

One `threading.Lock` per task name, held for the task's *entire* run
(including all retry attempts), owned by `SchedulerService`. If
`run_task()` can't acquire the lock immediately (non-blocking), it
records a `SKIPPED` run (with `started_at == finished_at`, since nothing
executed) and returns immediately — it never blocks waiting for the other
run to finish.

This is in-process locking, not a database-level lock, because that's
what actually matches this app's architecture: one Python process, one
`SchedulerService` instance. The only real race this needs to prevent is a
scheduled fire overlapping a manual "Run now" click (or two manual clicks
in quick succession) — both of which share this exact lock, since both
paths call the identical `SchedulerService.run_task()`. APScheduler's own
`max_instances=1` (set in `job_scheduler.py`) is a belt-and-braces second
guard at the trigger level.

### Retries — reusing `core.RetryManager`, not a new mechanism

`SchedulerService` reuses `core.retry_manager.RetryManager` — the exact
same exponential-backoff-with-jitter class `BrowserManager`/`PageManager`/
`AnthropicProvider` already use — configured with `retry_on=(Exception,)`
(rather than the `TransientError` marker those callers use), since
scheduler tasks have no browser/LLM-specific transient-vs-permanent
distinction: any failure here is worth one more attempt before giving up.
`max_attempts` is per-task (`TaskDefinition.max_attempts`, configurable
per task type via settings — `cleanup_old_logs` gets `max_attempts=1`,
everything else defaults to `settings.scheduler_task_max_attempts` = 3).

Between attempts, the session is explicitly rolled back
(`session.rollback()` inside the retry wrapper) — a failed attempt could
leave a partial write in the session's pending state, which would poison
the next retry attempt if not cleared first. Verified directly by
`test_run_task_rolls_back_session_between_retry_attempts`.

### One shared service instance

`web/app.py` creates exactly one `SchedulerService` (`scheduler_service`)
at import time — cheap (just per-task `threading.Lock`s), never starts a
background thread by itself. Both `routes/scheduler.py` and
`api/scheduler_api.py` resolve it through a FastAPI dependency
(`get_scheduler_service()`), the same DI pattern already used for
`get_db_session`/`get_current_user`/`get_llm_provider` — not by importing
the singleton object directly. This matters for testability: overriding
`get_db_session` alone has no effect on an object that isn't resolved
through FastAPI's dependency injection, so `tests/test_scheduler.py`'s
dashboard tests override `get_scheduler_service` too, pointing an
isolated `SchedulerService` at the test's in-memory database — otherwise
those tests would silently read/write the real `data/jobs.db`.

## The periodic scheduler is off by default

`settings.scheduler_enabled` defaults to `False`. `web/app.py`'s
`lifespan` context manager calls `job_scheduler.start_if_enabled()` on
startup, which does nothing (logs and returns `None`) unless this is set
`True` in `.env`. This is deliberate: importing the app — including under
`pytest`, which imports the entire `web.app` module tree — must never
silently start a real background thread. Manual "Run now" works
identically either way, since it calls `SchedulerService.run_task()`
directly, independent of whether the periodic `BackgroundScheduler` is
running.

When enabled, each task fires on its own `IntervalTrigger`
(`TaskDefinition.interval_seconds`, configurable per task via settings —
`scheduler_import_jobs_interval_seconds` etc.), with `coalesce=True` (a
missed run, e.g. because the app was down, fires once on resume, not once
per missed interval) and `max_instances=1`.

## A real bug found and fixed during testing: naive vs. aware datetimes

`SchedulerRepository` originally stamped `started_at`/`finished_at` with
`datetime.now(timezone.utc)` (timezone-aware). SQLite has no real
timezone-aware storage — a value written with `tzinfo` set comes back
**naive** on the next read regardless. `cleanup_old_logs`'s bulk `DELETE`
statement, which SQLAlchemy partly evaluates in Python against in-memory
object state, hit exactly this mismatch:
`TypeError: can't compare offset-naive and offset-aware datetimes`. Fixed
by adding `scheduler_models.utc_now()` — a naive-UTC helper
(`datetime.now(timezone.utc).replace(tzinfo=None)`) — used everywhere this
subsystem stamps a timestamp, matching how the rest of this codebase
already handles timestamps (`core.screenshot_manager`,
`documents.export_manager` both use naive `datetime.now()`). Caught by
`test_cleanup_old_logs_deletes_only_old_finished_runs` before it shipped.

## Dashboard

`/scheduler` (`routes/scheduler.py`, `templates/scheduler.html`): a table
of the 5 registered tasks (name, description, interval, last-run status
badge) each with a "Run now" button (a plain HTML form POST, matching
every other mutation-via-form route in this codebase, e.g.
`routes/documents.py`'s regenerate button), plus a recent-run-history
table below. Read/trigger only — this page cannot change a task's
schedule or retry configuration; those are `.env` settings.

`/api/scheduler/tasks`, `/api/scheduler/history`, and
`POST /api/scheduler/{task_name}/run` (`api/scheduler_api.py`) are the
JSON equivalents for programmatic callers, protected by
`get_current_api_user` (401, not a redirect) like every other API file.

`components/status_badge.html`'s color map was extended with
`pending`/`running`/`success`/`failed`/`skipped` — the same shared badge
already used for `WorkflowStatus`/`DocumentStatus` values.

## Testing

`tests/test_scheduler.py` — 27 tests:

- **`SchedulerService`**: a successful run records full history; a
  transient failure retries then succeeds (attempt count verified); a
  permanent failure exhausts all retries and records `FAILED` with the
  underlying error message; a task already holding its lock returns
  `SKIPPED` immediately without executing; the session rolls back between
  failed retry attempts; an unknown task name raises `KeyError`; history
  and latest-per-task queries return correctly ordered results.
- **Each of the 5 task functions individually**: fixture import creates
  jobs and is idempotent on a second run (and raises clearly if the
  fixture file is missing); AI matching genuinely exercises the
  LLM-blended path (`used_llm is True`) and skips users without a saved
  profile; document generation only drafts above the score threshold,
  skips matches that already have one, and — the central safety
  invariant — never auto-approves a document or advances a workflow past
  `DOCUMENTS_GENERATED`; workflow-status sync creates missing workflows
  and is a no-op on rows that already have one; log cleanup deletes only
  old *finished* rows, never a still-running one.
- **Dashboard**: the scheduler page renders with zero raw Jinja syntax
  (even with no history yet), "Run now" creates a history row and
  redirects, an unknown task 404s, and both the page and every
  `/api/scheduler/*` route correctly redirect/401 when unauthenticated —
  exercising the exact same `get_current_user`/`get_current_api_user`
  protection every other dashboard route already has.

Manually verified end-to-end against a real `uvicorn` process and the
seeded dev database: logged in, ran all 5 tasks via both the HTML "Run
now" button and the JSON API, confirmed idempotent re-runs, confirmed the
scheduler stays off by default (`SCHEDULER_ENABLED=false` logged at
startup), and confirmed unauthenticated requests are blocked exactly like
every other dashboard route. Full suite: 179 tests (152 pre-existing + 27
new), zero regressions.

## Extension points

- **Real LLM provider** — `SchedulerFakeLLMProvider` is a deliberate,
  clearly-named stand-in. Swapping in `AnthropicProvider` once a real API
  key is configured is a one-line change in `run_ai_matching.py`/
  `generate_draft_documents.py` — explicitly not done this milestone.
- **Notifications on task failure** — e.g. an email/Slack alert when a
  task's `FAILED` status is recorded — explicitly out of scope; no
  notification-sending capability exists anywhere in this codebase yet
  (same gap noted in docs/DASHBOARD.md).
- **Configurable schedules from the dashboard** — intervals are currently
  `.env` settings only; a future version could let a user adjust them
  from `/scheduler` itself.
- **Live scraping tasks** — `import_fixture_jobs` could be joined by a
  real `NHSScraper`-backed task once the compliance question already
  raised in the NHS Jobs milestone is revisited and explicitly approved
  for a specific site.
