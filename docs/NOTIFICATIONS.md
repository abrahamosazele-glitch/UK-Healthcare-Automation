# Notification & Event System

`src/job_automation/notifications/` is a centralized notification and
event system: every important action in the application can generate an
in-app notification, without the module that took the action ever
importing `NotificationService` directly. A lightweight event bus is the
decoupling mechanism — existing modules (scheduler, AI matching,
documents, workflow, auth) publish events; a set of listeners in this
package alone turns the ones worth surfacing into `Notification` rows.

**Only in-app notifications are implemented.** Email/SMS/push exist as
provider *interfaces* that raise `NotImplementedError` — never real
sending. **No Anthropic API key integration, no deployment, no automatic
application submission** — all explicitly out of scope, same as every
constraint on prior milestones.

## Architecture

```
src/job_automation/notifications/
├── notification_models.py    — NotificationType, NotificationSeverity
├── events.py                  — EventType, Event (dependency-free)
├── event_bus.py                — EventBus, and the shared event_bus singleton
├── notification_repository.py — NotificationRepository (pure data access)
├── notification_providers.py  — NotificationProvider interface + 4 implementations
├── notification_service.py    — NotificationService (the orchestrator)
└── notification_listeners.py  — register_notification_listeners()
```

```
Publishers (no import of NotificationService)         Listener module (imports both)
─────────────────────────────────────────────         ──────────────────────────────
scheduler.scheduler_service.SchedulerService      ┐
scheduler.tasks.run_ai_matching                   │
scheduler.tasks.import_fixture_jobs               ├──> event_bus.publish(Event, session) ──> notification_listeners.py
documents.document_service.DocumentService        │        (in-process, synchronous,           (the only module that
workflows.status_manager.StatusManager            │         subscriber failures swallowed)       imports NotificationService)
auth.auth_service.AuthService                     ┘                                                     │
                                                                                                          ▼
                                                                                              NotificationService.create()
                                                                                                          │
                                                                                                          ▼
                                                                                              NotificationRepository (DB)
                                                                                                    +
                                                                                              every configured NotificationProvider
                                                                                              (only InAppNotificationProvider by default)
```

## The `Notification` model

`database.models.notification.Notification` — one row per notification:
`id`, `user_id` (nullable), `type`, `title`, `message`, `severity`,
`created_at`, `read_at`, `source`, `metadata` (JSON). Migration
`bfbc84b1875c`.

Two deviations from a completely literal field list, both necessary and
documented in the model file itself:

- **`user_id` is nullable** — deliberately, unlike almost every other
  user-owned table in this schema. Some notifications are system-wide
  (e.g. "a background scheduler task failed") rather than about one
  candidate's data. `NotificationRepository`/`NotificationService` treat a
  `NULL` `user_id` row as visible to *every* user — every read query is a
  `WHERE user_id = :user_id OR user_id IS NULL`, the same way a real
  notice-board notification isn't addressed to anyone specifically.
- **The Python attribute is `metadata_`, not `metadata`** — `metadata` is
  reserved on every SQLAlchemy declarative model (it shadows
  `Base.metadata`, the table's schema registry). The actual database
  column is still named `metadata`
  (`mapped_column("metadata", JSON)`), matching this milestone's literal
  field list; only the Python-side attribute name had to change.

## The event bus

`event_bus.py`'s `EventBus` is deliberately minimal — synchronous,
in-process, no message broker, no persistence, no retry queue. This app is
one Python process (the same "don't build distributed-systems machinery"
reasoning already applied to the Background Scheduler milestone's
in-process locking); an in-memory publish/subscribe dispatcher is the
right amount of infrastructure for "future modules publish events instead
of directly calling `NotificationService`."

`EventBus.publish(event, session)` takes the triggering `Session`
alongside the `Event` — not because `Event` itself carries any
infrastructure (it's a plain, dependency-free dataclass:
`event_type`, `payload: dict`, `user_id: UUID | None`, `occurred_at`), but
because a listener that writes a `Notification` needs to do so **in the
same transaction** as whatever triggered the event, so the notification
commits or rolls back together with the change it's about (a workflow
transition, a document generation) rather than in a separate, potentially
inconsistent transaction.

