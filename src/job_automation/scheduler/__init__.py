"""
Background job scheduler: runs a fixed set of safe, internal automation
tasks on a schedule (or on demand via the dashboard's "Run now" buttons),
recording full status history. Explicitly does **not** call a real LLM
provider, scrape a live website, send a notification/email, or submit an
application — every task operates only on local/fixture data and
`FakeLLMProvider`, per this milestone's constraints (see
docs/BACKGROUND_SCHEDULER.md).

Supersedes this package's original scaffold-era placeholder
(`job_scheduler.py`, from the very first project milestone), which
described a vague "daily scrape -> apply -> report pipeline" runner before
any of scraping, matching, documents, or workflow existed. `job_scheduler
.py` now holds the real `BackgroundScheduler` bootstrap; its scope changed
from "run the live pipeline daily" to "run 5 specific, safe, internal
tasks" — deliberately, since live scraping and automatic application
submission are both explicitly out of scope for this and every other
milestone.

- `scheduler_models.py` — `TaskStatus`, `TaskDefinition`, `TaskRunSummary`:
  dependency-free value objects, same convention as every other
  `*_models.py` file in this project.
- `scheduler_repository.py` — persists `SchedulerTaskRunRecord` rows. Pure
  data access, no business logic.
- `task_registry.py` — the 5 concrete `TaskDefinition`s this milestone
  requires, each wrapping a function in `tasks/`.
- `scheduler_service.py` — `SchedulerService`: the orchestrator. Owns
  per-task locking (a `threading.Lock` per task name — "the same task
  cannot run twice at the same time"), retries (reusing
  `core.RetryManager`, the same backoff logic `BrowserManager`/
  `AnthropicProvider` already use), and status-history recording. Used by
  both the periodic APScheduler trigger and the dashboard's manual
  "Run now" action, so both paths share one lock and one history table.
- `job_scheduler.py` — creates and (if `settings.scheduler_enabled`)
  starts the `BackgroundScheduler`, wiring each `TASK_REGISTRY` entry to
  an interval trigger that calls `SchedulerService.run_task(name,
  triggered_by="schedule")`.
- `tasks/` — the 5 task functions themselves, each a thin orchestration
  layer over already-existing services (`JobIngestionService`,
  `MatchingService`, `DocumentService`, `WorkflowService`) — no new
  business logic beyond what "run this on a schedule" requires.
"""

from job_automation.scheduler.scheduler_models import TaskDefinition, TaskRunSummary, TaskStatus
from job_automation.scheduler.scheduler_service import SchedulerService

__all__ = ["SchedulerService", "TaskDefinition", "TaskRunSummary", "TaskStatus"]
