"""JSON API routes for client-facing dashboard data."""

from __future__ import annotations

import logging
import traceback
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import bigquery_service
import marketing_service
import web_auth
from dashboard.utils import pacing
from dashboard.renderers.bigquery_settings_renderer import render_bigquery_settings_page
from dashboard.renderers.gtm_renderer import render_gtm_page
from dashboard.renderers.bigquery_dashboard_renderer import render_bigquery_dashboard_page
from dashboard.routes.helpers import (
    penn_html_session_kwargs,
    session_can_switch_clients,
    validate_client_slug,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# The Nixon portal is reachable under two registered slugs: "nixon-bq-test" (the
# dashboard the picker lists and that per-client access grants target) and
# "nixon" (the legacy connector-config / marketing key its routes read under).
# Access to either must open it — otherwise a standard user granted only
# "nixon-bq-test" sees "Nixon Medical" in their picker but 403s on every route
# here, which all authed against the bare "nixon" slug.
_NIXON_ACCESS_SLUGS = ("nixon-bq-test", "nixon")

# Trailing window for the branded/target keyword weekly-position TREND chart
# (~13 weeks). Independent of the dashboard's selected date range so a short
# range like Last 7d still shows a multi-week "movement over time" line.
_KEYWORD_TREND_DAYS = 90


def _resolve_marketing_dates(
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    if start_date is None and end_date is None:
        end_date = date.today()
        start_date = end_date - timedelta(days=29)
    elif start_date is None and end_date is not None:
        start_date = end_date - timedelta(days=29)
    elif start_date is not None and end_date is None:
        end_date = date.today()

    assert start_date is not None
    assert end_date is not None
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date.")
    return start_date, end_date


def _resolve_compare_dates(
    compare_start: date | None,
    compare_end: date | None,
) -> tuple[date | None, date | None]:
    """Validate an explicit comparison window from the dashboard's Compare
    picker. Returns (None, None) when either bound is missing, which tells the
    service layer to fall back to the preceding same-length period."""
    if compare_start is None or compare_end is None:
        return None, None
    if compare_start > compare_end:
        raise HTTPException(
            status_code=400,
            detail="compare_start_date must be on or before compare_end_date.",
        )
    return compare_start, compare_end


def _load_bq_test_config(slug: str) -> tuple[str, str]:
    """Return (gcp_project_id, bq_mart_dataset_id) for a BigQuery-mode client.

    Reads directly from client_dashboard_config -- any client with a
    dashboard row and a configured GCP project can use the generic BQ
    dashboard template, no separate allowlist to maintain per client.
    Raises 404 if the client has no dashboard config at all, or 503 if it
    has one but no GCP project configured yet.
    """
    try:
        import client_dashboard_config as cdc
        row = cdc.get_config(slug)
    except Exception:
        row = None
    # The demo client has no GCP project by design — its data is synthetic and
    # served by demo_data via _cached_bq_read (which short-circuits before any
    # BigQuery read). Return a placeholder destination so the endpoint proceeds
    # to that short-circuit instead of 503-ing on the missing project.
    import demo_client
    if demo_client.is_demo(slug):
        return "demo", "marketing_marts"
    if row is None:
        raise HTTPException(status_code=404, detail=f"No dashboard configured for client '{slug}'.")
    project_id = (row.gcp_project_id or "").strip()
    dataset_id = (row.bq_mart_dataset_id or "").strip() or "marketing_marts"
    if not project_id:
        raise HTTPException(
            status_code=503,
            detail=f"BigQuery project not configured for '{slug}'. Set it in Settings.",
        )
    return project_id, dataset_id


def _load_page_path_filter(slug: str) -> list[str]:
    """The client's persisted Website Analytics page-path scope as a list of
    substring patterns (empty = whole site). Admins set it from the analytics
    page's "Edit page filter" editor; the page-path fetch endpoints pass it into
    marketing_service so their BigQuery reads only return matching paths."""
    try:
        import client_dashboard_config as cdc
        row = cdc.get_config(slug)
    except Exception:
        row = None
    if row is None:
        return []
    return marketing_service.parse_page_path_filter(
        getattr(row, "analytics_page_path_filter", None)
    )


def _bq_endpoint_failure(exc: Exception) -> HTTPException:
    logger.exception("Nixon endpoint failed")
    logger.error("Nixon endpoint traceback:\n%s", traceback.format_exc())
    return HTTPException(
        status_code=500,
        detail={
            "error": str(exc),
            "type": type(exc).__name__,
        },
    )


def _cached_bq_read(source: str, payload: dict, *, ttl_seconds: int, fetch) -> dict:
    """DB-backed cache for BigQuery-mart dashboard read endpoints (any client).

    Every underlying BQ table here is only updated by a daily (or slower)
    connector sync, so re-querying BigQuery on every page load/refresh never
    returns fresher data — it just re-pays the same query cost. ttl_seconds
    is a worst-case staleness bound; bigquery_refresh_orchestrator (and the
    manual "Run sync now" button) call db_cache.invalidate_prefix(f"{slug}.")
    right after a sync completes, so in practice a fresh sync is visible
    immediately rather than after the TTL. `source` should be namespaced
    "{client_slug}.{thing}" so invalidation only clears that client's cache.
    """
    # Demo client short-circuit: serve deterministic synthetic data instead of
    # querying BigQuery. Every panel funnels through this helper with a source
    # of the form "{slug}.{thing}", so this single hook populates the entire
    # demo dashboard (and any future panel that reuses this helper) with no GCP
    # project, connectors, or live data. See demo_data / demo_client.
    import demo_client
    _slug = source.split(".", 1)[0]
    if demo_client.is_demo(_slug):
        import demo_data
        _key = source.split(".", 1)[1] if "." in source else ""
        return demo_data.generate(_key, payload)

    import db_cache
    # Optional global TTL floor. These BQ tables only change on a sync, which
    # invalidates the cache immediately (see invalidate_prefix), so a longer TTL
    # keeps caches warm between syncs with zero staleness cost — only the first
    # load after a sync is cold. A super admin sets this from the Admin page
    # (app_settings.dashboard_cache_ttl_seconds); it falls back to the
    # DASH_CACHE_TTL_SECONDS environment variable, then to the per-endpoint
    # defaults (15 min) when neither is set. The lookup is memoized in-process
    # so this stays a cheap read on the hot path.
    try:
        import app_settings
        _floor = app_settings.dashboard_cache_ttl_seconds()
    except Exception:
        _floor = 0
    if _floor > 0:
        ttl_seconds = max(ttl_seconds, _floor)
    try:
        hit = db_cache.get_cached(source, payload)
    except Exception:
        # A transient cache-store (Postgres) hiccup must not 500 the endpoint --
        # fall through to a live BigQuery read instead of failing the request.
        logger.warning("db_cache read failed for %s; falling back to live fetch", source, exc_info=True)
        hit = None
    if hit is not None:
        return hit.response_json
    result = fetch()
    try:
        db_cache.put_cached(
            source, payload,
            response_json=result,
            row_count=len(result) if isinstance(result, list) else 0,
            ttl_seconds=ttl_seconds,
        )
    except Exception:
        pass
    return result


# --- Shared card readers -------------------------------------------------------
# The core paid-media card endpoints (summary / marketing / health) delegate to
# these so the exact same cache source, payload, TTL, and fetch are defined in
# one place. Post-sync cache warming (dashboard_warm_service) calls the very same
# helpers, which guarantees a warmed entry is keyed identically to what the
# endpoint later reads — no key can drift out of sync between the two paths.
# Auth and date resolution stay in the endpoints; these assume an already
# authorized, already normalized (lowercased) client_key and resolved dates.

def _summary_read(
    normalized: str,
    start: date,
    end: date,
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
) -> dict:
    payload = {"start": start.isoformat(), "end": end.isoformat()}
    fetch = lambda: marketing_service.fetch_summary(start_date=start, end_date=end)
    if normalized == "nixon":
        return _cached_bq_read("nixon.summary", payload, ttl_seconds=900, fetch=fetch)
    with marketing_service.route(
        client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
    ):
        return _cached_bq_read(f"{normalized}.summary", payload, ttl_seconds=900, fetch=fetch)


def _pacing_read(
    normalized: str,
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
    budget_hint: float | None = None,
) -> dict:
    """Weekday-aware budget-pacing recommendation for one client.

    Reuses the cached /summary read over a trailing window (ending yesterday, the
    latest complete paid-data day), then runs the pure ``pacing`` module. The
    monthly goal and the optional active-days override come from the same
    ``client_dashboard_config`` the Budget tracking card reads; ``budget_hint``
    (the goal already injected into the card) is the fallback when the client's
    budget lives outside that table.
    """
    today = date.today()
    as_of = today - timedelta(days=1)
    start = as_of - timedelta(days=pacing.PROFILE_WINDOW_DAYS - 1)
    summary = _summary_read(
        normalized, start, as_of, project_id=project_id, dataset_id=dataset_id
    )
    daily_spend: dict[str, float] = {}
    for row in summary.get("daily") or []:
        key = str(row.get("date") or "")[:10]
        if not key:
            continue
        daily_spend[key] = daily_spend.get(key, 0.0) + float(row.get("spend") or 0.0)

    monthly_budget: float | None = budget_hint
    override: list[int] = []
    try:
        import client_dashboard_config as cdc
        cfg = cdc.get_config(normalized)
        if cfg:
            if cfg.monthly_budget_usd is not None:
                monthly_budget = float(cfg.monthly_budget_usd)
            override = cdc.parse_active_weekdays(cfg.pacing_active_weekdays)
    except Exception:
        logger.warning("pacing: config read failed for %s", normalized, exc_info=True)

    result = pacing.compute_pacing(
        monthly_budget=monthly_budget,
        daily_spend=daily_spend,
        today=today,
        active_weekdays_override=override or None,
    )
    return result.to_dict()


def _health_read(
    normalized: str,
    limit: int,
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
) -> dict:
    fetch = lambda: marketing_service.fetch_marketing_health(limit=limit)
    if normalized == "nixon":
        return _cached_bq_read(
            "nixon.marketing.health", {"limit": limit}, ttl_seconds=900, fetch=fetch,
        )
    with marketing_service.route(
        client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
    ):
        return _cached_bq_read(f"{normalized}.health", {"limit": limit}, ttl_seconds=900, fetch=fetch)


def _marketing_read(
    normalized: str,
    start: date,
    end: date,
    top_limit: int = 10,
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
) -> dict:
    payload = {"start": start.isoformat(), "end": end.isoformat(), "top_limit": top_limit}
    fetch = lambda: marketing_service.fetch_marketing(
        start_date=start, end_date=end, top_limit=top_limit,
    )
    if normalized == "nixon":
        return _cached_bq_read("nixon.marketing", payload, ttl_seconds=900, fetch=fetch)
    with marketing_service.route(
        client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
    ):
        return _cached_bq_read(f"{normalized}.marketing", payload, ttl_seconds=900, fetch=fetch)


def _traffic_acquisition_read(
    normalized: str,
    start: date,
    end: date,
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
    page_path_filter: list[str] | None = None,
) -> dict:
    """GA4 traffic-acquisition breakdown — the Overview 'Website analytics' card.

    A page-path scope narrows the daily sessions series (the analytics tab's
    Sessions-over-time chart); the channel/source breakdowns stay site-wide.
    """
    path_filter = page_path_filter or []
    payload = {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)}
    fetch = lambda: marketing_service.fetch_traffic_acquisition(
        start_date=start, end_date=end, page_path_filter=path_filter,
    )
    if normalized == "nixon":
        return _cached_bq_read(
            "nixon.pages.traffic_acquisition", payload, ttl_seconds=900, fetch=fetch,
        )
    with marketing_service.route(
        client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
    ):
        return _cached_bq_read(
            f"{normalized}.pages.traffic_acquisition", payload, ttl_seconds=900, fetch=fetch,
        )


def _ai_traffic_daily_read(
    normalized: str,
    start: date,
    end: date,
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
    page_path_filter: list[str] | None = None,
) -> dict:
    """Daily AI-referral sessions by platform — the Overview 'AI traffic' card."""
    path_filter = page_path_filter or []
    payload = {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)}
    fetch = lambda: marketing_service.fetch_ai_traffic_daily(
        start_date=start, end_date=end, page_path_filter=path_filter,
    )
    if normalized == "nixon":
        return _cached_bq_read(
            "nixon.ai_traffic.daily", payload, ttl_seconds=900, fetch=fetch,
        )
    with marketing_service.route(
        client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
    ):
        return _cached_bq_read(
            f"{normalized}.ai_traffic.daily", payload, ttl_seconds=900, fetch=fetch,
        )


