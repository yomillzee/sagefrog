"""Meta (Facebook) Ads API routes.

The public/Custom-GPT surface for meta (facebook) ads: accounts, campaign and ad-set performance, videos, token checks and the warehouse sync.

Lifted out of main.py, which held every non-dashboard route in the app.
Registered by platforms.register_platform_routes(); see platforms/__init__.py.
Every route here keeps its own `dependencies=[Depends(require_api_key)]`
rather than hoisting it onto the router, so the move stays a pure
relocation and the generated OpenAPI is byte-for-byte what it was.
"""

from __future__ import annotations

import db_cache
import meta_service

from fastapi import APIRouter, Depends, HTTPException
from dashboard.utils.dates import WAREHOUSE_DATE_RANGES
from meta_auth import env_summary as meta_env_summary
from models import MetaAccountRef, MetaAccountsResponse, MetaAdSetPerformance, MetaAdSetsPerformanceResponse, MetaAdSetsPerformanceTotals, MetaCampaignPerformance, MetaEnvSummary, MetaPerformanceResponse, MetaPerformanceTotals, MetaTestAdsAccessResponse, MetaTestTokenResponse, MetaVideoItem, MetaVideosResponse, MetaWarehouseSyncRequest, MetaWarehouseSyncResponse
from security import require_api_key

router = APIRouter()


@router.get(
    "/meta/env",
    response_model=MetaEnvSummary,
    dependencies=[Depends(require_api_key)],
)
def meta_env() -> MetaEnvSummary:
    return MetaEnvSummary(**meta_env_summary())

@router.get(
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

@router.get(
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

@router.get(
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
    db_cache.put_cached_best_effort(
        "meta.accounts",
        cache_payload,
        response_json=rows,
        row_count=len(rows),
        status="ok",
        error=None,
    )
    return MetaAccountsResponse(
        count=len(rows),
        accounts=[MetaAccountRef(**r) for r in rows],
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "meta.performance",
        cache_payload,
        response_json=payload,
        row_count=len(payload.get("campaigns") or []),
        status="ok",
        error=None,
    )
    return MetaPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=MetaPerformanceTotals(**payload["totals"]),
        campaigns=[MetaCampaignPerformance(**c) for c in payload["campaigns"]],
        warehouse=payload.get("warehouse"),
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "meta.adsets.performance",
        cache_payload,
        response_json=payload,
        row_count=len(payload.get("adsets") or []),
        status="ok",
        error=None,
    )
    return MetaAdSetsPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=MetaAdSetsPerformanceTotals(**payload["totals"]),
        adsets=[MetaAdSetPerformance(**a) for a in payload["adsets"]],
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "meta.videos",
        cache_payload,
        response_json=payload,
        row_count=len(payload.get("videos") or []),
        status="ok",
        error=None,
    )
    return MetaVideosResponse(
        account_id=payload["account_id"],
        row_count=payload["row_count"],
        videos=[MetaVideoItem(**v) for v in payload["videos"]],
    )

@router.post(
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
