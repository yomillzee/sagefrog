"""Indeed Job Posting API service for retrieving job titles and registration counts."""

from __future__ import annotations

import json
from typing import Any

import httpx

from indeed_auth import load_indeed_env

# Indeed API endpoints
INDEED_OAUTH_URL = "https://secure.indeed.com/oauth/authorize"
INDEED_TOKEN_URL = "https://secure.indeed.com/oauth/access_token"
INDEED_API_BASE = "https://api.indeed.com/jobpostings"


class IndeedAPIError(Exception):
    """Raised when Indeed API returns an error."""


def _get_headers(access_token: str | None = None) -> dict[str, str]:
    """Build headers for Indeed API requests."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _make_request(
    method: str,
    endpoint: str,
    *,
    access_token: str | None = None,
    params: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make an HTTP request to Indeed API with error handling."""
    url = f"{INDEED_API_BASE}{endpoint}"
    headers = _get_headers(access_token)

    try:
        with httpx.Client(timeout=30.0) as client:
            if method.upper() == "GET":
                response = client.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                response = client.post(url, json=json_data, params=params, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if response.status_code >= 400:
                try:
                    error_data = response.json()
                except Exception:
                    error_data = {"detail": response.text}
                raise IndeedAPIError(
                    f"Indeed API error {response.status_code}: {json.dumps(error_data)}"
                )

            return response.json()
    except httpx.RequestError as e:
        raise IndeedAPIError(f"Request failed: {str(e)}") from e


def test_token() -> dict[str, Any]:
    """
    Test Indeed API credentials by making a simple request.
    Returns status dict with ok=True/False.
    """
    try:
        env = load_indeed_env()
        # Try to get a list of organizations (minimal request to validate credentials)
        _make_request(
            "GET",
            "/organizations",
            access_token=env.client_id,  # Indeed uses client_id as bearer token for some endpoints
            params={"limit": 1},
        )
        return {
            "ok": True,
            "message": "Indeed API credentials are valid",
            "endpoint": "organizations",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": "Failed to validate Indeed API credentials",
            "error": str(e),
        }


def list_job_postings(
    account_id: str | None = None,
    limit: int = 100,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve job postings with title and registration count.

    Args:
        account_id: Filter by account/organization ID (optional)
        limit: Max number of postings to return (default 100, max 500)
        status: Filter by status (ACTIVE, DRAFT, PENDING, ARCHIVED, etc.)

    Returns:
        List of job posting records with titles and registration counts
    """
    env = load_indeed_env()
    limit = min(int(limit or 100), 500)

    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status.upper()
    if account_id:
        params["account_id"] = account_id

    try:
        response = _make_request(
            "GET",
            "/postings",
            access_token=env.client_id,
            params=params,
        )

        postings = response.get("postings", [])
        # Normalize response structure
        normalized: list[dict[str, Any]] = []
        for posting in postings:
            normalized.append({
                "posting_id": posting.get("id") or posting.get("posting_id"),
                "job_title": posting.get("jobTitle") or posting.get("title") or "",
                "registration_count": int(posting.get("registrationCount") or posting.get("applications") or 0),
                "status": posting.get("status", "UNKNOWN"),
                "created_date": posting.get("createdDate") or posting.get("created_at"),
                "account_id": posting.get("account_id") or posting.get("organizationId"),
                "description": posting.get("description", "")[:500],  # Truncate
            })
        return normalized
    except IndeedAPIError as e:
        raise e
    except Exception as e:
        raise IndeedAPIError(f"Failed to fetch job postings: {str(e)}") from e


def get_job_posting(
    posting_id: str,
) -> dict[str, Any]:
    """
    Retrieve details for a specific job posting including registration count.

    Args:
        posting_id: The Indeed posting ID

    Returns:
        Job posting details with title, status, and registration count
    """
    env = load_indeed_env()

    try:
        response = _make_request(
            "GET",
            f"/postings/{posting_id}",
            access_token=env.client_id,
        )

        posting = response.get("posting", response)  # Handle both single posting and wrapped response
        return {
            "posting_id": posting.get("id") or posting.get("posting_id"),
            "job_title": posting.get("jobTitle") or posting.get("title", ""),
            "registration_count": int(posting.get("registrationCount") or posting.get("applications") or 0),
            "status": posting.get("status", "UNKNOWN"),
            "created_date": posting.get("createdDate") or posting.get("created_at"),
            "updated_date": posting.get("updatedDate") or posting.get("updated_at"),
            "account_id": posting.get("account_id") or posting.get("organizationId"),
            "description": posting.get("description", "")[:1000],
            "url": posting.get("url") or posting.get("posting_url"),
        }
    except IndeedAPIError as e:
        raise e
    except Exception as e:
        raise IndeedAPIError(f"Failed to fetch job posting {posting_id}: {str(e)}") from e


def get_registration_analytics(
    posting_id: str | None = None,
    account_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve registration analytics (job titles and registration counts).

    Args:
        posting_id: Filter by specific posting (optional)
        account_id: Filter by account/organization (optional)
        date_from: Start date for date range (ISO format, optional)
        date_to: End date for date range (ISO format, optional)

    Returns:
        Analytics data with aggregated registration counts by job title
    """
    env = load_indeed_env()

    params: dict[str, Any] = {}
    if posting_id:
        params["posting_id"] = posting_id
    if account_id:
        params["account_id"] = account_id
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    try:
        response = _make_request(
            "GET",
            "/analytics/registrations",
            access_token=env.client_id,
            params=params,
        )

        # Build job title -> registration count aggregation
        analytics = response.get("analytics", response)
        job_title_stats: dict[str, int] = {}

        postings = analytics if isinstance(analytics, list) else analytics.get("postings", [])
        for posting in postings:
            title = posting.get("jobTitle") or posting.get("title", "Unknown")
            count = int(posting.get("registrationCount") or posting.get("applications") or 0)
            job_title_stats[title] = job_title_stats.get(title, 0) + count

        total_registrations = sum(job_title_stats.values())
        return {
            "total_registrations": total_registrations,
            "job_titles": job_title_stats,
            "posting_count": len(job_title_stats),
            "date_range": {
                "from": date_from,
                "to": date_to,
            },
        }
    except IndeedAPIError as e:
        raise e
    except Exception as e:
        raise IndeedAPIError(f"Failed to fetch registration analytics: {str(e)}") from e
