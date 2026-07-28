"""Microsoft Advertising (Bing Ads) API client, authenticated via Google OAuth.

Microsoft Advertising supports Google as an OAuth identity provider: the user
authorizes through Google (see oauth_flows._exchange_microsoft_ads_code) and the
resulting Google access token is presented to the Microsoft Advertising REST API
with an ``IdentityProvider: Google`` header alongside the Microsoft developer
token. Microsoft still enforces its own advertiser permission checks — Google is
used only to authenticate the user.

This module wraps the two REST surfaces the connector needs:

* Customer Management v13 — list the accounts the authenticated user can reach
  (``GetUser`` → CustomerId, then ``GetAccountsInfo``).
* Reporting v13 — submit / poll / download a CampaignPerformanceReport and parse
  the CSV into per-campaign daily rows shaped like the other paid-media
  connectors (see bigquery_warehouse.mirror_campaign_daily_batch).

Tokens and secrets are never logged.
Docs: https://learn.microsoft.com/advertising/guides/authentication-oauth
"""

from __future__ import annotations

import csv
import io
import logging
import os
import time
import zipfile
from datetime import date
from typing import Any

import httpx

import oauth_flows
import oauth_store

_log = logging.getLogger(__name__)

# REST endpoints. Production by default; set MICROSOFT_ADS_ENVIRONMENT=sandbox to
# target the Bing Ads sandbox (useful for a dry run without touching live spend).
_PROD = {
    "customer": "https://clientcenter.api.bingads.microsoft.com/CustomerManagement/v13",
    "reporting": "https://reporting.api.bingads.microsoft.com/Reporting/v13",
}
_SANDBOX = {
    "customer": "https://clientcenter.api.sandbox.bingads.microsoft.com/CustomerManagement/v13",
    "reporting": "https://reporting.api.sandbox.bingads.microsoft.com/Reporting/v13",
}


def _endpoints() -> dict[str, str]:
    env = (os.getenv("MICROSOFT_ADS_ENVIRONMENT") or "production").strip().lower()
    return _SANDBOX if env in {"sandbox", "sb"} else _PROD


