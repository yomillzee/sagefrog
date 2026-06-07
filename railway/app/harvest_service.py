"""Harvest time tracking API (OAuth2 + v2 REST)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from harvest_auth import HarvestEnv, load_harvest_env

HARVEST_ID_BASE = "https://id.getharvest.com"
HARVEST_TOKEN_URL = f"{HARVEST_ID_BASE}/api/v2/oauth2/token"
HARVEST_API_BASE = "https://api.harvestapp.com/v2"
HARVEST_USER_AGENT = "Sagefrog Dashboard (sagefrog-production.up.railway.app)"


def _platform_error(exc: Exception) -> str:
    return str(exc)[:500]


def refresh_access_token(env: HarvestEnv | None = None) -> dict[str, Any]:
    env = env or load_harvest_env()
    body = {
        "grant_type": "refresh_token",
        "refresh_token": env.refresh_token,
        "client_id": env.client_id,
        "client_secret": env.client_secret,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            HARVEST_TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": HARVEST_USER_AGENT,
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Harvest OAuth refresh failed ({response.status_code}): {response.text[:500]}"
        )
    data = response.json()
    if not data.get("access_token"):
        raise RuntimeError("Harvest OAuth refresh returned no access_token.")
    return data


def _resolve_account_id(access_token: str) -> str:
    import oauth_store

    pub = oauth_store.public_status("harvest")
    meta = pub.metadata or {}
    account_id = str(meta.get("harvest_account_id") or "").strip()
    if account_id:
        return account_id
    accounts = list_accessible_accounts(access_token=access_token)
    if not accounts:
        raise RuntimeError("Harvest token has no accessible accounts.")
    account_id = str(accounts[0].get("id") or "")
    if not account_id:
        raise RuntimeError("Harvest account id missing from accounts response.")
    return account_id


def list_accessible_accounts(*, access_token: str) -> list[dict[str, Any]]:
    with httpx.Client(timeout=60.0) as client:
        response = client.get(
            f"{HARVEST_ID_BASE}/v2/accounts",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": HARVEST_USER_AGENT,
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Harvest accounts list failed ({response.status_code}): {response.text[:500]}"
        )
    payload = response.json()
    accounts = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(accounts, list):
        return []
    return accounts


def resolve_auth_context(env: HarvestEnv | None = None) -> tuple[str, str]:
    """Return (access_token, harvest_account_id)."""
    token_data = refresh_access_token(env)
    access_token = str(token_data["access_token"])
    account_id = _resolve_account_id(access_token)
    return access_token, account_id


def _api_headers(access_token: str, account_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Harvest-Account-Id": str(account_id),
        "User-Agent": HARVEST_USER_AGENT,
    }


def _harvest_get(
    path: str,
    *,
    access_token: str,
    account_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{HARVEST_API_BASE}{path}"
    with httpx.Client(timeout=120.0) as client:
        response = client.get(
            url,
            params=params,
            headers=_api_headers(access_token, account_id),
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Harvest API {path} failed ({response.status_code}): {response.text[:500]}")
    return response.json()


def _paginate(
    path: str,
    *,
    access_token: str,
    account_id: str,
    params: dict[str, Any] | None = None,
    key: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    base_params = dict(params or {})
    while True:
        payload = _harvest_get(
            path,
            access_token=access_token,
            account_id=account_id,
            params={**base_params, "page": page, "per_page": 100},
        )
        rows = payload.get(key) or []
        if not isinstance(rows, list):
            break
        out.extend(rows)
        next_page = payload.get("next_page")
        total_pages = int(payload.get("total_pages") or 0)
        if not next_page and (not total_pages or page >= total_pages):
            break
        page += 1
        if page > 50:
            break
    return out


def test_refresh_token(env: HarvestEnv | None = None) -> dict[str, Any]:
    try:
        access_token, _account_id = resolve_auth_context(env)
        projects = list_projects(access_token=access_token, account_id=_account_id)
        return {
            "ok": True,
            "message": "Harvest OAuth refresh succeeded.",
            "project_count": len(projects),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": _platform_error(exc),
            "project_count": 0,
            "error": _platform_error(exc),
        }


def list_projects(
    *,
    access_token: str | None = None,
    account_id: str | None = None,
    env: HarvestEnv | None = None,
) -> list[dict[str, Any]]:
    if not access_token or not account_id:
        access_token, account_id = resolve_auth_context(env)
    rows = _paginate(
        "/projects",
        access_token=access_token,
        account_id=account_id,
        params={"is_active": "true"},
        key="projects",
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        client = row.get("client") or {}
        out.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or "Project"),
                "code": str(row.get("code") or ""),
                "client_name": str(client.get("name") or ""),
                "is_active": bool(row.get("is_active", True)),
            }
        )
    out.sort(key=lambda r: (r.get("client_name") or "", r.get("name") or ""))
    return out


def fetch_time_entries(
    *,
    project_id: str,
    from_date: date,
    to_date: date,
    billable: bool | None = True,
    access_token: str | None = None,
    account_id: str | None = None,
    env: HarvestEnv | None = None,
) -> list[dict[str, Any]]:
    if not access_token or not account_id:
        access_token, account_id = resolve_auth_context(env)
    params: dict[str, Any] = {
        "project_id": int(project_id),
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    }
    if billable is not None:
        params["billable"] = "true" if billable else "false"
    return _paginate(
        "/time_entries",
        access_token=access_token,
        account_id=account_id,
        params=params,
        key="time_entries",
    )


def month_to_date_range(*, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today.replace(day=1)
    return start, today


def fetch_billable_mtd_report(project_id: str, *, env: HarvestEnv | None = None) -> dict[str, Any]:
    start, end = month_to_date_range()
    access_token, account_id = resolve_auth_context(env)
    projects = list_projects(access_token=access_token, account_id=account_id, env=env)
    project_name = next(
        (p.get("name") for p in projects if str(p.get("id")) == str(project_id)),
        f"Project {project_id}",
    )
    entries = fetch_time_entries(
        project_id=project_id,
        from_date=start,
        to_date=end,
        billable=True,
        access_token=access_token,
        account_id=account_id,
        env=env,
    )
    by_date: dict[str, float] = {}
    total = 0.0
    for entry in entries:
        spent = str(entry.get("spent_date") or "")[:10]
        hours = float(entry.get("hours") or 0)
        if not spent:
            continue
        by_date[spent] = by_date.get(spent, 0.0) + hours
        total += hours

    dates: list[str] = []
    cursor = start
    while cursor <= end:
        dates.append(cursor.isoformat())
        cursor += timedelta(days=1)

    series = [{"date": d, "hours": round(by_date.get(d, 0.0), 2)} for d in dates]
    return {
        "project_id": str(project_id),
        "project_name": project_name,
        "total_billable_hours": round(total, 2),
        "entry_count": len(entries),
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "by_date": series,
    }


def fetch_accounts_for_oauth_metadata(access_token: str) -> dict[str, Any]:
    accounts = list_accessible_accounts(access_token=access_token)
    ids = [str(a.get("id") or "") for a in accounts if a.get("id")]
    primary = ids[0] if ids else None
    return {
        "harvest_account_id": primary,
        "harvest_account_ids": ids,
        "harvest_account_count": len(ids),
    }
