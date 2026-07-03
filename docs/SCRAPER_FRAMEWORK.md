# Scraper Framework

`src/job_automation/scrapers/base/` is reusable scraper infrastructure built
on top of `job_automation.core` (the Playwright browser automation
framework). It contains **no site-specific logic** — no NHS Jobs, TRAC,
Indeed, Reed, or CareHome scraping code, and no AI matching or automated
application logic against a real site. Every future scraper is expected to
subclass and compose these classes, not reimplement search/parse/pagination/
login/application handling per site.

## Relationship to `job_automation.core`

`core/` knows about browsers, pages, and contexts — nothing about jobs,
search results, or logins. `scrapers/base/` is the layer above it that knows
the *shape* of a scraping problem (search, paginate, parse, optionally log
in and apply) without knowing any particular site's markup. A concrete
scraper (not built yet) is the layer above *that*, which knows one site's
actual selectors and URLs.

```
job_automation.core            — browsers, pages, contexts, retries, rate limits
        │
job_automation.scrapers.base   — search/parse/paginate/login/application *shape*
        │
job_automation.scrapers.<site> — one real site's selectors/URLs (not built yet)
```

## Why the file layout differs slightly from a strictly-mirrored `core/`

`ScraperConfig` and `ScraperExceptions` live inside `scrapers/base/` (not at
the `scrapers/` package level) per how this milestone's file list was given
— everything under `src/job_automation/scrapers/base/`. This keeps `base/`
fully self-contained as "the framework," with concrete per-site scrapers
living as siblings directly under `scrapers/` and importing from
`scrapers.base`.

## Components

### ScraperExceptions (`scraper_exceptions.py`)

`ScraperError` extends `job_automation.core.browser_exceptions
.BrowserAutomationError` rather than starting a second parallel hierarchy —
a scraper failure *is* a browser-automation failure at a higher level, and
reusing the base means one `except BrowserAutomationError` still catches
everything, and `TransientError` (from `core`) remains the single source of
truth for "is this worth retrying" across the whole app.

| Exception | Transient? | Meaning |
|---|---|---|
| `LoginError` | no | Login failed — likely wrong credentials or a changed form; retrying repeatedly also risks tripping bot defenses |
| `SearchError` | yes | Performing a search failed |
| `ParsingError` | no | Markup didn't match what the parser expected — needs a code update, not a retry |
| `PaginationError` | yes | Navigating between result pages failed |
| `ApplicationSubmissionError` | no | An upload/answer/submit/confirm step failed |
| `ScraperNotFoundError` | — | `ScraperRegistry.get()` found no scraper under that name |
| `ScraperRegistrationError` | — | Tried to register a name already claimed by a different class |

### ScraperConfig (`scraper_config.py`)

Composes `BrowserConfig` (`browser: BrowserConfig` field) rather than
duplicating its fields — timeouts, headless mode, retry count, and
download/screenshot directories are already fully specified there. Adds
exactly one scraper-specific field: `max_pages`, a hard safety cap on
pagination independent of whatever "last page" detection a site's DOM
offers. Same shape as `BrowserConfig`: frozen `pydantic.BaseModel`, works
standalone with defaults, built for real use via
`ScraperConfig.from_settings(settings, browser_config)`.

### ScraperRegistry (`scraper_registry.py`)

Supports `register()`, `unregister()`, `get()`, `list()` as required, but
the primary way scrapers get in here is **automatic**:
`BaseScraper.__init_subclass__` calls `register(cls.site_name, cls)` for
every concrete subclass the moment it's defined (at import time) — there is
no separate "add it to the registry" step to remember or forget. A class is
only auto-registered if it declares a non-empty `site_name` *and* is
concrete (`inspect.isabstract(cls)` is False) — this correctly excludes
`BaseScraper` itself and any intermediate abstract base a site family might
introduce (e.g. a shared `NHSTrustScraper` with no `site_name` of its own).

Kept as a classmethod-based registry (effectively a process-wide singleton)
since there's exactly one registry needed, not a class you'd ever want
multiple instances of.

### BaseParser (`base_parser.py`)

Converts one job listing's markup — a Playwright `Locator` scoped to a
single listing — into a typed `ParsedJob` dataclass covering every field
this milestone specified: title, employer, salary, band, location, contract
type, hours, visa sponsorship, closing date, job URL, reference number,
description, requirements. Only `title` is required; everything else is
optional since different sites expose different subsets (NHS's Agenda for
Change `band` has no equivalent on a generic care-sector board).

