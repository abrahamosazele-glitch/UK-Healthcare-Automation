# Job Ingestion Service

Turns the app from "demo jobs" into a real UK healthcare job aggregator:
downloads listings from multiple providers, normalizes them into the
existing `Job` schema, deduplicates across providers, and persists via the
existing `JobIngestionService`. Newly-imported jobs are automatically
AI-matched against every candidate's profile; a high-scoring match (or a
Band 3 / visa-sponsorship job) triggers a notification — but generating an
actual document (cover letter, supporting statement, interview prep,
skills-gap analysis) always requires an explicit click. **No deployment,
no automatic application submission, no live scraping of Indeed/TotalJobs.**

## Three scope decisions made for this milestone

These were explicit trade-offs agreed before implementation, not
unilateral choices — recorded here for anyone revisiting this milestone:

1. **Indeed, TotalJobs, Glassdoor, CV-Library, and company career pages
   are interface-only stubs.** Indeed/TotalJobs/Glassdoor prohibit
   automated scraping in their Terms of Service and run aggressive
   anti-bot protection, with no public jobseeker API comparable to
   Reed's; CV-Library's feed program is a paid partner arrangement
   requiring a signed agreement this codebase doesn't have credentials
   for; "company career pages" isn't one site at all (see
   `company_career_page_provider.py`'s own docstring). All five fully
   implement the `JobProvider` contract (so they slot into the registry/
   orchestrator/tests identically to a real provider) but `fetch_jobs()`
   always raises `NotImplementedError` with a message pointing at exactly
   what a real implementation would need. NHS Jobs and Trac Jobs
   (public-sector recruitment sites, lower ToS risk) and Reed (a real,
   documented public API) are the three real providers today.
2. **AI matching is automatic; document generation never is.** Every
   prior AI-integration milestone in this project made real Anthropic
   calls explicit-click-only, specifically to control cost and avoid
   surprise actions. A newly-imported job is automatically scored (see
   `ingestion.auto_match_service`) — that's the "tell me when something
   great shows up" behavior this milestone asks for — but the four
   documents (cover letter, supporting statement, interview prep,
   skills-gap analysis) still require an explicit click on the job's
   detail page, exactly like every other AI document generation in this
   app. This keeps real spend bounded and human-reviewed before it
   happens, even as job volume scales across five providers with a daily
   refresh.
3. **Verification uses realistic local fixtures, not live network
   calls.** This development environment has no general outbound internet
   access, so "real jobs import successfully" is verified the same way
   this project's existing NHS Jobs scraper already was: a local HTTP
   fixture server (`tests/fixture_server.py`) serving realistic HTML/JSON
   sample data, run through the actual provider code with zero mocking of
   the parsing/normalization/persistence logic itself. Running this
   against the real internet (a real `REED_API_KEY`, a real
   `jobs.nhs.uk`/`*.trac.jobs` host) is the next step for whoever deploys
   this with network access.

## Architecture

```
job_automation/ingestion/
    job_provider.py          JobProvider ABC, ProviderRunStats
    provider_registry.py      PROVIDER_REGISTRY: dict[str, type[JobProvider]]
    ingestion_orchestrator.py run_ingestion() — runs every configured provider, aggregates results
    auto_match_service.py     process_new_jobs() — AI-matches new jobs, publishes notifications
    multi_location.py         run_per_location() — one search per configured location, aggregated
    providers/
        nhs_provider.py                   wraps the existing scrapers.nhs.NHSScraper
        trac_provider.py                   wraps the new scrapers.trac.TracScraper
        reed_provider.py                   calls Reed's public JSON API directly (httpx, no browser)
        indeed_provider.py                 stub — fetch_jobs() raises NotImplementedError
        totaljobs_provider.py              stub — fetch_jobs() raises NotImplementedError
        glassdoor_provider.py              stub — fetch_jobs() raises NotImplementedError
        cv_library_provider.py             stub — fetch_jobs() raises NotImplementedError
        company_career_page_provider.py    stub — see its own docstring, not a generalizable site

job_automation/scrapers/trac/   Playwright-based scraper, mirrors scrapers/nhs/ exactly:
    trac_urls.py, trac_search.py (+ TracPaginator), trac_parser.py, trac_scraper.py
```

### `JobProvider` — one interface, two shapes of implementation

