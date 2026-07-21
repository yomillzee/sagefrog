"""Harvest time-tracking reads: hours by client for the current month.

Harvest is connected once, agency-wide (one Harvest account holds every client),
so unlike the per-client BigQuery connectors this reads live from the Harvest API
on demand — the same shape as the other admin overview pages. The agency-wide
OAuth token lives in ``oauth_store`` under platform ``harvest`` with
``client_slug=''``; its metadata carries the Harvest account id captured during
the connect flow (``oauth_flows.fetch_harvest_accounts``).

``build_client_hours_overview`` returns, for the current calendar month, each
client's cumulative hours-per-day series plus an optional monthly hours goal, so
the Client Hours page can draw a burn-up chart per client (actual vs goal pace).
Per-client monthly goals are stored here in a small Postgres table, keyed by
Harvest client id, and edited inline on that page.
"""

from __future__ import annotations

import calendar
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

import db
import db_cache
import oauth_flows
import oauth_store

_log = logging.getLogger(__name__)

HARVEST_API_BASE = "https://api.harvestapp.com/v2"
_PLATFORM = "harvest"
# Safety cap: current-month time entries for one agency should be well under this
# many pages (100 entries each); the cap only guards against a runaway loop.
_MAX_PAGES = 60
# The Harvest hours pull is cached in Postgres (api_cache) so repeated page loads
# and multiple admins don't each hit the Harvest API — which rate-limits at 100
# requests / 15s. Within the TTL every request is served from cache; goals are
# applied fresh on top so editing a goal is always instant. Override with
# HARVEST_CACHE_TTL_SECONDS; set it to 0 to disable caching.
_CACHE_SOURCE = "harvest.hours"
_DEFAULT_CACHE_TTL = 900  # 15 minutes


