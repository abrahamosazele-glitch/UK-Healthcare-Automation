"""
The main entry point for authentication: register a new account, authenticate
an email/password pair. Composes `PasswordHasher` and the `User` model —
the same orchestrator pattern as `ProfileService`/`WorkflowService`/
`DocumentService` elsewhere in this project.

Deliberately does not touch `request.session` or cookies at all — that's
`session_store.py`'s job. `AuthService` only knows about database rows and
password hashes; `web/routes/auth.py` is what wires an authenticated `User`
into a browser session.

Publishes `USER_REGISTERED`/`USER_LOGGED_IN` events on success (never calls
`NotificationService` directly — see `notifications.events`'s module
docstring). Deliberately does **not** publish anything on a failed login:
there's no legitimate `user_id` to notify (the caller isn't authenticated
yet), and echoing failed-attempt details back through any channel tied to
the target account would itself be a security-relevant behavior beyond
this milestone's scope.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.auth.auth_exceptions import EmailAlreadyRegisteredError, InvalidCredentialsError
from job_automation.auth.password_hasher import PasswordHasher
from job_automation.database.models.user import User
from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.utils.logger import logger


class AuthService:
    def __init__(
        self, session: Session, *, hasher: PasswordHasher | None = None, event_bus: EventBus = event_bus
    ) -> None:
        self._session = session
        self._hasher = hasher or PasswordHasher()
        self._event_bus = event_bus

    def register(self, *, email: str, password: str, full_name: str) -> User:
        normalized_email = email.strip().lower()
        existing = self._session.scalars(select(User).where(User.email == normalized_email)).first()
        if existing is not None:
            raise EmailAlreadyRegisteredError(f"{normalized_email} is already registered")

        user = User(
            email=normalized_email,
            full_name=full_name.strip(),
            hashed_password=self._hasher.hash(password),
        )
        self._session.add(user)
        self._session.flush()
        logger.info("Registered new user {}", user.id)
        self._event_bus.publish(
            Event(event_type=EventType.USER_REGISTERED, payload={"email": normalized_email}, user_id=user.id),
            self._session,
        )
        return user

    def authenticate(self, *, email: str, password: str) -> User:
        normalized_email = email.strip().lower()
        user = self._session.scalars(select(User).where(User.email == normalized_email)).first()
        # Deliberately verify against a real hash even on a missing user
        # (dummy_hash) — this keeps a "no such email" lookup and a "wrong
        # password" lookup taking roughly the same amount of time, so
        # response latency can't be used to enumerate registered emails.
        if user is None:
            self._hasher.verify(password, _DUMMY_HASH)
            raise InvalidCredentialsError("Invalid email or password")
        if not self._hasher.verify(password, user.hashed_password):
            raise InvalidCredentialsError("Invalid email or password")
        if not user.is_active:
            raise InvalidCredentialsError("This account has been deactivated")
        logger.info("Authenticated user {}", user.id)
        self._event_bus.publish(
            Event(event_type=EventType.USER_LOGGED_IN, payload={"email": normalized_email}, user_id=user.id),
            self._session,
        )
        return user


# A real bcrypt hash of an unguessable, never-used password — exists purely
# so `authenticate()` can run a same-cost `verify()` call even when no user
# row exists to check against (see the timing-safety comment above).
_DUMMY_HASH = PasswordHasher().hash("this-is-not-a-real-password-3f8a2c1e")
