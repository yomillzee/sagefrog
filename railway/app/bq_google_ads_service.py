"""Sync Google Ads API → BigQuery raw tables.

All tables live in {project}.raw_google_ads. Every table includes client_key
and account_id for multi-tenant idempotency. DELETE is scoped to
client_key + account_id + metric_date range so multiple clients can share
a dataset without collisions.

The campaign_daily schema is intentionally compatible with the existing
bigquery_warehouse.create_google_campaign_mart_view() view.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from datetime import datetime, UTC
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_GOOGLE_DATASET = "raw_google_ads"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_google_ads_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    google_dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "dataset": (
            (google_dataset_id or "").strip()
            or (os.getenv("BQ_GOOGLE_DATASET_ID") or "").strip()
            or _DEFAULT_GOOGLE_DATASET
        ),
        "credentials_env": (credentials_env or "").strip() or None,
    }
    token = _route_ctx.set(payload)
    try:
        yield
    finally:
        _route_ctx.reset(token)


def _ctx() -> dict:
    return _route_ctx.get() or {
        "project": None,
        "dataset": _DEFAULT_GOOGLE_DATASET,
        "credentials_env": None,
    }


def _project_id() -> str:
    r = _ctx()
    project = (r.get("project") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for Google Ads reads/writes. Call "
            "bq_google_ads_service.route(bq_project_id=...) for this client. Refusing to "
            "silently fall back to another client's project."
        )
    return project


def _dataset_id() -> str:
    return (_ctx().get("dataset") or _DEFAULT_GOOGLE_DATASET).strip()


def _credentials_env() -> str | None:
    return _ctx().get("credentials_env")


def _bq():
    from google.cloud import bigquery
    return bigquery


def _client():
    import bigquery_service
    return bigquery_service.build_client(_project_id(), credentials_env=_credentials_env())


def _table_ref(table_name: str) -> str:
    return f"{_project_id()}.{_dataset_id()}.{table_name}"


def _schema_campaign_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("account_id",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("channel_type",     "STRING",    mode="NULLABLE"),
        bq.SchemaField("metric_date",      "DATE",      mode="REQUIRED"),
        bq.SchemaField("spend",            "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("impressions",      "INT64",     mode="NULLABLE"),
        bq.SchemaField("clicks",           "INT64",     mode="NULLABLE"),
        bq.SchemaField("conversions",      "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("conversion_value", "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("synced_at",        "TIMESTAMP", mode="NULLABLE"),
    ]


def _schema_ad_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("account_id",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_group_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("ad_group_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_id",            "STRING",    mode="REQUIRED"),
        bq.SchemaField("ad_name",          "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_type",          "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_status",        "STRING",    mode="NULLABLE"),
        bq.SchemaField("final_url",        "STRING",    mode="NULLABLE"),
        bq.SchemaField("headline_1",       "STRING",    mode="NULLABLE"),
        bq.SchemaField("headline_2",       "STRING",    mode="NULLABLE"),
        bq.SchemaField("headline_3",       "STRING",    mode="NULLABLE"),
        bq.SchemaField("description_1",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("description_2",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("headlines",        "STRING",    mode="NULLABLE"),
        bq.SchemaField("descriptions",     "STRING",    mode="NULLABLE"),
        bq.SchemaField("image_ad_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("metric_date",      "DATE",      mode="REQUIRED"),
        bq.SchemaField("spend",            "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("impressions",      "INT64",     mode="NULLABLE"),
        bq.SchemaField("clicks",           "INT64",     mode="NULLABLE"),
        bq.SchemaField("conversions",      "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("conversion_value", "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("synced_at",        "TIMESTAMP", mode="NULLABLE"),
    ]


def _schema_keyword_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("account_id",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_id",      "STRING",    mode="NULLABLE"),
        bq.SchemaField("campaign_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_group_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("ad_group_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("criterion_id",     "STRING",    mode="REQUIRED"),
        bq.SchemaField("keyword_text",     "STRING",    mode="NULLABLE"),
        bq.SchemaField("match_type",       "STRING",    mode="NULLABLE"),
        bq.SchemaField("metric_date",      "DATE",      mode="REQUIRED"),
        bq.SchemaField("spend",            "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("impressions",      "INT64",     mode="NULLABLE"),
        bq.SchemaField("clicks",           "INT64",     mode="NULLABLE"),
        bq.SchemaField("conversions",      "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("conversion_value", "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("synced_at",        "TIMESTAMP", mode="NULLABLE"),
    ]


def _schema_demographic_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("account_id",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_id",      "STRING",    mode="NULLABLE"),
        bq.SchemaField("campaign_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("channel_type",     "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_group_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("ad_group_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("criterion_id",     "STRING",    mode="NULLABLE"),
        # dimension is 'age_range' or 'gender'; segment_value holds the raw
        # Google enum and segment_label the display string, so a relabel never
        # needs a backfill and the enum stays available for matching.
        bq.SchemaField("dimension",        "STRING",    mode="REQUIRED"),
        bq.SchemaField("segment_value",    "STRING",    mode="REQUIRED"),
        bq.SchemaField("segment_label",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("is_excluded",      "BOOL",      mode="NULLABLE"),
        bq.SchemaField("criterion_status", "STRING",    mode="NULLABLE"),
        bq.SchemaField("metric_date",      "DATE",      mode="REQUIRED"),
        bq.SchemaField("spend",            "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("impressions",      "INT64",     mode="NULLABLE"),
        bq.SchemaField("clicks",           "INT64",     mode="NULLABLE"),
        bq.SchemaField("conversions",      "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("conversion_value", "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("synced_at",        "TIMESTAMP", mode="NULLABLE"),
    ]


def _schema_conversion_action_daily(bq):
    """Per-conversion-action daily conversions, at ad grain.

    Carries no spend/clicks/impressions on purpose: a row scoped to one
    conversion action has no share of the ad's cost, and repeating the ad totals
    against each action is exactly how a breakdown table starts lying.
    """
    return [
        bq.SchemaField("client_key",             "STRING",    mode="REQUIRED"),
        bq.SchemaField("account_id",             "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_id",            "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_name",          "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_group_id",            "STRING",    mode="REQUIRED"),
        bq.SchemaField("ad_group_name",          "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_id",                  "STRING",    mode="REQUIRED"),
        bq.SchemaField("conversion_action_id",   "STRING",    mode="NULLABLE"),
        bq.SchemaField("conversion_action_name", "STRING",    mode="REQUIRED"),
        bq.SchemaField("conversion_category",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("metric_date",            "DATE",      mode="REQUIRED"),
        bq.SchemaField("conversions",            "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("conversion_value",       "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("synced_at",              "TIMESTAMP", mode="NULLABLE"),
    ]


def ensure_google_ads_tables() -> None:
    bq = _bq()
    client = _client()
    project = _project_id()
    dataset = _dataset_id()
    dataset_ref = f"{project}.{dataset}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True, timeout=30)

    for table_name, schema_fn in [
        ("campaign_daily", _schema_campaign_daily),
        ("ad_daily", _schema_ad_daily),
        ("keyword_daily", _schema_keyword_daily),
        ("demographic_daily", _schema_demographic_daily),
        ("conversion_action_daily", _schema_conversion_action_daily),
    ]:
        table_id = f"{dataset_ref}.{table_name}"
        schema = schema_fn(bq)

        # Add any columns the live table lacks (e.g. headlines/descriptions JSON)
        # without dropping data — create_table(exists_ok) alone never alters schema.
        try:
            existing = client.get_table(table_id, timeout=30)
            existing_cols = {f.name for f in existing.schema}
            required_cols = {f.name for f in schema}
            if not required_cols.issubset(existing_cols):
                missing = required_cols - existing_cols
                _log.info("Google Ads %s: adding missing columns %s", table_name, missing)
                table_obj = bq.Table(table_id, schema=schema)
                table_obj.time_partitioning = bq.TimePartitioning(field="metric_date")
                client.update_table(table_obj, ["schema"])
        except Exception:
            pass  # Table doesn't exist yet — create below.

        table = bq.Table(table_id, schema=schema)
        table.time_partitioning = bq.TimePartitioning(field="metric_date")
        client.create_table(table, exists_ok=True, timeout=30)
    _log.info("Google Ads tables ensured in %s", dataset_ref)


def _mart_dataset_id() -> str:
    return (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip()


def create_google_ads_mart_views() -> dict[str, Any]:
    """Build mart views over raw_google_ads.ad_daily that the explorer and breakdowns read.

    Creates three views in marketing_marts:
      - fact_google_ads_ad_daily       — ad-level metrics with creative fields
      - fact_google_ads_ad_group_daily — ad-group aggregation (derived from ad_daily)
      - explorer_google_ads_daily      — denormalized explorer table the UI queries

    Plus two views that only appear once their raw table exists (a client synced
    before those reports were added has neither until it re-syncs):
      - explorer_google_ads_keyword_daily
      - explorer_google_ads_demographic_daily
    """
    bq = _bq()
    client = _client()
    project = _project_id()
    raw_dataset = _dataset_id()
    mart_dataset = _mart_dataset_id()
    raw_ad_table = f"`{project}.{raw_dataset}.ad_daily`"
    raw_kw_table = f"`{project}.{raw_dataset}.keyword_daily`"
    raw_campaign_table = f"`{project}.{raw_dataset}.campaign_daily`"
    mart_ref = f"{project}.{mart_dataset}"

    client.create_dataset(bq.Dataset(mart_ref), exists_ok=True, timeout=30)

    # The keyword mart view is only added when the raw keyword_daily table
    # exists (clients synced before this feature won't have it until a re-sync);
    # skip it otherwise so CREATE VIEW doesn't fail on a missing source table.
    _kw_view: dict[str, str] = {}
    try:
        client.get_table(f"{project}.{raw_dataset}.keyword_daily", timeout=15)
        _kw_view = {
            "explorer_google_ads_keyword_daily": f"""
                SELECT
                  'google_ads' AS source,
                  client_key,
                  account_id,
                  campaign_id,
                  campaign_name,
                  ad_group_id,
                  ad_group_name,
                  criterion_id,
                  keyword_text,
                  match_type,
                  metric_date AS date,
                  spend,
                  impressions,
                  clicks,
                  conversions,
                  conversion_value
                FROM {raw_kw_table}
            """,
        }
    except Exception:
        _kw_view = {}

    # Same gating for demographics: clients synced before this feature have no
    # raw demographic_daily until they re-sync, and CREATE VIEW over a missing
    # table fails the whole mart rebuild.
    _demo_view: dict[str, str] = {}
    try:
        client.get_table(f"{project}.{raw_dataset}.demographic_daily", timeout=15)
        _demo_view = {
            "explorer_google_ads_demographic_daily": f"""
                SELECT
                  'google_ads' AS source,
                  client_key,
                  account_id,
                  campaign_id,
                  campaign_name,
                  channel_type,
                  ad_group_id,
                  ad_group_name,
                  criterion_id,
                  dimension,
                  segment_value,
                  segment_label,
                  is_excluded,
                  criterion_status,
                  metric_date AS date,
                  spend,
                  impressions,
                  clicks,
                  conversions,
                  conversion_value
                FROM `{project}.{raw_dataset}.demographic_daily`
            """,
        }
    except Exception:
        _demo_view = {}

    # Conversion-action breakdown, gated the same way: a client synced before
    # this report existed has no raw conversion_action_daily until it re-syncs.
    _conv_view: dict[str, str] = {}
    try:
        client.get_table(f"{project}.{raw_dataset}.conversion_action_daily", timeout=15)
        _conv_view = {
            "explorer_google_ads_conversion_action_daily": f"""
                SELECT
                  'google_ads' AS source,
                  client_key,
                  account_id,
                  campaign_id,
                  campaign_name,
                  ad_group_id,
                  ad_group_name,
                  ad_id,
                  conversion_action_id,
                  conversion_action_name,
                  conversion_category,
                  metric_date AS date,
                  conversions,
                  conversion_value
                FROM `{project}.{raw_dataset}.conversion_action_daily`
            """,
        }
    except Exception:
        _conv_view = {}

    views = {
        "fact_google_ads_ad_daily": f"""
            SELECT
              client_key,
              metric_date AS date,
              account_id,
              campaign_id,
              campaign_name,
              ad_group_id,
              ad_group_name,
              ad_id,
              ad_name,
              ad_type,
              ad_status,
              final_url,
              headline_1,
              headline_2,
              headline_3,
              description_1,
              description_2,
              headlines,
              descriptions,
              image_ad_name,
              spend,
              impressions,
              clicks,
              conversions,
              conversion_value,
              synced_at
            FROM {raw_ad_table}
        """,
        "fact_google_ads_ad_group_daily": f"""
            SELECT
              client_key,
              metric_date AS date,
              account_id,
              campaign_id,
              ANY_VALUE(campaign_name) AS campaign_name,
              ad_group_id,
              ANY_VALUE(ad_group_name) AS ad_group_name,
              SUM(spend) AS spend,
              SUM(impressions) AS impressions,
              SUM(clicks) AS clicks,
              SUM(conversions) AS conversions,
              SUM(conversion_value) AS conversion_value
            FROM {raw_ad_table}
            GROUP BY 1, 2, 3, 4, 6
        """,
        # explorer_google_ads_daily is the denormalized table the Campaign
        # Explorer UI reads. Ad-level rows come from ad_daily (ad_group_ad).
        # Performance Max / Smart campaigns have NO ad_group_ad rows, so they'd
        # be invisible in the explorer. We UNION campaign-level rows from
        # campaign_daily for any campaign that has no ad-level rows at all,
        # surfacing PMax at the campaign grain (a synthetic single node per
        # campaign). Dedup is on client_key+campaign_id so campaigns with real
        # ad rows are never double-counted.
        "explorer_google_ads_daily": f"""
            SELECT
              'google_ads' AS source,
              client_key,
              account_id,
              campaign_id,
              campaign_name,
              ad_group_id,
              ad_group_name,
              ad_id,
              COALESCE(NULLIF(ad_name, ''), ad_id) AS ad_label,
              NULLIF(headline_1, '') AS headline_1,
              NULLIF(headline_2, '') AS headline_2,
              NULLIF(headline_3, '') AS headline_3,
              NULLIF(description_1, '') AS description_1,
              NULLIF(description_2, '') AS description_2,
              headlines,
              descriptions,
              NULLIF(image_ad_name, '') AS image_ad_name,
              NULLIF(ad_name, '') AS ad_name,
              NULLIF(final_url, '') AS final_url,
              NULLIF(ad_type, '') AS ad_type,
              metric_date AS date,
              spend,
              impressions,
              clicks,
              conversions,
              conversion_value
            FROM {raw_ad_table}
            UNION ALL
            SELECT
              'google_ads' AS source,
              c.client_key,
              c.account_id,
              c.campaign_id,
              c.campaign_name,
              c.campaign_id AS ad_group_id,
              INITCAP(REPLACE(COALESCE(NULLIF(c.channel_type, ''), 'CAMPAIGN'), '_', ' ')) AS ad_group_name,
              c.campaign_id AS ad_id,
              COALESCE(NULLIF(c.campaign_name, ''), c.campaign_id) AS ad_label,
              CAST(NULL AS STRING) AS headline_1,
              CAST(NULL AS STRING) AS headline_2,
              CAST(NULL AS STRING) AS headline_3,
              CAST(NULL AS STRING) AS description_1,
              CAST(NULL AS STRING) AS description_2,
              CAST(NULL AS STRING) AS headlines,
              CAST(NULL AS STRING) AS descriptions,
              CAST(NULL AS STRING) AS image_ad_name,
              CAST(NULL AS STRING) AS ad_name,
              CAST(NULL AS STRING) AS final_url,
              INITCAP(REPLACE(COALESCE(NULLIF(c.channel_type, ''), 'CAMPAIGN'), '_', ' ')) AS ad_type,
              c.metric_date AS date,
              c.spend,
              c.impressions,
              c.clicks,
              c.conversions,
              c.conversion_value
            FROM {raw_campaign_table} c
            WHERE NOT EXISTS (
              SELECT 1 FROM {raw_ad_table} a
              WHERE a.client_key = c.client_key
                AND a.campaign_id = c.campaign_id
            )
        """,
    }
    views.update(_kw_view)
    views.update(_demo_view)
    views.update(_conv_view)

    results: dict[str, Any] = {}
    for view_name, select_sql in views.items():
        table_id = f"{mart_ref}.{view_name}"
        sql = f"CREATE OR REPLACE VIEW `{table_id}` AS\n{select_sql}"
        try:
            client.query(sql).result(timeout=60)
            results[view_name] = "created"
            _log.info("Google Ads mart view ensured: %s", table_id)
        except Exception as exc:
            results[view_name] = f"error: {exc!s:.200}"
            _log.warning("Google Ads mart view failed [%s]: %s", view_name, exc)
    return results


def _write_campaign_daily(
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    account_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("Google Ads campaign_daily — 0 rows from API, skipping write")
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref("campaign_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND account_id = '{account_id}' "
        f"  AND metric_date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=_schema_campaign_daily(bq),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info(
        "Google Ads wrote %d rows → campaign_daily [client=%s account=%s]",
        len(rows), client_key, account_id,
    )
    return len(rows)


def _write_ad_daily(
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    account_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("Google Ads ad_daily — 0 rows from API, skipping write")
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref("ad_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND account_id = '{account_id}' "
        f"  AND metric_date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=_schema_ad_daily(bq),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info(
        "Google Ads wrote %d rows → ad_daily [client=%s account=%s]",
        len(rows), client_key, account_id,
    )
    return len(rows)


def _write_keyword_daily(
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    account_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("Google Ads keyword_daily — 0 rows from API, skipping write")
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref("keyword_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND account_id = '{account_id}' "
        f"  AND metric_date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=_schema_keyword_daily(bq),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info(
        "Google Ads wrote %d rows → keyword_daily [client=%s account=%s]",
        len(rows), client_key, account_id,
    )
    return len(rows)


def _write_demographic_daily(
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    account_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("Google Ads demographic_daily — 0 rows from API, skipping write")
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref("demographic_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND account_id = '{account_id}' "
        f"  AND metric_date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=_schema_demographic_daily(bq),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info(
        "Google Ads wrote %d rows → demographic_daily [client=%s account=%s]",
        len(rows), client_key, account_id,
    )
    return len(rows)


def _write_conversion_action_daily(
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    account_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("Google Ads conversion_action_daily — 0 rows from API, skipping write")
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref("conversion_action_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND account_id = '{account_id}' "
        f"  AND metric_date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=_schema_conversion_action_daily(bq),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info(
        "Google Ads wrote %d rows → conversion_action_daily [client=%s account=%s]",
        len(rows), client_key, account_id,
    )
    return len(rows)


def sync_google_ads_to_bq(
    customer_id: str,
    *,
    start: str,
    end: str,
    refresh_token: str,
    client_key: str,
) -> dict[str, Any]:
    """Pull Google Ads campaign + ad-level metrics and write to BQ. Returns row counts."""
    from datetime import date as _date
    from auth import GoogleAdsEnv
    import google_ads_service

    ensure_google_ads_tables()

    dev_token = (
        os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") or os.getenv("GOOGLE_DEVELOPER_TOKEN") or ""
    ).strip()
    client_id_val = (
        os.getenv("GOOGLE_ADS_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or ""
    ).strip()
    client_secret_val = (
        os.getenv("GOOGLE_ADS_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or ""
    ).strip()
    login_cid = (
        os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or os.getenv("GOOGLE_LOGIN_CUSTOMER_ID") or ""
    ).strip() or None

    env = GoogleAdsEnv(
        developer_token=dev_token,
        client_id=client_id_val,
        client_secret=client_secret_val,
        refresh_token=refresh_token,
        login_customer_id=login_cid,
    )
    ads_client = google_ads_service.build_client(env)

    customer_id_clean = str(customer_id).replace("-", "").strip()
    start_date = _date.fromisoformat(start)
    end_date = _date.fromisoformat(end)

    _log.info(
        "Google Ads sync: client=%s account=%s start=%s end=%s dataset=%s",
        client_key, customer_id_clean, start, end, _dataset_id(),
    )

    now = datetime.now(tz=UTC).isoformat()
    errors: dict[str, str] = {}

    # Campaign-level daily metrics
    campaign_raw = google_ads_service.fetch_campaign_daily_metrics(
        customer_id_clean, start=start_date, end=end_date, client=ads_client
    )
    campaign_rows = [
        {
            "client_key": client_key,
            "account_id": customer_id_clean,
            "campaign_id": str(r.get("campaign_id") or ""),
            "campaign_name": r.get("campaign_name") or None,
            "channel_type": r.get("channel_type") or None,
            "metric_date": r.get("metric_date") or "",
            "spend": float(r.get("spend") or 0.0),
            "impressions": int(r.get("impressions") or 0),
            "clicks": int(r.get("clicks") or 0),
            "conversions": float(r.get("conversions") or 0.0),
            "conversion_value": float(r.get("conversion_value") or 0.0),
            "synced_at": now,
        }
        for r in campaign_raw
        if r.get("campaign_id") and r.get("metric_date")
    ]
    n_campaign = _write_campaign_daily(
        campaign_rows, client_key=client_key, account_id=customer_id_clean, start=start, end=end
    )

    # Ad-level daily metrics with creative fields (headlines, descriptions, ad type, URL)
    n_ad = 0
    try:
        ad_raw = google_ads_service.fetch_ad_daily_metrics(
            customer_id_clean, start=start_date, end=end_date, client=ads_client
        )
        ad_rows = [
            {
                "client_key": client_key,
                "account_id": customer_id_clean,
                "campaign_id": str(r.get("campaign_id") or ""),
                "campaign_name": r.get("campaign_name"),
                "ad_group_id": str(r.get("ad_group_id") or ""),
                "ad_group_name": r.get("ad_group_name"),
                "ad_id": str(r.get("ad_id") or ""),
                "ad_name": r.get("ad_name"),
                "ad_type": r.get("ad_type"),
                "ad_status": r.get("ad_status"),
                "final_url": r.get("final_url"),
                "headline_1": r.get("headline_1"),
                "headline_2": r.get("headline_2"),
                "headline_3": r.get("headline_3"),
                "description_1": r.get("description_1"),
                "description_2": r.get("description_2"),
                "headlines": r.get("headlines"),
                "descriptions": r.get("descriptions"),
                "image_ad_name": r.get("image_ad_name"),
                "metric_date": r.get("metric_date") or "",
                "spend": float(r.get("spend") or 0.0),
                "impressions": int(r.get("impressions") or 0),
                "clicks": int(r.get("clicks") or 0),
                "conversions": float(r.get("conversions") or 0.0),
                "conversion_value": float(r.get("conversion_value") or 0.0),
                "synced_at": now,
            }
            for r in ad_raw
            if r.get("ad_id") and r.get("metric_date")
        ]
        n_ad = _write_ad_daily(
            ad_rows, client_key=client_key, account_id=customer_id_clean, start=start, end=end
        )
    except Exception as exc:
        _log.warning("Google Ads ad_daily sync failed [%s]: %s", client_key, exc)
        errors["ad_daily"] = str(exc)[:300]

    # Search-keyword daily metrics (keyword_view report). Non-fatal on failure --
    # a Display/PMax-only account has no keyword_view rows, and the rest of the
    # sync should still succeed.
    n_kw = 0
    try:
        kw_raw = google_ads_service.fetch_keyword_daily_metrics(
            customer_id_clean, start=start_date, end=end_date, client=ads_client
        )
        kw_rows = [
            {
                "client_key": client_key,
                "account_id": customer_id_clean,
                "campaign_id": str(r.get("campaign_id") or "") or None,
                "campaign_name": r.get("campaign_name"),
                "ad_group_id": str(r.get("ad_group_id") or ""),
                "ad_group_name": r.get("ad_group_name"),
                "criterion_id": str(r.get("criterion_id") or ""),
                "keyword_text": r.get("keyword_text"),
                "match_type": r.get("match_type"),
                "metric_date": r.get("metric_date") or "",
                "spend": float(r.get("spend") or 0.0),
                "impressions": int(r.get("impressions") or 0),
                "clicks": int(r.get("clicks") or 0),
                "conversions": float(r.get("conversions") or 0.0),
                "conversion_value": float(r.get("conversion_value") or 0.0),
                "synced_at": now,
            }
            for r in kw_raw
            if r.get("criterion_id") and r.get("ad_group_id") and r.get("metric_date")
        ]
        n_kw = _write_keyword_daily(
            kw_rows, client_key=client_key, account_id=customer_id_clean, start=start, end=end
        )
    except Exception as exc:
        _log.warning("Google Ads keyword_daily sync failed [%s]: %s", client_key, exc)
        errors["keyword_daily"] = str(exc)[:300]

    # Age / gender segment metrics (age_range_view + gender_view). Non-fatal:
    # Performance Max and Smart campaigns expose no ad-group criteria at all, so
    # a PMax-only account legitimately returns nothing here.
    n_demo = 0
    try:
        demo_result = google_ads_service.fetch_demographic_daily_metrics(
            customer_id_clean, start=start_date, end=end_date, client=ads_client
        )
        demo_raw = demo_result.rows
        # A view the API rejected is reported, not swallowed. Without this a bad
        # projection and an account with genuinely no demographic targeting both
        # look like "synced fine, wrote 0 rows" — which is exactly how the first
        # version of this shipped silently broken.
        if demo_result.errors:
            errors["demographic_daily"] = "; ".join(
                f"{resource}: {msg}" for resource, msg in sorted(demo_result.errors.items())
            )[:300]
        demo_rows = [
            {
                "client_key": client_key,
                "account_id": customer_id_clean,
                "campaign_id": str(r.get("campaign_id") or "") or None,
                "campaign_name": r.get("campaign_name"),
                "channel_type": r.get("channel_type"),
                "ad_group_id": str(r.get("ad_group_id") or ""),
                "ad_group_name": r.get("ad_group_name"),
                "criterion_id": str(r.get("criterion_id") or "") or None,
                "dimension": r.get("dimension") or "",
                "segment_value": r.get("segment_value") or "",
                "segment_label": r.get("segment_label"),
                "is_excluded": bool(r.get("is_excluded")),
                "criterion_status": r.get("criterion_status"),
                "metric_date": r.get("metric_date") or "",
                "spend": float(r.get("spend") or 0.0),
                "impressions": int(r.get("impressions") or 0),
                "clicks": int(r.get("clicks") or 0),
                "conversions": float(r.get("conversions") or 0.0),
                "conversion_value": float(r.get("conversion_value") or 0.0),
                "synced_at": now,
            }
            for r in demo_raw
            if r.get("dimension") and r.get("segment_value")
            and r.get("ad_group_id") and r.get("metric_date")
        ]
        n_demo = _write_demographic_daily(
            demo_rows, client_key=client_key, account_id=customer_id_clean, start=start, end=end
        )
    except Exception as exc:
        _log.warning("Google Ads demographic_daily sync failed [%s]: %s", client_key, exc)
        errors["demographic_daily"] = str(exc)[:300]

    # Per-conversion-action conversions (segments.conversion_action). Non-fatal:
    # an account that counts no conversions at all returns nothing here, and the
    # explorer's Conv. selector simply has no actions to offer.
    n_conv = 0
    try:
        conv_raw = google_ads_service.fetch_conversion_action_daily_metrics(
            customer_id_clean, start=start_date, end=end_date, client=ads_client
        )
        conv_rows = [
            {
                "client_key": client_key,
                "account_id": customer_id_clean,
                "campaign_id": str(r.get("campaign_id") or ""),
                "campaign_name": r.get("campaign_name"),
                "ad_group_id": str(r.get("ad_group_id") or ""),
                "ad_group_name": r.get("ad_group_name"),
                "ad_id": str(r.get("ad_id") or ""),
                "conversion_action_id": str(r.get("conversion_action_id") or "") or None,
                "conversion_action_name": r.get("conversion_action_name") or "",
                "conversion_category": r.get("conversion_category") or None,
                "metric_date": r.get("metric_date") or "",
                "conversions": float(r.get("conversions") or 0.0),
                "conversion_value": float(r.get("conversion_value") or 0.0),
                "synced_at": now,
            }
            for r in conv_raw
            if r.get("ad_id") and r.get("conversion_action_name") and r.get("metric_date")
        ]
        n_conv = _write_conversion_action_daily(
            conv_rows, client_key=client_key, account_id=customer_id_clean,
            start=start, end=end,
        )
    except Exception as exc:
        _log.warning("Google Ads conversion_action_daily sync failed [%s]: %s", client_key, exc)
        errors["conversion_action_daily"] = str(exc)[:300]

    # Rebuild mart views (explorer_google_ads_daily + fact_ views)
    mart_errors: dict[str, str] = {}
    try:
        mart_result = create_google_ads_mart_views()
        mart_errors = {k: v for k, v in mart_result.items() if str(v).startswith("error")}
    except Exception as exc:
        _log.warning("Google Ads mart view rebuild failed [%s]: %s", client_key, exc)
        mart_errors["mart_views"] = str(exc)[:300]
    errors.update(mart_errors)

    total = n_campaign + n_ad + n_kw + n_demo + n_conv
    _log.info(
        "Google Ads sync complete [%s]: campaign_daily=%d ad_daily=%d keyword_daily=%d "
        "demographic_daily=%d conversion_action_daily=%d",
        client_key, n_campaign, n_ad, n_kw, n_demo, n_conv,
    )
    return {
        "total_rows": total, "campaign_rows": n_campaign, "ad_rows": n_ad,
        "keyword_rows": n_kw, "demographic_rows": n_demo,
        "conversion_action_rows": n_conv, "errors": errors,
    }
