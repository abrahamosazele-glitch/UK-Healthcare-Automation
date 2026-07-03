"""
Centralized, typed application settings loaded from environment variables / .env.

Every other module should import `settings` from here instead of reading
`os.environ` directly, so configuration stays in one place. Values are loaded
from the process environment first, falling back to a `.env` file (via
python-dotenv, wired in automatically by pydantic-settings' `env_file` option)
for local development.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment (Production Readiness milestone) ---
    # Gates the fail-fast production config checks below — "development"
    # (the default) never blocks startup, so a fresh `git clone` +
    # `copy .env.example .env` always runs regardless of what's in it.
    # Set `ENVIRONMENT=production` only once real secrets/HTTPS are in
    # place; that's what turns the checks in `_validate_production_config`
    # on.
    environment: Literal["development", "production", "test"] = "development"

    # --- AI provider ---
    # Read only from the environment / .env — never stored in the database,
    # never exposed to the frontend. `AnthropicProvider` raises a clear,
    # actionable error the moment this is missing rather than silently
    # falling back to anything; every caller either gets a real completion
    # or a 503 explaining why not (see `web.app.get_llm_provider`).
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_timeout_seconds: float = 60.0
    anthropic_max_retries: int = 3
    # Approximate published per-million-token pricing, used only to log a
    # rough cost estimate alongside token counts (see `AnthropicProvider
    # .complete()`) — not billing-accurate, and deliberately overridable
    # via .env as pricing changes rather than hardcoded as fact.
    anthropic_input_cost_per_million_usd: float = 3.0
    anthropic_output_cost_per_million_usd: float = 15.0

    # --- Database ---
    # `.env.example` documents this as `sqlite:///data/jobs.db` (relative)
    # for readability, but a relative sqlite path only resolves correctly
    # if the process's cwd happens to be the project root — not guaranteed
    # for every launcher (e.g. an IDE run configuration, a process manager,
    # or this project's own preview tooling may use a different cwd). The
    # validator below resolves a relative sqlite path against PROJECT_ROOT
    # so `.env`'s value behaves the same regardless of invocation cwd,
    # matching this same default's own (already-absolute) construction.
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'jobs.db'}"

    # --- Scraper behavior (not used yet — no scrapers implemented) ---
    # `NoDecode` stops pydantic-settings from trying to JSON-decode the raw
    # env string itself (its default behavior for any `list[...]`-typed
    # field) before the `_split_comma_separated` validator below ever runs
    # — without it, `SCRAPE_LOCATIONS=London,Manchester` in `.env` fails to
    # parse as JSON at the settings-source level, before validation.
    scrape_locations: Annotated[list[str], NoDecode] = ["London", "Manchester", "Birmingham"]
    scrape_keywords: Annotated[list[str], NoDecode] = [
        "Healthcare Assistant",
        "Support Worker",
        "Care Assistant",
        "Senior Care Worker",
    ]
    user_agent: str = "Mozilla/5.0 (compatible; UKHealthcareJobBot/1.0)"

    # --- Candidate profile ---
    candidate_profile_path: Path = PROJECT_ROOT / "data" / "candidate_profile.json"

    # --- Reporting ---
    report_recipient_email: str | None = None
    daily_report_hour: int = 18

    # --- Logging ---
    log_level: str = "INFO"
    log_dir: Path = PROJECT_ROOT / "logs"

    # --- Browser automation (job_automation.core) ---
    browser_headless: bool = True
    browser_slow_mo_ms: int = 0
    browser_navigation_timeout_ms: int = 30_000
    browser_action_timeout_ms: int = 10_000
    browser_viewport_width: int = 1280
    browser_viewport_height: int = 800
    browser_download_dir: Path = PROJECT_ROOT / "data" / "downloads"
    browser_screenshot_dir: Path = PROJECT_ROOT / "data" / "screenshots"
    browser_session_dir: Path = PROJECT_ROOT / "data" / "sessions"
    browser_session_max_age_hours: int = 24
    browser_max_retries: int = 3
    browser_retry_base_delay_seconds: float = 1.0
    browser_retry_max_delay_seconds: float = 30.0
    browser_rate_limit_min_delay_seconds: float = 1.0
    browser_rate_limit_max_delay_seconds: float = 3.0
    browser_proxy_server: str | None = None
    browser_proxy_username: str | None = None
    browser_proxy_password: str | None = None

    # --- Scraper framework (job_automation.scrapers.base) ---
    scraper_max_pages: int = 10

    # --- Job ingestion (job_automation.ingestion) ---
    # Read only from the environment / .env — same rule as the Anthropic key.
    # Reed's public jobseeker API (https://www.reed.co.uk/developers/jobseeker)
    # authenticates via HTTP Basic Auth with the API key as the username and
    # an empty password; leave blank to run entirely without Reed (the
    # provider raises a clear error the moment it's needed, same pattern as
    # AnthropicProvider's missing-key error).
    reed_api_key: str | None = None
    # Trac Jobs, unlike NHS Jobs, has no single national search — it's a
    # platform individual NHS trusts each run on their own subdomain (e.g.
    # https://<trust-name>.trac.jobs). `TracProvider` falls back to
    # `scrapers.trac.trac_urls.TRAC_BASE_URL`, a placeholder, when this is
    # unset — fine for fixture-based tests, but real ingestion needs this
    # pointed at one specific trust's real site. See docs/DEPLOYMENT.md.
    trac_jobs_base_url: str | None = None
    # Which providers `scheduler.tasks.import_provider_jobs` actually runs.
    # Indeed/TotalJobs are deliberately excluded from the default list —
    # both are interface-only stubs (no compliant data source wired up yet;
    # see ingestion/providers/indeed_provider.py's module docstring) whose
    # `fetch_jobs()` always raises, so running them by default would only
    # ever produce a failed task run.
    job_ingestion_providers: Annotated[list[str], NoDecode] = ["nhs_jobs", "trac_jobs", "reed"]

    # --- Authentication / sessions (job_automation.auth, job_automation.web) ---
    # Signs the session cookie (itsdangerous, via Starlette's SessionMiddleware)
    # — NOT a password-hashing secret. The insecure default only exists so the
    # app runs out of the box in dev; any real deployment must override it via
    # .env, or every session invalidates whenever this default ever changes.
    session_secret_key: str = "dev-insecure-secret-key-change-me"
    # False in local dev (plain http://localhost) — a `Secure` cookie is never
    # sent back by the browser over plain HTTP, which would silently break
    # login. Set true once the app is actually served over HTTPS (deployment
    # is explicitly out of scope for this milestone, so this stays False here).
    session_cookie_secure: bool = False
    session_max_age_seconds: int = 60 * 60 * 24 * 14  # 14 days

    # --- Background scheduler (job_automation.scheduler) ---
    # Off by default so importing the app (including under pytest) never
    # silently starts a real background thread — flip on explicitly once
    # continuous scheduled runs are actually wanted. Manual "Run now" from
    # the dashboard works regardless of this flag.
    scheduler_enabled: bool = False
    scheduler_fixture_jobs_path: Path = PROJECT_ROOT / "data" / "fixtures" / "local_jobs.json"
    scheduler_task_max_attempts: int = 3
    scheduler_task_retry_base_delay_seconds: float = 1.0
    scheduler_task_retry_max_delay_seconds: float = 5.0
    # A JobMatch needs at least this overall score before
    # generate_draft_documents will draft a supporting statement for it —
    # avoids drafting documents for obviously-poor matches on every run.
    scheduler_document_score_threshold: float = 60.0
    scheduler_log_retention_days: int = 30
    # How often each task fires when `scheduler_enabled` is true. Minutes,
    # not seconds, even in dev — these are meant to simulate periodic
    # background automation, not hammer the database continuously.
    scheduler_import_jobs_interval_seconds: int = 60 * 60
    scheduler_ai_matching_interval_seconds: int = 60 * 30
    scheduler_generate_documents_interval_seconds: int = 60 * 30
    scheduler_update_workflows_interval_seconds: int = 60 * 15
    scheduler_cleanup_logs_interval_seconds: int = 60 * 60 * 24
    # Added for the Job Management milestone's reminders feature.
    scheduler_reminders_interval_seconds: int = 60 * 15
    # Added for the Interview & Calendar Management milestone. Deliberately
    # more frequent than the job-reminders task: interview reminders
    # include a "30 minutes before" offset, so a 15-minute check interval
    # would risk firing 15-40 minutes late instead of close to on time.
    scheduler_interview_reminders_interval_seconds: int = 60 * 5
    # Added for the Job Ingestion Service milestone. `import_provider_jobs`
    # runs once daily at this hour (server local time, 24h clock) rather
    # than on a fixed interval — "refresh every morning" means a
    # predictable time of day, not "every N seconds since the app started."
    # See `scheduler.job_scheduler.create_scheduler()`'s `CronTrigger`
    # branch for the one task that uses this instead of interval_seconds.
    scheduler_job_ingestion_hour: int = 6
    # How often `check_closing_soon_jobs` scans for jobs newly within the
    # closing-soon window — hourly is frequent enough for a 48-hour window
    # without re-scanning constantly; `Job.closing_soon_notified_at` still
    # prevents a duplicate notification even if this ran more often.
    scheduler_closing_soon_check_interval_seconds: int = 60 * 60
    # How many hours before a job's closing date counts as "closing soon"
    # for the automatic notification (distinct from `JobFilter`'s
    # `closing_soon` search filter, which uses a 7-day window for browsing
    # — this one is deliberately tighter, matching this milestone's
    # explicit "within 48 hours" requirement for the urgent notification).
    job_ingestion_closing_soon_hours: int = 48
    # The AI match score (0-100) above which a newly imported job triggers
    # a "high match" notification (see `ingestion.auto_match_service`).
    # Matching itself always runs automatically on import; document
    # generation never does — see that module's docstring.
    job_ingestion_high_match_threshold: float = 80.0
    # How often `send_pending_emails` flushes the `EmailOutboxRecord` queue
    # — this, not the moment an email is enqueued, is what actually makes
    # SMTP round-trips happen, so a real import/scheduled task never blocks
    # on them. See docs/EMAIL_NOTIFICATIONS.md.
    scheduler_send_emails_interval_seconds: int = 60 * 2
    # `send_daily_digest`/`send_weekly_summary` run hourly and check each
    # user's own configured hour (`NotificationPreferences.daily_digest_hour`)
    # against the current UTC hour — an hourly check interval is frequent
    # enough that no user's chosen hour is ever missed by more than a few
    # minutes, without scanning continuously.
    scheduler_digest_check_interval_seconds: int = 60 * 60

    # --- Email delivery (job_automation.notifications.email_service) ---
    # SMTP only — no third-party email API. Tested against Gmail
    # (smtp.gmail.com:587 + STARTTLS + a Google Account App Password, not
    # your real account password — see docs/EMAIL_NOTIFICATIONS.md), but
    # any standard SMTP server works. Leave `smtp_host` blank to run
    # without real email — `EmailService` raises a clear error the moment
    # it's actually asked to send, same "degrade, don't crash at import
    # time" pattern as `AnthropicProvider`/`ReedProvider`'s missing-key
    # handling. Never logged/exposed anywhere but here/the process
    # environment.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    # What recipients see in the "From" header — defaults to
    # `smtp_username` (the account actually authenticating) if unset, since
    # most SMTP providers (Gmail included) reject/flag a From address that
    # doesn't match the authenticated account anyway.
    smtp_from_email: str | None = None
    smtp_from_name: str = "UK Healthcare Job Automation"
    # STARTTLS (587) vs implicit TLS (465) — true matches Gmail's standard
    # port-587 setup; set false only for a server that expects a plain (or
    # already-TLS-wrapped, port 465) connection.
    smtp_use_starttls: bool = True

    @field_validator("scrape_locations", "scrape_keywords", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: str | list[str]) -> list[str]:
        """Allow SCRAPE_LOCATIONS=London,Manchester in .env instead of JSON."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url", mode="after")
    @classmethod
    def _resolve_relative_sqlite_path(cls, value: str) -> str:
        prefix = "sqlite:///"
        if not value.startswith(prefix):
            return value
        raw_path = Path(value[len(prefix):])
        if raw_path.is_absolute():
            return value
        return f"{prefix}{(PROJECT_ROOT / raw_path).resolve()}"

    @model_validator(mode="after")
    def _validate_production_config(self) -> "Settings":
        """Fail fast, at process startup, rather than silently running an
        insecure configuration in production. Only genuinely dangerous
        misconfigurations raise — a session-signing key still at its
        development default, or secure cookies left off — since those two
        actively compromise every user's session the moment real traffic
        hits the app. Everything else this app can run without (no
        Anthropic/Reed key, SQLite instead of Postgres) is a legitimate,
        if less common, choice for a small production deployment, not
        something worth refusing to start over — see
        `AnalyticsService.ai_status()`/`ReedProvider`'s own graceful
        missing-key handling for the same "degrade, don't crash" stance
        applied elsewhere in this app.

        Gated entirely behind `environment == "production"` — every other
        environment value (including the "development" default) skips
        this, so cloning the repo and running with `.env.example`'s
        defaults never fails a check meant for real deployments."""
        if self.environment != "production":
            return self

        errors: list[str] = []
        if self.session_secret_key == "dev-insecure-secret-key-change-me":
            errors.append(
                "SESSION_SECRET_KEY is still the insecure development default. Set a real "
                "random value (e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`)."
            )
        if not self.session_cookie_secure:
            errors.append(
                "SESSION_COOKIE_SECURE must be true in production — session cookies must "
                "only ever be sent over HTTPS once real traffic reaches this app."
            )
        if errors:
            raise ValueError(
                "Refusing to start with ENVIRONMENT=production and an insecure configuration:\n- "
                + "\n- ".join(errors)
            )
        return self


settings = Settings()
