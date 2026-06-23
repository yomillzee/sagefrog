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
