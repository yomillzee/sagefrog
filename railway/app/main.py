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
from openapi_gpt import build_chatgpt_openapi
from cron_security import require_cron_secret
from security import configured_api_key, is_production, require_api_key
import audit_log
import client_config
import client_dashboard_config
import dashboard_registry
import business_line_rules
import admin_dev_notes
import client_insight_documents
import dashboard_settings
import dashboard_features
import dashboard_theme
import login_rate_limit
import not_found_page
import oauth_flows
import oauth_store
import web_auth
import web_users
from models import (
    AccountsResponse,
    AccountRef,
    CacheHealthResponse,
    SummaryAllRequest,
    SummaryAllResponse,
    GoogleAdsEnvSummary,
    HealthResponse,
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
    business_line_rules.ensure_schema()
    admin_dev_notes.ensure_schema()
    client_insight_documents.ensure_schema()
    oauth_store.ensure_schema()
    login_rate_limit.ensure_schema()
    boot = web_users.bootstrap_admin_from_env()
    if boot:
        audit_log.record(
            action="user.bootstrap_admin",
            subject_email=boot.email,
            detail={"source": "AUTH_BOOTSTRAP_ADMIN_*"},
        )
except Exception:
    # If Postgres isn't attached (or is temporarily unavailable), the service should still run.
    pass

if web_users.enabled():
    try:
        web_auth.add_session_middleware(app)
    except RuntimeError:
        pass

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


def _request_wants_html_404(request: Request) -> bool:
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
    if exc.status_code == 404 and _request_wants_html_404(request):
        return HTMLResponse(
            not_found_page.render_not_found_page(path=request.url.path),
            status_code=404,
        )
    detail = exc.detail
    if not isinstance(detail, str):
        detail = str(detail)
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
    # ChatGPT Custom Actions require a root-level `servers` URL (FastAPI omits it by default).
    base_url = (
        os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        or "https://sagefrog-production.up.railway.app"
    )
    schema["servers"] = [{"url": base_url}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]

_gpt_openapi_cache: dict | None = None


@app.get("/openapi-gpt.json", include_in_schema=False)
def openapi_for_chatgpt() -> JSONResponse:
    """OpenAPI document compatible with ChatGPT Custom Actions (single auth scheme)."""
    global _gpt_openapi_cache
    if _gpt_openapi_cache is None:
        _gpt_openapi_cache = build_chatgpt_openapi(app)
    return JSONResponse(_gpt_openapi_cache)


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
        "dashboard_penn": "/dashboard/penn",
        "dashboard_demo": "/dashboard/demo",
        "dashboard_client_settings": "/dashboard/{client_slug}/settings",
        "dashboard_penn_legacy_key": "/dashboard/penn?key=<CRON_SECRET>",
        "internal_sync_penn": "POST /internal/sync-penn (header X-Cron-Secret)",
        "ga4_env": "/ga4/env",
    }
    if not _hide_api_docs:
        out["docs"] = "/docs"
    return out


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    path = STATIC_DIR / "favicon-32x32.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(path, media_type="image/png")


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
        "account-level YOUTUBE_VIDEO assets. Intended for ChatGPT Custom Actions."
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
    if web_auth.get_current_user(request):
        target = (next or "/admin").strip() or "/admin"
        return RedirectResponse(url=target, status_code=303)
    target = (next or "/admin").strip() or "/admin"
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
    audit_log.record(
        action="login.success",
        actor_user_id=user.id,
        actor_email=user.email,
        detail={"role": user.role, "client_slug": user.client_slug},
        **ctx,
    )
    target = (next or "/admin").strip() or "/admin"
    if not target.startswith("/"):
        target = "/admin"
    return RedirectResponse(url=target, status_code=303)


@app.post("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    user = web_auth.get_current_user(request)
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


def _admin_dev_notes_for_page():
    if admin_dev_notes.enabled():
        return admin_dev_notes.list_notes()
    return None


@app.get("/admin", include_in_schema=False, response_class=HTMLResponse)
def admin_home(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
    oauth_disconnected: str | None = None,
    oauth_refresh: str | None = None,
):
    user = web_auth.get_current_user(request)
    if not user or user.role != "admin":
        return web_auth.redirect_to_login(request, next_path="/admin")
    users = web_users.list_users(include_inactive=False)
    events = audit_log.list_recent(limit=40)
    oauth_html = dashboard_settings.render_admin_oauth_section(
        return_url="/admin",
        oauth_connected=oauth_connected,
        oauth_error=(oauth_error or "").strip()[:300] or None,
        live_probe=bool(oauth_refresh),
    )
    flash = msg
    if oauth_disconnected and not flash:
        labels = {"google_ads": "Google Ads", "linkedin": "LinkedIn", "meta": "Meta", "harvest": "Harvest", "indeed": "Indeed"}
        flash = f"{labels.get(oauth_disconnected, oauth_disconnected)} disconnected."
    return HTMLResponse(
        web_auth.render_admin_page(
            user=user,
            users=users,
            audit_events=events,
            dev_notes=_admin_dev_notes_for_page(),
            message=flash,
            error=err,
            oauth_section_html=oauth_html,
        )
    )


@app.post("/admin/users", include_in_schema=False)
def admin_create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("client"),
    client_slug: str | None = Form(None),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        created = web_users.create_user(
            email=email,
            password=password,
            role=role,
            client_slug=client_slug,
        )
    except ValueError as e:
        users = web_users.list_users(include_inactive=False)
        events = audit_log.list_recent(limit=150)
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                dev_notes=_admin_dev_notes_for_page(),
                error=str(e),
            ),
            status_code=400,
        )
    audit_log.record(
        action="user.created",
        actor_user_id=user.id,
        actor_email=user.email,
        subject_email=created.email,
        detail={"role": created.role, "client_slug": created.client_slug},
        **ctx,
    )
    return RedirectResponse(url="/admin?msg=User+created", status_code=303)


