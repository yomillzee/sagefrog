"""JSON API routes for client-facing dashboard data."""

from __future__ import annotations

import hmac
import logging
import traceback
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Security
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

import bigquery_service
import nixon_marketing_service
import web_auth
import web_users
from dashboard.renderers.nixon_bq_settings_renderer import render_nixon_bq_settings_page
from dashboard.renderers.arg_bq_test_renderer import render_arg_bigquery_test_page
from dashboard.renderers.nixon_bq_test_renderer import render_nixon_bigquery_test_page
from dashboard.routes.helpers import penn_html_session_kwargs
from security import configured_api_key, is_production

router = APIRouter()
logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _resolve_nixon_marketing_dates(
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


def _api_key_is_valid(
    bearer_credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> bool:
    expected = configured_api_key()
    if not expected:
        return not is_production()
    token: str | None = None
    if bearer_credentials and bearer_credentials.credentials:
        token = bearer_credentials.credentials.strip()
    elif x_api_key:
        token = x_api_key.strip()
    return bool(token and hmac.compare_digest(token, expected))


def _authorize_nixon_api(
    request: Request,
    *,
    key: str | None,
    bearer_credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> None:
    if _api_key_is_valid(bearer_credentials, x_api_key):
        return
    if web_users.enabled():
        web_auth.authenticate_dashboard_api(request, client_slug="nixon", key=key)
        return
    if web_auth.legacy_dashboard_key_ok(key):
        return
    raise HTTPException(status_code=401, detail="Sign in or provide a valid API key.")


def _nixon_endpoint_failure(exc: Exception) -> HTTPException:
    logger.exception("Nixon endpoint failed")
    logger.error("Nixon endpoint traceback:\n%s", traceback.format_exc())
    return HTTPException(
        status_code=500,
        detail={
            "error": str(exc),
            "type": type(exc).__name__,
        },
    )


@router.get(
    "/dashboard/nixon-bq-test",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def nixon_bigquery_test_dashboard(request: Request, key: str | None = None):
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="nixon", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        return HTMLResponse(render_nixon_bigquery_test_page(**penn_html_session_kwargs(auth)))

    if not web_auth.legacy_dashboard_key_ok(key):
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard key.")
    return HTMLResponse(
        render_nixon_bigquery_test_page(
            access_key=key,
            use_session=False,
            session_email=None,
            session_is_admin=False,
        )
    )


@router.get(
    "/dashboard/arg-bq-test",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def arg_bigquery_test_dashboard(request: Request, key: str | None = None):
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="arg-bq-test", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        return HTMLResponse(render_arg_bigquery_test_page(**penn_html_session_kwargs(auth)))

    if not web_auth.legacy_dashboard_key_ok(key):
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard key.")
    return HTMLResponse(
        render_arg_bigquery_test_page(
            access_key=key,
            use_session=False,
            session_email=None,
            session_is_admin=False,
        )
    )


def _nixon_settings_context() -> tuple[dict, dict, str | None]:
    """Routing + account-id config + service-account email for the settings page."""
    import client_config
    import ga4_clients
    import railway_api

    routing: dict = {"marts_dataset": "marketing_marts", "railway_ready": railway_api.enabled()}
    sa_email: str | None = None
    try:
        target = ga4_clients.resolve_target(client_key="nixon")
        routing["project"] = target.bq_project_id
        routing["ga4_dataset"] = target.bq_dataset_id
        routing["creds_env"] = target.credentials_env or "GCP_SERVICE_ACCOUNT_JSON"
    except Exception:
        pass
    try:
        resolved = ga4_clients.resolve_client_config(client_key="nixon")
        sa_email = str((resolved.credentials or {}).get("client_email") or "") or None
    except Exception:
        pass
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
    return routing, account_ids, sa_email


@router.get(
    "/dashboard/nixon-bq-test/settings",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def nixon_bq_settings_page(
    request: Request,
    key: str | None = None,
    saved: str | None = None,
    cred_saved: str | None = None,
    cred_error: str | None = None,
    bq_error: str | None = None,
):
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="nixon", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        html_kw = penn_html_session_kwargs(auth)
    else:
        if not web_auth.legacy_dashboard_key_ok(key):
            raise HTTPException(status_code=401, detail="Invalid or missing dashboard key.")
        html_kw = {"access_key": key, "use_session": False, "session_email": None, "session_is_admin": False}

    routing, account_ids, sa_email = _nixon_settings_context()
    flash = None
    flash_error = None
    if saved:
        flash = "Account IDs saved."
    elif cred_saved:
        flash = "Service-account credential set on Railway — the service is redeploying."
    if cred_error:
        flash_error = str(cred_error)[:300]
    elif bq_error:
        flash_error = str(bq_error)[:300]

    return HTMLResponse(
        render_nixon_bq_settings_page(
            routing=routing,
            account_ids=account_ids,
            sa_email=sa_email,
            flash=flash,
            flash_error=flash_error,
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
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_marketing(
            start_date=start,
            end_date=end,
            top_limit=top_limit,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


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
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    normalized = (client_key or "").strip().lower()
    if normalized != "nixon":
        raise HTTPException(status_code=404, detail=f"Summary is not available for client '{client_key}'.")
    _authorize_nixon_api(
        request,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_summary(
            start_date=start,
            end_date=end,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


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
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    try:
        return nixon_marketing_service.fetch_nixon_marketing_health(limit=limit)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


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
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    normalized = (client_key or "").strip().lower()
    if normalized != "nixon":
        raise HTTPException(status_code=404, detail=f"Health is not available for client '{client_key}'.")
    _authorize_nixon_api(
        request,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    try:
        return nixon_marketing_service.fetch_nixon_marketing_health(limit=limit)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


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
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_google_ads_explorer(
            start_date=start,
            end_date=end,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


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
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_linkedin_explorer(
            start_date=start,
            end_date=end,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/top",
    summary="Nixon top pages (all traffic) from BigQuery",
)
def nixon_pages_top(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request, key=key, bearer_credentials=bearer_credentials, x_api_key=x_api_key
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_pages_top(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/sources",
    summary="Nixon page source / AI-referral breakdown from BigQuery",
)
def nixon_pages_sources(
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request, key=key, bearer_credentials=bearer_credentials, x_api_key=x_api_key
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_pages_sources(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.post(
    "/api/clients/nixon/backfill-linkedin",
    summary="Nixon: 180-day LinkedIn backfill into BigQuery (session-authed)",
)
def nixon_backfill_linkedin(
    request: Request,
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    """Run the 180-day onboarding ingestion for Nixon (writes raw_linkedin_ads +
    rebuilds the marts). Authed by the signed-in dashboard session or an API key
    — the same gate as the read endpoints — so no cron secret is needed."""
    _authorize_nixon_api(
        request, key=key, bearer_credentials=bearer_credentials, x_api_key=x_api_key
    )
    try:
        import dashboard_service

        result = dashboard_service.refresh_bq_client(
            "nixon", date_range="LAST_180_DAYS", sync_trigger="onboarding"
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc

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
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    """Pull the last 30 days into BigQuery for Nixon (the rolling refresh). Same
    auth as the read endpoints — no cron secret needed."""
    _authorize_nixon_api(
        request, key=key, bearer_credentials=bearer_credentials, x_api_key=x_api_key
    )
    try:
        import dashboard_service

        result = dashboard_service.refresh_bq_client(
            "nixon", date_range="LAST_30_DAYS", sync_trigger="manual_full"
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc

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


@router.get("/api/debug/bq", summary="Debug BigQuery client identity")
def debug_bigquery_identity(
    request: Request,
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    try:
        client = bigquery_service.build_client(project_id="nixon-medical")
        credentials = getattr(client, "_credentials", None) or getattr(client, "credentials", None)
        return {
            "project": client.project,
            "service_account": getattr(credentials, "service_account_email", None),
        }
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc
