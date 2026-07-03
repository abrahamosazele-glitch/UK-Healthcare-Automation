"""
Authentication: registration, login, and session management for the web
dashboard. Deliberately small and dependency-light — no OAuth, no JWT, no
multi-factor auth, no password reset flow (all explicitly out of scope for
this milestone; see docs/AUTHENTICATION.md's "Extension points").

- `password_hasher.py` — bcrypt via passlib. Never a plaintext or custom
  hash anywhere in this codebase.
- `auth_exceptions.py` — `EmailAlreadyRegisteredError`, `InvalidCredentialsError`.
- `auth_service.py` — `AuthService(session)`: `register()`/`authenticate()`,
  composing the hasher and `User` model. The only place a `User` row is
  ever created with a password.
- `session_store.py` — thin helpers around Starlette's signed session
  cookie (`request.session`), used by both `web/app.py`'s
  `get_current_user`/`get_current_api_user` dependencies and
  `web/routes/auth.py`'s login/logout handlers.
"""

from job_automation.auth.auth_exceptions import EmailAlreadyRegisteredError, InvalidCredentialsError
from job_automation.auth.auth_service import AuthService
from job_automation.auth.password_hasher import PasswordHasher

__all__ = [
    "AuthService",
    "EmailAlreadyRegisteredError",
    "InvalidCredentialsError",
    "PasswordHasher",
]
