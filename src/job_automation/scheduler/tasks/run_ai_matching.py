"""
Scheduled task: run AI matching for every active user against every active
job, using `SchedulerFakeLLMProvider` — never a real Anthropic call (this
task always runs in the background on a schedule, so it must never incur
real API cost/latency on its own; the manual "re-run with AI" dashboard
trigger, `web.routes.matches.rematch_with_ai`, is the only path that uses a
real provider, and only on an explicit user click).

Bridges the two `CandidateProfile` representations documented in
docs/CANDIDATE_PROFILE.md/docs/AI_MATCHING.md via `ai.profile_builder
.to_ai_profile()` — shared with that manual trigger, so this task and that
route don't each maintain their own conversion.

Users with no saved candidate profile are skipped (nothing to match
against) — not an error, just nothing to do for that user this run.

Publishes one `MATCH_COMPLETED` event per user who had at least one job
evaluated (never calls `NotificationService` directly — see
`notifications.events`'s module docstring), using the shared `event_bus`
singleton directly rather than constructor injection: this is a plain
function, not a service class, so there's no constructor to inject
through — the same reasoning this module already applies to importing
`settings` directly rather than taking it as a parameter.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.ai.matching_engine import MatchingEngine
from job_automation.ai.matching_service import MatchingService
from job_automation.ai.profile_builder import to_ai_profile
from job_automation.database.models.user import User
from job_automation.notifications.event_bus import event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.profile.profile_service import ProfileService
from job_automation.scheduler.fake_llm_provider import SchedulerFakeLLMProvider
from job_automation.utils.logger import logger


def run(session: Session) -> dict:
    profile_service = ProfileService(session)
    engine = MatchingEngine(llm_provider=SchedulerFakeLLMProvider())
    matching_service = MatchingService(session, engine)

    users_processed = 0
    users_skipped_no_profile = 0
    matches_evaluated = 0

    for user in session.scalars(select(User).where(User.is_active.is_(True))):
        rich_profile = profile_service.get(user.id)
        if rich_profile is None:
            users_skipped_no_profile += 1
            continue

        ai_profile = to_ai_profile(rich_profile)
        matches = matching_service.evaluate_active_jobs(ai_profile, user_id=user.id)
        matches_evaluated += len(matches)
        users_processed += 1
        if matches:
            event_bus.publish(
                Event(
                    event_type=EventType.MATCH_COMPLETED,
                    payload={"matches_evaluated": len(matches)},
                    user_id=user.id,
                ),
                session,
            )

    logger.info(
        "run_ai_matching: {} users processed, {} skipped (no profile), {} matches evaluated",
        users_processed,
        users_skipped_no_profile,
        matches_evaluated,
    )
    return {
        "users_processed": users_processed,
        "users_skipped_no_profile": users_skipped_no_profile,
        "matches_evaluated": matches_evaluated,
    }
