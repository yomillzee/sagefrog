"""Pydantic models for Indeed API requests and responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IndeedEnvSummary(BaseModel):
    """Summary of Indeed API environment configuration."""
    has_client_id: bool
    has_client_secret: bool
    has_api_key: bool
    has_publisher_id: bool


class IndeedTestTokenResponse(BaseModel):
    """Response from Indeed token validation."""
    ok: bool
    message: str
    endpoint: str | None = None
    error: str | None = None


class IndeedJobPostingRef(BaseModel):
    """Reference to a single job posting."""
    posting_id: str
    job_title: str
    registration_count: int
    status: str = "UNKNOWN"
    created_date: str | None = None
    account_id: str | None = None
    description: str | None = None


class IndeedJobPostingsResponse(BaseModel):
    """Response containing a list of job postings."""
    count: int
    postings: list[IndeedJobPostingRef]
    filters_applied: dict | None = None


class IndeedJobPostingDetailsResponse(BaseModel):
    """Detailed response for a single job posting."""
    posting_id: str
    job_title: str
    registration_count: int
    status: str
    created_date: str | None = None
    updated_date: str | None = None
    account_id: str | None = None
    description: str | None = None
    url: str | None = None


class IndeedRegistrationAnalyticsResponse(BaseModel):
    """Analytics response with job title and registration aggregations."""
    total_registrations: int
    job_titles: dict[str, int] = Field(description="Mapping of job title to registration count")
    posting_count: int
    date_range: dict | None = None


class IndeedJobPostingsRequest(BaseModel):
    """Request parameters for fetching job postings."""
    account_id: str | None = Field(None, description="Filter by account/organization ID")
    limit: int = Field(100, ge=1, le=500, description="Max postings to return")
    status: str | None = Field(None, description="Filter by status (ACTIVE, DRAFT, etc.)")


class IndeedAnalyticsRequest(BaseModel):
    """Request parameters for analytics."""
    posting_id: str | None = Field(None, description="Filter by specific posting")
    account_id: str | None = Field(None, description="Filter by account/organization")
    date_from: str | None = Field(None, description="Start date (ISO format)")
    date_to: str | None = Field(None, description="End date (ISO format)")
