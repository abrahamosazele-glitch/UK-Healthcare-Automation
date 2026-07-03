"""Exceptions raised by `AuthService` — caught by `web/routes/auth.py` to
re-render the register/login form with an error message, never surfaced as
a raw 500."""

from __future__ import annotations


class EmailAlreadyRegisteredError(Exception):
    """Raised by `AuthService.register()` when the email is already taken."""


class InvalidCredentialsError(Exception):
    """Raised by `AuthService.authenticate()` for either an unknown email or
    a wrong password — deliberately the same error/message for both, so a
    login form never reveals whether a given email is registered."""
