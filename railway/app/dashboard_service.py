"""Refresh Penn dashboard data and render HTML from Postgres snapshots."""

from __future__ import annotations

import html
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

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


def min_refresh_seconds() -> int:
    """Minimum seconds between manual dashboard refreshes (default 15 min)."""
    raw = (os.getenv("DASHBOARD_MIN_REFRESH_SECONDS") or "900").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 900


def _parse_refreshed_at(snapshot: dict[str, Any] | None) -> datetime | None:
    raw = (snapshot or {}).get("refreshed_at")
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def refresh_cooldown_status(snapshot: dict[str, Any] | None) -> tuple[bool, int]:
    """Return (allowed_now, seconds_remaining)."""
    last = _parse_refreshed_at(snapshot)
    if not last:
        return True, 0
    elapsed = (datetime.now(tz=UTC) - last).total_seconds()
    wait = min_refresh_seconds()
    if elapsed >= wait:
        return True, 0
    return False, int(wait - elapsed)


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


def _normalize_entity_row(row: dict[str, Any]) -> dict[str, Any]:
    entity = str(row.get("entity_level") or "campaign")
    if entity == "adset":
        parent_id = str(row.get("campaign_id") or "")
        parent_name = str(row.get("campaign_name") or "")
    elif entity == "campaign":
        parent_id = str(row.get("campaign_group_id") or "")
        parent_name = str(row.get("campaign_group_name") or "")
    else:
        parent_id = ""
        parent_name = ""
    return {
        "id": str(row.get("id") or row.get("campaign_id") or row.get("adset_id") or ""),
        "name": str(
            row.get("name")
            or row.get("campaign_name")
            or row.get("adset_name")
            or "—"
        ),
        "entity_level": entity,
        "spend": float(row.get("spend") or 0),
        "clicks": int(row.get("clicks") or 0),
        "impressions": int(row.get("impressions") or 0),
        "conversions": float(row.get("conversions") or 0),
        "parent_id": parent_id,
        "parent_name": parent_name,
    }


def _rows_for_display(rows: list[dict[str, Any]], *, min_spend: float = 0.01) -> list[dict[str, Any]]:
    """Hide zero-spend rows so inactive Google campaigns do not clutter the table."""
    visible = [r for r in rows if float(r.get("spend") or 0) >= min_spend]
    return visible if visible else rows


def _account_totals(perf: dict[str, Any]) -> dict[str, Any]:
    totals = dict(perf.get("totals") or {})
    totals.setdefault("spend", 0.0)
    totals.setdefault("clicks", 0)
    totals.setdefault("impressions", 0)
    totals.setdefault("conversions", 0.0)
    return totals


