# Browser Automation Framework

`src/job_automation/core/` is reusable Playwright infrastructure. It contains
**no site-specific logic** — no NHS Jobs, TRAC, Indeed, Reed, or CareHome code.
Every future scraper, login flow, and application-submission workflow is
expected to be built by composing the components below, not by calling
Playwright directly.

There is deliberately no facade class that wires everything together for
you — the framework's public surface is the 10 components themselves, and
callers compose them explicitly (see `scripts/verify_browser_framework.py`
for a complete example). This keeps every component swappable/mockable in
isolation and avoids hiding the composition a future scraper actually needs
to understand anyway.

## Why the synchronous Playwright API

The rest of this project (SQLAlchemy sessions, scripts, the scheduler) is
synchronous. Using Playwright's `sync_api` keeps the whole codebase in one
concurrency model — introducing `asyncio` here would force every future
scraper, the scheduler, and anything that calls into them to become async
too, for no benefit at the scale this project runs at (one browser, one job
site at a time). If high-concurrency scraping across many sites in parallel
becomes a real requirement later, an `AsyncBrowserManager` can be added
alongside `BrowserManager` without touching code that doesn't need it.

## Components

### BrowserConfig (`browser_config.py`)

An immutable (`frozen=True`) `pydantic.BaseModel` — not a `BaseSettings`
subclass. `core/` should not know how to read `.env`/environment variables
itself; `BrowserConfig.from_settings(settings)` builds one from the app's
existing `job_automation.config.settings.Settings`. Every other component
takes a `BrowserConfig` in its constructor (dependency injection) rather than
importing the global `settings` singleton, so each one is unit-testable with
a hand-built config (`BrowserConfig()` has defaults for every field and works
standalone) and has no hidden environment dependency.

Covers: headless/slow-mo, navigation/action timeouts, viewport, user agent,
proxy, download/screenshot/session directories + session max age, retry
count/backoff bounds, and rate-limit min/max delay.

### BrowserExceptions (`browser_exceptions.py`)

`BrowserAutomationError` is the base of every exception this package raises.
Every component translates foreign exceptions (Playwright's own `Error`/
`TimeoutError`, OS I/O errors) into one of these **at the point of contact**
— nothing above that boundary needs to know Playwright's exception types.

`TransientError` is a marker mixin, not a concrete exception. It identifies
which failures are worth retrying:

| Exception | Transient? | Meaning |
|---|---|---|
| `BrowserLaunchError` | yes | Playwright/Chromium failed to launch |
| `ContextCreationError` | yes | `browser.new_context()` failed |
| `PageNavigationError` | yes | `page.goto()` failed or timed out |
| `DownloadError` | yes | Saving/verifying a download failed |
| `ElementNotFoundError` | no | A selector doesn't exist — retrying won't change the page |
| `SessionExpiredError` | no | No valid saved session — caller must re-authenticate |
| `RetryExhaustedError` | — | Raised by `RetryManager` itself when every attempt failed |

`RetryManager` defaults to retrying only `TransientError` subclasses.

### RetryManager (`retry_manager.py`)

A generic executor: `retry_manager.execute(func, operation_name=..., retry_on=...)`.
Retries with **exponential backoff + jitter**: `delay = min(base * 2^(attempt-1), max_delay) + random(0, 25% of that)`.
Every retry logs the attempt number, operation name, exception type/message,
and computed delay before sleeping. If every attempt fails, raises
`RetryExhaustedError` chained (`raise ... from last_exc`) to the final
underlying exception, so the original cause is never lost. Any exception
outside `retry_on` propagates immediately, unretried — this is what makes
"retry only transient failures" actually work: a bad selector fails fast
instead of burning through retries that can't fix it.

Used by `BrowserManager` (launch retries) and `PageManager` (navigation
retries). Kept as a standalone class, not a decorator baked into each
method, so both reuse identical backoff/logging behavior.

### RateLimiter (`rate_limiter.py`)

`rate_limiter.wait()` sleeps a uniform-random duration in `[min_delay,
max_delay]`. That range **is** the jitter — there's no separate fixed-delay-
plus-jitter split, since adding jitter on top of a fixed delay is
mathematically the same as sampling over a wider range, and one range is
simpler to reason about than two. `PageManager.navigate()` calls this before
every navigation when a `RateLimiter` is supplied.

### DownloadManager (`download_manager.py`)

Takes over once a caller already has a Playwright `Download` object (from
`page.expect_download()`); it has no click/navigation logic of its own.
Responsibilities: ensure the download directory exists, resolve filename
collisions (`report.pdf` → `report (1).pdf` → `report (2).pdf`...), call
`download.save_as()`, then verify the file exists and is non-empty — raising
`DownloadError` if either check fails.

