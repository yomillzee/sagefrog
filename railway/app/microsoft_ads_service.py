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
    "campaign": "https://campaign.api.bingads.microsoft.com/CampaignManagement/v13",
}
_SANDBOX = {
    "customer": "https://clientcenter.api.sandbox.bingads.microsoft.com/CustomerManagement/v13",
    "reporting": "https://reporting.api.sandbox.bingads.microsoft.com/Reporting/v13",
    "campaign": "https://campaign.api.sandbox.bingads.microsoft.com/CampaignManagement/v13",
}

# Text ad types whose creative copy we surface in the Campaign Explorer. The
# reporting API only returns the *served* title parts (blank for RSAs), so the
# full headline/description asset lists come from Campaign Management instead.
_TEXT_AD_TYPES = ["ResponsiveSearch", "ExpandedText", "Text", "DynamicSearch"]
# Cap how many ad groups we pull creative for per sync, so a very large account
# can't turn one sync into thousands of metadata calls. Ad groups are visited
# spend-first, so the highest-spend creatives are always covered.
_MAX_AD_GROUPS_FOR_ASSETS = 300


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
        # Field names are singular (ExcludeReportHeader / ExcludeReportFooter).
        # The plural forms are silently ignored by the API, which then leaves the
        # metadata header block in the CSV and makes csv.DictReader read a metadata
        # line as the column headers — dropping every data row (0 rows loaded).
        "ExcludeReportHeader": True,
        "ExcludeReportFooter": True,
        "ExcludeColumnHeaders": False,
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

    raw_rows = _run_report(
        report_request, access_token=access_token, customer_id=customer_id,
        account_id=str(account_id), start=start, end=end, label="campaign",
    )
    out: list[dict[str, Any]] = []
    for raw in raw_rows:
        metric_date = _parse_report_date(raw.get("TimePeriod") or raw.get("GregorianDate") or "")
        campaign_id = str(raw.get("CampaignId") or "").strip()
        if not metric_date or not campaign_id:
            continue
        out.append({
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
    return out


def fetch_ad_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str,
    customer_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run a daily AdPerformanceReport and return ad_daily-shaped rows.

    One row per (campaign, ad group, ad, day) with the served ad copy so the
    Campaign Explorer can drill campaign → ad group → ad like Google text ads.
    Microsoft's reporting exposes the served title parts / descriptions (not the
    full RSA asset list), which map onto the same headline_1..3 / description_1..2
    fields the dashboard already renders.
    """
    if customer_id is None:
        customer_id = get_authenticated_customer_id(access_token)

    report_request = {
        "Type": "AdPerformanceReportRequest",
        "ReportName": "SagefrogAdPerformanceDaily",
        "Format": "Csv",
        "Aggregation": "Daily",
        "ExcludeReportHeader": True,
        "ExcludeReportFooter": True,
        "ExcludeColumnHeaders": False,
        "ReturnOnlyCompleteData": False,
        "Columns": [
            "TimePeriod",
            "CampaignId",
            "CampaignName",
            "AdGroupId",
            "AdGroupName",
            "AdId",
            "AdType",
            "AdTitle",
            "TitlePart1",
            "TitlePart2",
            "TitlePart3",
            "AdDescription",
            "AdDescription2",
            "Path1",
            "Path2",
            "DisplayUrl",
            "FinalUrl",
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

    raw_rows = _run_report(
        report_request, access_token=access_token, customer_id=customer_id,
        account_id=str(account_id), start=start, end=end, label="ad",
    )
    out: list[dict[str, Any]] = []
    for raw in raw_rows:
        metric_date = _parse_report_date(raw.get("TimePeriod") or raw.get("GregorianDate") or "")
        ad_id = str(raw.get("AdId") or "").strip()
        campaign_id = str(raw.get("CampaignId") or "").strip()
        if not metric_date or not ad_id or not campaign_id:
            continue
        out.append({
            "source": "microsoft",
            "account_id": str(account_id),
            "campaign_id": campaign_id,
            "campaign_name": str(raw.get("CampaignName") or "").strip(),
            "ad_group_id": str(raw.get("AdGroupId") or "").strip(),
            "ad_group_name": str(raw.get("AdGroupName") or "").strip(),
            "ad_id": ad_id,
            "ad_type": str(raw.get("AdType") or "").strip(),
            "ad_title": str(raw.get("AdTitle") or "").strip(),
            "title_part_1": str(raw.get("TitlePart1") or "").strip(),
            "title_part_2": str(raw.get("TitlePart2") or "").strip(),
            "title_part_3": str(raw.get("TitlePart3") or "").strip(),
            "description_1": str(raw.get("AdDescription") or "").strip(),
            "description_2": str(raw.get("AdDescription2") or "").strip(),
            "path_1": str(raw.get("Path1") or "").strip(),
            "path_2": str(raw.get("Path2") or "").strip(),
            "display_url": str(raw.get("DisplayUrl") or "").strip(),
            "final_url": str(raw.get("FinalUrl") or "").strip(),
            "metric_date": metric_date,
            "spend": _to_float(raw.get("Spend")),
            "clicks": _to_int(raw.get("Clicks")),
            "impressions": _to_int(raw.get("Impressions")),
            "conversions": _to_float(raw.get("Conversions")),
            "conversion_value": _to_float(raw.get("Revenue")),
        })
    return out


def fetch_goal_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str,
    customer_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run a daily GoalsAndFunnelsReport and return per-goal conversion rows.

    One row per (campaign, ad group, goal, day): {campaign_id, campaign_name,
    ad_group_id, ad_group_name, goal_id, goal_name, metric_date, conversions,
    conversion_value}. This is what splits Microsoft's ``Conv.`` into the
    individual conversion goals the account tracks.

    **Ad group is as fine as this report goes.** Microsoft's Goals and Funnels
    report exposes no AdId column, so the breakdown attaches to the ad group and
    the explorer shows a dash on the ad rows underneath rather than inventing a
    per-ad split. No spend/clicks/impressions either — a row scoped to one goal
    has no share of the ad group's cost.
    """
    if customer_id is None:
        customer_id = get_authenticated_customer_id(access_token)

    report_request = {
        "Type": "GoalsAndFunnelsReportRequest",
        "ReportName": "SagefrogGoalsAndFunnelsDaily",
        "Format": "Csv",
        "Aggregation": "Daily",
        "ExcludeReportHeader": True,
        "ExcludeReportFooter": True,
        "ExcludeColumnHeaders": False,
        "ReturnOnlyCompleteData": False,
        "Columns": [
            "TimePeriod",
            "CampaignId",
            "CampaignName",
            "AdGroupId",
            "AdGroupName",
            "Goal",
            "GoalId",
            "Conversions",
            "Revenue",
        ],
        "Scope": {"AccountIds": [int(str(account_id).strip())]},
        "Time": {
            "CustomDateRangeStart": {"Year": start.year, "Month": start.month, "Day": start.day},
            "CustomDateRangeEnd": {"Year": end.year, "Month": end.month, "Day": end.day},
        },
    }

    raw_rows = _run_report(
        report_request, access_token=access_token, customer_id=customer_id,
        account_id=str(account_id), start=start, end=end, label="goals",
    )
    out: list[dict[str, Any]] = []
    for raw in raw_rows:
        metric_date = _parse_report_date(raw.get("TimePeriod") or raw.get("GregorianDate") or "")
        campaign_id = str(raw.get("CampaignId") or "").strip()
        goal_name = str(raw.get("Goal") or "").strip()
        if not metric_date or not campaign_id or not goal_name:
            continue
        out.append({
            "source": "microsoft",
            "account_id": str(account_id),
            "campaign_id": campaign_id,
            "campaign_name": str(raw.get("CampaignName") or "").strip(),
            "ad_group_id": str(raw.get("AdGroupId") or "").strip(),
            "ad_group_name": str(raw.get("AdGroupName") or "").strip(),
            "goal_id": str(raw.get("GoalId") or "").strip(),
            "goal_name": goal_name,
            "metric_date": metric_date,
            "conversions": _to_float(raw.get("Conversions")),
            "conversion_value": _to_float(raw.get("Revenue")),
        })
    return out


def fetch_ad_assets(
    account_id: str,
    ad_group_ids: list[str],
    *,
    access_token: str,
    customer_id: str,
) -> dict[str, dict[str, Any]]:
    """Return {ad_id: {headlines, descriptions, path_1, path_2, final_url, ad_type}}.

    Uses Campaign Management GetAdsByAdGroupId to fetch the full creative copy the
    reporting API can't provide (RSA headline/description asset lists). Best-effort
    and bounded: a per-ad-group failure is logged and skipped, and at most
    _MAX_AD_GROUPS_FOR_ASSETS ad groups are visited so a huge account can't blow up
    one sync. Callers should pass ad_group_ids ordered spend-first.
    """
    headers = _headers(access_token, customer_id=customer_id, account_id=str(account_id))
    url = f"{_endpoints()['campaign']}/Ads/QueryByAdGroupId"
    out: dict[str, dict[str, Any]] = {}
    seen_groups = 0
    for ag_id in dict.fromkeys(str(g).strip() for g in ad_group_ids if str(g).strip()):
        if seen_groups >= _MAX_AD_GROUPS_FOR_ASSETS:
            _log.info("Microsoft Ads creative fetch capped at %d ad groups", _MAX_AD_GROUPS_FOR_ASSETS)
            break
        seen_groups += 1
        try:
            data = _post(url, headers, {"AdGroupId": int(ag_id), "AdTypes": _TEXT_AD_TYPES})
        except Exception as exc:
            _log.warning("Microsoft Ads GetAdsByAdGroupId failed [ad_group=%s]: %s", ag_id, exc)
            continue
        for ad in data.get("Ads") or []:
            ad_id = str((ad or {}).get("Id") or "").strip()
            if not ad_id:
                continue
            out[ad_id] = _extract_ad_assets(ad)
    _log.info(
        "Microsoft Ads creative fetched for %d ad(s) across %d ad group(s) [account=%s]",
        len(out), seen_groups, account_id,
    )
    return out


def _asset_texts(links: Any) -> list[str]:
    """Pull the .Asset.Text values out of a ResponsiveSearchAd asset-link array."""
    texts: list[str] = []
    for link in links or []:
        asset = (link or {}).get("Asset") or {}
        text = str(asset.get("Text") or "").strip()
        if text:
            texts.append(text)
    return texts


def _extract_ad_assets(ad: dict[str, Any]) -> dict[str, Any]:
    """Normalize any text ad type into {headlines[], descriptions[], path_1, path_2,
    final_url, ad_type}."""
    ad_type = str(ad.get("Type") or "").strip()
    finals = ad.get("FinalUrls") or []
    final_url = str(finals[0]).strip() if finals else str(ad.get("DestinationUrl") or "").strip()
    headlines: list[str] = []
    descriptions: list[str] = []
    if ad.get("Headlines") or ad.get("Descriptions"):
        # Responsive search ad — full asset lists.
        headlines = _asset_texts(ad.get("Headlines"))
        descriptions = _asset_texts(ad.get("Descriptions"))
    else:
        # Expanded/standard text ad — assemble from the discrete title/text parts.
        headlines = [
            str(ad.get(k) or "").strip()
            for k in ("TitlePart1", "TitlePart2", "TitlePart3", "Title")
        ]
        descriptions = [str(ad.get(k) or "").strip() for k in ("Text", "TextPart2")]
    return {
        "ad_type": ad_type,
        "headlines": [h for h in headlines if h],
        "descriptions": [d for d in descriptions if d],
        "path_1": str(ad.get("Path1") or "").strip(),
        "path_2": str(ad.get("Path2") or "").strip(),
        "final_url": final_url,
    }


def _run_report(
    report_request: dict[str, Any],
    *,
    access_token: str,
    customer_id: str,
    account_id: str,
    start: date,
    end: date,
    label: str,
) -> list[dict[str, str]]:
    """Submit → poll → download a report; return raw CSV rows (column-keyed dicts).

    Shared by the campaign and ad reports. Returns [] when Microsoft reports no
    data in range (Success with an empty download URL).
    """
    headers = _headers(access_token, customer_id=customer_id, account_id=str(account_id))
    submit_url = f"{_endpoints()['reporting']}/GenerateReport/Submit"
    submitted = _post(submit_url, headers, {"ReportRequest": report_request})
    report_request_id = str(submitted.get("ReportRequestId") or "").strip()
    if not report_request_id:
        raise RuntimeError("Microsoft Advertising reporting did not return a ReportRequestId.")

    _log.info(
        "Microsoft Ads %s report submitted [account=%s customer=%s range=%s→%s] request_id=%s",
        label, account_id, customer_id, start.isoformat(), end.isoformat(), report_request_id,
    )
    download_url = _poll_for_report(report_request_id, headers)
    if not download_url:
        _log.info(
            "Microsoft Ads %s report returned no download URL (no data in range) [account=%s range=%s→%s]",
            label, account_id, start.isoformat(), end.isoformat(),
        )
        return []
    return _download_report_rows(download_url, label=label, account_id=account_id)


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


def _download_report_rows(download_url: str, *, label: str, account_id: str) -> list[dict[str, str]]:
    """Download the (zipped) CSV report and return raw column-keyed rows.

    Robustly locates the column-header row (skipping any metadata preamble) so a
    stray header block can't silently drop every data row.
    """
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
                _log.warning("Microsoft Ads report zip had no .csv member (names=%s)", zf.namelist())
                return []
            text = zf.read(name).decode("utf-8-sig", errors="replace")
    else:
        text = content.decode("utf-8-sig", errors="replace")

    # Locate the column-header row. Both the campaign and ad reports carry
    # CampaignId + TimePeriod columns, so keying on those finds the real header
    # even if a metadata preamble ("Report Name", "Report Time", …) is present.
    all_lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(all_lines):
        flat = line.lower().replace('"', "").replace(" ", "")
        if "campaignid" in flat and "timeperiod" in flat:
            header_idx = i
            break
    if header_idx is None:
        _log.warning(
            "Microsoft Ads %s report: no column-header row found (first line: %r). Parsed 0 rows.",
            label, (all_lines[0] if all_lines else "")[:200],
        )
        return []

    rows = list(csv.DictReader(io.StringIO("\n".join(all_lines[header_idx:]))))
    _log.info(
        "Microsoft Ads %s report parsed: %d data row(s) [account=%s]",
        label, len(rows), account_id,
    )
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
