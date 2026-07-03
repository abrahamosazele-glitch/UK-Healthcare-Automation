"""
For every newly-imported job, automatically scores it against every active
user's saved candidate profile — reusing `MatchingEngine`/`MatchingService`
and the `ai.profile_builder.to_ai_profile()` adapter exactly as the manual
"Re-run with AI" dashboard button does (`web.routes.matches.rematch_with_ai`)
— and publishes a notification event when the result is worth surfacing:

- `NEW_HIGH_MATCH_JOB` — score exceeds `settings
  .job_ingestion_high_match_threshold` (80% by default).
- `NEW_BAND3_JOB` — `job.band == "Band 3"`, regardless of match score.
- `NEW_SPONSORSHIP_JOB` — `job.visa_sponsorship is True`, regardless of
  match score.

**Matching is automatic; document generation never is.** This is a
deliberate milestone decision: every previous AI-integration milestone in
this project made real Anthropic calls explicit-click-only specifically to
control cost and avoid surprise actions. Automating the *match* (a single,
cheap-ish scoring call) while keeping the four *document* generations
(cover letter, supporting statement, interview prep, skills-gap analysis —
each a separate, larger, billed call) behind an explicit click on the job's
detail page preserves that invariant while still delivering "tell me when
something great shows up" automatically. See docs/JOB_INGESTION.md.

Uses a real `AnthropicProvider` when `settings.anthropic_api_key` is
configured, falling back to `SchedulerFakeLLMProvider` (same as
`scheduler.tasks.run_ai_matching`) when it isn't — so a scheduled
ingestion run never hard-fails just because no key is set yet; it only
means the match scores it computes are rule-based/placeholder rather than
real AI, exactly like every other scheduled task's stance on this.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.ai.anthropic_provider import AnthropicProvider
from job_automation.ai.cache import MatchCache
from job_automation.ai.matching_engine import MatchingEngine
from job_automation.ai.matching_service import MatchingService
from job_automation.ai.profile_builder import to_ai_profile
from job_automation.config.settings import settings
from job_automation.core.retry_manager import RetryManager
from job_automation.database.models.job import Job
from job_automation.database.models.user import User
from job_automation.notifications.event_bus import event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.profile.profile_service import ProfileService
from job_automation.utils.logger import logger


def process_new_jobs(session: Session, job_ids: list[uuid.UUID]) -> dict:
    """Run AI matching for every (new job, active user with a saved
    profile) pair and publish `NEW_HIGH_MATCH_JOB`/`NEW_BAND3_JOB`/
    `NEW_SPONSORSHIP_JOB` events as appropriate. Returns a small summary
    dict for the scheduled task's result_summary."""
    summary = {"high_match_notifications": 0, "band3_notifications": 0, "sponsorship_notifications": 0}
    if not job_ids:
        return summary

    jobs = list(session.scalars(select(Job).where(Job.id.in_(job_ids))))
    if not jobs:
        return summary

    users = list(session.scalars(select(User).where(User.is_active.is_(True))))
    profile_service = ProfileService(session)
    engine = MatchingEngine(llm_provider=_build_llm_provider(), cache=MatchCache())
    matching_service = MatchingService(session, engine)

    for job in jobs:
        employer_name = job.employer.name if job.employer else None
        base_payload = {"job_id": str(job.id), "job_title": job.title, "employer_name": employer_name}

        for user in users:
            rich_profile = profile_service.get(user.id)
            if rich_profile is None:
                continue
            try:
                match = matching_service.evaluate_job(job, to_ai_profile(rich_profile), user_id=user.id)
            except Exception as exc:
                logger.error("Auto-match failed for job {} / user {}: {}", job.id, user.id, exc)
                continue

            score = float(match.match_score)
            if score > settings.job_ingestion_high_match_threshold:
                event_bus.publish(
                    Event(
                        event_type=EventType.NEW_HIGH_MATCH_JOB,
                        user_id=user.id,
                        payload={**base_payload, "match_score": score},
                    ),
                    session,
                )
                summary["high_match_notifications"] += 1

        if job.band == "Band 3":
            for user in users:
                event_bus.publish(
                    Event(
                        event_type=EventType.NEW_BAND3_JOB,
                        user_id=user.id,
                        payload={**base_payload, "band": job.band},
                    ),
                    session,
                )
                summary["band3_notifications"] += 1

        if job.visa_sponsorship:
            for user in users:
                event_bus.publish(
                    Event(event_type=EventType.NEW_SPONSORSHIP_JOB, user_id=user.id, payload=base_payload),
                    session,
                )
                summary["sponsorship_notifications"] += 1

    return summary


def _build_llm_provider():
    if not settings.anthropic_api_key:
        # Deferred import: `job_automation.scheduler`'s package `__init__`
        # eagerly imports `SchedulerService` -> `task_registry` ->
        # `scheduler.tasks.import_provider_jobs`, which imports back into
        # this very package (`ingestion.auto_match_service`) — a genuine
        # circular import if this were a top-level import here. Deferring
        # it to call time (this function only ever runs after both
        # packages have finished initializing) breaks the cycle without
        # changing any public API.
        from job_automation.scheduler.fake_llm_provider import SchedulerFakeLLMProvider

        logger.info("No Anthropic API key configured — auto-match will use rule-based/placeholder scoring only.")
        return SchedulerFakeLLMProvider()
    return AnthropicProvider(
        settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.anthropic_timeout_seconds,
        retry_manager=RetryManager(max_retries=settings.anthropic_max_retries),
        input_cost_per_million_usd=settings.anthropic_input_cost_per_million_usd,
        output_cost_per_million_usd=settings.anthropic_output_cost_per_million_usd,
    )
