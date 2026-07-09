"""
The FastAPI web dashboard application.

**This module contains no business logic of its own** beyond authentication
wiring (added this milestone). Every route in `routes/`/`api/` calls into
the already-existing services and repositories (`MatchingService`,
`WorkflowService`, `DocumentService`, `ProfileService`, `JobRepository`,
`JobMatchRepository`, `DocumentRepository`, `WorkflowRepository`,
`AnalyticsService`) — this file wires the app together: static file mounts,
Jinja2 templates, session middleware, shared auth dependencies, and router
registration.

**Real authentication, session-cookie based.** `get_current_user()` now
resolves the user from a signed session cookie (`SessionMiddleware`,
`job_automation.auth.session_store`) rather than "the first `User` row in
the database" — every dashboard page requires a logged-in session, and an
unauthenticated request is redirected to `/login` (via the
`NotAuthenticatedError` exception handler below), never silently served
someone else's data. See docs/AUTHENTICATION.md for the full design.

Two separate "current user" dependencies exist because HTML pages and the
JSON API need different failure behavior for the same underlying check:
`get_current_user` (HTML routes) redirects an anonymous request to
`/login`; `get_current_api_user` (JSON API routes) returns a `401` a
JavaScript `fetch()` caller can actually handle, since redirecting an
`fetch()`/HTMX JSON request to an HTML login page would just hand the
caller a login page's markup instead of an error it can detect.

**Background scheduler** (added this milestone): `scheduler_service`
below is the one `SchedulerService` instance the whole app shares — both
`web/routes/scheduler.py`'s manual "Run now" buttons and, if
`settings.scheduler_enabled` is true, APScheduler's periodic triggers
(`scheduler.job_scheduler.start_if_enabled()`, started/stopped via this
app's `lifespan`) call `scheduler_service.run_task()`, so a scheduled fire
and a manual click share the same per-task lock and history table. See
docs/BACKGROUND_SCHEDULER.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import text
from sqlalchemy.orm import Session

from job_automation.ai.anthropic_provider import AnthropicProvider
from job_automation.ai.cache import AIResponseCache, MatchCache
from job_automation.ai.llm_provider import LLMProvider, LLMProviderError
from job_automation.auth.session_store import get_session_user_id
from job_automation.config.settings import settings
from job_automation.config.logging_config import setup_logging
from job_automation.core.retry_manager import RetryManager
from job_automation.database.db_manager import get_session
from job_automation.database.models.user import User
from job_automation.scheduler.job_scheduler import start_if_enabled
from job_automation.scheduler.scheduler_service import SchedulerService
from job_automation.utils.logger import logger

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

#: Shared by the manual "Run now" routes and (if enabled) the periodic
#: APScheduler triggers — constructing it is cheap (just per-task
#: `threading.Lock`s) and never starts a background thread by itself, so
#: this is safe to create unconditionally at import time, including under
#: pytest.
scheduler_service = SchedulerService()

#: Shared by every route that generates AI content (documents, interview
#: prep, skills-gap analysis) — a single on-disk cache, not one per
#: request, so a second identical request actually gets a cache hit. See
#: `ai.cache.AIResponseCache`'s docstring.
ai_response_cache = AIResponseCache()

#: Same reasoning as `ai_response_cache`, for the manual "re-run with AI"
#: job-match trigger (`web.routes.matches.rematch_with_ai`) — a separate
#: cache/subdirectory because it stores structured `LLMAnalysis`, not plain
#: text. See `ai.cache.MatchCache`'s docstring.
match_cache = MatchCache()


def get_scheduler_service() -> SchedulerService:
    """FastAPI dependency wrapping the `scheduler_service` singleton —
    `routes/scheduler.py`/`api/scheduler_api.py` depend on this rather than
    importing the singleton directly, so tests can override it with an
    isolated `SchedulerService` (its own session factory, its own locks)
    the same way `get_db_session`/`get_current_user`/`get_llm_provider`
    are already overridden elsewhere. Importing the singleton directly
    would silently point every scheduler-page test at the real
    `data/jobs.db`, since overriding `get_db_session` alone has no effect
    on an object that isn't resolved through FastAPI's dependency
    injection."""
    return scheduler_service


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    background_scheduler = start_if_enabled(scheduler_service)
    try:
        yield
    finally:
        if background_scheduler is not None:
            background_scheduler.shutdown(wait=False)


class NotAuthenticatedError(Exception):
    """Raised by `get_current_user` when no valid session exists. Caught by
    the exception handler registered in `create_app()`, which redirects an
    HTML page request to `/login?next=<original path>` — never rendered as
    a raw 500 or a JSON error body for a browser navigation."""


def get_db_session() -> Iterator[Session]:
    """FastAPI dependency wrapping the existing `db_manager.get_session()`
    context manager — commits on a successful response, rolls back on an
    unhandled exception, always closes. Not a reimplementation, just a
    generator-shaped adapter for FastAPI's dependency injection."""
    with get_session() as session:
        yield session


