from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from linkedin_auth import LinkedInEnv, load_linkedin_env

LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com/rest"

_CONVERSION_FIELDS = (
    "conversions",
    "externalWebsiteConversions",
    "viralExternalWebsiteConversions",
    "leadGenerationMailContactInfoShares",
    "oneClickLeadFormOpens",
    "oneClickLeads",
    "opens",
)


def _normalize_account_id(account_id: str) -> str:
    return str(account_id or "").strip().split(":")[-1]


def _account_urn(account_id: str) -> str:
    clean = _normalize_account_id(account_id)
    return f"urn:li:sponsoredAccount:{clean}"


def _parse_spend(record: dict[str, Any]) -> float:
    return float(
        record.get("costInUsd")
        or record.get("costInLocalCurrency")
        or record.get("spend")
        or record.get("totalSpend")
        or 0
    )


def _parse_conversions(record: dict[str, Any]) -> float:
    return float(sum(float(record.get(key) or 0) for key in _CONVERSION_FIELDS))


def _parse_conversion_value(record: dict[str, Any]) -> float:
    return float(record.get("conversionValueInUsd") or record.get("conversionValue") or 0)


def _date_parts(value: date) -> dict[str, int]:
    return {"year": value.year, "month": value.month, "day": value.day}


def _format_date_range(start: date, end: date) -> str:
    s = _date_parts(start)
    e = _date_parts(end)
    return (
        f"(start:(year:{s['year']},month:{s['month']},day:{s['day']}),"
        f"end:(year:{e['year']},month:{e['month']},day:{e['day']}))"
    )


def resolve_date_range(preset: str) -> tuple[date, date, str]:
    """Map GPT-style presets to inclusive UTC calendar dates."""
    today = date.today()
    key = str(preset or "LAST_30_DAYS").strip().upper().replace("-", "_")

    if key == "LAST_7_DAYS":
        return today - timedelta(days=6), today, key
    if key == "LAST_30_DAYS":
        return today - timedelta(days=29), today, key
    if key == "LAST_90_DAYS":
        return today - timedelta(days=89), today, key
    if key == "LAST_180_DAYS":
        return today - timedelta(days=179), today, key
    if key == "THIS_MONTH":
        return today.replace(day=1), today, key
    if key == "LAST_MONTH":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end, key

    return today - timedelta(days=29), today, "LAST_30_DAYS"


def _client_headers(access_token: str, env: LinkedInEnv | None = None) -> dict[str, str]:
    env = env or load_linkedin_env()
    return {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": env.version,
    }


