# Single-stage image: `pip install`, Playwright's Chromium + its system
# libraries, then the app itself. NHS Jobs/Trac Jobs ingestion
# (job_automation.scrapers.nhs / .trac, driven by the daily
# import_provider_jobs scheduled task) launches a real headless Chromium
# via Playwright — not an optional dependency, so the browser is installed
# at build time rather than lazily at first run.
FROM python:3.13-slim

WORKDIR /app

# Playwright's own installer (`playwright install --with-deps`) pulls in
# everything Chromium needs (fonts, X11/graphics libs, etc.) via apt — no
# need to hand-list them here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY alembic.ini ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Everything persistent (SQLite file, generated documents, logs, browser
# screenshots/downloads) lives under /app/data and /app/logs — mount these
# as volumes for anything beyond a throwaway container. A production
# deployment on Postgres (see docs/DEPLOYMENT.md) doesn't need the
# /app/data volume for the database itself, only for generated documents.
RUN mkdir -p /app/data /app/logs

ENV PYTHONUNBUFFERED=1 \
    LOG_DIR=/app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "job_automation.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
