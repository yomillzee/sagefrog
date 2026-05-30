"""Refresh Penn dashboard data and render HTML from Postgres snapshots."""

from __future__ import annotations

import html
import json
import os
from datetime import date
from typing import Any

import dashboard_snapshots
import ga4_warehouse_service
import google_ads_service
import linkedin_service
import meta_service
import warehouse
from dates_util import resolve_date_range
from penn_config import PennDashboardConfig, load_penn_config


def configured_dashboard_secret() -> str | None:
    secret = (os.getenv("DASHBOARD_SECRET") or os.getenv("CRON_SECRET") or "").strip()
    return secret or None


def verify_dashboard_key(key: str | None) -> None:
    from fastapi import HTTPException

    expected = configured_dashboard_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Dashboard access is not configured (set CRON_SECRET or DASHBOARD_SECRET).",
        )
    if not key or key.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing key query parameter.")


def _platform_error(exc: Exception) -> str:
    return str(exc)[:500]


def _normalize_campaign_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or row.get("campaign_id") or ""),
        "name": str(row.get("name") or row.get("campaign_name") or "—"),
        "spend": float(row.get("spend") or 0),
        "clicks": int(row.get("clicks") or 0),
        "impressions": int(row.get("impressions") or 0),
        "conversions": float(row.get("conversions") or 0),
        "entity_level": str(row.get("entity_level") or "campaign"),
    }


def _totals_from_campaigns(campaigns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "spend": sum(c["spend"] for c in campaigns),
        "clicks": sum(c["clicks"] for c in campaigns),
        "impressions": sum(c["impressions"] for c in campaigns),
        "conversions": sum(c["conversions"] for c in campaigns),
        "campaign_count": len(campaigns),
    }


