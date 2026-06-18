"""SEMrush API client for domain analytics.

Fetches domain overview, organic keywords, and backlink metrics.
All calls are lightweight and cached via the snapshot system.

Required env vars:
    SEMRUSH_API_KEY   — API key (generate at semrush.com/api/)

Optional env vars:
    SEMRUSH_DOMAIN    — target domain without protocol (default: penncommunitybank.com)
    SEMRUSH_DATABASE  — regional database code (default: us)

Error handling: all public functions return empty dict/list on failure and
never raise, so snapshot refresh is not interrupted by SEMrush issues.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_BASE_URL = "https://api.semrush.com/"
_DEFAULT_DOMAIN = "penncommunitybank.com"
_DEFAULT_DATABASE = "us"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _api_key() -> str:
    return (os.getenv("SEMRUSH_API_KEY") or "").strip()


def _domain() -> str:
    return (os.getenv("SEMRUSH_DOMAIN") or _DEFAULT_DOMAIN).strip()


def _database() -> str:
    return (os.getenv("SEMRUSH_DATABASE") or _DEFAULT_DATABASE).strip()


# ---------------------------------------------------------------------------
# HTTP + parsing
# ---------------------------------------------------------------------------

def _get(params: dict[str, str], timeout: int = 30) -> str:
    """GET the SEMrush API. Raises on HTTP errors or non-200 status."""
    key = _api_key()
    if not key:
        raise ValueError("SEMRUSH_API_KEY env var is not set")
    params = dict(params)
    params["key"] = key
    url = _BASE_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _parse_csv(text: str) -> list[dict[str, str]]:
    """Parse SEMrush semicolon-delimited text response into a list of dicts."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    # SEMrush signals errors as a single line starting with "ERROR"
    if lines[0].startswith("ERROR"):
        raise ValueError(f"SEMrush API error: {lines[0]}")
    headers = lines[0].split(";")
    rows = []
    for line in lines[1:]:
        values = line.split(";")
        rows.append({h: (values[i] if i < len(values) else "") for i, h in enumerate(headers)})
    return rows


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Domain overview
# ---------------------------------------------------------------------------

