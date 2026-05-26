from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

import google_ads_service
from auth import env_summary
from models import GoogleAdsEnvSummary, HealthResponse, SearchRequest, SearchResponse

load_dotenv()

app = FastAPI(title="EOS Google Ads Service", version="0.1.0")


@app.get("/")
def root() -> dict:
    return {"service": "EOS Google Ads Service", "docs": "/docs", "health": "/health"}


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/google-ads/env", response_model=GoogleAdsEnvSummary)
def google_ads_env() -> GoogleAdsEnvSummary:
    return GoogleAdsEnvSummary(**env_summary())


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
