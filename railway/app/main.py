from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import bigquery_service
import dashboard_snapshots
import dashboard_service
from dashboard.routes import register_dashboard_routes
from dashboard.utils.dates import WAREHOUSE_DATE_RANGES
import ga4_warehouse_service
import google_ads_service
import linkedin_service
import meta_service
import indeed_service
import db_cache
import warehouse
from auth import creds_fingerprint, env_summary
from linkedin_auth import env_summary as linkedin_env_summary
from meta_auth import env_summary as meta_env_summary
from indeed_auth import env_summary as indeed_env_summary
from cron_security import require_cron_secret
from security import configured_api_key, is_production, require_api_key
import audit_log
import client_config
import feature_requests
import client_dashboard_config
import dashboard_registry
import business_line_rules
import client_insight_documents
import dashboard_settings
import ga4_credentials
import railway_api
import dashboard_features
import dashboard_theme
import login_rate_limit
import not_found_page
import connector_config_store
import oauth_flows
import oauth_store
import web_auth
import web_security
import web_users
from models import (
    AccountsResponse,
    AccountRef,
    CacheHealthResponse,
    SummaryAllRequest,
    SummaryAllResponse,
    GoogleAdsEnvSummary,
    HealthResponse,
    HealthReadyResponse,
    SearchManyRequest,
    SearchManyResponse,
    SearchManyResult,
    SearchRequest,
    SearchResponse,
    CredsFingerprintResponse,
    Ga4EnvSummary,
    Ga4ClientsResponse,
    Ga4ClientRef,
    Ga4QueryRequest,
    Ga4QueryResponse,
    TestTokenResponse,
    YoutubeVideosRequest,
    YoutubeVideosResponse,
    YoutubeVideoItem,
    LinkedInEnvSummary,
    LinkedInTestTokenResponse,
    LinkedInAccountsResponse,
    LinkedInAccountRef,
    LinkedInPerformanceResponse,
    LinkedInPerformanceTotals,
    LinkedInCampaignPerformance,
    LinkedInCampaignGroupRef,
    LinkedInCampaignGroupsResponse,
    LinkedInCampaignGroupPerformance,
    LinkedInCampaignGroupsPerformanceTotals,
    LinkedInCampaignGroupsPerformanceResponse,
    LinkedInCreativePerformance,
    LinkedInCreativesPerformanceTotals,
    LinkedInCreativesPerformanceResponse,
    LinkedInVideoItem,
    LinkedInVideosResponse,
    LinkedInWarehouseSyncRequest,
    LinkedInWarehouseSyncResponse,
    MetaEnvSummary,
    MetaTestTokenResponse,
    MetaTestAdsAccessResponse,
    MetaAccountsResponse,
    MetaAccountRef,
    MetaPerformanceResponse,
    MetaPerformanceTotals,
    MetaCampaignPerformance,
    MetaAdSetPerformance,
    MetaAdSetsPerformanceTotals,
    MetaAdSetsPerformanceResponse,
    MetaVideoItem,
    MetaVideosResponse,
    MetaWarehouseSyncRequest,
    MetaWarehouseSyncResponse,
    GoogleAdsWarehouseSyncRequest,
    Ga4WarehouseSyncRequest,
    WarehouseSyncResponse,
    WarehouseStatusResponse,
    WarehouseMetricsResponse,
)
from indeed_models import (
    IndeedEnvSummary,
    IndeedTestTokenResponse,
    IndeedJobPostingsResponse,
    IndeedJobPostingRef,
    IndeedJobPostingDetailsResponse,
    IndeedRegistrationAnalyticsResponse,
    IndeedJobPostingsRequest,
    IndeedAnalyticsRequest,
)


load_dotenv()


def _production_hide_api_docs() -> bool:
    """Hide Swagger/OpenAPI UI on Railway unless DISABLE_API_DOCS=0."""
    raw = (os.getenv("DISABLE_API_DOCS") or "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return bool(
        (os.getenv("RAILWAY_ENVIRONMENT") or "").strip()
        or (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    )


_hide_api_docs = _production_hide_api_docs()


def _require_api_key_configured() -> None:
    """Refuse to start in production when API_KEY is not set.

    Without API_KEY every platform endpoint (/google-ads/*, /linkedin/*, /meta/*,
    /ga4/*, /indeed/*, /warehouse/*) is publicly accessible. Fail hard here rather
    than silently exposing live marketing data.
    """
    if is_production() and not configured_api_key():
        import sys

        print(
            "FATAL: API_KEY is not set. All platform API routes (/google-ads/*, /linkedin/*, "
            "/meta/*, /ga4/*, /indeed/*, /warehouse/*) would be publicly accessible. "
            "Set API_KEY in Railway environment variables and redeploy.",
            file=sys.stderr,
        )
        sys.exit(1)


_require_api_key_configured()

app = FastAPI(
    title="EOS Ads + GA4 Service",
    version="0.2.0",
    description=(
        "All /google-ads/*, /linkedin/*, /meta/*, /ga4/*, /indeed/*, and /warehouse/* routes "
        "require Authorization: Bearer <API_KEY> or header X-API-Key: <API_KEY>. "
        "GET /health stays public for load balancers."
    ),
    docs_url=None if _hide_api_docs else "/docs",
    redoc_url=None if _hide_api_docs else "/redoc",
    openapi_url=None if _hide_api_docs else "/openapi.json",
)

try:
    db_cache.ensure_schema()
    warehouse.ensure_schema()
    dashboard_snapshots.ensure_schema()
    web_users.ensure_schema()
    audit_log.ensure_schema()
    client_dashboard_config.ensure_schema()
    dashboard_registry.ensure_schema()
    # Grandfather existing 'standard' users to every current client so the switch
    # to per-client scoping doesn't lock them out; runs once per user (IS NULL).
    try:
        _granted = web_users.backfill_standard_all_access(dashboard_registry.list_slugs())
        if _granted:
            print(f"Startup: granted all-client access to {_granted} pre-scoping standard user(s).")
    except Exception as _bf_exc:
        import sys as _sys
        print(f"WARNING: standard-user access backfill failed: {_bf_exc}", file=_sys.stderr)
    # One-time backfill: segment_filter_profile replaced runtime slug/label
    # inference for the campaign/segment filters. Seed the clients that predate
    # the column (only where unset) so their filters keep working; new clients
    # set this from Settings. Idempotent — safe to run every boot.
    try:
        for _seed_slug in ("nixon", "nixon-bq-test"):
            client_dashboard_config.backfill_segment_filter_profile(_seed_slug, "regions")
            # Also persist Nixon's mart destination onto its config row so HQ /
            # agency reads resolve it from config instead of a client-name fallback.
            client_dashboard_config.backfill_marketing_mart_destination(
                _seed_slug, "nixon-medical", "marketing_marts"
            )
    except Exception as _seg_exc:
        import sys as _sys
        print(f"WARNING: client config backfill failed: {_seg_exc}", file=_sys.stderr)
    # Built-in demo client: an always-available dashboard populated entirely
    # with synthetic sample data (no GCP project / connectors / live data), for
    # pitching prospects, walking clients through the portal, and training
    # staff. Idempotent; opt out with DEMO_CLIENT_ENABLED=0. Never fatal.
    try:
        import demo_client
        if demo_client.seed_demo_client():
            print(f"Startup: demo client '{demo_client.DEMO_SLUG}' ready.")
    except Exception as _demo_exc:
        import sys as _sys
        print(f"WARNING: demo client seed failed: {_demo_exc}", file=_sys.stderr)
    business_line_rules.ensure_schema()
    client_insight_documents.ensure_schema()
    oauth_store.ensure_schema()
    connector_config_store.ensure_schema()
    # Close out any sync-run left 'running' by a redeploy/crash mid-sync — the
    # BackgroundTask that would have finished it died with the old process, so
    # without this the connector stays stuck showing 'syncing' forever.
    try:
        _orphan_min = int((os.getenv("CONNECTOR_SYNC_ORPHAN_MINUTES") or "0").strip() or "0")
    except ValueError:
        _orphan_min = 0
    _orphaned = connector_config_store.fail_orphaned_sync_runs(older_than_minutes=_orphan_min)
    if _orphaned:
        print(f"Startup: closed {_orphaned} orphaned connector sync run(s).")
    # Consent & Tracking Health: ensure schema and close out scans left 'running'
    # by a redeploy/crash mid-scan (the background task died with the old process).
    try:
        import consent_store
        consent_store.ensure_schema()
        _consent_orphans = consent_store.fail_orphaned_runs(older_than_minutes=_orphan_min)
        if _consent_orphans:
            print(f"Startup: closed {_consent_orphans} orphaned consent scan run(s).")
    except Exception as _consent_exc:
        import sys as _sys
        print(f"WARNING: consent scan schema init failed: {_consent_exc}", file=_sys.stderr)
    login_rate_limit.ensure_schema()
    boot = web_users.bootstrap_admin_from_env()
    if boot:
        audit_log.record(
            action="user.bootstrap_admin",
            subject_email=boot.email,
            detail={"source": "AUTH_BOOTSTRAP_ADMIN_*"},
        )
except Exception as _boot_exc:
    import sys as _sys
    print(f"WARNING: DB schema/bootstrap error at startup: {_boot_exc}", file=_sys.stderr)

@app.middleware("http")
async def _inject_html_extras(request: Request, call_next):
    """Rewrite HTML responses to add the impersonation bar and CSRF plumbing.

    Registered before the session middleware so, in Starlette's stack, the
    session middleware ends up outermost and request.session is populated by
    the time this runs. Doing it here (rather than in each renderer) keeps the
    exit affordance and CSRF token on every page — dashboard, settings, files,
    connectors, the dashboards picker — with no per-renderer plumbing.

    Seeding the CSRF token only on HTML responses confines the session cookie
    to browser page loads; the mutation lands in request.session before the
    outer SessionMiddleware serializes the cookie on the way out.
    """
    response = await call_next(request)
    if "text/html" not in (response.headers.get("content-type") or "").lower():
        return response
    try:
        banner = web_auth.impersonation_banner_html(request)
    except Exception:
        banner = ""
    csrf_token: str | None = None
    if "session" in request.scope:
        try:
            csrf_token = web_security.ensure_csrf_token(request.session)
        except Exception:
            csrf_token = None
    if not banner and not csrf_token:
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    text = body.decode(response.charset or "utf-8")
    text = web_security.assemble_page_html(text, banner=banner, csrf_token=csrf_token)
    headers = {
        k: v
        for k, v in response.headers.items()
        if k.lower() not in ("content-length", "content-encoding")
    }
    return Response(
        content=text,
        status_code=response.status_code,
        headers=headers,
        media_type="text/html",
    )


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach a framework-safe set of security headers to every response."""
    response = await call_next(request)
    https = request.url.scheme == "https" or is_production()
    web_security.apply_security_headers(response, https=https)
    return response


@app.middleware("http")
async def _track_last_seen(request: Request, call_next):
    """Stamp the signed-in user's last-activity time (throttled, best-effort).

    Long-lived sessions mean last_login_at alone can't show whether a user has
    returned since; this keeps last_seen_at current from any authenticated
    request. touch_last_seen throttles the write and swallows its own errors, so
    this never blocks or fails the request. Runs inner to the session
    middleware, so request.session is populated and the throttle marker it sets
    is serialized back into the cookie on the way out.
    """
    response = await call_next(request)
    web_auth.touch_last_seen(request)
    return response


@app.middleware("http")
async def _csrf_protect(request: Request, call_next):
    """Reject cookie-authenticated state changes that lack a valid CSRF token."""
    if web_security.requires_csrf(request):
        if not await web_security.validate_csrf(request):
            return Response("CSRF verification failed.", status_code=403)
    return await call_next(request)


if web_users.enabled():
    try:
        web_auth.add_session_middleware(app)
    except RuntimeError as _sess_exc:
        # No session signing secret: browser login is disabled. Surface it loudly
        # rather than silently — in production this means AUTH_SESSION_SECRET is
        # unset (it no longer falls back to CRON_SECRET / API_KEY).
        import sys as _sys
        print(f"WARNING: session auth disabled — {_sess_exc}", file=_sys.stderr)

register_dashboard_routes(app)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_HTML_404_API_PREFIXES = (
    "/google-ads",
    "/linkedin",
    "/meta",
    "/ga4",
    "/indeed",
    "/warehouse",
    "/openapi",
    "/health",
    "/cron",
)


def _request_wants_html_error(request: Request) -> bool:
    """True for a top-level browser navigation (as opposed to an API/XHR call).

    Used to decide whether an error should render as a friendly HTML page or a
    JSON body: API prefixes and JSON-only Accept headers stay JSON.
    """
    path = request.url.path
    if any(path.startswith(prefix) for prefix in _HTML_404_API_PREFIXES):
        return False
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return False
    return True


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> HTMLResponse | JSONResponse:
    detail = exc.detail
    if not isinstance(detail, str):
        detail = str(detail)
    if _request_wants_html_error(request):
        if exc.status_code == 404:
            return HTMLResponse(
                not_found_page.render_not_found_page(path=request.url.path),
                status_code=404,
            )
        # A browser navigation that 401/403s (e.g. an admin stuck impersonating
        # a user without access to this page) must not dead-end on a JSON blob:
        # render a navigable HTML page so the injected "Exit view as" banner and
        # the back-links give a way out.
        if exc.status_code in (401, 403):
            return HTMLResponse(
                not_found_page.render_error_page(status_code=exc.status_code, detail=detail),
                status_code=exc.status_code,
            )
    return JSONResponse({"detail": detail}, status_code=exc.status_code)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["BearerAuth"] = {"type": "http", "scheme": "bearer", "description": "Same value as Railway `API_KEY`."}
    schemes["ApiKeyHeader"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Same value as Railway `API_KEY`.",
    }
    for path, item in schema.get("paths", {}).items():
        if not (
            path.startswith("/google-ads")
            or path.startswith("/linkedin")
            or path.startswith("/meta")
            or path.startswith("/ga4")
            or path.startswith("/indeed")
            or path.startswith("/warehouse")
        ):
            continue
        for method in ("get", "post", "put", "delete", "patch"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            # Either Bearer or X-API-Key (OpenAPI: alternatives are OR).
            op["security"] = [{"BearerAuth": []}, {"ApiKeyHeader": []}]
    # Advertise a root-level `servers` URL (FastAPI omits it by default) so the
    # published schema resolves absolute paths for external API clients.
    base_url = (
        os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        or "https://sagefrog-production.up.railway.app"
    )
    schema["servers"] = [{"url": base_url}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/")
def root() -> dict:
    out = {
        "service": "EOS Ads + GA4 Service",
        "health": "/health",
        "google_ads_test_token": "/google-ads/test-token",
        "youtube_videos": "/google-ads/youtube-videos",
        "linkedin_env": "/linkedin/env",
        "linkedin_test_token": "/linkedin/test-token",
        "linkedin_accounts": "/linkedin/accounts",
        "linkedin_performance": "/linkedin/performance",
        "linkedin_campaign_groups": "/linkedin/campaign-groups",
        "linkedin_campaign_groups_performance": "/linkedin/campaign-groups/performance",
        "linkedin_creatives_performance": "/linkedin/creatives/performance",
        "linkedin_videos": "/linkedin/videos",
        "linkedin_warehouse_sync": "/linkedin/warehouse/sync",
        "meta_env": "/meta/env",
        "meta_test_token": "/meta/test-token",
        "meta_test_ads_access": "/meta/test-ads-access",
        "meta_accounts": "/meta/accounts",
        "meta_performance": "/meta/performance",
        "meta_adsets_performance": "/meta/adsets/performance",
        "meta_videos": "/meta/videos",
        "meta_warehouse_sync": "/meta/warehouse/sync",
        "indeed_env": "/indeed/env",
        "indeed_test_token": "/indeed/test-token",
        "indeed_job_postings": "/indeed/postings",
        "indeed_job_posting_detail": "/indeed/postings/{posting_id}",
        "indeed_registration_analytics": "/indeed/analytics",
        "google_ads_warehouse_sync": "/google-ads/warehouse/sync",
        "ga4_warehouse_sync": "/ga4/warehouse/sync",
        "warehouse_status": "/warehouse/status",
        "warehouse_metrics": "/warehouse/metrics",
        "login": "/login",
        "admin": "/admin",
        "dashboard_client_settings": "/dashboard/{client_slug}/settings",
        "ga4_env": "/ga4/env",
    }
    if not _hide_api_docs:
        out["docs"] = "/docs"
    return out


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/health/ready", response_model=HealthReadyResponse)
def health_ready(response: Response) -> HealthReadyResponse:
    """Readiness probe: verifies DB connectivity so a broken deploy is not
    reported healthy. `/health` stays liveness-only."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        response.status_code = 503
        return HealthReadyResponse(status="error", database=False, detail="DATABASE_URL is not set.")
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return HealthReadyResponse(status="ok", database=True)
    except Exception as exc:
        response.status_code = 503
        return HealthReadyResponse(status="error", database=False, detail=str(exc)[:200])


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    path = STATIC_DIR / "favicon.ico"
    if not path.is_file():
        path = STATIC_DIR / "favicon-32x32.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Favicon not found")
    media_type = "image/x-icon" if path.suffix == ".ico" else "image/png"
    return FileResponse(path, media_type=media_type)


