"""
Registration, login, and logout — the only routes in this codebase that
don't require `get_current_user`/`get_current_api_user` (you can't be
logged in before logging in). Every other route in `routes/`/`api/`
requires an authenticated session; see docs/AUTHENTICATION.md.

Password length/confirmation validation happens here (form-level UX
concerns); hashing and comparison never do — that's entirely
`AuthService`'s job. This file only translates between HTTP forms/redirects
and `AuthService` + `session_store`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from job_automation.auth.auth_exceptions import EmailAlreadyRegisteredError, InvalidCredentialsError
from job_automation.auth.auth_service import AuthService
from job_automation.auth.session_store import clear_user_session, store_user_session
from job_automation.web.app import get_db_session, templates

router = APIRouter()


def _safe_redirect_target(next_path: str | None) -> str:
    """Only ever redirect to a same-site relative path. Rejects anything
    that doesn't start with exactly one `/` (empty, absolute URLs, and
    protocol-relative `//evil.com`-style URLs all fall through to the
    dashboard) — this is the one thing standing between `?next=` and an
    open-redirect vulnerability."""
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/dashboard"


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    session: Session = Depends(get_db_session),
) -> Response:
    form_values = {"full_name": full_name, "email": email}
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "register.html", {**form_values, "error": "Password must be at least 8 characters."}
        )
    if password != confirm_password:
        return templates.TemplateResponse(
            request, "register.html", {**form_values, "error": "Passwords do not match."}
        )

    try:
        user = AuthService(session).register(email=email, password=password, full_name=full_name)
    except EmailAlreadyRegisteredError:
        return templates.TemplateResponse(
            request, "register.html", {**form_values, "error": "That email is already registered."}
        )

    session.commit()
    store_user_session(request, user)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"next": next})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    session: Session = Depends(get_db_session),
) -> Response:
    try:
        user = AuthService(session).authenticate(email=email, password=password)
    except InvalidCredentialsError:
        return templates.TemplateResponse(
            request, "login.html", {"email": email, "next": next, "error": "Invalid email or password."}
        )

    store_user_session(request, user)
    return RedirectResponse(url=_safe_redirect_target(next), status_code=303)


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    clear_user_session(request)
    return RedirectResponse(url="/login", status_code=303)