```python
class JobProvider(ABC):
    source_name: ClassVar[str]  # matches Job.source_site

    @abstractmethod
    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        """Download, normalize, deduplicate, and persist this provider's
        current listings."""
```

`fetch_jobs()` is one method, not four separate abstract steps
(download/normalize/dedupe/save), because normalization and
dedup/persistence are already fully generic
(`database.services.JobIngestionService`, reused unchanged by every real
provider) — forcing each provider to re-expose them as separate abstract
methods would be ceremony around calling the same shared service. What
genuinely differs per provider is *how listings are fetched*:

- **`NHSProvider`/`TracProvider`** wrap the existing (or new)
  Playwright-based `scrapers.base.BaseScraper` subclasses, which already
  fetch + normalize + persist internally in one `.run()` call — NHS/Trac
  Jobs are JS-rendered sites with no public API, so a browser is needed,
  and detail-page enrichment happens interleaved with search-result
  paging (visit a card's own advert page, then go back), not as a
  separate later pass.
- **`ReedProvider`** calls Reed's public jobseeker API
  (https://www.reed.co.uk/developers/jobseeker) directly via `httpx` — a
  plain authenticated JSON GET, no browser needed. Authenticates via HTTP
  Basic Auth with the API key as the username and an empty password, per
  Reed's documented contract. Requires `REED_API_KEY` in `.env`; raises a
  clear `ReedProviderError` (not a silent no-op) when it's missing, the
  same pattern `AnthropicProvider`'s missing-key error already uses.
- **`IndeedProvider`/`TotalJobsProvider`** — see "scope decisions" above.

### Normalization

Every provider produces `scrapers.base.ParsedJob` — the same generic type
`NHSScraper` has produced since the NHS Jobs scraper milestone — rather
than a new parallel dataclass. `ParsedJob` already covers every field this
milestone's normalization requirement lists: title, employer, location,
salary (plus separately parsed min/max/period via
`utils.helpers.parse_salary_range`), band, contract type, working pattern,
visa sponsorship, description, closing date, reference number, apply URL.
"Employment type"/"remote" aren't separate stored fields: employment type
maps onto `contract_type`/`working_pattern` (already free text, matching
how the Jobs page's existing `employment_type` filter works), and "remote"
was already a computed, not stored, characteristic (`JobFilter.remote`
matches against `working_pattern`/`location` text) before this milestone —
no schema change needed for either. Source/source-job-id map directly onto
`Job.source_site`/`Job.external_id`; date discovered/updated map onto the
existing `Job.created_at`/`updated_at` (`TimestampMixin`).

## Duplicate detection

`database.services.JobIngestionService.save_parsed_job()` checks two
independent ways to match an existing row, in order:

1. **`(source_site, external_id)` or `url`** — the same listing seen again
   from *this* source (a re-run of the same provider). This existed
   before this milestone.
2. **Failing that, `content_hash`** (a hash of normalized
   title+employer+location, computed by `utils.helpers.compute_content_hash`
   and already stored on every `Job` row) **against *any* source** — the
   same real-world role, re-posted under a different listing ID on a
   different job board. This is genuinely new: an NHS trust's vacancy
   commonly appears on NHS Jobs, Trac Jobs, *and* is often independently
   re-posted to Reed/Indeed/TotalJobs — without this second check, the
   same physical job would show up as N separate rows.

When a cross-source match is what fires, the existing row's
`source_site`/`external_id` are deliberately left untouched (whichever
source discovered it first keeps its identity); only its content fields
are refreshed. `JobRepository.find_by_content_hash()` is the new query
this relies on.

## Automatic AI matching, explicit-only document generation

`ingestion.auto_match_service.process_new_jobs(session, job_ids)` runs
after every ingestion cycle, for every newly-*created* job (not
re-imports) against every active user's saved candidate profile — reusing
`MatchingEngine`/`MatchingService`/`ai.profile_builder.to_ai_profile()`
exactly as the manual "Re-run with AI" dashboard button
(`web.routes.matches.rematch_with_ai`) already does. It publishes:

- **`NEW_HIGH_MATCH_JOB`** — score exceeds
  `settings.job_ingestion_high_match_threshold` (80% by default). The
  notification links to the job's detail page, where the existing
  "Generate a document" form (now including `interview_prep` as a fourth
  option — see below) is the only place a real Anthropic call for that
  job is ever made.
- **`NEW_BAND3_JOB`** — `job.band == "Band 3"`, regardless of match score.
- **`NEW_SPONSORSHIP_JOB`** — `job.visa_sponsorship is True`, regardless
  of match score.

Uses a real `AnthropicProvider` when `settings.anthropic_api_key` is
configured, falling back to `SchedulerFakeLLMProvider` (same as
`scheduler.tasks.run_ai_matching`) when it isn't — so a scheduled
ingestion run never hard-fails just because no key is set; the match
scores it computes are rule-based/placeholder instead of real AI in that
case, same as every other background task's stance.

`DocumentType.INTERVIEW_PREP` generation was previously only reachable
from an existing `InterviewRecord` (`routes/interviews.py`'s
`generate_interview_prep`, added in the Anthropic AI Integration
milestone). `routes/documents.py`'s job-scoped `generate_document` route
now also accepts `interview_prep` — `DocumentService
.generate_interview_prep()` never actually required an interview to
exist, only a `job_id`, so this was a small, additive route change, not a
new pipeline.

### Closing-soon notifications

A separate scheduled task, `scheduler.tasks.check_closing_soon_jobs`,
scans all active jobs for a closing date within
`settings.job_ingestion_closing_soon_hours` (48 by default) that haven't
already triggered this notification (`Job.closing_soon_notified_at`,
a new nullable column — migration `0c09fb52cf5c`). Only notifies users who
already have a `JobMatch` for that job (relevant to them), not every
active user. `Job.closing_date` is a `date`, not a `datetime` — the
48-hour window is rounded up to whole days (`ceil(hours / 24)`), an
accepted approximation given closing dates on every real job board this
project imports from are dates, not timestamps.

## Scheduler

`scheduler.tasks.import_provider_jobs` runs the full ingestion +
auto-match pipeline. It's registered with a new `TaskDefinition.daily_at_hour`
field (`settings.scheduler_job_ingestion_hour`, 6am by default) rather than
`interval_seconds` — "refresh every morning" means a fixed time of day, not
an interval since app start. `scheduler.job_scheduler.create_scheduler()`
uses a `CronTrigger` when `daily_at_hour` is set, an `IntervalTrigger`
otherwise; every pre-existing task (which leaves `daily_at_hour=None`) is
completely unaffected. The dashboard's generic "Run now" button (already
built for every registered task) works for this task with no additional
code. `check_closing_soon_jobs` runs hourly via the normal interval
mechanism.

## Search, dashboard, analytics

- **Search** — `JobFilter` gained a `source` field (exact match against
  `Job.source_site`); the Jobs page's filter form gained a "Source"
  dropdown populated from `JobRepository.list_distinct_sources()` (only
  sources actually present in the database, not the full configured
  provider list). Every other filter (band, employer, location, salary,
  visa sponsorship, remote, employment type, closing soon) already
  existed from the Job Management milestone.
- **Dashboard** — a new "Job ingestion" section: jobs discovered/today/
  this week, jobs by source, top employers by job volume, and the latest
  jobs imported. Computed server-side (`AnalyticsService
  .job_ingestion_summary()`), not client-side-fetched, since it's rendered
  as plain stat cards/lists with no chart canvas.
- **Analytics** — a new "Job market" section on the Analytics page: jobs
  by band, by trust/employer, by location, by salary bucket, by source,
  and discovered-over-time (last 30 days). `AnalyticsService
  .job_market_analytics()`, fetched client-side from
  `GET /api/dashboard/job-market-analytics` (same pattern as every other
  Chart.js canvas on that page). Both new `AnalyticsService` methods are
  deliberately account-wide (unscoped by user) — this is about the job
  market as discovered, not any one candidate's applications, the same
  reasoning that already keeps `JobOrganizationSummary` separate from
  `DashboardSummary`.

## Testing

No test in this project ever makes a real network call. This milestone's
tests follow the same pattern the NHS Jobs scraper already established:

- **`tests/test_trac_scraper.py`** — the new Trac Jobs scraper, run
  against `tests/fixtures/trac/` via the local HTTP fixture server
  (`tests/fixture_server.py`), identical structure to
  `test_nhs_scraper.py`.
- **`tests/test_ingestion.py`** — the provider registry, Indeed/TotalJobs's
  `NotImplementedError`, Reed's provider (via `httpx.MockTransport`, never
  a live call), cross-source deduplication, the ingestion orchestrator's
  aggregation and per-provider failure isolation, auto-match notifications
  (band 3, sponsorship, high-match, using `SchedulerFakeLLMProvider` for
  deterministic scores), the `import_provider_jobs` scheduled task, and
  `check_closing_soon_jobs`.
- **`tests/test_web_dashboard.py`** additions — the Jobs page's `source`
  filter, and the two new dashboard/analytics API endpoints.

## Multi-location search

NHS Jobs and Trac Jobs' location filters each narrow a search to *one*
place — there's no "search these three cities at once" query. Both
providers therefore call `ingestion.multi_location.run_per_location()`,
which runs one full search per entry in `settings.scrape_locations` (or a
single unfiltered search if none are configured), aggregating every
`ProviderRunStats` field across all of them. One location's search failing
doesn't lose the others' results — logged and skipped, so e.g. a
CloudFront hiccup on one city's search doesn't zero out an otherwise
successful run. A single `JobIngestionService` (and its `created_job_ids`
tracking) is shared across every location's run within one provider, so
dedup and stats stay correct across the whole run, not just within one
location.

Reed doesn't use this helper: its API already takes keyword *and* location
as two independent parameters on the same request, so it loops over
`(keyword, location)` pairs itself rather than needing a separate page load
per location the way a browser-driven search does.

## Adding a new job source safely

Before writing any code:

1. **Check the source's Terms of Service and `robots.txt`.** If automated
   access to job listings is explicitly prohibited (Indeed, TotalJobs,
   Glassdoor all are) or requires a paid partner agreement you don't have
   (CV-Library), stop here — add a disabled placeholder instead (see
   below), don't scrape around the restriction.
2. **Prefer an official API/feed over HTML scraping** wherever one exists
   — Reed's public jobseeker API is the model: no browser, no fragile
   selectors, explicitly documented and supported for this exact use case.

If the source is compliant, implement a real provider:

1. Add `job_automation/ingestion/providers/<source>_provider.py` with a
   `JobProvider` subclass: set `source_name` (this becomes `Job.source_site`
   and must be unique), implement `fetch_jobs(session) -> ProviderRunStats`.
2. If it's a plain HTTP API, follow `reed_provider.py`: a direct `httpx`
   call, normalize each result into a `ParsedJob`, persist via
   `JobIngestionService.save_parsed_job()` — no Playwright needed.
3. If it's an HTML-only site with no API, follow `scrapers/nhs/`'s
   composition (`BaseScraper`/`BaseSearch`/`BaseParser`/`BasePaginator`)
   rather than writing bespoke Playwright code, then wrap it in a
   `JobProvider` the same way `nhs_provider.py`/`trac_provider.py` do.
4. If the site's location filter only accepts one location per query, use
   `ingestion.multi_location.run_per_location()` (see above) rather than
   joining locations into one string.
5. Register the class in `provider_registry.py`'s `PROVIDER_REGISTRY` dict.
6. Only add it to `settings.job_ingestion_providers`'s default list once
   it's real and configured — a stub or an unconfigured provider (e.g.
   Trac Jobs with no `TRAC_JOBS_BASE_URL` set) left in that list only ever
   produces a `provider_errors` entry on every run.
