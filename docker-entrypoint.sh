#!/bin/sh
# Runs on every container start, before the app: applies any pending Alembic
# migrations, then hands off to the actual command (`CMD` in the Dockerfile,
# or whatever's passed to `docker run`). Idempotent — `alembic upgrade head`
# is a no-op once the schema is already current, so this is safe to run on
# every restart of an existing container, not just the first one.
set -e

echo "Running database migrations..."
alembic upgrade head

exec "$@"
