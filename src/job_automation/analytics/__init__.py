"""
Analytics: computes aggregate statistics for the web dashboard (job counts,
match score distribution, interview/offer rates, document approval rates,
top employers, most-requested skills). New this milestone — no analytics
capability existed anywhere in this codebase before it.
"""

from job_automation.analytics.analytics_models import (
    ActivityItem,
    AnalyticsReport,
    DashboardSummary,
    MonthlyCount,
    NamedCount,
    ScoreBucket,
    UpcomingDeadline,
)
from job_automation.analytics.analytics_service import AnalyticsService

__all__ = [
    "ActivityItem",
    "AnalyticsReport",
    "DashboardSummary",
    "MonthlyCount",
    "NamedCount",
    "ScoreBucket",
    "UpcomingDeadline",
    "AnalyticsService",
]
