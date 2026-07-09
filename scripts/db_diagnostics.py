"""
Read-only CLI diagnostic: prints the active database URL, job counts by
source, the 10 most recently discovered jobs, and how many scheduler task
runs are recorded. Same report as the `/diagnostics/database` route
(`database.diagnostics.collect_database_diagnostics()`), for checking a
deployment's database without a browser session — e.g. against Railway's
database directly:

    railway run python scripts/db_diagnostics.py

Or locally:

    .venv\\Scripts\\python.exe scripts\\db_diagnostics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from job_automation.database.db_manager import get_session  # noqa: E402
from job_automation.database.diagnostics import collect_database_diagnostics  # noqa: E402


def main() -> None:
    with get_session() as session:
        diagnostics = collect_database_diagnostics(session)

    print(f"Database URL: {diagnostics.database_url}")
    print(f"Total jobs: {diagnostics.total_jobs}")
    print("Jobs by source:")
    if diagnostics.jobs_by_source:
        for source, count in diagnostics.jobs_by_source.items():
            print(f"  {source}: {count}")
    else:
        print("  (none)")

    print(f"Scheduler task runs recorded: {diagnostics.scheduler_task_run_count}")

    print("Latest jobs:")
    if diagnostics.latest_jobs:
        for job in diagnostics.latest_jobs:
            print(f"  [{job.source_site}] {job.title} ({job.created_at})")
    else:
        print("  (none)")


if __name__ == "__main__":
    main()
