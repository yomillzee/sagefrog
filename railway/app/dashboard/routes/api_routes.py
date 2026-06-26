"""JSON API routes for client-facing dashboard data."""

from __future__ import annotations

import hmac
import logging
import traceback
from datetime import date, timedelta

from fastapi import APIRouter, Form, HTTPException, Query, Request, Security
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

import bigquery_service
import client_bq_service
import nixon_marketing_service
import web_auth
import web_users
from dashboard.renderers.arg_bq_settings_renderer import render_arg_bq_settings_page
from dashboard.renderers.nixon_bq_settings_renderer import render_nixon_bq_settings_page
from dashboard.renderers.arg_bq_test_renderer import render_arg_bigquery_test_page
from dashboard.renderers.nixon_analytics_renderer import render_nixon_analytics_page
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


def _authorize_bq_client_api(
    request: Request,
    *,
    client_slug: str,
    key: str | None,
    bearer_credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> None:
    if _api_key_is_valid(bearer_credentials, x_api_key):
        return
    if web_users.enabled():
        web_auth.authenticate_dashboard_api(request, client_slug=client_slug, key=key)
        return
    if web_auth.legacy_dashboard_key_ok(key):
        return
    raise HTTPException(status_code=401, detail="Sign in or provide a valid API key.")


# URL slug → DB config key mapping for generic BQ-test clients.
# The URL uses a short name (e.g. "arg") while the DB stores config under the
# full slug (e.g. "arg-bq-test") to avoid collisions with other client types.
_BQ_TEST_CLIENT_CONFIG_KEYS: dict[str, str] = {
    "arg": "arg-bq-test",
    "penn-bq-test": "penn-bq-test",
}
_BQ_TEST_CLIENTS: frozenset[str] = frozenset(_BQ_TEST_CLIENT_CONFIG_KEYS)


def _load_bq_test_config(slug: str) -> tuple[str, str]:
    """Return (gcp_project_id, bq_mart_dataset_id) for a BQ-test client.

    Raises 404 if slug is not a recognised BQ-test client, or 503 if the
    client has no GCP project configured yet.
    """
    if slug not in _BQ_TEST_CLIENTS:
        raise HTTPException(status_code=404, detail=f"No BQ data available for client '{slug}'.")
    config_key = _BQ_TEST_CLIENT_CONFIG_KEYS[slug]
    try:
        import client_dashboard_config as cdc
        row = cdc.get_config(config_key)
    except Exception:
        row = None
    project_id = (row.gcp_project_id if row else None) or ""
    dataset_id = (row.bq_mart_dataset_id if row else None) or "marketing_marts"
    if not project_id:
        raise HTTPException(
            status_code=503,
            detail=f"BigQuery project not configured for '{slug}'. Set it in Settings.",
        )
    return project_id, dataset_id


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
    "/dashboard/nixon-bq-test/analytics",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def nixon_analytics_dashboard(request: Request, key: str | None = None):
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="nixon", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        return HTMLResponse(render_nixon_analytics_page(**penn_html_session_kwargs(auth)))

    if not web_auth.legacy_dashboard_key_ok(key):
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard key.")
    return HTMLResponse(
        render_nixon_analytics_page(
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


def _arg_settings_context() -> tuple[str, str, dict]:
    """Load saved BQ project + dataset + account IDs for the ARG client."""
    import client_config
    import client_dashboard_config

    bq_project_id = ""
    bq_dataset_id = ""
    account_ids: dict = {}
    try:
        row = client_dashboard_config.get_config("arg-bq-test")
        if row:
            bq_project_id = row.gcp_project_id or ""
            bq_dataset_id = row.bq_mart_dataset_id or ""
    except Exception:
        pass
    try:
        cfg = client_config.load_client_config("arg-bq-test")
        account_ids = {
            "google_customer_id": cfg.google_customer_id or "",
            "linkedin_account_id": cfg.linkedin_account_id or "",
            "meta_account_id": cfg.meta_account_id or "",
            "ga4_client_key": cfg.ga4_client_key or "",
        }
    except Exception:
        pass
    return bq_project_id, bq_dataset_id, account_ids


@router.get(
    "/dashboard/arg-bq-test/settings",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def arg_bq_settings_page(
    request: Request,
    key: str | None = None,
    saved: str | None = None,
    bq_saved: str | None = None,
    error: str | None = None,
):
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="arg-bq-test", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        html_kw = penn_html_session_kwargs(auth)
    else:
        if not web_auth.legacy_dashboard_key_ok(key):
            raise HTTPException(status_code=401, detail="Invalid or missing dashboard key.")
        html_kw = {"access_key": key, "use_session": False, "session_email": None, "session_is_admin": False}

    bq_project_id, bq_dataset_id, account_ids = _arg_settings_context()
    flash = "BQ connection saved." if bq_saved else ("Account IDs saved." if saved else None)
    flash_error = str(error)[:300] if error else None
    return HTMLResponse(
        render_arg_bq_settings_page(
            bq_project_id=bq_project_id,
            bq_dataset_id=bq_dataset_id,
            account_ids=account_ids,
            flash=flash,
            flash_error=flash_error,
            **html_kw,
        )
    )


@router.post(
    "/dashboard/arg-bq-test/settings",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def arg_bq_settings_post(
    request: Request,
    action: str = Form("save_bq"),
    key: str | None = None,
    gcp_project_id: str = Form(""),
    bq_mart_dataset_id: str = Form(""),
    linkedin_account_id: str = Form(""),
    google_customer_id: str = Form(""),
    meta_account_id: str = Form(""),
    ga4_client_key: str = Form(""),
):
    import client_dashboard_config

    use_session = False
    access_key = key
    session_email = None

    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="arg-bq-test", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        session_email = auth.user.email if auth.user else None

    from urllib.parse import quote as _quote
    base = "/dashboard/arg-bq-test/settings"

    def _redirect(params: str) -> RedirectResponse:
        url = f"{base}?{params}"
        if not use_session and access_key:
            url = f"{base}?key={_quote(access_key, safe='')}&{params}"
        return RedirectResponse(url=url, status_code=303)

    act = (action or "save_bq").strip().lower()

    if act == "save_bq":
        if not client_dashboard_config.enabled():
            return _redirect("error=" + _quote("DATABASE_URL is required to save settings."))
        try:
            client_dashboard_config.save_config(
                "arg-bq-test",
                label="ARG — BQ Test",
                google_customer_id=None,
                linkedin_account_id=None,
                meta_account_id=None,
                ga4_client_key=None,
                updated_by=session_email or "dashboard_key",
                gcp_project_id=gcp_project_id.strip() or None,
                bq_mart_dataset_id=bq_mart_dataset_id.strip() or None,
                dashboard_mode="bigquery",
            )
        except Exception as exc:
            return _redirect("error=" + _quote(str(exc)[:200]))
        return _redirect("bq_saved=1")

    if act == "save_accounts":
        if not client_dashboard_config.enabled():
            return _redirect("error=" + _quote("DATABASE_URL is required to save settings."))
        try:
            client_dashboard_config.save_config(
                "arg-bq-test",
                label="ARG — BQ Test",
                google_customer_id=google_customer_id.replace("-", "").strip() or None,
                linkedin_account_id=linkedin_account_id.strip() or None,
                meta_account_id=meta_account_id.strip() or None,
                ga4_client_key=ga4_client_key.strip() or None,
                updated_by=session_email or "dashboard_key",
                dashboard_mode="bigquery",
            )
        except Exception as exc:
            return _redirect("error=" + _quote(str(exc)[:200]))
        return _redirect("saved=1")

    if act == "provision_bq":
        try:
            row = client_dashboard_config.get_config("arg-bq-test") if client_dashboard_config.enabled() else None
            project_id = (row.gcp_project_id if row else None) or ""
            dataset_id = (row.bq_mart_dataset_id if row else None) or "marketing_marts"
            if not project_id:
                return _redirect("error=" + _quote("Save a GCP project ID first before provisioning."))
            result = client_bq_service.provision_mart_tables(
                project_id=project_id,
                dataset_id=dataset_id,
            )
            created = len(result.get("tables_created", []))
            existed = len(result.get("tables_already_existed", []))
            return _redirect("bq_saved=1&provision_ok=1&created=" + str(created) + "&existed=" + str(existed))
        except Exception as exc:
            return _redirect("error=" + _quote(str(exc)[:300]))

    raise HTTPException(status_code=400, detail="Unknown action.")


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
    if normalized == "nixon":
        _authorize_nixon_api(
            request,
            key=key,
            bearer_credentials=bearer_credentials,
            x_api_key=x_api_key,
        )
        start, end = _resolve_nixon_marketing_dates(start_date, end_date)
        try:
            return nixon_marketing_service.fetch_nixon_summary(start_date=start, end_date=end)
        except Exception as exc:
            raise _nixon_endpoint_failure(exc) from exc
    project_id, dataset_id = _load_bq_test_config(normalized)
    _authorize_bq_client_api(
        request,
        client_slug=normalized,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return client_bq_service.fetch_summary(
            client_key=normalized,
            start_date=start,
            end_date=end,
            project_id=project_id,
            dataset_id=dataset_id,
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
    if normalized == "nixon":
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
    project_id, dataset_id = _load_bq_test_config(normalized)
    _authorize_bq_client_api(
        request,
        client_slug=normalized,
        key=key,
        bearer_credentials=bearer_credentials,
        x_api_key=x_api_key,
    )
    try:
        return client_bq_service.fetch_health(
            client_key=normalized,
            limit=limit,
            project_id=project_id,
            dataset_id=dataset_id,
        )
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
    "/api/clients/nixon/meta/debug-insights",
    summary="Debug: probe Meta Insights API for Nixon ad account (no BQ write)",
    include_in_schema=False,
)
def nixon_meta_debug_insights(
    request: Request,
    level: str = Query(default="campaign", description="campaign | adset | ad"),
    days: int = Query(default=7, description="How many trailing days to probe"),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(
        request, key=key,
        bearer_credentials=bearer_credentials, x_api_key=x_api_key,
    )
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
        return nixon_marketing_service.fetch_nixon_meta_explorer(
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


@router.get(
    "/api/clients/nixon/pages/traffic-acquisition",
    summary="Nixon GA4 traffic acquisition breakdown from BigQuery",
)
def nixon_traffic_acquisition(
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
        return nixon_marketing_service.fetch_nixon_traffic_acquisition(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/device-split",
    summary="Nixon GA4 device category breakdown from BigQuery",
)
def nixon_device_split(
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
        return nixon_marketing_service.fetch_nixon_device_split(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/nixon/pages/landing",
    summary="Nixon GA4 landing page performance from BigQuery",
)
def nixon_landing_pages(
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
        return nixon_marketing_service.fetch_nixon_landing_pages(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/analytics/conversions", summary="Nixon GA4 conversion events breakdown")
def nixon_conversion_events(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(request, key=key, bearer_credentials=bearer_credentials, x_api_key=x_api_key)
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_conversion_events(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/analytics/user-acquisition", summary="Nixon GA4 first-touch user acquisition")
def nixon_user_acquisition(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(request, key=key, bearer_credentials=bearer_credentials, x_api_key=x_api_key)
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_user_acquisition(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get("/api/clients/nixon/analytics/demographics", summary="Nixon GA4 demographic & geographic breakdown")
def nixon_demographics(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    _authorize_nixon_api(request, key=key, bearer_credentials=bearer_credentials, x_api_key=x_api_key)
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_demographics(start_date=start, end_date=end)
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/marketing/health",
    summary="Client paid media mart health from BigQuery (generic BQ-test clients)",
)
def client_marketing_health(
    client_key: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500, description="Maximum mart_health rows to return."),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    _authorize_bq_client_api(
        request, client_slug=normalized, key=key,
        bearer_credentials=bearer_credentials, x_api_key=x_api_key,
    )
    try:
        return client_bq_service.fetch_health(
            client_key=normalized, limit=limit,
            project_id=project_id, dataset_id=dataset_id,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/google-ads/explorer",
    summary="Client Google Ads explorer from BigQuery marketing mart (generic BQ-test clients)",
)
def client_google_ads_explorer(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    _authorize_bq_client_api(
        request, client_slug=normalized, key=key,
        bearer_credentials=bearer_credentials, x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return client_bq_service.fetch_google_ads_explorer(
            client_key=normalized, start_date=start, end_date=end,
            project_id=project_id, dataset_id=dataset_id,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/linkedin/explorer",
    summary="Client LinkedIn creative explorer from BigQuery marketing mart (generic BQ-test clients)",
)
def client_linkedin_explorer(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    _authorize_bq_client_api(
        request, client_slug=normalized, key=key,
        bearer_credentials=bearer_credentials, x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return client_bq_service.fetch_linkedin_explorer(
            client_key=normalized, start_date=start, end_date=end,
            project_id=project_id, dataset_id=dataset_id,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/top",
    summary="Client top pages from BigQuery (generic BQ-test clients)",
)
def client_pages_top(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    _authorize_bq_client_api(
        request, client_slug=normalized, key=key,
        bearer_credentials=bearer_credentials, x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return client_bq_service.fetch_pages_top(
            client_key=normalized, start_date=start, end_date=end,
            project_id=project_id, dataset_id=dataset_id,
        )
    except Exception as exc:
        raise _nixon_endpoint_failure(exc) from exc


@router.get(
    "/api/clients/{client_key}/pages/sources",
    summary="Client page source breakdown from BigQuery (generic BQ-test clients)",
)
def client_pages_sources(
    client_key: str,
    request: Request,
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    key: str | None = None,
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> dict:
    normalized = (client_key or "").strip().lower()
    project_id, dataset_id = _load_bq_test_config(normalized)
    _authorize_bq_client_api(
        request, client_slug=normalized, key=key,
        bearer_credentials=bearer_credentials, x_api_key=x_api_key,
    )
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return client_bq_service.fetch_pages_sources(
            client_key=normalized, start_date=start, end_date=end,
            project_id=project_id, dataset_id=dataset_id,
        )
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