`parse()` is abstract (every site's markup differs); `parse_all()` is
concrete and reusable — it's the resilience policy of skipping and logging
an individual unparseable card instead of letting one bad listing abort an
entire page of otherwise-good results. Verified directly: the dummy fixture
site includes one deliberately malformed card (missing its title), and the
verification script confirms it's skipped, not raised.

### BaseSearch (`base_search.py`)

Keyword search, location search, filter building, and sorting are all
captured in one typed value object, `SearchCriteria` (`keywords`,
`location`, `filters`, `sort_by`), so a scraper always works with the same
shape regardless of site. `build_search_url()`/`execute_search()` are
abstract (query-string search vs. filling in a multi-step form are
completely different mechanically); `search()` is the concrete template
method tying them together with logging.

Pagination is **not** handled here on purpose — `BaseSearch` only needs to
get the first page of results showing; `BasePaginator` (a separate class)
takes it from there. Splitting them keeps each class's responsibility to
one thing.

### BasePaginator (`base_paginator.py`)

`has_next_page()`/`go_to_next_page()`/`has_previous_page()`/
`go_to_previous_page()` are abstract (every site's pagination controls
differ — a "Next" link, numbered pages, infinite scroll). `is_last_page()`
and the `next()`/`previous()` template methods are concrete, and combine the
site-specific detection with `ScraperConfig.max_pages` as an independent
safety net — so a scraper can never loop forever even if a site's markup
changes in a way that silently breaks `has_next_page()`. Verified two ways:
walking all 3 real pages of the dummy fixture and stopping at the true last
page (no `next-page` link), and separately with `max_pages=1` to confirm the
cap stops pagination after page 1 even though page 2 exists.

### BaseLogin (`base_login.py`)

`login()`, `logout()`, and `session_valid()` are abstract — every site has a
different form and a different way of proving you're authenticated (a
welcome banner, an account menu, a specific cookie). `restore_session()` and
`save_session()` are concrete: they only delegate to
`job_automation.core.session_manager.SessionManager`, which already owns
session persistence — there's nothing site-specific about *storing*
cookies, only about *proving* a session is valid.

### BaseApplication (`base_application.py`)

`upload_cv()`, `upload_cover_letter()`, `answer_questions()`, `submit()`,
and `confirm_submission()` are abstract (every application form differs).
`apply()` is the one concrete piece: the orchestration order — upload CV,
upload cover letter, answer questions, submit, confirm — is identical
regardless of site, so it's a template method here instead of being
reimplemented per scraper. This milestone only builds the reusable *shape*
of an application flow; no real site's form is wired up to it yet.

### BaseScraper (`base_scraper.py`)

The composition root. Owns browser lifecycle (via `BrowserManager`),
logging, retry integration (`RetryManager`), rate limiting (`RateLimiter`),
session loading (`SessionManager`), error handling, and cleanup — every
responsibility this milestone specified for it. Every one of these is
constructor-injectable but defaults to a real implementation built from
`ScraperConfig.browser`, so a typical concrete scraper doesn't need to wire
eight objects together itself.

**Deliberately does not hold `BaseLogin`/`BaseSearch`/`BasePaginator`/
`BaseApplication` instances.** Forcing all five Base* classes into this
constructor would make it an 8+ parameter god-object, and not every scraper
needs all of them (a site with no login wall has no `BaseLogin`; a scraper
not yet doing automated applications has no `BaseApplication`). Instead, a
concrete scraper composes whichever it needs in its own `__init__`, using
`self.page_manager`/`self.session_manager`/`self.download_manager`
(exposed as properties) as their shared dependency. See `DummyScraper` for
exactly this pattern.

`run()` is the template-method entry point: it starts the browser if not
already running, calls the abstract `scrape()`, captures a screenshot and
re-raises on any failure, and stops the browser again only if it started it
here (so `run()` works both standalone and inside a `with scraper:` block
without double-starting or prematurely stopping).

## Class diagram