def fetch_domain_overview(
    domain: str | None = None,
    database: str | None = None,
) -> dict[str, Any]:
    """Organic + paid overview for a domain.

    Returns dict with: domain, semrush_rank, organic_keywords, organic_traffic,
    organic_cost, paid_keywords, paid_traffic, error (str | None).
    """
    domain = domain or _domain()
    db = database or _database()
    try:
        text = _get({
            "type": "domain_ranks",
            "domain": domain,
            "database": db,
            "export_columns": "Dn,Rk,Or,Ot,Oc,Ad,At,Ac",
            "display_limit": "1",
        })
        rows = _parse_csv(text)
        if not rows:
            return {"domain": domain, "error": "No data returned for domain"}
        r = rows[0]
        # SEMrush returns header names that match human-readable labels;
        # fall back by position if exact header key differs across API versions.
        keys = list(r.keys())
        def _col(name: str, fallback_idx: int) -> str:
            return r.get(name) or (list(r.values())[fallback_idx] if len(r) > fallback_idx else "")

        return {
            "domain": domain,
            "database": db,
            "semrush_rank":       _int(_col("Rank", 1)),
            "organic_keywords":   _int(_col("Organic Keywords", 2)),
            "organic_traffic":    _int(_col("Organic Traffic", 3)),
            "organic_cost":       _float(_col("Organic Cost", 4)),
            "paid_keywords":      _int(_col("Adwords Keywords", 5)),
            "paid_traffic":       _int(_col("Adwords Traffic", 6)),
            "error": None,
        }
    except Exception as exc:
        return {"domain": domain, "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# Organic keywords
# ---------------------------------------------------------------------------

def fetch_organic_keywords(
    domain: str | None = None,
    database: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Top organic keywords ranked for a domain, sorted by traffic share.

    Each row: keyword, position, search_volume, cpc, url, traffic_pct.
    Returns [] on error (error is stored in the snapshot errors dict separately).
    """
    domain = domain or _domain()
    db = database or _database()
    try:
        text = _get({
            "type": "domain_organic",
            "domain": domain,
            "database": db,
            "export_columns": "Ph,Po,Nq,Cp,Ur,Tr",
            "display_limit": str(min(int(limit), 1000)),
            "display_sort": "tr_desc",
        })
        rows = _parse_csv(text)
        out = []
        for r in rows:
            keyword = r.get("Keyword") or r.get("Ph") or ""
            if not keyword:
                continue
            out.append({
                "keyword":      keyword,
                "position":     _int(r.get("Position") or r.get("Po")),
                "search_volume": _int(r.get("Search Volume") or r.get("Nq")),
                "cpc":          _float(r.get("CPC") or r.get("Cp")),
                "url":          (r.get("URL") or r.get("Ur") or "").strip(),
                "traffic_pct":  _float(r.get("Traffic (%)") or r.get("Tr")),
            })
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Backlinks
# ---------------------------------------------------------------------------

def fetch_backlinks_overview(domain: str | None = None) -> dict[str, Any]:
    """Backlink summary for a domain.

    Returns dict with: total_backlinks, referring_domains, referring_ips,
    dofollow, nofollow, authority_score, error (str | None).
    """
    domain = domain or _domain()
    try:
        text = _get({
            "type": "backlinks_overview",
            "target": domain,
            "target_type": "root_domain",
            "export_columns": "target,ascore,total,domains_num,ips_num,follows_num,nofollows_num",
        })
        rows = _parse_csv(text)
        if not rows:
            return {"domain": domain, "error": "No backlink data returned"}
        r = rows[0]
        return {
            "domain":            domain,
            "authority_score":   _int(r.get("Authority Score") or r.get("ascore")),
            "total_backlinks":   _int(r.get("Total Backlinks") or r.get("total")),
            "referring_domains": _int(r.get("Referring Domains") or r.get("domains_num")),
            "referring_ips":     _int(r.get("Referring IPs") or r.get("ips_num")),
            "dofollow":          _int(r.get("Follow Links") or r.get("follows_num")),
            "nofollow":          _int(r.get("Nofollow Links") or r.get("nofollows_num")),
            "error": None,
        }
    except Exception as exc:
        return {"domain": domain, "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# Position distribution helper
# ---------------------------------------------------------------------------

_POSITION_BUCKETS = [
    ("1–3",   1,   3),
    ("4–10",  4,  10),
    ("11–20", 11, 20),
    ("21–50", 21, 50),
    ("51+",   51, 9999),
]


def _position_distribution(keywords: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = {label: 0 for label, _, _ in _POSITION_BUCKETS}
    for kw in keywords:
        pos = int(kw.get("position") or 0)
        for label, lo, hi in _POSITION_BUCKETS:
            if lo <= pos <= hi:
                counts[label] += 1
                break
    return [{"bucket": label, "count": counts[label]} for label, _, _ in _POSITION_BUCKETS]


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------

def build_semrush_snapshot(
    domain: str | None = None,
    database: str | None = None,
    keyword_limit: int = 100,
) -> dict[str, Any]:
    """Fetch all SEMrush modules in parallel and return a combined snapshot dict.

    Always returns a dict — never raises. Errors are surfaced under the 'errors' key.
    """
    domain = domain or _domain()
    db = database or _database()

    if not _api_key():
        return {"error": "SEMRUSH_API_KEY not configured", "domain": domain}

    errors: dict[str, str] = {}

    def _ov():
        return fetch_domain_overview(domain, db)

    def _kw():
        return fetch_organic_keywords(domain, db, limit=keyword_limit)

    def _bl():
        return fetch_backlinks_overview(domain)

    with ThreadPoolExecutor(max_workers=3) as pool:
        ov_fut = pool.submit(_ov)
        kw_fut = pool.submit(_kw)
        bl_fut = pool.submit(_bl)

        try:
            overview = ov_fut.result()
            if overview.get("error"):
                errors["overview"] = overview["error"]
        except Exception as exc:
            overview = {}
            errors["overview"] = str(exc)[:300]

        try:
            keywords = kw_fut.result()
        except Exception as exc:
            keywords = []
            errors["keywords"] = str(exc)[:300]

        try:
            backlinks = bl_fut.result()
            if backlinks.get("error"):
                errors["backlinks"] = backlinks["error"]
        except Exception as exc:
            backlinks = {}
            errors["backlinks"] = str(exc)[:300]

    position_dist = _position_distribution(keywords)

    result: dict[str, Any] = {
        "domain": domain,
        "database": db,
        "overview": overview,
        "keywords": keywords,
        "backlinks": backlinks,
        "position_distribution": position_dist,
    }
    if errors:
        result["errors"] = errors
    return result