@app.get(
    "/cache/health",
    response_model=CacheHealthResponse,
    dependencies=[Depends(require_api_key)],
)
def cache_health() -> CacheHealthResponse:
    return CacheHealthResponse(**db_cache.status())


# ============================================================================
# INDEED ENDPOINTS
# ============================================================================


@app.get(
    "/indeed/env",
    response_model=IndeedEnvSummary,
    dependencies=[Depends(require_api_key)],
    summary="Check Indeed API environment configuration",
)
def indeed_env() -> IndeedEnvSummary:
    """Check if Indeed API credentials are configured (no secrets returned)."""
    return IndeedEnvSummary(**indeed_env_summary())


@app.get(
    "/indeed/test-token",
    response_model=IndeedTestTokenResponse,
    dependencies=[Depends(require_api_key)],
    summary="Validate Indeed API credentials",
)
def indeed_test_token() -> IndeedTestTokenResponse:
    """Verify Indeed API credentials are valid."""
    try:
        result = indeed_service.test_token()
    except Exception as e:
        return IndeedTestTokenResponse(
            ok=False,
            message="Could not validate Indeed credentials",
            error=str(e),
        )
    return IndeedTestTokenResponse(**result)


@app.post(
    "/indeed/postings",
    response_model=IndeedJobPostingsResponse,
    dependencies=[Depends(require_api_key)],
    summary="List job postings with registration counts",
    description="Fetch all job postings with titles and registration counts from Indeed.",
)
def indeed_job_postings(body: IndeedJobPostingsRequest) -> IndeedJobPostingsResponse:
    """Retrieve job postings with titles and registration counts."""
    cache_payload = {
        "account_id": body.account_id,
        "limit": body.limit,
        "status": body.status,
    }
    hit = db_cache.get_cached("indeed.postings", cache_payload)
    if hit is not None:
        rows = hit.response_json or []
        return IndeedJobPostingsResponse(
            count=int(hit.row_count or len(rows)),
            postings=[IndeedJobPostingRef(**r) for r in rows],
        )
    
    try:
        rows = indeed_service.list_job_postings(
            account_id=body.account_id,
            limit=body.limit,
            status=body.status,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    
    try:
        db_cache.put_cached(
            "indeed.postings",
            cache_payload,
            response_json=rows,
            row_count=len(rows),
            status="ok",
            error=None,
            ttl_seconds=3600,  # Cache for 1 hour
        )
    except Exception:
        pass
    
    return IndeedJobPostingsResponse(
        count=len(rows),
        postings=[IndeedJobPostingRef(**r) for r in rows],
    )


@app.get(
    "/indeed/postings/{posting_id}",
    response_model=IndeedJobPostingDetailsResponse,
    dependencies=[Depends(require_api_key)],
    summary="Get details for a specific job posting",
)
def indeed_job_posting_detail(posting_id: str) -> IndeedJobPostingDetailsResponse:
    """Retrieve detailed information for a single job posting."""
    posting_id = posting_id.strip()
    if not posting_id:
        raise HTTPException(status_code=400, detail="posting_id is required")
    
    cache_payload = {"posting_id": posting_id}
    hit = db_cache.get_cached("indeed.posting_detail", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return IndeedJobPostingDetailsResponse(**payload)
    
    try:
        payload = indeed_service.get_job_posting(posting_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    
    try:
        db_cache.put_cached(
            "indeed.posting_detail",
            cache_payload,
            response_json=payload,
            row_count=1,
            status="ok",
            error=None,
            ttl_seconds=3600,
        )
    except Exception:
        pass
    
    return IndeedJobPostingDetailsResponse(**payload)


@app.post(
    "/indeed/analytics",
    response_model=IndeedRegistrationAnalyticsResponse,
    dependencies=[Depends(require_api_key)],
    summary="Get registration analytics by job title",
    description="Retrieve aggregated registration counts grouped by job title.",
)
def indeed_registration_analytics(body: IndeedAnalyticsRequest) -> IndeedRegistrationAnalyticsResponse:
    """Retrieve registration analytics aggregated by job title."""
    cache_payload = {
        "posting_id": body.posting_id,
        "account_id": body.account_id,
        "date_from": body.date_from,
        "date_to": body.date_to,
    }
    hit = db_cache.get_cached("indeed.analytics", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return IndeedRegistrationAnalyticsResponse(**payload)
    
    try:
        payload = indeed_service.get_registration_analytics(
            posting_id=body.posting_id,
            account_id=body.account_id,
            date_from=body.date_from,
            date_to=body.date_to,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    
    try:
        db_cache.put_cached(
            "indeed.analytics",
            cache_payload,
            response_json=payload,
            row_count=payload.get("posting_count", 0),
            status="ok",
            error=None,
            ttl_seconds=3600,
        )
    except Exception:
        pass
    
    return IndeedRegistrationAnalyticsResponse(**payload)


# ============================================================================
# GOOGLE ADS ENDPOINTS
# ============================================================================

@app.get(
    "/google-ads/env",
    response_model=GoogleAdsEnvSummary,
    dependencies=[Depends(require_api_key)],
)
def google_ads_env() -> GoogleAdsEnvSummary:
    return GoogleAdsEnvSummary(**env_summary())


@app.get(
    "/google-ads/creds-check",
    response_model=CredsFingerprintResponse,
    dependencies=[Depends(require_api_key)],
)
def google_ads_creds_check() -> CredsFingerprintResponse:
    """Compare token prefixes/lengths with OAuth Playground (no secrets returned)."""
    return CredsFingerprintResponse(**creds_fingerprint())


@app.get(
    "/google-ads/test-token",
    response_model=TestTokenResponse,
    dependencies=[Depends(require_api_key)],
)
def google_ads_test_token() -> TestTokenResponse:
    """Verify OAuth refresh only (no GAQL). Returns ok=false with error detail on failure."""
    try:
        result = google_ads_service.test_refresh_token()
    except Exception as e:
        return TestTokenResponse(
            ok=False,
            message="Could not load credentials from environment.",
            error=str(e),
        )
    return TestTokenResponse(**result)


@app.post(
    "/google-ads/search",
    response_model=SearchResponse,
    dependencies=[Depends(require_api_key)],
)
def google_ads_search(body: SearchRequest) -> SearchResponse:
    cache_payload = {"customer_id": body.customer_id, "query": body.query}
    hit = db_cache.get_cached("google_ads.search", cache_payload)
    if hit is not None:
        return SearchResponse(
            customer_id=body.customer_id,
            row_count=int(hit.row_count or 0),
            rows=hit.response_json or [],
        )
    try:
        rows = google_ads_service.search(customer_id=body.customer_id, query=body.query)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "google_ads.search",
            cache_payload,
            response_json=rows,
            row_count=len(rows),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return SearchResponse(customer_id=body.customer_id, row_count=len(rows), rows=rows)


@app.get(
    "/google-ads/accounts",
    response_model=AccountsResponse,
    dependencies=[Depends(require_api_key)],
)
def google_ads_accounts() -> AccountsResponse:
    try:
        customer_ids = google_ads_service.list_accessible_customer_ids()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    accounts: list[AccountRef] = []
    for cid in customer_ids:
        try:
            meta = google_ads_service.get_account_metadata(customer_id=cid)
            accounts.append(AccountRef(**meta))
        except Exception as e:
            accounts.append(
                AccountRef(
                    customer_id=cid,
                    resource_name=f"customers/{cid}",
                    status="error",
                    error=str(e),
                )
            )
    return AccountsResponse(count=len(accounts), accounts=accounts)


@app.post(
    "/google-ads/youtube-videos",
    response_model=YoutubeVideosResponse,
    dependencies=[Depends(require_api_key)],
    summary="List YouTube video assets with watch/embed URLs",
    description=(
        "Returns YouTube links from Google Ads video assets (not parsed from ad names). "
        "Merges ad_group_ad_asset_view (Demand Gen, etc.), classic VIDEO ads, and optional "
        "account-level YOUTUBE_VIDEO assets."
    ),
)
def google_ads_youtube_videos(body: YoutubeVideosRequest) -> YoutubeVideosResponse:
    cache_payload = {
        "customer_id": body.customer_id,
        "include_account_assets": body.include_account_assets,
        "include_metrics": body.include_metrics,
        "date_range": body.date_range,
    }
    hit = db_cache.get_cached("google_ads.youtube_videos", cache_payload)
    if hit is not None:
        rows = hit.response_json or []
        return YoutubeVideosResponse(
            customer_id=body.customer_id,
            row_count=int(hit.row_count or len(rows)),
            videos=[YoutubeVideoItem(**r) for r in rows],
        )
    try:
        rows = google_ads_service.list_youtube_videos(
            customer_id=body.customer_id,
            include_account_assets=body.include_account_assets,
            include_metrics=body.include_metrics,
            date_range=body.date_range,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "google_ads.youtube_videos",
            cache_payload,
            response_json=rows,
            row_count=len(rows),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return YoutubeVideosResponse(
        customer_id=body.customer_id,
        row_count=len(rows),
        videos=[YoutubeVideoItem(**r) for r in rows],
    )


@app.post(
    "/google-ads/search-many",
    response_model=SearchManyResponse,
    dependencies=[Depends(require_api_key)],
)
def google_ads_search_many(body: SearchManyRequest) -> SearchManyResponse:
    results: list[SearchManyResult] = []
    for cid in body.customer_ids:
        try:
            rows = google_ads_service.search(customer_id=cid, query=body.query)
            results.append(SearchManyResult(customer_id=cid, row_count=len(rows), rows=rows))
        except Exception as e:
            results.append(SearchManyResult(customer_id=cid, status="error", error=str(e)))

    success_count = sum(1 for r in results if r.status == "ok")
    failure_count = len(results) - success_count
    return SearchManyResponse(
        requested_count=len(body.customer_ids),
        success_count=success_count,
        failure_count=failure_count,
        results=results,
    )


@app.post(
    "/google-ads/summary-all",
    response_model=SummaryAllResponse,
    dependencies=[Depends(require_api_key)],
)
def google_ads_summary_all(body: SummaryAllRequest) -> SummaryAllResponse:
    allowed_ranges = {"LAST_7_DAYS", "LAST_30_DAYS", "THIS_MONTH", "LAST_MONTH"}
    if body.date_range not in allowed_ranges:
        raise HTTPException(status_code=400, detail=f"Invalid date_range: {body.date_range}")

    customer_ids = body.customer_ids or google_ads_service.list_accessible_customer_ids()
    account_rows: list[dict] = []
    totals = {"impressions": 0, "clicks": 0, "conversions": 0.0, "cost_micros": 0, "spend": 0.0}
    success_count = 0

    for cid in customer_ids:
        try:
            summary = google_ads_service.account_summary(customer_id=cid, date_range=body.date_range)
            account_rows.append({"customer_id": cid, "status": "ok", "summary": summary})
            totals["impressions"] += int(summary.get("impressions", 0) or 0)
            totals["clicks"] += int(summary.get("clicks", 0) or 0)
            totals["conversions"] += float(summary.get("conversions", 0.0) or 0.0)
            totals["cost_micros"] += int(summary.get("cost_micros", 0) or 0)
            totals["spend"] += float(summary.get("spend", 0.0) or 0.0)
            success_count += 1
        except Exception as e:
            account_rows.append({"customer_id": cid, "status": "error", "error": str(e)})

    totals["ctr"] = (totals["clicks"] / totals["impressions"]) if totals["impressions"] else 0.0
    return SummaryAllResponse(
        date_range=body.date_range,
        account_count=len(customer_ids),
        success_count=success_count,
        failure_count=len(customer_ids) - success_count,
        totals=totals,
        accounts=account_rows,
    )


@app.post(
    "/google-ads/warehouse/sync",
    response_model=WarehouseSyncResponse,
    dependencies=[Depends(require_api_key)],
    summary="Sync Google Ads daily metrics into Postgres warehouse",
)
def google_ads_warehouse_sync(body: GoogleAdsWarehouseSyncRequest) -> WarehouseSyncResponse:
    preset = body.date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(status_code=400, detail=f"Invalid date_range: {body.date_range}")
    try:
        result = google_ads_service.sync_account_to_warehouse(body.customer_id, date_range=preset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WarehouseSyncResponse(**result)


@app.post(
    "/ga4/warehouse/sync",
    response_model=WarehouseSyncResponse,
    dependencies=[Depends(require_api_key)],
    summary="Sync GA4 daily metrics from BigQuery into Postgres warehouse",
)
def ga4_warehouse_sync(body: Ga4WarehouseSyncRequest) -> WarehouseSyncResponse:
    preset = body.date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(status_code=400, detail=f"Invalid date_range: {body.date_range}")
    try:
        result = ga4_warehouse_service.sync_to_warehouse(
            date_range=preset,
            client_key=body.client_key,
            bq_project_id=body.bq_project_id,
            bq_dataset_id=body.bq_dataset_id,
            account_id=body.account_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WarehouseSyncResponse(**result)


@app.get(
    "/linkedin/env",
    response_model=LinkedInEnvSummary,
    dependencies=[Depends(require_api_key)],
)
def linkedin_env() -> LinkedInEnvSummary:
    return LinkedInEnvSummary(**linkedin_env_summary())


@app.get(
    "/linkedin/test-token",
    response_model=LinkedInTestTokenResponse,
    dependencies=[Depends(require_api_key)],
)
def linkedin_test_token() -> LinkedInTestTokenResponse:
    """Verify LinkedIn OAuth refresh and ad account access."""
    try:
        result = linkedin_service.test_refresh_token()
    except Exception as e:
        return LinkedInTestTokenResponse(
            ok=False,
            message="Could not load LinkedIn credentials from environment.",
            error=str(e),
        )
    return LinkedInTestTokenResponse(**result)


@app.get(
    "/linkedin/accounts",
    response_model=LinkedInAccountsResponse,
    dependencies=[Depends(require_api_key)],
)
def linkedin_accounts() -> LinkedInAccountsResponse:
    cache_payload: dict = {}
    hit = db_cache.get_cached("linkedin.accounts", cache_payload)
    if hit is not None:
        rows = hit.response_json or []
        return LinkedInAccountsResponse(
            count=int(hit.row_count or len(rows)),
            accounts=[LinkedInAccountRef(**r) for r in rows],
        )
    try:
        rows = linkedin_service.list_ad_accounts()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "linkedin.accounts",
            cache_payload,
            response_json=rows,
            row_count=len(rows),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return LinkedInAccountsResponse(
        count=len(rows),
        accounts=[LinkedInAccountRef(**r) for r in rows],
    )


@app.get(
    "/linkedin/performance",
    response_model=LinkedInPerformanceResponse,
    dependencies=[Depends(require_api_key)],
    summary="LinkedIn Ads performance for one account",
)
def linkedin_performance(
    account_id: str,
    date_range: str = "LAST_30_DAYS",
) -> LinkedInPerformanceResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    preset = date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(WAREHOUSE_DATE_RANGES))}",
        )

    cache_payload = {"account_id": account_id, "date_range": preset}
    hit = db_cache.get_cached("linkedin.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return LinkedInPerformanceResponse(
            account_id=payload.get("account_id", account_id),
            entity_level=payload.get("entity_level", "account"),
            date_range=payload.get("date_range", {}),
            totals=LinkedInPerformanceTotals(**(payload.get("totals") or {})),
            campaigns=[LinkedInCampaignPerformance(**c) for c in payload.get("campaigns") or []],
            warehouse=payload.get("warehouse"),
        )
    try:
        payload = linkedin_service.account_performance(account_id, date_range=preset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "linkedin.performance",
            cache_payload,
            response_json=payload,
            row_count=len(payload.get("campaigns") or []),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return LinkedInPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=LinkedInPerformanceTotals(**payload["totals"]),
        campaigns=[LinkedInCampaignPerformance(**c) for c in payload["campaigns"]],
        warehouse=payload.get("warehouse"),
    )


@app.get(
    "/linkedin/campaign-groups",
    response_model=LinkedInCampaignGroupsResponse,
    dependencies=[Depends(require_api_key)],
    summary="List LinkedIn campaign groups for one ad account",
)
def linkedin_campaign_groups(account_id: str) -> LinkedInCampaignGroupsResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")

    cache_payload = {"account_id": account_id}
    hit = db_cache.get_cached("linkedin.campaign_groups", cache_payload)
    if hit is not None:
        rows = hit.response_json or []
        return LinkedInCampaignGroupsResponse(
            account_id=account_id,
            count=int(hit.row_count or len(rows)),
            campaign_groups=[LinkedInCampaignGroupRef(**r) for r in rows],
        )
    try:
        rows = linkedin_service.list_campaign_groups(account_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "linkedin.campaign_groups",
            cache_payload,
            response_json=rows,
            row_count=len(rows),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return LinkedInCampaignGroupsResponse(
        account_id=str(account_id).strip().split(":")[-1],
        count=len(rows),
        campaign_groups=[LinkedInCampaignGroupRef(**r) for r in rows],
    )


@app.get(
    "/linkedin/campaign-groups/performance",
    response_model=LinkedInCampaignGroupsPerformanceResponse,
    dependencies=[Depends(require_api_key)],
    summary="LinkedIn Ads performance by campaign group",
)
def linkedin_campaign_groups_performance(
    account_id: str,
    date_range: str = "LAST_30_DAYS",
) -> LinkedInCampaignGroupsPerformanceResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    preset = date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(WAREHOUSE_DATE_RANGES))}",
        )

    cache_payload = {"account_id": account_id, "date_range": preset}
    hit = db_cache.get_cached("linkedin.campaign_groups.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return LinkedInCampaignGroupsPerformanceResponse(
            account_id=payload.get("account_id", account_id),
            entity_level=payload.get("entity_level", "account"),
            date_range=payload.get("date_range", {}),
            totals=LinkedInCampaignGroupsPerformanceTotals(**(payload.get("totals") or {})),
            campaign_groups=[
                LinkedInCampaignGroupPerformance(**g) for g in payload.get("campaign_groups") or []
            ],
        )
    try:
        payload = linkedin_service.campaign_groups_performance(account_id, date_range=preset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "linkedin.campaign_groups.performance",
            cache_payload,
            response_json=payload,
            row_count=len(payload.get("campaign_groups") or []),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return LinkedInCampaignGroupsPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=LinkedInCampaignGroupsPerformanceTotals(**payload["totals"]),
        campaign_groups=[
            LinkedInCampaignGroupPerformance(**g) for g in payload["campaign_groups"]
        ],
    )


@app.get(
    "/linkedin/creatives/performance",
    response_model=LinkedInCreativesPerformanceResponse,
    dependencies=[Depends(require_api_key)],
    summary="LinkedIn Ads performance by creative (sub-campaign; LinkedIn has no ad set)",
)
def linkedin_creatives_performance(
    account_id: str,
    date_range: str = "LAST_30_DAYS",
    campaign_id: str | None = None,
) -> LinkedInCreativesPerformanceResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    preset = date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(WAREHOUSE_DATE_RANGES))}",
        )
    campaign_id = (campaign_id or "").strip() or None

    cache_payload = {"account_id": account_id, "date_range": preset, "campaign_id": campaign_id}
    hit = db_cache.get_cached("linkedin.creatives.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return LinkedInCreativesPerformanceResponse(
            account_id=payload.get("account_id", account_id),
            entity_level=payload.get("entity_level", "account"),
            date_range=payload.get("date_range", {}),
            totals=LinkedInCreativesPerformanceTotals(**(payload.get("totals") or {})),
            creatives=[LinkedInCreativePerformance(**c) for c in payload.get("creatives") or []],
        )
    try:
        payload = linkedin_service.creatives_performance(
            account_id, date_range=preset, campaign_id=campaign_id
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "linkedin.creatives.performance",
            cache_payload,
            response_json=payload,
            row_count=len(payload.get("creatives") or []),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return LinkedInCreativesPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=LinkedInCreativesPerformanceTotals(**payload["totals"]),
        creatives=[LinkedInCreativePerformance(**c) for c in payload["creatives"]],
    )


