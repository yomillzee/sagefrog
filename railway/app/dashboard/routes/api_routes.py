"""JSON API routes for client-facing dashboard data."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

import nixon_marketing_service
from security import require_api_key

router = APIRouter()


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


@router.get(
    "/api/clients/nixon/marketing",
    dependencies=[Depends(require_api_key)],
    summary="Nixon paid media performance from BigQuery marketing mart",
)
def nixon_marketing(
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
    start, end = _resolve_nixon_marketing_dates(start_date, end_date)
    try:
        return nixon_marketing_service.fetch_nixon_marketing(
            start_date=start,
            end_date=end,
            top_limit=top_limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500]) from exc


@router.get(
    "/api/clients/nixon/marketing/health",
    dependencies=[Depends(require_api_key)],
    summary="Nixon paid media mart health from BigQuery",
)
def nixon_marketing_health(
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Maximum mart_health rows to return.",
    ),
) -> dict:
    try:
        return nixon_marketing_service.fetch_nixon_marketing_health(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500]) from exc
