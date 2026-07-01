"""Read-side queries for the Lead Tracking page.

Aggregates the HubSpot mart fact tables (fact_hubspot_contacts /
fact_hubspot_deals) into a small set of dashboard-ready report structures.
Every query is guarded so the page renders gracefully before a table exists
(e.g. deals before the first deals sync runs).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import bigquery_service
import connector_config_store

LOGGER = logging.getLogger(__name__)

_CONTACT_TABLE = "fact_hubspot_contacts"
_DEAL_TABLE = "fact_hubspot_deals"
_MQL_STAGE = "marketingqualifiedlead"


@dataclass
class LeadTrackingReport:
    project: str
    dataset: str
    configured: bool = True
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


def _resolve_target(client_slug: str) -> tuple[str | None, str]:
    cfg = connector_config_store.get_config(client_slug, "hubspot")
    project = (cfg.bq_project_id if cfg and cfg.bq_project_id else None) \
        or (os.getenv("HUBSPOT_SYNC_PROJECT_ID") or "").strip() or None
    dataset = (cfg.mart_dataset_id if cfg and cfg.mart_dataset_id else None) \
        or (os.getenv("HUBSPOT_SYNC_DATASET") or "").strip() or "marketing_marts"
    return project, dataset


def _rows(client, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in client.query(sql).result()]


def build_report(client_slug: str) -> LeadTrackingReport:
    project, dataset = _resolve_target(client_slug)
    if not project:
        return LeadTrackingReport(project="", dataset=dataset, configured=False,
                                  error="HubSpot BigQuery destination is not configured for this client.")

    report = LeadTrackingReport(project=project, dataset=dataset)
    try:
        client = bigquery_service.build_client(project_id=project)
    except Exception as exc:
        report.error = f"Could not connect to BigQuery: {exc}"
        return report

    ct = f"`{project}.{dataset}.{_CONTACT_TABLE}`"
    dt = f"`{project}.{dataset}.{_DEAL_TABLE}`"

    # ---- Contacts ----
    try:
        summary = _rows(client, f"""
            SELECT
              COUNTIF(stage_filter = '{_MQL_STAGE}')        AS mql_count,
              COUNT(DISTINCT contact_id)                    AS contact_count
            FROM {ct}
        """)
        if summary:
            report.mql_count = int(summary[0].get("mql_count") or 0)
            report.contact_count = int(summary[0].get("contact_count") or 0)

        report.mqls_by_month = _rows(client, f"""
            SELECT FORMAT_DATE('%Y-%m', DATE(became_stage_date)) AS month,
                   COUNT(*) AS contacts
            FROM {ct}
            WHERE stage_filter = '{_MQL_STAGE}' AND became_stage_date IS NOT NULL
            GROUP BY month ORDER BY month
        """)
        report.leads_by_source = _rows(client, f"""
            SELECT COALESCE(hs_analytics_source, 'Unknown') AS source,
                   COUNT(*) AS contacts
            FROM {ct}
            WHERE stage_filter = '{_MQL_STAGE}'
            GROUP BY source ORDER BY contacts DESC
        """)
        report.recent_mqls = _rows(client, f"""
            SELECT email, company, hs_analytics_source AS source,
                   became_stage_date
            FROM {ct}
            WHERE stage_filter = '{_MQL_STAGE}'
            ORDER BY became_stage_date DESC
            LIMIT 20
        """)
        report.contacts_available = True
    except Exception as exc:
        LOGGER.info("Lead Tracking contacts query skipped [%s]: %s", client_slug, exc)

    # ---- Deals ----
    try:
        dsummary = _rows(client, f"""
            SELECT
              COUNT(*)                            AS deal_count,
              SUM(amount)                         AS pipeline_amount,
              SUM(IF(is_closed_won, amount, 0))   AS won_amount
            FROM {dt}
        """)
        if dsummary:
            report.deal_count = int(dsummary[0].get("deal_count") or 0)
            report.pipeline_amount = float(dsummary[0].get("pipeline_amount") or 0.0)
            report.won_amount = float(dsummary[0].get("won_amount") or 0.0)

        report.deals_by_source = _rows(client, f"""
            SELECT COALESCE(hs_analytics_source, 'Unknown') AS source,
                   COUNT(*)                          AS deals,
                   SUM(amount)                       AS amount,
                   SUM(IF(is_closed_won, amount, 0)) AS won_amount
            FROM {dt}
            GROUP BY source ORDER BY deals DESC
        """)
        report.deals_available = True
    except Exception as exc:
        LOGGER.info("Lead Tracking deals query skipped [%s]: %s", client_slug, exc)

    return report