@app.get(
    "/linkedin/videos",
    response_model=LinkedInVideosResponse,
    dependencies=[Depends(require_api_key)],
    summary="LinkedIn ad creative video/image URLs with thumbnails",
)
def linkedin_videos(
    account_id: str,
    campaign_id: str | None = None,
    videos_only: bool = True,
) -> LinkedInVideosResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    campaign_id = (campaign_id or "").strip() or None

    cache_payload = {
        "account_id": account_id,
        "campaign_id": campaign_id,
        "videos_only": videos_only,
    }
    hit = db_cache.get_cached("linkedin.videos", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return LinkedInVideosResponse(
            account_id=payload.get("account_id", account_id),
            row_count=int(payload.get("row_count") or 0),
            videos=[LinkedInVideoItem(**v) for v in payload.get("videos") or []],
        )
    try:
        payload = linkedin_service.list_video_creatives(
            account_id, campaign_id=campaign_id, videos_only=videos_only
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "linkedin.videos",
            cache_payload,
            response_json=payload,
            row_count=len(payload.get("videos") or []),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return LinkedInVideosResponse(
        account_id=payload["account_id"],
        row_count=payload["row_count"],
        videos=[LinkedInVideoItem(**v) for v in payload["videos"]],
    )


@app.post(
    "/linkedin/warehouse/sync",
    response_model=LinkedInWarehouseSyncResponse,
    dependencies=[Depends(require_api_key)],
    summary="Sync LinkedIn daily metrics into Postgres warehouse",
)
def linkedin_warehouse_sync(body: LinkedInWarehouseSyncRequest) -> LinkedInWarehouseSyncResponse:
    preset = body.date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(status_code=400, detail=f"Invalid date_range: {body.date_range}")
    try:
        result = linkedin_service.sync_account_to_warehouse(body.account_id, date_range=preset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return LinkedInWarehouseSyncResponse(**result)


@app.get(
    "/meta/env",
    response_model=MetaEnvSummary,
    dependencies=[Depends(require_api_key)],
)
def meta_env() -> MetaEnvSummary:
    return MetaEnvSummary(**meta_env_summary())


@app.get(
    "/meta/test-token",
    response_model=MetaTestTokenResponse,
    dependencies=[Depends(require_api_key)],
)
def meta_test_token() -> MetaTestTokenResponse:
    try:
        result = meta_service.test_access_token()
    except Exception as e:
        return MetaTestTokenResponse(
            ok=False,
            message="Could not load Meta credentials from environment.",
            error=str(e),
        )
    return MetaTestTokenResponse(**result)


@app.get(
    "/meta/test-ads-access",
    response_model=MetaTestAdsAccessResponse,
    dependencies=[Depends(require_api_key)],
    summary="Check ads_read access for one Meta ad account (required for metaVideos)",
)
def meta_test_ads_access(account_id: str) -> MetaTestAdsAccessResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    try:
        result = meta_service.test_ads_read_access(account_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return MetaTestAdsAccessResponse(**result)


@app.get(
    "/meta/accounts",
    response_model=MetaAccountsResponse,
    dependencies=[Depends(require_api_key)],
    summary="List Meta ad accounts in Business Manager",
)
def meta_accounts() -> MetaAccountsResponse:
    cache_payload: dict = {}
    hit = db_cache.get_cached("meta.accounts", cache_payload)
    if hit is not None:
        rows = hit.response_json or []
        return MetaAccountsResponse(
            count=int(hit.row_count or len(rows)),
            accounts=[MetaAccountRef(**r) for r in rows],
        )
    try:
        rows = meta_service.list_ad_accounts()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "meta.accounts",
            cache_payload,
            response_json=rows,
            row_count=len(rows),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return MetaAccountsResponse(
        count=len(rows),
        accounts=[MetaAccountRef(**r) for r in rows],
    )


@app.get(
    "/meta/performance",
    response_model=MetaPerformanceResponse,
    dependencies=[Depends(require_api_key)],
    summary="Meta Ads performance for one ad account",
)
def meta_performance(
    account_id: str,
    date_range: str = "LAST_30_DAYS",
) -> MetaPerformanceResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    preset = date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(WAREHOUSE_DATE_RANGES))}",
        )

    cache_payload = {"account_id": account_id, "date_range": preset}
    hit = db_cache.get_cached("meta.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return MetaPerformanceResponse(
            account_id=payload.get("account_id", account_id),
            entity_level=payload.get("entity_level", "account"),
            date_range=payload.get("date_range", {}),
            totals=MetaPerformanceTotals(**(payload.get("totals") or {})),
            campaigns=[MetaCampaignPerformance(**c) for c in payload.get("campaigns") or []],
            warehouse=payload.get("warehouse"),
        )
    try:
        payload = meta_service.account_performance(account_id, date_range=preset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "meta.performance",
            cache_payload,
            response_json=payload,
            row_count=len(payload.get("campaigns") or []),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return MetaPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=MetaPerformanceTotals(**payload["totals"]),
        campaigns=[MetaCampaignPerformance(**c) for c in payload["campaigns"]],
        warehouse=payload.get("warehouse"),
    )


@app.get(
    "/meta/adsets/performance",
    response_model=MetaAdSetsPerformanceResponse,
    dependencies=[Depends(require_api_key)],
    summary="Meta Ads performance by ad set",
)
def meta_adsets_performance(
    account_id: str,
    date_range: str = "LAST_30_DAYS",
    campaign_id: str | None = None,
) -> MetaAdSetsPerformanceResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    preset = date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(WAREHOUSE_DATE_RANGES))}",
        )
    campaign_id = (campaign_id or "").strip() or None

    cache_payload = {"account_id": account_id, "date_range": preset, "campaign_id": campaign_id}
    hit = db_cache.get_cached("meta.adsets.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return MetaAdSetsPerformanceResponse(
            account_id=payload.get("account_id", account_id),
            entity_level=payload.get("entity_level", "account"),
            date_range=payload.get("date_range", {}),
            totals=MetaAdSetsPerformanceTotals(**(payload.get("totals") or {})),
            adsets=[MetaAdSetPerformance(**a) for a in payload.get("adsets") or []],
        )
    try:
        payload = meta_service.adsets_performance(
            account_id, date_range=preset, campaign_id=campaign_id
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "meta.adsets.performance",
            cache_payload,
            response_json=payload,
            row_count=len(payload.get("adsets") or []),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return MetaAdSetsPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=MetaAdSetsPerformanceTotals(**payload["totals"]),
        adsets=[MetaAdSetPerformance(**a) for a in payload["adsets"]],
    )