def _aggregated_paid_media(platform_totals: dict[str, Any]) -> dict[str, Any]:
    spend = clicks = impressions = conversions = 0.0
    for key in ("google", "linkedin", "meta"):
        t = platform_totals.get(key) or {}
        spend += float(t.get("spend") or 0)
        clicks += int(t.get("clicks") or 0)
        impressions += int(t.get("impressions") or 0)
        conversions += float(t.get("conversions") or 0)
    return {
        "spend": spend,
        "clicks": int(clicks),
        "impressions": int(impressions),
        "conversions": conversions,
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
        "breakdowns": {},
        "platform_totals": {},
        "aggregated_paid_media": {},
        "errors": {},
    }

    breakdowns: dict[str, dict[str, list[dict[str, Any]]]] = {}

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
            campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
            breakdowns["google"] = {"campaign": campaigns}
            payload["platform_totals"]["google"] = _account_totals(perf)
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
        li_campaigns: list[dict[str, Any]] = []
        li_groups: list[dict[str, Any]] = []
        li_totals: dict[str, Any] | None = None
        try:
            perf = linkedin_service.account_performance(cfg.linkedin_account_id, date_range=preset)
            li_campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
            li_totals = _account_totals(perf)
        except Exception as exc:
            payload["errors"]["linkedin_campaigns"] = _platform_error(exc)
        try:
            groups_perf = linkedin_service.campaign_groups_performance(
                cfg.linkedin_account_id, date_range=preset
            )
            li_groups = [_normalize_entity_row(g) for g in groups_perf.get("campaign_groups") or []]
            if li_totals is None:
                li_totals = _account_totals(groups_perf)
        except Exception as exc:
            payload["errors"]["linkedin_campaign_groups"] = _platform_error(exc)
        if li_campaigns or li_groups:
            breakdowns["linkedin"] = {
                "campaign_group": li_groups,
                "campaign": li_campaigns,
            }
        if li_totals:
            payload["platform_totals"]["linkedin"] = li_totals

    if cfg.meta_account_id:
        try:
            payload["warehouse_sync"]["meta"] = meta_service.sync_account_to_warehouse(
                cfg.meta_account_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["meta_sync"] = _platform_error(exc)
        meta_campaigns: list[dict[str, Any]] = []
        meta_adsets: list[dict[str, Any]] = []
        meta_totals: dict[str, Any] | None = None
        try:
            perf = meta_service.account_performance(cfg.meta_account_id, date_range=preset)
            meta_campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
            meta_totals = _account_totals(perf)
        except Exception as exc:
            payload["errors"]["meta_campaigns"] = _platform_error(exc)
        try:
            adsets_perf = meta_service.adsets_performance(cfg.meta_account_id, date_range=preset)
            meta_adsets = [_normalize_entity_row(a) for a in adsets_perf.get("adsets") or []]
        except Exception as exc:
            payload["errors"]["meta_adsets"] = _platform_error(exc)
        if meta_campaigns or meta_adsets:
            breakdowns["meta"] = {
                "campaign": meta_campaigns,
                "adset": meta_adsets,
            }
        if meta_totals:
            payload["platform_totals"]["meta"] = meta_totals

    payload["breakdowns"] = breakdowns

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

    payload["aggregated_paid_media"] = _aggregated_paid_media(payload["platform_totals"])

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


def _entity_level_label(level: str) -> str:
    labels = {
        "campaign": "campaign",
        "campaign_group": "campaign group",
        "adset": "ad set",
        "creative": "creative",
    }
    return labels.get(level, level.replace("_", " "))


def _entity_table(
    title: str,
    rows: list[dict[str, Any]],
    *,
    entity_level: str,
    parent_header: str | None = None,
    note: str = "",
) -> str:
    rows = _rows_for_display(rows)
    if not rows:
        return f"""
        <section class="panel">
          <h2>{_esc(title)}</h2>
          <p class="muted">No {_esc(_entity_level_label(entity_level))} data for this period.</p>
        </section>
        """
    level_badge = _entity_level_label(entity_level)
    rows_html = []
    for row in sorted(rows, key=lambda c: c.get("spend", 0), reverse=True):
        parent_cell = ""
        if parent_header:
            parent_cell = f'<td class="name">{_esc(row.get("parent_name") or "—")}</td>'
        rows_html.append(
            f"""<tr>
              <td class="mono">{_esc(row.get("id"))}</td>
              <td class="name">{_esc(row.get("name"))}</td>
              {parent_cell}
              <td class="num">{_fmt_money(float(row.get("spend") or 0))}</td>
              <td class="num">{_fmt_int(row.get("clicks") or 0)}</td>
              <td class="num">{_fmt_int(row.get("impressions") or 0)}</td>
              <td class="num">{_fmt_pct(float(row.get("clicks") or 0), float(row.get("impressions") or 1))}</td>
              <td class="num">{_fmt_int(row.get("conversions") or 0)}</td>
            </tr>"""
        )
    parent_th = f"<th>{_esc(parent_header)}</th>" if parent_header else ""
    note_html = f'<p class="table-note">{_esc(note)}</p>' if note else ""
    return f"""
    <section class="panel">
      <h2>{_esc(title)} <span class="badge">{level_badge} · {len(rows)} rows</span></h2>
      {note_html}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              {parent_th}
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
    default_note = note or "Account-level total (do not sum breakdown tables below)"
    return f"""
    <div class="card">
      <div class="card-label">{_esc(label)}</div>
      <div class="card-value">{_fmt_money(spend) if label != "GA4 (site)" else "—"}</div>
      <div class="card-stats">
        <span>{_fmt_int(clicks)} clicks</span>
        <span>{_fmt_int(impressions)} impr.</span>
        <span>{_fmt_int(conversions)} conv.</span>
      </div>
      <div class="card-note">{_esc(default_note)}</div>
    </div>
    """


def _refresh_toolbar(
    *,
    access_key: str | None,
    snapshot: dict[str, Any] | None,
    flash_message: str | None = None,
) -> str:
    if not access_key:
        return ""
    allowed, remaining = refresh_cooldown_status(snapshot)
    notice = ""
    if flash_message:
        notice = f'<div class="notice">{_esc(flash_message)}</div>'
    elif not allowed:
        mins = max(1, (remaining + 59) // 60)
        notice = (
            f'<div class="notice muted">Refresh available in ~{mins} min '
            f"(pulls Google, LinkedIn, Meta + GA4 — takes ~15–20s).</div>"
        )
    refresh_url = f"/dashboard/penn/refresh?key={quote(access_key, safe='')}"
    if allowed:
        button = (
            f'<form method="post" action="{refresh_url}" class="refresh-form">'
            f'<button type="submit" class="refresh-btn">Refresh now</button></form>'
        )
    else:
        button = '<button type="button" class="refresh-btn" disabled>Refresh now</button>'
    return f'<div class="refresh-bar">{notice}{button}</div>'


def _hierarchy_rules_html() -> str:
    return """
    <section class="panel hierarchy-rules">
      <h2>Hierarchy rules</h2>
      <p class="muted">Each platform uses different ad levels. Summary cards are <strong>account totals</strong>.
      Breakdown tables are separate — never sum campaign groups + campaigns (LinkedIn) or campaigns + ad sets (Meta).</p>
      <table class="hierarchy-table">
        <thead><tr><th>Level</th><th>LinkedIn</th><th>Meta</th><th>Google Ads</th></tr></thead>
        <tbody>
          <tr><td>Group / folder</td><td>Campaign group</td><td>—</td><td>—</td></tr>
          <tr><td>Campaign</td><td>Campaign</td><td>Campaign</td><td>Campaign</td></tr>
          <tr><td>Sub-campaign</td><td>Creative (not ad set)</td><td>Ad set</td><td>Ad group</td></tr>
        </tbody>
      </table>
    </section>
    """


def _aggregated_card(totals: dict[str, Any]) -> str:
    if not totals:
        return ""
    return f"""
    <section class="panel aggregated">
      <h2>All paid media (aggregated)</h2>
      <p class="muted">Sum of Google + LinkedIn + Meta account totals for this date range.</p>
      <div class="aggregated-stats">
        <span><strong>{_fmt_money(float(totals.get("spend") or 0))}</strong> spend</span>
        <span>{_fmt_int(totals.get("clicks") or 0)} clicks</span>
        <span>{_fmt_int(totals.get("impressions") or 0)} impressions</span>
        <span>{_fmt_int(totals.get("conversions") or 0)} conversions</span>
      </div>
    </section>
    """


def _platform_breakdown_html(breakdowns: dict[str, Any]) -> str:
    """Render per-platform tables at the correct entity levels."""
    parts: list[str] = []
    google = breakdowns.get("google") or {}
    parts.append(
        _entity_table(
            "Google Ads",
            google.get("campaign") or [],
            entity_level="campaign",
            note="entity_level=campaign only. Google has no separate ad-set level in this dashboard.",
        )
    )

    linkedin = breakdowns.get("linkedin") or {}
    if linkedin.get("campaign_group"):
        parts.append(
            _entity_table(
                "LinkedIn — campaign groups",
                linkedin.get("campaign_group") or [],
                entity_level="campaign_group",
                note="Folders above campaigns. Not comparable to Meta ad sets.",
            )
        )
    parts.append(
        _entity_table(
            "LinkedIn — campaigns",
            linkedin.get("campaign") or [],
            entity_level="campaign",
            parent_header="Campaign group",
            note="entity_level=campaign. LinkedIn has no ad set level.",
        )
    )

    meta = breakdowns.get("meta") or {}
    parts.append(
        _entity_table(
            "Meta — campaigns",
            meta.get("campaign") or [],
            entity_level="campaign",
            note="entity_level=campaign. Do not merge with ad set rows below.",
        )
    )
    if meta.get("adset"):
        parts.append(
            _entity_table(
                "Meta — ad sets",
                meta.get("adset") or [],
                entity_level="adset",
                parent_header="Parent campaign",
                note="entity_level=adset (sub-campaign). Not the same as LinkedIn campaign groups.",
            )
        )
    return "\n".join(parts)


def _breakdowns_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    breakdowns = snapshot.get("breakdowns")
    if breakdowns:
        return breakdowns
    legacy = snapshot.get("campaigns") or {}
    return {platform: {"campaign": rows} for platform, rows in legacy.items()}


def render_penn_html(
    snapshot: dict[str, Any] | None,
    *,
    access_key: str | None = None,
    flash_message: str | None = None,
) -> str:
    if not snapshot:
        toolbar = _refresh_toolbar(access_key=access_key, snapshot=None, flash_message=flash_message)
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Penn Dashboard</title>
<style>body{{font-family:system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#1a1a1a}}
.muted{{color:#666}}.refresh-bar{{margin:16px 0}}.refresh-btn{{padding:8px 16px;border-radius:8px;border:1px solid #0b5cab;background:#0b5cab;color:#fff;cursor:pointer;font-size:0.95rem}}.refresh-btn:disabled{{opacity:0.5;cursor:not-allowed}}.notice{{margin-bottom:10px;font-size:0.9rem}}</style></head><body>
<h1>Penn Community Bank — Ads Dashboard</h1>
<p class="muted">No snapshot yet. Click refresh to pull data from ad platforms.</p>
{toolbar}
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

    breakdowns = _breakdowns_from_snapshot(snapshot)
    aggregated = snapshot.get("aggregated_paid_media") or _aggregated_paid_media(totals)
    breakdown_html = _platform_breakdown_html(breakdowns)
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
    td.mono {{ font-family: ui-monospace, monospace; font-size: 0.8rem; color: var(--muted); }}
    .table-note {{ margin: 0 0 12px; font-size: 0.85rem; color: var(--muted); }}
    .hierarchy-rules p {{ margin-top: 0; }}
    .hierarchy-table {{ margin-top: 12px; font-size: 0.88rem; }}
    .aggregated-stats {{ display: flex; flex-wrap: wrap; gap: 16px 24px; font-size: 1rem; margin-top: 8px; }}
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
    .refresh-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 12px; }}
    .refresh-form {{ margin: 0; }}
    .refresh-btn {{
      padding: 8px 16px;
      border-radius: 8px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font-size: 0.9rem;
    }}
    .refresh-btn:disabled {{ opacity: 0.5; cursor: not-allowed; background: #94a3b8; border-color: #94a3b8; }}
    .notice {{ font-size: 0.88rem; color: var(--muted); }}
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
      {_refresh_toolbar(access_key=access_key, snapshot=snapshot, flash_message=flash_message)}
    </header>

    {error_html}

    {_hierarchy_rules_html()}

    {_aggregated_card(aggregated)}

    <div class="cards">
      {_summary_card("Google Ads", totals.get("google"))}
      {_summary_card("LinkedIn", totals.get("linkedin"))}
      {_summary_card("Meta", totals.get("meta"))}
      {_summary_card("GA4 (site)", totals.get("ga4"), note=ga4_note)}
    </div>

    <section class="panel">
      <h2>Daily ad spend (account level)</h2>
      <p class="table-note">One line per platform per day from warehouse — not campaign/ad set breakdown.</p>
      <canvas id="spendChart"></canvas>
    </section>

    {breakdown_html}
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
