"""
Analytics page shell — the actual chart data is fetched client-side from
`GET /api/dashboard/analytics` (see `api/dashboard_api.py` and `js/charts.js`)
rather than being computed here, so the page loads instantly and the same
JSON endpoint is reusable by anything else that wants this data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from job_automation.database.models.user import User
from job_automation.web.app import get_current_user, templates

router = APIRouter()


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "analytics.html", {"active_page": "analytics", "current_user": current_user}
    )