7. Add tests mirroring `test_ingestion.py`'s existing shape: a real-fetch
   test (mocked HTTP or a local fixture server, never a live call), a
   missing-configuration test if applicable, and confirm
   `run_ingestion()`/`import_provider_jobs.run()` still isolate this
   provider's failures from every other configured provider.

If the source *isn't* compliant yet (no API, ToS prohibits scraping, or a
partner agreement you don't have credentials for), add a disabled
placeholder instead — see `indeed_provider.py`/`glassdoor_provider.py` for
the pattern: full `JobProvider` contract implemented, `fetch_jobs()` always
raises `NotImplementedError` with a message explaining exactly what's
missing, registered in `PROVIDER_REGISTRY` (so it's discoverable and
testable) but excluded from the default `job_ingestion_providers` list.

## Known limitations

- **Trac Jobs' CSS selectors are fixture-verified only**, not verified
  against a live `*.trac.jobs` tenant site — the same caveat NHS Jobs'
  selectors already carry (see docs/NHS_SCRAPER.md). Confirm/correct
  before pointing `TracProvider` at a real trust's site.
- **Reed's request/response shape is Reed's long-published, stable public
  API contract**, but has not been exercised against the live API in this
  environment (no outbound internet access here). Confirm field names
  against a real response the first time this runs against production.
