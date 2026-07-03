# CI/CD

A single GitHub Actions workflow (`.github/workflows/ci.yml`) with three
jobs: **lint**, **test**, and **docker** (build + smoke-test the image).
Every push and every pull request targeting `main`/`master` runs all
three; a failure in any of them fails the whole workflow run.

## What each job does

### `lint`

`pip install -r requirements-dev.txt` (adds `ruff` on top of
`requirements.txt`), then `ruff check .`. Ruff's own default rule
selection (pyflakes + a core set of pycodestyle checks) — no custom
strictness beyond that, and deliberately not the full `E` pycodestyle set
(`E501` line-too-long especially), since this codebase's established
style favors long, explanatory docstrings/comments over strict line
wrapping. Two verification scripts (`scripts/verify_live_production.py`,
`scripts/verify_scraper_framework.py`) are excluded from one rule
(`E402`) via `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml` —
both deliberately import after mutating `sys.path`, which ruff otherwise
flags as an import not at the top of the file.

### `test`

Installs dependencies, installs Playwright's Chromium (real tests launch
a real headless browser against local HTML fixtures — see
docs/JOB_INGESTION.md — never a live site), then:

```bash
pytest --cov=job_automation --cov-report=xml --cov-report=term-missing --junitxml=pytest-report.xml
```

No test ever makes a real network call or touches `data/jobs.db` — every
test runs against an isolated in-memory SQLite database and either mocks
external APIs (Anthropic, Reed) or serves job-board HTML from local
fixtures. This means CI needs no secrets at all (no `ANTHROPIC_API_KEY`,
no `.env` file — one doesn't even exist in the repo, see `.gitignore`) to
pass.

Both `pytest-report.xml` (JUnit format) and `coverage.xml` (Cobertura
format) are uploaded as workflow artifacts (`if: always()`, so a failing
run still uploads them — that's exactly when you need them to debug from
the Actions UI without reproducing locally first). Download them from the
workflow run's summary page, under "Artifacts."

### `docker`

Runs only after `lint` and `test` both succeed (`needs: [lint, test]`) —
no point spending build minutes on an image built from code that's
already known to fail its own checks. Builds the image with Docker
Buildx (cached via GitHub Actions' own cache backend, `type=gha`, so
unchanged layers don't rebuild on every run), then boots the freshly
built image with **no configuration at all** (no `.env`, no
`DATABASE_URL`, nothing) and polls `GET /health` for up to 30 seconds,
failing the job (and dumping the container's logs) if it never reports
healthy. This is a real assertion that the image the Dockerfile produces
actually starts and serves traffic on nothing but its own built-in
defaults (SQLite, `ENVIRONMENT=development`) — not just that `docker
build` exits zero.

## Caching

- **pip**: `actions/setup-python`'s built-in `cache: pip`, keyed on the
  hash of `requirements.txt`/`requirements-dev.txt` — a dependency change
  invalidates it automatically.
- **Playwright's Chromium binary**: a dedicated `actions/cache` step
  keyed on `requirements.txt`'s hash (which pins Playwright's floor
  version). `playwright install --with-deps chromium` still runs every
  time regardless of cache hit — Playwright's own installer already
  no-ops when the exact build it needs is already present at the cached
  path, so this is safe (never silently stale) while still skipping the
  actual download on a cache hit.
- **Docker layers**: `cache-from`/`cache-to: type=gha` on the
  `docker/build-push-action` step — GitHub's own Actions cache backend,
  no extra registry or credentials needed.

## Running the same checks locally

```bash
# One-time setup
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium

# Lint (same command CI runs)
ruff check .

# Tests with coverage (same command CI runs)
pytest --cov=job_automation --cov-report=xml --cov-report=term-missing --junitxml=pytest-report.xml

# Docker build + smoke test (same steps CI runs)
docker build -t uk-healthcare-job-automation:local .
docker run -d --name smoke -p 8000:8000 uk-healthcare-job-automation:local
curl -sf http://localhost:8000/health   # retry for a few seconds if this is empty at first
docker logs smoke
docker rm -f smoke
```

## Setting up the GitHub repo

This project's own local git history was initialized as part of this
milestone (no GitHub remote existed before). To actually get workflow
runs and a working badge:

1. Create an empty repository on GitHub (don't let GitHub initialize it
   with a README/license — this repo already has its own history).
2. `git remote add origin https://github.com/<owner>/<repo>.git`
3. `git push -u origin master`
4. Replace `OWNER/REPO` in `README.md`'s CI badge URL with the real
   `<owner>/<repo>` — until then the badge renders as a broken image
   since there's no workflow run for it to report on.
5. The workflow needs no repository secrets — every credential (Anthropic
   API key, SMTP password, etc.) is only ever used by the running
   application, never by CI, which mocks/fixtures all of it (see
   "What each job does" above).

## Branch protection recommendations

Once pushed to GitHub, under **Settings → Branches → Add branch
protection rule** for `main`/`master`:

- **Require a pull request before merging** — no direct pushes to the
  default branch, even for the repo owner.
- **Require status checks to pass before merging**, and select all three
  jobs (`Lint (ruff)`, `Test (pytest)`, `Build & validate Docker image`)
  once they've each run at least once (GitHub only lists checks that
  have executed before).
- **Require branches to be up to date before merging** — catches a PR
  that passed CI against an older `main` but would conflict/break
  against the current one.
- **Require conversation resolution before merging** — if using PR review
  comments.
- Optionally, **Require a pull request review from someone other than
  the author** once there's more than one contributor.
- Do **not** enable "Require signed commits" unless you already have
  commit signing set up locally — it silently blocks every push
  otherwise.

## Compatibility with Railway/Render/Docker deployments

This workflow is CI only — it never deploys anywhere, deliberately:

- **Railway and Render** each build directly from this repo's `Dockerfile`
  via their own GitHub integration (triggered by their own webhook on
  push, independent of GitHub Actions) — see docs/DEPLOYMENT.md. This
  workflow validates the same `Dockerfile` builds and boots correctly
  *before* either platform ever attempts to, catching a broken image
  before a real deploy would.
- **Plain Docker/VPS deployments** (`docker compose up --build`, or a
  manual `docker build`) use the exact same `Dockerfile` this workflow
  already builds and smoke-tests on every push.
- Nothing in this workflow pushes an image to any registry (`push: false,
  load: true` on the build step) — if you later want GitHub Actions to
  also publish an image (e.g. to GHCR) for a platform that pulls a
  pre-built image rather than building it itself, that's an additive
  change to the `docker` job, not a replacement for it.

## Known limitations

- **The Docker build/smoke-test steps were written and validated for
  correctness by hand (the exact `docker build`/`docker run`/`curl`
  sequence documented above), but never executed by an actual CI run** —
  this milestone was completed in a sandbox with no `docker` binary
  available at all. Run the "Running the same checks locally" Docker
  section yourself once, or watch the first real workflow run on GitHub,
  before relying on it.
- **Tested against a single Python version (3.13)** — the one this
  project has actually been developed and tested against throughout.
  `pyproject.toml` declares `requires-python = ">=3.11"`, but no 3.11
  interpreter was available in this sandbox to actually verify
  compatibility, so the workflow doesn't claim a version matrix it can't
  back up. Add `3.11`/`3.12` to a `strategy.matrix.python-version` on the
  `test` job once verified.
- **No deployment step** — see "Compatibility" above; this is
  intentional, not an oversight, for this milestone specifically.