@app.post("/admin/users/{user_id}/deactivate", include_in_schema=False)
def admin_deactivate_user(
    user_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    if user_id == admin.id:
        return RedirectResponse(url="/admin?err=Cannot+deactivate+your+own+account", status_code=303)
    target = web_users.get_user_record(user_id)
    if target and target.role == "admin" and web_users.count_admins() <= 1:
        return RedirectResponse(url="/admin?err=Cannot+deactivate+the+only+admin", status_code=303)
    if target and web_users.deactivate_user(user_id):
        audit_log.record(
            action="user.deactivated",
            actor_user_id=admin.id,
            actor_email=admin.email,
            subject_email=target.email,
            detail={"role": target.role, "client_slug": target.client_slug},
            **ctx,
        )
    return RedirectResponse(url="/admin?msg=User+deactivated", status_code=303)


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
        oauth_html = dashboard_settings.render_admin_oauth_section(return_url="/admin")
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                dev_notes=_admin_dev_notes_for_page(),
                error=str(exc),
                oauth_section_html=oauth_html,
            ),
            status_code=400,
        )
    audit_log.record(
        action="dashboard.created",
        actor_email=user.email,
        detail={"client_slug": created.client_slug, "label": created.label},
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin?msg=Dashboard+{quote(created.label)}+created",
        status_code=303,
    )


@app.post("/admin/dashboards/{client_slug}/delete", include_in_schema=False)
def admin_delete_dashboard(
    client_slug: str,
    request: Request,
    confirm_label: str = Form(""),
    user: web_users.WebUser = Depends(web_auth.require_admin),
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
        oauth_html = dashboard_settings.render_admin_oauth_section(return_url="/admin")
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                dev_notes=_admin_dev_notes_for_page(),
                error=str(exc),
                oauth_section_html=oauth_html,
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
        url=f"/admin?msg=Dashboard+{quote(deleted['label'])}+deleted",
        status_code=303,
    )


@app.post("/admin/dev-notes", include_in_schema=False)
def admin_create_dev_note(
    request: Request,
    title: str = Form(...),
    body: str = Form(""),
    category: str = Form("note"),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        note = admin_dev_notes.create_note(
            title=title,
            body=body,
            category=category,
            created_by=user.email,
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin?err={quote(str(exc))}", status_code=303)
    audit_log.record(
        action="dev_note.created",
        actor_user_id=user.id,
        actor_email=user.email,
        detail={"note_id": note.id, "title": note.title, "category": note.category},
        **ctx,
    )
    return RedirectResponse(url="/admin?msg=Dev+note+added", status_code=303)


@app.post("/admin/dev-notes/{note_id}", include_in_schema=False)
def admin_update_dev_note(
    note_id: int,
    request: Request,
    title: str = Form(...),
    body: str = Form(""),
    category: str = Form("note"),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        note = admin_dev_notes.update_note(
            note_id,
            title=title,
            body=body,
            category=category,
            updated_by=user.email,
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin?err={quote(str(exc))}", status_code=303)
    audit_log.record(
        action="dev_note.updated",
        actor_user_id=user.id,
        actor_email=user.email,
        detail={"note_id": note.id, "title": note.title, "category": note.category},
        **ctx,
    )
    return RedirectResponse(url="/admin?msg=Dev+note+updated", status_code=303)


@app.post("/admin/dev-notes/{note_id}/delete", include_in_schema=False)
def admin_delete_dev_note(
    note_id: int,
    request: Request,
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    existing = admin_dev_notes.get_note(note_id)
    if existing and admin_dev_notes.delete_note(note_id):
        audit_log.record(
            action="dev_note.deleted",
            actor_user_id=user.id,
            actor_email=user.email,
            detail={"note_id": existing.id, "title": existing.title},
            **ctx,
        )
    return RedirectResponse(url="/admin?msg=Dev+note+deleted", status_code=303)


@app.get(
    "/oauth/{platform}/connect",
    summary="Start OAuth connect flow (admin)",
    include_in_schema=False,
)
async def oauth_connect(platform: str, request: Request, return_to: str = "/admin"):
    slug = platform.strip().lower()
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
    oauth_flows.store_oauth_state(request, platform=slug, state=state, return_to=dest)
    try:
        url = oauth_flows.build_authorize_url(slug, state=state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    slug = platform.strip().lower()
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")
    expected_state, return_to = oauth_flows.pop_oauth_state(request, platform=slug)
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
        oauth_store.save_tokens(
            slug,
            refresh_token=tokens.get("refresh_token"),
            access_token=tokens.get("access_token"),
            token_expires_at=tokens.get("token_expires_at"),
            scopes=tokens.get("scopes"),
            metadata=tokens.get("metadata"),
            connected_by=actor,
        )
        audit_log.record(
            action="oauth.connected",
            actor_email=actor,
            detail={"platform": slug},
            **audit_log.request_context(request),
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"{dest}{sep}oauth_error={quote(str(exc)[:200])}",
            status_code=303,
        )
    return RedirectResponse(url=f"{dest}{sep}oauth_connected={quote(slug)}", status_code=303)


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
    slug = platform.strip().lower()
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
