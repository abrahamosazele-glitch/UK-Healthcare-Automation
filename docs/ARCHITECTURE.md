# Architecture

## Data flow

```
scrapers.*  --(RawJobListing)-->  deduplication.duplicate_checker
                                          |
                                   (new jobs only)
                                          v
                              database.db_manager (SQLite/Postgres)
                                          |
                    +---------------------+---------------------+
                    v                                           v
          ai.cv_generator +                          reports.daily_report
          ai.cover_letter_generator                  (reads Job/Application
                    |                                  tables for the day)
                    v
      applications.application_tracker
       (Application row: status, doc paths)
```

`scheduler.job_scheduler` (or an OS-level cron/Task Scheduler entry calling
the scripts in order) drives this once a day. `main.py` wires the same steps
together for a single manual run.

## Folder-by-folder

- **src/job_automation/** — the installable package (src-layout, installed via
  `pip install -e .` as the first line of `requirements.txt`, so it's
  importable as `job_automation.*` from scripts, Alembic's `env.py`, and
  tests without any sys.path hacks).
  - **config/** — environment-driven settings (`settings.py`, a
    `pydantic-settings` `Settings` class that reads `.env` via python-dotenv
    under the hood, now including all browser-automation options) and
    logging setup (`logging_config.py` — a real Loguru `setup_logging()`:
    colored console sink + a rotating `logs/app.log` file sink). Lives inside
    the package (not at repo root) specifically so it can be imported
    consistently as `job_automation.config.settings` from anywhere, including
    Alembic migrations.
  - **core/** — the reusable Playwright browser automation framework (see
    [Browser automation framework](#browser-automation-framework) below, and
    the dedicated [docs/BROWSER_FRAMEWORK.md](BROWSER_FRAMEWORK.md)).
    Infrastructure only — no site-specific scraping logic lives here.
  - **scrapers/** — `base/` is the reusable scraper framework (see
    [Scraper framework](#scraper-framework) below, and the dedicated
    [docs/SCRAPER_FRAMEWORK.md](SCRAPER_FRAMEWORK.md)) built on top of
    `job_automation.core`. `nhs/` is the first site-specific implementation
    (see [NHS Jobs scraper](#nhs-jobs-scraper) below, and
    [docs/NHS_SCRAPER.md](NHS_SCRAPER.md)) — verified only against local
    fixtures, never the live site (compliance reasons, see that doc). The
    remaining per-site files directly under `scrapers/`
    (`indeed_scraper.py`, `totaljobs_scraper.py`, `reed_scraper.py`) are
    still stubs.
  - **database/** — the full production schema. `base.py` holds the
    SQLAlchemy `DeclarativeBase`; `mixins.py` has `UUIDPKMixin` (UUID primary
    key) and `TimestampMixin` (`created_at`/`updated_at`); `enums.py` has the
    status/type enums shared across models. `models/` is a package with one
    file per table (see [Database schema](#database-schema) below) —
    `models/__init__.py` imports every model so they register on
    `Base.metadata` and their string-based `relationship()` targets resolve.
    `repositories/` (`EmployerRepository`, `JobRepository`) and `services/`
    (`JobIngestionService`) are new as of the NHS Jobs scraper milestone —
    pure data access vs. dedup/insert-or-update business logic, respectively
    (see [docs/NHS_SCRAPER.md](NHS_SCRAPER.md)). `db_manager.py` has a
    working engine/session factory wired to `settings.database_url`, a
    SQLite `PRAGMA foreign_keys=ON` listener (so `ON DELETE CASCADE`/
    `SET NULL` are actually enforced in dev), and `init_db()` for dev
    convenience. `migrations/` is a working Alembic setup whose `env.py`
    pulls the DB URL from `Settings` and targets `Base.metadata` — six
    migrations applied so far: `d93073b3212b` (the original 12 tables),
    `75a188d09ece` (NHS job detail fields on `Job`), `e167f5bf7f2e`
    (`Job.visa_sponsorship` + `JobMatch.analysis`, for the AI matching
    engine), `6b90ec850793` (`candidate_profiles` table, for Candidate
    Profile Intelligence), `43d2f89f498d` (`generated_documents` table, for
    Document Generation Intelligence), and `63654f3e5360`
    (`application_workflows`/`workflow_status_history`/`workflow_audit_logs`
    tables + `generated_documents.workflow_id`, for Application Workflow
    Management — see below; this one needed Alembic's batch mode since
    SQLite can't `ALTER TABLE` to add a foreign key constraint directly).
  - **deduplication/** — `duplicate_checker.py` filters scraped jobs against
    what's already stored, using both exact source-ID matching and a
    normalized-content hash for near-duplicate reposts.
  - **ai/** — the AI matching engine (see [AI matching
    engine](#ai-matching-engine) below, and the dedicated
    [docs/AI_MATCHING.md](AI_MATCHING.md)): `matching_models.py`,
    `llm_provider.py`, `anthropic_provider.py`, `prompt_builder.py`,
    `rule_engine.py`, `score_calculator.py`, `cache.py`,
    `matching_engine.py`, `profile_builder.py`, `matching_service.py`.
    `ai_client.py`/`prompts.py`/`cv_generator.py`/`cover_letter_generator.py`
    remain the separate, still-unbuilt CV-file-generation feature from the
    original scaffold, untouched by any milestone so far — cover letter and
    supporting-statement *drafting* is now handled by `documents/` instead
    (see below), which reuses `ai.llm_provider`/`AnthropicProvider` but not
    these four files.
  - **profile/** — Candidate Profile Intelligence (see [Candidate
    profile](#candidate-profile-intelligence) below, and the dedicated
    [docs/CANDIDATE_PROFILE.md](CANDIDATE_PROFILE.md)): the complete,
    structured candidate representation used as the source of truth by
    `documents/`. Deliberately separate from `ai.matching_models
    .CandidateProfile` — see that doc for why the two coexist.
  - **documents/** — Document Generation Intelligence (see [Document
    generation](#document-generation-intelligence) below, and the dedicated
    [docs/DOCUMENT_GENERATION.md](DOCUMENT_GENERATION.md)): generates
    reviewable draft supporting statements, cover letters, and application
    answers from `profile.CandidateProfile` + `ai.matching_models
    .JobSnapshot`/`MatchResult`, via the same `ai.llm_provider.LLMProvider`
    abstraction the matching engine uses. Drafts only — no submission logic
    anywhere in this package.
  - **workflows/** — Application Workflow Management (see [Application
    workflow](#application-workflow-management) below, and the dedicated
    [docs/APPLICATION_WORKFLOW.md](APPLICATION_WORKFLOW.md)): the internal
    journey from a matched job through human review to ready-to-apply and
    beyond (applied/interview/offer/closed), connecting `Job`, `JobMatch`,
    `CandidateProfile`, and `GeneratedDocumentRecord`. No automatic
    application submission anywhere in this package.
  - **applications/** — `application_tracker.py` records what was applied to
    and its status over time. `application_submitter.py` is a placeholder for
    optional site-specific auto-submission, kept separate since most sites
    need manual submission.
  - **reports/** — `daily_report.py` summarizes each day's run using
    `report_templates/daily_report_template.html`.
  - **scheduler/** — `job_scheduler.py`, an in-process APScheduler alternative
    to OS-level cron/Task Scheduler.
  - **utils/** — cross-cutting helpers: logging re-export, text/date/salary
    parsing helpers, and pydantic validators.
  - **main.py** — orchestrates the full pipeline end-to-end.
- **templates/** — Jinja2 templates for the CV and cover letter documents
  (rendered, then written out as .docx via python-docx).
- **data/** — runtime output, gitignored except `.gitkeep` placeholders:
  `raw/` (optional raw scrape dumps for debugging), `cvs/`, `cover_letters/`,
  `reports/`, `screenshots/` (from `ScreenshotManager`), `downloads/` (from
  `DownloadManager`), `sessions/` (from `SessionManager` — contains
  authentication cookies, never commit), `cache/ai_matches/` (from
  `ai.cache.MatchCache` — cached LLM analyses), `documents/<type>/` (from
  `documents.ExportManager` — exported Markdown/TXT drafts, personal content,
  never commit), plus two coexisting candidate-profile templates:
  `candidate_profile.example.json` (flat schema, used by `ai.profile_builder`
  for matching) and `candidate_profile_full.example.json` (rich schema, used
  by `job_automation.profile` — see docs/CANDIDATE_PROFILE.md for why there
  are two).
- **scripts/** — thin CLI entry points: `init_db.py`, `run_scraper.py`,
  `generate_applications.py`, `run_daily_report.py` (each pipeline stage runs
  and schedules independently, not as one monolithic job),
  `verify_browser_framework.py` (smoke test for `core/`), and
  `verify_scraper_framework.py` (runs `DummyScraper` against a local fixture
  site — the smoke test for `scrapers/base/`).
- **tests/** — pytest suite (`pytest` actually installed and running, not
  just a stub): `test_nhs_scraper.py`, `test_ai_matching.py`,
  `test_document_generation.py`, and `test_application_workflow.py` (the
  latter three each with their own `FakeLLMProvider`, a minimal in-test
  `LLMProvider` double — no real Anthropic API calls are made anywhere in
  this suite), `test_candidate_profile.py`, shared fixtures in
  `conftest.py` (in-memory SQLite `db_session`, a Playwright `browser`/`page`
  pair, and `nhs_fixture_url`), `dummy_scraper.py`, `fixture_server.py`
  (generic local-HTTP fixture server, shared by the dummy-scraper and NHS
  fixture sets), and `fixtures/dummy_site/` + `fixtures/nhs/` +
  `fixtures/profile/` (static files used only to verify the frameworks —
  never a real website). `test_scrapers/`, `test_database/`, `test_ai/`,
  `test_deduplication/` remain empty stub packages (tests so far live at
  the top level of `tests/` instead, mirroring the milestone that produced
  them rather than the package structure).
- **logs/** — rotating log files, written by `config.logging_config`
  (`app.log`, rotated daily, retained 14 days).
- **docs/** — this file, plus [BROWSER_FRAMEWORK.md](BROWSER_FRAMEWORK.md),
  [SCRAPER_FRAMEWORK.md](SCRAPER_FRAMEWORK.md), [NHS_SCRAPER.md](NHS_SCRAPER.md),
  [AI_MATCHING.md](AI_MATCHING.md), [CANDIDATE_PROFILE.md](CANDIDATE_PROFILE.md),
  [DOCUMENT_GENERATION.md](DOCUMENT_GENERATION.md), and
  [APPLICATION_WORKFLOW.md](APPLICATION_WORKFLOW.md).

## Database schema

Every table has a UUID primary key (`id`, client-generated via
`uuid.uuid4()` so it's known before INSERT) and `created_at`/`updated_at`
timestamps, from `UUIDPKMixin`/`TimestampMixin`. Enum columns (`ApplicationStatus`,
`JobType`, etc.) are stored as `VARCHAR` (`native_enum=False`), not a native
DB enum type, to avoid `ALTER TYPE` migrations every time an allowed value
changes.

```
Employer 1──* Job 1──* Application *──1 User 1──* CV
                │            │  \            │
                │            │   *──1 CoverLetter
                │            │
                *            *──* Interview
           JobMatch *──1 User    (via Application)

User 1──* JobAlert
User 1──* Certificate
User 1──* ActivityLog
Job  1──* JobMatch
ScraperRun  (standalone — no relationships)
```

- **User** — a candidate/account. Owns `cvs`, `applications`, `job_alerts`,
  `activity_logs`, `job_matches`, `certificates`. Deleting a `User` cascades
  to all of these (`ondelete="CASCADE"` + `cascade="all, delete-orphan"`) —
  they're all personal data with no meaning without the owner.
- **Employer** — an organization posting jobs. Owns `jobs`; deleting an
  `Employer` cascades to its `Job` rows (as long as no `Application`
  references them — see below).
- **Job** — one scraped listing, `employer_id` FK to `Employer`. Has a
  `(source_site, external_id)` unique constraint (exact-duplicate detection)
  and an indexed `content_hash` (for near-duplicate/repost detection) — both
  now actually used by `JobRepository`/`JobIngestionService` (see
  [docs/NHS_SCRAPER.md](NHS_SCRAPER.md)). `applications` and `job_matches`
  are the reverse relationships. **`Job` cannot be deleted while an
  `Application` references it** (no cascade, no `ondelete` — this was
  verified: attempting it raises `IntegrityError`), so application history
  outlives the listing. Deleting a `Job` does cascade to its `JobMatch` rows,
  which have no independent value once the job is gone. Gained `band`,
  `contract_type`, `working_pattern`, `closing_date`, `requirements` (JSON),
  `benefits` (JSON), and `salary_raw` in the NHS Jobs scraper milestone.
- **Application** — the central record: `job_id`, `user_id` (both required),
  plus optional `cv_id`/`cover_letter_id`. `(job_id, user_id)` is unique — one
  application per user per job. If the linked `CV` or `CoverLetter` is
  deleted, the FK is set to `NULL` (`ondelete="SET NULL"`) rather than
  deleting the application — verified by deleting a `CV` and confirming the
  `Application` row survived with `cv_id = NULL`. Owns `interviews`
  (cascade-deleted with the application).
- **CV** — a generated CV file, owned by a `User` (`user_id`, cascade on user
  delete). One `CV` can be attached to multiple `Application` rows, so the FK
  lives on `Application`, not here.
- **CoverLetter** — a generated cover letter file. Per the given schema, it
  has **no direct `User` relationship** (unlike `CV`) — only `applications`.
  This means a cover letter's owner is only reachable via
  `cover_letter.applications[0].user`, and a `CoverLetter` row is expected to
  be created alongside the `Application` it belongs to. Flagged here as a
  deliberate literal reading of the spec; add a `user_id` FK the same way
  `CV` has one if standalone cover-letter management is needed later.
- **Certificate** — a candidate's certification (DBS check, Manual Handling,
  etc.), mirroring the `certifications` list in
  `data/candidate_profile.example.json`. **Not in the original relationship
  list** — added `user_id` + a `User.certificates` back-reference as the one
  necessary addition, since a certificate with no owner isn't a usable
  record.
- **Interview** — belongs to one `Application` (`application_id`, cascade on
  application delete). Has `interview_type` (phone/video/in_person) and
  `outcome` (scheduled/passed/failed/cancelled/no_show).
- **ActivityLog** — a generic audit-trail row (`action` + free-text
  `details`). `user_id` is **nullable** so system-level events (e.g. a
  scraper run finishing) can be logged without a user attached; cascades on
  user delete when one is set.
- **ScraperRun** — one row per scraper execution (`source_site`, `status`,
  `started_at`/`finished_at`, `jobs_found`, `new_jobs_saved`,
  `error_message`). Standalone by design — no relationships — it's an
  operational log, not domain data.
- **JobAlert** — a saved search (`keywords`, `location`, `frequency`) owned
  by a `User`, cascade on user delete.
- **JobMatch** — a computed relevance score between one `Job` and one `User`
  (`match_score`, `matched_keywords`, `status`). `(job_id, user_id)` is
  unique — re-running matching updates the row instead of duplicating it.
  Cascades on either the `Job` or the `User` being deleted.

All of the above (relationship resolution, every `ON DELETE` rule, and the
`Job`-can't-be-deleted-with-open-applications protection) was verified with a
real insert/query/delete smoke test against the SQLite dev database before
the test rows were cleaned up.

## Browser automation framework

`job_automation.core` is the reusable Playwright infrastructure every future
scraper/login/application-submission flow is built on — no site-specific
logic lives here. Full design, lifecycle, and extension points are in
[docs/BROWSER_FRAMEWORK.md](BROWSER_FRAMEWORK.md); in short:

- **BrowserConfig** — immutable settings object, built from `Settings` via
  `.from_settings()`; every other component takes one in its constructor
  instead of reading global config itself.
- **BrowserExceptions** — a `BrowserAutomationError` hierarchy; a
  `TransientError` marker mixin tells `RetryManager` what's safe to retry.
- **BrowserManager** — owns the Playwright driver + Chromium `Browser`
  lifecycle; a context manager (`with BrowserManager(config) as bm:`).
- **ContextManager** — creates configured `BrowserContext`s (viewport, UA,
  proxy, storage state) and can persist/close them.
- **SessionManager** — built on `ContextManager`; saves/reloads
  authenticated sessions per site name with time-based expiry.
- **PageManager** — open/close pages, retrying rate-limited navigation, and
  "safe" click/type/scroll/element-exists helpers that log-and-degrade
  instead of raising.
- **RetryManager** — generic exponential-backoff executor, retries only
  `TransientError`s.
- **RateLimiter** — randomized delay between actions (human-like timing).
- **DownloadManager** — saves a Playwright `Download`, dedupes filenames,
  verifies the file landed.
- **ScreenshotManager** — captures a page screenshot on demand; used
  automatically by `PageManager` on navigation/click/type failure and
  unexpected dialogs, into `data/screenshots/`.

Verified end-to-end by `scripts/verify_browser_framework.py`: launches
Chromium, opens a context/page, navigates to `https://example.com`,
screenshots it, and tears everything down — confirmed no leftover Chromium
processes after shutdown.

## Scraper framework

`job_automation.scrapers.base` sits one layer above `core/`: it knows the
*shape* of a scraping problem (search, paginate, parse, optionally log in
and apply) without knowing any real site's markup. Full design, class
diagram, and an extension guide for adding a real scraper are in
[docs/SCRAPER_FRAMEWORK.md](SCRAPER_FRAMEWORK.md); in short:

- **ScraperConfig** — composes `BrowserConfig` (no duplicated fields) plus
  one addition: `max_pages`, a pagination safety cap.
- **ScraperExceptions** — `ScraperError` extends `core`'s
  `BrowserAutomationError`; `SearchError`/`PaginationError` reuse `core`'s
  `TransientError` marker, `LoginError`/`ParsingError`/
  `ApplicationSubmissionError` don't (fail fast, don't retry).
- **ScraperRegistry** — `register()`/`unregister()`/`get()`/`list()`, plus
  automatic registration: `BaseScraper.__init_subclass__` registers every
  concrete subclass the moment it's defined.
- **BaseParser** — abstract `parse()` (site-specific selectors) into a typed
  `ParsedJob`; concrete `parse_all()` skips and logs one bad card instead of
  aborting the page.
- **BaseSearch** — typed `SearchCriteria` (keywords/location/filters/sort);
  abstract query-building, concrete `search()` template method.
- **BasePaginator** — abstract next/previous-page detection/navigation;
  concrete `is_last_page()`/`next()`/`previous()` combine that with the
  `max_pages` safety cap.
- **BaseLogin** — abstract `login()`/`logout()`/`session_valid()`; concrete
  `restore_session()`/`save_session()` delegate to `core`'s `SessionManager`.
- **BaseApplication** — abstract upload/answer/submit/confirm steps;
  concrete `apply()` template method orchestrates them in order.
- **BaseScraper** — the composition root: browser lifecycle, logging, retry
  integration, rate limiting, session loading, error handling, and cleanup,
  built from `core/` components (each constructor-injectable, defaulted from
  config). Does not itself hold Login/Search/Paginator/Application instances
  — a concrete scraper composes whichever of those it needs.

Verified by `scripts/verify_scraper_framework.py` running `DummyScraper`
(`tests/dummy_scraper.py`) against a static local fixture site
(`tests/fixtures/dummy_site/`) — registration, browser launch, search,
parsing (including skip-on-malformed-card), pagination (both natural
last-page detection and the `max_pages` cap), login, application, and
cleanup all confirmed working, with no real website ever touched.

## NHS Jobs scraper

The first site-specific scraper (`job_automation.scrapers.nhs`), built
entirely by composing `core/` and `scrapers/base/` — no new browser,
retry, or rate-limiting logic. Full design in
[docs/NHS_SCRAPER.md](NHS_SCRAPER.md); in short:

- **Compliance-driven scope**: NHS Jobs' terms and conditions restrict
  automated reproduction of site content, so this milestone verifies
  **only** against local static HTML fixtures (`tests/fixtures/nhs/`) — the
  live site was never scraped, and the code's selectors are an unverified
  best-effort until a compliant access path exists.
- **`nhs_urls.py` / `nhs_search.py` / `nhs_parser.py` / `nhs_login.py` /
  `nhs_scraper.py`** — `NHSSearch`/`NHSPaginator` (colocated) support all 8
  required search facets via `SearchCriteria.filters`; `NHSParser` parses in
  two phases (search-card summary, then a detail-page visit for
  description/requirements/benefits/employer URL); `NHSLogin` exists but is
  unverified/untested (no login fixture this milestone); `NHSScraper`
  composes all of it plus `JobIngestionService` and tracks `ScrapeStats`.
- **New repository/service layer** (`database/repositories/`,
  `database/services/`) — didn't exist before this milestone; built now,
  scoped to job persistence: dedup by `(source_site, external_id)` or `url`,
  insert-or-update, never a duplicate row.
- Verified by 6 pytest tests (`tests/test_nhs_scraper.py`) covering parser
  field extraction + malformed-card skipping, detail enrichment, pagination
  (natural last-page + `max_pages` cap), duplicate detection, and a full
  scraper run persisting 3 jobs then updating the same 3 on a second run.

## AI matching engine

`job_automation.ai` evaluates jobs already stored in the database against a
candidate profile — no scraping, no application submission. Full design,
scoring algorithm, and extension points in
[docs/AI_MATCHING.md](AI_MATCHING.md); in short:

- **Pipeline**: `RuleEngine` (deterministic, free, always runs) + optional
  LLM semantic analysis (cached, only called when no cached result exists)
  → `ScoreCalculator` blends both into one `MatchResult` (`overall_score`,
  `confidence_score`, per-category scores, strengths/weaknesses/missing
  requirements/recommended actions). Works correctly with no LLM configured
  at all — never produces an empty or crashed result.
- **`LLMProvider`** — a one-method abstraction (`AnthropicProvider` is the
  first implementation); `MatchingEngine` never depends on a concrete
  provider, only the interface, which is what lets a second provider be
  added later with zero changes to the engine itself.
- **`MatchCache`** — disk-backed (`data/cache/ai_matches/`), caches the LLM
  analysis specifically (not the cheap, deterministic rule scores), keyed by
  a hash of the candidate profile + job content + prompt version.
- **New schema**: `Job.visa_sponsorship` and `JobMatch.analysis` (JSON) —
  migration `e167f5bf7f2e`.
- **`CandidateProfile`** is built from `data/candidate_profile.json`
  (extended with new preference fields), not a new database table — see
  docs/AI_MATCHING.md for why.
- Verified by 22 pytest tests (`tests/test_ai_matching.py`), all using a
  `FakeLLMProvider` test double — no real Anthropic API calls made anywhere.

## Candidate Profile Intelligence

`job_automation.profile` builds the complete structured candidate
representation used as the source of truth for AI document generation —
`documents/` (below) now consumes it for supporting statements and cover
letters; CV-*file* generation remains unbuilt. No scraping, no browser
automation, no application logic. Full design, schema, and validation rules
in [docs/CANDIDATE_PROFILE.md](CANDIDATE_PROFILE.md); in short:

- **`CandidateProfile`** — a richer, separate value object from
  `ai.matching_models.CandidateProfile` (see that doc for why the two
  coexist rather than being merged this milestone): personal information,
  education, employment history (with a *derived* `healthcare_experience`
  view, not a separately-maintained list), skills, certificates,
  professional registration (NMC/HCPC/GMC), languages, visa status, and
  preferences.
- **Three real loaders** (`ProfileLoader` ABC): JSON, YAML, and a
  structured Markdown format, all converging on the same intermediate dict
  shape via `CandidateProfile.from_dict()`. Two more (PDF, DOCX) are
  real classes implementing the interface today but raise
  `NotImplementedError` — a deliberate "future interface," not an
  oversight, so implementing them later requires no caller-side changes.
- **`CVParser`** — a separate concern from the loaders above: parses a real,
  prose-style CV (not written with this system's schema in mind) into
  employment history, education, skills (including skills only mentioned in
  job responsibilities, not an explicit list), and certificates, via shared
  per-section parsers (`EmploymentHistoryParser`, `EducationParser`,
  `CertificateParser`) also reused by the Markdown loader.
- **`SkillsExtractor`** — normalizes free text against a fixed 15-tag UK
  healthcare skills taxonomy.
- **`ProfileValidator`** — flags missing skills/employment/qualifications/
  certificates and real contradictions (visa status, expired-but-active
  professional registration) as warnings/errors; never blocks anything.
- **New schema**: `candidate_profiles` table (`CandidateProfileRecord`, one
  JSON blob per user) — migration `6b90ec850793`. `ProfileRepository`
  follows the same repository pattern as `database.repositories`, just
  located inside `profile/` per this milestone's file list.
- Verified by 25 pytest tests (`tests/test_candidate_profile.py`) covering
  all three loaders (including JSON/YAML producing an equal profile), CV
  parsing, skill normalization, employment analysis, validation rules, the
  preference engine, and repository persistence.

## Document Generation Intelligence

`job_automation.documents` generates reviewable **draft** application
documents — never submits, sends, or applies to anything. Full design,
prompt strategy, and validation rules in
[docs/DOCUMENT_GENERATION.md](DOCUMENT_GENERATION.md); in short:

- **Reuses three existing subsystems rather than rebuilding them**:
  `profile.CandidateProfile` for who the candidate is,
  `ai.matching_models.JobSnapshot`/`MatchResult` for the job and match
  context, and `ai.llm_provider.LLMProvider` (the same abstraction
  `AnthropicProvider` already implements for the matching engine) for
  actually generating text. No new LLM provider abstraction, no new retry
  logic.
- **Three generators** (`SupportingStatementGenerator`,
  `CoverLetterGenerator`, `ApplicationAnswerGenerator`), each requiring an
  `LLMProvider` (not optional — unlike scoring, there's no meaningful
  non-LLM way to draft prose). A shared grounding-rules prompt preamble
  implements "avoid inventing experience not in the profile" and the
  required NHS/care-values theme list (safeguarding, communication,
  teamwork, confidentiality, dignity, person-centred care).
- **`DocumentValidator`** — the deterministic backstop for when a prompt
  instruction isn't followed: flags mentions of certificates/professional
  registrations not on the candidate's actual profile, and years-of-
  experience claims inconsistent with `profile.employment_history
  .total_years_of_experience()` (reused, not reimplemented).
- **Human-review workflow**: every generated document is `DRAFT` or
  `NEEDS_REVIEW` (never auto-approved); `DocumentService.approve()`/
  `.reject()` are the only way status changes, always an explicit human
  decision.
- **New schema**: `generated_documents` table (`GeneratedDocumentRecord`) —
  migration `43d2f89f498d`. Deliberately separate from the existing
  `CoverLetter`/`CV` models (finished files attached to an `Application`) —
  see docs/DOCUMENT_GENERATION.md for the distinction and a future
  connection point.
- **`ExportManager`** writes Markdown and TXT drafts to
  `data/documents/<type>/`, collision-safe, mirroring
  `core.download_manager.DownloadManager`'s pattern.
- Verified by 17 pytest tests (`tests/test_document_generation.py`), all
  against a `FakeLLMProvider` test double — no real LLM calls made.

## Application Workflow Management

`job_automation.workflows` manages the internal journey from a matched job
through human review to ready-to-apply and beyond — **no automatic
application submission anywhere in this package**. Full design, state
machine, and testing detail in
[docs/APPLICATION_WORKFLOW.md](APPLICATION_WORKFLOW.md); in short:

- **10 statuses**, one explicit state machine
  (`application_workflow.ApplicationWorkflow`): `NEW_MATCH` →
  `DOCUMENTS_GENERATED` → `NEEDS_REVIEW` → `APPROVED`/`REJECTED` →
  `READY_TO_APPLY` → `APPLIED` → `INTERVIEW` → `OFFER` → `CLOSED`.
  `REJECTED` loops back to `DOCUMENTS_GENERATED` (regenerate and retry);
  `CLOSED` is reachable from every other status and is terminal.
- **Three layered files for one concern**: `application_workflow.py` (the
  rules — pure, no I/O), `status_manager.py` (the mechanism — performs a
  validated transition + records history), `review_service.py` (the
  business meaning of an approve/reject decision, built on the other two).
- **`ChecklistService`** computes a 5-item ready-to-apply checklist
  (supporting statement approved, cover letter approved, certificates on
  file, visa status confirmed, no document awaiting approval), always
  considering only the *latest* version of each document type — reuses
  `documents.document_models.DocumentType`/`DocumentStatus` directly.
- **Document version tracking comes free**: `GeneratedDocumentRecord`
  never updated in place (new row per generation) plus a new
  `workflow_id` FK on it means querying by workflow already returns full
  version history — no new versioning mechanism needed.
- **Status history and audit logs get their own tables**
  (`workflow_status_history`, `workflow_audit_logs`) rather than JSON
  blobs — a deliberate departure from the JSON-blob pattern used elsewhere,
  since an append-only audit trail should be immutable and independently
  queryable, not part of a mutable parent row.
- **Preventing automatic submission**: exactly one method,
  `WorkflowService.mark_applied()`, can reach `APPLIED`, and it must be
  called explicitly — no scraper, browser-automation, or scheduled-job code
  anywhere in this codebase calls it.
- **Bug found and fixed while verifying this milestone**: a real network
  hiccup surfaced a latent `TypeError` in `core.retry_manager.RetryManager`
  (present since the Browser Framework milestone, never triggered before)
  — fixed by matching retryable exceptions with `isinstance()` instead of a
  bare `except (TransientError,)` clause. See
  docs/APPLICATION_WORKFLOW.md and docs/BROWSER_FRAMEWORK.md for detail.
- Verified by 16 pytest tests (`tests/test_application_workflow.py`)
  covering the state machine rules, the checklist, a full match-to-closed
  journey with exact status-history assertions, the reject-and-regenerate
  loop, audit logging, and persistence (including that deleting a workflow
  `SET NULL`s its documents' `workflow_id` rather than deleting the drafts).

## Web Dashboard

`job_automation.web` is a FastAPI + Jinja2 + HTMX + Bootstrap 5 dashboard
that consumes every subsystem built in the previous eight milestones — it
adds no business logic of its own beyond two small, explicitly-justified
exceptions. **No authentication, no deployment configuration, no automatic
application submission** — all explicitly out of scope this milestone.
Full design, page-by-page detail, and every architectural trade-off in
[docs/DASHBOARD.md](DASHBOARD.md); in short:

- **9 HTML pages** (`routes/`) and **4 JSON API files** (`api/`) covering
  8 conceptual resource areas — matches live with jobs, applications live
  with workflow, since a "match" and an "application" are each inherently
  scoped to (job) and (workflow-at-a-later-stage) respectively rather than
  separate resources.
- **Reuses every existing service/repository exactly as-is**:
  `MatchingService`, `WorkflowService`, `DocumentService`, `ProfileService`,
  `JobRepository`, `JobMatchRepository`, `DocumentRepository`,
  `WorkflowRepository`. `JobRepository.search()`/`JobFilter` and
  `JobMatchRepository.get()`/`list_for_user()` were added *additively* for
  the Jobs/Matches pages — no existing method changed.
- **Two genuinely new pieces of backend logic**, both explicitly called
  out rather than hidden as "just reuse": `job_automation.analytics`
  (`AnalyticsService` — no analytics capability existed anywhere before
  this milestone) and the additive `JobRepository.search()` extension.
- **No `ApplicationRepository`**: an "application" is an
  `ApplicationWorkflowRecord` at `READY_TO_APPLY` or later — the workflow
  subsystem already *is* the application-tracking mechanism, with
  interview date/offer/notes all derived from `WorkflowStatusHistoryRecord`
  via one shared function (`routes/applications.py`'s
  `build_application_rows()`) rather than duplicated per caller.
- **`_NeverCalledLLMProvider`**: `DocumentService`'s constructor requires
  an `LLMProvider` even for actions (approve/reject/export) that never call
  one — a stub that raises if ever actually invoked avoids requiring a
  configured Anthropic API key just to approve an already-generated
  document, a real bug this milestone found and fixed in its own new code
  before it shipped.
- **Settings vs. Candidate Profile preference-editing split**: one 9-field
  `CandidatePreferences` dataclass, edited from exactly one page (Settings)
  to avoid two forms silently clobbering each other's fields; Candidate
  Profile displays preferences read-only instead.
- Verified by 33 pytest tests (`tests/test_web_dashboard.py`) using
  `TestClient` against an in-memory database and a `FakeLLMProvider`
  override, plus manual end-to-end verification with
  `scripts/seed_demo_data.py` and a real `uvicorn` server. The full
  pre-existing suite (86 tests) still passes unmodified — 119 total, zero
  regressions.

## Authentication and User Accounts

`job_automation.auth` plus session wiring in `web/app.py` and new routes
in `web/routes/auth.py` turn the dashboard into a real multi-user system:
registration, login, logout, and per-user data isolation enforced on every
route. **No API key integration, background jobs, notifications,
deployment, or automatic application submission** — all explicitly out of
scope this milestone. Full design in
[docs/AUTHENTICATION.md](AUTHENTICATION.md); in short:

- **This was possible with almost no changes to the rest of the
  codebase**: every table has been keyed by `user_id` since the first
  database milestone, and every route/service already scoped queries by
  `current_user.id`. The only production-code change needed was
  replacing `get_current_user()`'s old "first `User` row in the database"
  placeholder with a real session-cookie lookup — no repository, service,
  or template had to change to become multi-user-safe, since they
  already were.
- **`job_automation.auth`** (4 files): `PasswordHasher` (bcrypt via
  passlib), `AuthService` (register/authenticate, including a
  timing-safe/identical-message rejection for both "unknown email" and
  "wrong password", to resist email enumeration), `session_store.py`
  (thin helpers around Starlette's signed `SessionMiddleware` cookie —
  stores only `user_id`, nothing else).
- **Two "current user" dependencies, one check**: `get_current_user`
  (all 9 HTML route files, unchanged call sites) redirects to `/login` on
  a missing session; `get_current_api_user` (all 4 API files, updated
  this milestone) returns `401` instead, since redirecting a JSON/HTMX
  caller to an HTML login page is worse than a clear error it can detect.
- **New `User.hashed_password` column** (migration `45e851e801ec`) — the
  dev database was wiped and re-migrated from scratch (same precedent as
  the Application Workflow milestone's FK-migration recovery), since no
  real user data existed to preserve.
- **`scripts/seed_demo_data.py`** now creates its demo user through
  `AuthService.register()` (real bcrypt hash) instead of constructing a
  `User` row directly, and prints working demo login credentials.
- Verified by 33 new pytest tests (`tests/test_authentication.py`) using
  **no** dependency overrides — real registration, login (correct/wrong
  password/unknown email), logout, every protected page/API route
  redirecting/401-ing when unauthenticated, an open-redirect attempt via
  `?next=` correctly rejected, and two independently-registered users
  proven to have fully isolated profiles/workflows/documents. The
  existing `tests/test_web_dashboard.py` was updated (one obsolete
  "no user -> 500" test removed, its real behavior now covered for real in
  `test_authentication.py`) rather than rewritten — full suite: 152
  tests, zero regressions.

## Background Job Scheduler

`job_automation.scheduler` runs 5 safe, internal automation tasks on a
schedule (APScheduler) or on demand (dashboard "Run now" buttons), with
full status history, retries, and per-task locking. **No Anthropic API
key, no notifications, no deployment, no automatic application
submission, no live scraping** — every task uses local/fixture data and a
fake LLM provider. Full design in
[docs/BACKGROUND_SCHEDULER.md](BACKGROUND_SCHEDULER.md); in short:

- **The 5 tasks**: `import_fixture_jobs` (a committed JSON fixture, never
  a live site), `run_ai_matching` (via `SchedulerFakeLLMProvider`),
  `generate_draft_documents`, `update_workflow_statuses`,
  `cleanup_old_logs` (prunes the scheduler's own history table, not
  `app.log` — loguru already handles that rotation).
- **`SchedulerService`** is the orchestrator: one `threading.Lock` per
  task name ("the same task cannot run twice at the same time"), retries
  via `core.RetryManager` reused as-is (`retry_on=(Exception,)`, since
  scheduler tasks have no browser/LLM-specific transient/permanent
  distinction), and full history via a new `SchedulerTaskRunRecord` table
  (`PENDING`/`RUNNING`/`SUCCESS`/`FAILED`/`SKIPPED`). One shared instance,
  resolved via a `get_scheduler_service()` FastAPI dependency (not
  imported as a bare singleton), serves the manual HTML button, the JSON
  API, and the periodic trigger alike.
- **Never crosses the "auto-approve" / "no automatic submission"
  boundary**: `generate_draft_documents` calls the exact same
  `DocumentService.generate_supporting_statement()` a human-requested
  document uses (always `DRAFT`/`NEEDS_REVIEW`, always needs explicit
  approval); `update_workflow_statuses` only ever creates a workflow at
  `NEW_MATCH`, never calling `approve()`/`mark_applied()`/etc. Directly
  regression-tested.
- **Off by default**: `settings.scheduler_enabled=False` means importing
  the app (including under `pytest`) never starts a real background
  thread; manual "Run now" works regardless.
- **Real bug found and fixed during testing**: `SchedulerRepository`
  originally stamped timestamps with `datetime.now(timezone.utc)`
  (aware), but SQLite has no real timezone-aware storage — values come
  back naive on read, and `cleanup_old_logs`'s bulk delete raised
  `TypeError: can't compare offset-naive and offset-aware datetimes`.
  Fixed with a shared naive-UTC `utc_now()` helper, matching how the rest
  of this codebase already handles timestamps.
- Verified by 27 new pytest tests (`tests/test_scheduler.py`) covering
  successful/failing/retrying runs, locking, each task function
  individually (including the never-auto-approves regression test), and
  the dashboard page/API (including that both correctly require
  authentication). Full suite: 179 tests, zero regressions.

## Notification & Event System

`job_automation.notifications` lets every important action generate an
in-app notification, via a lightweight in-process event bus rather than
existing modules calling `NotificationService` directly. **Only in-app
notifications are implemented** — email/SMS/push are interface-only
placeholders that raise `NotImplementedError`. **No Anthropic API key, no
deployment, no automatic application submission.** Full design in
[docs/NOTIFICATIONS.md](NOTIFICATIONS.md); in short:

- **New `Notification` model** (migration `bfbc84b1875c`) — `user_id` is
  nullable (system-wide notifications, e.g. a scheduler failure, are
  visible to every user via an `OR user_id IS NULL` read clause); the
  Python attribute is `metadata_` since `metadata` is reserved on every
  SQLAlchemy declarative model (shadows `Base.metadata`) — the actual
  column is still named `metadata`.
- **`EventBus`** (`event_bus.py`): synchronous, in-process
  publish/subscribe, matching this app's single-process architecture (the
  same reasoning already applied to the Background Scheduler milestone's
  in-process locking). A subscriber's failure is caught and logged, never
  propagated — a notification bug must never break the business operation
  that published the event.
- **9 event types** (`SCHEDULER_TASK_STARTED/FINISHED`, `JOB_IMPORTED`,
  `MATCH_COMPLETED`, `DOCUMENT_GENERATED`, `WORKFLOW_UPDATED`,
  `USER_REGISTERED`, `USER_LOGGED_IN`, `ERROR_OCCURRED`), published from
  `SchedulerService`, the two relevant scheduler tasks, `DocumentService`,
  `StatusManager` (the one choke point every workflow transition in the
  app funnels through), and `AuthService` — none of which import
  `NotificationService`; only `notification_listeners.py` does.
- **Listener registration is automatic**: `notifications/__init__.py`
  registers its listeners on the shared `event_bus` singleton at import
  time, since every publisher already imports that singleton and
  importing any submodule of a package executes its `__init__.py` first.
  This means standalone scripts (`scripts/seed_demo_data.py`) generate
  real notifications with zero notification-specific wiring — verified
  directly: running the seed script produces 15 real `Notification` rows.
- **"Errors occur" scoped deliberately**: rather than build a new
  app-wide exception-handling mechanism (real scope creep for a milestone
  told to stop after in-app notifications), `ERROR_OCCURRED` is published
  from the one well-defined, already-tested failure path that exists —
  `SchedulerService`'s retry-exhausted failure. No notification is
  published on a failed login (no legitimate `user_id` to notify yet).
- **Dashboard**: a navbar bell + unread badge (HTMX polling a dedicated
  HTML-fragment route, not the JSON API), a `/notifications` page, and
  `api/notifications_api.py`. Mark read/mark-all-read are plain HTML form
  POSTs with a redirect (matching `routes/documents.py`/`routes/scheduler.py`'s
  existing mutation pattern), kept separate from the JSON API's equivalent
  endpoints rather than layering one on top of the other.
- Verified by 37 new pytest tests (`tests/test_notifications.py`)
  covering the service, the event bus (including failure isolation and
  idempotent registration), all 4 providers, every integration hook
  individually, and the dashboard page/API. Full suite: 217 tests, zero
  regressions.

## Job Management

`job_automation.job_organization` turns the dashboard into a personal job
management platform: save/favourite/hide/archive jobs, track them through a
Kanban pipeline, attach notes/ratings/priority/deadlines/tags/checklists,
and set reminders. **No Anthropic API, no live NHS scraping, no TRAC
integration, no deployment, no automatic application submission, no
email/SMS/push notifications.** Full design in
[docs/JOB_MANAGEMENT.md](JOB_MANAGEMENT.md); in short:

- **`PipelineStage` is a deliberately new enum, not a reuse of
  `WorkflowStatus`** — the milestone's central architecture decision.
  `WorkflowStatus.REJECTED` means "a reviewer rejected the drafted
  document, loop back and regenerate" (non-terminal); this milestone's
  "Rejected" Kanban column means "the employer rejected the application"
  (terminal). Reusing one enum for both would make two different
  real-world facts indistinguishable by their stored status. `PipelineStage`
  /`JobPipeline` reuse `ApplicationWorkflow`'s exact *pattern* (a frozen
  `ALLOWED_TRANSITIONS` dict + validate/raise helpers) without reusing its
  code, since the two state machines protect different invariants.
- **New tables**: `saved_jobs` (one row per user/job — flags, pipeline
  stage, notes/rating/priority/deadline/interview date, tags, checklist)
  and `job_reminders` (belongs to a `saved_jobs` row). Migration
  `e9c269d9e55f`. Deliberately separate from `JobMatch`/
  `ApplicationWorkflowRecord` — a job can be tracked before any AI match or
  workflow exists.
- **`JobOrganizationService`/`ReminderService`** — the same
  repository/service/event-bus pattern as every prior milestone. Pipeline
  transitions publish `PIPELINE_STAGE_UPDATED`; due reminders publish
  `REMINDER_DUE` (via a 6th scheduler task, `send_due_reminders`) — flag
  toggles and detail edits deliberately publish nothing.
- **`JobRepository.search()`/`JobFilter` extended additively**: max
  salary, remote, closing soon/expired, keywords, and `user_id`-scoped
  saved/favourite/archived/pipeline-stage filters via a `LEFT OUTER JOIN`
  onto `saved_jobs` — every pre-existing filter still works identically
  with `user_id=None`.
- **New screens**: the Jobs list gets save/favourite/hide/archive buttons
  and new filters; the job detail page gets a full "Organization" panel
  (stage-move buttons limited to actually-valid next stages, tracking
  details, tags, checklist, reminders); a new `/board` Kanban page
  (button-based moves, not drag-and-drop — see docs/JOB_MANAGEMENT.md's
  "Known limitations"); the dashboard gains a "Job organization" section
  with stat cards, a pipeline-stage chart, and an activity feed that
  reuses the existing `NotificationService` rather than a second feed.
- Verified by 56 new pytest tests across three files (`test_job_organization
  .py`, `test_job_search.py`, `test_job_organization_web.py`), including a
  regression test proving `PipelineStage.REJECTED` and
  `WorkflowStatus.REJECTED` coexist independently for the same job. Full
  suite: 273 tests, zero regressions.

## Employer & Application CRM

`job_automation.employer_crm` turns `Employer` into a full personal CRM:
favourite employers, an NHS Trust's departments/locations, a recruiter
contact book, a combined notes+communication-history timeline, and
success-rate analytics per employer. **No Anthropic API, no live
scraping, no deployment, no automatic application submission, no real
email/SMS.** Full design in [docs/EMPLOYER_CRM.md](EMPLOYER_CRM.md); in
short:

- **Where "rejections" data comes from is the central decision**, mirroring
  the Job Management milestone's `PipelineStage`-vs-`WorkflowStatus` call.
  Applications sent/interviews/offers come from `WorkflowStatusHistoryRecord`
  (the same authoritative source already used account-wide). Rejections
  deliberately do **not** come from `WorkflowStatus.REJECTED` (that means "a
  reviewer rejected the drafted document," non-terminal, unrelated to the
  employer) — they come from `SavedJob.pipeline_stage ==
  PipelineStage.REJECTED`, the only place this schema actually records an
  employer rejecting a candidate. Proven by a dedicated regression test.
- **New tables**: `employer_profiles` (per-candidate favourite + visa notes,
  the employer-level counterpart to `SavedJob`), `employer_departments`
  (shared reference data — a Trust's departmental structure), `employer_
  contacts` (per-candidate recruiter contact book), `employer_activity_log`
  (per-candidate notes + communication history combined into one timestamped
  timeline via an `entry_type` discriminator). Migration `161f6cd3b296`.
- **One `EmployerCrmService`**, not several — every CRM entity here only
  ever changes in response to a candidate editing an employer's profile
  page, one cohesive use case (unlike Job Management's save-flags/reminders
  split, which needed two services because reminders have an independent
  scheduler-driven lifecycle).
- **`AnalyticsService`/`EmployerRepository` extended, not duplicated** — new
  `employer_outcome_summary()`/`list_employer_outcome_summaries()` methods
  and an `EmployerFilter`/`search()` extension following the exact
  `JobFilter`/`JobRepository.search()` pattern (optional `user_id`-scoped
  `LEFT OUTER JOIN` for `favourite_only`).
- **New screens**: `/employers` (search/filter list), `/employers/{id}`
  (full profile: favourite, visa notes, departments, contacts, activity
  timeline, analytics widgets), and a new "Employer CRM" dashboard section
  (stat cards + top-employers-by-volume table).
- Verified by 38 new pytest tests across two files (`test_employer_crm.py`,
  `test_employer_crm_web.py`), including the rejections-source regression
  test. Full suite: 311 tests, zero regressions.

## Interview & Calendar Management

`job_automation.interviews` adds interview scheduling, a status lifecycle,
a preparation checklist, categorized notes, reminders, and a calendar —
integrating with, but never automatically driving, the Application
Workflow, Employer CRM, Notifications, and Dashboard subsystems. **No
Anthropic API, no live scraping, no automatic application submission, no
deployment, no real email/SMS, no Outlook/Google Calendar sync, no
automatic interview scheduling.** Full design in
[docs/INTERVIEWS.md](INTERVIEWS.md); in short:

- **`InterviewRecord`, not `Interview`** — this codebase already has an
  unused `Interview` model tied to the original pre-workflow scaffold
  (`Application`/`CV`/`CoverLetter`, none of which anything real uses).
  The new model uses this project's established `...Record` suffix
  convention (`ApplicationWorkflowRecord`, `CandidateProfileRecord`) to
  avoid colliding with or resurrecting the dead one.
- **`interview_type` vs. `interview_stage` are deliberately independent
  fields** — type is the format/kind of session (phone/video/face-to-face/
  assessment centre/practical/informal chat/second interview/final
  interview, literally the milestone's 8 listed values); stage is which
  round of a multi-round process this interview represents. They can
  disagree in the data on purpose.
- **Workflow integration is opt-in and always explicit** —
  `InterviewService.sync_workflow_status()` calls the *existing*
  `WorkflowService.mark_interview()`/`.mark_offer()`/`.close()` only from
  a user-clicked button; nothing in scheduling/status-update/reschedule
  ever calls it automatically. Proven by a dedicated regression test.
- **New tables** (migration `52ad230e88b2`): `interview_records`,
  `interview_checklist_items` (10 default items seeded per interview),
  `interview_notes` (categorized: questions asked, my answers, recruiter
  feedback, things to improve, salary discussed, next steps, general),
  `interview_reminders` (mirrors `JobReminder`'s design exactly).
- **`scheduled_at` is always normalized to naive UTC** at the point of
  persistence, avoiding the naive/aware-datetime `TypeError` class this
  project has hit before, even though the JSON API accepts a
  timezone-aware `datetime` field a client could send.
- **A seventh scheduler task**, `send_due_interview_reminders` (5-minute
  interval — shorter than job reminders' 15 minutes, since interview
  reminders include a "30 minutes before" offset), following the exact
  `TaskDefinition`/`run(session) -> dict` pattern every other task uses.
- **`AnalyticsService` extended, not duplicated** — new
  `interview_analytics_summary()`/`employer_interview_stats()` methods
  built from the real `InterviewRecord` table, deliberately left separate
  from `EmployerOutcomeSummary.interviews` (a workflow-history-based proxy
  that predates real interview scheduling and stays exactly as it was).
- **New screens**: `/interviews`, `/interviews/{id}`, `/interviews/new`,
  and a new `/calendar` (one route, month/week/day views via a `view`
  query parameter). Cross-module integration: a new "Interviews" dashboard
  section, an employer profile "Interview history" section, and an
  "Interview" column on the Applications page with a pre-filled "Schedule"
  link.
- Verified by 46 new pytest tests across two files (`test_interviews.py`,
  `test_interviews_web.py`), including the never-automatic-workflow-sync
  regression test. Full suite: 357 tests, zero regressions.

## Anthropic AI Integration

Replaces the rule-based/fake-only AI path with a real `AnthropicProvider`
for five generation features — job match re-analysis, supporting statement,
cover letter, application answer, interview preparation, and missing-skills
analysis — while `FakeLLMProvider` stays the only provider any test ever
uses. **No deployment, no live scraping, no automatic application
submission, no API key in the database or the UI.** Full design in
[docs/ANTHROPIC_INTEGRATION.md](ANTHROPIC_INTEGRATION.md); in short:

- **Configuration is `.env`-only** (`settings.anthropic_*`) — model,
  timeout, retry count, per-million-token cost estimates. Missing key
  raises `LLMProviderError` in `AnthropicProvider`'s constructor, before
  any SDK client exists; `get_llm_provider()` (now in `web/app.py`, moved
  from `routes/documents.py` so every AI route can share it) turns that
  into a `503` with an actionable message rather than a crash or a silent
  fallback.
- **Retry and timeout reuse existing infrastructure** — `core
  .retry_manager.RetryManager` (the same class browser automation uses) and
  the Anthropic SDK's own `httpx` timeout, not reimplemented.
- **Cost/token logging is a log line, not a table** — every real completion
  logs model/token counts/estimated cost via loguru; an operational
  concern, not analytics data a page queries.
- **`AIResponseCache`** (`data/cache/ai_responses/`) — a new, generic
  sibling to the pre-existing `MatchCache`, keyed on a hash of `(kind,
  system_prompt, user_prompt)` rather than separately-fingerprinted
  ingredients, since the rendered prompt already encodes everything.
  `complete_with_cache()` is the shared wrapper all five generators call.
- **Two new `DocumentType` values** (`interview_prep`,
  `skills_gap_analysis`) reuse the entire existing `DocumentService`
  generate → validate → draft/needs-review → approve/reject → export
  pipeline — a plain-string column, no migration, no other code needed
  updating (`export_manager.py`/templates are already generic on
  `.document_type.value`).
- **Explicit-only triggers**: `POST /matches/{job_id}/rematch`, `POST
  /jobs/{job_id}/documents/generate`, `POST /interviews/{id}/generate-prep`
  — every real AI call originates from a user clicking a button; the
  background scheduler tasks (`run_ai_matching`, `generate_draft_documents`)
  are unchanged and still always use `SchedulerFakeLLMProvider`.
- **Dashboard AI status card** (`AnalyticsService.ai_status()`) —
  configured/not, model, and the percentage of the user's job matches
  actually scored by a real LLM call — computed entirely from existing
  data, no new usage-tracking table.
- Verified by a new `tests/test_anthropic_provider.py` (11 tests, the only
  file that touches `AnthropicProvider`/the `anthropic` package — via a
  mocked SDK client, never a real call), plus new coverage in
  `test_document_generation.py` and `test_web_dashboard.py`. Full suite:
  387 tests, zero real API calls anywhere.

## Job Ingestion Service

Replaces demo jobs with a real multi-provider job aggregator. Full design
in [docs/JOB_INGESTION.md](JOB_INGESTION.md); in short:

- **`job_automation.ingestion`** — new package, one `JobProvider`
  interface (`fetch_jobs(session) -> ProviderRunStats`) over five
  providers: `NHSProvider`/`TracProvider` wrap Playwright-based scrapers
  (`scrapers.nhs`, and the new `scrapers.trac` — a second site built on
  the same `scrapers.base` framework), `ReedProvider` calls Reed's public
  JSON API directly via `httpx` (no browser needed), and
  `IndeedProvider`/`TotalJobsProvider` are interface-only stubs
  (`fetch_jobs()` raises `NotImplementedError`) — both sites prohibit
  automated scraping and offer no comparable public API.
- **Normalization reuses `scrapers.base.ParsedJob`** unchanged — it
  already covered every field this milestone's requirements list, no new
  parallel dataclass needed.
- **Cross-source deduplication is new**: `JobIngestionService
  .save_parsed_job()` now falls back to a `content_hash`
  (title+employer+location) match against *any* source when the existing
  `(source_site, external_id)`/`url` check misses — catching the same
  real-world role independently re-posted across NHS Jobs/Trac Jobs/Reed,
  which the pre-existing same-source-only dedup couldn't.
- **AI matching is automatic on import; document generation stays
  explicit-click-only** — `ingestion.auto_match_service.process_new_jobs()`
  scores every newly-created job for every user (reusing
  `MatchingEngine`/`MatchingService` exactly as the manual "Re-run with
  AI" button does) and publishes `NEW_HIGH_MATCH_JOB`/`NEW_BAND3_JOB`/
  `NEW_SPONSORSHIP_JOB` notifications — but generating an actual document
  still requires a click on the job's detail page, preserving every prior
  AI-integration milestone's cost-control invariant even as import volume
  scales.
- **A new `Job.closing_soon_notified_at` column** (migration
  `0c09fb52cf5c`) backs `scheduler.tasks.check_closing_soon_jobs`, an
  hourly task notifying matched users once when a job newly falls within
  48 hours of closing.
- **`TaskDefinition` gained an optional `daily_at_hour` field** —
  `import_provider_jobs` runs once daily via a `CronTrigger`
  (`scheduler.job_scheduler.create_scheduler()`), not an `IntervalTrigger`
  like every other task, for a genuine "every morning" schedule.
  Every pre-existing task (which leaves this `None`) is unaffected.
- **Search/dashboard/analytics extended, not duplicated** — `JobFilter`
  gained a `source` field; `AnalyticsService` gained
  `job_ingestion_summary()` (jobs today/this week/by source, top employers
  by volume, latest jobs — dashboard) and `job_market_analytics()` (jobs
  by band/trust/location/salary/source/time — Analytics page), both
  deliberately account-wide like `JobOrganizationSummary`, not per-user
  like `DashboardSummary`.
- Verified against local fixtures only (no outbound internet access in
  this environment) — `tests/test_trac_scraper.py` (new Trac Jobs scraper,
  local HTTP fixture server, same pattern as `test_nhs_scraper.py`) and
  `tests/test_ingestion.py` (providers, cross-source dedup, orchestrator,
  auto-match notifications, both new scheduled tasks — Reed via
  `httpx.MockTransport`, never a live call). Full suite: 413 tests, zero
  real network calls anywhere.

## Production Readiness

Prepares the app to actually run somewhere other than a developer's
machine. Full design in [docs/DEPLOYMENT.md](DEPLOYMENT.md); in short:

- **`Settings.environment`** (`"development"` default, `"production"`,
  `"test"`) gates a new `@model_validator` (`_validate_production_config`)
  that refuses to start with `ENVIRONMENT=production` while
  `SESSION_SECRET_KEY` is still its insecure dev default or
  `SESSION_COOKIE_SECURE` is off — fail fast at process startup rather
  than silently serving an insecure configuration. Every other value this
  app can run without (no Anthropic/Reed key, SQLite instead of Postgres)
  stays a legitimate, non-fatal choice.
- **`GET /health`** (`web/app.py`) — unauthenticated, checks real database
  connectivity (`SELECT 1`), returns `{"status", "database",
  "environment"}` with `200`/`503`. What every platform in
  docs/DEPLOYMENT.md polls to decide whether a deploy is healthy.
- **Production logging** (`config/logging_config.py`) — `ENVIRONMENT=production`
  switches the console sink from colorized text to structured JSON on
  stdout (what Railway/Render/`docker logs` actually want to parse) and
  disables `diagnose` on both sinks, so an exception traceback never
  dumps local variable values (a secret-leak risk once real API
  keys/user data reach this app). `setup_logging()` is now actually
  called (`create_app()`'s first line) — previously it was defined but
  never invoked by the running app, so none of this configuration used to
  take effect outside of scripts. A new global `Exception` handler in
  `app.py` ensures a genuinely unexpected error still gets logged through
  loguru (not just the stdlib logger Starlette's own
  `ServerErrorMiddleware` would otherwise use) before returning a clean
  JSON 500.
- **PostgreSQL compatibility** — `db_manager.py`'s engine now sets
  `pool_pre_ping=True` (avoids stale-connection errors against a managed
  Postgres instance that closes idle connections). A genuine bug was
  found and fixed here: every `DateTime(timezone=True)` column (68
  columns across 26 tables, including the `TimestampMixin`
  `created_at`/`updated_at` every table has) is now plain `DateTime()` —
  on SQLite the two are indistinguishable, but on Postgres the
  `timezone=True` form returns a timezone-*aware* `datetime`, which would
  crash the instant it was compared against this app's naive-UTC-only
  convention (`utils.helpers.utc_now()`). Migration `c38bf18c2826` alters
  every affected column; verified via a full migration chain run
  (including a downgrade/upgrade round trip) and a schema-vs-model
  column-by-column diff, not just a visual read of the migration file.
- **Docker** — a `Dockerfile` (Python 3.13-slim, Playwright's Chromium
  installed at build time since NHS/Trac ingestion needs a real browser),
  `docker-entrypoint.sh` (runs `alembic upgrade head` before every
  container start), `.dockerignore`, and a `docker-compose.yml` for local
  app+Postgres testing. Not build-tested inside this sandbox (no `docker`
  binary, no internet to pull the base image) — see docs/DEPLOYMENT.md's
  Docker section for the one-time verification to run before trusting it.
- **`Settings.trac_jobs_base_url`** (new) — `TracProvider`, constructed
  with no arguments by `provider_registry.get_provider()` (the path the
  scheduler and the manual "Import now" button both use), previously had
  no way to reach a real trust's Trac Jobs site outside of tests; this
  setting closes that gap. Unrelated to Postgres/Docker, but found during
  this same milestone while writing `scripts/verify_live_production.py`
  and just as necessary for real production ingestion to ever work.
- **`scripts/verify_live_production.py`** — the live-provider
  verification runbook: reachability checks, then the exact
  `scheduler.tasks.import_provider_jobs.run()` function the daily
  scheduled task uses, against whatever `DATABASE_URL` is configured.
  Refuses to run without `--yes`. Meant to be run once, from a machine
  with real internet access, after deploying — this sandbox has none
  (see docs/JOB_INGESTION.md's "Manual live verification" section).

## Real Email Notification Delivery

Turns `notifications.notification_providers.EmailNotificationProvider`
(previously a placeholder that always raised `NotImplementedError`) into
a real SMTP-backed channel. Full design in
docs/EMAIL_NOTIFICATIONS.md; in short:

- **`EmailService`** (`notifications/email_service.py`) — plain
  `smtplib`/`email.mime`, no third-party email API. One SMTP connection
  per `send()` call. Tested against Gmail (`SMTP_HOST=smtp.gmail.com` +
  an App Password), but any standard SMTP server works.
- **`email_templates.py`** — renders `(subject, html_body)` for the eight
  required notification types. Two shapes cover all eight:
  `_render_generic()` (six single-fact types) and `_render_digest()` (the
  daily digest and weekly summary, both driven by
  `notifications.digest_stats.compute_stats()`).
- **Two new event/notification types** — `DAILY_DIGEST`, `WEEKLY_SUMMARY`
  (`scheduler.tasks.send_daily_digest`/`.send_weekly_summary`, both new).
  The other six required templates reuse events that already existed
  (`JOB_IMPORTED`, `NEW_HIGH_MATCH_JOB`, `INTERVIEW_REMINDER_DUE`,
  `JOB_CLOSING_SOON`, `SCHEDULER_TASK_FINISHED`, `DOCUMENT_GENERATED`) —
  no changes needed to any of the modules that publish those.
- **`NotificationPreferences`** (new table, one row per user, created
  lazily) — per-type email toggles, quiet hours (UTC, wraps past
  midnight), daily digest hour, a per-user AI-match threshold (can only
  raise the bar above `settings.job_ingestion_high_match_threshold`,
  never lower it), and an optional preferred email address.
- **`EmailNotificationProvider` decides, never sends** — reusing
  `NotificationService`'s existing provider-list extension point (see
  docs/NOTIFICATIONS.md) rather than a parallel event-listener path. Its
  `send()` inserts an `EmailOutboxRecord` (new table) if that user's
  preferences allow it; it never opens an SMTP connection itself. A
  system-wide notification (`user_id is None` — `JOB_IMPORTED`,
  `SCHEDULER_TASK_FINISHED`) fans out to every active user, each deciding
  independently via their own preferences.
- **`send_pending_emails`** (new scheduled task, every
  `SCHEDULER_SEND_EMAILS_INTERVAL_SECONDS`) — the only thing that ever
  calls `EmailService`/opens a real SMTP connection, on its own schedule.
  This is the entire "email sending is asynchronous" mechanism: enqueuing
  is a cheap synchronous insert; a slow or unreachable mail server only
  delays how soon the email goes out, never the request/task that
  triggered the notification. One connection per email (not a shared
  batch connection), so one bad row can't affect the others in the same
  run; a row failing 3 times in a row is marked `"failed"` and not
  retried again.
- **`send_daily_digest`/`send_weekly_summary`** (new scheduled tasks, run
  hourly) — check each active user's own configured hour
  (`NotificationPreferences.daily_digest_hour`) against the current UTC
  hour, guarded by `last_daily_digest_sent_date`/
  `last_weekly_summary_sent_week` against double-sending. Publish the
  in-app notification for every eligible user regardless of their
  `email_daily_digest` flag — that flag only gates the email, not the
  in-app version.
- **`/notifications/settings`, `/notifications/history`**
  (`routes/notifications.py`, extended rather than a new router) — the
  settings form and the per-user email history (queued/sent/failed).
  `routes/settings.py`'s page links to the former rather than duplicating
  the form.

## AI Career Assistant

Turns an already-computed job match into plain-English, actionable
insight, shown on the job detail page. Full design in
docs/CAREER_ASSISTANT.md; in short:

- **`career_assistant.CareerAssistantService`** (new package) — pure
  Python, zero LLM calls, zero database access. Derives a plain-English
  score explanation, prioritized CV suggestions, and a predicted
  interview-readiness level (0-100 score + label) entirely from an
  already-computed `MatchResult` — runs on every job detail page view a
  match exists for, at zero cost, mirroring this app's established
  "rule-based always-on, real LLM call explicit-and-optional" split.
- **`DocumentType.CAREER_INSIGHT`** (new enum value, no migration needed)
  + **`documents.career_insight_generator.CareerInsightGenerator`** — the
  optional, explicit-click, real-LLM-backed narrative companion, wired
  into the pre-existing document generate → validate → persist →
  approve/reject → export pipeline exactly like `INTERVIEW_PREP`/
  `SKILLS_GAP_ANALYSIS` before it — no new persistence or review UI.
- **`components/career_assistant_panel.html`** — included in
  `job_detail.html` right after the existing AI match card, only when
  `routes/jobs.py`'s `job_detail()` computed a `career_insight` (i.e. a
  match with analysis exists) — one new, `None`-able context key, no
  changes to any existing route/template behavior for any other page.

## CI/CD

`.github/workflows/ci.yml` — three jobs on every push/PR: `lint` (ruff),
`test` (pytest + coverage, uploaded as artifacts), and `docker` (build
the existing Dockerfile and smoke-test `/health` on the built image, no
config needed). Full design, local-run instructions, and branch
protection recommendations in docs/CI_CD.md. This project's own git
history (previously nonexistent — no repo existed before this milestone)
was initialized as part of adding this.

## Root files

- **README.md** — quick start and stack overview.
- **requirements.txt** — Playwright, SQLAlchemy + Alembic + `psycopg2-binary`
  (Postgres driver — SQLite needs none beyond the stdlib), FastAPI +
  Jinja2 + `python-multipart`, pydantic/pydantic-settings, python-dotenv,
  Loguru, Anthropic SDK, passlib/bcrypt/itsdangerous (auth), APScheduler,
  PyYAML, pytest/httpx, and `-e .` (editable install of this package
  itself).
- **pyproject.toml** — src-layout packaging (`packages.find where=["src"]`)
  so `pip install -e .` picks up every subpackage under `src/job_automation`,
  including `config`, automatically.
- **alembic.ini** — Alembic config generated by `alembic init`; the
  `sqlalchemy.url` line is intentionally left blank since `env.py` sets it at
  runtime from `settings.database_url`.
- **.env.example** — every environment variable the app reads; copy to
  `.env` (gitignored) and fill in secrets/config.
- **Dockerfile / docker-entrypoint.sh / .dockerignore / docker-compose.yml**
  — see "Production Readiness" above and docs/DEPLOYMENT.md.
- **.gitignore** — excludes `.env`, `.venv/`, the database file, generated
  CVs/cover letters/reports (personal data), and standard Python/IDE cruft.
- **.venv/** — the project's virtual environment (gitignored). Activate with
  `.venv\Scripts\activate` (PowerShell/cmd) before running scripts, or
  invoke `.venv\Scripts\python.exe` directly.