def refresh_penn(*, date_range: str = "LAST_30_DAYS") -> dict[str, Any]:
    cfg = load_penn_config()
    start, end, preset = resolve_date_range(date_range)

    payload: dict[str, Any] = {
        "client_key": cfg.client_key,
        "label": cfg.label,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "accounts": {
            "google": cfg.google_customer_id,
            "linkedin": cfg.linkedin_account_id,
            "meta": cfg.meta_account_id,
            "ga4_client_key": cfg.ga4_client_key,
        },
        "warehouse_sync": {},
        "daily_metrics": {},
        "campaigns": {},
        "platform_totals": {},
        "errors": {},
    }

    if cfg.google_customer_id:
        try:
            payload["warehouse_sync"]["google"] = google_ads_service.sync_account_to_warehouse(
                cfg.google_customer_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["google_sync"] = _platform_error(exc)
        try:
            perf = google_ads_service.campaign_performance(cfg.google_customer_id, date_range=preset)
            campaigns = [_normalize_campaign_row(c) for c in perf.get("campaigns") or []]
            payload["campaigns"]["google"] = campaigns
            payload["platform_totals"]["google"] = perf.get("totals") or _totals_from_campaigns(campaigns)
        except Exception as exc:
            payload["errors"]["google_campaigns"] = _platform_error(exc)

    if cfg.linkedin_account_id:
        try:
            payload["warehouse_sync"]["linkedin"] = linkedin_service.sync_account_to_warehouse(
                cfg.linkedin_account_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["linkedin_sync"] = _platform_error(exc)
        try:
            perf = linkedin_service.account_performance(cfg.linkedin_account_id, date_range=preset)
            campaigns = [_normalize_campaign_row(c) for c in perf.get("campaigns") or []]
            payload["campaigns"]["linkedin"] = campaigns
            payload["platform_totals"]["linkedin"] = perf.get("totals") or _totals_from_campaigns(campaigns)
        except Exception as exc:
            payload["errors"]["linkedin_campaigns"] = _platform_error(exc)

    if cfg.meta_account_id:
        try:
            payload["warehouse_sync"]["meta"] = meta_service.sync_account_to_warehouse(
                cfg.meta_account_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["meta_sync"] = _platform_error(exc)
        try:
            perf = meta_service.account_performance(cfg.meta_account_id, date_range=preset)
            campaigns = [_normalize_campaign_row(c) for c in perf.get("campaigns") or []]
            payload["campaigns"]["meta"] = campaigns
            payload["platform_totals"]["meta"] = perf.get("totals") or _totals_from_campaigns(campaigns)
        except Exception as exc:
            payload["errors"]["meta_campaigns"] = _platform_error(exc)

    if cfg.ga4_client_key:
        try:
            payload["warehouse_sync"]["ga4"] = ga4_warehouse_service.sync_to_warehouse(
                date_range=preset,
                client_key=cfg.ga4_client_key,
            )
            ga4_account = payload["warehouse_sync"]["ga4"].get("account_id")
            payload["accounts"]["ga4"] = ga4_account
        except Exception as exc:
            payload["errors"]["ga4_sync"] = _platform_error(exc)

    if warehouse.enabled():
        for source, account_id in (
            ("google", cfg.google_customer_id),
            ("linkedin", cfg.linkedin_account_id),
            ("meta", cfg.meta_account_id),
            ("ga4", payload["accounts"].get("ga4")),
        ):
            if not account_id:
                continue
            try:
                rows = warehouse.query_metrics(
                    source=source,
                    account_id=str(account_id),
                    from_date=start,
                    to_date=end,
                    limit=5000,
                )
                payload["daily_metrics"][source] = rows
                if source == "ga4" and source not in payload["platform_totals"]:
                    payload["platform_totals"]["ga4"] = {
                        "spend": 0.0,
                        "clicks": sum(int(r.get("clicks") or 0) for r in rows),
                        "impressions": sum(int(r.get("impressions") or 0) for r in rows),
                        "conversions": sum(float(r.get("conversions") or 0) for r in rows),
                        "campaign_count": 0,
                    }
            except Exception as exc:
                payload["errors"][f"{source}_daily"] = _platform_error(exc)

    dashboard_snapshots.save_snapshot(cfg.client_key, payload)
    return payload


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def _fmt_pct(num: float, den: float) -> str:
    if not den:
        return "—"
    return f"{100.0 * num / den:.2f}%"


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _campaign_table(title: str, campaigns: list[dict[str, Any]]) -> str:
    if not campaigns:
        return f"""
        <section class="panel">
          <h2>{_esc(title)}</h2>
          <p class="muted">No campaign data for this period.</p>
        </section>
        """
    rows_html = []
    for row in sorted(campaigns, key=lambda c: c.get("spend", 0), reverse=True):
        rows_html.append(
            f"""<tr>
              <td class="name">{_esc(row.get("name"))}</td>
              <td class="num">{_fmt_money(float(row.get("spend") or 0))}</td>
              <td class="num">{_fmt_int(row.get("clicks") or 0)}</td>
              <td class="num">{_fmt_int(row.get("impressions") or 0)}</td>
              <td class="num">{_fmt_pct(float(row.get("clicks") or 0), float(row.get("impressions") or 1))}</td>
              <td class="num">{_fmt_int(row.get("conversions") or 0)}</td>
            </tr>"""
        )
    return f"""
    <section class="panel">
      <h2>{_esc(title)} <span class="badge">{len(campaigns)} campaigns</span></h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Spend</th>
              <th>Clicks</th>
              <th>Impressions</th>
              <th>CTR</th>
              <th>Conv.</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
    </section>
    """


def _summary_card(label: str, totals: dict[str, Any] | None, *, note: str = "") -> str:
    if not totals:
        return f"""
        <div class="card card-empty">
          <div class="card-label">{_esc(label)}</div>
          <div class="card-value muted">No data</div>
          {f'<div class="card-note">{_esc(note)}</div>' if note else ''}
        </div>
        """
    spend = float(totals.get("spend") or 0)
    clicks = int(totals.get("clicks") or 0)
    impressions = int(totals.get("impressions") or 0)
    conversions = float(totals.get("conversions") or 0)
    count = int(totals.get("campaign_count") or 0)
    return f"""
    <div class="card">
      <div class="card-label">{_esc(label)}</div>
      <div class="card-value">{_fmt_money(spend) if label != "GA4 (site)" else "—"}</div>
      <div class="card-stats">
        <span>{_fmt_int(clicks)} clicks</span>
        <span>{_fmt_int(impressions)} impr.</span>
        <span>{_fmt_int(conversions)} conv.</span>
        {f'<span>{count} campaigns</span>' if count else ''}
      </div>
      {f'<div class="card-note">{_esc(note)}</div>' if note else ''}
    </div>
    """


def render_penn_html(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Penn Dashboard</title>
<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#1a1a1a}
.muted{color:#666}</style></head><body>
<h1>Penn Community Bank — Ads Dashboard</h1>
<p class="muted">No snapshot yet. Run <code>POST /internal/sync-penn</code> with your cron job first.</p>
</body></html>"""

    label = snapshot.get("label") or "Penn Community Bank"
    dr = snapshot.get("date_range") or {}
    refreshed = snapshot.get("refreshed_at") or "—"
    preset = dr.get("preset") or ""
    range_label = f"{dr.get('start', '')} → {dr.get('end', '')} ({preset})"

    totals = snapshot.get("platform_totals") or {}
    errors = snapshot.get("errors") or {}
    error_html = ""
    if errors:
        items = "".join(f"<li><strong>{_esc(k)}</strong>: {_esc(v)}</li>" for k, v in errors.items())
        error_html = f'<div class="errors"><strong>Partial refresh warnings</strong><ul>{items}</ul></div>'

    chart_data = snapshot.get("daily_metrics") or {}
    dates_set: set[str] = set()
    for rows in chart_data.values():
        for row in rows:
            dates_set.add(str(row.get("metric_date") or "")[:10])
    dates = sorted(d for d in dates_set if d)

    def series_for(source: str) -> list[float]:
        by_date = {str(r.get("metric_date") or "")[:10]: float(r.get("spend") or 0) for r in chart_data.get(source, [])}
        return [by_date.get(d, 0.0) for d in dates]

    chart_json = json.dumps(
        {
            "labels": dates,
            "datasets": [
                {"label": "Google Ads", "data": series_for("google"), "borderColor": "#4285f4"},
                {"label": "LinkedIn", "data": series_for("linkedin"), "borderColor": "#0a66c2"},
                {"label": "Meta", "data": series_for("meta"), "borderColor": "#1877f2"},
            ],
        }
    )

    campaigns = snapshot.get("campaigns") or {}
    ga4_note = "Sessions / page views / conversions (no ad spend)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — Ads Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #f4f6f8;
      --panel: #fff;
      --text: #1a2332;
      --muted: #5c6773;
      --border: #dde3ea;
      --accent: #0b5cab;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px 20px 48px; }}
    header {{ margin-bottom: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 1.75rem; }}
    .meta {{ color: var(--muted); font-size: 0.95rem; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
      box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }}
    .card-empty {{ opacity: 0.85; }}
    .card-label {{ font-size: 0.85rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
    .card-value {{ font-size: 1.6rem; font-weight: 700; margin: 6px 0; }}
    .card-stats {{ display: flex; flex-wrap: wrap; gap: 8px 12px; font-size: 0.85rem; color: var(--muted); }}
    .card-note {{ margin-top: 8px; font-size: 0.8rem; color: var(--muted); }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 20px;
      box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }}
    h2 {{ margin: 0 0 14px; font-size: 1.15rem; }}
    .badge {{
      font-size: 0.75rem;
      font-weight: 600;
      background: #eef3f9;
      color: var(--accent);
      padding: 2px 8px;
      border-radius: 999px;
      vertical-align: middle;
    }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .03em; }}
    td.num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    td.name {{ max-width: 360px; }}
    .errors {{
      background: #fff8e6;
      border: 1px solid #f0d080;
      border-radius: 10px;
      padding: 12px 16px;
      margin-bottom: 20px;
      font-size: 0.9rem;
    }}
    .errors ul {{ margin: 8px 0 0; padding-left: 20px; }}
    canvas {{ max-height: 300px; }}
    .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>{_esc(label)}</h1>
      <div class="meta">
        Paid media performance · {_esc(range_label)}<br>
        Last refreshed: {_esc(refreshed)} UTC
      </div>
    </header>

    {error_html}

    <div class="cards">
      {_summary_card("Google Ads", totals.get("google"))}
      {_summary_card("LinkedIn", totals.get("linkedin"))}
      {_summary_card("Meta", totals.get("meta"))}
      {_summary_card("GA4 (site)", totals.get("ga4"), note=ga4_note)}
    </div>

    <section class="panel">
      <h2>Daily ad spend</h2>
      <canvas id="spendChart"></canvas>
    </section>

    {_campaign_table("Google Ads — campaigns", campaigns.get("google") or [])}
    {_campaign_table("LinkedIn — campaigns", campaigns.get("linkedin") or [])}
    {_campaign_table("Meta — campaigns", campaigns.get("meta") or [])}
  </div>
  <script>
    const chartPayload = {chart_json};
    const ctx = document.getElementById('spendChart');
    if (ctx && chartPayload.labels.length) {{
      new Chart(ctx, {{
        type: 'line',
        data: chartPayload,
        options: {{
          responsive: true,
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            y: {{ beginAtZero: true, ticks: {{ callback: v => '$' + v.toLocaleString() }} }}
          }},
          plugins: {{ legend: {{ position: 'bottom' }} }}
        }}
      }});
    }}
  </script>
</body>
</html>"""
