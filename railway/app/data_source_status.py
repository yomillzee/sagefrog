"""Per-client data-source feed status for the settings page.

Read-only health check: for each data source a client has configured, does
its BigQuery table / export exist and is it receiving data? Reads route via
the same registries the dashboard uses (ga4_clients / gsc_clients), so the
status reflects the client's real project rather than the Penn fallback.

This drives the "Data source status" panel â€” it validates IDs and shows
whether each feed is live, since Google Ads / GA4 land in BigQuery through
Google's Data Transfer Service (which the app can't trigger), and
LinkedIn / Meta marts only exist once their sync has run.
"""

from __future__ import annotations

from typing import Any

import bigquery_service

_DEFAULT_MART_DATASET = "marketing_marts"

# status values: feeding | fallback | empty | missing | error | not_configured


def _is_not_found(exc: Exception) -> bool:
    if getattr(exc, "code", None) == 404:
        return True
    msg = str(exc).lower()
    return "not found" in msg and "table" in msg


def _run_one(sql: str, *, project_id: str, credentials_env: str | None) -> dict[str, Any]:
    rows = bigquery_service.run_query(
        sql, project_id=project_id, credentials_env=credentials_env, max_rows=1
    )
    return dict(rows[0]) if rows else {}


def _fmt_date(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v).strip()[:10] or None


def _ga4_target(client_key: str):
    import ga4_clients
    return ga4_clients.resolve_target(client_key=client_key)


def _mart_routing(ga4_target) -> tuple[str, str, str | None]:
    """(project, dataset, credentials_env) for the client's mart tables.

    Marts live in the same project as the client's GA4 export. Dataset is the
    conventional marketing_marts unless overridden by env.
    """
    import os
    project = ga4_target.bq_project_id
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DATASET).strip()
    creds = ga4_target.credentials_env or None
    return project, dataset, creds


def _check_mart(
    *, project: str, dataset: str, table: str, credentials_env: str | None,
    recent_days: int = 30,
) -> dict[str, Any]:
    """COUNT recent rows + MAX(date) for a mart fact table; classify result."""
    full = f"`{project}.{dataset}.{table}`"
    sql = f"""
      SELECT
        COUNTIF(date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(recent_days)} DAY)) AS recent_n,
        COUNT(*) AS total_n,
        MAX(date) AS mx
      FROM {full}
    """
    try:
        r = _run_one(sql, project_id=project, credentials_env=credentials_env)
    except Exception as exc:
        if _is_not_found(exc):
            return {"status": "missing", "rows": None, "max_date": None, "error": None}
        return {"status": "error", "rows": None, "max_date": None, "error": str(exc)[:200]}
    total = int(r.get("total_n") or 0)
    recent = int(r.get("recent_n") or 0)
    mx = _fmt_date(r.get("mx"))
    if total <= 0:
        return {"status": "empty", "rows": 0, "max_date": None, "error": None}
    return {
        "status": "feeding" if recent > 0 else "empty",
        "rows": recent,
        "max_date": mx,
        "error": None,
    }


def _check_ga4_export(ga4_target) -> dict[str, Any]:
    project = ga4_target.bq_project_id
    dataset = ga4_target.bq_dataset_id
    creds = ga4_target.credentials_env or None
    sql = f"""
      SELECT COUNT(*) AS recent_n, MAX(event_date) AS mx
      FROM `{project}.{dataset}.events_*`
      WHERE _TABLE_SUFFIX >= FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
    """
    try:
        r = _run_one(sql, project_id=project, credentials_env=creds)
    except Exception as exc:
        if _is_not_found(exc):
            return {"status": "missing", "rows": None, "max_date": None, "error": None}
        return {"status": "error", "rows": None, "max_date": None, "error": str(exc)[:200]}
    recent = int(r.get("recent_n") or 0)
    raw_mx = r.get("mx")
    mx = None
    if raw_mx:
        s = str(raw_mx).strip()[:8]
        mx = f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s
    return {
        "status": "feeding" if recent > 0 else "empty",
        "rows": recent,
        "max_date": mx,
        "error": None,
    }


def _check_gsc(client_slug: str) -> dict[str, Any]:
    """Reuse the GSC table checker, collapsing its per-table result."""
    try:
        import bq_gsc_service
        checks = bq_gsc_service.check_tables(client_slug=client_slug)
    except Exception as exc:
        return {"status": "error", "rows": None, "max_date": None, "error": str(exc)[:200]}
    hist = checks.get("hist_query") or {}
    if not hist.get("ok"):
        err = hist.get("error") or ""
        if "not found" in err.lower():
            return {"status": "missing", "rows": None, "max_date": None, "error": None}
        return {"status": "error", "rows": None, "max_date": None, "error": err[:200] or None}
    rows = int(hist.get("row_count") or 0)
    return {
        "status": "feeding" if rows > 0 else "empty",
        "rows": rows,
        "max_date": hist.get("max_date"),
        "error": None,
    }