def _cache_ttl_seconds() -> int:
    raw = (os.getenv("HARVEST_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_CACHE_TTL
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_CACHE_TTL

GOALS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS harvest_client_goals (
  harvest_client_id TEXT PRIMARY KEY,
  client_name       TEXT,
  monthly_goal      NUMERIC,
  updated_by        TEXT,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def is_connected() -> bool:
    try:
        return bool(oauth_store.get_refresh_token(_PLATFORM))
    except Exception:
        return False


def _account_id() -> str | None:
    """Harvest account id for the data-API header.

    Prefers an explicit HARVEST_ACCOUNT_ID override, then the id captured in the
    stored token's metadata during connect.
    """
    override = (os.getenv("HARVEST_ACCOUNT_ID") or "").strip()
    if override:
        return override
    try:
        pub = oauth_store.public_status(_PLATFORM)
        acct = str((pub.metadata or {}).get("account_id") or "").strip()
        return acct or None
    except Exception:
        return None


def _access_token() -> str:
    refresh = oauth_store.get_refresh_token(_PLATFORM)
    if not refresh:
        raise RuntimeError(
            "Harvest is not connected. Connect it on the Admin page "
            "(Platform connections → Harvest)."
        )
    return oauth_flows.refresh_harvest_access_token(refresh)


def _api_headers(access_token: str, account_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Harvest-Account-Id": account_id,
        "User-Agent": "Sagefrog Analytics (mjmillss@gmail.com)",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Time entries → cumulative hours by client
# ---------------------------------------------------------------------------


def _month_bounds(today: date) -> tuple[date, int, int]:
    """Return (first_of_month, days_in_month, days_elapsed_through_today)."""
    first = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    return first, days_in_month, days_elapsed


def _fetch_month_entries(
    *, access_token: str, account_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    """All time entries with spent_date in [start, end], following pagination."""
    entries: list[dict[str, Any]] = []
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "per_page": 100,
        "page": 1,
    }
    headers = _api_headers(access_token, account_id)
    with httpx.Client(timeout=60.0, headers=headers) as client:
        for _ in range(_MAX_PAGES):
            resp = client.get(f"{HARVEST_API_BASE}/time_entries", params=params)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Harvest time_entries read failed ({resp.status_code}): {resp.text[:300]}"
                )
            data = resp.json()
            entries.extend(data.get("time_entries") or [])
            total_pages = int(data.get("total_pages") or 1)
            page = int(data.get("page") or 1)
            if page >= total_pages:
                break
            params["page"] = page + 1
    return entries


def _cumulative_by_client(
    entries: list[dict[str, Any]], *, first: date, days_elapsed: int
) -> dict[str, dict[str, Any]]:
    """Fold raw entries into ``{harvest_client_id: {name, daily[day]->hours}}``."""
    by_client: dict[str, dict[str, Any]] = {}
    for e in entries:
        client = e.get("client") or {}
        cid = str(client.get("id") or "").strip()
        if not cid:
            cid = "0"
        name = str(client.get("name") or "").strip() or "(No client)"
        spent = str(e.get("spent_date") or "").strip()
        try:
            hours = float(e.get("hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        try:
            d = date.fromisoformat(spent)
        except ValueError:
            continue
        if d < first:
            continue
        day_idx = d.day  # 1-based day of month
        if day_idx > days_elapsed:
            continue
        slot = by_client.setdefault(cid, {"name": name, "daily": {}})
        slot["name"] = name
        slot["daily"][day_idx] = slot["daily"].get(day_idx, 0.0) + hours
    return by_client


def _build_client_series(
    *, account_id: str, first: date, today: date, days_elapsed: int
) -> list[dict[str, Any]]:
    """Pull the month's time entries from Harvest and fold them into a per-client
    cumulative-hours series (no goals applied). This is the expensive, cacheable
    part: one token refresh + paginated time_entries reads."""
    access_token = _access_token()
    entries = _fetch_month_entries(
        access_token=access_token, account_id=account_id, start=first, end=today
    )
    by_client = _cumulative_by_client(entries, first=first, days_elapsed=days_elapsed)
    clients: list[dict[str, Any]] = []
    for cid, slot in by_client.items():
        daily = slot["daily"]
        running = 0.0
        series: list[float] = []
        for day in range(1, days_elapsed + 1):
            running += daily.get(day, 0.0)
            series.append(round(running, 2))
        clients.append(
            {
                "harvest_client_id": cid,
                "name": slot["name"],
                "total_hours": round(running, 2),
                "series": series,
            }
        )
    clients.sort(key=lambda c: c["total_hours"], reverse=True)
    return clients


def _load_client_series(
    *, account_id: str, first: date, today: date, days_elapsed: int, use_cache: bool
) -> tuple[list[dict[str, Any]], str | None]:
    """Return (clients_series, refreshed_at_iso). Served from the Postgres cache
    within the TTL so repeated loads don't re-hit Harvest; on a miss it pulls
    fresh and stores the result. ``use_cache=False`` forces a fresh pull (the
    page's manual refresh)."""
    ttl = _cache_ttl_seconds()
    # Key by account + calendar day: the same day reuses one pull (TTL bounds
    # intraday freshness); a new day naturally starts a new cache entry.
    payload = {
        "account_id": account_id,
        "year": today.year,
        "month": today.month,
        "day": days_elapsed,
    }
    if use_cache and ttl > 0:
        try:
            hit = db_cache.get_cached(_CACHE_SOURCE, payload)
        except Exception as exc:
            _log.warning("Harvest cache read failed: %s", exc)
            hit = None
        if hit and isinstance(hit.response_json, list):
            created = getattr(hit, "created_at", None)
            return hit.response_json, (created.isoformat() if created else None)

    clients = _build_client_series(
        account_id=account_id, first=first, today=today, days_elapsed=days_elapsed
    )
    if ttl > 0:
        try:
            db_cache.put_cached(
                _CACHE_SOURCE, payload,
                response_json=clients, row_count=len(clients), ttl_seconds=ttl,
            )
        except Exception as exc:
            _log.warning("Harvest cache write failed: %s", exc)
    return clients, datetime.now(tz=UTC).isoformat()


def build_client_hours_overview(
    *, today: date | None = None, use_cache: bool = True
) -> dict[str, Any]:
    """Current-month hours-by-client for the Client Hours admin page.

    The Harvest time-entry pull is cached (see _load_client_series); per-client
    goals are applied fresh on every call so goal edits show immediately without
    a Harvest round-trip. Pass ``use_cache=False`` to force a fresh pull.

    Shape (JSON-serialisable):
      {
        connected, account_id, account_name,
        month_label, year, month, days_in_month, days_elapsed, as_of,
        refreshed_at, cache_ttl_seconds,
        clients: [ { harvest_client_id, name, goal,
                     total_hours, series:[cumhours per elapsed day] } ],
        totals: { total_hours, goal },
        error?: str,
      }
    """
    today = today or _today()
    first, days_in_month, days_elapsed = _month_bounds(today)
    month_label = f"{calendar.month_name[today.month]} {today.year}"
    base: dict[str, Any] = {
        "connected": is_connected(),
        "account_id": _account_id(),
        "account_name": None,
        "month_label": month_label,
        "year": today.year,
        "month": today.month,
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "as_of": today.isoformat(),
        "refreshed_at": None,
        "cache_ttl_seconds": _cache_ttl_seconds(),
        "clients": [],
        "totals": {"total_hours": 0.0, "goal": 0.0},
    }
    try:
        pub = oauth_store.public_status(_PLATFORM)
        base["account_name"] = (pub.metadata or {}).get("account_name")
    except Exception:
        pass

    if not base["connected"]:
        base["error"] = "Harvest is not connected."
        return base
    account_id = base["account_id"]
    if not account_id:
        base["error"] = (
            "Harvest is connected but no account id is available. Set HARVEST_ACCOUNT_ID, "
            "or reconnect Harvest so the account id is captured."
        )
        return base

    try:
        series_clients, refreshed_at = _load_client_series(
            account_id=account_id, first=first, today=today,
            days_elapsed=days_elapsed, use_cache=use_cache,
        )
    except Exception as exc:
        _log.warning("Harvest hours read failed: %s", exc)
        base["error"] = str(exc)[:300]
        return base

    base["refreshed_at"] = refreshed_at
    goals = get_goals()
    clients: list[dict[str, Any]] = []
    grand_total = 0.0
    goal_total = 0.0
    for c in series_clients:
        cid = str(c.get("harvest_client_id"))
        total = float(c.get("total_hours") or 0.0)
        grand_total += total
        goal = goals.get(cid)
        if goal is not None:
            goal_total += goal
        clients.append(
            {
                "harvest_client_id": cid,
                "name": c.get("name"),
                "goal": goal,
                "total_hours": total,
                "series": c.get("series") or [],
            }
        )

    base["clients"] = clients
    base["totals"] = {"total_hours": round(grand_total, 2), "goal": round(goal_total, 2)}
    return base


def _today() -> date:
    """'Today' in the agency's timezone (default America/New_York).

    Harvest spent_date is a plain calendar date, so anchoring the month + the
    'through today' cutoff to the agency's local day avoids a UTC server rolling
    the month over hours early/late. Override with HARVEST_TIMEZONE.
    """
    tz_name = (os.getenv("HARVEST_TIMEZONE") or "America/New_York").strip()
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return date.today()


# ---------------------------------------------------------------------------
# Per-client monthly hour goals
# ---------------------------------------------------------------------------


def _goals_enabled() -> bool:
    return bool((os.getenv("DATABASE_URL") or "").strip())


def _ensure_goals_schema() -> bool:
    if not _goals_enabled():
        return False
    with db.connection() as conn:
        conn.execute(GOALS_SCHEMA_SQL)
    return True


def get_goals() -> dict[str, float]:
    """Return ``{harvest_client_id: monthly_goal}`` for all clients with a goal set."""
    if not _goals_enabled():
        return {}
    try:
        _ensure_goals_schema()
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT harvest_client_id, monthly_goal FROM harvest_client_goals "
                "WHERE monthly_goal IS NOT NULL"
            ).fetchall()
    except Exception as exc:
        _log.warning("Harvest goals read failed: %s", exc)
        return {}
    out: dict[str, float] = {}
    for cid, goal in rows:
        if goal is None:
            continue
        try:
            out[str(cid)] = float(goal)
        except (TypeError, ValueError):
            continue
    return out


def set_goal(
    *, harvest_client_id: str, monthly_goal: float | None, client_name: str = "", updated_by: str = ""
) -> None:
    """Upsert (or clear, when ``monthly_goal`` is None) one client's monthly goal."""
    cid = (harvest_client_id or "").strip()
    if not cid:
        raise ValueError("harvest_client_id is required.")
    if not _goals_enabled():
        raise RuntimeError("DATABASE_URL is required to store Harvest goals.")
    _ensure_goals_schema()
    goal_val: float | None = None
    if monthly_goal is not None:
        goal_val = float(monthly_goal)
        if goal_val < 0:
            raise ValueError("Monthly goal cannot be negative.")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO harvest_client_goals (harvest_client_id, client_name, monthly_goal, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (harvest_client_id) DO UPDATE SET
              client_name = COALESCE(EXCLUDED.client_name, harvest_client_goals.client_name),
              monthly_goal = EXCLUDED.monthly_goal,
              updated_by = EXCLUDED.updated_by,
              updated_at = NOW()
            """,
            (cid, (client_name or "").strip() or None, goal_val, (updated_by or "").strip() or None),
        )


@dataclass(frozen=True)
class HarvestStatus:
    connected: bool
    account_id: str | None
    account_name: str | None


def status() -> HarvestStatus:
    acct_id = _account_id()
    name = None
    try:
        pub = oauth_store.public_status(_PLATFORM)
        name = (pub.metadata or {}).get("account_name")
    except Exception:
        pass
    return HarvestStatus(connected=is_connected(), account_id=acct_id, account_name=name)
