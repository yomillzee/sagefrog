"""Indeed API routes.

The public/Custom-GPT surface for indeed: environment and token checks, job postings and registration analytics.

Lifted out of main.py, which held every non-dashboard route in the app.
Registered by platforms.register_platform_routes(); see platforms/__init__.py.
Every route here keeps its own `dependencies=[Depends(require_api_key)]`
rather than hoisting it onto the router, so the move stays a pure
relocation and the generated OpenAPI is byte-for-byte what it was.
"""

from __future__ import annotations

import db_cache
import indeed_service

from fastapi import APIRouter, Depends, HTTPException
from indeed_auth import env_summary as indeed_env_summary
from indeed_models import IndeedAnalyticsRequest, IndeedEnvSummary, IndeedJobPostingDetailsResponse, IndeedJobPostingRef, IndeedJobPostingsRequest, IndeedJobPostingsResponse, IndeedRegistrationAnalyticsResponse, IndeedTestTokenResponse
from security import require_api_key

router = APIRouter()


@router.get(
    "/indeed/env",
    response_model=IndeedEnvSummary,
    dependencies=[Depends(require_api_key)],
    summary="Check Indeed API environment configuration",
)
def indeed_env() -> IndeedEnvSummary:
    """Check if Indeed API credentials are configured (no secrets returned)."""
    return IndeedEnvSummary(**indeed_env_summary())

@router.get(
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

@router.post(
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

    db_cache.put_cached_best_effort(
        "indeed.postings",
        cache_payload,
        response_json=rows,
        row_count=len(rows),
        status="ok",
        error=None,
        ttl_seconds=3600,  # Cache for 1 hour
    )

    return IndeedJobPostingsResponse(
        count=len(rows),
        postings=[IndeedJobPostingRef(**r) for r in rows],
    )

@router.get(
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

    db_cache.put_cached_best_effort(
        "indeed.posting_detail",
        cache_payload,
        response_json=payload,
        row_count=1,
        status="ok",
        error=None,
        ttl_seconds=3600,
    )

    return IndeedJobPostingDetailsResponse(**payload)

@router.post(
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

    db_cache.put_cached_best_effort(
        "indeed.analytics",
        cache_payload,
        response_json=payload,
        row_count=payload.get("posting_count", 0),
        status="ok",
        error=None,
        ttl_seconds=3600,
    )

    return IndeedRegistrationAnalyticsResponse(**payload)