- **`ReedProvider` fetches one page (up to 100 results) per
  (keyword, location) combination**, not paginating further with
  `resultsToSkip` — a scope limit, not a technical ceiling.
- **Indeed/TotalJobs/Glassdoor/CV-Library/company career pages remain
  unimplemented** pending a compliant data source — see "scope decisions"
  above and each provider module's own docstring for what's specifically
  missing.

## Manual live verification (run this on a machine with internet access)

Everything above was verified against local fixtures/mocks only — this
development environment has no outbound internet access at all (confirmed:
even a plain request to google.com fails at the TLS layer here). The steps
below are what's left to confirm against the real internet, in order from
easiest/lowest-risk to most involved.

### 1. Reed (real API, ~5 minutes, no code changes expected)

1. Register a free API key at https://www.reed.co.uk/developers/jobseeker.
2. Add it to `.env`: `REED_API_KEY=<your key>`.
3. Start the app, log in, go to **Scheduler**, click **Run now** next to
   **Import Provider Jobs** (or run just Reed directly: `python -c
   "from job_automation.database.db_manager import get_session;
   from job_automation.ingestion.providers.reed_provider import ReedProvider;
   from job_automation.config.settings import settings;
   s = get_session();
   with s as session: print(ReedProvider(api_key=settings.reed_api_key).fetch_jobs(session)); session.commit()"`
   from the project root, with `PYTHONPATH=src`).
