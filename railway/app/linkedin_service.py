from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from dates_util import resolve_date_range
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


_LINKEDIN_VERSION_FALLBACKS = ("202509", "202604", "202401", "202309")


def _client_headers(
    access_token: str,
    env: LinkedInEnv | None = None,
    *,
    api_version: str | None = None,
    restli_method: str | None = None,
) -> dict[str, str]:
    env = env or load_linkedin_env()
    version = (api_version or env.version).strip()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": version,
    }
    if restli_method:
        headers["X-RestLi-Method"] = restli_method
    return headers


def _version_candidates(env: LinkedInEnv) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in (env.version, *_LINKEDIN_VERSION_FALLBACKS):
        val = str(raw or "").strip()
        if val and val not in seen:
            seen.add(val)
            ordered.append(val)
    return ordered


def _is_version_resource_not_found(exc: Exception) -> bool:
    msg = str(exc)
    return "404" in msg and ("RESOURCE_NOT_FOUND" in msg or "No virtual resource" in msg)


def _linkedin_get(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    env: LinkedInEnv | None = None,
    api_version: str | None = None,
    restli_method: str | None = None,
) -> dict[str, Any]:
    url = f"{LINKEDIN_API_BASE}{path}"
    with httpx.Client(timeout=120.0) as client:
        response = client.get(
            url,
            params=params,
            headers=_client_headers(
                access_token, env, api_version=api_version, restli_method=restli_method
            ),
        )
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json()
        except Exception:
            pass
        version_note = f" (Linkedin-Version={api_version})" if api_version else ""
        raise RuntimeError(
            f"LinkedIn API error {response.status_code} on {path}{version_note}: {detail}"
        )
    return response.json()


def _linkedin_get_with_versions(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    env: LinkedInEnv | None = None,
    restli_method: str | None = None,
) -> dict[str, Any]:
    env = env or load_linkedin_env()
    last_error: Exception | None = None
    for version in _version_candidates(env):
        try:
            return _linkedin_get(
                path,
                access_token=access_token,
                params=params,
                env=env,
                api_version=version,
                restli_method=restli_method,
            )
        except Exception as exc:
            last_error = exc
            if _is_version_resource_not_found(exc):
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"LinkedIn request failed for {path}")


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


def _campaign_group_id_from_pivot(urn: str) -> str:
    return str(urn or "").strip().split(":")[-1]


