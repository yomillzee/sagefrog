from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.openapi.utils import get_openapi

import bigquery_service
import google_ads_service
from auth import creds_fingerprint, env_summary
from security import require_api_key
from models import (
    AccountsResponse,
    AccountRef,
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
    Ga4QueryRequest,
    Ga4QueryResponse,
    TestTokenResponse,
)

load_dotenv()

app = FastAPI(
    title="EOS Google Ads Service",
    version="0.1.0",
    description=(
        "When the server has API_KEY set in Railway, all /google-ads/* routes require "
        "Authorization: Bearer (your API_KEY value) or header X-API-Key with the same value. "
        "GET /health stays public for load balancers."
    ),
)


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
        if not (path.startswith("/google-ads") or path.startswith("/ga4")):
            continue
        for method in ("get", "post", "put", "delete", "patch"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            # Either Bearer or X-API-Key (OpenAPI: alternatives are OR).
            op["security"] = [{"BearerAuth": []}, {"ApiKeyHeader": []}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/")
def root() -> dict:
    return {
        "service": "EOS Google Ads Service",
        "docs": "/docs",
        "health": "/health",
        "test_token": "/google-ads/test-token",
        "ga4_env": "/ga4/env",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


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
    try:
        rows = google_ads_service.search(customer_id=body.customer_id, query=body.query)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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


@app.get(
    "/ga4/env",
    response_model=Ga4EnvSummary,
    dependencies=[Depends(require_api_key)],
)
def ga4_env() -> Ga4EnvSummary:
    return Ga4EnvSummary(**bigquery_service.env_summary())


@app.post(
    "/ga4/query",
    response_model=Ga4QueryResponse,
    dependencies=[Depends(require_api_key)],
)
def ga4_query(body: Ga4QueryRequest) -> Ga4QueryResponse:
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
    return Ga4QueryResponse(row_count=len(rows), rows=rows)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
