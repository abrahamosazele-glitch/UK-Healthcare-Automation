"""
Database engine/session management.

Provides the SQLAlchemy engine and session factory used by every other
module. No repository/CRUD helpers yet (save_job, get_job_by_source_id, etc.)
since there are no models to persist until scraping is implemented — this is
just the connection plumbing, configured from `settings.database_url`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from job_automation.config.settings import settings
from job_automation.utils.logger import logger

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

# Logged unconditionally at import time (not gated behind create_app() or
# any one entry point) so the same line appears in every process that ever
# touches the database — the web app, the scheduler, scripts/*.py, a
# Railway deploy's boot log — making it obvious the instant two of them
# disagree about which database they're using.
logger.info("Database URL: {}", settings.database_url)

engine: Engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    # Checks a pooled connection is still alive with a cheap `SELECT 1`
    # before handing it to a request, and transparently reconnects if not.
    # Matters in production (a managed Postgres instance, e.g. Railway/
    # Render, will close idle connections server-side after some minutes)
    # where a stale pooled connection would otherwise surface as a random
    # "server closed the connection unexpectedly" on the next request. A
    # no-op cost for SQLite's single local file connection.
    pool_pre_ping=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        # SQLite ignores FOREIGN KEY constraints (and therefore our ON DELETE
        # CASCADE / SET NULL rules) unless this pragma is set per connection.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create all tables known to Base.metadata. Dev/local convenience only —
    production schema changes should go through Alembic migrations instead."""
    from job_automation.database import models  # noqa: F401
    from job_automation.database.base import Base

    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-managed session: `with get_session() as session: ...`."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
