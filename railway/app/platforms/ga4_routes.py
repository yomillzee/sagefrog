"""GA4 / BigQuery API routes.

The public/Custom-GPT surface for ga4 / bigquery: the configured GA4 clients, ad-hoc queries and the warehouse sync.

Lifted out of main.py, which held every non-dashboard route in the app.
Registered by platforms.register_platform_routes(); see platforms/__init__.py.
Every route here keeps its own `dependencies=[Depends(require_api_key)]`
rather than hoisting it onto the router, so the move stays a pure
relocation and the generated OpenAPI is byte-for-byte what it was.
"""

from __future__ import annotations

import bigquery_service
import db_cache
import ga4_warehouse_service

from fastapi import APIRouter, Depends, HTTPException
from dashboard.utils.dates import WAREHOUSE_DATE_RANGES
from models import Ga4ClientRef, Ga4ClientsResponse, Ga4EnvSummary, Ga4QueryRequest, Ga4QueryResponse, Ga4WarehouseSyncRequest, WarehouseSyncResponse
from security import require_api_key

router = APIRouter()


@router.post(
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

@router.get(
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

@router.get(
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

@router.post(
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
    db_cache.put_cached_best_effort(
        "ga4.query",
        cache_payload,
        response_json=rows,
        row_count=len(rows),
        status="ok",
        error=None,
    )
    return Ga4QueryResponse(row_count=len(rows), rows=rows)