4. Check the dashboard's "Job ingestion" section and the Jobs page
   (`?source=reed`) for real listings.
5. If Reed's actual JSON field names differ from what
   `ingestion/providers/reed_provider.py`'s `_row_to_parsed_job()` expects,
   the mismatch will surface as missing/blank fields (title is required —
   a real mismatch there would raise, not silently produce blanks) —
   adjust the field mapping to match.

### 2. NHS Jobs and Trac Jobs (real scraping, more involved)

Both currently point at nothing live — `NHSProvider`/`TracProvider`
default to placeholder base URLs (`NHS_BASE_URL`/`TRAC_BASE_URL` in their
respective `*_urls.py`) and their parsers' CSS selectors were written
against this milestone's local fixtures, not inspected against the real
sites (per this project's own compliance approach — see
docs/NHS_SCRAPER.md's "Known limitations" for why that was the deliberate
choice originally, and this milestone kept it for Trac).

To verify for real:

1. **Confirm scraping is actually permitted** — check the target site's
   `robots.txt` and Terms of Service. NHS Jobs and Trac Jobs are public-
   sector recruitment sites, generally lower-risk than Indeed/TotalJobs,
   but confirm before pointing a scraper at a real site.
2. Open the real site in a browser, search for a role, and use browser
   devtools to inspect the actual markup of one search-result card and one
   job/vacancy detail page.
3. Compare against `scrapers/nhs/nhs_parser.py` / `scrapers/trac/trac_parser.py`'s
   selectors (e.g. `.search-result__title`, `li.trac-vacancy`,
   `.vacancy-detail__description`) and `nhs_urls.py` / `trac_urls.py`'s
   query parameter names (e.g. `keyword`, `location`, `band`). Update
   whichever don't match the real DOM/URL structure.
4. Run `NHSProvider()`/`TracProvider()` (no `base_url`/`search_path`
   override — those parameters exist specifically so tests can point at a
   fixture server; omitting them uses the real `NHS_BASE_URL`/
   `TRAC_BASE_URL`) against the real site and confirm jobs import
   correctly, the same way step 1's Reed check does.
5. For Trac Jobs specifically: `TracProvider`/`TRAC_BASE_URL` currently
   points at one placeholder trust subdomain
   (`example-trust.trac.jobs`) — a real deployment needs one `TracScraper`
   instance (or provider config) per NHS trust tenant whose vacancies you
   want to import, since each trust runs its own `*.trac.jobs` subdomain
   with no central search across trusts.

### 3. End-to-end with the daily scheduler

Once both of the above are confirmed, set `SCHEDULER_ENABLED=true` in
`.env` and restart — `import_provider_jobs` will then fire automatically
every day at `SCHEDULER_JOB_INGESTION_HOUR` (6am by default), the same
code path "Run now" already exercises manually.
