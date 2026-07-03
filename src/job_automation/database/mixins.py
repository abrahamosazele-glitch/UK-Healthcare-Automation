"""
Reusable declarative mixins shared by every model.

`UUIDPKMixin` gives every table a client-generated UUID primary key (rather
than a DB auto-increment integer), so IDs are safe to generate before an
INSERT — useful once AI/document-generation code needs to reference a row's
ID before it's persisted. `TimestampMixin` gives every table `created_at` /
`updated_at`, set at the database level (`server_default=func.now()`) so the
timestamp is correct even for rows inserted outside the ORM.

**`DateTime(timezone=True)` is deliberately not used here** (or anywhere
else in `database/models/`, for the same reason): this app's entire
timestamp convention is naive UTC (`utils.helpers.utc_now()`), chosen
because SQLite has no real timezone-aware storage and always hands back a
naive `datetime` on read regardless of what was written. Declaring these
columns `timezone=True` would be harmless on SQLite but would silently
change behavior on Postgres, whose `TIMESTAMP WITH TIME ZONE` returns a
timezone-*aware* `datetime` from psycopg2 — comparing that against this
app's naive `utc_now()` anywhere (scheduler history, closing-soon checks,
analytics date bucketing, ...) would raise `TypeError: can't subtract
offset-naive and offset-aware datetimes` the moment this app runs against
Postgres instead of SQLite. Plain `DateTime()` (`TIMESTAMP WITHOUT TIME
ZONE` on Postgres) round-trips a naive UTC datetime identically on both
backends.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPKMixin:
    """Adds `id: uuid.UUID` as the primary key."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Adds `created_at` / `updated_at`, both maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
