"""the metrics warehouse API routes.

The public/Custom-GPT surface for the metrics warehouse: status and the aggregated metrics read.

Lifted out of main.py, which held every non-dashboard route in the app.
Registered by platforms.register_platform_routes(); see platforms/__init__.py.
Every route here keeps its own `dependencies=[Depends(require_api_key)]`
rather than hoisting it onto the router, so the move stays a pure
relocation and the generated OpenAPI is byte-for-byte what it was.
"""

from __future__ import annotations

import warehouse

from fastapi import APIRouter, Depends, HTTPException
from datetime import date
from models import WarehouseMetricsResponse, WarehouseStatusResponse
from security import require_api_key

router = APIRouter()


@router.get(
    "/warehouse/status",
    response_model=WarehouseStatusResponse,
    dependencies=[Depends(require_api_key)],
)
def warehouse_status() -> WarehouseStatusResponse:
    return WarehouseStatusResponse(**warehouse.status())

@router.get(
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
