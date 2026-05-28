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


def _campaign_id_from_pivot(urn: str) -> str:
    return str(urn or "").strip().split(":")[-1]


def _analytics_url(
    *,
    pivot: str,
    account_id: str,
    start: date,
    end: date,
    fields: str,
) -> str:
    account_urn = quote(_account_urn(account_id), safe="")
    date_range = _format_date_range(start, end)
    return (
        f"/adAnalytics?q=analytics"
        f"&pivot={pivot}"
        f"&timeGranularity=ALL"
        f"&dateRange={date_range}"
        f"&accounts=List({account_urn})"
        f"&fields={fields}"
    )


def _fetch_analytics(
    account_id: str,
    *,
    pivot: str,
    start: date,
    end: date,
    access_token: str,
    env: LinkedInEnv,
) -> tuple[list[dict[str, Any]], bool]:
    """Load adAnalytics for ACCOUNT or CAMPAIGN pivot (conversions field optional)."""
    with_conversions = _analytics_url(
        pivot=pivot,
        account_id=account_id,
        start=start,
        end=end,
        fields="impressions,clicks,costInUsd,conversions,conversionValueInUsd,pivotValues",
    )
    fallback = _analytics_url(
        pivot=pivot,
        account_id=account_id,
        start=start,
        end=end,
        fields="impressions,clicks,costInUsd,pivotValues",
    )
    if pivot == "ACCOUNT":
        # Account pivot does not use pivotValues in the same way.
        with_conversions = _analytics_url(
            pivot=pivot,
            account_id=account_id,
            start=start,
            end=end,
            fields="impressions,clicks,costInUsd,conversions,conversionValueInUsd",
        )
        fallback = _analytics_url(
            pivot=pivot,
            account_id=account_id,
            start=start,
            end=end,
            fields="impressions,clicks,costInUsd",
        )

    try:
        payload = _linkedin_get(with_conversions, access_token=access_token, env=env)
        return payload.get("elements") or [], True
    except Exception as primary_error:
        msg = str(primary_error)
        if 'Projected field "conversions"' not in msg:
            raise
        payload = _linkedin_get(fallback, access_token=access_token, env=env)
        return payload.get("elements") or [], False


def _fetch_campaign_by_id(
    account_id: str,
    campaign_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> dict[str, Any]:
    account_id_clean = _normalize_account_id(account_id)
    campaign_id_clean = _campaign_id_from_pivot(campaign_id)
    try:
        return _linkedin_get(
            f"/adAccounts/{account_id_clean}/adCampaigns/{campaign_id_clean}",
            access_token=access_token,
            env=env,
        )
    except Exception:
        return {}


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

    # Avoid adCampaigns?q=search — 202604 rejects both RestLI `search=(...)` and dotted
    # search.status.values / search.test params. Use adAnalytics only.
    account_rows, account_conversions_ok = _fetch_analytics(
        account_id_clean,
        pivot="ACCOUNT",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    campaign_rows, campaign_conversions_ok = _fetch_analytics(
        account_id_clean,
        pivot="CAMPAIGN",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    conversion_fields_supported = account_conversions_ok or campaign_conversions_ok

    account_ins = account_rows[0] if account_rows else {}
    totals = {
        "spend": _parse_spend(account_ins),
        "clicks": int(account_ins.get("clicks") or 0),
        "impressions": int(account_ins.get("impressions") or 0),
        "conversions": _parse_conversions(account_ins) if conversion_fields_supported else 0.0,
        "conversion_value": (
            _parse_conversion_value(account_ins) if conversion_fields_supported else 0.0
        ),
        "campaign_count": 0,
    }

    by_campaign: dict[str, list[dict[str, Any]]] = {}
    for row in campaign_rows:
        for urn in row.get("pivotValues") or []:
            if "sponsoredCampaign" not in str(urn):
                continue
            cid = _campaign_id_from_pivot(urn)
            if cid:
                by_campaign.setdefault(cid, []).append(row)

    totals["campaign_count"] = len(by_campaign)

    campaigns_out: list[dict[str, Any]] = []
    for cid, matched in sorted(by_campaign.items()):
        meta = _fetch_campaign_by_id(
            account_id_clean, cid, access_token=access_token, env=env
        )
        spend = sum(_parse_spend(row) for row in matched)
        clicks = int(sum(int(row.get("clicks") or 0) for row in matched))
        impressions = int(sum(int(row.get("impressions") or 0) for row in matched))
        conversions = (
            sum(_parse_conversions(row) for row in matched)
            if conversion_fields_supported
            else 0.0
        )
        campaigns_out.append(
            {
                "id": cid,
                "name": meta.get("name") or "",
                "status": meta.get("status") or "",
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
