from __future__ import annotations

import os
from datetime import date

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

import bigquery_service
import ga4_warehouse_service
import google_ads_service
import linkedin_service
import meta_service
import db_cache
import warehouse
from auth import creds_fingerprint, env_summary
from linkedin_auth import env_summary as linkedin_env_summary
from meta_auth import env_summary as meta_env_summary
from openapi_gpt import build_chatgpt_openapi
from security import require_api_key
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
    LinkedInWarehouseSyncRequest,
    LinkedInWarehouseSyncResponse,
    MetaEnvSummary,
    MetaTestTokenResponse,
    MetaAccountsResponse,
    MetaAccountRef,
    MetaPerformanceResponse,
    MetaPerformanceTotals,
    MetaCampaignPerformance,
    MetaWarehouseSyncRequest,
    MetaWarehouseSyncResponse,
    GoogleAdsWarehouseSyncRequest,
    Ga4WarehouseSyncRequest,
    WarehouseSyncResponse,
    WarehouseStatusResponse,
    WarehouseMetricsResponse,
)

_WAREHOUSE_DATE_RANGES = frozenset(
    {
        "LAST_7_DAYS",
        "LAST_30_DAYS",
        "LAST_90_DAYS",
        "LAST_180_DAYS",
        "THIS_MONTH",
        "LAST_MONTH",
    }
)

load_dotenv()

app = FastAPI(
    title="EOS Ads + GA4 Service",
    version="0.2.0",
    description=(
        "When the server has API_KEY set in Railway, all /google-ads/*, /linkedin/*, /meta/*, and /ga4/* "
        "routes require Authorization: Bearer (your API_KEY value) or header X-API-Key with the "
        "same value. GET /health stays public for load balancers."
    ),
)

try:
    db_cache.ensure_schema()
    warehouse.ensure_schema()
except Exception:
    # If Postgres isn't attached (or is temporarily unavailable), the service should still run.
    pass


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
    return {
        "service": "EOS Ads + GA4 Service",
        "docs": "/docs",
        "health": "/health",
        "google_ads_test_token": "/google-ads/test-token",
        "youtube_videos": "/google-ads/youtube-videos",
        "linkedin_env": "/linkedin/env",
        "linkedin_test_token": "/linkedin/test-token",
        "linkedin_accounts": "/linkedin/accounts",
        "linkedin_performance": "/linkedin/performance",
        "linkedin_campaign_groups": "/linkedin/campaign-groups",
        "linkedin_campaign_groups_performance": "/linkedin/campaign-groups/performance",
        "linkedin_warehouse_sync": "/linkedin/warehouse/sync",
        "meta_env": "/meta/env",
        "meta_test_token": "/meta/test-token",
        "meta_accounts": "/meta/accounts",
        "meta_performance": "/meta/performance",
        "meta_warehouse_sync": "/meta/warehouse/sync",
        "google_ads_warehouse_sync": "/google-ads/warehouse/sync",
        "ga4_warehouse_sync": "/ga4/warehouse/sync",
        "warehouse_status": "/warehouse/status",
        "warehouse_metrics": "/warehouse/metrics",
        "ga4_env": "/ga4/env",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get(
    "/cache/health",
    response_model=CacheHealthResponse,
    dependencies=[Depends(require_api_key)],
)
def cache_health() -> CacheHealthResponse:
    return CacheHealthResponse(**db_cache.status())


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
    if preset not in _WAREHOUSE_DATE_RANGES:
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
    if preset not in _WAREHOUSE_DATE_RANGES:
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
    if preset not in _WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(_WAREHOUSE_DATE_RANGES))}",
        )

    cache_payload = {"account_id": account_id, "date_range": preset}
    hit = db_cache.get_cached("linkedin.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return LinkedInPerformanceResponse(
            account_id=payload.get("account_id", account_id),
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
    if preset not in _WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(_WAREHOUSE_DATE_RANGES))}",
        )

    cache_payload = {"account_id": account_id, "date_range": preset}
    hit = db_cache.get_cached("linkedin.campaign_groups.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return LinkedInCampaignGroupsPerformanceResponse(
            account_id=payload.get("account_id", account_id),
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
        date_range=payload["date_range"],
        totals=LinkedInCampaignGroupsPerformanceTotals(**payload["totals"]),
        campaign_groups=[
            LinkedInCampaignGroupPerformance(**g) for g in payload["campaign_groups"]
        ],
    )


@app.post(
    "/linkedin/warehouse/sync",
    response_model=LinkedInWarehouseSyncResponse,
    dependencies=[Depends(require_api_key)],
    summary="Sync LinkedIn daily metrics into Postgres warehouse",
)
def linkedin_warehouse_sync(body: LinkedInWarehouseSyncRequest) -> LinkedInWarehouseSyncResponse:
    preset = body.date_range.strip().upper().replace("-", "_")
    if preset not in _WAREHOUSE_DATE_RANGES:
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
    if preset not in _WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range: {date_range}. Use one of: {', '.join(sorted(_WAREHOUSE_DATE_RANGES))}",
        )

    cache_payload = {"account_id": account_id, "date_range": preset}
    hit = db_cache.get_cached("meta.performance", cache_payload)
    if hit is not None:
        payload = hit.response_json or {}
        return MetaPerformanceResponse(
            account_id=payload.get("account_id", account_id),
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
        date_range=payload["date_range"],
        totals=MetaPerformanceTotals(**payload["totals"]),
        campaigns=[MetaCampaignPerformance(**c) for c in payload["campaigns"]],
        warehouse=payload.get("warehouse"),
    )


@app.post(
    "/meta/warehouse/sync",
    response_model=MetaWarehouseSyncResponse,
    dependencies=[Depends(require_api_key)],
    summary="Sync Meta daily metrics into Postgres warehouse",
)
def meta_warehouse_sync(body: MetaWarehouseSyncRequest) -> MetaWarehouseSyncResponse:
    preset = body.date_range.strip().upper().replace("-", "_")
    if preset not in _WAREHOUSE_DATE_RANGES:
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