**A subscriber's failure never breaks the publisher.** `publish()` catches
and logs any exception a handler raises. A bug in notification creation
must never fail the actual business operation (approving a document,
matching a job) that published the event — verified directly by
`test_event_bus_swallows_a_failing_handler_without_raising`.

### The 9 event types

`EventType` / `NotificationType` mirror each other one-to-one (kept as
separate enums so a future event could map to zero, one, or several
notification types without forcing them to stay identical forever):

`SCHEDULER_TASK_STARTED`, `SCHEDULER_TASK_FINISHED`, `JOB_IMPORTED`,
`MATCH_COMPLETED`, `DOCUMENT_GENERATED`, `WORKFLOW_UPDATED`,
`USER_REGISTERED`, `USER_LOGGED_IN`, `ERROR_OCCURRED`.

### Listener registration happens automatically on import

`notifications/__init__.py` calls `register_notification_listeners(event_bus)`
at the bottom of the module, after its own imports. Every publisher
(`scheduler_service.py`, `document_service.py`, `status_manager.py`,
`auth_service.py`) already does `from job_automation.notifications.event_bus
import event_bus`, and importing any submodule of a package always
executes that package's `__init__.py` first — so the shared `event_bus`
singleton is guaranteed to have its listeners wired up the moment anything
in the app publishes to it, including standalone scripts
(`scripts/seed_demo_data.py`) that never import `web.app`. Verified
directly: running the seed script (which never touches the notifications
package explicitly) produces 15 real `Notification` rows purely as a side
effect of `AuthService.register()`, `DocumentService.generate_*()`, and
`WorkflowService`'s calls into `StatusManager.transition()`.

`register_notification_listeners()` is idempotent **per bus instance**
(tracked via a dynamic attribute set on the `EventBus` object itself, not
a module-level flag) — so the shared singleton only ever registers once
regardless of how many times this runs, while tests construct a fresh
`EventBus()` and register cleanly on it every time without being blocked
by an unrelated test's earlier registration on the real singleton.

## Integration points

Every hook publishes an event; **none of them import `NotificationService`**
— that's the whole point of routing through the bus:

| Trigger | Where | Event | Notes |
|---|---|---|---|
| Scheduler task starts | `SchedulerService._run_locked()` | `SCHEDULER_TASK_STARTED` | System-wide (`user_id=None`). |
| Scheduler task finishes (success or failure) | same | `SCHEDULER_TASK_FINISHED` | Severity `success` or `warning`. |
| Scheduler task fails | same | `ERROR_OCCURRED` | Severity `error`; the one concrete "errors occur" hook this milestone wires (see below). |
| Fixture jobs imported | `scheduler.tasks.import_fixture_jobs` | `JOB_IMPORTED` | Listener skips notifying if nothing was created/updated. |
| AI matching completes for a user | `scheduler.tasks.run_ai_matching` | `MATCH_COMPLETED` | Per user, only if ≥1 match was evaluated. |
| A document is generated | `DocumentService._validate_and_save()` | `DOCUMENT_GENERATED` | Covers manual regeneration *and* scheduled generation — one choke point, both callers. |
| A workflow transitions | `StatusManager.transition()` | `WORKFLOW_UPDATED` | Every transition in the app funnels through here — automatic advances and human approve/reject/apply decisions alike. |
| A user registers | `AuthService.register()` | `USER_REGISTERED` | Severity `success`. |
| A user logs in successfully | `AuthService.authenticate()` | `USER_LOGGED_IN` | Nothing published on failed login — see below. |

### Why "errors occur" is scoped to scheduler task failures