def _linkedin_get_all_elements(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """Follow LinkedIn cursor pagination (pageToken / nextPageToken) until exhausted."""
    env = env or load_linkedin_env()
    query = dict(params or {})
    elements: list[dict[str, Any]] = []
    while True:
        payload = _linkedin_get(path, params=query, access_token=access_token, env=env)
        elements.extend(payload.get("elements") or [])
        page_token = (payload.get("metadata") or {}).get("nextPageToken") or (
            (payload.get("paging") or {}).get("pageToken")
        )
        if not page_token:
            break
        query["pageToken"] = page_token
    return elements


def _linkedin_get_paged_elements(
    path: str,
    *,
    access_token: str,
    env: LinkedInEnv | None = None,
    count: int = 100,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Non-search LinkedIn list APIs use start/count pagination."""
    env = env or load_linkedin_env()
    elements: list[dict[str, Any]] = []
    start = 0
    for _ in range(max_pages):
        payload = _linkedin_get(
            path,
            params={"start": start, "count": count},
            access_token=access_token,
            env=env,
        )
        batch = payload.get("elements") or []
        elements.extend(batch)
        if len(batch) < count:
            break
        start += count
    return elements


def _format_run_schedule_value(value: Any) -> str:
    if isinstance(value, dict):
        parts = [value.get("year"), value.get("month"), value.get("day")]
        if all(parts):
            return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
        return ""
    if isinstance(value, (int, float)) and value > 0:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).date().isoformat()
    return ""


def _normalize_campaign_group_row(row: dict[str, Any]) -> dict[str, Any]:
    group_id = _campaign_group_id_from_pivot(str(row.get("id") or ""))
    run_schedule = row.get("runSchedule") if isinstance(row.get("runSchedule"), dict) else {}
    return {
        "id": group_id,
        "name": row.get("name") or "",
        "status": row.get("status") or "",
        "run_schedule_start": _format_run_schedule_value(run_schedule.get("start")),
        "run_schedule_end": _format_run_schedule_value(run_schedule.get("end")),
    }


def _row_belongs_to_account(row: dict[str, Any], account_id: str) -> bool:
    acct = str(row.get("account") or "")
    clean = _normalize_account_id(account_id)
    if not clean:
        return False
    return _normalize_account_id(acct) == clean


def _filter_campaign_groups_for_account(
    rows: list[dict[str, Any]], account_id: str
) -> list[dict[str, Any]]:
    return [row for row in rows if _row_belongs_to_account(row, account_id)]


def _search_global_campaign_groups(
    search_fragment: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> list[dict[str, Any]]:
    """Top-level /adCampaignGroups FINDER search. Omit pageSize — 202509 rejects it on this route."""
    url = f"/adCampaignGroups?q=search&search={search_fragment}"
    query: dict[str, Any] = {}
    elements: list[dict[str, Any]] = []
    while True:
        payload = _linkedin_get_with_versions(
            url, params=query or None, access_token=access_token, env=env
        )
        elements.extend(payload.get("elements") or [])
        page_token = (payload.get("metadata") or {}).get("nextPageToken") or (
            (payload.get("paging") or {}).get("pageToken")
        )
        if not page_token:
            break
        query["pageToken"] = page_token
    return elements


def _search_account_campaign_groups(
    account_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> list[dict[str, Any]]:
    """Account-scoped FINDER search (no pageSize)."""
    account_id_clean = _normalize_account_id(account_id)
    url = (
        f"/adAccounts/{account_id_clean}/adCampaignGroups"
        f"?q=search&search=(status:(values:List(ACTIVE,DRAFT,PAUSED,ARCHIVED,CANCELED)))"
    )
    query: dict[str, Any] = {}
    elements: list[dict[str, Any]] = []
    while True:
        payload = _linkedin_get_with_versions(
            url, params=query or None, access_token=access_token, env=env
        )
        elements.extend(payload.get("elements") or [])
        page_token = (payload.get("metadata") or {}).get("nextPageToken") or (
            (payload.get("paging") or {}).get("pageToken")
        )
        if not page_token:
            break
        query["pageToken"] = page_token
    return elements


def _groups_from_performance_payload(perf: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(g.get("id") or ""),
            "name": g.get("name") or "",
            "status": g.get("status") or "",
            "run_schedule_start": "",
            "run_schedule_end": "",
        }
        for g in perf.get("campaign_groups") or []
        if g.get("id")
    ]


def _batch_get_campaign_groups_by_ids(
    group_ids: list[str],
    *,
    access_token: str,
    env: LinkedInEnv,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    if not group_ids:
        return []
    rows: list[dict[str, Any]] = []
    chunk_size = 50
    for offset in range(0, len(group_ids), chunk_size):
        chunk = group_ids[offset : offset + chunk_size]
        ids_param = "List(" + ",".join(chunk) + ")"
        paths = [f"/adCampaignGroups?ids={ids_param}"]
        if account_id:
            paths.append(
                f"/adAccounts/{_normalize_account_id(account_id)}/adCampaignGroups?ids={ids_param}"
            )
        last_error: Exception | None = None
        for path in paths:
            try:
                payload = _linkedin_get_with_versions(
                    path, access_token=access_token, env=env
                )
                rows.extend((payload.get("results") or {}).values())
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
    return rows


def _stub_campaign_group(group_id: str) -> dict[str, Any]:
    return {
        "id": group_id,
        "name": "",
        "status": "",
        "run_schedule_start": "",
        "run_schedule_end": "",
    }


def _campaign_group_ids_from_analytics(
    account_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
    date_range: str = "LAST_180_DAYS",
) -> list[str]:
    start, end, _ = resolve_date_range(date_range)
    rows, _ = _fetch_analytics(
        account_id,
        pivot="CAMPAIGN_GROUP",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    ids: set[str] = set()
    for row in rows:
        for urn in row.get("pivotValues") or []:
            if "sponsoredCampaignGroup" not in str(urn):
                continue
            gid = _campaign_group_id_from_pivot(urn)
            if gid:
                ids.add(gid)
    return sorted(ids)


def _campaign_group_id_from_campaign_meta(meta: dict[str, Any]) -> str:
    for key in ("campaignGroup", "associatedCampaignGroup", "campaignGroupUrn"):
        val = meta.get(key)
        if val:
            gid = _campaign_group_id_from_pivot(str(val))
            if gid:
                return gid
    return ""


def _campaign_group_context_from_campaign_meta(
    meta: dict[str, Any],
    account_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
    group_name_cache: dict[str, str] | None = None,
) -> dict[str, str]:
    gid = _campaign_group_id_from_campaign_meta(meta)
    if not gid:
        return {"campaign_group_id": "", "campaign_group_name": ""}
    gname = ""
    if group_name_cache is not None and gid in group_name_cache:
        gname = group_name_cache[gid]
    else:
        gmeta = _fetch_campaign_group_by_id(
            account_id, gid, access_token=access_token, env=env
        )
        gname = gmeta.get("name") or ""
        if group_name_cache is not None:
            group_name_cache[gid] = gname
    return {"campaign_group_id": gid, "campaign_group_name": gname}


def _creative_id_from_pivot(urn: str) -> str:
    return str(urn or "").strip().split(":")[-1]


def _campaign_group_ids_from_campaigns(
    account_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
    date_range: str = "LAST_180_DAYS",
) -> list[str]:
    """Discover campaign group IDs via CAMPAIGN analytics + adCampaigns metadata."""
    start, end, _ = resolve_date_range(date_range)
    rows, _ = _fetch_analytics(
        account_id,
        pivot="CAMPAIGN",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    campaign_ids: set[str] = set()
    for row in rows:
        for urn in row.get("pivotValues") or []:
            if "sponsoredCampaign" not in str(urn):
                continue
            cid = _campaign_id_from_pivot(urn)
            if cid:
                campaign_ids.add(cid)

    account_id_clean = _normalize_account_id(account_id)
    group_ids: set[str] = set()
    for cid in campaign_ids:
        meta = _fetch_campaign_by_id(
            account_id_clean, cid, access_token=access_token, env=env
        )
        gid = _campaign_group_id_from_campaign_meta(meta)
        if gid:
            group_ids.add(gid)
    return sorted(group_ids)


def _discover_campaign_group_ids(
    account_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> list[str]:
    ids: set[str] = set()
    for date_range in ("LAST_180_DAYS", "LAST_90_DAYS", "LAST_30_DAYS"):
        for loader in (_campaign_group_ids_from_analytics, _campaign_group_ids_from_campaigns):
            try:
                ids.update(
                    loader(
                        account_id,
                        access_token=access_token,
                        env=env,
                        date_range=date_range,
                    )
                )
            except Exception:
                continue
    return sorted(ids)


def _enrich_campaign_group_ids(
    account_id: str,
    group_ids: list[str],
    *,
    access_token: str,
    env: LinkedInEnv,
) -> list[dict[str, Any]]:
    """Resolve metadata for group IDs; return id-only stubs when LinkedIn metadata APIs fail."""
    if not group_ids:
        return []
    meta_by_id: dict[str, dict[str, Any]] = {}
    try:
        for row in _batch_get_campaign_groups_by_ids(
            group_ids, access_token=access_token, env=env, account_id=account_id
        ):
            normalized = _normalize_campaign_group_row(row)
            if normalized["id"]:
                meta_by_id[normalized["id"]] = normalized
    except Exception:
        for gid in group_ids:
            row = _fetch_campaign_group_by_id(
                account_id, gid, access_token=access_token, env=env
            )
            if row:
                normalized = _normalize_campaign_group_row(row)
                if normalized["id"]:
                    meta_by_id[normalized["id"]] = normalized

    out: list[dict[str, Any]] = []
    for gid in group_ids:
        out.append(meta_by_id.get(gid) or _stub_campaign_group(gid))
    return out


def list_campaign_groups(
    account_id: str,
    *,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """List campaign groups for one ad account."""
    env = env or load_linkedin_env()
    access_token = access_token or refresh_access_token(env)["access_token"]
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    account_urn_encoded = quote(_account_urn(account_id_clean), safe="")
    errors: list[str] = []

    def _finish(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned = [g for g in groups if g.get("id")]
        return sorted(cleaned, key=lambda item: (item.get("name") or item["id"]).lower())

    # 1) Same analytics path as linkedinCampaignGroupsPerformance (no adCampaignGroups FINDER).
    for date_range in ("LAST_180_DAYS", "LAST_90_DAYS", "LAST_30_DAYS"):
        try:
            perf = campaign_groups_performance(
                account_id_clean,
                date_range=date_range,
                access_token=access_token,
                env=env,
            )
            groups = _groups_from_performance_payload(perf)
            if groups:
                return _finish(groups)
        except Exception as exc:
            errors.append(f"performance({date_range}): {exc}")

    # 2) Discover group IDs via analytics + campaign metadata, enrich when possible.
    try:
        discovered_ids = _discover_campaign_group_ids(
            account_id_clean, access_token=access_token, env=env
        )
        if discovered_ids:
            return _finish(
                _enrich_campaign_group_ids(
                    account_id_clean,
                    discovered_ids,
                    access_token=access_token,
                    env=env,
                )
            )
    except Exception as exc:
        errors.append(f"discover: {exc}")

    # 3) Account-scoped FINDER (202509 docs; no pageSize).
    try:
        rows = _search_account_campaign_groups(
            account_id_clean, access_token=access_token, env=env
        )
        groups = [_normalize_campaign_group_row(row) for row in rows if row.get("id")]
        if groups or rows == []:
            return _finish(groups)
    except Exception as exc:
        errors.append(f"account-search: {exc}")

    # 4) Top-level FINDER search with version fallbacks.
    global_searches = [
        f"(account:(values:List({account_urn_encoded})))",
        "(status:(values:List(ACTIVE,DRAFT,PAUSED,ARCHIVED,CANCELED)))",
        "(status:(values:List(ACTIVE,DRAFT)))",
    ]
    for search_fragment in global_searches:
        try:
            rows = _search_global_campaign_groups(
                search_fragment, access_token=access_token, env=env
            )
            if search_fragment.startswith("(status:"):
                rows = _filter_campaign_groups_for_account(rows, account_id_clean)
            groups = [_normalize_campaign_group_row(row) for row in rows if row.get("id")]
            if groups:
                return _finish(groups)
            if rows == [] and search_fragment.startswith("(account:"):
                return []
        except Exception as exc:
            errors.append(f"global-search: {exc}")

    # Return empty list instead of 400 — GPT can use linkedinCampaignGroupsPerformance.
    return []


def _fetch_campaign_group_by_id(
    account_id: str,
    campaign_group_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> dict[str, Any]:
    group_id_clean = _campaign_group_id_from_pivot(campaign_group_id)
    account_id_clean = _normalize_account_id(account_id)
    for path in (
        f"/adCampaignGroups/{group_id_clean}",
        f"/adAccounts/{account_id_clean}/adCampaignGroups/{group_id_clean}",
    ):
        try:
            return _linkedin_get_with_versions(path, access_token=access_token, env=env)
        except Exception:
            continue
    return {}


def _rollup_campaign_rows_to_groups(
    account_id: str,
    campaign_rows: list[dict[str, Any]],
    *,
    access_token: str,
    env: LinkedInEnv,
) -> dict[str, list[dict[str, Any]]]:
    """When CAMPAIGN_GROUP analytics is empty, map CAMPAIGN rows to groups via campaign metadata."""
    account_id_clean = _normalize_account_id(account_id)
    campaign_to_group: dict[str, str] = {}
    by_group: dict[str, list[dict[str, Any]]] = {}

    for row in campaign_rows:
        for urn in row.get("pivotValues") or []:
            if "sponsoredCampaign" not in str(urn):
                continue
            cid = _campaign_id_from_pivot(urn)
            if not cid:
                continue
            if cid not in campaign_to_group:
                meta = _fetch_campaign_by_id(
                    account_id_clean, cid, access_token=access_token, env=env
                )
                gid = _campaign_group_id_from_campaign_meta(meta) or cid
                campaign_to_group[cid] = gid
            by_group.setdefault(campaign_to_group[cid], []).append(row)
    return by_group


def campaign_groups_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> dict[str, Any]:
    """Account totals plus spend/clicks/impressions by campaign group."""
    env = env or load_linkedin_env()
    access_token = access_token or refresh_access_token(env)["access_token"]
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)

    account_rows, account_conversions_ok = _fetch_analytics(
        account_id_clean,
        pivot="ACCOUNT",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    group_rows, group_conversions_ok = _fetch_analytics(
        account_id_clean,
        pivot="CAMPAIGN_GROUP",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    conversion_fields_supported = account_conversions_ok or group_conversions_ok

    account_ins = account_rows[0] if account_rows else {}
    totals = {
        "spend": _parse_spend(account_ins),
        "clicks": int(account_ins.get("clicks") or 0),
        "impressions": int(account_ins.get("impressions") or 0),
        "conversions": _parse_conversions(account_ins) if conversion_fields_supported else 0.0,
        "conversion_value": (
            _parse_conversion_value(account_ins) if conversion_fields_supported else 0.0
        ),
        "campaign_group_count": 0,
    }

    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in group_rows:
        for urn in row.get("pivotValues") or []:
            if "sponsoredCampaignGroup" not in str(urn):
                continue
            gid = _campaign_group_id_from_pivot(urn)
            if gid:
                by_group.setdefault(gid, []).append(row)

    if not by_group:
        campaign_rows, campaign_conversions_ok = _fetch_analytics(
            account_id_clean,
            pivot="CAMPAIGN",
            start=start,
            end=end,
            access_token=access_token,
            env=env,
        )
        conversion_fields_supported = (
            conversion_fields_supported or campaign_conversions_ok
        )
        by_group = _rollup_campaign_rows_to_groups(
            account_id_clean,
            campaign_rows,
            access_token=access_token,
            env=env,
        )

    totals["campaign_group_count"] = len(by_group)

    groups_out: list[dict[str, Any]] = []
    for gid, matched in sorted(by_group.items()):
        meta = _fetch_campaign_group_by_id(
            account_id_clean, gid, access_token=access_token, env=env
        )
        spend = sum(_parse_spend(row) for row in matched)
        clicks = int(sum(int(row.get("clicks") or 0) for row in matched))
        impressions = int(sum(int(row.get("impressions") or 0) for row in matched))
        conversions = (
            sum(_parse_conversions(row) for row in matched)
            if conversion_fields_supported
            else 0.0
        )
        groups_out.append(
            {
                "id": gid,
                "entity_level": "campaign_group",
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
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "campaign_groups": groups_out,
    }


def _date_from_analytics_row(row: dict[str, Any]) -> date | None:
    parts = (row.get("dateRange") or {}).get("start") or (row.get("dateRange") or {})
    if not isinstance(parts, dict):
        return None
    try:
        return date(int(parts["year"]), int(parts["month"]), int(parts["day"]))
    except (KeyError, TypeError, ValueError):
        return None


def _analytics_url(
    *,
    pivot: str,
    account_id: str,
    start: date,
    end: date,
    fields: str,
    time_granularity: str = "ALL",
) -> str:
    account_urn = quote(_account_urn(account_id), safe="")
    date_range = _format_date_range(start, end)
    return (
        f"/adAnalytics?q=analytics"
        f"&pivot={pivot}"
        f"&timeGranularity={time_granularity}"
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


def fetch_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """Account-level metrics per day (for Postgres warehouse / metrics_daily)."""
    env = env or load_linkedin_env()
    access_token = access_token or refresh_access_token(env)["access_token"]
    account_id_clean = _normalize_account_id(account_id)

    with_conversions = _analytics_url(
        pivot="ACCOUNT",
        account_id=account_id_clean,
        start=start,
        end=end,
        time_granularity="DAILY",
        fields="impressions,clicks,costInUsd,conversions,conversionValueInUsd,dateRange",
    )
    fallback = _analytics_url(
        pivot="ACCOUNT",
        account_id=account_id_clean,
        start=start,
        end=end,
        time_granularity="DAILY",
        fields="impressions,clicks,costInUsd,dateRange",
    )

    try:
        payload = _linkedin_get(with_conversions, access_token=access_token, env=env)
        conversion_ok = True
    except Exception as primary_error:
        msg = str(primary_error)
        if 'Projected field "conversions"' not in msg:
            raise
        payload = _linkedin_get(fallback, access_token=access_token, env=env)
        conversion_ok = False

    by_date: dict[str, dict[str, Any]] = {}
    for row in payload.get("elements") or []:
        metric_day = _date_from_analytics_row(row)
        if not metric_day:
            continue
        key = metric_day.isoformat()
        if key not in by_date:
            by_date[key] = {
                "metric_date": key,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            }
        rec = by_date[key]
        rec["spend"] += _parse_spend(row)
        rec["clicks"] += int(row.get("clicks") or 0)
        rec["impressions"] += int(row.get("impressions") or 0)
        if conversion_ok:
            rec["conversions"] += _parse_conversions(row)
            rec["conversion_value"] += _parse_conversion_value(row)

    out: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        out.append(
            by_date.get(key)
            or {
                "metric_date": key,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            }
        )
        cursor += timedelta(days=1)
    return out


def sync_account_to_warehouse(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> dict[str, Any]:
    """Pull daily LinkedIn metrics and upsert into metrics_daily."""
    import warehouse

    if not warehouse.enabled():
        raise RuntimeError("DATABASE_URL is not set — warehouse storage is disabled.")

    start, end, preset = resolve_date_range(date_range)
    daily_rows = fetch_daily_metrics(
        account_id,
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    account_id_clean = _normalize_account_id(account_id)
    written = warehouse.upsert_metrics_daily_batch("linkedin", account_id_clean, daily_rows)
    coverage = warehouse.account_date_coverage("linkedin", account_id_clean)
    return {
        "account_id": account_id_clean,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "days_synced": written,
        "coverage": coverage,
    }


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


def _fetch_creative_by_id(
    account_id: str,
    creative_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> dict[str, Any]:
    account_id_clean = _normalize_account_id(account_id)
    creative_id_clean = _creative_id_from_pivot(creative_id)
    creative_urn = quote(f"urn:li:sponsoredCreative:{creative_id_clean}", safe="")
    try:
        return _linkedin_get(
            f"/adAccounts/{account_id_clean}/creatives/{creative_urn}",
            access_token=access_token,
            env=env,
        )
    except Exception:
        return {}


def creatives_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    campaign_id: str | None = None,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> dict[str, Any]:
    """
    Creative-level metrics (LinkedIn has no ad set — this is the level below campaign).
    Use for ad/creative dashboards; do not roll into campaign totals by name.
    """
    env = env or load_linkedin_env()
    access_token = access_token or refresh_access_token(env)["access_token"]
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)
    creative_rows, conversion_ok = _fetch_analytics(
        account_id_clean,
        pivot="CREATIVE",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )

    by_creative: dict[str, list[dict[str, Any]]] = {}
    for row in creative_rows:
        for urn in row.get("pivotValues") or []:
            if "sponsoredCreative" not in str(urn):
                continue
            crid = _creative_id_from_pivot(urn)
            if crid:
                by_creative.setdefault(crid, []).append(row)

    group_name_cache: dict[str, str] = {}
    creatives_out: list[dict[str, Any]] = []
    filter_campaign = _normalize_account_id(campaign_id) if campaign_id else ""

    for crid, matched in sorted(by_creative.items()):
        meta = _fetch_creative_by_id(
            account_id_clean, crid, access_token=access_token, env=env
        )
        campaign_urn = str(meta.get("campaign") or "")
        cid = _campaign_id_from_pivot(campaign_urn) if campaign_urn else ""
        if filter_campaign and cid != filter_campaign:
            continue

        cname = ""
        group_ctx = {"campaign_group_id": "", "campaign_group_name": ""}
        if cid:
            cmeta = _fetch_campaign_by_id(
                account_id_clean, cid, access_token=access_token, env=env
            )
            cname = cmeta.get("name") or ""
            group_ctx = _campaign_group_context_from_campaign_meta(
                cmeta,
                account_id_clean,
                access_token=access_token,
                env=env,
                group_name_cache=group_name_cache,
            )

        spend = sum(_parse_spend(row) for row in matched)
        clicks = int(sum(int(row.get("clicks") or 0) for row in matched))
        impressions = int(sum(int(row.get("impressions") or 0) for row in matched))
        conversions = sum(_parse_conversions(row) for row in matched) if conversion_ok else 0.0

        creatives_out.append(
            {
                "id": crid,
                "entity_level": "creative",
                "name": meta.get("name") or meta.get("intendedStatus") or "",
                "status": meta.get("intendedStatus") or meta.get("status") or "",
                "campaign_id": cid,
                "campaign_name": cname,
                "campaign_group_id": group_ctx["campaign_group_id"],
                "campaign_group_name": group_ctx["campaign_group_name"],
                "spend": spend,
                "clicks": clicks,
                "impressions": impressions,
                "conversions": conversions,
            }
        )

    totals = {
        "spend": sum(c["spend"] for c in creatives_out),
        "clicks": sum(c["clicks"] for c in creatives_out),
        "impressions": sum(c["impressions"] for c in creatives_out),
        "conversions": sum(c["conversions"] for c in creatives_out),
        "creative_count": len(creatives_out),
    }

    return {
        "account_id": account_id_clean,
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "creatives": creatives_out,
    }


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
    group_name_cache: dict[str, str] = {}
    for cid, matched in sorted(by_campaign.items()):
        meta = _fetch_campaign_by_id(
            account_id_clean, cid, access_token=access_token, env=env
        )
        group_ctx = _campaign_group_context_from_campaign_meta(
            meta,
            account_id_clean,
            access_token=access_token,
            env=env,
            group_name_cache=group_name_cache,
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
                "entity_level": "campaign",
                "name": meta.get("name") or "",
                "status": meta.get("status") or "",
                "campaign_group_id": group_ctx["campaign_group_id"],
                "campaign_group_name": group_ctx["campaign_group_name"],
                "spend": spend,
                "clicks": clicks,
                "impressions": impressions,
                "conversions": conversions,
            }
        )

    result = {
        "account_id": account_id_clean,
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "campaigns": campaigns_out,
    }

    try:
        import warehouse

        if warehouse.enabled():
            sync_meta = sync_account_to_warehouse(
                account_id_clean,
                date_range=preset,
                access_token=access_token,
                env=env,
            )
            result["warehouse"] = {
                "stored": True,
                "days_synced": sync_meta["days_synced"],
                "coverage": sync_meta["coverage"],
            }
    except Exception as exc:
        result["warehouse"] = {"stored": False, "error": str(exc)[:500]}

    return result


def _linkedin_finder_get_all_elements(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """Cursor-paginated FINDER (q=criteria) requests."""
    query = dict(params or {})
    elements: list[dict[str, Any]] = []
    while True:
        payload = _linkedin_get_with_versions(
            path,
            access_token=access_token,
            params=query,
            env=env,
            restli_method="FINDER",
        )
        elements.extend(payload.get("elements") or [])
        page_token = (payload.get("metadata") or {}).get("nextPageToken") or (
            (payload.get("paging") or {}).get("pageToken")
        )
        if not page_token:
            break
        query["pageToken"] = page_token
    return elements


def _collect_media_urns(node: Any, *, videos: set[str], images: set[str]) -> None:
    if isinstance(node, str):
        text = node.strip()
        if not text.startswith("urn:li:"):
            return
        lower = text.lower()
        if ":video:" in lower:
            videos.add(text)
        elif ":image:" in lower or ":digitalmediaasset:" in lower:
            images.add(text)
        return
    if isinstance(node, dict):
        for value in node.values():
            _collect_media_urns(value, videos=videos, images=images)
    elif isinstance(node, list):
        for value in node:
            _collect_media_urns(value, videos=videos, images=images)


def _fetch_linkedin_post_content(
    reference: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> dict[str, Any]:
    ref = str(reference or "").strip()
    if not ref:
        return {}
    encoded = quote(ref, safe="")
    for path in (f"/posts/{encoded}",):
        try:
            return _linkedin_get_with_versions(path, access_token=access_token, env=env)
        except Exception:
            continue
    if "ugcPost" in ref:
        post_id = ref.split(":")[-1]
        try:
            return _linkedin_get_with_versions(
                f"/ugcPosts/{post_id}", access_token=access_token, env=env
            )
        except Exception:
            return {}
    return {}


def _fetch_linkedin_video_asset(
    video_urn: str,
    *,
    access_token: str,
    env: LinkedInEnv,
    cache: dict[str, dict[str, str]],
) -> dict[str, str]:
    if video_urn in cache:
        return cache[video_urn]
    encoded = quote(video_urn, safe="")
    out = {"video_urn": video_urn, "video_url": "", "thumbnail_url": ""}
    try:
        data = _linkedin_get_with_versions(
            f"/videos/{encoded}", access_token=access_token, env=env
        )
        out["video_url"] = str(data.get("downloadUrl") or "")
        out["thumbnail_url"] = str(data.get("thumbnail") or "")
    except Exception:
        pass
    cache[video_urn] = out
    return out


def _fetch_linkedin_image_asset(
    image_urn: str,
    *,
    access_token: str,
    env: LinkedInEnv,
    cache: dict[str, dict[str, str]],
) -> dict[str, str]:
    if image_urn in cache:
        return cache[image_urn]
    encoded = quote(image_urn, safe="")
    out = {"image_urn": image_urn, "image_url": "", "thumbnail_url": ""}
    try:
        data = _linkedin_get_with_versions(
            f"/images/{encoded}", access_token=access_token, env=env
        )
        url = str(data.get("downloadUrl") or "")
        out["image_url"] = url
        out["thumbnail_url"] = url
    except Exception:
        pass
    cache[image_urn] = out
    return out


def _list_creatives_for_account(
    account_id: str,
    *,
    campaign_id: str | None = None,
    access_token: str,
    env: LinkedInEnv,
) -> list[dict[str, Any]]:
    account_id_clean = _normalize_account_id(account_id)
    params: dict[str, Any] = {"q": "criteria", "pageSize": 100}
    if campaign_id:
        cid = _normalize_account_id(campaign_id)
        params["campaigns"] = f"List(urn:li:sponsoredCampaign:{cid})"

    try:
        rows = _linkedin_finder_get_all_elements(
            f"/adAccounts/{account_id_clean}/creatives",
            access_token=access_token,
            params=params,
            env=env,
        )
        if rows:
            return rows
    except Exception:
        pass

    # Fallback: discover creatives with recent delivery via analytics.
    start, end, _ = resolve_date_range("LAST_180_DAYS")
    analytics_rows, _ = _fetch_analytics(
        account_id_clean,
        pivot="CREATIVE",
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    creative_ids: set[str] = set()
    for row in analytics_rows:
        for urn in row.get("pivotValues") or []:
            if "sponsoredCreative" not in str(urn):
                continue
            crid = _creative_id_from_pivot(urn)
            if crid:
                creative_ids.add(crid)

    out: list[dict[str, Any]] = []
    for crid in sorted(creative_ids):
        meta = _fetch_creative_by_id(
            account_id_clean, crid, access_token=access_token, env=env
        )
        if meta:
            out.append(meta)
    return out


def list_video_creatives(
    account_id: str,
    *,
    campaign_id: str | None = None,
    videos_only: bool = True,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> dict[str, Any]:
    """
    Video/image preview URLs for LinkedIn ad creatives.
    Resolves video thumbnail + downloadUrl via Videos API and images via Images API.
    """
    env = env or load_linkedin_env()
    access_token = access_token or refresh_access_token(env)["access_token"]
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    filter_campaign = _normalize_account_id(campaign_id) if campaign_id else ""
    creatives = _list_creatives_for_account(
        account_id_clean,
        campaign_id=filter_campaign or None,
        access_token=access_token,
        env=env,
    )

    video_cache: dict[str, dict[str, str]] = {}
    image_cache: dict[str, dict[str, str]] = {}
    campaign_name_cache: dict[str, str] = {}
    videos_out: list[dict[str, Any]] = []

    for creative in creatives:
        crid = _creative_id_from_pivot(str(creative.get("id") or ""))
        if not crid:
            continue

        campaign_urn = str(creative.get("campaign") or "")
        cid = _campaign_id_from_pivot(campaign_urn) if campaign_urn else ""
        if filter_campaign and cid != filter_campaign:
            continue

        cname = ""
        if cid:
            if cid in campaign_name_cache:
                cname = campaign_name_cache[cid]
            else:
                cmeta = _fetch_campaign_by_id(
                    account_id_clean, cid, access_token=access_token, env=env
                )
                cname = cmeta.get("name") or ""
                campaign_name_cache[cid] = cname

        content = creative.get("content") if isinstance(creative.get("content"), dict) else {}
        inline = (
            creative.get("inlineContent")
            if isinstance(creative.get("inlineContent"), dict)
            else {}
        )
        videos: set[str] = set()
        images: set[str] = set()
        _collect_media_urns(content, videos=videos, images=images)
        _collect_media_urns(inline, videos=videos, images=images)

        ref = str(content.get("reference") or "")
        if ref and not videos and not images:
            post = _fetch_linkedin_post_content(
                ref, access_token=access_token, env=env
            )
            _collect_media_urns(post, videos=videos, images=images)

        if videos:
            for video_urn in sorted(videos):
                asset = _fetch_linkedin_video_asset(
                    video_urn,
                    access_token=access_token,
                    env=env,
                    cache=video_cache,
                )
                videos_out.append(
                    {
                        "source": "linkedin_creative",
                        "creative_id": crid,
                        "creative_name": creative.get("name") or "",
                        "creative_status": creative.get("intendedStatus") or "",
                        "campaign_id": cid,
                        "campaign_name": cname,
                        "media_type": "video",
                        "video_urn": video_urn,
                        "video_url": asset.get("video_url") or "",
                        "thumbnail_url": asset.get("thumbnail_url") or "",
                        "image_url": "",
                    }
                )
            continue

        if videos_only:
            continue

        for image_urn in sorted(images):
            asset = _fetch_linkedin_image_asset(
                image_urn,
                access_token=access_token,
                env=env,
                cache=image_cache,
            )
            videos_out.append(
                {
                    "source": "linkedin_creative",
                    "creative_id": crid,
                    "creative_name": creative.get("name") or "",
                    "creative_status": creative.get("intendedStatus") or "",
                    "campaign_id": cid,
                    "campaign_name": cname,
                    "media_type": "image",
                    "video_urn": "",
                    "video_url": "",
                    "thumbnail_url": asset.get("thumbnail_url") or "",
                    "image_url": asset.get("image_url") or "",
                }
            )

    videos_out.sort(
        key=lambda row: (
            str(row.get("campaign_name") or ""),
            str(row.get("creative_name") or ""),
            str(row.get("creative_id") or ""),
        )
    )

    return {
        "account_id": account_id_clean,
        "row_count": len(videos_out),
        "videos": videos_out,
    }
