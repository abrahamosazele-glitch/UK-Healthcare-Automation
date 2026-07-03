# UK Healthcare Job Automation

A UK healthcare job aggregator and application assistant: imports real
listings from multiple job boards, deduplicates them, scores them against
your candidate profile with real AI, drafts tailored documents (cover
letters, supporting statements, interview prep, skills-gap analyses),
tracks every application through to offer, and manages employer contacts,
interviews, and reminders — all through a web dashboard.

## Status

Actively developed, milestone by milestone. Currently implemented:

- **Web dashboard** — FastAPI + Jinja2 + Bootstrap 5, with authentication
  (register/login/logout, session cookies).
- **Job Ingestion Service** — real multi-provider job aggregation (NHS
  Jobs, Trac Jobs, Reed today; Indeed/TotalJobs are interface-ready stubs
  pending a compliant data source), with cross-source deduplication and a
  daily scheduled refresh. See [docs/JOB_INGESTION.md](docs/JOB_INGESTION.md).
- **AI matching & document generation** — real Anthropic Claude
  integration for job-match scoring, cover letters, supporting statements,
  interview prep, and skills-gap analyses, with caching and cost logging.
  Every real AI document generation is an explicit user action. See
  [docs/ANTHROPIC_INTEGRATION.md](docs/ANTHROPIC_INTEGRATION.md).
- **Candidate profile intelligence**, **application workflow tracking**,
  **employer & application CRM**, **interview & calendar management**,
  **job organization (Kanban board, saved/favourite/hide/archive,
  reminders)**, **notifications**, **background scheduler**, and
  **analytics** — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
  full milestone-by-milestone breakdown.
- **Production readiness** — environment-gated config validation, a
  `/health` endpoint, structured production logging, PostgreSQL
  compatibility, Docker support, and deployment guides for Railway,
  Render, Docker, and a plain VPS. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- **Real email notification delivery** — real SMTP (tested against
  Gmail) for new jobs, high AI matches, interview/closing-soon reminders,
  daily digests, weekly summaries, scheduler status, and document
  generation, with per-user settings (per-type toggles, quiet hours,
  digest timing, AI match threshold, preferred address) and an email
  history page. Sending is asynchronous — see
  [docs/EMAIL_NOTIFICATIONS.md](docs/EMAIL_NOTIFICATIONS.md).
- **AI Career Assistant** — plain-English match explanations, prioritized
  CV improvement suggestions, and predicted interview readiness, shown on
  every job's detail page — derived instantly from the existing AI match
  at zero cost, with an optional real-LLM personalised narrative on
  explicit click. See [docs/CAREER_ASSISTANT.md](docs/CAREER_ASSISTANT.md).

**Not implemented / explicitly out of scope so far**: automatic
application submission, live scraping of Indeed/TotalJobs (both lack a
compliant public API and prohibit automated scraping — see
[docs/JOB_INGESTION.md](docs/JOB_INGESTION.md)).

## Tech stack

- **Python 3.11+**
- **FastAPI + Jinja2 + Bootstrap 5** — web dashboard
- **SQLAlchemy + Alembic** — database ORM and migrations (SQLite by
  default; Postgres-compatible, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md))
- **Playwright** — scraping JS-rendered job boards (NHS Jobs, Trac Jobs)
- **httpx** — Reed's public JSON API, no browser needed
- **Anthropic Claude API** — job matching and document generation
- **APScheduler** — daily job ingestion refresh + other background tasks
- **Pydantic / pydantic-settings** — config and data validation
- **Loguru** — structured logging
- **pytest** — testing (490+ tests, no real network calls — every
  external API/site is mocked or served from a local fixture)

## Project layout

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a full explanation of
every folder and file.

## Quick start

```bash
.venv\Scripts\activate                  # PowerShell/cmd
copy .env.example .env                  # fill in real values (see below)
python -m alembic upgrade head          # apply all migrations
python scripts/seed_demo_data.py        # optional: seed a demo user + jobs
python -m uvicorn job_automation.web.app:app --app-dir src --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000 and log in (or register a new account; the
demo seed script creates `jane.doe@example.com` / `DemoPassword123!`).

Or, with Docker: `docker compose up --build` (see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for this and every other
deployment target — Railway, Render, plain Docker, a VPS).

### Configuring real integrations

Everything in `.env` is optional — the app runs fully functional without
any of these, using rule-based matching and no live job imports:

- `ANTHROPIC_API_KEY` — enables real AI job matching and document
  generation (see [docs/ANTHROPIC_INTEGRATION.md](docs/ANTHROPIC_INTEGRATION.md)).
- `REED_API_KEY` — enables the Reed job-ingestion provider (free
  registration at https://www.reed.co.uk/developers/jobseeker).
- `TRAC_JOBS_BASE_URL` — points the Trac Jobs provider at one real NHS
  trust's Trac Jobs site (there's no single national Trac Jobs search the
  way NHS Jobs has one).
- `JOB_INGESTION_PROVIDERS` — which providers the scheduled ingestion task
  actually runs (defaults to `nhs_jobs,trac_jobs,reed`).
- `SMTP_HOST` (+ `SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD`/etc.) —
  enables real email notifications (see
  [docs/EMAIL_NOTIFICATIONS.md](docs/EMAIL_NOTIFICATIONS.md) for Gmail
  App Password setup).

To install browser binaries for Playwright-based scraping:
```bash
python -m playwright install chromium
```

### Running tests

```bash
python -m pytest
```

No test ever makes a real network call — external APIs (Anthropic, Reed)
are mocked, and scraped sites (NHS Jobs, Trac Jobs) are served from local
HTML fixtures under `tests/fixtures/`.