def _resolve_session_user(request: Request, session: Session) -> User | None:
    user_id = get_session_user_id(request)
    if user_id is None:
        return None
    return session.get(User, user_id)


def get_current_user(request: Request, session: Session = Depends(get_db_session)) -> User:
    """For HTML page routes: the logged-in user, or a redirect to
    `/login` — never "the first user in the database" (that demo-only
    behavior was removed this milestone; see docs/AUTHENTICATION.md)."""
    user = _resolve_session_user(request, session)
    if user is None:
        raise NotAuthenticatedError()
    return user


def get_current_api_user(request: Request, session: Session = Depends(get_db_session)) -> User:
    """For JSON API routes: the logged-in user, or a `401` — deliberately
    not a redirect, since a `fetch()`/HTMX caller expecting JSON can't
    usefully follow a redirect to an HTML login page."""
    user = _resolve_session_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_ai_response_cache() -> AIResponseCache:
    """FastAPI dependency wrapping the shared `ai_response_cache` singleton
    — same reasoning as `get_scheduler_service`: routes depend on this
    rather than importing the singleton directly, so tests can override it
    with an isolated cache (a fresh temp directory) instead of silently
    sharing/polluting `data/cache/ai_responses/` between test runs."""
    return ai_response_cache


def get_match_cache() -> MatchCache:
    """FastAPI dependency wrapping the shared `match_cache` singleton — see
    `get_ai_response_cache`'s docstring for the same reasoning."""
    return match_cache


