# Deployment

Four ways to run this app in production: Railway, Render, plain Docker (any
VPS/host that can run a container), and a bare VPS with no container at all.
All four run the exact same app — the differences are only in how the
process is started, how Postgres is provisioned, and how HTTPS/health
checks are wired up.

This app has no separate worker process. The background scheduler
(`SCHEDULER_ENABLED=true`) runs inside the same process as the web server
(`APScheduler`, started from `web/app.py`'s `lifespan`) — there is nothing
else to deploy. This does mean the process must stay running continuously;
a platform that scales a service to zero between requests (most serverless
"scale to zero" tiers) will silently stop the daily job-ingestion refresh
and closing-soon-job checks. Pick an "always-on" tier/plan for the same
reason.

## Before deploying anywhere

1. **Generate a real session secret** — the app refuses to start with
   `ENVIRONMENT=production` while `SESSION_SECRET_KEY` is still at its
   development default (see `config/settings.py`'s
   `_validate_production_config`):
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
2. **Decide your database.** SQLite (the dev default) works fine for a
   single-instance deployment with a persistent disk, but most of the
   platforms below either don't offer persistent disks by default or make
   a managed Postgres instance the natural choice. See
   [PostgreSQL](#postgresql) below — this was verified (schema
   compatibility, timezone handling) during the Production Readiness
   milestone, though a real Postgres connection was never available
   inside the sandbox that built this feature; verify it for real on your
   own machine per that section.
3. **Set every production-relevant env var** — copy `.env.example`,
   fill in real values. At minimum for a working deployment:
   `ENVIRONMENT=production`, `SESSION_SECRET_KEY`,
   `SESSION_COOKIE_SECURE=true` (once served over HTTPS — true on every
   platform below except a VPS without a reverse proxy in front of it),
   `DATABASE_URL`, `ANTHROPIC_API_KEY` (optional — the app runs without
   real AI, just without generating real documents), `REED_API_KEY` and/or
   `TRAC_JOBS_BASE_URL` (optional — see docs/JOB_INGESTION.md).
4. **Never commit `.env`.** Every platform below has its own place to set
   secrets (Railway variables, Render environment groups, a `.env` file
   on a VPS with restrictive file permissions).

## PostgreSQL

`DATABASE_URL=postgresql://user:password@host:5432/dbname` — `psycopg2-binary`
is already in `requirements.txt`. Everything else is automatic:

- `alembic upgrade head` creates the exact same schema on Postgres as on
  SQLite — including migration `c38bf18c2826`, which makes every
  timestamp column naive (`TIMESTAMP WITHOUT TIME ZONE`), matching this
  app's naive-UTC-everywhere convention. Without that migration, Postgres
  would hand back timezone-*aware* datetimes and crash the first time one
  was compared against `utils.helpers.utc_now()`. See
  `database/mixins.py`'s docstring for the full explanation.
- `db_manager.py`'s engine is created with `pool_pre_ping=True`, so a
  connection a managed Postgres instance has quietly closed after being
  idle doesn't surface as a random mid-request error.
- Every search filter uses `.ilike()`, not `.like()` — SQLAlchemy compiles
  this to Postgres's native case-insensitive `ILIKE`, not a
  `LOWER(...) LIKE LOWER(...)` workaround, so behavior matches SQLite's
  case-insensitive `LIKE` without any dialect-specific code.
- **Set the database's session timezone to UTC.** `created_at`/`updated_at`
  use `server_default=func.now()` — evaluated inside Postgres itself, so
  it follows Postgres's session timezone, not Python's. Most managed
  Postgres providers default to UTC already; if yours doesn't, either set
  it at the database level (`ALTER DATABASE dbname SET timezone TO 'UTC'`)
  or append `?options=-c%20timezone%3DUTC` to `DATABASE_URL`.

**This was verified by compiling every table's DDL and several of the
more complex analytics queries against SQLAlchemy's `postgresql` dialect,
running the full migration chain (including a downgrade/upgrade round
trip) against a fresh SQLite file, and a static audit for
Postgres-incompatible SQL (none found — no raw `PRAGMA`/dialect-specific
functions outside `db_manager.py`'s already-SQLite-gated one). It was
**not** verified against a real running Postgres server — this sandbox
has no internet access and no local Postgres install. Before trusting a
production deployment, run `alembic upgrade head` against your real
`DATABASE_URL` once and confirm the app behaves normally (log in, view
the dashboard, run `scripts/verify_live_production.py`).**

## Docker

The `Dockerfile` at the repo root builds the app, installs Playwright's
Chromium (NHS Jobs/Trac Jobs ingestion launches a real headless browser —
not optional), and runs `alembic upgrade head` on every container start
(`docker-entrypoint.sh`) before starting `uvicorn`.

### Local Docker Compose (app + Postgres)

```bash
cp .env.example .env   # fill in real values
docker compose up --build
```

`docker-compose.yml` starts a local Postgres container and points the app
at it automatically — useful for exercising the Postgres path (see
above) without a managed cloud database. `./data` and `./logs` are
volume-mounted so generated documents and log files survive a container
restart.

### Any other Docker host (a VPS, a self-managed server)

```bash
docker build -t job-automation .
docker run -d --name job-automation \
  --env-file .env \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  job-automation
```

Put a reverse proxy (nginx, Caddy) in front for HTTPS/TLS — the container
itself only serves plain HTTP on port 8000. The image's `HEALTHCHECK`
hits `/health` every 30s; `docker ps` shows the resulting health status.

**Not build-tested in this sandbox** (no `docker` binary and no internet
access to pull the base image) — build it once on your own machine before
relying on it: `docker build -t job-automation .` should complete without
errors, and `docker run --rm job-automation python -c "import job_automation"`
should succeed.

## Railway

Railway builds directly from a `Dockerfile` in the repo root — no
additional build configuration needed.

1. Create a new Railway project, add this repo as a service (Railway
   detects the `Dockerfile` automatically).
2. Add a Postgres plugin to the project. Railway injects `DATABASE_URL`
   automatically in the `postgresql://...` form this app expects — no
   manual wiring needed.
3. Set the remaining environment variables (`ENVIRONMENT=production`,
   `SESSION_SECRET_KEY`, `SESSION_COOKIE_SECURE=true`, `ANTHROPIC_API_KEY`,
   etc.) under the service's Variables tab.
4. Under Settings, set the health check path to `/health` — Railway polls
   it during and after each deploy and rolls back a deploy that never
   turns healthy.
5. Railway's generated domain is served over HTTPS by default, so
   `SESSION_COOKIE_SECURE=true` is correct from the start.
6. Since there's no separate worker, one Railway service is the entire
   deployment. Pick a plan/replica count that doesn't sleep the service —
   the in-process scheduler needs to keep running.

## Render

Render also builds directly from a `Dockerfile`.

1. Create a new **Web Service**, point it at this repo, and set the
   runtime to **Docker** (Render detects the `Dockerfile` automatically).
2. Create a **Render Postgres** instance (or bring your own) and copy its
   internal connection string into `DATABASE_URL`.
3. Add the remaining environment variables under the service's
   Environment tab — or, if running multiple services, an Environment
   Group shared between them.
4. Set the health check path to `/health` in the service settings.
5. Render's default `.onrender.com` domain is HTTPS — `SESSION_COOKIE_SECURE=true`
   is correct immediately.
6. Use an "always on" instance type, not one that spins down on
   inactivity — same reasoning as Railway: the in-process scheduler needs
   a continuously-running process.

## VPS (no Docker)

For a plain Ubuntu/Debian VPS running Python directly:

```bash
git clone <this-repo> /opt/job-automation
cd /opt/job-automation
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium

cp .env.example .env   # fill in real values, including a real DATABASE_URL
.venv/bin/python -m alembic upgrade head
```

Run it under a process manager so it restarts on crash/reboot — a
`systemd` unit is the simplest option:

```ini
# /etc/systemd/system/job-automation.service
[Unit]
Description=UK Healthcare Job Automation
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/job-automation
EnvironmentFile=/opt/job-automation/.env
ExecStart=/opt/job-automation/.venv/bin/python -m uvicorn job_automation.web.app:app --app-dir src --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now job-automation
```

Put nginx (or Caddy) in front for HTTPS — `certbot --nginx` is the
standard way to get a free TLS certificate — and only then set
`SESSION_COOKIE_SECURE=true`; a `Secure` cookie is never sent back by the
browser over plain HTTP, which silently breaks login if set too early.
Point nginx's own health check (or an uptime monitor) at `/health`.

## After deploying, on any platform

1. **Run the live-provider verification runbook against the *deployed*
   database, not your laptop's.** `DATABASE_URL` is read from whatever
   environment the process actually runs in — running
   `verify_live_production.py` from a bare local shell reads your local
   `.env` (your local SQLite file), which has nothing to do with the
   database the deployed site reads from. On Railway, run it through the
   Railway CLI so it inherits the deployed service's real environment
   variables instead:
   ```bash
   railway run python scripts/verify_live_production.py --yes
   ```
   (Render/a VPS: SSH into the running container/instance and run it
   there, for the same reason.) This runs the exact same ingestion +
   auto-match code path the daily scheduled task uses, against your real
   `DATABASE_URL`, and reports a per-provider summary. Configure
   `REED_API_KEY`/`TRAC_JOBS_BASE_URL` first (see docs/JOB_INGESTION.md)
   — without them, Reed/Trac Jobs will report a clear per-provider error
   rather than importing anything, while NHS Jobs (no API key needed)
   still runs.
2. **Confirm which database the deployed site is actually using** —
   `railway run python scripts/db_diagnostics.py` (or the authenticated
   `GET /diagnostics/database` route on the live site) prints the active
   `database_url`, job counts by source, the latest jobs, and how many
   scheduler task runs are recorded. If this shows 0 jobs right after a
   local scraper run reported success, that's the tell: the scraper ran
   against your local database, not the deployed one — see step 1.
3. **Log in and click through the app** — dashboard, job search, AI
   matches, generate a document, view the scheduler page. `/health`
   should report `{"status": "ok", "database": "ok", ...}`.
4. **Turn on the scheduler** (`SCHEDULER_ENABLED=true`) once you're
   satisfied ingestion works — this is what makes the daily
   NHS/Trac/Reed refresh, AI auto-matching, and closing-soon
   notifications actually run inside the deployed app itself, without
   anyone clicking a button or running a script by hand.
