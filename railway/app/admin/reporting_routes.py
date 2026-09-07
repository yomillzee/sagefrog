"""Admin routes for the agency-wide reporting pages.

Lifted out of main.py. Benchmarks, agency trends and the budget HQ: each is a
page plus the JSON feed it fetches, and none of them belong to a single client.

Registered by admin.register_admin_routes(); see admin/__init__.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import web_auth
import web_users

router = APIRouter(include_in_schema=False)


@router.get("/admin/hq")
def admin_budget_hq(request: Request):
    """Legacy 'Budget HQ' — superseded by HQ (the DuckDB agency overview at
    /admin/agency-trends). Redirect so old links/bookmarks land on the new HQ."""
    return RedirectResponse(url="/admin/agency-trends", status_code=307)

@router.get("/admin/hq/data")
def admin_budget_hq_data(
    user: web_users.WebUser = Depends(web_auth.require_admin),
) -> dict:
    """JSON feed for the Budget HQ page: MTD spend + pacing for every client."""
    from dashboard.services.hq_budget_service import build_hq_budget_overview

    return build_hq_budget_overview()

@router.get("/admin/agency-trends", include_in_schema=False, response_class=HTMLResponse)
def admin_agency_trends(request: Request):
    """Admin-only 'Agency Trends': every client's spend vs budget, primary KPI
    (date-range filterable), and channel mix, computed in one DuckDB scan."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/agency-trends")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.agency_trends_renderer import render_agency_trends_page

    return HTMLResponse(render_agency_trends_page(user_email=user.email))

@router.get("/admin/agency-trends/data")
def admin_agency_trends_data(
    kpi_range: str = "month",
    include_today: bool = False,
    user: web_users.WebUser = Depends(web_auth.require_admin),
) -> dict:
    """JSON feed for the Agency Trends page: the DuckDB HQ reproduction (spend vs
    budget + sessions) plus cross-client week-over-week momentum.

    ``kpi_range`` (month | last_week | last_30d) and ``include_today`` scope only
    the primary-KPI column; budget pacing and the sessions sparkline are fixed."""
    from dashboard.services.agency_trends_service import build_agency_overview

    return build_agency_overview(kpi_range=kpi_range, include_today=include_today)

@router.get("/admin/benchmarks", include_in_schema=False, response_class=HTMLResponse)
def admin_benchmarks(request: Request):
    """Admin-only 'Benchmarks': agency averages for each metric, bucketed by the
    industry tag on each account."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/benchmarks")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.agency_benchmarks_renderer import render_agency_benchmarks_page

    return HTMLResponse(render_agency_benchmarks_page(user_email=user.email))

@router.get("/admin/benchmarks/data")
def admin_benchmarks_data(
    window: str = "month",
    platform: str = "all",
    user: web_users.WebUser = Depends(web_auth.require_admin),
) -> dict:
    """JSON feed for the Benchmarks page: per-industry distributions of every
    registered metric, plus each client's own value.

    ``window`` (month | last_30d) and ``platform`` (all | google | linkedin | …)
    scope the aggregation. Both read the Health page's warm per-client caches, so
    a refresh normally costs no BigQuery."""
    from dashboard.services.agency_benchmarks_service import build_agency_benchmarks

    return build_agency_benchmarks(window=window, platform=platform)
