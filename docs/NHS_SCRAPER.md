# NHS Jobs Scraper

The first site-specific scraper, built entirely on `job_automation.core`
(browser automation) and `job_automation.scrapers.base` (the generic scraper
framework). No new browser-lifecycle, retry, rate-limiting, or session
logic was written for NHS specifically — everything here is composition of
what already existed, plus a small database repository/service layer that
didn't exist yet (see [Persistence](#persistence--duplicate-detection)).

## Compliance: why this milestone is fixture-only

Before writing any code, NHS Jobs' Terms and Conditions
(`jobs.nhs.uk/candidate/acceptable-use`) were checked. They permit printing/
downloading extracts "for personal non-commercial use" but state:

> "you agree not to... copy, reproduce, distribute, republish, download,
> display, post or transmit in any form or by any means any of the content
> on NHS Jobs except as permitted above."

A scraper that systematically walks search results and stores structured
job data in a database is exactly the kind of automated reproduction this
clause is written to restrict, even for personal, non-commercial use.
Because of this, **this milestone does not access the live NHS Jobs site at
all**. Every test and the verification flow run against local static HTML
fixtures (`tests/fixtures/nhs/`) served over local HTTP
(`tests/fixture_server.py`) — nothing here has ever made a request to
`jobs.nhs.uk`. See [Known limitations](#known-limitations) for exactly what
that means for selector accuracy.

## Architecture

```
src/job_automation/scrapers/nhs/
├── nhs_urls.py     — URL construction (search URL, login URL, relative-href resolution)
├── nhs_login.py    — NHSLogin(BaseLogin)      — unverified, not exercised by tests (see below)
├── nhs_search.py   — NHSSearch(BaseSearch), NHSPaginator(BasePaginator), build_nhs_search_criteria()
├── nhs_parser.py   — NHSParser(BaseParser)    — two-phase: summary + detail
└── nhs_scraper.py  — NHSScraper(BaseScraper), ScrapeStats

src/job_automation/database/
├── repositories/   — EmployerRepository, JobRepository (pure data access — new this milestone)
└── services/       — JobIngestionService (dedup + insert-or-update logic — new this milestone)
```

`NHSPaginator` lives inside `nhs_search.py` rather than its own file — this
milestone's file list didn't include a separate paginator module, and
pagination has no meaning without a search, so colocating them was a
reasonable reading of that list.

The repository/service layer didn't exist anywhere in the codebase before
this milestone (the earlier database milestone only built schema and
migrations). It was added now, scoped to exactly what job persistence
needs — `EmployerRepository.get_or_create()` and
`JobRepository.find_existing()/create()/update()` for pure data access,
`JobIngestionService.save_parsed_job()` for the dedup-then-insert-or-update
business logic. `JobIngestionService` depends on
`job_automation.scrapers.base.ParsedJob` deliberately: it's meant to be
reused by every future scraper (TRAC, Indeed, Reed all produce the same
`ParsedJob` shape), not rewritten per site.

Two small extensions were made to shared code, since this milestone's
required fields didn't all fit what already existed:

- `ParsedJob` (in `scrapers/base/base_parser.py`) gained `employer_url`,
  `posted_date`, and `benefits` — common enough across job sites to belong
  in the generic contract, not NHS-specific fields bolted on separately.
- The `Job` model gained `band`, `contract_type`, `working_pattern`,
  `closing_date`, `requirements` (JSON list), `benefits` (JSON list), and
  `salary_raw` (the original free-text salary, kept alongside the parsed
  `salary_min`/`salary_max`/`salary_period` since real-world salary
  formatting varies more than any regex fully covers). Migration
  `75a188d09ece_add_nhs_job_detail_fields_to_job_model` adds these; applied
  and verified. `job_type` (the shared enum from the database milestone)
  is deliberately left unpopulated by this scraper — it doesn't map
  cleanly onto NHS's two separate axes (contract type vs. working pattern),
  so both are stored as free text instead of forcing an inaccurate mapping.

## Search flow

```
build_nhs_search_criteria(keywords=..., location=..., band=..., contract_type=...,
                           working_pattern=..., distance=..., salary_min=..., visa_sponsorship=...)
        │  (typed constructor — hides the raw SearchCriteria.filters key names)
        ▼
SearchCriteria(keywords, location, filters={...}, sort_by)
        │
        ▼
NHSSearch.search(page, criteria)          [BaseSearch's template method]
    ├─ build_search_url(criteria)          → nhs_urls.build_search_url(criteria, base_url, search_path)
    ├─ PageManager.navigate(page, url)     → rate-limited, retried on transient failure
    └─ execute_search(page, criteria)      → confirms .search-result cards are visible
```

All 8 required search facets (keyword, location, distance, salary, band,
contract type, working pattern, visa sponsorship) are supported via
`SearchCriteria.filters`, translated into query parameters by
`nhs_urls.build_search_url()`. `base_url`/`search_path` are parameters, not
hardcoded — this is what let the exact same URL-building code be pointed at
a local fixture server for testing and, unmodified, at the real
`NHS_BASE_URL`/`SEARCH_RESULTS_PATH` constants for any future compliant use.

## Parser flow

Real job boards only show summary fields on a search-results card; full
description/requirements/benefits require visiting the job's own advert
page. `NHSParser` mirrors this with two phases:

```
NHSParser.parse(element)         [required by BaseParser, called per card]
    → title, employer, location, salary, band, contract_type, hours,
      closing_date, posted_date, job_url, reference_number
    → raises ParsingError if .search-result__title is missing (fatal —
      BaseParser.parse_all() catches this and skips just that card)

NHSScraper._enrich_and_persist(job)
    → navigates to job.job_url
    → NHSParser.parse_detail(page, job)      [NHS-specific, not in BaseParser]
        → description, requirements, benefits, employer_url
        → raises ParsingError if .job-detail__description is missing
    → page.go_back() — always, even if this job failed, so the next
      card/page continues from the results listing
    → JobIngestionService.save_parsed_job(job)
```

A deliberately malformed card (missing `.search-result__title`) is included
in `tests/fixtures/nhs/search_page_1.html` to prove
`BaseParser.parse_all()`'s skip-and-log resilience actually engages for
NHS's parser, not just in the abstract.

## Pagination

`NHSPaginator(BasePaginator)` detects `a.search-results__next-page` /
`a.search-results__previous-page` for next/previous, and additionally reads
a `.search-results__page-info` ("Page X of Y") element for a page count —
purely for logging/statistics, since `BasePaginator`'s `max_pages` safety
cap doesn't need to know the total in advance. Two fixture pages
(`search_page_1.html`, `search_page_2.html`) are enough to exercise: next
detected on page 1, last-page correctly detected on page 2 (no next link),
and — separately — a `max_pages=1` scraper config stopping after page 1
regardless of natural last-page detection.

## Persistence / duplicate detection

```
NHSScraper.scrape()
    for each parsed+enriched job:
        JobIngestionService.save_parsed_job(job)
            ├─ EmployerRepository.get_or_create(name, website=employer_url)
            ├─ JobRepository.find_existing(source_site, external_id, url)
            │     match on (source_site, external_id) OR url — either is
            │     sufficient to identify "the same listing seen again"
            └─ found?  → JobRepository.update(existing, **fields)
               not found? → JobRepository.create(source_site, external_id, **fields)
```

`external_id` is the NHS reference number when present, falling back to the
job URL if a listing has none. Verified directly: inserting the same
`ParsedJob` twice (by reference number, then again by URL alone with no
reference number given) both correctly resolve to the same row being
updated rather than a second row being created — see
`test_job_ingestion_service_inserts_then_updates_without_duplicating` and
the full-run duplicate check in `test_nhs_scraper_full_run_persists_jobs_
and_reports_statistics` (running the same search twice: 3 inserts, then 3
updates, never 6 rows).

## Logging and error handling

`NHSScraper` logs: search started (with keywords/location), every page
visited (with page count if detected), every job inserted/updated, every
skipped (unparseable) card, every failed job (with a screenshot captured via
`ScreenshotManager`, reusing `core/`'s existing failure-capture pattern —
no new screenshot logic was written), and final completion statistics
(`ScrapeStats`: pages visited, jobs parsed/skipped/inserted/updated/failed).
One job failing (detail-page parse error, DB error) is caught, logged, and
screenshotted without stopping the rest of the scrape — verified by the
malformed-card fixture and by the try/except/finally structure in
`NHSScraper._enrich_and_persist()`, which always attempts to navigate back
to the results listing regardless of whether that job succeeded.

## Known limitations

- **Selectors are not verified against the live DOM.** Per the compliance
  constraint, no further live-site inspection was performed while building
  the parser/search/pagination selectors. They're a best-effort, realistic,
  GOV.UK Design System-flavored scheme written to match this milestone's
  own fixtures — real NHS Jobs markup will almost certainly differ and must
  be confirmed before this scraper is ever pointed at a real, compliant
  data source.
- **`NHSLogin` is unverified and untested.** This milestone's fixtures cover
  search/parse/paginate/persist only (browsing NHS Jobs doesn't require an
  account); there's no login fixture, so `nhs_login.py`'s selectors are
  pure best-effort and have never been exercised by any test.
- **One shared `job_detail.html` fixture stands in for every listing's
  advert page.** Real adverts each have unique content; the fixture proves
  the navigate/parse/go-back mechanics work, not that every possible advert
  layout is handled.
- **Salary parsing is regex-based and won't cover every real-world format.**
  `salary_raw` is always kept alongside the parsed `salary_min`/`salary_max`/
  `salary_period` specifically so no information is lost when parsing is
  only partial.
- **No de-activation of stale jobs.** `is_active` is always set `True` on
  save; a job that disappears from search results (closed, withdrawn) isn't
  currently detected or marked inactive.
- **`employer_url` is frequently `None`.** Many NHS adverts don't link to
  the employing Trust's own website (candidates apply via NHS Jobs itself);
  this is expected, not a bug.
- **`distance` is passed through as a raw string** (whatever unit/format a
  real search form expects — miles, likely) since no live form was
  inspected to confirm units or valid range.

## Future improvements

- Verify and correct every selector against the real site once a compliant
  access path is confirmed (e.g. an official API, or written permission
  from NHSBSA) — this is a prerequisite for any production use, not an
  optional improvement.
- Add a login fixture and tests for `NHSLogin`/`session_valid()` once that
  compliant path exists.
- Job de-activation: mark `is_active = False` for previously-seen jobs that
  no longer appear in a fresh search.
- Promote `ScrapeStats` to `scrapers/base/` if a second site scraper (TRAC,
  Indeed, Reed) needs identical run-statistics tracking, rather than each
  one defining its own.
- Tighten `parse_salary_range()` (in `utils/helpers.py`) as real-world
  salary formats are encountered that the current regex doesn't cover.

## Verification

```bash
.venv\Scripts\python.exe -m pytest tests/test_nhs_scraper.py -v
```

6 tests, all passing, all against local fixtures only:
`test_nhs_parser_extracts_fields_and_skips_malformed_card`,
`test_nhs_parser_detail_enriches_parsed_job`,
`test_nhs_paginator_detects_next_and_last_page_and_page_count`,
`test_nhs_paginator_respects_max_pages_safety_cap`,
`test_job_ingestion_service_inserts_then_updates_without_duplicating`,
`test_nhs_scraper_full_run_persists_jobs_and_reports_statistics` (runs a
full `NHSScraper` against the fixture site twice — first run inserts 3 jobs,
second run updates the same 3, confirmed via direct DB queries and
`scraper.stats`).