def get_llm_provider() -> LLMProvider:
    """Builds the real `AnthropicProvider` from configured settings —
    model, timeout, retry count, and per-million-token cost estimates all
    come from `settings.anthropic_*` (`.env`/environment only; never the
    database, never the UI). Raises a clear 503 (not a placeholder/fake
    success) if no API key is configured — AI generation genuinely
    requires a working provider, and this dashboard doesn't pretend
    otherwise. Overridden with `FakeLLMProvider` in tests via
    `app.dependency_overrides[get_llm_provider]`."""
    try:
        return AnthropicProvider(
            settings.anthropic_api_key,
            model=settings.anthropic_model,
            timeout_seconds=settings.anthropic_timeout_seconds,
            retry_manager=RetryManager(max_retries=settings.anthropic_max_retries),
            input_cost_per_million_usd=settings.anthropic_input_cost_per_million_usd,
            output_cost_per_million_usd=settings.anthropic_output_cost_per_million_usd,
        )
    except LLMProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def create_app() -> FastAPI:
    # Configured as the very first thing `create_app()` does — every log
    # line this app emits from here on (including "Web dashboard app
    # created" below) should go through the real sinks (level, rotation,
    # production JSON), not loguru's unconfigured default. `uvicorn
    # job_automation.web.app:app` never calls this itself, and there's no
    # separate main.py entry point for this app, so this is the one place
    # guaranteed to run before any route is ever hit. Idempotent, so
    # importing this module twice (or a script that already called it)
    # is harmless.
    setup_logging()

    app = FastAPI(title="UK Healthcare Job Automation Dashboard", lifespan=_lifespan)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret_key,
        https_only=settings.session_cookie_secure,
        same_site="lax",
        max_age=settings.session_max_age_seconds,
    )

    app.mount("/css", StaticFiles(directory=str(WEB_DIR / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(WEB_DIR / "js")), name="js")

    from job_automation.web.api import (
        dashboard_api,
        documents_api,
        employers_api,
        interviews_api,
        job_organization_api,
        jobs_api,
        notifications_api,
        scheduler_api,
        workflow_api,
    )
    from job_automation.web.routes import (
        analytics,
        applications,
        auth as auth_routes,
        board,
        calendar as calendar_routes,
        candidate,
        dashboard,
        documents,
        employers,
        interviews,
        job_organization,
        jobs,
        matches,
        notifications as notifications_routes,
        scheduler as scheduler_routes,
        settings as settings_routes,
        workflow,
    )

    app.include_router(auth_routes.router)
    app.include_router(dashboard.router)
    app.include_router(jobs.router)
    app.include_router(job_organization.router)
    app.include_router(board.router)
    app.include_router(employers.router)
    app.include_router(interviews.router)
    app.include_router(calendar_routes.router)
    app.include_router(matches.router)
    app.include_router(documents.router)
    app.include_router(workflow.router)
    app.include_router(applications.router)
    app.include_router(candidate.router)
    app.include_router(analytics.router)
    app.include_router(settings_routes.router)
    app.include_router(scheduler_routes.router)
    app.include_router(notifications_routes.router)

    app.include_router(dashboard_api.router)
    app.include_router(jobs_api.router)
    app.include_router(job_organization_api.router)
    app.include_router(employers_api.router)
    app.include_router(interviews_api.router)
    app.include_router(documents_api.router)
    app.include_router(notifications_api.router)
    app.include_router(workflow_api.router)
    app.include_router(scheduler_api.router)

    @app.exception_handler(NotAuthenticatedError)
    def _redirect_to_login(request: Request, exc: NotAuthenticatedError) -> RedirectResponse:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)

    @app.exception_handler(Exception)
    async def _log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """Without this, an unhandled exception is caught by Starlette's own
        `ServerErrorMiddleware` and logged through the stdlib `logging`
        module — invisible to loguru's sinks (`app.log`, and in production
        the structured stdout sink a log aggregator is watching). FastAPI's
        own handlers for `HTTPException`/`RequestValidationError` are
        registered separately and, being more specific, still take
        precedence over this one — this only fires for genuinely unexpected
        errors."""
        logger.exception("Unhandled exception on {} {}: {}", request.method, request.url.path, exc)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/", include_in_schema=False)
    def root(request: Request) -> RedirectResponse:
        return RedirectResponse(url="/dashboard" if get_session_user_id(request) is not None else "/login")

    @app.get("/health", include_in_schema=False)
    def health_check(session: Session = Depends(get_db_session)) -> JSONResponse:
        """Unauthenticated liveness/readiness probe for Docker/Railway/
        Render/a load balancer — deliberately outside `get_current_user`'s
        auth requirement (a health checker has no session cookie) and
        outside the app's Jinja templates (a plain JSON body, not an HTML
        page, is what every one of those platforms actually expects to
        parse). Checks real database connectivity (not just "did the
        process start") since a DB outage should fail the health check,
        not silently report healthy while every real page 500s."""
        try:
            session.execute(text("SELECT 1"))
            database_ok = True
        except Exception as exc:
            database_ok = False
            logger.error("Health check: database connectivity failed: {}", exc)

        body = {
            "status": "ok" if database_ok else "unhealthy",
            "database": "ok" if database_ok else "unreachable",
            "environment": settings.environment,
        }
        return JSONResponse(body, status_code=200 if database_ok else 503)

    @app.get("/diagnostics/database", include_in_schema=False)
    def database_diagnostics(
        session: Session = Depends(get_db_session),
        _current_user: User = Depends(get_current_api_user),
    ) -> JSONResponse:
        """Confirms the scraper/scheduler/dashboard are reading and writing
        the same database: active `settings.database_url`, job counts by
        source, the most recently discovered jobs, and how many scheduler
        task runs are on record. Requires login (a 401 for an anonymous
        caller, same as every other JSON API route) since it surfaces real
        job/scheduler data, not just a boolean like `/health`. See
        `database.diagnostics.collect_database_diagnostics()` — also used
        by `scripts/db_diagnostics.py` for the same report from the
        command line (e.g. `railway run` against a deployed database with
        no browser session)."""
        from dataclasses import asdict

        from job_automation.database.diagnostics import collect_database_diagnostics

        return JSONResponse(asdict(collect_database_diagnostics(session)))

    logger.info("Web dashboard app created")
    return app


app = create_app()
