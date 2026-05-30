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
    elif entity == "ad":
        parent_id = str(row.get("adset_id") or "")
        parent_name = str(row.get("adset_name") or "")
    elif entity == "creative":
        parent_id = str(row.get("campaign_id") or "")
        parent_name = str(row.get("campaign_name") or "")
    elif entity == "campaign":
        parent_id = str(row.get("campaign_group_id") or "")
        parent_name = str(row.get("campaign_group_name") or "")
    else:
        parent_id = ""
        parent_name = ""
    out: dict[str, Any] = {
        "id": str(
            row.get("id")
            or row.get("campaign_id")
            or row.get("adset_id")
            or row.get("ad_id")
            or ""
        ),
        "name": str(
            row.get("name")
            or row.get("campaign_name")
            or row.get("adset_name")
            or row.get("ad_name")
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
    for key in ("thumbnail_url", "image_url", "media_type", "creative_name"):
        val = row.get(key)
        if val:
            out[key] = str(val)
    return out


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
        li_groups: list[dict[str, Any]] = []
        li_campaigns: list[dict[str, Any]] = []
        li_creatives: list[dict[str, Any]] = []
        li_totals: dict[str, Any] | None = None
        try:
            groups_perf = linkedin_service.campaign_groups_performance(
                cfg.linkedin_account_id, date_range=preset
            )
            li_groups = [_normalize_entity_row(g) for g in groups_perf.get("campaign_groups") or []]
            li_totals = _account_totals(groups_perf)
        except Exception as exc:
            payload["errors"]["linkedin_campaign_groups"] = _platform_error(exc)
        try:
            perf = linkedin_service.account_performance(cfg.linkedin_account_id, date_range=preset)
            li_campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
        except Exception as exc:
            payload["errors"]["linkedin_campaigns"] = _platform_error(exc)
        try:
            creatives_perf = linkedin_service.creatives_performance(
                cfg.linkedin_account_id, date_range=preset
            )
            li_creatives = [_normalize_entity_row(c) for c in creatives_perf.get("creatives") or []]
        except Exception as exc:
            payload["errors"]["linkedin_creatives"] = _platform_error(exc)
        if li_groups or li_campaigns or li_creatives:
            breakdowns["linkedin"] = {
                "campaign_group": li_groups,
                "campaign": li_campaigns,
                "creative": li_creatives,
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
        meta_ads: list[dict[str, Any]] = []
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
        try:
            ads_perf = meta_service.ads_performance(cfg.meta_account_id, date_range=preset)
            meta_ads = [_normalize_entity_row(a) for a in ads_perf.get("ads") or []]
        except Exception as exc:
            payload["errors"]["meta_ads"] = _platform_error(exc)
        if meta_campaigns or meta_adsets or meta_ads:
            breakdowns["meta"] = {
                "campaign": meta_campaigns,
                "adset": meta_adsets,
                "ad": meta_ads,
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
        "ad": "ad",
        "creative": "creative",
    }
    return labels.get(level, level.replace("_", " "))


def _drillable_table(
    platform: str,
    title: str,
    rows: list[dict[str, Any]],
    *,
    entity_level: str,
    drill_hint: str = "",
    note: str = "",
) -> str:
    rows = _rows_for_display(rows)
    level_badge = _entity_level_label(entity_level)
    drillable = platform in ("linkedin", "meta") and entity_level in (
        "campaign_group",
        "campaign",
    )
    hint = drill_hint or (
        "Click a row to compare child performance in the detail panel →"
        if drillable
        else ""
    )
    if not rows:
        return f"""
        <section class="panel platform-panel platform-{platform}" data-platform="{platform}">
          <div class="panel-head">
            <h2>{_esc(title)}</h2>
            <span class="badge">{level_badge} · 0 rows</span>
          </div>
          <p class="muted">No {_esc(level_badge)} data for this period.</p>
        </section>
        """
    rows_html = []
    for row in sorted(rows, key=lambda c: c.get("spend", 0), reverse=True):
        spend = float(row.get("spend") or 0)
        clicks = int(row.get("clicks") or 0)
        impressions = int(row.get("impressions") or 0)
        conv = float(row.get("conversions") or 0)
        row_class = "drill-row" if drillable else ""
        row_attrs = ""
        if drillable:
            row_attrs = (
                f'data-platform="{_esc(platform)}" '
                f'data-level="{_esc(entity_level)}" '
                f'data-id="{_esc(row.get("id"))}" '
                f'data-name="{_esc(row.get("name"))}" '
                f'tabindex="0" role="button" '
                f'aria-label="View breakdown for {_esc(row.get("name"))}"'
            )
        chevron = '<td class="chevron">›</td>' if drillable else ""
        cpc = _fmt_money(spend / clicks) if clicks else "—"
        rows_html.append(
            f"""<tr class="{row_class}" {row_attrs}>
              {chevron}
              <td class="name">{_esc(row.get("name"))}</td>
              <td class="num">{_fmt_money(spend)}</td>
              <td class="num">{_fmt_int(clicks)}</td>
              <td class="num">{_fmt_int(impressions)}</td>
              <td class="num">{_fmt_pct(clicks, impressions or 1)}</td>
              <td class="num">{_fmt_int(conv)}</td>
              <td class="num">{cpc}</td>
            </tr>"""
        )
    chevron_th = '<th class="chevron-col"></th>' if drillable else ""
    note_html = f'<p class="table-note">{_esc(note)}</p>' if note else ""
    hint_html = f'<p class="drill-hint">{_esc(hint)}</p>' if hint else ""
    return f"""
    <section class="panel platform-panel platform-{platform}" data-platform="{platform}">
      <div class="panel-head">
        <h2>{_esc(title)}</h2>
        <span class="badge">{level_badge} · {len(rows)} rows</span>
      </div>
      {note_html}
      {hint_html}
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              {chevron_th}
              <th>Name</th>
              <th>Spend</th>
              <th>Clicks</th>
              <th>Impressions</th>
              <th>CTR</th>
              <th>Conv.</th>
              <th>CPC</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
    </section>
    """


def _entity_table(
    title: str,
    rows: list[dict[str, Any]],
    *,
    entity_level: str,
    parent_header: str | None = None,
    note: str = "",
) -> str:
    """Non-drillable table (Google Ads)."""
    rows = _rows_for_display(rows)
    if not rows:
        return f"""
        <section class="panel platform-panel platform-google">
          <div class="panel-head">
            <h2>{_esc(title)}</h2>
          </div>
          <p class="muted">No {_esc(_entity_level_label(entity_level))} data for this period.</p>
        </section>
        """
    level_badge = _entity_level_label(entity_level)
    rows_html = []
    for row in sorted(rows, key=lambda c: c.get("spend", 0), reverse=True):
        spend = float(row.get("spend") or 0)
        clicks = int(row.get("clicks") or 0)
        cpc = _fmt_money(spend / clicks) if clicks else "—"
        rows_html.append(
            f"""<tr>
              <td class="name">{_esc(row.get("name"))}</td>
              <td class="num">{_fmt_money(spend)}</td>
              <td class="num">{_fmt_int(clicks)}</td>
              <td class="num">{_fmt_int(row.get("impressions") or 0)}</td>
              <td class="num">{_fmt_pct(clicks, float(row.get("impressions") or 1))}</td>
              <td class="num">{_fmt_int(row.get("conversions") or 0)}</td>
              <td class="num">{cpc}</td>
            </tr>"""
        )
    note_html = f'<p class="table-note">{_esc(note)}</p>' if note else ""
    return f"""
    <section class="panel platform-panel platform-google">
      <div class="panel-head">
        <h2>{_esc(title)} <span class="badge">{level_badge} · {len(rows)} rows</span></h2>
      </div>
      {note_html}
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Spend</th>
              <th>Clicks</th>
              <th>Impressions</th>
              <th>CTR</th>
              <th>Conv.</th>
              <th>CPC</th>
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
            "Google Ads — campaigns",
            google.get("campaign") or [],
            entity_level="campaign",
        )
    )

    linkedin = breakdowns.get("linkedin") or {}
    groups = linkedin.get("campaign_group") or []
    if groups:
        parts.append(
            _drillable_table(
                "linkedin",
                "LinkedIn — campaign groups",
                groups,
                entity_level="campaign_group",
                note=(
                    "Top-level groups from the Marketing API (matches GPT linkedinCampaignGroupsPerformance). "
                    "Click a group to compare campaigns inside it."
                ),
            )
        )
    else:
        parts.append(
            """
        <section class="panel platform-panel platform-linkedin">
          <div class="panel-head"><h2>LinkedIn — campaign groups</h2></div>
          <p class="muted">No campaign group data — click Refresh now.</p>
        </section>
        """
        )

    meta = breakdowns.get("meta") or {}
    meta_campaigns = meta.get("campaign") or []
    meta_adset_count = len(meta.get("adset") or [])
    meta_ad_count = len(meta.get("ad") or [])
    parts.append(
        _drillable_table(
            "meta",
            "Meta — campaigns",
            meta_campaigns,
            entity_level="campaign",
            note=(
                f"Same {len(meta_campaigns)} campaigns as metaPerformance in GPT. "
                f"Drill down: {meta_adset_count} ad sets → {meta_ad_count} ads with creative previews."
            ),
            drill_hint="Click a campaign → ad sets → individual ads with thumbnails →",
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
    breakdowns_json = json.dumps(breakdowns)
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
      --bg: #eef1f5;
      --panel: #fff;
      --text: #0f1c2e;
      --muted: #5a6578;
      --border: #d8dee8;
      --accent: #0b5cab;
      --navy: #0a2540;
      --navy-light: #123456;
      --gold: #b8922e;
      --shadow: 0 4px 24px rgba(10, 37, 64, 0.08);
      --shadow-sm: 0 1px 3px rgba(10, 37, 64, 0.06);
      --radius: 14px;
      --google: #4285f4;
      --linkedin: #0a66c2;
      --meta: #1877f2;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    body.drill-open {{ overflow: hidden; }}
    .hero {{
      background: linear-gradient(135deg, var(--navy) 0%, var(--navy-light) 100%);
      color: #fff;
      padding: 28px 0 32px;
      margin-bottom: 28px;
      box-shadow: var(--shadow);
    }}
    .hero-inner {{ max-width: 1280px; margin: 0 auto; padding: 0 24px; }}
    .hero h1 {{ margin: 0 0 6px; font-size: 1.85rem; font-weight: 700; letter-spacing: -0.02em; }}
    .hero .meta {{ color: rgba(255,255,255,0.75); font-size: 0.92rem; }}
    .hero .refresh-bar {{ margin-top: 16px; }}
    .hero .notice {{ color: rgba(255,255,255,0.85); }}
    .hero .refresh-btn {{
      background: rgba(255,255,255,0.15);
      border-color: rgba(255,255,255,0.35);
      backdrop-filter: blur(4px);
    }}
    .hero .refresh-btn:hover:not(:disabled) {{ background: rgba(255,255,255,0.25); }}
    .layout {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 0;
      max-width: 1280px;
      margin: 0 auto;
      padding: 0 24px 48px;
    }}
    @media (min-width: 1100px) {{
      .layout.has-drill {{ grid-template-columns: 1fr 380px; gap: 24px; align-items: start; }}
    }}
    .wrap {{ min-width: 0; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 20px;
      box-shadow: var(--shadow-sm);
      transition: box-shadow 0.2s, transform 0.2s;
    }}
    .card:hover {{ box-shadow: var(--shadow); transform: translateY(-1px); }}
    .card-empty {{ opacity: 0.85; }}
    .card-label {{ font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; font-weight: 600; }}
    .card-value {{ font-size: 1.75rem; font-weight: 700; margin: 8px 0 4px; letter-spacing: -0.02em; }}
    .card-stats {{ display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 0.84rem; color: var(--muted); }}
    .card-note {{ margin-top: 10px; font-size: 0.78rem; color: var(--muted); line-height: 1.35; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 22px 24px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-sm);
    }}
    .panel.aggregated {{
      background: linear-gradient(to right, #fafbfd, var(--panel));
      border-left: 4px solid var(--gold);
    }}
    .panel-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 16px;
      margin-bottom: 12px;
    }}
    h2 {{ margin: 0; font-size: 1.12rem; font-weight: 650; letter-spacing: -0.01em; }}
    .platform-panel {{ border-left: 4px solid var(--border); }}
    .platform-google {{ border-left-color: var(--google); }}
    .platform-linkedin {{ border-left-color: var(--linkedin); }}
    .platform-meta {{ border-left-color: var(--meta); }}
    .badge {{
      font-size: 0.72rem;
      font-weight: 600;
      background: #eef3f9;
      color: var(--accent);
      padding: 3px 10px;
      border-radius: 999px;
      white-space: nowrap;
    }}
    .table-wrap {{ overflow-x: auto; margin: 0 -4px; }}
    .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    .data-table th, .data-table td {{ padding: 11px 12px; border-bottom: 1px solid var(--border); text-align: left; }}
    .data-table th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      background: #f8fafc;
      position: sticky;
      top: 0;
    }}
    .data-table tbody tr:last-child td {{ border-bottom: none; }}
    td.num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    td.name {{ max-width: 340px; font-weight: 500; }}
    td.chevron, th.chevron-col {{
      width: 28px;
      padding-right: 0;
      color: var(--muted);
      font-size: 1.1rem;
    }}
    tr.drill-row {{ cursor: pointer; transition: background 0.15s; }}
    tr.drill-row:hover {{ background: #f0f5fb; }}
    tr.drill-row.selected {{ background: #e3eef9; outline: 2px solid var(--accent); outline-offset: -2px; }}
    tr.drill-row:focus-visible {{ outline: 2px solid var(--accent); outline-offset: -2px; }}
    .drill-hint {{
      margin: 0 0 12px;
      font-size: 0.82rem;
      color: var(--accent);
      font-weight: 500;
    }}
    .table-note {{ margin: 0 0 10px; font-size: 0.82rem; color: var(--muted); line-height: 1.4; }}
    .aggregated-stats {{ display: flex; flex-wrap: wrap; gap: 16px 28px; font-size: 1.02rem; margin-top: 4px; }}
    .aggregated-stats strong {{ font-size: 1.35rem; color: var(--navy); }}
    .errors {{
      background: #fff8e6;
      border: 1px solid #f0d080;
      border-radius: var(--radius);
      padding: 14px 18px;
      margin-bottom: 20px;
      font-size: 0.88rem;
    }}
    .errors ul {{ margin: 8px 0 0; padding-left: 20px; }}
    canvas {{ max-height: 280px; }}
    .muted {{ color: var(--muted); }}
    .refresh-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }}
    .refresh-form {{ margin: 0; }}
    .refresh-btn {{
      padding: 9px 18px;
      border-radius: 9px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font-size: 0.88rem;
      font-weight: 600;
      transition: background 0.15s;
    }}
    .refresh-btn:hover:not(:disabled) {{ filter: brightness(1.08); }}
    .refresh-btn:disabled {{ opacity: 0.45; cursor: not-allowed; background: #94a3b8; border-color: #94a3b8; }}
    .notice {{ font-size: 0.86rem; color: var(--muted); }}

    .drill-panel {{
      position: fixed;
      inset: 0;
      z-index: 100;
      background: rgba(10, 37, 64, 0.35);
      backdrop-filter: blur(2px);
      display: flex;
      justify-content: flex-end;
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.25s, visibility 0.25s;
    }}
    .drill-panel.open {{ opacity: 1; visibility: visible; }}
    .drill-sheet {{
      width: min(480px, 100vw);
      height: 100%;
      background: var(--panel);
      box-shadow: -8px 0 32px rgba(10, 37, 64, 0.15);
      display: flex;
      flex-direction: column;
      transform: translateX(100%);
      transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    .drill-panel.open .drill-sheet {{ transform: translateX(0); }}
    @media (min-width: 1100px) {{
      .drill-panel {{
        position: sticky;
        top: 24px;
        inset: auto;
        background: none;
        backdrop-filter: none;
        opacity: 1;
        visibility: visible;
        height: fit-content;
        max-height: calc(100vh - 48px);
      }}
      .drill-panel:not(.open) {{ display: none; }}
      .layout.has-drill .drill-panel {{ display: flex; }}
      .drill-sheet {{
        width: 100%;
        height: auto;
        max-height: calc(100vh - 48px);
        border-radius: var(--radius);
        border: 1px solid var(--border);
        box-shadow: var(--shadow);
        transform: none;
      }}
    }}
    .drill-header {{
      padding: 18px 20px 14px;
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
    }}
    .drill-header-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }}
    .drill-header h3 {{ margin: 0; font-size: 1rem; font-weight: 650; line-height: 1.35; }}
    .drill-parent {{ font-size: 0.82rem; color: var(--muted); }}
    .drill-actions {{ display: flex; gap: 6px; }}
    .drill-btn {{
      background: #f0f4f8;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 0.8rem;
      cursor: pointer;
      color: var(--text);
    }}
    .drill-btn:hover {{ background: #e3eaf2; }}
    .drill-btn.icon {{ padding: 6px 11px; font-size: 1.1rem; line-height: 1; }}
    .drill-body {{ overflow: auto; flex: 1; padding: 0 12px 16px; }}
    .drill-empty {{ padding: 32px 20px; text-align: center; color: var(--muted); font-size: 0.9rem; }}
    .drill-summary {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      padding: 14px 8px 8px;
    }}
    .drill-stat {{
      background: #f8fafc;
      border-radius: 10px;
      padding: 10px 12px;
      text-align: center;
    }}
    .drill-stat-val {{ font-size: 1.1rem; font-weight: 700; display: block; }}
    .drill-stat-lbl {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
    .drill-row-nested {{ cursor: pointer; }}
    .drill-row-nested:hover {{ background: #f0f5fb; }}
    .share-bar {{
      height: 4px;
      background: #e8edf3;
      border-radius: 2px;
      margin-top: 4px;
      overflow: hidden;
    }}
    .share-bar-fill {{ height: 100%; background: var(--accent); border-radius: 2px; }}
    .ad-thumb {{
      width: 44px;
      height: 44px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--border);
      display: block;
      background: #f0f4f8;
    }}
    .ad-thumb-placeholder {{
      width: 44px;
      height: 44px;
      border-radius: 8px;
      background: linear-gradient(135deg, #eef1f5, #e2e8f0);
      display: block;
      flex-shrink: 0;
    }}
    .ad-name-cell {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .ad-name-text {{ min-width: 0; }}
    .ad-creative-sub {{
      display: block;
      font-size: 0.72rem;
      color: var(--muted);
      margin-top: 2px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 200px;
    }}
    td.creative-col {{ width: 52px; padding-right: 0; }}
  </style>
</head>
<body>
  <div class="hero">
    <div class="hero-inner">
      <h1>{_esc(label)}</h1>
      <div class="meta">
        Paid media performance · {_esc(range_label)}<br>
        Last refreshed: {_esc(refreshed)} UTC
      </div>
      {_refresh_toolbar(access_key=access_key, snapshot=snapshot, flash_message=flash_message)}
    </div>
  </div>

  <div class="layout" id="layout">
    <div class="wrap">
      {error_html}

      {_aggregated_card(aggregated)}

      <div class="cards">
        {_summary_card("Google Ads", totals.get("google"))}
        {_summary_card("LinkedIn", totals.get("linkedin"), note="Account total · click a group to drill down")}
        {_summary_card("Meta", totals.get("meta"), note="Account total · campaign → ad set → ad drill-down")}
        {_summary_card("GA4 (site)", totals.get("ga4"), note=ga4_note)}
      </div>

      <section class="panel">
        <div class="panel-head"><h2>Daily ad spend (account level)</h2></div>
        <p class="table-note">One line per platform per day from warehouse — not campaign/ad set breakdown.</p>
        <canvas id="spendChart"></canvas>
      </section>

      {breakdown_html}
    </div>

    <aside id="drillPanel" class="drill-panel" aria-hidden="true">
      <div class="drill-sheet">
        <div class="drill-header">
          <div class="drill-header-top">
            <div class="drill-actions">
              <button type="button" class="drill-btn" id="drillBack" hidden>← Back</button>
            </div>
            <div class="drill-actions">
              <button type="button" class="drill-btn icon" id="drillClose" aria-label="Close">×</button>
            </div>
          </div>
          <h3 id="drillTitle">Breakdown</h3>
          <div class="drill-parent" id="drillParent"></div>
        </div>
        <div class="drill-summary" id="drillSummary"></div>
        <div class="drill-body">
          <div class="table-wrap">
            <table class="data-table" id="drillTable">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Spend</th>
                  <th>Clicks</th>
                  <th>CTR</th>
                  <th>Conv.</th>
                  <th>Share</th>
                </tr>
              </thead>
              <tbody id="drillTbody"></tbody>
            </table>
          </div>
          <div class="drill-empty" id="drillEmpty" hidden>No child rows for this selection.</div>
        </div>
      </div>
    </aside>
  </div>
  <script>
    const chartPayload = {chart_json};
    const breakdowns = {breakdowns_json};

    const DRILL_MAP = {{
      'linkedin:campaign_group': {{ childLevel: 'campaign', childLabel: 'Campaigns' }},
      'linkedin:campaign': {{ childLevel: 'creative', childLabel: 'Creatives / ads' }},
      'meta:campaign': {{ childLevel: 'adset', childLabel: 'Ad sets' }},
      'meta:adset': {{ childLevel: 'ad', childLabel: 'Ads' }},
    }};

    const fmtMoney = n => '$' + Number(n || 0).toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    const fmtInt = n => Number(n || 0).toLocaleString();
    const fmtPct = (n, d) => d ? (100 * n / d).toFixed(2) + '%' : '—';
    const escHtml = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

    const drillPanel = document.getElementById('drillPanel');
    const drillTitle = document.getElementById('drillTitle');
    const drillParent = document.getElementById('drillParent');
    const drillTbody = document.getElementById('drillTbody');
    const drillEmpty = document.getElementById('drillEmpty');
    const drillSummary = document.getElementById('drillSummary');
    const drillBack = document.getElementById('drillBack');
    const drillClose = document.getElementById('drillClose');
    const layout = document.getElementById('layout');

    let drillStack = [];
    let selectedRow = null;

    function childRows(platform, level, parentId) {{
      const rule = DRILL_MAP[platform + ':' + level];
      if (!rule) return [];
      const pool = (breakdowns[platform] || {{}})[rule.childLevel] || [];
      return pool.filter(r => String(r.parent_id || '') === String(parentId))
        .sort((a, b) => (b.spend || 0) - (a.spend || 0));
    }}

    function renderDrillSummary(rows) {{
      const spend = rows.reduce((s, r) => s + (r.spend || 0), 0);
      const clicks = rows.reduce((s, r) => s + (r.clicks || 0), 0);
      const conv = rows.reduce((s, r) => s + (r.conversions || 0), 0);
      drillSummary.innerHTML = `
        <div class="drill-stat"><span class="drill-stat-val">${{fmtMoney(spend)}}</span><span class="drill-stat-lbl">Spend</span></div>
        <div class="drill-stat"><span class="drill-stat-val">${{fmtInt(clicks)}}</span><span class="drill-stat-lbl">Clicks</span></div>
        <div class="drill-stat"><span class="drill-stat-val">${{fmtInt(conv)}}</span><span class="drill-stat-lbl">Conv.</span></div>`;
    }}

    function renderDrillTable(rows, platform, currentLevel) {{
      const totalSpend = rows.reduce((s, r) => s + (r.spend || 0), 0) || 1;
      const nestedRule = DRILL_MAP[platform + ':' + currentLevel];
      const showCreative = currentLevel === 'ad';
      const thead = document.querySelector('#drillTable thead tr');
      if (thead) {{
        thead.innerHTML = showCreative
          ? '<th class="creative-col"></th><th>Name</th><th>Spend</th><th>Clicks</th><th>CTR</th><th>Conv.</th><th>Share</th>'
          : '<th>Name</th><th>Spend</th><th>Clicks</th><th>CTR</th><th>Conv.</th><th>Share</th>';
      }}
      drillTbody.innerHTML = rows.map(r => {{
        const share = 100 * (r.spend || 0) / totalSpend;
        const canNest = nestedRule && childRows(platform, currentLevel, r.id).length > 0;
        const nestAttrs = canNest
          ? `class="drill-row-nested" data-platform="${{platform}}" data-level="${{currentLevel}}" data-id="${{r.id}}" data-name="${{escHtml(r.name)}}" tabindex="0" role="button"`
          : '';
        const thumbUrl = r.thumbnail_url || r.image_url || '';
        const thumbHtml = thumbUrl
          ? `<img class="ad-thumb" src="${{escHtml(thumbUrl)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`
          : '<span class="ad-thumb-placeholder"></span>';
        const creativeSub = (r.creative_name && r.creative_name !== r.name)
          ? `<span class="ad-creative-sub">${{escHtml(r.creative_name)}}</span>` : '';
        const typeBadge = r.media_type
          ? `<span class="ad-creative-sub">${{escHtml(r.media_type)}}</span>` : '';
        const nameCell = showCreative
          ? `<td class="creative-col">${{thumbHtml}}</td><td class="name"><div class="ad-name-text">${{escHtml(r.name || '—')}}${{creativeSub}}${{typeBadge}}</div></td>`
          : `<td class="name">${{escHtml(r.name || '—')}}${{canNest ? ' <span style="color:var(--muted);font-size:0.75rem">›</span>' : ''}}</td>`;
        return `<tr ${{nestAttrs}}>
          ${{nameCell}}
          <td class="num">${{fmtMoney(r.spend)}}</td>
          <td class="num">${{fmtInt(r.clicks)}}</td>
          <td class="num">${{fmtPct(r.clicks, r.impressions)}}</td>
          <td class="num">${{fmtInt(r.conversions)}}</td>
          <td class="num"><div class="share-bar"><div class="share-bar-fill" style="width:${{Math.max(share, 2)}}%"></div></div>${{share.toFixed(0)}}%</td>
        </tr>`;
      }}).join('');
      drillEmpty.hidden = rows.length > 0;
      document.getElementById('drillTable').hidden = rows.length === 0;
      renderDrillSummary(rows);
    }}

    function showCurrentDrill() {{
      const frame = drillStack[drillStack.length - 1];
      if (!frame) return;
      const rule = DRILL_MAP[frame.platform + ':' + frame.parentLevel];
      if (!rule) return;
      const rows = childRows(frame.platform, frame.parentLevel, frame.parentId);
      drillTitle.textContent = rule.childLabel;
      drillParent.textContent = frame.breadcrumb;
      drillBack.hidden = drillStack.length <= 1;
      renderDrillTable(rows, frame.platform, rule.childLevel);
    }}

    function pushDrill(platform, parentLevel, parentId, parentName) {{
      const rule = DRILL_MAP[platform + ':' + parentLevel];
      if (!rule) return;
      const breadcrumb = drillStack.length
        ? drillStack[drillStack.length - 1].breadcrumb + ' → ' + parentName
        : parentName;
      drillStack.push({{ platform, parentLevel, parentId, parentName, breadcrumb }});
      drillPanel.classList.add('open');
      drillPanel.setAttribute('aria-hidden', 'false');
      layout.classList.add('has-drill');
      document.body.classList.add('drill-open');
      showCurrentDrill();
    }}

    function drillFromMainRow(row) {{
      if (selectedRow) selectedRow.classList.remove('selected');
      selectedRow = row;
      row.classList.add('selected');
      drillStack = [];
      pushDrill(row.dataset.platform, row.dataset.level, row.dataset.id, row.dataset.name);
    }}

    function drillFromNestedRow(row) {{
      pushDrill(row.dataset.platform, row.dataset.level, row.dataset.id, row.dataset.name);
    }}

    function closeDrill() {{
      drillPanel.classList.remove('open');
      drillPanel.setAttribute('aria-hidden', 'true');
      layout.classList.remove('has-drill');
      document.body.classList.remove('drill-open');
      drillStack = [];
      if (selectedRow) {{ selectedRow.classList.remove('selected'); selectedRow = null; }}
    }}

    drillBack.addEventListener('click', () => {{
      if (drillStack.length <= 1) {{ closeDrill(); return; }}
      drillStack.pop();
      showCurrentDrill();
    }});

    drillClose.addEventListener('click', closeDrill);
    drillPanel.addEventListener('click', e => {{ if (e.target === drillPanel) closeDrill(); }});

    document.querySelectorAll('tr.drill-row').forEach(row => {{
      row.addEventListener('click', () => drillFromMainRow(row));
      row.addEventListener('keydown', e => {{ if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); drillFromMainRow(row); }} }});
    }});

    drillTbody.addEventListener('click', e => {{
      const row = e.target.closest('tr.drill-row-nested');
      if (row) drillFromNestedRow(row);
    }});
    drillTbody.addEventListener('keydown', e => {{
      const row = e.target.closest('tr.drill-row-nested');
      if (row && (e.key === 'Enter' || e.key === ' ')) {{ e.preventDefault(); drillFromNestedRow(row); }}
    }});

    const ctx = document.getElementById('spendChart');
    if (ctx && chartPayload.labels.length) {{
      new Chart(ctx, {{
        type: 'line',
        data: chartPayload,
        options: {{
          responsive: true,
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            y: {{ beginAtZero: true, ticks: {{ callback: v => '$' + v.toLocaleString() }} }},
            x: {{ grid: {{ display: false }} }}
          }},
          plugins: {{ legend: {{ position: 'bottom' }} }},
          elements: {{ line: {{ tension: 0.3, borderWidth: 2 }}, point: {{ radius: 0, hitRadius: 8 }} }}
        }}
      }});
    }}
  </script>
</body>
</html>"""