@router.get(
    "/dashboard/nixon-bq-test",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def nixon_bigquery_test_dashboard(request: Request):
    auth = web_auth.authenticate_dashboard_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    if isinstance(auth, RedirectResponse):
        return auth
    return HTMLResponse(render_bigquery_dashboard_page(
        session_can_switch_clients=session_can_switch_clients(auth),
        **penn_html_session_kwargs(auth),
    ))


@router.get(
    "/dashboard/nixon-bq-test/analytics",
    include_in_schema=False,
)
def nixon_analytics_dashboard(request: Request):
    # The standalone Nixon analytics page was retired in favor of the shared
    # BigQuery dashboard's built-in Analytics view. Redirect the old URL there
    # (preserving any query params, e.g. the access key) so links keep working.
    params = dict(request.query_params)
    params["view"] = "analytics"
    return RedirectResponse(
        "/dashboard/nixon-bq-test?" + urlencode(params), status_code=308
    )


@router.get(
    "/dashboard/nixon-bq-test/gtm",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def nixon_gtm_dashboard(request: Request):
    auth = web_auth.authenticate_dashboard_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    if isinstance(auth, RedirectResponse):
        return auth
    kw = penn_html_session_kwargs(auth)
    return HTMLResponse(render_gtm_page(client_slug="nixon-bq-test", **kw))


@router.get(
    "/dashboard/{client_slug}/gtm",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def client_gtm_dashboard(client_slug: str, request: Request):
    """Event Tracking (GTM live-tags) page for any bigquery_nixon client.

    Registered after the nixon-specific /dashboard/nixon-bq-test/gtm route above,
    so that literal path still hits its own handler (which auths under "nixon").
    """
    slug = validate_client_slug(client_slug)
    import client_dashboard_config as _cdc

    db_cfg = _cdc.get_config(slug)
    if not (db_cfg and db_cfg.dashboard_mode == "bigquery_nixon"):
        raise HTTPException(status_code=404, detail="Not found")
    label = (db_cfg.label or slug).strip() or slug

    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    return HTMLResponse(
        render_gtm_page(client_slug=slug, label=label, **penn_html_session_kwargs(auth))
    )


def _nixon_settings_context() -> dict:
    """Account-id config for the settings page."""
    import client_config

    account_ids: dict = {}
    try:
        cfg = client_config.load_client_config("nixon")
        account_ids = {
            "google_customer_id": cfg.google_customer_id or "",
            "linkedin_account_id": cfg.linkedin_account_id or "",
            "meta_account_id": cfg.meta_account_id or "",
            "ga4_client_key": cfg.ga4_client_key or "",
        }
    except Exception:
        pass
    return account_ids


@router.get(
    "/dashboard/nixon-bq-test/settings",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def nixon_bq_settings_page(
    request: Request,
    saved: str | None = None,
    bq_error: str | None = None,
):
    auth = web_auth.authenticate_dashboard_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    if isinstance(auth, RedirectResponse):
        return auth
    html_kw = penn_html_session_kwargs(auth)

    account_ids = _nixon_settings_context()
    flash = None
    flash_error = None
    if saved:
        flash = "Account IDs saved."
    if bq_error:
        flash_error = str(bq_error)[:300]

    # The monthly budget, budget-tracker visibility, and explorer filters are all
    # saved under the "nixon-bq-test" slug (the budget POST + settings save write
    # there). Load that row back so the saved values re-render on this page —
    # without this the budget/goal field always came back blank after a save,
    # which looked like the budget wasn't saving at all.
    import client_dashboard_config

    db_cfg = client_dashboard_config.get_config("nixon-bq-test")

    return HTMLResponse(
        render_bigquery_settings_page(
            account_ids=account_ids,
            flash=flash,
            flash_error=flash_error,
            explorer_filters=(getattr(db_cfg, "explorer_filters", None) or ""),
            monthly_budget=getattr(db_cfg, "monthly_budget_usd", None),
            budget_tracker_enabled=bool(getattr(db_cfg, "explorer_budget_tracker", True)),
            consent_sidebar_enabled=bool(getattr(db_cfg, "consent_sidebar_enabled", False)),
            **html_kw,
        )
    )


@router.get(
    "/api/clients/nixon/marketing",
    summary="Nixon paid media performance from BigQuery marketing mart",
)
def nixon_marketing(
    request: Request,
    start_date: date | None = Query(
        default=None,
        description="Inclusive start date. Defaults to 29 days before end_date/today.",
    ),
    end_date: date | None = Query(
        default=None,
        description="Inclusive end date. Defaults to today.",
    ),
    top_limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Number of top campaigns by spend to return.",
    ),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _marketing_read("nixon", start, end, top_limit)
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/summary",
    summary="Client paid media summary from BigQuery marketing mart",
)
def client_summary(
    client_key: str,
    request: Request,
    start_date: date | None = Query(
        default=None,
        description="Inclusive start date. Defaults to 29 days before end_date/today.",
    ),
    end_date: date | None = Query(
        default=None,
        description="Inclusive end date. Defaults to today.",
    ),
) -> dict:
    normalized = (client_key or "").strip().lower()
    if normalized == "nixon":
        web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
        start, end = _resolve_marketing_dates(start_date, end_date)
        try:
            return _summary_read("nixon", start, end)
        except Exception as exc:
            raise _bq_endpoint_failure(exc) from exc
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _summary_read(
            normalized, start, end, project_id=project_id, dataset_id=dataset_id,
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/budget/pacing",
    summary="Weekday-aware budget pacing recommendation (suggested daily budget)",
)
def client_budget_pacing(
    client_key: str,
    request: Request,
    budget: float | None = Query(
        default=None,
        description="Monthly goal already shown on the card; used only when the "
        "client's budget isn't stored in client_dashboard_config.",
    ),
) -> dict:
    normalized = (client_key or "").strip().lower()
    if normalized == "nixon":
        web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
        try:
            return _pacing_read("nixon", budget_hint=budget)
        except Exception as exc:
            raise _bq_endpoint_failure(exc) from exc
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        return _pacing_read(
            normalized, project_id=project_id, dataset_id=dataset_id, budget_hint=budget,
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/marketing/health",
    summary="Nixon paid media mart health from BigQuery",
)
def nixon_marketing_health(
    request: Request,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Maximum mart_health rows to return.",
    ),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    try:
        return _cached_bq_read(
            "nixon.marketing.health", {"limit": limit}, ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_marketing_health(limit=limit),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/health",
    summary="Client paid media mart health from BigQuery",
)
def client_health(
    client_key: str,
    request: Request,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Maximum health rows to return.",
    ),
) -> dict:
    normalized = (client_key or "").strip().lower()
    if normalized == "nixon":
        web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
        try:
            return _health_read("nixon", limit)
        except Exception as exc:
            raise _bq_endpoint_failure(exc) from exc
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        return _health_read(
            normalized, limit, project_id=project_id, dataset_id=dataset_id,
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/google-ads/keywords",
    summary="Nixon Google Ads search-keyword performance (Cost by Keyword) from BigQuery",
)
def nixon_google_ads_keywords(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.google_ads_keywords",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_google_ads_keywords(
                start_date=start, end_date=end,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/google-ads/explorer",
    summary="Nixon Google Ads explorer from BigQuery marketing mart",
)
def nixon_google_ads_explorer(
    request: Request,
    start_date: date | None = Query(
        default=None,
        description="Inclusive start date. Defaults to 29 days before end_date/today.",
    ),
    end_date: date | None = Query(
        default=None,
        description="Inclusive end date. Defaults to today.",
    ),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.google_ads",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_google_ads_explorer(
                start_date=start,
                end_date=end,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/gsc/summary",
    summary="Nixon Search Console summary (KPIs, daily, top queries + pages) from BigQuery",
)
def nixon_gsc_summary(
    request: Request,
    start_date: date | None = Query(
        default=None,
        description="Inclusive start date. Defaults to 29 days before end_date/today.",
    ),
    end_date: date | None = Query(
        default=None,
        description="Inclusive end date. Defaults to today.",
    ),
    compare_start_date: date | None = Query(
        default=None,
        description="Comparison window start (dashboard Compare picker). Defaults to the preceding same-length period.",
    ),
    compare_end_date: date | None = Query(default=None, description="Comparison window end."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    cmp_start, cmp_end = _resolve_compare_dates(compare_start_date, compare_end_date)
    try:
        import bq_gsc_service
        # GSC routes by client_slug; the Nixon dashboard's BQ client is
        # "nixon-bq-test" (where the GSC connector + raw_gsc/mart views live).
        return _cached_bq_read(
            "nixon.gsc.summary",
            {
                "start": start.isoformat(), "end": end.isoformat(),
                "compare_start": cmp_start.isoformat() if cmp_start else "",
                "compare_end": cmp_end.isoformat() if cmp_end else "",
            },
            ttl_seconds=900,
            fetch=lambda: bq_gsc_service.build_gsc_mart_summary(
                start=start, end=end, client_slug="nixon-bq-test",
                compare_start=cmp_start, compare_end=cmp_end,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/gsc/keyword-matches",
    summary="Nixon: queries matching branded/target keyword terms, scanned across the full date range",
)
def nixon_gsc_keyword_matches(
    request: Request,
    terms: str = Query(default="", description="Comma-separated include terms."),
    exclude: str = Query(default="", description="Comma-separated exclude terms."),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    compare_start_date: date | None = Query(
        default=None,
        description="Comparison window start for each row's prior avg position. Defaults to the preceding same-length period.",
    ),
    compare_end_date: date | None = Query(default=None, description="Comparison window end."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    term_list = [t.strip() for t in terms.split(",") if t.strip()]
    exclude_list = [t.strip() for t in exclude.split(",") if t.strip()]
    start, end = _resolve_marketing_dates(start_date, end_date)
    cmp_start, cmp_end = _resolve_compare_dates(compare_start_date, compare_end_date)
    if not term_list:
        return {"rows": [], "weekly": []}
    # The weekly trend is a "movement over time" chart, so it always spans a
    # trailing ~13-week window ending at the selected range's end -- otherwise a
    # short range (e.g. Last 7d) yields a single weekly bucket and no line. The
    # rows table stays scoped to the actually-selected range.
    trend_start = min(start, end - timedelta(days=_KEYWORD_TREND_DAYS))
    try:
        import bq_gsc_service
        # Same "nixon" marketing key -> "nixon-bq-test" BQ client_slug split as
        # nixon_gsc_summary above.
        cache_key = {
            "start": start.isoformat(), "end": end.isoformat(),
            "terms": sorted(term_list), "exclude": sorted(exclude_list),
            "compare_start": cmp_start.isoformat() if cmp_start else "",
            "compare_end": cmp_end.isoformat() if cmp_end else "",
        }
        # The weekly trend has no comparison window, so it keeps the old key —
        # switching Compare must not cold-miss a cache it can't change.
        trend_cache_key = {"start": trend_start.isoformat(), "end": end.isoformat(), "terms": sorted(term_list), "exclude": sorted(exclude_list)}
        rows = _cached_bq_read(
            "nixon.gsc.keyword_matches", cache_key, ttl_seconds=900,
            fetch=lambda: bq_gsc_service.gsc_keyword_matches(
                start=start, end=end, terms=term_list, exclude_terms=exclude_list,
                client_slug="nixon-bq-test",
                compare_start=cmp_start, compare_end=cmp_end,
            ),
        )
        weekly = _cached_bq_read(
            "nixon.gsc.keyword_weekly_trend", trend_cache_key, ttl_seconds=900,
            fetch=lambda: bq_gsc_service.gsc_keyword_weekly_trend(
                start=trend_start, end=end, terms=term_list, exclude_terms=exclude_list,
                client_slug="nixon-bq-test",
            ),
        )
        return {"rows": rows, "weekly": weekly}
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/semrush/summary",
    summary="Nixon SEMrush domain snapshot (overview, keywords, backlinks) from BigQuery",
)
def nixon_semrush_summary(
    request: Request,
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    try:
        import bq_semrush_service
        # SEMrush routes by client_slug; the Nixon dashboard's BQ client is
        # "nixon-bq-test" (same slug the GSC connector uses — see
        # nixon_gsc_summary above), not the marketing client_slug "nixon".
        # No date range and the underlying data only resyncs ~once/day, so
        # this gets a much longer TTL than the range-scoped endpoints.
        return _cached_bq_read(
            "nixon.semrush.summary", {}, ttl_seconds=6 * 3600,
            fetch=lambda: bq_semrush_service.fetch_latest_snapshot(
                client_key="nixon-bq-test", project="nixon-medical", mart_dataset="marketing_marts",
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pagespeed/summary",
    summary="Nixon PageSpeed Insights scores + Core Web Vitals from BigQuery",
)
def nixon_pagespeed_summary(
    request: Request,
    strategy: str = "desktop",
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    strat = (strategy or "desktop").strip().lower()
    if strat not in ("desktop", "mobile"):
        strat = "desktop"
    try:
        import bq_pagespeed_service
        # Like SEMrush/GSC, PageSpeed routes by the Nixon BQ client_slug
        # "nixon-bq-test" (not the marketing slug "nixon"). Point-in-time data
        # that resyncs ~once/day, so a long TTL like the other snapshot reads.
        return _cached_bq_read(
            f"nixon.pagespeed.summary.{strat}", {}, ttl_seconds=6 * 3600,
            fetch=lambda: bq_pagespeed_service.fetch_latest_snapshot(
                client_key="nixon-bq-test", project="nixon-medical", mart_dataset="marketing_marts",
                strategy=strat,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/linkedin/explorer",
    summary="Nixon LinkedIn creative explorer from BigQuery marketing mart",
)
def nixon_linkedin_explorer(
    request: Request,
    start_date: date | None = Query(
        default=None,
        description="Inclusive start date. Defaults to 29 days before end_date/today.",
    ),
    end_date: date | None = Query(
        default=None,
        description="Inclusive end date. Defaults to today.",
    ),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.linkedin",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_linkedin_explorer(
                start_date=start,
                end_date=end,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/meta/debug-insights",
    summary="Debug: probe Meta Insights API for Nixon ad account (no BQ write)",
    include_in_schema=False,
)
def nixon_meta_debug_insights(
    request: Request,
    level: str = Query(default="campaign", description="campaign | adset | ad"),
    days: int = Query(default=7, description="How many trailing days to probe"),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    import oauth_store, meta_service
    from datetime import date, timedelta
    from meta_auth import load_meta_env

    end = date.today()
    start = end - timedelta(days=days - 1)

    # Prefer the Nixon-scoped connector token, fall back to global
    token = (
        oauth_store.get_access_token("meta", client_slug="nixon-bq-test")
        or oauth_store.get_access_token("meta")
    )
    env = load_meta_env()
    account_id = "3566409366740666"

    try:
        import json as _json
        from meta_service import _act_id, _normalize_account_id, _time_range, _INSIGHT_FIELDS, _ADSET_INSIGHT_FIELDS, _AD_INSIGHT_FIELDS

        field_map = {
            "campaign": _INSIGHT_FIELDS,
            "adset": _ADSET_INSIGHT_FIELDS,
            "ad": _AD_INSIGHT_FIELDS,
        }
        fields = field_map.get(level, _INSIGHT_FIELDS)
        act = _act_id(_normalize_account_id(account_id))

        # raw first call — return what Meta actually sends before any parsing
        from meta_service import _graph_get
        raw = _graph_get(
            f"/{act}/insights",
            access_token=token or env.access_token,
            params={
                "fields": fields,
                "time_range": _time_range(start, end),
                "time_increment": 1,
                "level": level,
                "limit": 10,
            },
            env=env,
        )
        top_keys = list(raw.keys())
        data_len = len(raw.get("data") or [])
        sample = (raw.get("data") or [])[:2]
        return {
            "account_id": account_id,
            "act_id": act,
            "token_source": "client-scoped" if (token and oauth_store.get_access_token("meta", client_slug="nixon-bq-test")) else "global-fallback",
            "token_present": bool(token),
            "level": level,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "raw_top_keys": top_keys,
            "data_rows_in_first_page": data_len,
            "sample_rows": sample,
            "report_run_id": raw.get("report_run_id"),
            "error": raw.get("error"),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/clients/nixon/linkedin/demographics",
    summary="Nixon LinkedIn member demographics from BigQuery marketing mart",
)
def nixon_linkedin_demographics(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    window: str | None = Query(
        default=None,
        description="Synced window to serve (e.g. LAST_30_DAYS). Defaults to the one closest to the requested range.",
    ),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.linkedin_demographics",
            {"start": start.isoformat(), "end": end.isoformat(), "window": window or ""},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_linkedin_demographics(
                start_date=start, end_date=end, window=window,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/meta/explorer",
    summary="Nixon Meta Ads ad-level explorer from BigQuery marketing mart",
)
def nixon_meta_explorer(
    request: Request,
    start_date: date | None = Query(
        default=None,
        description="Inclusive start date. Defaults to 29 days before end_date/today.",
    ),
    end_date: date | None = Query(
        default=None,
        description="Inclusive end date. Defaults to today.",
    ),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.meta",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_meta_explorer(
                start_date=start,
                end_date=end,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


def _merge_verified(totals: dict, per_event: dict) -> dict:
    """Combine the blended by_ad_id totals with the per-key-event breakdown."""
    return {
        **totals,
        "events": per_event.get("events", []),
        "by_ad_id_event": per_event.get("by_ad_id_event", {}),
    }


def _merge_google_verified(totals: dict, per_event: dict) -> dict:
    """Combine Google by_campaign_id totals with the per-key-event breakdown."""
    return {
        **totals,
        "events": per_event.get("events", []),
        "by_campaign_id_event": per_event.get("by_campaign_id_event", {}),
    }


@router.get(
    "/api/clients/nixon/meta/verified-conversions",
    summary="Nixon GA4-verified conversions per Meta ad id from BigQuery",
)
def nixon_meta_verified_conversions(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.meta_verified",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: _merge_verified(
                marketing_service.fetch_meta_verified_conversions(start_date=start, end_date=end),
                marketing_service.fetch_meta_verified_key_events(start_date=start, end_date=end),
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/google-ads/verified-conversions",
    summary="Nixon GA4-verified conversions per Google Ads campaign id from BigQuery",
)
def nixon_google_verified_conversions(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.google_verified",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: _merge_google_verified(
                marketing_service.fetch_google_verified_conversions(start_date=start, end_date=end),
                marketing_service.fetch_google_verified_key_events(start_date=start, end_date=end),
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/linkedin/verified-conversions",
    summary="Nixon GA4-verified conversions per LinkedIn campaign-group name from BigQuery",
)
def nixon_linkedin_verified_conversions(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.explorer.linkedin_verified",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_linkedin_verified_key_events(
                start_date=start, end_date=end,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/top",
    summary="Nixon top pages (all traffic) from BigQuery",
)
def nixon_pages_top(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.pages.top",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_pages_top(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/sources",
    summary="Nixon page source / AI-referral breakdown from BigQuery",
)
def nixon_pages_sources(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.pages.sources",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_pages_sources(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/ai-traffic/daily",
    summary="Nixon daily AI-referral sessions by platform from BigQuery",
)
def nixon_ai_traffic_daily(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _ai_traffic_daily_read("nixon", start, end)
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/traffic-acquisition",
    summary="Nixon GA4 traffic acquisition breakdown from BigQuery",
)
def nixon_traffic_acquisition(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _traffic_acquisition_read("nixon", start, end)
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/device-split",
    summary="Nixon GA4 device category breakdown from BigQuery",
)
def nixon_device_split(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.pages.device_split",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_device_split(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/landing",
    summary="Nixon GA4 landing page performance from BigQuery",
)
def nixon_landing_pages(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.pages.landing",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_landing_pages(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/landing-events",
    summary="Nixon GA4 landing page × event breakdown (for key-event selection)",
)
def nixon_landing_page_events(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.pages.landing_events",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_landing_page_events(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/pages/top-key-events", summary="Nixon GA4 page path × event breakdown (all traffic)")
def nixon_top_pages_key_events(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.pages.top_key_events", {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_page_key_events(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/pages/traffic-key-events", summary="Nixon GA4 traffic source × event breakdown")
def nixon_traffic_key_events(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.pages.traffic_key_events", {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_traffic_key_events(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/analytics/user-acq-key-events", summary="Nixon GA4 first-user source × event breakdown")
def nixon_user_acq_key_events(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.analytics.user_acq_key_events", {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_user_acq_key_events(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/analytics/conversions", summary="Nixon GA4 conversion events breakdown")
def nixon_conversion_events(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.analytics.conversions",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_conversion_events(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/analytics/user-acquisition", summary="Nixon GA4 first-touch user acquisition")
def nixon_user_acquisition(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.analytics.user_acquisition",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_user_acquisition(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/analytics/demographics", summary="Nixon GA4 demographic & geographic breakdown")
def nixon_demographics(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _cached_bq_read(
            "nixon.analytics.demographics",
            {"start": start.isoformat(), "end": end.isoformat()},
            ttl_seconds=900,
            fetch=lambda: marketing_service.fetch_demographics(start_date=start, end_date=end),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/marketing/health",
    summary="Client paid media mart health from BigQuery (generic BQ-test clients)",
)
def client_marketing_health(
    client_key: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500, description="Maximum mart_health rows to return."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.marketing.health", {"limit": limit}, ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_marketing_health(limit=limit),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/google-ads/explorer",
    summary="Client Google Ads explorer from BigQuery marketing mart (generic BQ-test clients)",
)
def client_google_ads_explorer(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.google_ads",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_google_ads_explorer(
                    start_date=start, end_date=end,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/microsoft-ads/explorer",
    summary="Client Microsoft Ads campaign explorer from BigQuery marketing mart (generic BQ-test clients)",
)
def client_microsoft_ads_explorer(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.microsoft_ads",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_microsoft_explorer(
                    start_date=start, end_date=end,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/google-ads/keywords",
    summary="Client Google Ads search-keyword performance (Cost by Keyword) (generic BQ-test clients)",
)
def client_google_ads_keywords(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.google_ads_keywords",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_google_ads_keywords(
                    start_date=start, end_date=end,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/linkedin/explorer",
    summary="Client LinkedIn creative explorer from BigQuery marketing mart (generic BQ-test clients)",
)
def client_linkedin_explorer(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.linkedin",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_linkedin_explorer(
                    start_date=start, end_date=end,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/linkedin/demographics",
    summary="Client LinkedIn member demographics from BigQuery marketing mart (generic BQ-test clients)",
)
def client_linkedin_demographics(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    window: str | None = Query(
        default=None,
        description="Synced window to serve (e.g. LAST_30_DAYS). Defaults to the one closest to the requested range.",
    ),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.linkedin_demographics",
                {"start": start.isoformat(), "end": end.isoformat(), "window": window or ""},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_linkedin_demographics(
                    start_date=start, end_date=end, window=window,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/top",
    summary="Client top pages from BigQuery (generic BQ-test clients)",
)
def client_pages_top(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            path_filter = _load_page_path_filter(normalized)
            return _cached_bq_read(
                f"{normalized}.pages.top",
                {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_pages_top(
                    start_date=start, end_date=end, page_path_filter=path_filter,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/sources",
    summary="Client page source breakdown from BigQuery (generic BQ-test clients)",
)
def client_pages_sources(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            path_filter = _load_page_path_filter(normalized)
            return _cached_bq_read(
                f"{normalized}.pages.sources",
                {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_pages_sources(
                    start_date=start, end_date=end, page_path_filter=path_filter,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/ai-traffic/daily",
    summary="Client daily AI-referral sessions by platform (generic BQ-test clients)",
)
def client_ai_traffic_daily(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _ai_traffic_daily_read(
            normalized, start, end, project_id=project_id, dataset_id=dataset_id,
            page_path_filter=_load_page_path_filter(normalized),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/meta/explorer",
    summary="Client Meta Ads ad-level explorer from BigQuery marketing mart (generic BQ-test clients)",
)
def client_meta_explorer(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.meta",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_meta_explorer(
                    start_date=start, end_date=end,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/meta/verified-conversions",
    summary="Client GA4-verified conversions per Meta ad id from BigQuery (generic BQ-test clients)",
)
def client_meta_verified_conversions(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.meta_verified",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: _merge_verified(
                    marketing_service.fetch_meta_verified_conversions(start_date=start, end_date=end),
                    marketing_service.fetch_meta_verified_key_events(start_date=start, end_date=end),
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/google-ads/verified-conversions",
    summary="Client GA4-verified conversions per Google Ads campaign id from BigQuery (generic BQ-test clients)",
)
def client_google_verified_conversions(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.google_verified",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: _merge_google_verified(
                    marketing_service.fetch_google_verified_conversions(start_date=start, end_date=end),
                    marketing_service.fetch_google_verified_key_events(start_date=start, end_date=end),
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/linkedin/verified-conversions",
    summary="Client GA4-verified conversions per LinkedIn campaign-group name from BigQuery (generic BQ-test clients)",
)
def client_linkedin_verified_conversions(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.explorer.linkedin_verified",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_linkedin_verified_key_events(
                    start_date=start, end_date=end,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/traffic-acquisition",
    summary="Client GA4 traffic acquisition breakdown from BigQuery (generic BQ-test clients)",
)
def client_traffic_acquisition(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        return _traffic_acquisition_read(
            normalized, start, end, project_id=project_id, dataset_id=dataset_id,
            page_path_filter=_load_page_path_filter(normalized),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/device-split",
    summary="Client GA4 device category breakdown from BigQuery (generic BQ-test clients)",
)
def client_device_split(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.pages.device_split",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_device_split(start_date=start, end_date=end),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/landing",
    summary="Client GA4 landing page performance from BigQuery (generic BQ-test clients)",
)
def client_landing_pages(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            path_filter = _load_page_path_filter(normalized)
            return _cached_bq_read(
                f"{normalized}.pages.landing",
                {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_landing_pages(
                    start_date=start, end_date=end, page_path_filter=path_filter,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/landing-events",
    summary="Client GA4 landing page × event breakdown (generic BQ-test clients)",
)
def client_landing_page_events(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            path_filter = _load_page_path_filter(normalized)
            return _cached_bq_read(
                f"{normalized}.pages.landing_events",
                {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_landing_page_events(
                    start_date=start, end_date=end, page_path_filter=path_filter,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/top-key-events",
    summary="Client GA4 page path × event breakdown, all traffic (generic BQ-test clients)",
)
def client_top_pages_key_events(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            path_filter = _load_page_path_filter(normalized)
            return _cached_bq_read(
                f"{normalized}.pages.top_key_events",
                {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_page_key_events(
                    start_date=start, end_date=end, page_path_filter=path_filter,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/ga4/active-key-events",
    summary="Client's active GA4 key events (event names with key_events>0, last 90d)",
)
def client_active_key_events(
    client_key: str,
    request: Request,
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=90)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.ga4.active_key_events",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=3600,
                fetch=lambda: marketing_service.fetch_active_key_events(start_date=start, end_date=end),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/traffic-key-events",
    summary="Client GA4 traffic source × event breakdown (generic BQ-test clients)",
)
def client_traffic_key_events(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id):
            return _cached_bq_read(
                f"{normalized}.pages.traffic_key_events",
                {"start": start.isoformat(), "end": end.isoformat()}, ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_traffic_key_events(start_date=start, end_date=end),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/analytics/user-acq-key-events",
    summary="Client GA4 first-user source × event breakdown (generic BQ-test clients)",
)
def client_user_acq_key_events(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id):
            return _cached_bq_read(
                f"{normalized}.analytics.user_acq_key_events",
                {"start": start.isoformat(), "end": end.isoformat()}, ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_user_acq_key_events(start_date=start, end_date=end),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.post(
    "/api/clients/{client_key}/ga4/key-events",
    summary="Save the client's selected GA4 key events (which events count)",
)
async def save_ga4_key_events(
    client_key: str,
    request: Request,
) -> dict:
    normalized = (client_key or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        import client_dashboard_config as cdc
        cdc.update_ga4_key_events(
            normalized, event_names=str(body.get("event_names") or ""), updated_by="dashboard",
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True}


@router.post(
    "/api/clients/{client_key}/explorer/filters",
    summary="Save the client's Campaign Explorer filter chips (name-phrase rules)",
)
async def save_explorer_filters(
    client_key: str,
    request: Request,
) -> dict:
    """Persist the Campaign Explorer filter definition (one `Label = phrase`
    per line, optionally grouped under `[Group]` headers). Stored verbatim in
    client_dashboard_config; parsed into chips by the dashboard renderer."""
    normalized = (client_key or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        import client_dashboard_config as cdc
        cdc.update_explorer_filters(
            normalized, filters_text=str(body.get("filters") or ""), updated_by="dashboard",
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True}


@router.post(
    "/api/clients/{client_key}/analytics/page-path-filter",
    summary="Set the Website Analytics page-path scope for this client (admin only)",
)
async def save_analytics_page_path_filter(
    client_key: str,
    request: Request,
) -> dict:
    """Persist the Website Analytics page-path scope (one path pattern per line,
    case-insensitive substring). Body: ``{"filter": "/careers\\n/jobs"}`` (empty
    clears it → whole site). Admin-only — the scope is portal-wide config that
    limits what page data every viewer of this client sees, not a per-viewer
    preference."""
    normalized = (client_key or "").strip().lower()
    auth = web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    if not (auth.user and auth.user.role == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        import client_dashboard_config as cdc
        cdc.update_analytics_page_path_filter(
            normalized, filter_text=str(body.get("filter") or ""), updated_by="dashboard",
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True}


@router.post(
    "/api/clients/{client_key}/default-date-range",
    summary="Set the date-range preset the dashboard lands on for this client (admin only)",
)
async def save_default_date_range(
    client_key: str,
    request: Request,
) -> dict:
    """Persist the client's landing date-range preset. Body: ``{"preset":
    "last_month"}`` (one of the known presets; empty clears it). Admin-only — the
    landing range is portal-wide config, not a per-viewer preference."""
    normalized = (client_key or "").strip().lower()
    auth = web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    if not (auth.user and auth.user.role == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        import client_dashboard_config as cdc
        saved = cdc.save_default_date_preset(
            normalized, str(body.get("preset") or ""), updated_by="dashboard",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True, "preset": saved.default_date_preset or ""}


@router.post(
    "/api/clients/{client_key}/explorer/campaigns",
    summary="Set the Campaign Explorer campaign allowlist for this client (admin only)",
)
async def save_explorer_campaign_allowlist(
    client_key: str,
    request: Request,
) -> dict:
    """Persist which campaigns the portal's client may see in the Campaign
    Explorer. Body: ``{"campaigns": ["Name A", "Name B"]}`` (empty list clears
    the restriction). Admin-only — this scopes what a client's portal exposes."""
    normalized = (client_key or "").strip().lower()
    auth = web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    if not (auth.user and auth.user.role == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    campaigns = body.get("campaigns")
    if not isinstance(campaigns, (list, tuple)):
        campaigns = []
    try:
        import client_dashboard_config as cdc
        saved = cdc.save_explorer_campaign_allowlist(
            normalized, [str(c) for c in campaigns], updated_by="dashboard",
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True, "campaigns": list(saved.explorer_campaign_allowlist)}


@router.post(
    "/api/clients/{client_key}/email-performance/selection",
    summary="Set the Email Performance email selection for this client (admin only)",
)
async def save_email_performance_selection(
    client_key: str,
    request: Request,
) -> dict:
    """Persist which HubSpot marketing emails the Email Performance page shows for
    this client. Body: ``{"emails": ["123", "456"]}`` (empty list clears the
    selection, falling back to the page's default). Admin-only — the selection is
    portal-wide config, not a per-viewer preference."""
    normalized = (client_key or "").strip().lower()
    auth = web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    if not (auth.user and auth.user.role == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    emails = body.get("emails")
    if not isinstance(emails, (list, tuple)):
        emails = []
    try:
        import client_dashboard_config as cdc
        saved = cdc.save_email_performance_selection(
            normalized, [str(e) for e in emails], updated_by="dashboard",
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True, "emails": list(saved.email_performance_selection)}


# Tabs that support admin card-layout editing, mapped to the set of card keys
# that tab knows about. Keys outside the set are rejected so a stale/garbage key
# can't wedge the tab's render. Overview and Campaign Explorer are both editable.
_CARD_LAYOUT_TABS: dict[str, set[str]] = {}


def _card_layout_tab_keys(tab_key: str) -> set[str] | None:
    """Known card keys for a layout-editable tab, or None if the tab isn't one."""
    from dashboard.renderers.bigquery_dashboard_renderer import (
        EXPLORER_LAYOUT_CARDS,
        OVERVIEW_PINNABLE_CARDS,
    )

    if not _CARD_LAYOUT_TABS:
        _CARD_LAYOUT_TABS["overview"] = set(OVERVIEW_PINNABLE_CARDS)
        _CARD_LAYOUT_TABS["explorer"] = set(EXPLORER_LAYOUT_CARDS)
    return _CARD_LAYOUT_TABS.get(tab_key)


@router.post(
    "/api/clients/{client_key}/tabs/{tab_key}/card-layout",
    summary="Save a tab's card order + hidden set (admin only)",
)
async def save_tab_card_layout(
    client_key: str,
    tab_key: str,
    request: Request,
) -> dict:
    """Persist the admin-authored card layout for one tab of the BigQuery-mart
    dashboard. Body: ``{"order": [<card_key>, ...], "hidden": [<card_key>, ...]}``.
    Both lists are filtered to the tab's known cards; unknown keys are dropped
    rather than rejected so a card that's simply not present this render (its
    connector is off) can't fail the save. Admin-only — clients cannot reorder
    or hide cards on another client's dashboard."""
    normalized = (client_key or "").strip().lower()
    tab = (tab_key or "").strip().lower()
    auth = web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    if not (auth.user and auth.user.role == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    known = _card_layout_tab_keys(tab)
    if known is None:
        raise HTTPException(status_code=400, detail="Unknown tab.")
    try:
        body = await request.json()
    except Exception:
        body = {}

    def _keys(values: object) -> list[str]:
        if not isinstance(values, (list, tuple)):
            return []
        out: list[str] = []
        for v in values:
            key = str(v).strip()
            if key in known and key not in out:
                out.append(key)
        return out

    order = _keys(body.get("order"))
    hidden = _keys(body.get("hidden"))
    try:
        import client_dashboard_config as cdc
        cdc.save_card_layout(
            normalized, tab, order=order, hidden=hidden, updated_by="dashboard",
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True, "order": order, "hidden": hidden}


@router.get(
    "/api/clients/{client_key}/analytics/conversions",
    summary="Client GA4 conversion events breakdown (generic BQ-test clients)",
)
def client_conversion_events(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.analytics.conversions",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_conversion_events(start_date=start, end_date=end),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/analytics/user-acquisition",
    summary="Client GA4 first-touch user acquisition (generic BQ-test clients)",
)
def client_user_acquisition(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            return _cached_bq_read(
                f"{normalized}.analytics.user_acquisition",
                {"start": start.isoformat(), "end": end.isoformat()},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_user_acquisition(start_date=start, end_date=end),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/analytics/demographics",
    summary="Client GA4 demographic & geographic breakdown (generic BQ-test clients)",
)
def client_demographics(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    try:
        with marketing_service.route(
            client_key=normalized, project_id=project_id, mart_dataset_id=dataset_id,
        ):
            path_filter = _load_page_path_filter(normalized)
            return _cached_bq_read(
                f"{normalized}.analytics.demographics",
                {"start": start.isoformat(), "end": end.isoformat(), "scope": "|".join(path_filter)},
                ttl_seconds=900,
                fetch=lambda: marketing_service.fetch_demographics(
                    start_date=start, end_date=end, page_path_filter=path_filter,
                ),
            )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/gsc/summary",
    summary="Client Search Console summary from BigQuery (generic BQ-test clients)",
)
def client_gsc_summary(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    compare_start_date: date | None = Query(
        default=None,
        description="Comparison window start (dashboard Compare picker). Defaults to the preceding same-length period.",
    ),
    compare_end_date: date | None = Query(default=None, description="Comparison window end."),
) -> dict:
    normalized = (client_key or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    start, end = _resolve_marketing_dates(start_date, end_date)
    cmp_start, cmp_end = _resolve_compare_dates(compare_start_date, compare_end_date)
    try:
        import bq_gsc_service
        return _cached_bq_read(
            f"{normalized}.gsc.summary",
            {
                "start": start.isoformat(), "end": end.isoformat(),
                "compare_start": cmp_start.isoformat() if cmp_start else "",
                "compare_end": cmp_end.isoformat() if cmp_end else "",
            },
            ttl_seconds=900,
            fetch=lambda: bq_gsc_service.build_gsc_mart_summary(
                start=start, end=end, client_slug=normalized,
                compare_start=cmp_start, compare_end=cmp_end,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/gsc/keyword-matches",
    summary="Queries matching branded/target keyword terms, scanned across the full date range",
)
def client_gsc_keyword_matches(
    client_key: str,
    request: Request,
    terms: str = Query(default="", description="Comma-separated include terms."),
    exclude: str = Query(default="", description="Comma-separated exclude terms."),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    compare_start_date: date | None = Query(
        default=None,
        description="Comparison window start for each row's prior avg position. Defaults to the preceding same-length period.",
    ),
    compare_end_date: date | None = Query(default=None, description="Comparison window end."),
) -> dict:
    """Unlike gsc/summary's top_queries (LIMIT 25 by clicks), this scans every
    query in range for the given terms -- so a branded/target keyword that
    isn't a top-25 performer by raw click volume still shows up. A query counts
    when it contains any include term (`terms`) and none of the `exclude` terms.
    Also returns a weekly clicks/impressions rollup ("weekly") for trending the
    same terms over time, computed in the same request rather than a separate
    round trip."""
    normalized = (client_key or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    term_list = [t.strip() for t in terms.split(",") if t.strip()]
    exclude_list = [t.strip() for t in exclude.split(",") if t.strip()]
    start, end = _resolve_marketing_dates(start_date, end_date)
    cmp_start, cmp_end = _resolve_compare_dates(compare_start_date, compare_end_date)
    if not term_list:
        return {"rows": [], "weekly": []}
    # Trend spans a trailing ~13-week window (see nixon_gsc_keyword_matches);
    # rows table stays scoped to the selected range.
    trend_start = min(start, end - timedelta(days=_KEYWORD_TREND_DAYS))
    try:
        import bq_gsc_service
        cache_key = {
            "start": start.isoformat(), "end": end.isoformat(),
            "terms": sorted(term_list), "exclude": sorted(exclude_list),
            "compare_start": cmp_start.isoformat() if cmp_start else "",
            "compare_end": cmp_end.isoformat() if cmp_end else "",
        }
        # The weekly trend has no comparison window, so it keeps the old key —
        # switching Compare must not cold-miss a cache it can't change.
        trend_cache_key = {"start": trend_start.isoformat(), "end": end.isoformat(), "terms": sorted(term_list), "exclude": sorted(exclude_list)}
        rows = _cached_bq_read(
            f"{normalized}.gsc.keyword_matches", cache_key, ttl_seconds=900,
            fetch=lambda: bq_gsc_service.gsc_keyword_matches(
                start=start, end=end, terms=term_list, exclude_terms=exclude_list,
                client_slug=normalized,
                compare_start=cmp_start, compare_end=cmp_end,
            ),
        )
        weekly = _cached_bq_read(
            f"{normalized}.gsc.keyword_weekly_trend", trend_cache_key, ttl_seconds=900,
            fetch=lambda: bq_gsc_service.gsc_keyword_weekly_trend(
                start=trend_start, end=end, terms=term_list, exclude_terms=exclude_list,
                client_slug=normalized,
            ),
        )
        return {"rows": rows, "weekly": weekly}
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.post(
    "/api/clients/{client_key}/gsc/keyword-config",
    summary="Save Search Console branded roots + target keywords for a client",
)
async def save_gsc_keyword_config(
    client_key: str,
    request: Request,
) -> dict:
    """Persist the branded roots (brand-name stems) + specific target keywords
    used by the Search Console 'Branded & Target Keywords' section, plus optional
    per-group exclude roots. Stored one per line in client_dashboard_config."""
    normalized = (client_key or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        import client_dashboard_config as cdc
        cdc.update_gsc_keywords(
            normalized,
            branded_roots=str(body.get("branded_roots") or ""),
            target_keywords=str(body.get("target_keywords") or ""),
            branded_exclude=str(body.get("branded_exclude") or ""),
            target_exclude=str(body.get("target_exclude") or ""),
            updated_by="dashboard",
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True}


@router.post(
    "/api/clients/{client_key}/pagespeed/targets",
    summary="Save per-KPI PageSpeed target bands for a client",
)
async def save_pagespeed_targets_endpoint(
    client_key: str,
    request: Request,
) -> dict:
    """Persist the Site Performance tab's per-KPI target bands. Body is
    {targets: {performance: {min, max}, ...}}; values are clamped to 0–100 and
    only the four Lighthouse category keys are kept."""
    normalized = (client_key or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = (body or {}).get("targets") or {}

    def _band(v: object) -> dict | None:
        if not isinstance(v, dict):
            return None
        try:
            lo = max(0, min(100, int(round(float(v.get("min"))))))
            hi = max(0, min(100, int(round(float(v.get("max"))))))
        except (TypeError, ValueError):
            return None
        if hi < lo:
            lo, hi = hi, lo
        return {"min": lo, "max": hi}

    cleaned: dict[str, dict] = {}
    for kpi in ("performance", "accessibility", "best_practices", "seo"):
        band = _band(raw.get(kpi))
        if band:
            cleaned[kpi] = band
    try:
        import client_dashboard_config as cdc
        cdc.save_pagespeed_targets(normalized, cleaned, updated_by="dashboard")
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
    return {"ok": True, "targets": cleaned}


@router.get(
    "/api/clients/{client_key}/semrush/summary",
    summary="Client SEMrush domain snapshot from BigQuery (generic BQ-test clients)",
)
def client_semrush_summary(
    client_key: str,
    request: Request,
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        import bq_semrush_service
        return _cached_bq_read(
            f"{normalized}.semrush.summary", {}, ttl_seconds=6 * 3600,
            fetch=lambda: bq_semrush_service.fetch_latest_snapshot(
                client_key=normalized, project=project_id, mart_dataset=dataset_id,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pagespeed/summary",
    summary="Client PageSpeed Insights scores + Core Web Vitals from BigQuery",
)
def client_pagespeed_summary(
    client_key: str,
    request: Request,
    strategy: str = "desktop",
) -> dict:
    normalized = (client_key or "").strip().lower()
    strat = (strategy or "desktop").strip().lower()
    if strat not in ("desktop", "mobile"):
        strat = "desktop"
    project_id, dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        import bq_pagespeed_service
        return _cached_bq_read(
            f"{normalized}.pagespeed.summary.{strat}", {}, ttl_seconds=6 * 3600,
            fetch=lambda: bq_pagespeed_service.fetch_latest_snapshot(
                client_key=normalized, project=project_id, mart_dataset=dataset_id,
                strategy=strat,
            ),
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


@router.post(
    "/api/clients/nixon/backfill-linkedin",
    summary="Nixon: 180-day LinkedIn backfill into BigQuery (session-authed)",
)
def nixon_backfill_linkedin(
    request: Request,
) -> dict:
    """Run the 180-day onboarding ingestion for Nixon (writes raw_linkedin_ads +
    rebuilds the marts). Authed by the signed-in dashboard session or an API key
    — the same gate as the read endpoints — so no cron secret is needed."""
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    try:
        import dashboard_service

        result = dashboard_service.refresh_bq_client(
            "nixon", date_range="LAST_180_DAYS", sync_trigger="onboarding"
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc

    run = (result or {}).get("refresh_run") or {}
    linkedin = ((run.get("sources") or {}).get("linkedin")) or {}
    return {
        "ok": True,
        "date_range": run.get("date_range"),
        "linkedin": {
            "status": linkedin.get("status"),
            "rows_fetched": linkedin.get("rows_fetched"),
            "rows_merged": linkedin.get("rows_merged"),
            "data_through": linkedin.get("data_through"),
        },
    }


@router.post(
    "/api/clients/nixon/refresh",
    summary="Nixon: refresh BigQuery (last 30 days), session-authed",
)
def nixon_refresh(
    request: Request,
) -> dict:
    """Pull the last 30 days into BigQuery for Nixon (the rolling refresh). Same
    auth as the read endpoints — no cron secret needed."""
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    try:
        import dashboard_service

        result = dashboard_service.refresh_bq_client(
            "nixon", date_range="LAST_30_DAYS", sync_trigger="manual_full"
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc

    run = (result or {}).get("refresh_run") or {}
    linkedin = ((run.get("sources") or {}).get("linkedin")) or {}
    return {
        "ok": True,
        "date_range": run.get("date_range"),
        "linkedin": {
            "status": linkedin.get("status"),
            "rows_fetched": linkedin.get("rows_fetched"),
            "data_through": linkedin.get("data_through"),
        },
    }


@router.post(
    "/api/clients/{client_key}/refresh",
    summary="Client: refresh BigQuery (last 30 days), session-authed (generic BQ clients)",
)
def client_refresh(
    client_key: str,
    request: Request,
) -> dict:
    """Generic sibling of nixon_refresh -- pulls the last 30 days into BigQuery
    for any bigquery_nixon-mode client by running its connector syncs. Same
    session/API-key auth as the read endpoints."""
    normalized = (client_key or "").strip().lower()
    # Ensure the client actually has a BQ dashboard configured before running
    # a (potentially slow) refresh; raises 404/503 the same way the reads do.
    _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        import dashboard_service

        result = dashboard_service.refresh_bq_client(
            normalized, date_range="LAST_30_DAYS", sync_trigger="manual_full"
        )
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc

    run = (result or {}).get("refresh_run") or {}
    return {"ok": True, "date_range": run.get("date_range")}


@router.post(
    "/api/clients/{client_key}/bq-verify",
    summary="Client: verify BigQuery project access + provision datasets (generic BQ clients)",
)
def client_bq_verify(
    client_key: str,
    request: Request,
) -> dict:
    """Run the same provision + access check the connector wizard does, on
    demand — so an admin can confirm the GCP project exists and the shared
    service account has both required roles (Data Editor + Job User) at any
    time, not just during connector setup, and diagnose 'data stopped showing'
    (e.g. access revoked). Idempotent (create_dataset exists_ok=True)."""
    normalized = (client_key or "").strip().lower()
    project_id, _dataset_id = _load_bq_test_config(normalized)
    web_auth.authenticate_dashboard_api(request, client_slug=normalized)
    try:
        import client_bigquery_setup
        result = client_bigquery_setup.ensure_client_datasets(project_id=project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "datasets": result.get("datasets") or [],
            "message": f"Access verified — service account can read/write in '{project_id}'.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "project_id": project_id,
            "error": (
                f"{str(exc)[:200]} — make sure the GCP project '{project_id}' exists and "
                "marketing-data-reader@sagefrog.iam.gserviceaccount.com has BOTH the "
                "'BigQuery Data Editor' and 'BigQuery Job User' roles on it."
            ),
        }


@router.get("/api/debug/bq", summary="Debug BigQuery client identity")
def debug_bigquery_identity(
    request: Request,
) -> dict:
    web_auth.authenticate_dashboard_api_any(request, client_slugs=_NIXON_ACCESS_SLUGS)
    try:
        client = bigquery_service.build_client(project_id="nixon-medical")
        credentials = getattr(client, "_credentials", None) or getattr(client, "credentials", None)
        return {
            "project": client.project,
            "service_account": getattr(credentials, "service_account_email", None),
        }
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


# ── GTM live-tags audit ───────────────────────────────────────────────────────

def _record_gtm_health(slug: str, *, ok: bool, err: Exception | None, prev_status: str | None) -> None:
    """Write the GTM connector's status back on a live read.

    GTM is the one connector not in the daily BigQuery sync (it's read live on
    the Event Tracking page), so nothing else updates its status — without this
    the card shows a stale "connected" even after its OAuth token dies. Only
    writes on a status *change* to avoid a DB write on every page load.
    """
    try:
        import connector_config_store
        if ok:
            if prev_status == "error":
                connector_config_store.update_status(slug, "gtm", status="connected")
        elif prev_status != "error":
            connector_config_store.update_status(
                slug, "gtm", status="error", error_message=str(err or "")[:300],
            )
    except Exception:
        logger.warning("Failed to record GTM health [%s]", slug, exc_info=True)


@router.get(
    "/api/clients/{client_slug}/gtm/live-tags",
    summary="GTM live container version — normalised tag + trigger audit",
)
def gtm_live_tags(
    client_slug: str,
    request: Request,
    refresh: bool = Query(default=False, description="Bypass 15-minute cache."),
    account_id: str | None = Query(default=None, description="GTM account ID override."),
    container_id: str | None = Query(default=None, description="GTM container ID override."),
) -> dict:
    """
    Returns the live GTM container version normalised into per-tag rows.

    Each row includes tag_name, raw_type, friendly_type, paused, consent_status,
    firing_trigger_ids/names, trigger_types, trigger_criteria, trigger_settings,
    and trigger_logic.  The response is cached per client/container for 15 minutes;
    pass refresh=true to force a live fetch.
    """
    slug = (client_slug or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=slug)

    import connector_config_store
    import gtm_quota
    import gtm_service
    import oauth_store

    # Resolve GTM account + container from connector config; query params act as override
    conn_cfg = connector_config_store.get_config(slug, "gtm")
    stored_parts = (
        ((conn_cfg.source_account_id or "") if conn_cfg else "").split(":")
    )
    stored_account = stored_parts[0] if len(stored_parts) == 2 and stored_parts[0] else None
    stored_container = stored_parts[1] if len(stored_parts) == 2 and stored_parts[1] else None

    resolved_account = (account_id or "").strip() or stored_account
    resolved_container = (container_id or "").strip() or stored_container
    if not resolved_account or not resolved_container:
        raise HTTPException(
            status_code=422,
            detail=(
                "GTM container not configured. "
                "Connect GTM in the connector wizard (Settings → Connectors)."
            ),
        )

    prev_status = conn_cfg.status if conn_cfg else None
    refresh_token = oauth_store.get_refresh_token(
        "google_tag_manager", client_slug=slug
    ) or oauth_store.get_refresh_token("google_tag_manager")
    if not refresh_token:
        _record_gtm_health(slug, ok=False, err=Exception("No Google Tag Manager OAuth token."), prev_status=prev_status)
        raise HTTPException(
            status_code=403,
            detail=(
                "No Google Tag Manager OAuth token found for this client. "
                "Connect Google Tag Manager in the connector wizard."
            ),
        )

    try:
        result = gtm_service.get_live_tags(
            slug,
            resolved_account,
            resolved_container,
            refresh_token,
            force_refresh=refresh,
        )
    except gtm_quota.GTMRateLimited as exc:
        # Google's project-wide GTM quota, not a broken connection. Leave the
        # connector's health alone (marking it "error" would strand the card in
        # a failed state until someone re-tests) and answer 429 with a
        # Retry-After so the browser knows when to come back.
        logger.info("GTM live-tags rate-limited [%s]: %s", slug, exc)
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(max(1, int(exc.retry_after or 60)))},
        ) from exc
    except PermissionError as exc:
        _record_gtm_health(slug, ok=False, err=exc, prev_status=prev_status)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        _record_gtm_health(slug, ok=False, err=exc, prev_status=prev_status)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("GTM live-tags error [%s]: %s", slug, exc)
        _record_gtm_health(slug, ok=False, err=exc, prev_status=prev_status)
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc

    # Live read succeeded — clear a stale "error" so the card recovers.
    _record_gtm_health(slug, ok=True, err=None, prev_status=prev_status)
    return result


# ── GA4 raw table health (task 1 — confirm backfill coverage) ─────────────────

@router.get(
    "/api/clients/{client_slug}/ga4/health/raw",
    summary="GA4 raw table health: row counts, date range, yesterday coverage",
)
def ga4_raw_health(
    client_slug: str,
    request: Request,
) -> dict:
    slug = (client_slug or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    import bq_ga4_service
    import connector_config_store

    cfg = connector_config_store.get_config(slug, "ga4")
    if not cfg:
        raise HTTPException(status_code=404, detail="No GA4 connector configured for this client.")

    property_id = cfg.source_account_id or ""
    bq_project_id = cfg.bq_project_id
    raw_dataset_id = cfg.raw_dataset_id or "raw_ga4"

    try:
        with bq_ga4_service.route(
            bq_project_id=bq_project_id,
            ga4_dataset_id=raw_dataset_id,
        ):
            health = bq_ga4_service.check_ga4_health(
                client_key=slug, property_id=property_id
            )
        return {
            "client_slug": slug,
            "property_id": property_id,
            "bq_project": bq_project_id,
            "raw_dataset": raw_dataset_id,
            "tables": health,
        }
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


# ── GA4 mart view health (task 8) ─────────────────────────────────────────────

@router.get(
    "/api/clients/{client_slug}/ga4/health/mart",
    summary="GA4 mart view health: row counts, date range, latest synced_at",
)
def ga4_mart_health(
    client_slug: str,
    request: Request,
) -> dict:
    slug = (client_slug or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    import bq_ga4_mart_service
    import connector_config_store

    cfg = connector_config_store.get_config(slug, "ga4")
    if not cfg:
        raise HTTPException(status_code=404, detail="No GA4 connector configured for this client.")

    bq_project_id = cfg.bq_project_id or "nixon-medical"
    property_id = cfg.source_account_id or None

    try:
        health = bq_ga4_mart_service.check_ga4_mart_health(
            project=bq_project_id,
            client_key=slug,
            property_id=property_id,
        )
        return health
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc


# ── GA4 mart view provisioning (tasks 2–4, 6) ────────────────────────────────

@router.post(
    "/api/clients/{client_slug}/ga4/provision-views",
    summary="CREATE OR REPLACE GA4 mart views in marketing_marts",
)
def ga4_provision_views(
    client_slug: str,
    request: Request,
) -> dict:
    """Idempotent — safe to re-run.  Rebuilds all GA4 mart views from raw_ga4.

    Query params:
      views — comma-separated list to rebuild only specific views (default: all)
    """
    slug = (client_slug or "").strip().lower()
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    import bq_ga4_mart_service
    import connector_config_store

    cfg = connector_config_store.get_config(slug, "ga4")
    if not cfg:
        raise HTTPException(status_code=404, detail="No GA4 connector configured for this client.")

    bq_project_id = cfg.bq_project_id or "nixon-medical"
    raw_dataset_id = cfg.raw_dataset_id or "raw_ga4"

    # Optional ?views= filter (comma-separated view names)
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(str(request.url.query))
    requested_views_raw = qs.get("views", [""])[0]
    requested_views = [v.strip() for v in requested_views_raw.split(",") if v.strip()] or None

    try:
        result = bq_ga4_mart_service.provision_ga4_mart_views(
            project=bq_project_id,
            raw_project=bq_project_id,
            raw_dataset=raw_dataset_id,
            client_key=slug,
            views=requested_views,
        )
        return result
    except Exception as exc:
        raise _bq_endpoint_failure(exc) from exc
