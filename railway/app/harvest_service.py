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
_DEFAULT_CACHE_TTL = 6 * 3600  # 6 hours


def _cache_ttl_seconds() -> int:
    raw = (os.getenv("HARVEST_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_CACHE_TTL
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_CACHE_TTL

# Goals are a monthly-hours target that can be a single number, a range
# (goal_min..goal_max), or open-ended (goal_min with goal_max NULL, e.g. "160+").
# goal_min is always the floor; goal_max is the ceiling (NULL = no ceiling).
# hard_ceiling marks goal_max as a *hard* cap — the team must stop at the ceiling
# every month (no billing over it). It only applies when goal_max is set.
GOALS_SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS harvest_client_goals (
      harvest_client_id TEXT PRIMARY KEY,
      client_name       TEXT,
      goal_min          NUMERIC,
      goal_max          NUMERIC,
      hard_ceiling      BOOLEAN NOT NULL DEFAULT FALSE,
      updated_by        TEXT,
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Backfill the hard_ceiling flag onto tables created before it existed.
    """
    ALTER TABLE harvest_client_goals
      ADD COLUMN IF NOT EXISTS hard_ceiling BOOLEAN NOT NULL DEFAULT FALSE
    """,
    # Migrate the original single-value column (monthly_goal) to the min/max pair:
    # add the columns if missing and backfill goal_min = goal_max = monthly_goal.
    """
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'harvest_client_goals' AND column_name = 'monthly_goal'
      ) THEN
        IF NOT EXISTS (
          SELECT 1 FROM information_schema.columns
          WHERE table_name = 'harvest_client_goals' AND column_name = 'goal_min'
        ) THEN
          ALTER TABLE harvest_client_goals ADD COLUMN goal_min NUMERIC;
          ALTER TABLE harvest_client_goals ADD COLUMN goal_max NUMERIC;
          UPDATE harvest_client_goals SET goal_min = monthly_goal, goal_max = monthly_goal
            WHERE monthly_goal IS NOT NULL;
        END IF;
      END IF;
    END $$
    """,
]

# Admin-assigned classification of each Harvest project as retainer vs one-off
# project work. Untagged projects (no row) count only under the "All" scope.
PROJECT_TAGS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS harvest_project_tags (
  harvest_project_id TEXT PRIMARY KEY,
  project_name       TEXT,
  client_name        TEXT,
  tag                TEXT NOT NULL,
  updated_by         TEXT,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""
VALID_PROJECT_TAGS = frozenset({"retainer", "project"})

# Admin-assigned account owner for each client — the team member who owns the
# relationship. Purely a label/filter on the Client Hours page; a client with no
# row is "unassigned". The canonical roster is shared with the renderer (chip +
# owner filter) and the write endpoint (validation).
CLIENT_OWNERS = (
    "Alyssa",
    "Kristen",
    "Mike",
    "Sam",
    "Kelly",
    "Payton",
    "Sukh",
    "Andre",
    "Libby",
    "Joliene",
    "April",
)
VALID_OWNERS = frozenset(CLIENT_OWNERS)
# Case-insensitive lookup so an owner posted in any casing maps to the canonical
# spelling stored/displayed.
_OWNER_BY_LOWER = {o.lower(): o for o in CLIENT_OWNERS}

CLIENT_OWNERS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS harvest_client_owners (
  harvest_client_id TEXT PRIMARY KEY,
  client_name       TEXT,
  owner             TEXT NOT NULL,
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
    """Fold raw entries into ``{client_id: {name, projects: {project_id: {name,
    daily, daily_billable}}}}``.

    Hours are bucketed by Harvest client *and project* so the page can split a
    client's hours into retainer vs one-off project work (admins tag each
    project). ``daily`` sums every entry's hours; ``daily_billable`` sums only
    entries flagged billable in Harvest.
    """
    by_client: dict[str, dict[str, Any]] = {}
    for e in entries:
        client = e.get("client") or {}
        cid = str(client.get("id") or "").strip() or "0"
        cname = str(client.get("name") or "").strip() or "(No client)"
        project = e.get("project") or {}
        pid = str(project.get("id") or "").strip() or "0"
        pname = str(project.get("name") or "").strip() or "(No project)"
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
        cslot = by_client.setdefault(cid, {"name": cname, "projects": {}})
        cslot["name"] = cname
        pslot = cslot["projects"].setdefault(pid, {"name": pname, "daily": {}, "daily_billable": {}})
        pslot["name"] = pname
        pslot["daily"][day_idx] = pslot["daily"].get(day_idx, 0.0) + hours
        if bool(e.get("billable")):
            pslot["daily_billable"][day_idx] = pslot["daily_billable"].get(day_idx, 0.0) + hours
    return by_client


def _build_client_series(
    *, account_id: str, first: date, today: date, days_elapsed: int
) -> list[dict[str, Any]]:
    """Pull the month's time entries from Harvest and fold them into per-client,
    per-project cumulative-hours series (no goals/tags applied). This is the
    expensive, cacheable part: one token refresh + paginated time_entries reads.

    Each client carries a ``projects`` list; the page sums the projects a scope
    (all / retainer / project) selects, so tag and scope changes never re-hit
    Harvest.
    """
    access_token = _access_token()
    entries = _fetch_month_entries(
        access_token=access_token, account_id=account_id, start=first, end=today
    )
    by_client = _cumulative_by_client(entries, first=first, days_elapsed=days_elapsed)

    def _cumulate(daily: dict[int, float]) -> tuple[list[float], float]:
        running = 0.0
        series: list[float] = []
        for day in range(1, days_elapsed + 1):
            running += daily.get(day, 0.0)
            series.append(round(running, 2))
        return series, round(running, 2)

    clients: list[dict[str, Any]] = []
    for cid, cslot in by_client.items():
        projects: list[dict[str, Any]] = []
        client_total = 0.0
        for pid, pslot in cslot["projects"].items():
            series, total = _cumulate(pslot["daily"])
            series_b, _ = _cumulate(pslot["daily_billable"])
            client_total += total
            projects.append(
                {
                    "project_id": pid,
                    "name": pslot["name"],
                    "series": series,
                    "series_billable": series_b,
                    "total_hours": total,
                }
            )
        projects.sort(key=lambda p: p["total_hours"], reverse=True)
        clients.append(
            {
                "harvest_client_id": cid,
                "name": cslot["name"],
                "projects": projects,
                "_total": round(client_total, 2),
            }
        )
    clients.sort(key=lambda c: c["_total"], reverse=True)
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
    tags = get_project_tags()
    owners = get_client_owners()
    clients: list[dict[str, Any]] = []
    grand_total = 0.0
    goal_min_total = 0.0
    goal_max_total = 0.0
    for c in series_clients:
        cid = str(c.get("harvest_client_id"))
        # Attach each project's current tag (retainer|project|None) fresh, so tag
        # edits show without waiting out the hours cache. Projects are summed
        # client-side per the selected scope.
        projects = []
        client_total = 0.0
        for p in c.get("projects") or []:
            pid = str(p.get("project_id"))
            client_total += float(p.get("total_hours") or 0.0)
            projects.append(
                {
                    "project_id": pid,
                    "name": p.get("name"),
                    "tag": tags.get(pid),
                    "series": p.get("series") or [],
                    "series_billable": p.get("series_billable") or [],
                    "total_hours": float(p.get("total_hours") or 0.0),
                }
            )
        grand_total += client_total
        goal = goals.get(cid) or {}
        goal_min = goal.get("min")
        goal_max = goal.get("max")
        if goal_min is not None:
            goal_min_total += goal_min
        # For the max total, an open-ended floor ("N+") contributes its floor.
        goal_max_total += goal_max if goal_max is not None else (goal_min or 0.0)
        clients.append(
            {
                "harvest_client_id": cid,
                "name": c.get("name"),
                "goal_min": goal_min,
                "goal_max": goal_max,
                "goal_label": format_goal(goal_min, goal_max),
                # Hard ceiling only reads true when there's a ceiling to stop at.
                "goal_hard": bool(goal.get("hard")) and goal_max is not None,
                "owner": owners.get(cid),
                "projects": projects,
            }
        )

    base["clients"] = clients
    base["totals"] = {
        "total_hours": round(grand_total, 2),
        "goal_min": round(goal_min_total, 2),
        "goal_max": round(goal_max_total, 2),
    }
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


def parse_goal_text(text: str) -> tuple[float | None, float | None]:
    """Parse an admin-entered goal into ``(goal_min, goal_max)``.

    Accepts a single number ("80" → 80..80), a range ("80-100" or "80–100" →
    80..100), or an open-ended floor ("80+" → 80..None). An empty string clears
    the goal, returned as ``(None, None)``. Raises ValueError on anything else.
    """
    t = (text or "").strip().replace("–", "-").replace("—", "-").replace(" ", "")
    if not t:
        return None, None
    if t.endswith("+"):
        lo = float(t[:-1])
        if lo < 0:
            raise ValueError("Goal cannot be negative.")
        return lo, None
    # A "-" after the first character marks a range (guard the leading sign).
    if "-" in t[1:]:
        parts = [p for p in t.split("-") if p != ""]
        if len(parts) != 2:
            raise ValueError("Use a range like 80-100.")
        lo, hi = float(parts[0]), float(parts[1])
        if lo < 0 or hi < 0:
            raise ValueError("Goal cannot be negative.")
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi
    v = float(t)
    if v < 0:
        raise ValueError("Goal cannot be negative.")
    return v, v


def format_goal(goal_min: float | None, goal_max: float | None) -> str:
    """Human-readable goal label: "80h", "80–100h", "80h+", or "" when unset."""
    def _n(v: float) -> str:
        return str(int(v)) if float(v).is_integer() else f"{v:g}"

    if goal_min is None and goal_max is None:
        return ""
    if goal_min is not None and goal_max is None:
        return f"{_n(goal_min)}h+"
    if goal_min is not None and goal_max is not None:
        if goal_min == goal_max:
            return f"{_n(goal_min)}h"
        return f"{_n(goal_min)}–{_n(goal_max)}h"
    return f"≤{_n(goal_max)}h"


def _goals_enabled() -> bool:
    return bool((os.getenv("DATABASE_URL") or "").strip())


def _ensure_goals_schema() -> bool:
    if not _goals_enabled():
        return False
    with db.connection() as conn:
        for stmt in GOALS_SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def get_goals() -> dict[str, dict[str, float | bool | None]]:
    """Return ``{harvest_client_id: {"min": x, "max": y|None, "hard": bool}}`` for
    clients with a goal. ``hard`` marks goal_max as a hard ceiling (stop-at cap)."""
    if not _goals_enabled():
        return {}
    try:
        _ensure_goals_schema()
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT harvest_client_id, goal_min, goal_max, hard_ceiling "
                "FROM harvest_client_goals "
                "WHERE goal_min IS NOT NULL OR goal_max IS NOT NULL"
            ).fetchall()
    except Exception as exc:
        _log.warning("Harvest goals read failed: %s", exc)
        return {}
    out: dict[str, dict[str, float | bool | None]] = {}
    for cid, gmin, gmax, hard in rows:
        try:
            lo = float(gmin) if gmin is not None else None
            hi = float(gmax) if gmax is not None else None
        except (TypeError, ValueError):
            continue
        if lo is None and hi is None:
            continue
        # A hard ceiling only means something with a ceiling to stop at.
        out[str(cid)] = {"min": lo, "max": hi, "hard": bool(hard) and hi is not None}
    return out


def set_goal(
    *,
    harvest_client_id: str,
    goal_min: float | None,
    goal_max: float | None,
    hard_ceiling: bool = False,
    client_name: str = "",
    updated_by: str = "",
) -> None:
    """Upsert one client's monthly goal, or clear it when both bounds are None.

    ``hard_ceiling`` marks goal_max as a hard cap (the team stops at the ceiling).
    It is forced off when there is no ceiling (goal_max is None), since there is
    nothing to cap against.
    """
    cid = (harvest_client_id or "").strip()
    if not cid:
        raise ValueError("harvest_client_id is required.")
    if not _goals_enabled():
        raise RuntimeError("DATABASE_URL is required to store Harvest goals.")
    _ensure_goals_schema()
    lo = float(goal_min) if goal_min is not None else None
    hi = float(goal_max) if goal_max is not None else None
    if (lo is not None and lo < 0) or (hi is not None and hi < 0):
        raise ValueError("Goal cannot be negative.")
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    hard = bool(hard_ceiling) and hi is not None
    if lo is None and hi is None:
        # Clearing: remove the row so it drops out of get_goals entirely.
        with db.connection() as conn:
            conn.execute("DELETE FROM harvest_client_goals WHERE harvest_client_id = %s", (cid,))
        return
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO harvest_client_goals (harvest_client_id, client_name, goal_min, goal_max, hard_ceiling, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (harvest_client_id) DO UPDATE SET
              client_name = COALESCE(EXCLUDED.client_name, harvest_client_goals.client_name),
              goal_min = EXCLUDED.goal_min,
              goal_max = EXCLUDED.goal_max,
              hard_ceiling = EXCLUDED.hard_ceiling,
              updated_by = EXCLUDED.updated_by,
              updated_at = NOW()
            """,
            (cid, (client_name or "").strip() or None, lo, hi, hard, (updated_by or "").strip() or None),
        )


# ---------------------------------------------------------------------------
# Per-project retainer/project tags
# ---------------------------------------------------------------------------


def _ensure_project_tags_schema() -> bool:
    if not _goals_enabled():
        return False
    with db.connection() as conn:
        conn.execute(PROJECT_TAGS_SCHEMA_SQL)
    return True


def get_project_tags() -> dict[str, str]:
    """Return ``{harvest_project_id: tag}`` for every tagged project."""
    if not _goals_enabled():
        return {}
    try:
        _ensure_project_tags_schema()
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT harvest_project_id, tag FROM harvest_project_tags WHERE tag IS NOT NULL"
            ).fetchall()
    except Exception as exc:
        _log.warning("Harvest project tags read failed: %s", exc)
        return {}
    out: dict[str, str] = {}
    for pid, tag in rows:
        t = str(tag or "").strip().lower()
        if t in VALID_PROJECT_TAGS:
            out[str(pid)] = t
    return out


def set_project_tag(
    *,
    harvest_project_id: str,
    tag: str | None,
    project_name: str = "",
    client_name: str = "",
    updated_by: str = "",
) -> None:
    """Set a project's tag ('retainer' or 'project'), or clear it when tag is
    blank/None (the project then counts only under the 'All' scope)."""
    pid = (harvest_project_id or "").strip()
    if not pid:
        raise ValueError("harvest_project_id is required.")
    if not _goals_enabled():
        raise RuntimeError("DATABASE_URL is required to store Harvest project tags.")
    _ensure_project_tags_schema()
    t = (tag or "").strip().lower()
    if not t:
        with db.connection() as conn:
            conn.execute("DELETE FROM harvest_project_tags WHERE harvest_project_id = %s", (pid,))
        return
    if t not in VALID_PROJECT_TAGS:
        raise ValueError("Tag must be 'retainer' or 'project'.")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO harvest_project_tags (harvest_project_id, project_name, client_name, tag, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (harvest_project_id) DO UPDATE SET
              project_name = COALESCE(EXCLUDED.project_name, harvest_project_tags.project_name),
              client_name = COALESCE(EXCLUDED.client_name, harvest_project_tags.client_name),
              tag = EXCLUDED.tag,
              updated_by = EXCLUDED.updated_by,
              updated_at = NOW()
            """,
            (
                pid,
                (project_name or "").strip() or None,
                (client_name or "").strip() or None,
                t,
                (updated_by or "").strip() or None,
            ),
        )


# ---------------------------------------------------------------------------
# Per-client account owner
# ---------------------------------------------------------------------------


def _ensure_client_owners_schema() -> bool:
    if not _goals_enabled():
        return False
    with db.connection() as conn:
        conn.execute(CLIENT_OWNERS_SCHEMA_SQL)
    return True


def get_client_owners() -> dict[str, str]:
    """Return ``{harvest_client_id: owner}`` for every client with an owner set."""
    if not _goals_enabled():
        return {}
    try:
        _ensure_client_owners_schema()
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT harvest_client_id, owner FROM harvest_client_owners WHERE owner IS NOT NULL"
            ).fetchall()
    except Exception as exc:
        _log.warning("Harvest client owners read failed: %s", exc)
        return {}
    out: dict[str, str] = {}
    for cid, owner in rows:
        canon = _OWNER_BY_LOWER.get(str(owner or "").strip().lower())
        if canon:
            out[str(cid)] = canon
    return out


def set_client_owner(
    *,
    harvest_client_id: str,
    owner: str | None,
    client_name: str = "",
    updated_by: str = "",
) -> str | None:
    """Set a client's account owner, or clear it when ``owner`` is blank/None.
    Returns the canonical owner name stored (or None when cleared)."""
    cid = (harvest_client_id or "").strip()
    if not cid:
        raise ValueError("harvest_client_id is required.")
    if not _goals_enabled():
        raise RuntimeError("DATABASE_URL is required to store client owners.")
    _ensure_client_owners_schema()
    raw = (owner or "").strip()
    if not raw:
        with db.connection() as conn:
            conn.execute("DELETE FROM harvest_client_owners WHERE harvest_client_id = %s", (cid,))
        return None
    canon = _OWNER_BY_LOWER.get(raw.lower())
    if not canon:
        raise ValueError("Unknown owner.")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO harvest_client_owners (harvest_client_id, client_name, owner, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (harvest_client_id) DO UPDATE SET
              client_name = COALESCE(EXCLUDED.client_name, harvest_client_owners.client_name),
              owner = EXCLUDED.owner,
              updated_by = EXCLUDED.updated_by,
              updated_at = NOW()
            """,
            (cid, (client_name or "").strip() or None, canon, (updated_by or "").strip() or None),
        )
    return canon


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