@app.get(
    "/meta/videos",
    response_model=MetaVideosResponse,
    dependencies=[Depends(require_api_key)],
    summary="Meta ad video/image URLs with thumbnails",
)
def meta_videos(
    account_id: str,
    campaign_id: str | None = None,
    videos_only: bool = True,
) -> MetaVideosResponse:
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Missing account_id query parameter.")
    campaign_id = (campaign_id or "").strip() or None

    cache_payload = {
        "account_id": account_id,
        "campaign_id": campaign_id,
        "videos_only": videos_only,
    }
    hit = db_cache.get_cached("meta.videos", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return MetaVideosResponse(
            account_id=payload.get("account_id", account_id),
            row_count=int(payload.get("row_count") or 0),
            videos=[MetaVideoItem(**v) for v in payload.get("videos") or []],
        )
    try:
        payload = meta_service.list_videos(
            account_id, campaign_id=campaign_id, videos_only=videos_only
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        db_cache.put_cached(
            "meta.videos",
            cache_payload,
            response_json=payload,
            row_count=len(payload.get("videos") or []),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return MetaVideosResponse(
        account_id=payload["account_id"],
        row_count=payload["row_count"],
        videos=[MetaVideoItem(**v) for v in payload["videos"]],
    )


@app.post(
    "/meta/warehouse/sync",
    response_model=MetaWarehouseSyncResponse,
    dependencies=[Depends(require_api_key)],
    summary="Sync Meta daily metrics into Postgres warehouse",
)
def meta_warehouse_sync(body: MetaWarehouseSyncRequest) -> MetaWarehouseSyncResponse:
    preset = body.date_range.strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(status_code=400, detail=f"Invalid date_range: {body.date_range}")
    try:
        result = meta_service.sync_account_to_warehouse(body.account_id, date_range=preset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return MetaWarehouseSyncResponse(**result)


@app.get(
    "/warehouse/status",
    response_model=WarehouseStatusResponse,
    dependencies=[Depends(require_api_key)],
)
def warehouse_status() -> WarehouseStatusResponse:
    return WarehouseStatusResponse(**warehouse.status())


@app.get(
    "/warehouse/metrics",
    response_model=WarehouseMetricsResponse,
    dependencies=[Depends(require_api_key)],
    summary="Read stored daily metrics from Postgres",
)
def warehouse_metrics(
    from_date: str,
    to_date: str,
    source: str | None = None,
    account_id: str | None = None,
    limit: int = 5000,
) -> WarehouseMetricsResponse:
    try:
        start = date.fromisoformat(from_date.strip()[:10])
        end = date.fromisoformat(to_date.strip()[:10])
    except ValueError as e:
        raise HTTPException(status_code=400, detail="from_date and to_date must be YYYY-MM-DD") from e
    if end < start:
        raise HTTPException(status_code=400, detail="to_date must be on or after from_date")
    rows = warehouse.query_metrics(
        source=source,
        account_id=account_id,
        from_date=start,
        to_date=end,
        limit=limit,
    )
    return WarehouseMetricsResponse(count=len(rows), rows=rows)


@app.get("/login", include_in_schema=False, response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None, error: str | None = None) -> HTMLResponse:
    if not web_users.enabled():
        raise HTTPException(
            status_code=503,
            detail="User login requires DATABASE_URL (Postgres).",
        )
    existing = web_auth.get_current_user(request)
    if existing:
        return RedirectResponse(url=_post_login_target(existing, next), status_code=303)
    target = oauth_flows.validate_return_to(next)
    ctx = audit_log.request_context(request)
    rl = login_rate_limit.check_login_allowed(ip=ctx.get("ip_address"))
    if not rl.allowed:
        return HTMLResponse(
            web_auth.render_login_page(error=rl.message or error, next_path=target),
            status_code=429,
        )
    return HTMLResponse(web_auth.render_login_page(error=error, next_path=target))


@app.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
):
    if not web_users.enabled():
        raise HTTPException(status_code=503, detail="User login requires Postgres.")
    ctx = audit_log.request_context(request)
    rl = login_rate_limit.check_login_allowed(ip=ctx.get("ip_address"), email=email)
    if not rl.allowed:
        return HTMLResponse(
            web_auth.render_login_page(error=rl.message, next_path=next),
            status_code=429,
        )
    user = web_users.authenticate(email, password)
    if not user:
        login_rate_limit.record_login_failure(ip=ctx.get("ip_address"), email=email)
        audit_log.record(
            action="login.failed",
            actor_email=email,
            subject_email=email,
            detail={"reason": "invalid credentials"},
            **ctx,
        )
        return HTMLResponse(
            web_auth.render_login_page(error="Invalid email or password.", next_path=next),
            status_code=401,
        )
    login_rate_limit.clear_login_limits(ip=ctx.get("ip_address"), email=email)
    web_auth.login_user(request, user)
    web_users.record_login(user.id)
    audit_log.record(
        action="login.success",
        actor_user_id=user.id,
        actor_email=user.email,
        detail={"role": user.role, "client_slug": user.client_slug},
        **ctx,
    )
    target = _post_login_target(user, next)
    return RedirectResponse(url=target, status_code=303)


@app.post("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    # Attribute the logout to the real account, not any user being viewed-as.
    user = web_auth.get_real_user(request)
    ctx = audit_log.request_context(request)
    if user:
        audit_log.record(
            action="logout",
            actor_user_id=user.id,
            actor_email=user.email,
            **ctx,
        )
    web_auth.logout_user(request)
    return RedirectResponse(url="/login", status_code=303)


@app.post("/admin/view-as", include_in_schema=False)
def admin_view_as_start(
    request: Request,
    user_id: int = Form(...),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
) -> RedirectResponse:
    """Admin: begin viewing the platform as another registered user."""
    target = web_users.get_user_record(int(user_id))
    ctx = audit_log.request_context(request)
    if not target or not target.is_active:
        return RedirectResponse(url="/dashboards", status_code=303)
    if target.id == admin.id:
        # Viewing as yourself is a no-op; just clear any prior impersonation.
        web_auth.clear_view_as(request)
        return RedirectResponse(url="/dashboards", status_code=303)
    web_auth.set_view_as(request, target.id)
    audit_log.record(
        action="admin.view_as.start",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=target.email,
        detail={"target_role": target.role, "target_slug": target.client_slug},
        **ctx,
    )
    # Land where the target user lands after login (their dashboards picker /
    # single dashboard) so the admin sees exactly that user's entry point.
    return RedirectResponse(url="/dashboards", status_code=303)


@app.post("/admin/view-as/exit", include_in_schema=False)
def admin_view_as_exit(request: Request) -> RedirectResponse:
    """Leave "view as" and return to the real admin account."""
    real = web_auth.get_real_user(request)
    target = web_auth.current_view_as(request)
    web_auth.clear_view_as(request)
    if real and target:
        audit_log.record(
            action="admin.view_as.stop",
            actor_user_id=real.id,
            actor_email=real.email,
            subject_email=target.email,
            **audit_log.request_context(request),
        )
    return RedirectResponse(url="/dashboards", status_code=303)


def _gcp_credentials_section_html() -> str:
    # One shared service account (marketing-data-reader@sagefrog.iam.gserviceaccount.com)
    # is granted BigQuery access on every client's GCP project via IAM, so there is
    # exactly one credential to manage. No per-client override — if a client ever
    # genuinely needs an isolated service account, set that env var directly in
    # Railway and pass credentials_env explicitly wherever that client's data is read.
    summary = railway_api.env_summary()
    if summary["ready"]:
        status = (
            '<p class="muted">Railway API connected. Uploading a key sets the '
            "variable and redeploys this service.</p>"
        )
        disabled = ""
    else:
        missing = [
            env
            for env, present in (
                ("RAILWAY_API_TOKEN", summary["has_token"]),
                ("RAILWAY_PROJECT_ID", summary["has_project_id"]),
                ("RAILWAY_ENVIRONMENT_ID", summary["has_environment_id"]),
                ("RAILWAY_SERVICE_ID", summary["has_service_id"]),
            )
            if not present
        ]
        status = (
            '<div class="notice err">Railway API not configured. Set these Railway '
            f'variables on this service, then reload: <span class="mono">{", ".join(missing)}</span></div>'
        )
        disabled = " disabled"
    return f"""
    <section>
      <h2>GCP service account credentials</h2>
      <p class="muted">Upload the shared agency service account's JSON key
      (<span class="mono">{ga4_credentials.GLOBAL_GCP_CREDENTIALS_ENV}</span>). It's validated,
      base64-encoded, and written to Railway. Railway then redeploys this service so the
      credential goes live (~1–2 min).</p>
      {status}
      <form method="post" action="/admin/gcp-credentials" enctype="multipart/form-data"
        onsubmit="return confirm('Set {ga4_credentials.GLOBAL_GCP_CREDENTIALS_ENV} on Railway? The service will redeploy.');">
        <label for="cred_file">Service account JSON</label>
        <input id="cred_file" name="credentials_file" type="file"
          accept="application/json,.json" required>
        <button type="submit" class="primary"{disabled}>Upload &amp; set credential</button>
      </form>
    </section>"""


def _post_login_target(user: web_users.WebUser, next_value: str | None) -> str:
    """Where to send a user after login. Non-admins can't use /admin (the default
    `next`), so route them to the dashboards picker instead of a 403 dead-end."""
    target = oauth_flows.validate_return_to(next_value)
    if user.role != "admin" and target == "/admin":
        return "/dashboards"
    return target


@app.get("/dashboards", include_in_schema=False, response_class=HTMLResponse)
def dashboards_home(request: Request):
    """Landing page listing the client dashboards the signed-in user can open."""
    if not web_users.enabled():
        raise HTTPException(
            status_code=503, detail="User login requires DATABASE_URL (Postgres)."
        )
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/dashboards")
    import dashboard_registry

    items = [
        (row.client_slug, row.label or row.client_slug)
        for row in dashboard_registry.list_clients()
        if user.can_access_client(row.client_slug)
    ]
    # A client user tied to a single dashboard: skip the picker, go straight in.
    if user.role == "client" and len(items) == 1:
        return RedirectResponse(url=f"/dashboard/{items[0][0]}", status_code=303)
    return HTMLResponse(web_auth.render_dashboards_page(user=user, dashboards=items))


def _render_admin_page(
    request: Request,
    *,
    page: str,
    msg: str | None = None,
    err: str | None = None,
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
    oauth_disconnected: str | None = None,
):
    """Shared renderer for the split-out admin destinations (Clients, Users,
    Feature requests, Advanced settings). Each lives at its own /admin/* route
    but shares auth, data loading, and the navy-sidebar shell."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path=f"/admin/{page}")
    if user.role != "admin":
        # Logged in but not an admin: 403, never bounce back to /login. The
        # login page redirects an already-authenticated user straight to `next`,
        # so redirecting here would ping-pong /admin <-> /login forever.
        raise HTTPException(status_code=403, detail="Admin access required.")
    users = web_users.list_users(include_inactive=False)
    events = audit_log.list_recent(limit=40)
    # OAuth + GCP credentials only surface on the Advanced page, so only build
    # those (and read their return URL) there.
    oauth_html = ""
    credentials_html = ""
    if page == "advanced":
        oauth_html = dashboard_settings.render_admin_oauth_section(
            return_url="/admin/advanced",
            oauth_connected=oauth_connected,
            oauth_error=(oauth_error or "").strip()[:300] or None,
        )
        credentials_html = _gcp_credentials_section_html()
    flash = msg
    if oauth_disconnected and not flash:
        labels = {"google_ads": "Google Ads", "linkedin": "LinkedIn", "meta": "Meta", "indeed": "Indeed", "harvest": "Harvest"}
        flash = f"{labels.get(oauth_disconnected, oauth_disconnected)} disconnected."

    return HTMLResponse(
        web_auth.render_admin_page(
            user=user,
            users=users,
            groups=web_users.list_groups(include_inactive=False),
            audit_events=events,
            message=flash,
            error=err,
            oauth_section_html=oauth_html,
            credentials_section_html=credentials_html,
            is_super_admin=web_auth.is_super_admin(user),
            dashboard_cache_ttl=_dashboard_cache_ttl_seconds(),
            page=page,
        )
    )


@app.get("/admin", include_in_schema=False)
def admin_home(request: Request) -> RedirectResponse:
    """The old catch-all Overview has been split into focused pages. Land on
    Clients so bookmarks to /admin keep working."""
    return RedirectResponse(url="/admin/clients", status_code=307)


@app.get("/admin/clients", include_in_schema=False, response_class=HTMLResponse)
def admin_clients(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    return _render_admin_page(request, page="clients", msg=msg, err=err)


@app.get("/admin/users", include_in_schema=False, response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    return _render_admin_page(request, page="users", msg=msg, err=err)


@app.get("/admin/feature-requests", include_in_schema=False, response_class=HTMLResponse)
def admin_feature_requests_page(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    return _render_admin_page(request, page="feature-requests", msg=msg, err=err)


@app.get("/admin/advanced", include_in_schema=False, response_class=HTMLResponse)
def admin_advanced_page(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
    oauth_disconnected: str | None = None,
):
    return _render_admin_page(
        request,
        page="advanced",
        msg=msg,
        err=err,
        oauth_connected=oauth_connected,
        oauth_error=oauth_error,
        oauth_disconnected=oauth_disconnected,
    )


def _dashboard_cache_ttl_seconds() -> int:
    try:
        import app_settings

        return app_settings.dashboard_cache_ttl_seconds()
    except Exception:
        return 0


@app.post("/admin/settings/dashboard-cache", include_in_schema=False)
def admin_set_dashboard_cache_ttl(
    request: Request,
    ttl_seconds: int = Form(...),
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Persist the dashboard read-cache TTL floor (super admins only)."""
    import app_settings

    try:
        app_settings.set_dashboard_cache_ttl_seconds(int(ttl_seconds), updated_by=admin.email)
    except Exception:
        return RedirectResponse(
            url="/admin/advanced?err=Could+not+update+cache+setting", status_code=303
        )
    return RedirectResponse(
        url="/admin/advanced?msg=Dashboard+cache+duration+updated", status_code=303
    )


@app.post("/admin/feature-requests/{request_id}/done", include_in_schema=False)
def admin_feature_request_done(
    request_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Mark a team feature request handled — clears it from the notification badge."""
    try:
        feature_requests.mark_done(request_id, resolved_by=admin.email)
    except Exception:
        return RedirectResponse(
            url="/admin/feature-requests?err=Could+not+update+feature+request",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/feature-requests?msg=Feature+request+marked+done", status_code=303
    )


@app.post("/admin/feature-requests/{request_id}/archive", include_in_schema=False)
def admin_feature_request_archive(
    request_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Archive a team feature request — dismisses it from the inbox, keeps the row."""
    try:
        feature_requests.archive_request(request_id, archived_by=admin.email)
    except Exception:
        return RedirectResponse(
            url="/admin/feature-requests?err=Could+not+archive+feature+request",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/feature-requests?msg=Feature+request+archived", status_code=303
    )


@app.post("/admin/feature-requests/{request_id}/delete", include_in_schema=False)
def admin_feature_request_delete(
    request_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Permanently delete a team feature request — can't be undone."""
    try:
        feature_requests.delete_request(request_id)
    except Exception:
        return RedirectResponse(
            url="/admin/feature-requests?err=Could+not+delete+feature+request",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/feature-requests?msg=Feature+request+deleted", status_code=303
    )


@app.get("/admin/hq", include_in_schema=False)
def admin_budget_hq(request: Request):
    """Legacy 'Budget HQ' — superseded by HQ (the DuckDB agency overview at
    /admin/agency-trends). Redirect so old links/bookmarks land on the new HQ."""
    return RedirectResponse(url="/admin/agency-trends", status_code=307)


@app.get("/admin/hq/data", include_in_schema=False)
def admin_budget_hq_data(
    user: web_users.WebUser = Depends(web_auth.require_admin),
) -> dict:
    """JSON feed for the Budget HQ page: MTD spend + pacing for every client."""
    from dashboard.services.hq_budget_service import build_hq_budget_overview

    return build_hq_budget_overview()


@app.get("/admin/agency-trends", include_in_schema=False, response_class=HTMLResponse)
def admin_agency_trends(request: Request):
    """Admin-only 'Agency Trends': every client's week-over-week paid-media
    momentum plus channel mix, computed in one DuckDB scan over metrics_daily."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/agency-trends")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.agency_trends_renderer import render_agency_trends_page

    return HTMLResponse(render_agency_trends_page(user_email=user.email))


@app.get("/admin/agency-trends/data", include_in_schema=False)
def admin_agency_trends_data(
    user: web_users.WebUser = Depends(web_auth.require_admin),
) -> dict:
    """JSON feed for the Agency Trends page: the DuckDB HQ reproduction (spend vs
    budget + sessions) plus cross-client week-over-week momentum."""
    from dashboard.services.agency_trends_service import build_agency_overview

    return build_agency_overview()


@app.get("/admin/client-hours", include_in_schema=False, response_class=HTMLResponse)
def admin_client_hours(request: Request):
    """Admin-only 'Client Hours': a Harvest burn-up chart (hours vs monthly goal)
    for every client this month. Data loads from …/data."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/client-hours")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.client_hours_renderer import render_client_hours_page

    return HTMLResponse(render_client_hours_page(user_email=user.email))


@app.get("/admin/client-hours/data", include_in_schema=False)
def admin_client_hours_data(
    refresh: int = 0,
    user: web_users.WebUser = Depends(web_auth.require_admin),
) -> dict:
    """JSON feed for the Client Hours page: current-month cumulative hours per
    client (cached; live from the Harvest API on a cache miss) plus each client's
    monthly goal. ``?refresh=1`` forces a fresh Harvest pull, bypassing the cache."""
    import harvest_service

    return harvest_service.build_client_hours_overview(use_cache=not bool(refresh))


@app.post("/admin/client-hours/goal", include_in_schema=False)
async def admin_client_hours_goal(
    request: Request,
    harvest_client_id: str = Form(...),
    goal: str = Form(""),
    client_name: str = Form(""),
):
    """Set one client's monthly hours goal. ``goal`` accepts a single number
    ("80"), a range ("80-100"), or an open-ended floor ("80+"); blank clears it."""
    user = await web_auth.require_admin(request)
    import harvest_service

    try:
        goal_min, goal_max = harvest_service.parse_goal_text(goal)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    try:
        harvest_service.set_goal(
            harvest_client_id=harvest_client_id,
            goal_min=goal_min,
            goal_max=goal_max,
            client_name=client_name,
            updated_by=user.email,
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="harvest.goal_set",
        actor_email=user.email,
        detail={"harvest_client_id": harvest_client_id, "goal_min": goal_min, "goal_max": goal_max},
        **audit_log.request_context(request),
    )
    return JSONResponse({
        "ok": True,
        "goal_min": goal_min,
        "goal_max": goal_max,
        "goal_label": harvest_service.format_goal(goal_min, goal_max),
    })


@app.post("/admin/client-hours/project-tag", include_in_schema=False)
async def admin_client_hours_project_tag(
    request: Request,
    harvest_project_id: str = Form(...),
    tag: str = Form(""),
    project_name: str = Form(""),
    client_name: str = Form(""),
):
    """Tag one Harvest project as 'retainer' or 'project' (blank clears the tag,
    so it counts only under the 'All' scope)."""
    user = await web_auth.require_admin(request)
    import harvest_service

    try:
        harvest_service.set_project_tag(
            harvest_project_id=harvest_project_id,
            tag=tag,
            project_name=project_name,
            client_name=client_name,
            updated_by=user.email,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="harvest.project_tagged",
        actor_email=user.email,
        detail={"harvest_project_id": harvest_project_id, "tag": (tag or "").strip().lower() or None},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "tag": (tag or "").strip().lower() or None})


@app.post("/admin/client-hours/owner", include_in_schema=False)
async def admin_client_hours_owner(
    request: Request,
    harvest_client_id: str = Form(...),
    owner: str = Form(""),
    client_name: str = Form(""),
):
    """Set one client's account owner (a team member's name), or clear it when
    ``owner`` is blank. The owner is a label/filter on the Client Hours page."""
    user = await web_auth.require_admin(request)
    import harvest_service

    try:
        stored = harvest_service.set_client_owner(
            harvest_client_id=harvest_client_id,
            owner=owner,
            client_name=client_name,
            updated_by=user.email,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="harvest.owner_set",
        actor_email=user.email,
        detail={"harvest_client_id": harvest_client_id, "owner": stored},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "owner": stored})


# ---------------------------------------------------------------------------
# Client Hours share links: read-only, no-login views of the burn-up page.
# Admins mint/list/revoke unguessable tokens (…/share*); the public routes
# (/share/client-hours/{token}) render the same page read-only and serve a
# cache-only data feed so a leaked link can't force live Harvest pulls.
# ---------------------------------------------------------------------------


@app.get("/admin/client-hours/shares", include_in_schema=False)
async def admin_client_hours_shares(request: Request) -> JSONResponse:
    """List the active read-only share links for the Client Hours page."""
    await web_auth.require_admin(request)
    import client_hours_share

    return JSONResponse({"ok": True, "links": client_hours_share.list_share_links()})


@app.post("/admin/client-hours/share", include_in_schema=False)
async def admin_client_hours_share_create(
    request: Request,
    label: str = Form(""),
) -> JSONResponse:
    """Mint a new read-only share link. Returns the token + its full URL."""
    user = await web_auth.require_admin(request)
    import client_hours_share

    try:
        link = client_hours_share.create_share_link(created_by=user.email, label=label)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="client_hours.share_created",
        actor_email=user.email,
        detail={"token": link["token"][:8] + "…", "label": link.get("label")},
        **audit_log.request_context(request),
    )
    link["url"] = f"{oauth_flows.public_base_url()}/share/client-hours/{link['token']}"
    return JSONResponse({"ok": True, "link": link})


@app.post("/admin/client-hours/share/revoke", include_in_schema=False)
async def admin_client_hours_share_revoke(
    request: Request,
    token: str = Form(...),
) -> JSONResponse:
    """Revoke a read-only share link so it can no longer be viewed."""
    user = await web_auth.require_admin(request)
    import client_hours_share

    try:
        revoked = client_hours_share.revoke_share_link(token=token, revoked_by=user.email)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    if revoked:
        audit_log.record(
            action="client_hours.share_revoked",
            actor_email=user.email,
            detail={"token": (token or "")[:8] + "…"},
            **audit_log.request_context(request),
        )
    return JSONResponse({"ok": True, "revoked": revoked})


@app.get("/share/client-hours/{token}", include_in_schema=False, response_class=HTMLResponse)
def shared_client_hours(token: str):
    """Public, read-only Client Hours view for a valid share token — no login.
    An invalid or revoked token renders a plain 404 page."""
    import client_hours_share
    from dashboard.renderers.client_hours_renderer import (
        render_shared_client_hours_page,
    )

    link = client_hours_share.resolve_share_token(token)
    if not link:
        return HTMLResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Link unavailable</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:520px;margin:80px auto;"
            "padding:0 20px;color:#0f1c2e'>"
            "<h1 style='font-size:1.3rem'>This link isn’t available</h1>"
            "<p style='color:#5a6578'>The share link is invalid or has been revoked. "
            "Ask the person who shared it for a new one.</p></body></html>",
            status_code=404,
        )
    return HTMLResponse(
        render_shared_client_hours_page(token=link["token"], label=link.get("label"))
    )


@app.get("/share/client-hours/{token}/data", include_in_schema=False)
def shared_client_hours_data(token: str) -> JSONResponse:
    """Public JSON feed for a shared Client Hours view. Cache-only — never forces
    a live Harvest pull — so a leaked link can't be used to hammer the API."""
    import client_hours_share

    link = client_hours_share.resolve_share_token(token, record_view=False)
    if not link:
        return JSONResponse({"error": "This link is invalid or has been revoked."}, status_code=404)
    import harvest_service

    return JSONResponse(harvest_service.build_client_hours_overview(use_cache=True))


@app.get("/admin/docs", include_in_schema=False, response_class=HTMLResponse)
def admin_docs(request: Request):
    """Admin-only 'Docs': how to set up a new client dashboard, in the portal."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/docs")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.docs_renderer import render_docs_page

    return HTMLResponse(render_docs_page(user_email=user.email))


@app.post("/admin/gcp-credentials", include_in_schema=False)
async def admin_set_gcp_credentials(
    request: Request,
    credentials_file: UploadFile = File(...),
):
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin")
    if user.role != "admin":
        # Logged in but not an admin: 403, never bounce back to /login. The
        # login page redirects an already-authenticated user straight to `next`,
        # so redirecting here would ping-pong /admin <-> /login forever.
        raise HTTPException(status_code=403, detail="Admin access required.")

    name = ga4_credentials.GLOBAL_GCP_CREDENTIALS_ENV
    if not railway_api.enabled():
        return RedirectResponse(
            url="/admin/advanced?err=" + quote(
                "Railway API is not configured "
                "(set RAILWAY_API_TOKEN / RAILWAY_PROJECT_ID / RAILWAY_ENVIRONMENT_ID / RAILWAY_SERVICE_ID)."
            ),
            status_code=303,
        )
    try:
        raw = (await credentials_file.read()).decode("utf-8")
        encoded, client_email = ga4_credentials.validate_and_encode_service_account(raw)
        railway_api.set_variable(name, encoded)
    except Exception as exc:
        return RedirectResponse(
            url="/admin/advanced?err=" + quote(f"Upload failed: {str(exc)[:200]}"),
            status_code=303,
        )

    audit_log.record(
        action="admin.gcp_credentials_set",
        actor_email=user.email,
        detail={"env_var": name, "service_account": client_email},
        **audit_log.request_context(request),
    )
    return RedirectResponse(
        url="/admin/advanced?msg=" + quote(
            f"Set {name} for {client_email}. Railway is redeploying — live in ~1–2 min."
        ),
        status_code=303,
    )


def _parse_group_id(raw: str | None) -> int | None:
    """Coerce a form-submitted group id to int, treating blank/'none' as None.

    The Create-user and role dropdowns submit an empty value for the "Ungrouped"
    option, so an unparseable value simply means no group."""
    val = (raw or "").strip()
    if not val or val.lower() in ("none", "0", "-"):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


@app.post("/admin/groups", include_in_schema=False)
def admin_create_group(
    request: Request,
    name: str = Form(...),
    client_slugs: list[str] = Form(default=[]),
    description: str | None = Form(None),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        created = web_users.create_group(
            name=name, client_slugs=client_slugs, description=description
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    audit_log.record(
        action="group.created",
        actor_user_id=admin.id,
        actor_email=admin.email,
        detail={"name": created["name"], "client_slugs": created["client_slugs"]},
        **ctx,
    )
    return RedirectResponse(url="/admin/users?msg=Group+created", status_code=303)


@app.post("/admin/groups/{group_id}", include_in_schema=False)
def admin_update_group(
    group_id: int,
    request: Request,
    name: str = Form(...),
    client_slugs: list[str] = Form(default=[]),
    description: str | None = Form(None),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        updated = web_users.update_group(
            group_id, name=name, client_slugs=client_slugs, description=description
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    if not updated:
        return RedirectResponse(url="/admin/users?err=Group+not+found", status_code=303)
    audit_log.record(
        action="group.updated",
        actor_user_id=admin.id,
        actor_email=admin.email,
        detail={"name": updated["name"], "client_slugs": updated["client_slugs"]},
        **ctx,
    )
    return RedirectResponse(url="/admin/users?msg=Group+updated", status_code=303)


@app.post("/admin/groups/{group_id}/delete", include_in_schema=False)
def admin_delete_group(
    group_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    target = web_users.get_group(group_id)
    if not target:
        return RedirectResponse(url="/admin/users?err=Group+not+found", status_code=303)
    if not web_users.delete_group(group_id):
        return RedirectResponse(
            url="/admin/users?err=Remove+its+members+before+deleting+the+group",
            status_code=303,
        )
    audit_log.record(
        action="group.deleted",
        actor_user_id=admin.id,
        actor_email=admin.email,
        detail={"name": target["name"]},
        **ctx,
    )
    return RedirectResponse(url="/admin/users?msg=Group+deleted", status_code=303)


@app.post("/admin/users", include_in_schema=False)
def admin_create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("client"),
    client_slug: str | None = Form(None),
    allowed_client_slugs: list[str] = Form(default=[]),
    group_id: str | None = Form(None),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        created = web_users.create_user(
            email=email,
            password=password,
            role=role,
            client_slug=client_slug,
            allowed_client_slugs=allowed_client_slugs,
            group_id=_parse_group_id(group_id),
        )
    except ValueError as e:
        users = web_users.list_users(include_inactive=False)
        events = audit_log.list_recent(limit=150)
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                error=str(e),
                page="users",
            ),
            status_code=400,
        )
    audit_log.record(
        action="user.created",
        actor_user_id=user.id,
        actor_email=user.email,
        subject_email=created.email,
        detail={
            "role": created.role,
            "client_slug": created.client_slug,
            "allowed_client_slugs": list(created.allowed_client_slugs),
            "group_id": created.group_id,
        },
        **ctx,
    )
    return RedirectResponse(url="/admin/users?msg=User+created", status_code=303)


@app.post("/admin/users/{user_id}/deactivate", include_in_schema=False)
def admin_deactivate_user(
    user_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    if user_id == admin.id:
        return RedirectResponse(url="/admin/users?err=Cannot+deactivate+your+own+account", status_code=303)
    target = web_users.get_user_record(user_id)
    if target and target.role == "admin" and web_users.count_admins() <= 1:
        return RedirectResponse(url="/admin/users?err=Cannot+deactivate+the+only+admin", status_code=303)
    if target and web_users.deactivate_user(user_id):
        audit_log.record(
            action="user.deactivated",
            actor_user_id=admin.id,
            actor_email=admin.email,
            subject_email=target.email,
            detail={"role": target.role, "client_slug": target.client_slug},
            **ctx,
        )
    return RedirectResponse(url="/admin/users?msg=User+deactivated", status_code=303)


@app.post("/admin/users/{user_id}/reset-password", include_in_schema=False)
def admin_reset_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    target = web_users.get_user_record(user_id)
    if not target:
        return RedirectResponse(url="/admin/users?err=User+not+found", status_code=303)
    try:
        ok = web_users.set_password(user_id, new_password)
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    if ok:
        audit_log.record(
            action="user.password_reset",
            actor_user_id=admin.id,
            actor_email=admin.email,
            subject_email=target.email,
            **ctx,
        )
        return RedirectResponse(
            url=f"/admin/users?msg=Password+reset+for+{quote(target.email)}", status_code=303
        )
    return RedirectResponse(url="/admin/users?err=Password+reset+failed", status_code=303)


# Cap the stored data URI. Avatars are resized client-side to ~160px, so a real
# headshot lands well under this; the cap just stops an oversized paste from
# bloating the row.
_AVATAR_MAX_CHARS = 400_000


@app.post("/admin/users/{user_id}/avatar", include_in_schema=False)
def admin_set_user_avatar(
    user_id: int,
    request: Request,
    avatar: str = Form(""),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
) -> JSONResponse:
    """Set (or clear) a user's avatar. Expects a resized ``data:image/...`` URI."""
    target = web_users.get_user_record(user_id)
    if not target:
        return JSONResponse({"ok": False, "error": "User not found."}, status_code=404)
    value = (avatar or "").strip()
    if value:
        if not value.startswith("data:image/"):
            return JSONResponse(
                {"ok": False, "error": "Avatar must be an image."}, status_code=400
            )
        if len(value) > _AVATAR_MAX_CHARS:
            return JSONResponse(
                {"ok": False, "error": "Image is too large — try a smaller crop."},
                status_code=413,
            )
    stored = value or None
    if not web_users.set_avatar(user_id, stored):
        return JSONResponse({"ok": False, "error": "Save failed."}, status_code=400)
    audit_log.record(
        action="user.avatar_updated",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=target.email,
        detail={"cleared": stored is None},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "avatar": stored})


