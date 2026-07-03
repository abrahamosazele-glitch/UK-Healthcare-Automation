"""
Thin helpers around Starlette's signed session cookie
(`SessionMiddleware`, registered in `web/app.py`). A session only ever
stores one thing — the authenticated user's id — never profile data, never
anything else, so there's nothing here to keep in sync with the database.

Kept separate from `auth_service.py` so that module can stay entirely
database/hashing-focused with zero knowledge of HTTP/cookies, and separate
from `web/app.py` so the session's on-disk "shape" (just one dict key) is
defined in exactly one place.
"""

from __future__ import annotations

import uuid

from starlette.requests import Request

from job_automation.database.models.user import User

_SESSION_USER_ID_KEY = "user_id"


def store_user_session(request: Request, user: User) -> None:
    request.session[_SESSION_USER_ID_KEY] = str(user.id)


def clear_user_session(request: Request) -> None:
    request.session.pop(_SESSION_USER_ID_KEY, None)


def get_session_user_id(request: Request) -> uuid.UUID | None:
    raw = request.session.get(_SESSION_USER_ID_KEY)
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