```
BrowserAutomationError (core)
        │
   ScraperError
   ├── LoginError
   ├── SearchError (+ TransientError)
   ├── ParsingError
   ├── PaginationError (+ TransientError)
   ├── ApplicationSubmissionError
   ├── ScraperNotFoundError
   └── ScraperRegistrationError

ABC BaseScraper                      ABC BaseLogin
  site_name: ClassVar[str]             login() / logout() / session_valid()   [abstract]
  __init_subclass__ -> auto-register   restore_session() / save_session()     [concrete, uses SessionManager]
  start() / stop() / run()  [concrete]
  scrape()                  [abstract]  ABC BaseSearch
  composes (via constructor,             build_search_url() / execute_search() [abstract]
  all optional/defaulted):               search()                             [concrete template method]
    BrowserManager, ContextManager,
    PageManager, SessionManager,       ABC BasePaginator
    RetryManager, RateLimiter,          has_next_page() / go_to_next_page()
    ScreenshotManager,                  has_previous_page() / go_to_previous_page()  [abstract]
    DownloadManager                     is_last_page() / next() / previous()  [concrete]

ABC BaseParser                       ABC BaseApplication
  parse()             [abstract]        upload_cv() / upload_cover_letter()
  parse_all()         [concrete]        answer_questions() / submit()
                                         confirm_submission()                 [abstract]
                                         apply()                              [concrete template method]

ScraperRegistry (classmethods only, no instances)
  register() / unregister() / get() / list()

ScraperConfig
  browser: BrowserConfig
  max_pages: int
```

A concrete scraper (e.g. the not-yet-built `NHSJobsScraper`) subclasses
`BaseScraper` and, in its own `__init__`, instantiates concrete subclasses of
`BaseLogin`/`BaseSearch`/`BaseParser`/`BasePaginator`/`BaseApplication` —
composition, not multiple inheritance.

## Lifecycle of a scraper run

```
DummyScraper(config, base_url)
        │  (composes DummyLogin/DummySearch/DummyParser/DummyPaginator/
        │   DummyApplication in its own __init__, each wired to
        │   self.page_manager from BaseScraper)
        ▼
scraper.run()  (or: `with scraper:` then scraper.scrape())
        │
        ├─ BaseScraper.start()
        │     BrowserManager.start() -> ContextManager.create_context()
        │     (SessionManager supplies a restored context if a valid saved
        │      session exists) -> PageManager.open_page()
        │
        ├─ scraper.scrape()                      [subclass-implemented]
        │     BaseSearch.search(page, criteria)   -> first results page
        │     loop:
        │       BaseParser.parse_all(cards)       -> list[ParsedJob]
        │       BasePaginator.next(page)          -> False when done
        │
        ├─ (on any exception) ScreenshotManager.capture(..., reason=f"{site}_run_failed")
        │
        └─ BaseScraper.stop()
              PageManager.close_page() -> ContextManager.close_context()
              -> BrowserManager.stop()
```

## Error handling

Same layered strategy as `core/` (see docs/BROWSER_FRAMEWORK.md), extended
one level up:

1. Site-specific abstract methods (`login`, `execute_search`,
   `go_to_next_page`, `parse`, `submit`, etc.) are expected to raise the
   matching `ScraperError` subclass on failure — `LoginError`,
   `SearchError`, `PaginationError`, `ParsingError`,
   `ApplicationSubmissionError`.
2. `RetryManager` (from `core`, reused unchanged) retries `SearchError` and
   `PaginationError` because they inherit `TransientError`; it does not
   retry `LoginError`, `ParsingError`, or `ApplicationSubmissionError`, which
   don't.
3. `BaseParser.parse_all()` and `BaseScraper.run()` are the two "boundary"
   points that convert a single failure into a resilience decision instead
   of letting it propagate unconditionally: a bad job card is skipped
   (parsing keeps going), a failed `scrape()` call is screenshotted before
   the exception continues up (nothing is silently swallowed at that level
   — only logged-with-context).

## Retry strategy

Nothing new is introduced here — `BaseSearch`/`BasePaginator` implementations
are expected to raise `SearchError`/`PaginationError` (both `TransientError`)
from within methods that a concrete scraper wraps in
`self.retry_manager.execute(...)` where retrying makes sense (e.g. a flaky
page load during search). `BaseScraper` doesn't force retry wrapping onto
every abstract method itself, since not every step benefits equally — a
concrete scraper decides which of its own operations are worth retrying,
using the same `RetryManager` instance `BaseScraper` already constructed.

## Logging strategy