@app.post("/admin/users/{user_id}/role", include_in_schema=False)
def admin_set_user_role(
    user_id: int,
    request: Request,
    role: str = Form(...),
    client_slug: str | None = Form(None),
    allowed_client_slugs: list[str] = Form(default=[]),
    group_id: str | None = Form(None),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    if user_id == admin.id:
        return RedirectResponse(url="/admin/users?err=Cannot+change+your+own+role", status_code=303)
    target = web_users.get_user_record(user_id)
    if not target:
        return RedirectResponse(url="/admin/users?err=User+not+found", status_code=303)
    new_role = (role or "").strip().lower()
    # Never let the last remaining admin be demoted out of the admin role.
    if target.role == "admin" and new_role != "admin" and web_users.count_admins() <= 1:
        return RedirectResponse(url="/admin/users?err=Cannot+change+the+only+admin%27s+role", status_code=303)
    try:
        updated = web_users.set_role(
            user_id, new_role, client_slug, allowed_client_slugs, _parse_group_id(group_id)
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    if not updated:
        return RedirectResponse(url="/admin/users?err=Role+update+failed", status_code=303)
    audit_log.record(
        action="user.role_changed",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=updated.email,
        detail={
            "role": updated.role,
            "client_slug": updated.client_slug,
            "allowed_client_slugs": list(updated.allowed_client_slugs),
            "group_id": updated.group_id,
        },
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin/users?msg=Role+updated+for+{quote(updated.email)}", status_code=303
    )


@app.post("/admin/dashboards", include_in_schema=False)
def admin_create_dashboard(
    request: Request,
    client_slug: str = Form(...),
    label: str = Form(...),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        created = dashboard_registry.create_client(
            client_slug=client_slug,
            label=label,
            created_by=user.email,
        )
    except ValueError as exc:
        users = web_users.list_users(include_inactive=False)
        events = audit_log.list_recent(limit=150)
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                error=str(exc),
                page="clients",
            ),
            status_code=400,
        )
    audit_log.record(
        action="dashboard.created",
        actor_email=user.email,
        detail={"client_slug": created.client_slug, "label": created.label},
        **ctx,
    )
    # NOTE: GSC table provisioning intentionally does NOT happen here. At
    # creation time the client has no BQ registry entry yet, so routing would
    # fall back to the Penn default project and create the tables in the wrong
    # place. Provisioning runs in admin_save_gsc_config(), once the client's
    # BigQuery destination is known.
    return RedirectResponse(
        url=f"/admin/clients?msg=Dashboard+{quote(created.label)}+created",
        status_code=303,
    )


@app.post("/admin/dashboards/{client_slug}/mode", include_in_schema=False)
def admin_convert_dashboard_mode(
    client_slug: str,
    request: Request,
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    """Convert a legacy dashboard to the connector-driven Nixon template.

    New dashboards get dashboard_mode='bigquery_nixon' at creation; this is the
    one-click equivalent for dashboards created before that default (avoids a
    manual DB update). Preserves all other config fields.
    """
    slug = (client_slug or "").strip().lower()
    ctx = audit_log.request_context(request)
    try:
        import client_dashboard_config as cdc
        existing = cdc.get_config(slug)
        cdc.save_config(
            slug,
            label=(existing.label if existing else slug),
            google_customer_id=existing.google_customer_id if existing else None,
            linkedin_account_id=existing.linkedin_account_id if existing else None,
            meta_account_id=existing.meta_account_id if existing else None,
            ga4_client_key=existing.ga4_client_key if existing else None,
            updated_by=user.email,
            dashboard_mode="bigquery_nixon",
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/clients?msg=Convert+failed:+{quote(str(exc)[:120])}", status_code=303
        )
    audit_log.record(
        action="dashboard.mode_changed",
        actor_email=user.email,
        detail={"client_slug": slug, "dashboard_mode": "bigquery_nixon"},
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin/clients?msg=Dashboard+{quote(slug)}+now+uses+the+new+template", status_code=303
    )


@app.post("/admin/dashboards/{client_slug}/logo", include_in_schema=False)
def admin_set_dashboard_logo(
    client_slug: str,
    request: Request,
    logo: str = Form(""),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
) -> JSONResponse:
    """Set (or clear) a dashboard's logo. Expects a resized ``data:image/...`` URI."""
    value = (logo or "").strip()
    if value:
        if not value.startswith("data:image/"):
            return JSONResponse({"ok": False, "error": "Logo must be an image."}, status_code=400)
        if len(value) > _AVATAR_MAX_CHARS:
            return JSONResponse(
                {"ok": False, "error": "Image is too large — try a smaller crop."},
                status_code=413,
            )
    stored = value or None
    if not dashboard_registry.set_logo(client_slug, stored):
        return JSONResponse({"ok": False, "error": "Dashboard not found."}, status_code=404)
    audit_log.record(
        action="dashboard.logo_updated",
        actor_email=admin.email,
        detail={"client_slug": client_slug, "cleared": stored is None},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "logo": stored})


@app.post("/admin/dashboards/{client_slug}/delete", include_in_schema=False)
def admin_delete_dashboard(
    client_slug: str,
    request: Request,
    confirm_label: str = Form(""),
    user: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    ctx = audit_log.request_context(request)
    try:
        deleted = dashboard_registry.delete_client(
            client_slug=client_slug,
            confirm_label=confirm_label,
            deleted_by=user.email,
        )
    except ValueError as exc:
        users = web_users.list_users(include_inactive=False)
        events = audit_log.list_recent(limit=150)
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                error=str(exc),
                page="clients",
            ),
            status_code=400,
        )
    audit_log.record(
        action="dashboard.deleted",
        actor_email=user.email,
        detail=deleted,
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin/clients?msg=Dashboard+{quote(deleted['label'])}+deleted",
        status_code=303,
    )