def refresh_access_token(env: LinkedInEnv | None = None) -> dict[str, Any]:
    env = env or load_linkedin_env()
    body = {
        "grant_type": "refresh_token",
        "refresh_token": env.refresh_token,
        "client_id": env.client_id,
        "client_secret": env.client_secret,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            LINKEDIN_TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json()
        except Exception:
            pass
        raise RuntimeError(f"LinkedIn OAuth refresh failed ({response.status_code}): {detail}")
    data = response.json()
    if not data.get("access_token"):
        raise RuntimeError("LinkedIn OAuth refresh returned no access_token.")
    return data


def test_refresh_token(env: LinkedInEnv | None = None) -> dict[str, Any]:
    try:
        token_data = refresh_access_token(env)
        accounts = list_ad_accounts(access_token=token_data["access_token"], env=env)
        return {
            "ok": True,
            "message": "LinkedIn OAuth refresh succeeded.",
            "account_count": len(accounts),
            "error": None,
        }
    except Exception as e:
        err = str(e)
        hint = (
            "Usually: refresh token revoked, wrong client secret, or token minted with "
            "different LINKEDIN_CLIENT_ID. Scopes need r_ads and r_ads_reporting."
        )
        if "invalid_grant" in err.lower():
            hint = (
                "invalid_grant: regenerate refresh token with the same LINKEDIN_CLIENT_ID / "
                "LINKEDIN_CLIENT_SECRET in Railway."
            )
        return {
            "ok": False,
            "message": "LinkedIn OAuth refresh failed.",
            "account_count": 0,
            "error": f"{err} — {hint}",
        }


def _linkedin_get(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    env: LinkedInEnv | None = None,
) -> dict[str, Any]:
    url = f"{LINKEDIN_API_BASE}{path}"
    with httpx.Client(timeout=120.0) as client:
        response = client.get(url, params=params, headers=_client_headers(access_token, env))
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json()
        except Exception:
            pass
        raise RuntimeError(f"LinkedIn API error {response.status_code} on {path}: {detail}")
    return response.json()


def list_ad_accounts(
    *,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    env = env or load_linkedin_env()
    access_token = access_token or refresh_access_token(env)["access_token"]
    try:
        payload = _linkedin_get("/adAccounts", params={"q": "search"}, access_token=access_token, env=env)
    except Exception:
        payload = _linkedin_get("/adAccounts", access_token=access_token, env=env)

    accounts: list[dict[str, Any]] = []
    for row in payload.get("elements") or []:
        account_id = _normalize_account_id(str(row.get("id") or ""))
        accounts.append(
            {
                "id": account_id,
                "name": row.get("name") or row.get("reference") or "",
                "status": row.get("status") or "",
                "currency": row.get("currency") or "",
                "type": row.get("type") or "",
            }
        )
    return accounts


def _campaign_urns(campaign: dict[str, Any]) -> set[str]:
    raw = str(campaign.get("id") or "")
    raw_no_prefix = raw.split(":")[-1]
    return {raw, raw_no_prefix, f"urn:li:sponsoredCampaign:{raw_no_prefix}"}


def _linkedin_search_pages(
    path: str,
    *,
    base_params: dict[str, Any],
    access_token: str,
    env: LinkedInEnv,
) -> list[dict[str, Any]]:
    """Cursor-paginate a LinkedIn q=search endpoint (metadata.nextPageToken)."""
    rows: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params = dict(base_params)
        if page_token:
            params["pageToken"] = page_token
        payload = _linkedin_get(path, params=params, access_token=access_token, env=env)
        rows.extend(payload.get("elements") or [])
        page_token = (payload.get("metadata") or {}).get("nextPageToken")
        if not page_token:
            break
    return rows


def _fetch_active_campaigns(
    account_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> list[dict[str, Any]]:
    account_id_clean = _normalize_account_id(account_id)
    path = f"/adAccounts/{account_id_clean}/adCampaigns"

    # RestLI parenthesized search=(status:(values:List(ACTIVE))) is rejected on current REST
    # versions — use dotted finder params per LinkedIn docs (202604+).
    search_attempts: list[dict[str, Any]] = [
        {
            "q": "search",
            "search.status.values[0]": "ACTIVE",
            "search.test": "false",
            "pageSize": 1000,
        },
        {
            "q": "search",
            "search.status.values": "ACTIVE",
            "search.test": "false",
            "pageSize": 1000,
        },
    ]

    last_error: Exception | None = None
    for params in search_attempts:
        try:
            campaigns = _linkedin_search_pages(
                path, base_params=params, access_token=access_token, env=env
            )
            return [c for c in campaigns if c.get("status") == "ACTIVE"]
        except Exception as e:
            last_error = e

    if last_error:
        raise last_error
    return []


def _fetch_campaign_analytics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str,
    env: LinkedInEnv,
) -> tuple[list[dict[str, Any]], bool]:
    account_urn = quote(_account_urn(account_id), safe="")
    date_range = _format_date_range(start, end)
    base = (
        f"/adAnalytics?q=analytics"
        f"&pivot=CAMPAIGN"
        f"&timeGranularity=ALL"
        f"&dateRange={date_range}"
        f"&accounts=List({account_urn})"
    )
    with_conversions = f"{base}&fields=pivotValues,impressions,clicks,costInUsd,conversions,conversionValueInUsd"
    fallback = f"{base}&fields=pivotValues,impressions,clicks,costInUsd"

    try:
        payload = _linkedin_get(with_conversions, access_token=access_token, env=env)
        return payload.get("elements") or [], True
    except Exception as primary_error:
        msg = str(primary_error)
        if 'Projected field "conversions"' not in msg:
            raise
        payload = _linkedin_get(fallback, access_token=access_token, env=env)
        return payload.get("elements") or [], False


def account_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> dict[str, Any]:
    env = env or load_linkedin_env()
    access_token = access_token or refresh_access_token(env)["access_token"]
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)
    active_campaigns = _fetch_active_campaigns(account_id_clean, access_token=access_token, env=env)

    empty_totals = {
        "spend": 0.0,
        "clicks": 0,
        "impressions": 0,
        "conversions": 0.0,
        "conversion_value": 0.0,
        "campaign_count": len(active_campaigns),
    }

    if not active_campaigns:
        return {
            "account_id": account_id_clean,
            "date_range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "preset": preset,
            },
            "totals": empty_totals,
            "campaigns": [],
        }

    rows, conversion_fields_supported = _fetch_campaign_analytics(
        account_id_clean,
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )

    active_urns: set[str] = set()
    campaign_by_urn: dict[str, dict[str, Any]] = {}
    for campaign in active_campaigns:
        urns = _campaign_urns(campaign)
        for urn in urns:
            active_urns.add(urn)
            campaign_by_urn[urn] = campaign

    active_rows = [
        row
        for row in rows
        if any(urn in active_urns for urn in (row.get("pivotValues") or []))
    ]

    totals = dict(empty_totals)
    totals["spend"] = sum(_parse_spend(row) for row in active_rows)
    totals["clicks"] = int(sum(int(row.get("clicks") or 0) for row in active_rows))
    totals["impressions"] = int(sum(int(row.get("impressions") or 0) for row in active_rows))
    if conversion_fields_supported:
        totals["conversions"] = sum(_parse_conversions(row) for row in active_rows)
        totals["conversion_value"] = sum(_parse_conversion_value(row) for row in active_rows)

    campaigns_out: list[dict[str, Any]] = []
    seen_campaign_ids: set[str] = set()
    for campaign in active_campaigns:
        cid = str(campaign.get("id") or "").split(":")[-1]
        if cid in seen_campaign_ids:
            continue
        seen_campaign_ids.add(cid)
        urns = _campaign_urns(campaign)
        matched = [
            row
            for row in active_rows
            if any(urn in urns for urn in (row.get("pivotValues") or []))
        ]
        spend = sum(_parse_spend(row) for row in matched)
        clicks = int(sum(int(row.get("clicks") or 0) for row in matched))
        impressions = int(sum(int(row.get("impressions") or 0) for row in matched))
        conversions = (
            sum(_parse_conversions(row) for row in matched) if conversion_fields_supported else 0.0
        )
        campaigns_out.append(
            {
                "id": cid,
                "name": campaign.get("name") or "",
                "status": campaign.get("status") or "",
                "spend": spend,
                "clicks": clicks,
                "impressions": impressions,
                "conversions": conversions,
            }
        )

    return {
        "account_id": account_id_clean,
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "campaigns": campaigns_out,
    }