Every framework class logs via `job_automation.utils.logger`, exactly like
`core/`: scraper start/stop, every registry registration, every search
executed, every page advanced, every skipped unparseable card (as a
warning), every login/application step (via the underlying `PageManager`
calls, which already log clicks/types/navigation), and the final result
count from `run()`.

## Extension guide: adding a new scraper

1. Pick a unique `site_name` (e.g. `"trac_jobs"`).
2. Create `src/job_automation/scrapers/trac_jobs_scraper.py`.
3. Write concrete subclasses of whichever Base* classes the site actually
   needs (a site with no login wall for searching doesn't need a
   `BaseLogin`; skip `BaseApplication` until automated applications are in
   scope):
   - `TracJobsParser(BaseParser)` — implement `parse()` with TRAC's real
     selectors.
   - `TracJobsSearch(BaseSearch)` — implement `build_search_url()`/
     `execute_search()` against TRAC's actual search UI.
   - `TracJobsPaginator(BasePaginator)` — implement the four abstract
     methods against TRAC's actual pagination controls.
   - `TracJobsLogin(BaseLogin)` / `TracJobsApplication(BaseApplication)` —
     only if/when login or automated applications are in scope.
4. `class TracJobsScraper(BaseScraper): site_name = "trac_jobs"` —
   compose the concrete classes from step 3 in `__init__`, implement
   `scrape()` following the same search -> loop(parse, paginate) shape as
   `DummyScraper`.
5. Nothing needs to be registered manually — defining the class registers
   it. Retrieve it later via `ScraperRegistry.get("trac_jobs")`.
6. Do not reimplement retry, rate limiting, screenshots, session
   persistence, or browser lifecycle — all of that is inherited from
   `BaseScraper`/`core`.

## Future scraper checklist

- [ ] Confirm the target site's actual selectors for every `ParsedJob` field
      it exposes (some sites won't have all of them — leave those `None`).
- [ ] Confirm whether search requires login (most healthcare job boards
      don't, for browsing — only for applying).
- [ ] Confirm the pagination mechanism (link-based, numbered, infinite
      scroll) and implement `BasePaginator` accordingly; set a sane
      `max_pages` in `ScraperConfig` regardless.
- [ ] Decide `rate_limit_min/max_delay_seconds` appropriate for the target
      site — check its robots.txt / terms of service first.
- [ ] Verify the site's robots.txt and terms of service permit automated
      access before writing the scraper at all.
- [ ] If login is required, implement `BaseLogin` and verify
      `session_valid()` correctly detects both the logged-in and
      logged-out states.
- [ ] Add the new scraper's own fixture-based verification script (mirror
      `scripts/verify_scraper_framework.py`) before pointing it at the real
      site, if practical.
- [ ] Confirm `ParsingError` is raised (not a silently-empty `ParsedJob`)
      when a required field is missing, so `parse_all()`'s skip-and-log
      resilience actually engages.

## Verification

`scripts/verify_scraper_framework.py` runs `DummyScraper` (in
`tests/dummy_scraper.py`) against a static fixture site
(`tests/fixtures/dummy_site/`, served locally over HTTP by
`tests/dummy_site_server.py` — never a real website) and checks:

- **Registration**: `DummyScraper` auto-registers on import; manual
  `register()`/`unregister()`/`get()`/`list()` all work; `get()` on an
  unknown name raises `ScraperNotFoundError`.
- **Browser launch**: `scraper.is_running` is `True` inside the session.
- **Search flow**: `DummySearch` navigates to the fixture's first results
  page and confirms job cards are present.
- **Parser**: every `ParsedJob` field is checked against known fixture
  values (title, employer, band, location, visa sponsorship, closing date,
  reference number, job URL, requirements); a deliberately malformed card
  (missing title) is confirmed skipped, not raised.
- **Pagination**: walks all 3 real fixture pages and stops at the true last
  page; a second run with `max_pages=1` confirms the safety cap independently
  of natural last-page detection.
- **Cleanup**: `scraper.is_running` is `False` after the `with` block exits.
- Also exercises `BaseLogin` (fills and submits a real form, confirms
  `session_valid()`) and `BaseApplication` (uploads two real files, answers
  a question, submits, confirms) for completeness, though these weren't in
  the required checklist.

Run it with:

```bash
.venv\Scripts\python.exe scripts\verify_scraper_framework.py
```

Confirmed manually (2026-07-01): exits 0, and no leftover Playwright/
headless Chromium processes remain afterward.
