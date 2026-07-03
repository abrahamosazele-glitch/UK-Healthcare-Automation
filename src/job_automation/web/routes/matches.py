"""
AI Matches page — lists every JobMatch for the current candidate.

`rematch_with_ai` is the one manual trigger that runs the real
`AnthropicProvider` for job matching (background `run_ai_matching` always
uses `SchedulerFakeLLMProvider` — see that task's module docstring): a
candidate looking at a rule-based-only match (`analysis.used_llm == False`,
surfaced by `components/match_card.html`'s "rule-based only" badge) can ask
for a genuine LLM analysis of that one job, on an explicit click — never
automatically.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_automation.ai.cache import MatchCache
from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_engine import MatchingEngine
from job_automation.ai.matching_service import MatchingService
from job_automation.ai.profile_builder import to_ai_profile
from job_automation.database.models.user import User
from job_automation.database.repositories.job_match_repository import JobMatchRepository
from job_automation.database.repositories.job_repository import JobRepository
from job_automation.notifications.event_bus import event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.profile.profile_service import ProfileService
from job_automation.web.app import get_current_user, get_db_session, get_llm_provider, get_match_cache, templates

router = APIRouter()


@router.get("/matches", response_class=HTMLResponse)
def matches_list(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    matches = JobMatchRepository(session).list_for_user(current_user.id)
    return templates.TemplateResponse(
        request,
        "matches.html",
        {"active_page": "matches", "current_user": current_user, "matches": matches},
    )


@router.post("/matches/{job_id}/rematch")
def rematch_with_ai(
    job_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    match_cache: MatchCache = Depends(get_match_cache),
) -> RedirectResponse:
    job = JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    profile = ProfileService(session).get(current_user.id)
    if profile is None:
        raise HTTPException(status_code=400, detail="No candidate profile saved — cannot run AI matching")

    engine = MatchingEngine(llm_provider=llm_provider, cache=match_cache)
    matching_service = MatchingService(session, engine)
    matching_service.evaluate_job(job, to_ai_profile(profile), user_id=current_user.id)

    event_bus.publish(
        Event(event_type=EventType.MATCH_COMPLETED, payload={"matches_evaluated": 1}, user_id=current_user.id),
        session,
    )

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
