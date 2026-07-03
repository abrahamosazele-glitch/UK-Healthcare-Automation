"""
JSON API for dashboard summary data, analytics (chart data for
`js/charts.js`), and candidate profile — grouped here as this milestone's
"core/misc" API surface (see `api/__init__.py`'s docstring for the mapping
of all 8 conceptual areas across the 4 specified API files).

Every endpoint calls `AnalyticsService`/`ProfileService` and returns their
already-dataclass-shaped output directly — FastAPI's `jsonable_encoder`
serializes plain dataclasses natively, so no extra schema/serializer layer
was added for these.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from job_automation.analytics import AnalyticsReport, AnalyticsService, DashboardSummary
from job_automation.analytics.analytics_models import JobIngestionSummary, JobMarketAnalytics, JobOrganizationSummary
from job_automation.database.models.user import User
from job_automation.profile.profile_service import ProfileService
from job_automation.web.app import get_current_api_user, get_db_session

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> DashboardSummary:
    return AnalyticsService(session).dashboard_summary(current_user.id)


@router.get("/job-organization")
def job_organization_summary(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> JobOrganizationSummary:
    return AnalyticsService(session).job_organization_summary(current_user.id)


@router.get("/analytics")
def dashboard_analytics(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> AnalyticsReport:
    return AnalyticsService(session).build_report(current_user.id)


@router.get("/job-ingestion")
def job_ingestion_summary(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> JobIngestionSummary:
    return AnalyticsService(session).job_ingestion_summary()


@router.get("/job-market-analytics")
def job_market_analytics(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> JobMarketAnalytics:
    return AnalyticsService(session).job_market_analytics()


@router.get("/candidate-profile")
def candidate_profile(
    session: Session = Depends(get_db_session), current_user: User = Depends(get_current_api_user)
) -> dict:
    profile = ProfileService(session).get(current_user.id)
    if profile is None:
        return {"profile": None}
    return {"profile": profile.to_dict()}