@app.post("/admin/snapshot/{client_slug}/delete", include_in_schema=False)
def admin_delete_snapshot(
    client_slug: str,
    request: Request,
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    import dashboard_snapshots
    slug = (client_slug or "").strip().lower()
    dashboard_snapshots.delete_snapshot(slug)
    audit_log.record(
        action="dashboard.snapshot.deleted",
        actor_email=user.email,
        detail={"client_slug": slug},
        **audit_log.request_context(request),
    )
    return RedirectResponse(
        url=f"/admin/clients?msg=Snapshot+cleared+for+{quote(slug)}",
        status_code=303,
    )


@app.get(
    "/oauth/{platform}/connect",
    summary="Start OAuth connect flow (admin)",
    include_in_schema=False,
)
async def oauth_connect(platform: str, request: Request, return_to: str = "/admin", client: str = ""):
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")
    user = await web_auth.require_admin(request)
    dest = oauth_flows.validate_return_to(return_to)
    prereq = oauth_flows.connect_prerequisites(slug)
    if not prereq.get("ready"):
        missing = ", ".join(prereq.get("missing") or [])
        raise HTTPException(
            status_code=503,
            detail=f"Set {missing} in Railway before connecting {slug}.",
        )
    if not oauth_store.enabled():
        raise HTTPException(status_code=503, detail="DATABASE_URL is required to store OAuth tokens.")
    state = oauth_flows.make_state()
    oauth_flows.store_oauth_state(request, platform=slug, state=state, return_to=dest, client_slug=client.strip())
    try:
        url = oauth_flows.build_authorize_url(slug, state=state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=303)


@app.get(
    "/connect/{platform}/{client_slug}",
    summary="No-login connect link (signed) — start OAuth for one client",
    include_in_schema=False,
)
async def connect_link(platform: str, client_slug: str, request: Request, t: str = ""):
    """Public, signed-token connect link. Lets a specialist/client authorize one
    client's connector (e.g. their HubSpot portal) without a portal login."""
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")
    verified = oauth_flows.verify_connect_state(t)
    if not verified or verified[0] != client_slug.strip().lower() or verified[1] != slug:
        return HTMLResponse(
            "<div style='font-family:system-ui;max-width:520px;margin:80px auto;text-align:center'>"
            "<h2>This connect link is invalid or has expired.</h2>"
            "<p style='color:#555'>Ask your Sagefrog contact for a fresh link.</p></div>",
            status_code=400,
        )
    prereq = oauth_flows.connect_prerequisites(slug)
    if not prereq.get("ready"):
        missing = ", ".join(prereq.get("missing") or [])
        return HTMLResponse(
            f"<div style='font-family:system-ui;max-width:520px;margin:80px auto;text-align:center'>"
            f"<h2>{slug.title()} isn't configured yet.</h2><p style='color:#555'>Missing: {missing}</p></div>",
            status_code=503,
        )
    try:
        # Reuse the signed token as the OAuth state; the callback verifies it.
        url = oauth_flows.build_authorize_url(slug, state=t)
    except Exception as exc:
        return HTMLResponse(
            f"<div style='font-family:system-ui;max-width:520px;margin:80px auto;text-align:center'>"
            f"<h2>Couldn't start the connection.</h2><p style='color:#555'>{quote(str(exc)[:200])}</p></div>",
            status_code=400,
        )
    return RedirectResponse(url=url, status_code=303)


@app.get(
    "/oauth/{platform}/callback",
    summary="OAuth provider callback",
    include_in_schema=False,
)
async def oauth_callback(
    platform: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")

    # Signed connect-link flow (no session): the OAuth `state` is a signed token
    # carrying the client_slug, so a specialist/client can authorize without login.
    link_state = oauth_flows.verify_connect_state(state or "")
    if link_state and link_state[1] == slug:
        link_slug = link_state[0]
        dest = f"/dashboard/{link_slug}/connectors/{slug}"
        sep = "?"
        if error:
            msg = (error_description or error or "OAuth denied")[:200]
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(msg)}", status_code=303)
        if not code:
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote('Missing authorization code.')}", status_code=303)
        try:
            tokens = oauth_flows.exchange_code(slug, code=code.strip())
            verify_error = oauth_flows.verify_connected_account(slug, tokens, client_slug=link_slug)
            if verify_error:
                audit_log.record(
                    action="oauth.rejected",
                    actor_email="connect-link",
                    detail={"platform": slug, "client_slug": link_slug, "via": "connect_link", "reason": verify_error},
                    **audit_log.request_context(request),
                )
                return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(verify_error[:200])}", status_code=303)
            oauth_store.save_tokens(
                slug,
                refresh_token=tokens.get("refresh_token"),
                access_token=tokens.get("access_token"),
                token_expires_at=tokens.get("token_expires_at"),
                scopes=tokens.get("scopes"),
                metadata=tokens.get("metadata"),
                connected_by="connect-link",
                client_slug=link_slug,
            )
            audit_log.record(
                action="oauth.connected",
                actor_email="connect-link",
                detail={"platform": slug, "client_slug": link_slug, "via": "connect_link"},
                **audit_log.request_context(request),
            )
        except Exception as exc:
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(str(exc)[:200])}", status_code=303)
        return RedirectResponse(url=f"{dest}{sep}oauth_connected={quote(slug)}", status_code=303)

    expected_state, return_to, oauth_client_slug = oauth_flows.pop_oauth_state(request, platform=slug)
    dest = oauth_flows.validate_return_to(return_to)
    sep = "&" if "?" in dest else "?"
    if error:
        msg = (error_description or error or "OAuth denied")[:200]
        return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(msg)}", status_code=303)
    if not code or not state or not expected_state or state != expected_state:
        return RedirectResponse(
            url=f"{dest}{sep}oauth_error={quote('Invalid OAuth state. Try connecting again.')}",
            status_code=303,
        )
    user = web_auth.get_current_user(request)
    actor = user.email if user else None
    try:
        tokens = oauth_flows.exchange_code(slug, code=code.strip())
        verify_error = oauth_flows.verify_connected_account(slug, tokens, client_slug=oauth_client_slug)
        if verify_error:
            audit_log.record(
                action="oauth.rejected",
                actor_email=actor,
                detail={"platform": slug, "client_slug": oauth_client_slug or "global", "reason": verify_error},
                **audit_log.request_context(request),
            )
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(verify_error[:200])}", status_code=303)
        oauth_store.save_tokens(
            slug,
            refresh_token=tokens.get("refresh_token"),
            access_token=tokens.get("access_token"),
            token_expires_at=tokens.get("token_expires_at"),
            scopes=tokens.get("scopes"),
            metadata=tokens.get("metadata"),
            connected_by=actor,
            client_slug=oauth_client_slug,
        )
        audit_log.record(
            action="oauth.connected",
            actor_email=actor,
            detail={"platform": slug, "client_slug": oauth_client_slug or "global"},
            **audit_log.request_context(request),
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"{dest}{sep}oauth_error={quote(str(exc)[:200])}",
            status_code=303,
        )
    return RedirectResponse(url=f"{dest}{sep}oauth_connected={quote(slug)}", status_code=303)