### ScreenshotManager (`screenshot_manager.py`)

`screenshot_manager.capture(page, reason)` saves a full-page PNG named
`<reason>_<timestamp>.png` under `data/screenshots/`. **Capture failures are
swallowed and logged, returning `None` instead of raising** — this is almost
always called from inside an `except` block or a dialog handler, and a
broken screenshot must never mask the real error that triggered it.

Called automatically by `PageManager` on:
- navigation failure (after retries are exhausted)
- `safe_click`/`safe_type` failure
- an unexpected dialog (`alert`/`confirm`/`prompt`/`beforeunload`) appearing

### BrowserManager (`browser_manager.py`)

Owns the outermost lifecycle: starts `sync_playwright()`, launches Chromium
(wrapped in `RetryManager.execute` — a flaky launch is transient), and
guarantees shutdown. Usable as a context manager:

```python
with BrowserManager(config) as bm:
    browser = bm.browser
    ...
# browser + Playwright driver both closed here, even if an exception occurred
```

`stop()` **never raises** — every cleanup step is wrapped in its own
try/except that logs a warning and continues, because a broken shutdown must
not mask whatever the caller was doing before it. `is_running` and the
`browser` property let callers check/access state without touching private
attributes.

### ContextManager (`context_manager.py`)

`create_context(browser, storage_state=None)` builds a `BrowserContext`
configured with viewport, user agent, proxy (all from `BrowserConfig`),
`accept_downloads=True`, and — if a `storage_state` path is given and exists
— restores cookies/localStorage from it (an already-authenticated context).
Also exposes `save_storage_state()` and `close_context()`. Stateless with
respect to which `Browser` it uses; `Browser` is a parameter, not something
this class holds, since `BrowserManager` owns that lifecycle.

### SessionManager (`session_manager.py`)

Built **on top of** `ContextManager` (composition), not a reimplementation.
Owns *where* sessions live (`data/sessions/<name>.json`) and *whether* one is
still usable:

- `has_valid_session(name)` — file exists and its mtime is younger than
  `session_max_age_hours` (time-based expiry — deliberately simple, since
  this module is site-agnostic and can't know what a "logged out" page looks
  like for any particular site).
- `load_session(browser, name)` — raises `SessionExpiredError` if invalid,
  otherwise returns a context restored from the saved state.
- `save_session(context, name)` — persist current cookies/localStorage,
  typically called right after a successful login.
- `clear_session(name)` — delete a saved session file.

### PageManager (`page_manager.py`)

The component scrapers will call the most. Composes `RetryManager`,
`ScreenshotManager`, and an optional `RateLimiter` — none of their logic is
duplicated here, only orchestrated.

- `open_page(context)` / `close_page(page)` — also registers a `dialog`
  handler on every new page (see below).
- `navigate(page, url, wait_until="load")` — rate-limits, then retries
  transient navigation failures; screenshots and re-raises
  (`PageNavigationError` via `RetryExhaustedError`) if every attempt fails.
- `safe_click` / `safe_type` / `safe_scroll` — log a warning and return
  `False` on failure instead of raising (click/type also trigger a
  screenshot). Scraping loops should survive one bad selector, not crash.
- `element_exists(page, selector, timeout_ms)` — `True`/`False`, no
  exception either way.
- `wait_for_selector(page, selector, state, timeout_ms)` — returns a
  `Locator` or `None` on timeout.
- Dialog handling: any `alert`/`confirm`/`prompt`/`beforeunload` that no
  calling code explicitly handles would otherwise block Playwright
  indefinitely. The registered handler logs it, captures a screenshot, and
  dismisses it so automation doesn't hang.

## Lifecycle of a browser session

```
BrowserConfig.from_settings(settings)
        │
        ▼
BrowserManager(config).start() ──► sync_playwright().start() ──► chromium.launch()
        │                                  (retried via RetryManager on failure)
        ▼
ContextManager.create_context(browser, storage_state=...)
        │            (SessionManager supplies storage_state if a valid
        │             saved session exists; otherwise None → fresh login)
        ▼
PageManager.open_page(context) ──► registers dialog handler
        │
        ▼
PageManager.navigate(page, url) ──► RateLimiter.wait() ──► retry-wrapped goto()
        │                                                  (ScreenshotManager
        │                                                   captures on failure)
        ▼
safe_click / safe_type / safe_scroll / element_exists / wait_for_selector ...
        │
        ▼
(optional) DownloadManager.save_download(download)
(optional) SessionManager.save_session(context, name)  — after a login
        │
        ▼
PageManager.close_page(page)
ContextManager.close_context(context)
BrowserManager.stop() ──► browser.close() ──► playwright.stop()
        (both steps always run, each independently try/except'd)
```

