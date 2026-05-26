from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class GoogleAdsEnvSummary(BaseModel):
    has_developer_token: bool
    has_login_customer_id: bool
    has_client_id: bool
    has_client_secret: bool
    has_refresh_token: bool


class SearchRequest(BaseModel):
    customer_id: str = Field(..., description="Google Ads customer ID without dashes, e.g. 1234567890")
    query: str = Field(..., description="GAQL query")


class SearchResponse(BaseModel):
    customer_id: str
    row_count: int
    rows: list[dict]


class TestTokenResponse(BaseModel):
    ok: bool
    message: str
    token_expires_at: str | None = None
    error: str | None = None


class CredsFingerprintResponse(BaseModel):
    client_id: dict | None = None
    client_id_looks_valid: bool = False
    client_secret: dict | None = None
    refresh_token: dict | None = None
    refresh_token_looks_valid: bool = False
