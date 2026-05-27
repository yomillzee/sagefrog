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


class AccountRef(BaseModel):
    customer_id: str
    resource_name: str
    descriptive_name: str | None = None
    currency_code: str | None = None
    time_zone: str | None = None
    status: str = "ok"
    error: str | None = None


class AccountsResponse(BaseModel):
    count: int
    accounts: list[AccountRef]


class SearchManyRequest(BaseModel):
    customer_ids: list[str] = Field(..., description="One or more Google Ads customer IDs (digits only)")
    query: str = Field(..., description="GAQL query")


class SearchManyResult(BaseModel):
    customer_id: str
    row_count: int = 0
    rows: list[dict] = Field(default_factory=list)
    status: str = "ok"
    error: str | None = None


class SearchManyResponse(BaseModel):
    requested_count: int
    success_count: int
    failure_count: int
    results: list[SearchManyResult]


class SummaryAllRequest(BaseModel):
    customer_ids: list[str] | None = Field(
        default=None,
        description="Optional subset of customer IDs. If omitted, all accessible accounts are used.",
    )
    date_range: str = Field(
        default="LAST_30_DAYS",
        description="One of: LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, LAST_MONTH",
    )


class SummaryAllResponse(BaseModel):
    date_range: str
    account_count: int
    success_count: int
    failure_count: int
    totals: dict
    accounts: list[dict]


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