def _register_microsoft_ads_callback() -> None:
    """Mount the Microsoft Ads OAuth callback at the exact path Google redirects to.

    Any path shaped ``/oauth/<slug>/callback`` is already served by the generic
    ``/oauth/{platform}/callback`` route (which normalizes hyphens to
    underscores, so ``/oauth/microsoft-ads/callback`` resolves to the
    ``microsoft_ads`` platform). Only a genuinely custom redirect path needs an
    explicit alias — and it must be inserted AHEAD of the parameterized routes,
    since Starlette matches in registration order and the generic route would
    otherwise win and 404 on an unknown platform segment.
    """
    try:
        path = oauth_flows.microsoft_ads_callback_path()
    except Exception:
        return
    if not path:
        return
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "oauth" and parts[2] == "callback":
        return  # /oauth/<slug>/callback — handled by the generic route

    async def _ms_ads_callback_alias(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ):
        return await oauth_callback(
            "microsoft_ads",
            request,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        )

    app.add_api_route(path, _ms_ads_callback_alias, methods=["GET"], include_in_schema=False)
    # Move the freshly-appended route to the front so it precedes the generic
    # /oauth/{platform}/callback route in match order.
    app.router.routes.insert(0, app.router.routes.pop())


_register_microsoft_ads_callback()