The milestone's requirement is broad ("Generate notifications when...
Errors occur"), but this codebase has no single global exception-catching
pathway today (no app-wide FastAPI exception handler for unhandled
errors, only the specific `NotAuthenticatedError` one). Rather than
inventing a new global error-handling strategy — real scope creep for a
milestone explicitly told to stop after in-app notifications — this uses
the one well-defined, already-tested "failure" concept that exists:
`SchedulerService`'s retry-exhausted failure path, which already produces
a clean error message and represents a genuine, actionable failure a user
should know about. A future milestone could add a broader mechanism (e.g.
a global exception handler publishing `ERROR_OCCURRED`) without changing
anything here.

### Why nothing is published on a failed login

There's no legitimate `user_id` to notify — the caller isn't authenticated
yet, so a notification would either go nowhere (in-app is inherently
tied to a logged-in session viewing their own notifications page) or would
have to guess which account was targeted, which is itself a
security-relevant behavior beyond this milestone's scope. Verified
directly by `test_failed_login_publishes_no_notification`.

### The `run_ai_matching`/`import_fixture_jobs` exception: direct singleton use

Every class-based integration point (`SchedulerService`, `DocumentService`,
`StatusManager`, `AuthService`) takes an `event_bus` constructor parameter
defaulting to the shared singleton — the same dependency-injection pattern
already used throughout this project (`get_scheduler_service()`,
`get_llm_provider()`, etc.), letting tests substitute an isolated
`EventBus()`. The two **task functions**
(`run_ai_matching.run()`, `import_fixture_jobs.run()`) instead import the
shared `event_bus` singleton directly at module level. This is
deliberate, not an inconsistency: these are plain `(session) -> dict`
functions, not classes with constructors — they already import `settings`
the same direct, un-injected way. Tests for these two functions still get
full isolation by constructing their own `EventBus()`, registering
listeners on it, and asserting against the notifications written to their
own in-memory database — the *listeners* are isolated per test even though
the *publish call site* uses the global bus object.

## Notification providers

`notification_providers.py` defines `NotificationProvider` (one abstract
method, `send(notification)`) and four implementations:

- **`InAppNotificationProvider`** — "delivering" an in-app notification is
  a no-op: persisting the `Notification` row (already done by
  `NotificationService.create()` before any provider runs) *is* the
  delivery — the dashboard bell/page read that row directly.
- **`EmailNotificationProvider`** — real, added for the Real Email
  Notification Delivery milestone. Never sends anything itself; decides
  per-user whether a notification should become a queued email (per-type
  preference, quiet hours, the AI-match threshold) and, if so, inserts an
  `EmailOutboxRecord`. See docs/EMAIL_NOTIFICATIONS.md for the full design.
  `NotificationService`'s default provider list is
  `[InAppNotificationProvider(), EmailNotificationProvider(session)]`.
- **`SMSNotificationProvider`, `PushNotificationProvider`** — still
  placeholders: real classes satisfying the interface, not just docstring
  mentions, but `send()` deliberately raises `NotImplementedError` rather
  than silently no-op-ing — the same "real future interface, not a silent
  no-op" pattern already used for `profile.profile_loader`'s PDF/DOCX
  loaders. Nothing in this codebase constructs these two by default.

## Dashboard

- **Bell + unread badge** (`templates/components/navbar.html`) — an HTMX
  element polling `GET /notifications/bell` (`hx-trigger="load, every
  30s"`), which returns either an empty string (0 unread) or a small
  `<span class="badge ...">N</span>` fragment. A dedicated HTML-fragment
  route, not `/api/notifications/unread-count` (JSON) — HTMX swaps HTML
  into the DOM, not a JSON body.