def build_status(client_slug: str) -> list[dict[str, Any]]:
    """Status rows for every data source, in display order.

    Each row: {key, label, status, rows, max_date, hint, error}. Sources the
    client hasn't configured come back status='not_configured' without a query.
    """
    import client_config
    import client_dashboard_config as _cdc

    slug = (client_slug or "").strip().lower()
    cfg = client_config.load_client_config(slug)
    db_cfg = _cdc.get_config(slug)
    ga4_key = cfg.ga4_client_key or slug
    gsc_site = (db_cfg.gsc_site_url if db_cfg else None) or ""

    try:
        ga4_target = _ga4_target(ga4_key)
    except Exception:
        ga4_target = None

    out: list[dict[str, Any]] = []

    # GA4 export (Data Transfer / native export â€” app can't write it)
    if cfg.ga4_client_key or ga4_target is not None:
        row = (
            _check_ga4_export(ga4_target)
            if ga4_target is not None
            else {"status": "error", "rows": None, "max_date": None, "error": "GA4 routing unresolved"}
        )
        row.update({
            "key": "ga4",
            "label": "GA4 export",
            "hint": "Google BigQuery Data Transfer / native export",
        })
        out.append(row)
    else:
        out.append({"key": "ga4", "label": "GA4 export", "status": "not_configured",
                    "rows": None, "max_date": None, "hint": "Set a GA4 client key", "error": None})

    # Mart-backed sources share the GA4 project routing
    mart_ok = ga4_target is not None
    if mart_ok:
        m_project, m_dataset, m_creds = _mart_routing(ga4_target)

    # Google Ads (views over the Google Ads -> BigQuery Data Transfer)
    if cfg.google_customer_id:
        if mart_ok:
            row = _check_mart(project=m_project, dataset=m_dataset,
                              table="fact_google_ads_campaign_daily", credentials_env=m_creds)
            if row["status"] == "missing":
                row["hint"] = f"Set up Google Ads â†’ BigQuery Data Transfer in {m_project}"
            else:
                row["hint"] = "Google BigQuery Data Transfer"
        else:
            row = {"status": "error", "rows": None, "max_date": None, "error": "mart routing unresolved", "hint": ""}
        row.update({"key": "google", "label": "Google Ads"})
        out.append(row)
    else:
        out.append({"key": "google", "label": "Google Ads", "status": "not_configured",
                    "rows": None, "max_date": None, "hint": "No Google customer ID", "error": None})

    # LinkedIn (app-written mart)
    if cfg.linkedin_account_id:
        if mart_ok:
            row = _check_mart(project=m_project, dataset=m_dataset,
                              table="fact_linkedin_ads_campaign_daily", credentials_env=m_creds)
            if row["status"] in ("missing", "empty"):
                row["hint"] = "Account set â€” syncs on next refresh"
            else:
                row["hint"] = "App sync (LinkedIn API â†’ BigQuery)"
        else:
            row = {"status": "error", "rows": None, "max_date": None, "error": "mart routing unresolved", "hint": ""}
        row.update({"key": "linkedin", "label": "LinkedIn"})
        out.append(row)
    else:
        out.append({"key": "linkedin", "label": "LinkedIn", "status": "not_configured",
                    "rows": None, "max_date": None, "hint": "No LinkedIn account ID", "error": None})

    # Meta (app-written mart)
    if cfg.meta_account_id:
        if mart_ok:
            row = _check_mart(project=m_project, dataset=m_dataset,
                              table="fact_meta_ads_campaign_daily", credentials_env=m_creds)
            if row["status"] in ("missing", "empty"):
                row["hint"] = "Account set â€” syncs on next refresh"
            else:
                row["hint"] = "App sync (Meta API â†’ BigQuery)"
        else:
            row = {"status": "error", "rows": None, "max_date": None, "error": "mart routing unresolved", "hint": ""}
        row.update({"key": "meta", "label": "Meta"})
        out.append(row)
    else:
        out.append({"key": "meta", "label": "Meta", "status": "not_configured",
                    "rows": None, "max_date": None, "hint": "No Meta ad account ID", "error": None})

    # GSC (app-written tables)
    if gsc_site:
        row = _check_gsc(slug)
        if row["status"] in ("missing", "empty"):
            row["hint"] = "Site set â€” run Sync GSC â†’ BigQuery"
        else:
            row["hint"] = "App sync (Search Console API â†’ BigQuery)"
        row.update({"key": "gsc", "label": "Search Console"})
        out.append(row)
    else:
        out.append({"key": "gsc", "label": "Search Console", "status": "not_configured",
                    "rows": None, "max_date": None, "hint": "No GSC property set", "error": None})

    return out
