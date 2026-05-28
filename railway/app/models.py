from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class CacheHealthResponse(BaseModel):
    has_database_url: bool
    schema_ready: bool
    table_exists: bool
    cache_rows: int = 0
    error: str | None = None


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


class Ga4EnvSummary(BaseModel):
    has_gcp_service_account_json: bool
    has_bq_project_id: bool
    has_bq_dataset_id: bool
    bq_project_id: str | None = None
    bq_dataset_id: str | None = None
    gcp_service_account_json_char_count: int = 0
    gcp_service_account_json_hint: str = Field(
        default="empty",
        description="empty | raw_json | possibly_double_quoted_wrap | base64_or_other",
    )
    gcp_service_account_json_suspected_truncated: bool = Field(
        default=False,
        description="True if value is very short — common when Railway truncates a multiline paste.",
    )
    gcp_service_account_json_parse_ok: bool = False
    gcp_service_account_json_parse_error: str | None = Field(
        default=None,
        description="Parse error detail when parse_ok is false (no secret material).",
    )


class Ga4QueryRequest(BaseModel):
    sql: str = Field(..., description="BigQuery SQL query")
    max_rows: int = Field(default=1000, ge=1, le=50000, description="Maximum rows to return")


class Ga4QueryResponse(BaseModel):
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


class YoutubeVideosRequest(BaseModel):
    customer_id: str = Field(
        ...,
        description="Google Ads customer ID without dashes, e.g. 1234567890",
        examples=["1234567890"],
    )
    include_account_assets: bool = Field(
        default=True,
        description=(
            "Also include YOUTUBE_VIDEO rows from the asset table (account inventory). "
            "Useful when ad_group_ad_asset_view does not cover a campaign type."
        ),
    )
    include_metrics: bool = Field(
        default=False,
        description="Include impressions/clicks/cost/conversions for ad_group_ad_asset_view rows.",
    )
    date_range: str = Field(
        default="LAST_30_DAYS",
        description="Date range for metrics. One of: LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, LAST_MONTH",
    )


class YoutubeVideoItem(BaseModel):
    source: str = Field(
        description="ad_group_ad_asset_view | video_ad | asset",
    )
    campaign_id: str | None = None
    campaign_name: str | None = None
    ad_group_id: str | None = None
    ad_group_name: str | None = None
    ad_id: str | None = None
    ad_name: str | None = None
    ad_status: str | None = None
    asset_id: str | None = None
    asset_name: str | None = None
    asset_field_type: str | None = None
    youtube_video_id: str
    youtube_video_title: str | None = None
    youtube_watch_url: str
    youtube_embed_url: str
    youtube_thumbnail_url: str
    metrics: dict | None = None


class YoutubeVideosResponse(BaseModel):
    customer_id: str
    row_count: int
    videos: list[YoutubeVideoItem]


class LinkedInEnvSummary(BaseModel):
    has_client_id: bool
    has_client_secret: bool
    has_refresh_token: bool
    linkedin_version: str = "202604"
    refresh_token_looks_valid: bool = False


class LinkedInTestTokenResponse(BaseModel):
    ok: bool
    message: str
    account_count: int = 0
    error: str | None = None


class LinkedInAccountRef(BaseModel):
    id: str
    name: str = ""
    status: str = ""
    currency: str = ""
    type: str = ""


class LinkedInAccountsResponse(BaseModel):
    count: int
    accounts: list[LinkedInAccountRef]


class LinkedInPerformanceTotals(BaseModel):
    spend: float = 0.0
    clicks: int = 0
    impressions: int = 0
    conversions: float = 0.0
    conversion_value: float = 0.0
    campaign_count: int = 0


class LinkedInCampaignPerformance(BaseModel):
    id: str
    name: str = ""
    status: str = ""
    spend: float = 0.0
    clicks: int = 0
    impressions: int = 0
    conversions: float = 0.0


class LinkedInPerformanceResponse(BaseModel):
    account_id: str
    date_range: dict
    totals: LinkedInPerformanceTotals
    campaigns: list[LinkedInCampaignPerformance]
