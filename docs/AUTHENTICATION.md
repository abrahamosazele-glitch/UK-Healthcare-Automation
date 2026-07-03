# Authentication and User Accounts

`src/job_automation/auth/` plus session wiring in `web/app.py` and three
new routes in `web/routes/auth.py` turn the dashboard from a single-user
demo tool into a real multi-user system: anyone can register an account,
log in, log out, and every dashboard page/API endpoint now requires a
valid session and only ever operates on the logged-in user's own data.

**No API key integration, background jobs, notifications, deployment, or
automatic application submission** were touched or added this milestone —
all explicitly out of scope, same as every prior instruction on those
topics.

## Why this was possible without touching most of the codebase

Every table in the schema (`Job` and `Employer` excepted — see below) has
been keyed by `user_id` since the very first database milestone, and every
route/service already filtered by `current_user.id` — the entire dashboard
was already architecturally multi-user, just missing a real way to
establish *who* `current_user` is. This milestone's job was narrow and
precise: replace one function's implementation
(`get_current_user()`'s "first `User` row in the database" placeholder)
with a real one, and add the registration/login/logout flow that feeds it.
No repository, service, or template needed to change to become
multi-user-safe — they already were.

## Architecture

```
src/job_automation/auth/
├── __init__.py
├── password_hasher.py    — PasswordHasher (bcrypt via passlib)
├── auth_exceptions.py     — EmailAlreadyRegisteredError, InvalidCredentialsError
├── auth_service.py        — AuthService (register/authenticate)
└── session_store.py       — thin helpers around Starlette's signed session cookie
```

```
web/routes/auth.py  (GET/POST /register, GET/POST /login, POST /logout)
    ├──> AuthService.register(email, password, full_name)
    │        └──> PasswordHasher.hash(password)   [bcrypt]
    ├──> AuthService.authenticate(email, password)
    │        └──> PasswordHasher.verify(password, user.hashed_password)
    └──> session_store.store_user_session(request, user)  /  clear_user_session(request)

web/app.py
    ├── SessionMiddleware (itsdangerous-signed cookie, registered in create_app())
    ├── get_current_user(request, session)      — HTML routes: redirect to /login if no session
    ├── get_current_api_user(request, session)  — JSON API routes: 401 if no session
    └── NotAuthenticatedError exception handler — turns get_current_user's failure into a redirect
```

`AuthService` never touches `request`/cookies; `session_store.py` never
touches passwords/hashing; `web/routes/auth.py` is the only place that
knows about both. This mirrors the existing project convention of
separating "the mechanism" from "the business meaning" (e.g.
`status_manager.py` vs. `review_service.py` in the workflow subsystem).

## Password hashing

`PasswordHasher` wraps `passlib.context.CryptContext(schemes=["bcrypt"])` —
never a plaintext password, never `hashlib`, never a hand-rolled scheme.
`User.hashed_password` (new column, migration `45e851e801ec`) stores only
the bcrypt hash. `bcrypt` is pinned to `<4.1` in `requirements.txt` because
passlib 1.7.4's bcrypt backend probes `bcrypt.__about__.__version__`, which
bcrypt 4.1 removed — a compatibility pin, not a security concern.

`AuthService.authenticate()` deliberately runs a real `verify()` call
against a dummy hash even when the email doesn't exist
(`_DUMMY_HASH` module constant), so a request for an unregistered email and
a request with a wrong password for a real email take roughly the same
amount of time — this and returning the identical `"Invalid email or
password"` message for both cases together prevent using the login form to
enumerate which emails are registered.

## Sessions

Starlette's `SessionMiddleware` (registered in `create_app()`) signs the
session cookie with `itsdangerous` using `settings.session_secret_key` — the
cookie's *contents* (just `{"user_id": "<uuid>"}`) are visible to the
browser (it's signed, not encrypted), but tampering with it is detected and
rejected, since the client doesn't have the secret key needed to produce a
valid new signature.

Cookie flags, all configured on `SessionMiddleware`:
- `httponly` (Starlette's default) — inaccessible to JavaScript, mitigating
  session-cookie theft via XSS.
- `same_site="lax"` — sent on top-level navigations and same-site requests,
  not on cross-site requests, mitigating CSRF for the state-changing
  GET-driven navigations this app uses.
- `https_only=settings.session_cookie_secure` — `False` by default (local
  dev is plain `http://localhost`; a `Secure` cookie would never be sent
  back over plain HTTP, silently breaking login). Must be set `True` in
  `.env` once this is ever served over HTTPS — see "Extension points."
- `max_age=settings.session_max_age_seconds` — 14 days by default.

`session_store.py` stores exactly one key (`user_id`) and nothing else —
no cached profile data, no permissions, nothing that could drift out of
sync with the database. Every request re-fetches the `User` row fresh from
`session.get(User, user_id)`.

## Two "current user" dependencies, one underlying check

`get_current_user` (HTML page routes) and `get_current_api_user` (JSON API
routes, all 4 files in `web/api/`) both resolve the same session cookie the
same way, but fail differently:

- `get_current_user` raises `NotAuthenticatedError`, caught by an
  `@app.exception_handler` that returns `RedirectResponse("/login?next=<path>")`
  — a browser navigating to a protected page lands on the login form, not a
  raw error.
- `get_current_api_user` raises `HTTPException(401)` — a JSON body a
  `fetch()`/HTMX caller can actually detect and branch on. Redirecting a
  JSON request to an HTML login page would just hand the caller a login
  page's markup as if it were the API response, which is worse than a
  clear 401.

All 9 HTML route files kept using the name `get_current_user` (their
existing `Depends(get_current_user)` calls needed zero changes — only the
function's *implementation* changed). All 4 API files were updated to
depend on `get_current_api_user` instead.

## Protecting every route

No route decorator or per-route "requires login" flag exists — protection
is a direct consequence of `Depends(get_current_user)` /
`Depends(get_current_api_user)` already being present on every dashboard
route (they always needed *a* `User` to scope queries by; now that
dependency also happens to enforce authentication). The three
`web/routes/auth.py` routes (`/register`, `/login`, `/logout`) are the only
ones that don't depend on either, for the obvious reason that you can't be
logged in before logging in.

`/` itself checks the session directly (not via `get_current_user`, to
avoid a redirect-then-redirect round trip) and sends an authenticated
visitor to `/dashboard`, everyone else to `/login`.

## Replacing the demo user

The old `get_current_user()` implementation — "the first `User` row in the
database" — is gone. Every page that referenced "Jane Doe" was already
reading `current_user.full_name` from the dependency, not a hardcoded
string, so nothing in `templates/` changed: whoever is actually logged in
now appears in the navbar, exactly as it always displayed *some* user's
name, just previously an arbitrary one.

## Per-user data isolation

Every per-user resource (`CandidateProfileRecord`, `JobMatch`,
`GeneratedDocumentRecord`, `ApplicationWorkflowRecord`) was already scoped
by `user_id` in every repository/service call — this milestone changed
*which* `user_id` those calls use (the real logged-in user's, not the
first row's), not the scoping logic itself. `Job`/`Employer` remain
deliberately unscoped: they're shared listings scraped from job boards,
visible to every candidate, the same way a real job board shows the same
posting to everyone — a second registered user correctly sees the exact
same job list as the first, and this is not a data leak.

`api/documents_api.py`'s existing `_owned_document_or_404()` (built in the
Web Dashboard milestone, before real auth existed) already 404s when
`document.user_id != current_user.id` — that check now guards against a
*real* other user, not a hypothetical one a test constructed by hand.

## Extension points

Deliberately not built this milestone (kept in scope only for what was
asked):

- **Password reset / "forgot password"** — no email-sending capability
  exists anywhere in this codebase (same gap noted for notification
  preferences in docs/DASHBOARD.md), so a reset flow has nowhere to send a
  reset link.
- **Email verification** — registration trusts the email address as given.
- **Multi-factor authentication, OAuth/social login, JWT-based API
  tokens** — session cookies were the right fit for a single
  server-rendered dashboard; none of these were needed or requested.
- **Rate limiting / account lockout** — the timing-safe comparison above
  mitigates email enumeration, but repeated login attempts aren't
  throttled.
- **`SESSION_COOKIE_SECURE=true`** — must be set once this is ever
  deployed behind HTTPS (deployment itself is explicitly out of scope);
  local dev correctly keeps this `False`.
- **`SESSION_SECRET_KEY`** — the shipped default
  (`dev-insecure-secret-key-change-me`) is fine for local dev only;
  anything beyond that must set a real random value in `.env`, since
  every existing session is invalidated the moment this value changes.

## Testing

Two files, deliberately split by what they're actually testing:

- **`tests/test_authentication.py`** (33 tests) — the real thing, **no**
  `get_current_user`/`get_current_api_user` overrides: registration
  (creates a real bcrypt-hashed user, auto-logs-in, rejects duplicate
  emails/short passwords/mismatched passwords), login (correct credentials
  succeed, wrong password and unknown email both fail with the identical
  message, `?next=` redirect works, `?next=//evil.example.com` is rejected
  as an open-redirect attempt and falls back to `/dashboard`), logout
  (clears the session, dashboard becomes protected again), every one of
  the 9 protected HTML pages and 8 protected API routes redirects/401s
  when unauthenticated, `/` redirects correctly both ways, two
  independently-registered users have fully isolated candidate profiles
  and empty (never shared) workflows/documents, and the session cookie
  carries `httponly`/`samesite=lax`.
- **`tests/test_web_dashboard.py`** (33 tests, updated this milestone) —
  dashboard *features*, with `get_current_user`/`get_current_api_user`
  overridden to "the most-recently-seeded test user" (a test-only
  convenience, replacing the removed "first user in the database"
  production behavior). This keeps feature tests decoupled from auth
  mechanics, the same way `get_llm_provider` is already overridden with a
  `FakeLLMProvider` there — one file proves auth works, the other proves
  the dashboard works once already authenticated.

Full suite: 152 tests, zero regressions (120 pre-existing minus one
obsolete "no user -> 500" test whose real behavior moved to
`test_authentication.py`'s protected-route tests, plus 33 new).

Manually verified end-to-end against a real `uvicorn` process: unauthenticated
requests to every page (303 to `/login`) and every API route (401);
register → auto-login → dashboard; wrong password rejected; logout clears
the session; two independently registered users (`alice2@example.com`,
`bob2@example.com`) each see only their own candidate profile, matches,
documents, and workflows, while both correctly see the same shared job
listings.
