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
from penn_business_lines import (
    build_business_line_campaigns,
    business_line_catalog,
    platform_catalog,
)


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
    payload["business_line_campaigns"] = build_business_line_campaigns(breakdowns)

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


def _json_for_html_script(data: Any) -> str:
    """Embed JSON in HTML without breaking out of a script tag."""
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


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
    expandable = platform in ("linkedin", "meta") and entity_level in (
        "campaign_group",
        "campaign",
    )
    hint = drill_hint or (
        "Click ▸ to expand and compare child rows inline"
        if expandable
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
        cpc = _fmt_money(spend / clicks) if clicks else "—"
        expand_class = " tree-expandable" if expandable else ""
        chevron = (
            '<span class="tree-chevron" aria-hidden="true">▸</span>'
            if expandable
            else '<span class="tree-chevron leaf"></span>'
        )
        row_attrs = (
            f'data-platform="{_esc(platform)}" '
            f'data-level="{_esc(entity_level)}" '
            f'data-id="{_esc(row.get("id"))}" '
            f'data-depth="0"'
        )
        if expandable:
            row_attrs += (
                f' tabindex="0" role="button" '
                f'aria-expanded="false" '
                f'aria-label="Expand {_esc(row.get("name"))}"'
            )
        rows_html.append(
            f"""<tr class="tree-row tree-depth-0{expand_class}" {row_attrs}>
              <td class="chevron-col">{chevron}</td>
              <td class="name">{_esc(row.get("name"))}</td>
              <td class="num">{_fmt_money(spend)}</td>
              <td class="num">{_fmt_int(clicks)}</td>
              <td class="num">{_fmt_int(impressions)}</td>
              <td class="num">{_fmt_pct(clicks, impressions or 1)}</td>
              <td class="num">{_fmt_int(conv)}</td>
              <td class="num">{cpc}</td>
            </tr>"""
        )
    chevron_th = '<th class="chevron-col"></th>'
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
          <tbody class="tree-table" data-platform="{_esc(platform)}">
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
                    "Top-level groups from the Marketing API. "
                    "Click ▸ to expand campaigns and creatives inline."
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
            drill_hint="Click ▸ on a campaign to expand ad sets, then ads with thumbnails",
        )
    )
    return "\n".join(parts)