def _developer_token() -> str:
    token = (os.getenv("MICROSOFT_ADS_DEVELOPER_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("MICROSOFT_ADS_DEVELOPER_TOKEN is not set.")
    return token


def resolve_access_token(client_slug: str) -> str:
    """Return a live Google access token for this client's Microsoft Ads connection.

    Reads the stored (client-scoped, else global) Google refresh token and
    exchanges it for a fresh access token every call — access tokens are short
    lived, so we never rely on a possibly-stale stored one. Persists the refreshed
    access token + expiry back to the store (best effort) for observability.
    """
    refresh = oauth_store.get_refresh_token("microsoft_ads", client_slug=client_slug)
    if not refresh:
        raise RuntimeError(oauth_store.token_error(
            "microsoft_ads", client_slug=client_slug,
            missing=(
                f"No Microsoft Ads OAuth token for client '{client_slug}'. "
                "Connect Microsoft Ads in the connector setup wizard."
            ),
        ))
    refreshed = oauth_flows.refresh_microsoft_ads_access_token(refresh)
    access = refreshed["access_token"]
    try:
        oauth_store.save_tokens(
            "microsoft_ads",
            access_token=access,
            token_expires_at=refreshed.get("token_expires_at"),
            client_slug=(client_slug or "").strip(),
        )
    except Exception:
        _log.debug("Microsoft Ads access-token write-back skipped", exc_info=True)
    return access


def _headers(access_token: str, *, customer_id: str | None = None, account_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "DeveloperToken": _developer_token(),
        "IdentityProvider": "Google",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if customer_id:
        headers["CustomerId"] = str(customer_id)
    if account_id:
        headers["CustomerAccountId"] = str(account_id)
    return headers


def _post(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        # Response bodies here carry Microsoft error codes, not tokens.
        raise RuntimeError(f"Microsoft Advertising API error ({resp.status_code}): {resp.text[:500]}")
    try:
        return resp.json()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Customer Management — account discovery
# ---------------------------------------------------------------------------

def get_authenticated_customer_id(access_token: str) -> str:
    """Return the CustomerId of the authenticated user (via GetUser, UserId=null)."""
    url = f"{_endpoints()['customer']}/User/Query"
    data = _post(url, _headers(access_token), {"UserId": None})
    user = data.get("User") or {}
    customer_id = str(user.get("CustomerId") or "").strip()
    if not customer_id:
        # Fall back to the first customer role if the User object omitted it.
        for role in data.get("CustomerRoles") or []:
            cid = str((role or {}).get("CustomerId") or "").strip()
            if cid:
                return cid
        raise RuntimeError("Could not determine Microsoft Advertising CustomerId for this user.")
    return customer_id


def list_accounts(*, client_slug: str) -> list[dict[str, Any]]:
    """Return [{"id","name","status"}] for accounts the connected Google user can reach."""
    access_token = resolve_access_token(client_slug)
    customer_id = get_authenticated_customer_id(access_token)
    url = f"{_endpoints()['customer']}/AccountsInfo/Query"
    data = _post(
        url,
        _headers(access_token, customer_id=customer_id),
        {"CustomerId": customer_id, "OnlyParentAccounts": False},
    )
    out: list[dict[str, Any]] = []
    for acct in data.get("AccountsInfo") or []:
        acct_id = str(acct.get("Id") or "").strip()
        if not acct_id:
            continue
        out.append({
            "id": acct_id,
            "name": str(acct.get("Name") or acct.get("Number") or acct_id),
            "status": str(acct.get("AccountLifeCycleStatus") or ""),
            "number": str(acct.get("Number") or ""),
            "customer_id": customer_id,
        })
    return out


# ---------------------------------------------------------------------------
# Reporting — CampaignPerformanceReport → per-campaign daily rows
# ---------------------------------------------------------------------------

_REPORT_POLL_MAX_SECONDS = 300
_REPORT_POLL_INTERVAL_SECONDS = 5


def fetch_campaign_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str,
    customer_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run a daily CampaignPerformanceReport and return campaign_daily-shaped rows.

    Each row: {source, account_id, campaign_id, campaign_name, metric_date,
    spend, clicks, impressions, conversions, conversion_value}.
    """
    if customer_id is None:
        customer_id = get_authenticated_customer_id(access_token)

    report_request = {
        "Type": "CampaignPerformanceReportRequest",
        "ReportName": "SagefrogCampaignPerformanceDaily",
        "Format": "Csv",
        "Aggregation": "Daily",
        "ExcludeReportHeaders": True,
        "ExcludeReportFooters": True,
        "ReturnOnlyCompleteData": False,
        "Columns": [
            "TimePeriod",
            "CampaignId",
            "CampaignName",
            "Spend",
            "Impressions",
            "Clicks",
            "Conversions",
            "Revenue",
        ],
        "Scope": {"AccountIds": [int(str(account_id).strip())]},
        "Time": {
            "CustomDateRangeStart": {"Year": start.year, "Month": start.month, "Day": start.day},
            "CustomDateRangeEnd": {"Year": end.year, "Month": end.month, "Day": end.day},
        },
    }

    headers = _headers(access_token, customer_id=customer_id, account_id=str(account_id))
    submit_url = f"{_endpoints()['reporting']}/GenerateReport/Submit"
    submitted = _post(submit_url, headers, {"ReportRequest": report_request})
    report_request_id = str(submitted.get("ReportRequestId") or "").strip()
    if not report_request_id:
        raise RuntimeError("Microsoft Advertising reporting did not return a ReportRequestId.")

    download_url = _poll_for_report(report_request_id, headers)
    if not download_url:
        # Status resolved to Success with no data (no spend in range) — not an error.
        return []
    return _parse_report_csv(download_url, account_id=str(account_id))


def _poll_for_report(report_request_id: str, headers: dict[str, str]) -> str:
    """Poll until the report is Success/Error; return the download URL (or '')."""
    poll_url = f"{_endpoints()['reporting']}/GenerateReport/Poll"
    deadline = time.monotonic() + _REPORT_POLL_MAX_SECONDS
    while True:
        data = _post(poll_url, headers, {"ReportRequestId": report_request_id})
        status_obj = data.get("ReportRequestStatus") or {}
        status = str(status_obj.get("Status") or "").strip()
        if status == "Success":
            return str(status_obj.get("ReportDownloadUrl") or "").strip()
        if status == "Error":
            raise RuntimeError("Microsoft Advertising report generation failed (status=Error).")
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Microsoft Advertising report timed out after {_REPORT_POLL_MAX_SECONDS}s "
                f"(last status: {status or 'unknown'})."
            )
        time.sleep(_REPORT_POLL_INTERVAL_SECONDS)


def _parse_report_csv(download_url: str, *, account_id: str) -> list[dict[str, Any]]:
    """Download the (zipped) CSV report and map rows to campaign_daily shape."""
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        resp = client.get(download_url)
    if resp.status_code >= 400:
        raise RuntimeError(f"Microsoft Advertising report download failed ({resp.status_code}).")

    content = resp.content
    text: str
    if content[:2] == b"PK":  # ZIP magic — Csv reports are delivered zipped.
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
            if not name:
                return []
            text = zf.read(name).decode("utf-8-sig", errors="replace")
    else:
        text = content.decode("utf-8-sig", errors="replace")

    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        metric_date = _parse_report_date(raw.get("TimePeriod") or raw.get("GregorianDate") or "")
        campaign_id = str(raw.get("CampaignId") or "").strip()
        if not metric_date or not campaign_id:
            continue
        rows.append({
            "source": "microsoft",
            "account_id": str(account_id),
            "campaign_id": campaign_id,
            "campaign_name": str(raw.get("CampaignName") or "").strip(),
            "metric_date": metric_date,
            "spend": _to_float(raw.get("Spend")),
            "clicks": _to_int(raw.get("Clicks")),
            "impressions": _to_int(raw.get("Impressions")),
            "conversions": _to_float(raw.get("Conversions")),
            "conversion_value": _to_float(raw.get("Revenue")),
        })
    return rows


def _parse_report_date(value: str) -> str:
    """Normalize Microsoft's TimePeriod to YYYY-MM-DD; '' if unparseable."""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            from datetime import datetime as _dt

            return _dt.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _to_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").replace("%", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(round(_to_float(value)))
    except (TypeError, ValueError):
        return 0