@app.post(
    "/oauth/{platform}/disconnect",
    summary="Remove stored OAuth token (admin)",
    include_in_schema=False,
)
async def oauth_disconnect(
    platform: str,
    request: Request,
    return_to: str = Form("/admin"),
):
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")
    user = await web_auth.require_admin(request)
    oauth_store.delete_platform(slug)
    audit_log.record(
        action="oauth.disconnected",
        actor_email=user.email,
        detail={"platform": slug},
        **audit_log.request_context(request),
    )
    dest = oauth_flows.validate_return_to(return_to)
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(url=f"{dest}{sep}oauth_disconnected={quote(slug)}", status_code=303)


@app.get(
    "/ga4/env",
    response_model=Ga4EnvSummary,
    dependencies=[Depends(require_api_key)],
)
def ga4_env() -> Ga4EnvSummary:
    summ = bigquery_service.env_summary()
    try:
        from ga4_clients import load_client_registry

        summ["has_ga4_clients_registry"] = bool(load_client_registry())
    except Exception:
        summ["has_ga4_clients_registry"] = False
    return Ga4EnvSummary(**summ)


@app.get(
    "/ga4/clients",
    response_model=Ga4ClientsResponse,
    dependencies=[Depends(require_api_key)],
    summary="List configured GA4 BigQuery clients (multi-project)",
)
def ga4_clients() -> Ga4ClientsResponse:
    clients = ga4_warehouse_service.list_configured_clients()
    summ = bigquery_service.env_summary()
    return Ga4ClientsResponse(
        count=len(clients),
        clients=[Ga4ClientRef(**c) for c in clients],
        default_bq_project_id=summ.get("bq_project_id"),
        default_bq_dataset_id=summ.get("bq_dataset_id"),
    )


@app.post(
    "/ga4/query",
    response_model=Ga4QueryResponse,
    dependencies=[Depends(require_api_key)],
)
def ga4_query(body: Ga4QueryRequest) -> Ga4QueryResponse:
    try:
        bigquery_service.assert_read_only_select(body.sql)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    cache_payload = {"sql": body.sql, "max_rows": body.max_rows}
    hit = db_cache.get_cached("ga4.query", cache_payload)
    if hit is not None:
        rows = hit.response_json or []
        return Ga4QueryResponse(row_count=int(hit.row_count or len(rows)), rows=rows)
    try:
        rows = bigquery_service.run_query(sql=body.sql, max_rows=body.max_rows)
    except Exception as e:
        msg = str(e)
        if "GCP_SERVICE_ACCOUNT_JSON" in msg:
            summ = bigquery_service.env_summary()
            raise HTTPException(
                status_code=400,
                detail={
                    "message": msg,
                    "hint": (
                        "Redeploy the latest railway/app so base64 + diagnostics work. "
                        "Set GCP_SERVICE_ACCOUNT_JSON to one-line base64 from PowerShell "
                        "([Convert]::ToBase64String([IO.File]::ReadAllBytes(\"FULL_PATH_TO_KEY.json\")) | Set-Clipboard). "
                        "Call GET /ga4/env and confirm gcp_service_account_json_parse_ok is true."
                    ),
                    "gcp_service_account_diagnostics": {
                        "char_count": summ.get("gcp_service_account_json_char_count"),
                        "hint": summ.get("gcp_service_account_json_hint"),
                        "parse_ok": summ.get("gcp_service_account_json_parse_ok"),
                        "suspected_truncated": summ.get("gcp_service_account_json_suspected_truncated"),
                        "parse_error": summ.get("gcp_service_account_json_parse_error"),
                    },
                },
            ) from e
        raise HTTPException(status_code=400, detail=msg) from e
    try:
        db_cache.put_cached(
            "ga4.query",
            cache_payload,
            response_json=rows,
            row_count=len(rows),
            status="ok",
            error=None,
        )
    except Exception:
        pass
    return Ga4QueryResponse(row_count=len(rows), rows=rows)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