def _business_line_campaigns_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    stored = snapshot.get("business_line_campaigns")
    if stored:
        return stored
    return build_business_line_campaigns(_breakdowns_from_snapshot(snapshot))


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

    chart_json = _json_for_html_script(
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
    breakdowns_json = _json_for_html_script(breakdowns)
    bl_campaigns = _business_line_campaigns_from_snapshot(snapshot)
    bl_campaigns_json = _json_for_html_script(bl_campaigns)
    bl_catalog_json = _json_for_html_script(business_line_catalog())
    platform_catalog_json = _json_for_html_script(platform_catalog())
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
      max-width: 1280px;
      margin: 0 auto;
      padding: 0 24px 48px;
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
    tr.tree-row {{ transition: background 0.12s; }}
    tr.tree-expandable {{ cursor: pointer; }}
    tr.tree-expandable:hover {{ background: #f0f5fb; }}
    tr.tree-row.expanded {{ background: #e8f0fa; }}
    tr.tree-row.expanded > td {{ border-bottom-color: #c5d9ef; }}
    tr.tree-empty {{ background: #fafbfc; }}
    tr.tree-empty td {{ font-size: 0.84rem; font-style: italic; }}
    tr.tree-depth-1 {{ background: #fafcfe; }}
    tr.tree-depth-2 {{ background: #f5f9fd; }}
    tr.tree-depth-3 {{ background: #f0f6fc; }}
    .tree-chevron {{
      display: inline-block;
      width: 1em;
      color: var(--accent);
      font-size: 0.85rem;
      font-weight: 700;
      transition: transform 0.15s;
    }}
    .tree-chevron.leaf {{ visibility: hidden; }}
    tr.expanded .tree-chevron {{ transform: rotate(90deg); }}
    td.name {{ max-width: 420px; }}
    .name-inner {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .ad-thumb {{
      width: 40px;
      height: 40px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--border);
      flex-shrink: 0;
      background: #f0f4f8;
    }}
    .ad-creative-sub {{
      display: block;
      font-size: 0.72rem;
      color: var(--muted);
      margin-top: 2px;
    }}
    .entity-tag {{
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
      margin-right: 6px;
    }}
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

    .dash-tabs {{
      display: flex;
      gap: 4px;
      margin-bottom: 20px;
      border-bottom: 2px solid var(--border);
      padding-bottom: 0;
    }}
    .dash-tab {{
      appearance: none;
      border: none;
      background: transparent;
      padding: 12px 20px;
      font-size: 0.92rem;
      font-weight: 600;
      color: var(--muted);
      cursor: pointer;
      border-bottom: 3px solid transparent;
      margin-bottom: -2px;
      border-radius: 8px 8px 0 0;
      transition: color 0.15s, border-color 0.15s, background 0.15s;
    }}
    .dash-tab:hover {{ color: var(--text); background: rgba(255,255,255,0.5); }}
    .dash-tab.active {{
      color: var(--accent);
      border-bottom-color: var(--accent);
      background: var(--panel);
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}

    .filter-panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-top: 3px solid #2e7d32;
      border-radius: var(--radius);
      padding: 18px 20px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-sm);
    }}
    .filter-group {{ margin-bottom: 14px; }}
    .filter-group:last-child {{ margin-bottom: 0; }}
    .filter-label {{
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .filter-chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .filter-chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 12px;
      border-radius: 999px;
      border: 1.5px solid var(--border);
      background: #fff;
      font-size: 0.84rem;
      cursor: pointer;
      user-select: none;
      transition: background 0.12s, border-color 0.12s;
    }}
    .filter-chip input {{ accent-color: var(--accent); cursor: pointer; }}
    .filter-chip.checked {{
      background: #eef4fb;
      border-color: var(--accent);
      font-weight: 600;
    }}
    .filter-chip.chip-google.checked {{ border-color: #4285f4; background: #eef4ff; }}
    .filter-chip.chip-meta.checked {{ border-color: #1877f2; background: #eef3fc; }}
    .filter-chip.chip-linkedin.checked {{ border-color: #e67e22; background: #fef6ee; }}
    .filter-status {{
      margin-top: 14px;
      font-size: 0.8rem;
      color: var(--muted);
      text-align: right;
    }}
    .bl-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .bl-stat {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: var(--shadow-sm);
    }}
    .bl-stat-val {{ font-size: 1.35rem; font-weight: 700; }}
    .bl-stat-lbl {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin-top: 4px; }}
    .platform-pill {{
      display: inline-block;
      font-size: 0.68rem;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 999px;
      text-transform: uppercase;
      letter-spacing: .03em;
    }}
    .platform-pill.google {{ background: #eef4ff; color: #4285f4; }}
    .platform-pill.meta {{ background: #eef3fc; color: #1877f2; }}
    .platform-pill.linkedin {{ background: #fef6ee; color: #e67e22; }}
    .bl-tag {{
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--muted);
      background: #f0f4f8;
      padding: 2px 8px;
      border-radius: 6px;
      white-space: nowrap;
    }}
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

  <div class="layout">
    <div class="wrap">
      {error_html}

      <nav class="dash-tabs" role="tablist">
        <button type="button" class="dash-tab active" data-tab="platform" role="tab" aria-selected="true">By platform</button>
        <button type="button" class="dash-tab" data-tab="business-line" role="tab" aria-selected="false">By business line</button>
      </nav>

      <div id="tab-platform" class="tab-panel active" role="tabpanel">
        {_aggregated_card(aggregated)}

        <div class="cards">
          {_summary_card("Google Ads", totals.get("google"))}
          {_summary_card("LinkedIn", totals.get("linkedin"), note="Account total · expand groups inline")}
          {_summary_card("Meta", totals.get("meta"), note="Account total · expand campaigns inline")}
          {_summary_card("GA4 (site)", totals.get("ga4"), note=ga4_note)}
        </div>

        <section class="panel">
          <div class="panel-head"><h2>Daily ad spend (account level)</h2></div>
          <p class="table-note">One line per platform per day from warehouse — not campaign/ad set breakdown.</p>
          <canvas id="spendChart"></canvas>
        </section>

        {breakdown_html}
      </div>

      <div id="tab-business-line" class="tab-panel" role="tabpanel">
        <section class="filter-panel">
          <div class="filter-group">
            <div class="filter-label">Channel</div>
            <div class="filter-chips" id="channelFilters"></div>
          </div>
          <div class="filter-group">
            <div class="filter-label">Business line</div>
            <div class="filter-chips" id="businessLineFilters"></div>
          </div>
          <div class="filter-status" id="filterStatus"></div>
        </section>

        <div class="bl-summary" id="blSummary"></div>

        <section class="panel platform-panel">
          <div class="panel-head">
            <h2>Campaign performance</h2>
            <span class="badge" id="blRowCount">0 rows</span>
          </div>
          <p class="table-note">Campaigns grouped by business line from campaign naming. Select channels and lines above to filter.</p>
          <div class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Platform</th>
                  <th>Business line</th>
                  <th>Campaign</th>
                  <th>Spend</th>
                  <th>Clicks</th>
                  <th>Impressions</th>
                  <th>CTR</th>
                  <th>Conv.</th>
                  <th>CPC</th>
                </tr>
              </thead>
              <tbody id="blTableBody"></tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </div>
  <script type="application/json" id="chart-data">{chart_json}</script>
  <script type="application/json" id="breakdowns-data">{breakdowns_json}</script>
  <script type="application/json" id="bl-campaigns-data">{bl_campaigns_json}</script>
  <script type="application/json" id="bl-catalog-data">{bl_catalog_json}</script>
  <script type="application/json" id="platform-catalog-data">{platform_catalog_json}</script>
  <script>
    function readJson(id, fallback) {{
      const el = document.getElementById(id);
      if (!el) return fallback;
      try {{
        return JSON.parse(el.textContent || 'null') ?? fallback;
      }} catch (err) {{
        console.error('Failed to parse', id, err);
        return fallback;
      }}
    }}

    const chartPayload = readJson('chart-data', {{ labels: [], datasets: [] }});
    const breakdowns = readJson('breakdowns-data', {{}});
    const blCampaigns = readJson('bl-campaigns-data', []);
    const blCatalog = readJson('bl-catalog-data', []);
    const platformCatalog = readJson('platform-catalog-data', []);

    const fmtMoney = n => '$' + Number(n || 0).toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    const fmtInt = n => Number(n || 0).toLocaleString();
    const fmtPct = (n, d) => d ? (100 * n / d).toFixed(2) + '%' : '—';
    const escHtml = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

    document.querySelectorAll('.dash-tab').forEach(btn => {{
      btn.addEventListener('click', () => {{
        const tab = btn.dataset.tab;
        document.querySelectorAll('.dash-tab').forEach(b => {{
          const on = b.dataset.tab === tab;
          b.classList.toggle('active', on);
          b.setAttribute('aria-selected', on ? 'true' : 'false');
        }});
        document.querySelectorAll('.tab-panel').forEach(p => {{
          p.classList.toggle('active', p.id === 'tab-' + tab);
        }});
      }});
    }});

    const channelState = new Set(platformCatalog.map(p => p.id));
    const blState = new Set(blCatalog.map(b => b.id));
    const ALL_CHANNELS = '__all_channels__';
    const ALL_BL = '__all_bl__';

    function syncAllChip(group, allId, state, catalog) {{
      const allOn = catalog.every(item => state.has(item.id));
      return allOn;
    }}

    function renderFilterChips() {{
      const chWrap = document.getElementById('channelFilters');
      const blWrap = document.getElementById('businessLineFilters');
      if (!chWrap || !blWrap) return;

      chWrap.innerHTML = '';
      const allCh = document.createElement('label');
      allCh.className = 'filter-chip' + (syncAllChip('channel', ALL_CHANNELS, channelState, platformCatalog) ? ' checked' : '');
      allCh.innerHTML = `<input type="checkbox" data-group="channel" data-all="1" ${{syncAllChip('channel', ALL_CHANNELS, channelState, platformCatalog) ? 'checked' : ''}}> All Channels`;
      chWrap.appendChild(allCh);
      for (const p of platformCatalog) {{
        const chip = document.createElement('label');
        const on = channelState.has(p.id);
        chip.className = `filter-chip chip-${{p.id}}${{on ? ' checked' : ''}}`;
        chip.innerHTML = `<input type="checkbox" data-group="channel" data-id="${{p.id}}" ${{on ? 'checked' : ''}}> ${{escHtml(p.label)}}`;
        chWrap.appendChild(chip);
      }}

      blWrap.innerHTML = '';
      const allBl = document.createElement('label');
      allBl.className = 'filter-chip' + (syncAllChip('bl', ALL_BL, blState, blCatalog) ? ' checked' : '');
      allBl.innerHTML = `<input type="checkbox" data-group="bl" data-all="1" ${{syncAllChip('bl', ALL_BL, blState, blCatalog) ? 'checked' : ''}}> All Business Lines`;
      blWrap.appendChild(allBl);
      for (const b of blCatalog) {{
        const chip = document.createElement('label');
        const on = blState.has(b.id);
        chip.className = 'filter-chip' + (on ? ' checked' : '');
        chip.innerHTML = `<input type="checkbox" data-group="bl" data-id="${{b.id}}" ${{on ? 'checked' : ''}}> ${{escHtml(b.label)}}`;
        blWrap.appendChild(chip);
      }}
    }}

    function onFilterChange(e) {{
      const input = e.target;
      if (!input.matches('input[type=checkbox]')) return;
      const group = input.dataset.group;
      const isAll = input.dataset.all === '1';
      const id = input.dataset.id;
      const state = group === 'channel' ? channelState : blState;
      const catalog = group === 'channel' ? platformCatalog : blCatalog;

      if (isAll) {{
        if (input.checked) {{
          catalog.forEach(item => state.add(item.id));
        }} else {{
          state.clear();
        }}
      }} else if (input.checked) {{
        state.add(id);
      }} else {{
        state.delete(id);
      }}

      if (state.size === 0) {{
        catalog.forEach(item => state.add(item.id));
      }}

      renderFilterChips();
      applyBlView();
    }}

    function applyBlView() {{
      const filtered = blCampaigns.filter(r =>
        channelState.has(r.platform) && blState.has(r.business_line)
      );
      const spend = filtered.reduce((s, r) => s + (r.spend || 0), 0);
      const clicks = filtered.reduce((s, r) => s + (r.clicks || 0), 0);
      const impressions = filtered.reduce((s, r) => s + (r.impressions || 0), 0);
      const conv = filtered.reduce((s, r) => s + (r.conversions || 0), 0);
      const cpc = clicks ? spend / clicks : 0;

      const summary = document.getElementById('blSummary');
      if (summary) {{
        summary.innerHTML = `
          <div class="bl-stat"><div class="bl-stat-val">${{fmtMoney(spend)}}</div><div class="bl-stat-lbl">Spend</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(clicks)}}</div><div class="bl-stat-lbl">Clicks</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(impressions)}}</div><div class="bl-stat-lbl">Impressions</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(conv)}}</div><div class="bl-stat-lbl">Conversions</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{clicks ? fmtMoney(cpc) : '—'}}</div><div class="bl-stat-lbl">CPC</div></div>`;
      }}

      const tbody = document.getElementById('blTableBody');
      if (tbody) {{
        if (!filtered.length) {{
          tbody.innerHTML = '<tr><td colspan="9" class="muted" style="padding:24px;text-align:center">No campaigns match these filters.</td></tr>';
        }} else {{
          tbody.innerHTML = filtered.map(r => {{
            const cpcVal = r.clicks ? fmtMoney(r.spend / r.clicks) : '—';
            return `<tr>
              <td><span class="platform-pill ${{escHtml(r.platform)}}">${{escHtml(r.platform_label)}}</span></td>
              <td><span class="bl-tag">${{escHtml(r.business_line_label)}}</span></td>
              <td class="name">${{escHtml(r.name)}}</td>
              <td class="num">${{fmtMoney(r.spend)}}</td>
              <td class="num">${{fmtInt(r.clicks)}}</td>
              <td class="num">${{fmtInt(r.impressions)}}</td>
              <td class="num">${{fmtPct(r.clicks, r.impressions)}}</td>
              <td class="num">${{fmtInt(r.conversions)}}</td>
              <td class="num">${{cpcVal}}</td>
            </tr>`;
          }}).join('');
        }}
      }}

      const rowCount = document.getElementById('blRowCount');
      if (rowCount) rowCount.textContent = filtered.length + ' row' + (filtered.length === 1 ? '' : 's');

      const status = document.getElementById('filterStatus');
      if (status) {{
        const chLabels = platformCatalog.filter(p => channelState.has(p.id)).map(p => p.label);
        const blLabels = blCatalog.filter(b => blState.has(b.id)).map(b => b.label);
        const chText = chLabels.length === platformCatalog.length ? 'all channels' : chLabels.join(', ');
        const blText = blLabels.length === blCatalog.length ? 'all business lines' : blLabels.join(', ');
        status.textContent = `Showing ${{blText}} on ${{chText}} · ${{filtered.length}} campaign${{filtered.length === 1 ? '' : 's'}}`;
      }}
    }}

    document.getElementById('channelFilters')?.addEventListener('change', onFilterChange);
    document.getElementById('businessLineFilters')?.addEventListener('change', onFilterChange);
    renderFilterChips();
    applyBlView();

    const DRILL_MAP = {{
      'linkedin:campaign_group': {{ childLevel: 'campaign', childLabel: 'Campaign' }},
      'linkedin:campaign': {{ childLevel: 'creative', childLabel: 'Creative' }},
      'meta:campaign': {{ childLevel: 'adset', childLabel: 'Ad set' }},
      'meta:adset': {{ childLevel: 'ad', childLabel: 'Ad' }},
    }};
    const LEVEL_LABELS = {{
      campaign_group: 'Group',
      campaign: 'Campaign',
      creative: 'Creative',
      adset: 'Ad set',
      ad: 'Ad',
    }};

    function childRows(platform, level, parentId) {{
      const rule = DRILL_MAP[platform + ':' + level];
      if (!rule) return [];
      const pool = (breakdowns[platform] || {{}})[rule.childLevel] || [];
      const pid = String(parentId || '');
      return pool.filter(r => String(r.parent_id || '') === pid)
        .sort((a, b) => (b.spend || 0) - (a.spend || 0));
    }}

    function isExpandable(platform, level) {{
      return !!DRILL_MAP[platform + ':' + level];
    }}

    function buildNameCell(r, level, depth) {{
      const pad = 8 + depth * 20;
      const tag = LEVEL_LABELS[level]
        ? `<span class="entity-tag">${{escHtml(LEVEL_LABELS[level])}}</span>` : '';
      let inner = `${{tag}}${{escHtml(r.name || '—')}}`;
      if (level === 'ad') {{
        const thumbUrl = r.thumbnail_url || r.image_url || '';
        const thumb = thumbUrl
          ? `<img class="ad-thumb" src="${{escHtml(thumbUrl)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`
          : '';
        const creativeSub = (r.creative_name && r.creative_name !== r.name)
          ? `<span class="ad-creative-sub">${{escHtml(r.creative_name)}}</span>` : '';
        const typeBadge = r.media_type
          ? `<span class="ad-creative-sub">${{escHtml(r.media_type)}}</span>` : '';
        inner = `<div class="name-inner">${{thumb}}<span><span>${{tag}}${{escHtml(r.name || '—')}}</span>${{creativeSub}}${{typeBadge}}</span></div>`;
      }}
      return `<td class="name" style="padding-left:${{pad}}px">${{inner}}</td>`;
    }}

    function buildTreeRow(r, platform, level, depth) {{
      const spend = r.spend || 0;
      const clicks = r.clicks || 0;
      const impressions = r.impressions || 0;
      const conv = r.conversions || 0;
      const cpc = clicks ? fmtMoney(spend / clicks) : '—';
      const expandable = isExpandable(platform, level);
      const chevron = expandable
        ? '<span class="tree-chevron" aria-hidden="true">▸</span>'
        : '<span class="tree-chevron leaf"></span>';
      const tr = document.createElement('tr');
      tr.className = `tree-row tree-depth-${{depth}}${{expandable ? ' tree-expandable' : ''}}`;
      tr.dataset.platform = platform;
      tr.dataset.level = level;
      tr.dataset.id = r.id;
      tr.dataset.depth = String(depth);
      if (expandable) {{
        tr.tabIndex = 0;
        tr.setAttribute('role', 'button');
        tr.setAttribute('aria-expanded', 'false');
      }}
      tr.innerHTML = `
        <td class="chevron-col">${{chevron}}</td>
        ${{buildNameCell(r, level, depth)}}
        <td class="num">${{fmtMoney(spend)}}</td>
        <td class="num">${{fmtInt(clicks)}}</td>
        <td class="num">${{fmtInt(impressions)}}</td>
        <td class="num">${{fmtPct(clicks, impressions)}}</td>
        <td class="num">${{fmtInt(conv)}}</td>
        <td class="num">${{cpc}}</td>`;
      return tr;
    }}

    function collapseDescendants(row) {{
      const depth = parseInt(row.dataset.depth || '0', 10);
      let next = row.nextElementSibling;
      while (next && next.classList.contains('tree-row') && parseInt(next.dataset.depth || '0', 10) > depth) {{
        const rm = next;
        next = next.nextElementSibling;
        rm.remove();
      }}
      row.classList.remove('expanded');
      row.setAttribute('aria-expanded', 'false');
    }}

    function toggleTreeRow(row) {{
      if (!row.classList.contains('tree-expandable')) return;
      if (row.classList.contains('expanded')) {{
        collapseDescendants(row);
        return;
      }}
      const platform = row.dataset.platform;
      const level = row.dataset.level;
      const id = row.dataset.id;
      const depth = parseInt(row.dataset.depth || '0', 10);
      const rule = DRILL_MAP[platform + ':' + level];
      if (!rule) return;
      const children = childRows(platform, level, id);
      let insertAfter = row;
      if (!children.length) {{
        const empty = document.createElement('tr');
        empty.className = 'tree-row tree-empty';
        empty.dataset.depth = String(depth + 1);
        empty.innerHTML = `
          <td></td>
          <td class="name muted" style="padding-left:${{8 + (depth + 1) * 20}}px">No child rows for this period</td>
          <td colspan="6"></td>`;
        insertAfter.after(empty);
      }} else {{
        for (const child of children) {{
          const childRow = buildTreeRow(child, platform, rule.childLevel, depth + 1);
          insertAfter.after(childRow);
          insertAfter = childRow;
        }}
      }}
      row.classList.add('expanded');
      row.setAttribute('aria-expanded', 'true');
    }}

    document.querySelectorAll('.tree-table').forEach(tbody => {{
      tbody.addEventListener('click', e => {{
        const row = e.target.closest('tr.tree-expandable');
        if (!row || !tbody.contains(row)) return;
        e.preventDefault();
        e.stopPropagation();
        toggleTreeRow(row);
      }});
      tbody.addEventListener('keydown', e => {{
        const row = e.target.closest('tr.tree-expandable');
        if (!row || !tbody.contains(row)) return;
        if (e.key === 'Enter' || e.key === ' ') {{
          e.preventDefault();
          toggleTreeRow(row);
        }}
      }});
    }});

    const ctx = document.getElementById('spendChart');
    if (ctx && chartPayload.labels && chartPayload.labels.length) {{
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
