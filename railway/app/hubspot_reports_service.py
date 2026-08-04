"""Read-side queries for the Lead Tracking page.

Aggregates the HubSpot mart fact tables (fact_hubspot_contacts /
fact_hubspot_deals) into a small set of dashboard-ready report structures.
Every query is guarded so the page renders gracefully before a table exists
(e.g. deals before the first deals sync runs).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import bigquery_service
import connector_config_store

LOGGER = logging.getLogger(__name__)

_CONTACT_TABLE = "fact_hubspot_contacts"
_DEAL_TABLE = "fact_hubspot_deals"
_EMAIL_TABLE = "fact_hubspot_emails"
_MQL_STAGE = "marketingqualifiedlead"

# Lifecycle stages this report can track, mapped to display nouns. The keys
# match the `stage_filter` values written by the HubSpot sync
# (see hubspot_sync_service._LIFECYCLE_FUNNEL). The report is not hard-wired to
# MQLs: whichever stage the admin configured the connector to import is the one
# the page reports on, so a client tracking "leads" doesn't see an empty page.
_STAGE_NOUNS: dict[str, tuple[str, str]] = {
    "subscriber":             ("Subscriber", "Subscribers"),
    "lead":                   ("Lead", "Leads"),
    "marketingqualifiedlead": ("MQL", "MQLs"),
    "salesqualifiedlead":     ("SQL", "SQLs"),
    "opportunity":            ("Opportunity", "Opportunities"),
    "customer":               ("Customer", "Customers"),
}


def _stage_nouns(stage: str) -> tuple[str, str]:
    """(singular, plural) display noun for a lifecycle stage, e.g.
    'marketingqualifiedlead' -> ('MQL', 'MQLs'). Falls back to a title-cased key
    for any custom stage a connector might sync."""
    known = _STAGE_NOUNS.get(stage)
    if known:
        return known
    label = (stage or "").replace("_", " ").strip().title() or "Lead"
    return label, f"{label}s"


def _resolve_stage(client_slug: str) -> str:
    """Which lifecycle stage this client's report tracks.

    Reads the connector's configured `lifecycle_stage` (the same option the
    connectors page exposes) and defaults to MQL. Only recognised stage keys are
    accepted, so the value is always safe to interpolate into SQL."""
    cfg = connector_config_store.get_config(client_slug, "hubspot")
    if cfg and cfg.sync_options:
        try:
            raw = str((json.loads(cfg.sync_options) or {}).get("lifecycle_stage") or "").strip()
        except Exception:
            raw = ""
        if raw in _STAGE_NOUNS:
            return raw
    return _MQL_STAGE


@dataclass
class LeadTrackingReport:
    project: str
    dataset: str
    configured: bool = True
    # Which lifecycle stage this report tracks, plus its display nouns. The
    # counts below (mql_count etc.) are really "primary stage" counts — the
    # field names are kept for backwards compatibility.
    stage: str = _MQL_STAGE
    stage_noun: str = "MQL"
    stage_noun_plural: str = "MQLs"
    # Summary tiles
    mql_count: int = 0
    contact_count: int = 0
    deal_count: int = 0
    pipeline_amount: float = 0.0
    won_amount: float = 0.0
    # Breakdowns: list of dicts
    mqls_by_month: list[dict[str, Any]] = field(default_factory=list)
    leads_by_source: list[dict[str, Any]] = field(default_factory=list)
    deals_by_source: list[dict[str, Any]] = field(default_factory=list)
    recent_mqls: list[dict[str, Any]] = field(default_factory=list)
    # Section availability (False when the underlying table is missing)
    contacts_available: bool = False
    deals_available: bool = False
    error: str | None = None


@dataclass
class EmailPerformanceReport:
    """Backing data for the standalone Email Performance page: every synced
    marketing email with its post-send counters, newest first."""
    configured: bool = True
    available: bool = False          # False when fact_hubspot_emails doesn't exist yet
    emails: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def fetch_email_performance(client_slug: str) -> EmailPerformanceReport:
    """Recent marketing emails + counters for the Email Performance page.

    Reads only fact_hubspot_emails (no contacts/deals), so the page stays light.
    Fails soft: an unconfigured client or a missing table yields an empty,
    non-erroring report the renderer shows as an empty state."""
    project, dataset = _resolve_target(client_slug)
    if not project:
        return EmailPerformanceReport(
            configured=False,
            error="HubSpot BigQuery destination is not configured for this client.",
        )
    report = EmailPerformanceReport()
    try:
        client = bigquery_service.build_client(project_id=project)
        et = f"`{project}.{dataset}.{_EMAIL_TABLE}`"
        rows = _rows(client, f"""
            SELECT email_id, name, subject, email_type, state, publish_date,
                   sent, delivered, opens, clicks, unsubscribed, bounces
            FROM {et}
            WHERE COALESCE(sent, 0) > 0
            ORDER BY publish_date DESC
            LIMIT 500
        """)
        report.available = True
        report.emails = rows
    except Exception as exc:
        # Table not created yet (email sync hasn't run / not a Marketing tier),
        # or a transient read error — either way the page renders an empty state.
        LOGGER.info("Email Performance read skipped [%s]: %s", client_slug, exc)
    return report


def _resolve_target(client_slug: str) -> tuple[str | None, str]:
    cfg = connector_config_store.get_config(client_slug, "hubspot")
    project = (cfg.bq_project_id if cfg and cfg.bq_project_id else None) \
        or (os.getenv("HUBSPOT_SYNC_PROJECT_ID") or "").strip() or None
    dataset = (cfg.mart_dataset_id if cfg and cfg.mart_dataset_id else None) \
        or (os.getenv("HUBSPOT_SYNC_DATASET") or "").strip() or "marketing_marts"
    return project, dataset


def _rows(client, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in client.query(sql).result()]


def fetch_mtd_mql_count(client_slug: str, *, start: date, end: date) -> int | None:
    """Count of contacts that became MQLs within [start, end] (inclusive).

    A lightweight single-query read for the HQ "primary KPI" column, so the HQ
    view can show a client's month-to-date MQLs without pulling the whole Lead
    Tracking report. Returns None when HubSpot isn't configured for the client or
    the read fails (e.g. the contacts table doesn't exist yet)."""
    project, dataset = _resolve_target(client_slug)
    if not project:
        return None
    stage = _resolve_stage(client_slug)
    try:
        client = bigquery_service.build_client(project_id=project)
        ct = f"`{project}.{dataset}.{_CONTACT_TABLE}`"
        sql = f"""
            SELECT COUNT(*) AS mqls
            FROM {ct}
            WHERE stage_filter = '{stage}'
              AND became_stage_date IS NOT NULL
              AND DATE(became_stage_date) BETWEEN DATE('{start.isoformat()}') AND DATE('{end.isoformat()}')
        """
        rows = _rows(client, sql)
        return int(rows[0].get("mqls") or 0) if rows else 0
    except Exception as exc:
        LOGGER.info("MTD MQL count skipped [%s]: %s", client_slug, exc)
        return None


def build_report(client_slug: str) -> LeadTrackingReport:
    project, dataset = _resolve_target(client_slug)
    if not project:
        return LeadTrackingReport(project="", dataset=dataset, configured=False,
                                  error="HubSpot BigQuery destination is not configured for this client.")

    stage = _resolve_stage(client_slug)
    report = LeadTrackingReport(project=project, dataset=dataset, stage=stage)
    try:
        client = bigquery_service.build_client(project_id=project)
    except Exception as exc:
        report.error = f"Could not connect to BigQuery: {exc}"
        return report

    ct = f"`{project}.{dataset}.{_CONTACT_TABLE}`"
    dt = f"`{project}.{dataset}.{_DEAL_TABLE}`"

    # Prefer the configured stage, but if the contacts were imported under a
    # different lifecycle stage than the one currently configured (e.g. the
    # admin switched the connector to "lead"), the configured stage can be empty
    # while real data sits under another stage_filter. Detect the populated
    # stages and only keep the configured one when it actually has rows —
    # otherwise the whole page would render empty against a populated table.
    # Fallback is restricted to recognised stage keys, so `stage` stays safe to
    # interpolate into the queries below.
    try:
        dist = _rows(client, f"""
            SELECT stage_filter, COUNT(*) AS n
            FROM {ct}
            WHERE stage_filter IS NOT NULL
            GROUP BY stage_filter
        """)
        present = {str(r.get("stage_filter")): int(r.get("n") or 0)
                   for r in dist if r.get("stage_filter") in _STAGE_NOUNS}
        if present and not present.get(stage):
            stage = max(present, key=present.get)
    except Exception as exc:
        LOGGER.info("Lead Tracking stage detection skipped [%s]: %s", client_slug, exc)

    report.stage = stage
    report.stage_noun, report.stage_noun_plural = _stage_nouns(stage)

    # These 6 queries are independent, so run them concurrently instead of
    # back-to-back — the page was slow because it blocked the HTML render on ~6
    # sequential BigQuery jobs. The client is safe to share across query threads;
    # wall time drops from the sum of all jobs to roughly the slowest one.
    from concurrent.futures import ThreadPoolExecutor

    sqls = {
        "contacts_summary": f"""
            SELECT
              COUNTIF(stage_filter = '{stage}')             AS mql_count,
              COUNT(DISTINCT contact_id)                    AS contact_count
            FROM {ct}
        """,
        "mqls_by_month": f"""
            SELECT FORMAT_DATE('%Y-%m', DATE(became_stage_date)) AS month,
                   COUNT(*) AS contacts
            FROM {ct}
            WHERE stage_filter = '{stage}' AND became_stage_date IS NOT NULL
            GROUP BY month ORDER BY month
        """,
        "leads_by_source": f"""
            SELECT COALESCE(hs_analytics_source, 'Unknown') AS source,
                   COUNT(*) AS contacts
            FROM {ct}
            WHERE stage_filter = '{stage}'
            GROUP BY source ORDER BY contacts DESC
        """,
        "recent_mqls": f"""
            SELECT email, company, hs_analytics_source AS source,
                   became_stage_date
            FROM {ct}
            WHERE stage_filter = '{stage}'
            ORDER BY became_stage_date DESC
            LIMIT 20
        """,
        "deals_summary": f"""
            SELECT
              COUNT(*)                            AS deal_count,
              SUM(amount)                         AS pipeline_amount,
              SUM(IF(is_closed_won, amount, 0))   AS won_amount
            FROM {dt}
        """,
        "deals_by_source": f"""
            SELECT COALESCE(hs_analytics_source, 'Unknown') AS source,
                   COUNT(*)                          AS deals,
                   SUM(amount)                       AS amount,
                   SUM(IF(is_closed_won, amount, 0)) AS won_amount
            FROM {dt}
            GROUP BY source ORDER BY deals DESC
        """,
    }

    def _q(name: str) -> list[dict[str, Any]] | None:
        """Run one query; None marks a failure (e.g. table not created yet)."""
        try:
            return _rows(client, sqls[name])
        except Exception as exc:
            LOGGER.info("Lead Tracking query '%s' skipped [%s]: %s", name, client_slug, exc)
            return None

    with ThreadPoolExecutor(max_workers=len(sqls)) as pool:
        res = {name: fut.result() for name, fut in
               {name: pool.submit(_q, name) for name in sqls}.items()}

    # ---- Contacts ---- (available if its core query succeeded)
    csum = res["contacts_summary"]
    if csum is not None:
        report.contacts_available = True
        if csum:
            report.mql_count = int(csum[0].get("mql_count") or 0)
            report.contact_count = int(csum[0].get("contact_count") or 0)
        report.mqls_by_month = res["mqls_by_month"] or []
        report.leads_by_source = res["leads_by_source"] or []
        report.recent_mqls = res["recent_mqls"] or []

    # ---- Deals ----
    dsum = res["deals_summary"]
    if dsum is not None:
        report.deals_available = True
        if dsum:
            report.deal_count = int(dsum[0].get("deal_count") or 0)
            report.pipeline_amount = float(dsum[0].get("pipeline_amount") or 0.0)
            report.won_amount = float(dsum[0].get("won_amount") or 0.0)
        report.deals_by_source = res["deals_by_source"] or []

    return report