## Error handling strategy

1. **Translate at the boundary.** Every component catches Playwright/OS
   exceptions where it touches Playwright and re-raises a
   `BrowserAutomationError` subclass. Nothing else in the codebase needs to
   import from `playwright.sync_api` to handle errors.
2. **Mark what's retryable.** The `TransientError` mixin is the single
   source of truth for "is this worth retrying" — `RetryManager` doesn't
   need a hardcoded list of exception types.
3. **Never let cleanup raise.** `BrowserManager.stop()`, `ContextManager
   .close_context()`, and `PageManager.close_page()` all swallow and log
   exceptions during teardown.
4. **Never let a screenshot mask the real error.** `ScreenshotManager
   .capture()` swallows its own failures.
5. **Degrade, don't crash, in scraping loops.** `PageManager`'s `safe_*`
   methods return `False`/`None` on failure rather than raising, since a
   multi-page scrape shouldn't die because one element was missing on one
   page.

## Retry strategy

Exponential backoff with jitter, capped at `retry_max_delay_seconds`:
`delay = min(base_delay * 2^(attempt-1), max_delay) + random(0, 25% of that)`.
Applied to: browser launch (`BrowserManager`) and page navigation
(`PageManager`). Not applied to `safe_click`/`safe_type`/`safe_scroll` — a
missing element is treated as permanent (`ElementNotFoundError`-flavored),
not transient, so those fail fast rather than retrying a selector that will
never appear.

**Bug fixed (Application Workflow Management milestone, 2026-07-01):**
`RetryManager.execute()` matched retryable exceptions with `except retry_on
as exc:`, where the default `retry_on` is `(TransientError,)`. Since
`TransientError` is a plain marker mixin, not itself a `BaseException`
subclass, this raised `TypeError: catching classes that do not inherit from
BaseException is not allowed` the first time a *real* exception actually
needed to be matched against it — a latent bug present since this
milestone but never triggered until a genuine transient failure occurred
(every prior test/run either succeeded on the first attempt or used a fake
provider that bypassed this code path entirely). Fixed by matching with
`except Exception as exc: if not isinstance(exc, retry_on): raise` instead
— `isinstance()` has no such restriction, so `TransientError` keeps its
original design as a pure marker mixin rather than needing to become an
exception type itself. See docs/APPLICATION_WORKFLOW.md for how this was
found and verified.

## Logging strategy

`config/logging_config.py`'s `setup_logging()` configures two Loguru sinks:
a colored console sink at `settings.log_level`, and a rotating
`logs/app.log` file sink always at `DEBUG` (so a run can be diagnosed after
the fact even if the console was quieter). `core/` modules import the
configured logger via `job_automation.utils.logger` rather than
`loguru` directly, matching the rest of the codebase.

Every component logs its key lifecycle events: browser launched/closed,
Playwright driver started/stopped, new context/page, navigation, every
retry attempt (with delay), every download, every screenshot (with reason),
and every swallowed cleanup/capture error as a warning.

## Future extension points

- **Session validity beyond time-based expiry.** `SessionManager` only
  checks file age. A specific scraper can layer a stricter check on top
  (e.g. load the session, navigate to a known page, and look for a login
  form) without changing this class — that logic belongs in the scraper,
  not in generic infrastructure that can't know what "logged out" looks
  like for a given site.
- **Async variant.** If concurrent multi-site scraping is needed later, an
  `AsyncBrowserManager`/`AsyncPageManager` pair can be added alongside the
  sync versions without breaking existing callers.
- **Additional browsers.** `BrowserManager` only launches Chromium today;
  `playwright.firefox`/`playwright.webkit` could be added as a `browser_type`
  option on `BrowserConfig` if a site requires it.
- **Structured retry policies per operation.** `RetryManager.execute()`
  already accepts a per-call `retry_on` override; a future scraper needing
  custom transient-error rules doesn't need to touch this class.

## Verification

`scripts/verify_browser_framework.py` composes all 10 components exactly as
a future scraper would: launches a browser, creates a context, opens a page,
navigates to `https://example.com`, captures a screenshot, and tears
everything down in reverse order — asserting `BrowserManager.is_running` is
`True` after start and `False` after `__exit__`, and that the screenshot
file exists and is non-empty. Run it with:

```bash
.venv\Scripts\python.exe scripts\verify_browser_framework.py
```

Confirmed manually (2026-07-01): the script exits 0, and no Playwright/
headless Chromium processes remain running afterward (checked via `wmic
process where "name='chrome.exe'"` — only the developer's own desktop
browser was present, no `ms-playwright`/headless processes).
