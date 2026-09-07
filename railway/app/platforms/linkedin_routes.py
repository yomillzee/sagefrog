"""LinkedIn Marketing API routes.

The public/Custom-GPT surface for linkedin marketing: accounts, campaign groups, creative and campaign performance, videos and the warehouse sync.

Lifted out of main.py, which held every non-dashboard route in the app.
Registered by platforms.register_platform_routes(); see platforms/__init__.py.
Every route here keeps its own `dependencies=[Depends(require_api_key)]`
rather than hoisting it onto the router, so the move stays a pure
relocation and the generated OpenAPI is byte-for-byte what it was.
"""

from __future__ import annotations

import db_cache
import linkedin_service

from fastapi import APIRouter, Depends, HTTPException
from dashboard.utils.dates import WAREHOUSE_DATE_RANGES
from linkedin_auth import env_summary as linkedin_env_summary
from models import LinkedInAccountRef, LinkedInAccountsResponse, LinkedInCampaignGroupPerformance, LinkedInCampaignGroupRef, LinkedInCampaignGroupsPerformanceResponse, LinkedInCampaignGroupsPerformanceTotals, LinkedInCampaignGroupsResponse, LinkedInCampaignPerformance, LinkedInCreativePerformance, LinkedInCreativesPerformanceResponse, LinkedInCreativesPerformanceTotals, LinkedInEnvSummary, LinkedInPerformanceResponse, LinkedInPerformanceTotals, LinkedInTestTokenResponse, LinkedInVideoItem, LinkedInVideosResponse, LinkedInWarehouseSyncRequest, LinkedInWarehouseSyncResponse
from security import require_api_key

router = APIRouter()


@router.get(
    "/linkedin/env",
    response_model=LinkedInEnvSummary,
    dependencies=[Depends(require_api_key)],
)
def linkedin_env() -> LinkedInEnvSummary:
    return LinkedInEnvSummary(**linkedin_env_summary())

@router.get(
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

@router.get(
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
    db_cache.put_cached_best_effort(
        "linkedin.accounts",
        cache_payload,
        response_json=rows,
        row_count=len(rows),
        status="ok",
        error=None,
    )
    return LinkedInAccountsResponse(
        count=len(rows),
        accounts=[LinkedInAccountRef(**r) for r in rows],
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "linkedin.performance",
        cache_payload,
        response_json=payload,
        row_count=len(payload.get("campaigns") or []),
        status="ok",
        error=None,
    )
    return LinkedInPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=LinkedInPerformanceTotals(**payload["totals"]),
        campaigns=[LinkedInCampaignPerformance(**c) for c in payload["campaigns"]],
        warehouse=payload.get("warehouse"),
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "linkedin.campaign_groups",
        cache_payload,
        response_json=rows,
        row_count=len(rows),
        status="ok",
        error=None,
    )
    return LinkedInCampaignGroupsResponse(
        account_id=str(account_id).strip().split(":")[-1],
        count=len(rows),
        campaign_groups=[LinkedInCampaignGroupRef(**r) for r in rows],
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "linkedin.campaign_groups.performance",
        cache_payload,
        response_json=payload,
        row_count=len(payload.get("campaign_groups") or []),
        status="ok",
        error=None,
    )
    return LinkedInCampaignGroupsPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=LinkedInCampaignGroupsPerformanceTotals(**payload["totals"]),
        campaign_groups=[
            LinkedInCampaignGroupPerformance(**g) for g in payload["campaign_groups"]
        ],
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "linkedin.creatives.performance",
        cache_payload,
        response_json=payload,
        row_count=len(payload.get("creatives") or []),
        status="ok",
        error=None,
    )
    return LinkedInCreativesPerformanceResponse(
        account_id=payload["account_id"],
        entity_level=payload.get("entity_level", "account"),
        date_range=payload["date_range"],
        totals=LinkedInCreativesPerformanceTotals(**payload["totals"]),
        creatives=[LinkedInCreativePerformance(**c) for c in payload["creatives"]],
    )

@router.get(
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
    db_cache.put_cached_best_effort(
        "linkedin.videos",
        cache_payload,
        response_json=payload,
        row_count=len(payload.get("videos") or []),
        status="ok",
        error=None,
    )
    return LinkedInVideosResponse(
        account_id=payload["account_id"],
        row_count=payload["row_count"],
        videos=[LinkedInVideoItem(**v) for v in payload["videos"]],
    )

@router.post(
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
