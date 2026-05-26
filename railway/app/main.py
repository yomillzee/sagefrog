from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

import google_ads_service
from auth import env_summary
from models import (
    GoogleAdsEnvSummary,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    TestTokenResponse,
)

load_dotenv()

app = FastAPI(title="EOS Google Ads Service", version="0.1.0")


@app.get("/")
def root() -> dict:
    return {
        "service": "EOS Google Ads Service",
        "docs": "/docs",
        "health": "/health",
        "test_token": "/google-ads/test-token",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/google-ads/env", response_model=GoogleAdsEnvSummary)
def google_ads_env() -> GoogleAdsEnvSummary:
    return GoogleAdsEnvSummary(**env_summary())


@app.get("/google-ads/test-token", response_model=TestTokenResponse)
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


@app.post("/google-ads/search", response_model=SearchResponse)
def google_ads_search(body: SearchRequest) -> SearchResponse:
    try:
        rows = google_ads_service.search(customer_id=body.customer_id, query=body.query)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SearchResponse(customer_id=body.customer_id, row_count=len(rows), rows=rows)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