- **`/notifications` page** (`routes/notifications.py`,
  `templates/notifications.html`) — every notification visible to the
  current user (their own plus system-wide), newest first, with a
  severity badge (reusing `components/status_badge.html`, extended with
  `info`/`error` entries for `NotificationSeverity` alongside the
  existing `WorkflowStatus`/`DocumentStatus`/`TaskStatus` values), an
  "Unread" indicator, and a per-item "Mark read" button.
- **Mark read / mark all read** are plain HTML form POSTs that redirect
  back to `/notifications` (full page reload) — the same pattern already
  used by `routes/documents.py`'s regenerate button and
  `routes/scheduler.py`'s "Run now" button, not an HTMX partial swap.
  `api/notifications_api.py`'s JSON `POST /api/notifications/{id}/read`
  /`POST /api/notifications/mark-all-read` are the separate, programmatic
  equivalents for external callers/tests — one mutation path per action,
  not two layered on top of each other.
- **`api/notifications_api.py`** — `GET /api/notifications`,
  `GET /api/notifications/unread-count`, `POST /api/notifications/{id}/read`,
  `POST /api/notifications/mark-all-read`. Protected by
  `get_current_api_user` (401, not a redirect), like every other API file.

## Testing

`tests/test_notifications.py` — 37 tests:

- **`NotificationService`**: create persists and delivers through
  providers; defaults to `InAppNotificationProvider` only; unread count
  and ordering; a system-wide (`user_id=None`) notification is visible to
  every user; marking read is idempotent and rejects another user's
  notification (but allows reading a system-wide one); mark-all-read only
  touches the calling user's own visible notifications.
- **Event bus**: a subscribed handler receives its event; an
  unsubscribed event type delivers to nobody; a failing handler doesn't
  stop a working one from running or propagate to the publisher;
  `register_notification_listeners()` is idempotent per bus instance;
  `clear()` removes every subscription.
- **Providers**: `InAppNotificationProvider.send()` never raises;
  `Email`/`SMS`/`PushNotificationProvider.send()` all raise
  `NotImplementedError`; a service can be configured with a placeholder
  provider without ever exercising it.
- **Every integration hook**: scheduler success publishes
  started+finished notifications; scheduler failure publishes
  error+finished notifications; document generation notifies the owning
  user; a real workflow transition (via `close()`, since `start_workflow()`
  itself doesn't go through `StatusManager.transition()`) notifies the
  owning user; registration publishes a welcome notification; successful
  login publishes a notification; failed login publishes nothing.
- **Dashboard**: the notifications page renders with zero raw Jinja (both
  populated and empty), the bell badge shows the right count (and is
  empty at zero), mark read/mark-all-read work via both the HTML form
  routes and the JSON API, and both the page and the API correctly
  require authentication.

Manually verified end-to-end: running `scripts/seed_demo_data.py` (which
never imports anything from `job_automation.notifications`) produced 15
real notifications purely through the event bus; a live `uvicorn` session
confirmed the bell badge, the notifications page, mark read (16 → 15
unread), and mark all read (→ 0 unread) all work against the real
database. Full suite: 217 tests (180 pre-existing + 37 new), zero
regressions.

## Extension points

Deliberately not built:

- **Real SMS/push sending** — the two remaining placeholder providers
  define the interface; wiring one up needs an actual API/
  device-registration integration decision. Email is real — see
  docs/EMAIL_NOTIFICATIONS.md.
- **A broader "errors occur" hook** — e.g. a global FastAPI exception
  handler publishing `ERROR_OCCURRED` for any unhandled 500 (the
  Production Readiness milestone's `_log_unhandled_exception` logs these
  through loguru but doesn't publish an event), or hooking failed-login
  attempts into a security-alert channel.
- **Notification preferences** (which types/severities a user wants to
  see, digest/throttling for high-frequency events like
  `SCHEDULER_TASK_STARTED`) — everything is shown today; no per-user
  filtering exists yet.
- **Real-time push to the browser** (WebSocket/SSE instead of 30-second
  polling) — the current bell badge is "eventually visible within 30
  seconds," not instant.
