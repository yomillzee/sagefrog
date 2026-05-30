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
import ga4_attribution_service
from ga4_attribution_service import (
    METHODOLOGY,
    PLATFORM_TIER_LABELS,
    build_ga4_campaign_index,
    match_ga4_campaigns_to_ads,
)
import google_ads_service
import linkedin_service
import meta_service
import warehouse
from dates_util import resolve_date_range
from penn_config import PennDashboardConfig, load_penn_config
from penn_business_lines import (
    active_business_line_catalog,
    active_platform_catalog,
    build_business_line_campaigns,
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
        parent_id = str(row.get("adset_id") or row.get("ad_group_id") or "")
        parent_name = str(row.get("adset_name") or row.get("ad_group_name") or "")
    elif entity == "ad_group":
        parent_id = str(row.get("campaign_id") or "")
        parent_name = str(row.get("campaign_name") or "")
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
            or row.get("ad_group_id")
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
    for key in ("thumbnail_url", "image_url", "media_type", "creative_name", "video_url", "youtube_embed_url", "youtube_watch_url"):
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


def _merge_linkedin_creative_media(
    creatives: list[dict[str, Any]], account_id: str
) -> None:
    """Attach thumbnail/image metadata to LinkedIn creative rows when available."""
    if not creatives or not account_id:
        return
    try:
        media_data = linkedin_service.list_video_creatives(account_id, videos_only=False)
        by_id: dict[str, dict[str, Any]] = {}
        for row in media_data.get("videos") or []:
            cid = str(row.get("creative_id") or "")
            if not cid:
                continue
            existing = by_id.get(cid)
            thumb = str(row.get("thumbnail_url") or row.get("image_url") or "")
            if existing and (existing.get("thumbnail_url") or not thumb):
                continue
            by_id[cid] = row
        for creative in creatives:
            media = by_id.get(str(creative.get("id") or ""))
            if not media:
                continue
            thumb = str(media.get("thumbnail_url") or media.get("image_url") or "")
            if thumb:
                creative["thumbnail_url"] = thumb
            image_url = str(media.get("image_url") or "")
            if image_url:
                creative["image_url"] = image_url
            video_url = str(media.get("video_url") or "")
            if video_url:
                creative["video_url"] = video_url
            media_type = str(media.get("media_type") or "")
            if media_type:
                creative["media_type"] = media_type
            creative_name = str(media.get("creative_name") or "")
            if creative_name:
                creative["creative_name"] = creative_name
    except Exception:
        pass


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
            google_campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
            payload["platform_totals"]["google"] = _account_totals(perf)
        except Exception as exc:
            payload["errors"]["google_campaigns"] = _platform_error(exc)
            google_campaigns = []
        google_adgroups: list[dict[str, Any]] = []
        google_ads: list[dict[str, Any]] = []
        try:
            ag_perf = google_ads_service.adgroups_performance(
                cfg.google_customer_id, date_range=preset
            )
            google_adgroups = [
                _normalize_entity_row(g) for g in ag_perf.get("adgroups") or []
            ]
        except Exception as exc:
            payload["errors"]["google_adgroups"] = _platform_error(exc)
        try:
            ads_perf = google_ads_service.ads_performance(
                cfg.google_customer_id, date_range=preset, include_creative=True
            )
            google_ads = [_normalize_entity_row(a) for a in ads_perf.get("ads") or []]
        except Exception as exc:
            payload["errors"]["google_ads"] = _platform_error(exc)
        if google_campaigns or google_adgroups or google_ads:
            breakdowns["google"] = {
                "campaign": google_campaigns,
                "ad_group": google_adgroups,
                "ad": google_ads,
            }

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
            li_creatives_raw = creatives_perf.get("creatives") or []
            _merge_linkedin_creative_media(li_creatives_raw, cfg.linkedin_account_id)
            li_creatives = [_normalize_entity_row(c) for c in li_creatives_raw]
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
        try:
            payload["ga4_attribution"] = ga4_attribution_service.fetch_attribution_for_dashboard(
                date_range=preset,
                client_key=cfg.ga4_client_key,
            )
        except Exception as exc:
            payload["errors"]["ga4_attribution"] = _platform_error(exc)

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
        "ad_group": "ad group",
        "adset": "ad set",
        "ad": "ad",
        "creative": "creative",
    }
    return labels.get(level, level.replace("_", " "))


_GA4_TABLE_HEADERS = """
              <th class="ga4-col" title="GA4 attributed sessions">Sess.</th>
              <th class="ga4-col" title="GA4 engagement rate">Eng.</th>
              <th class="ga4-col" title="GA4 key events">Events</th>"""


def _ga4_row_cells(
    campaign_id: str,
    ga4_by_campaign: dict[str, dict[str, Any]] | None,
    *,
    is_campaign_row: bool,
) -> str:
    if not ga4_by_campaign:
        return ""
    if not is_campaign_row:
        return (
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
        )
    metrics = ga4_by_campaign.get(str(campaign_id or "")) or {}
    sessions = int(metrics.get("sessions") or 0)
    if not sessions:
        return (
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
        )
    engaged = int(metrics.get("engaged_sessions") or 0)
    key_events = int(metrics.get("key_events") or 0)
    return (
        f'<td class="num ga4-col">{_fmt_int(sessions)}</td>'
        f'<td class="num ga4-col">{_fmt_pct(engaged, sessions)}</td>'
        f'<td class="num ga4-col">{_fmt_int(key_events)}</td>'
    )


def _drillable_table(
    platform: str,
    title: str,
    rows: list[dict[str, Any]],
    *,
    entity_level: str,
    drill_hint: str = "",
    note: str = "",
    site_footer: str = "",
    ga4_by_campaign: dict[str, dict[str, Any]] | None = None,
) -> str:
    rows = _rows_for_display(rows)
    level_badge = _entity_level_label(entity_level)
    expandable = (
        (platform == "google" and entity_level in ("campaign", "ad_group"))
        or (
            platform in ("linkedin", "meta")
            and entity_level in ("campaign_group", "campaign")
        )
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
          {site_footer}
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
        ga4_cells = _ga4_row_cells(
            str(row.get("id") or ""),
            ga4_by_campaign,
            is_campaign_row=(entity_level == "campaign"),
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
              {ga4_cells}
            </tr>"""
        )
    chevron_th = '<th class="chevron-col"></th>'
    ga4_headers = _GA4_TABLE_HEADERS if ga4_by_campaign is not None else ""
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
              {ga4_headers}
            </tr>
          </thead>
          <tbody class="tree-table" data-platform="{_esc(platform)}">
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
      {site_footer}
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


def _summary_card(
    label: str,
    totals: dict[str, Any] | None,
    *,
    note: str = "",
    site_report: dict[str, Any] | None = None,
) -> str:
    site_html = ""
    if site_report:
        ga_sessions = int((site_report.get("totals") or {}).get("sessions") or 0)
        ga_engaged = int((site_report.get("totals") or {}).get("engaged_sessions") or 0)
        key_events = int((site_report.get("totals") or {}).get("key_events") or 0)
        if ga_sessions:
            site_html = f"""
      <div class="card-site">
        On-site: {_fmt_int(ga_sessions)} sessions · {_fmt_pct(ga_engaged, ga_sessions)} engaged · {_fmt_int(key_events)} key events
      </div>"""
    if not totals:
        return f"""
        <div class="card card-empty">
          <div class="card-label">{_esc(label)}</div>
          <div class="card-value muted">No data</div>
          {site_html}
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
      {site_html}
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


def _platform_breakdown_html(
    breakdowns: dict[str, Any],
    *,
    ga4_attr: dict[str, Any] | None = None,
    platform_totals: dict[str, Any] | None = None,
) -> str:
    """Render per-platform tables at the correct entity levels."""
    ga4_platforms = _ga4_platform_reports(ga4_attr)
    ad_totals = platform_totals or {}

    def ga4_index(platform: str) -> dict[str, dict[str, Any]]:
        campaigns = (breakdowns.get(platform) or {}).get("campaign") or []
        report = ga4_platforms.get(platform) or {}
        if not campaigns or not report:
            return {}
        return build_ga4_campaign_index(report.get("by_campaign") or [], campaigns)

    def site_block(platform: str) -> str:
        return _platform_site_impact_html(
            platform,
            ga4_platforms.get(platform) or {},
            ad_totals=ad_totals.get(platform),
        )

    parts: list[str] = []
    google = breakdowns.get("google") or {}
    google_campaigns = google.get("campaign") or []
    google_ag_count = len(google.get("ad_group") or [])
    google_ad_count = len(google.get("ad") or [])
    parts.append(
        _drillable_table(
            "google",
            "Google Ads — campaigns",
            google_campaigns,
            entity_level="campaign",
            note=(
                f"Drill down: {google_ag_count} ad groups → {google_ad_count} ads "
                "with creative previews when available. "
                "On-site columns are GA4 sessions matched to each campaign."
            ),
            site_footer=site_block("google"),
            ga4_by_campaign=ga4_index("google"),
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
                    "Click ▸ to expand campaigns and creatives inline. "
                    "On-site columns apply to LinkedIn campaigns when expanded."
                ),
                site_footer=site_block("linkedin"),
                ga4_by_campaign=ga4_index("linkedin"),
            )
        )
    else:
        parts.append(
            f"""
        <section class="panel platform-panel platform-linkedin">
          <div class="panel-head"><h2>LinkedIn — campaign groups</h2></div>
          <p class="muted">No campaign group data — click Refresh now.</p>
          {site_block("linkedin")}
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
            site_footer=site_block("meta"),
            ga4_by_campaign=ga4_index("meta"),
        )
    )
    return "\n".join(parts)


def _ga4_platform_reports(ga4_attr: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not ga4_attr:
        return {}
    platforms = ga4_attr.get("platforms")
    if platforms:
        return platforms
    return {"google": ga4_attr}


def _platform_site_impact_html(
    platform: str,
    report: dict[str, Any],
    *,
    ad_totals: dict[str, Any] | None,
) -> str:
    """Compact on-site GA4 block embedded under each platform panel."""
    totals = report.get("totals") or {}
    ga_sessions = int(totals.get("sessions") or 0)
    if not ga_sessions and not report:
        return ""

    ga_engaged = int(totals.get("engaged_sessions") or 0)
    key_events = int(totals.get("key_events") or 0)
    page_views = int(totals.get("page_views") or 0)
    ad_clicks = int((ad_totals or {}).get("clicks") or 0)
    by_tier = totals.get("by_tier") or {}
    tier_labels = PLATFORM_TIER_LABELS.get(platform) or {}

    tier_rows = []
    for key, tier_label in tier_labels.items():
        t = by_tier.get(key) or {}
        sessions = int(t.get("sessions") or 0)
        if not sessions:
            continue
        engaged = int(t.get("engaged_sessions") or 0)
        tier_rows.append(
            f"""<tr>
              <td>{_esc(tier_label)}</td>
              <td class="num">{_fmt_int(sessions)}</td>
              <td class="num">{_fmt_pct(engaged, sessions)}</td>
              <td class="num">{_fmt_int(t.get("key_events") or 0)}</td>
            </tr>"""
        )

    top_events = report.get("top_events") or []
    event_rows = "".join(
        f"""<tr>
          <td class="name">{_esc(ev.get("event_name"))}</td>
          <td class="num">{_fmt_int(ev.get("event_count") or 0)}</td>
        </tr>"""
        for ev in top_events[:8]
    )

    click_span = ""
    if ad_clicks and ga_sessions:
        click_span = (
            f'<span class="muted">{_fmt_int(ad_clicks)} ad clicks · '
            f"{ga_sessions / ad_clicks:.2f} sess/click</span>"
        )

    if not ga_sessions:
        return """
      <div class="site-impact site-impact-empty">
        <span class="site-impact-label">On-site (GA4)</span>
        <span class="muted">No attributed sessions this period</span>
      </div>"""

    details_inner = ""
    if tier_rows:
        details_inner += f"""
          <p class="table-note muted">{_esc(METHODOLOGY.get(platform, report.get("methodology") or ""))}</p>
          <div class="table-wrap">
            <table class="data-table compact">
              <thead><tr><th>Match tier</th><th>Sessions</th><th>Eng. rate</th><th>Key events</th></tr></thead>
              <tbody>{''.join(tier_rows)}</tbody>
            </table>
          </div>"""
    if event_rows:
        details_inner += f"""
          <div class="table-wrap">
            <table class="data-table compact">
              <thead><tr><th>Top event</th><th>Count</th></tr></thead>
              <tbody>{event_rows}</tbody>
            </table>
          </div>"""

    details_block = ""
    if details_inner:
        details_block = f"""
      <details class="site-impact-details">
        <summary>Attribution detail &amp; top events</summary>
        {details_inner}
      </details>"""

    return f"""
      <div class="site-impact">
        <div class="site-impact-bar">
          <span class="site-impact-label">On-site (GA4)</span>
          <span><strong>{_fmt_int(ga_sessions)}</strong> sessions</span>
          <span>{_fmt_pct(ga_engaged, ga_sessions)} engaged</span>
          <span>{_fmt_int(key_events)} key events</span>
          <span>{_fmt_int(page_views)} page views</span>
          {click_span}
        </div>
        {details_block}
      </div>"""


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
    ga4_attr = snapshot.get("ga4_attribution")
    ga4_platforms = _ga4_platform_reports(ga4_attr)
    aggregated = snapshot.get("aggregated_paid_media") or _aggregated_paid_media(totals)
    breakdown_html = _platform_breakdown_html(
        breakdowns,
        ga4_attr=ga4_attr,
        platform_totals=totals,
    )
    breakdowns_json = _json_for_html_script(breakdowns)
    ga4_campaign_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for platform in ("google", "linkedin", "meta"):
        campaigns = (breakdowns.get(platform) or {}).get("campaign") or []
        report = ga4_platforms.get(platform) or {}
        ga4_campaign_metrics[platform] = (
            build_ga4_campaign_index(report.get("by_campaign") or [], campaigns)
            if campaigns and report
            else {}
        )
    ga4_campaign_metrics_json = _json_for_html_script(ga4_campaign_metrics)
    bl_campaigns = _business_line_campaigns_from_snapshot(snapshot)
    bl_campaigns_json = _json_for_html_script(bl_campaigns)
    bl_catalog_json = _json_for_html_script(active_business_line_catalog(bl_campaigns))
    platform_catalog_json = _json_for_html_script(active_platform_catalog(bl_campaigns))
    ga4_note = "All-site total (not ad-attributed)"
    combined_daily = (ga4_attr or {}).get("combined_daily") or []
    if not combined_daily and (ga4_attr or {}).get("daily"):
        combined_daily = [
            {
                "metric_date": d.get("metric_date"),
                "google": int(d.get("sessions") or 0),
                "linkedin": 0,
                "meta": 0,
                "key_events": int(d.get("key_events") or 0),
            }
            for d in ga4_attr.get("daily") or []
        ]
    ga4_attr_chart_json = _json_for_html_script(
        {
            "labels": [str(d.get("metric_date") or "")[:10] for d in combined_daily],
            "datasets": [
                {
                    "label": "Google Ads sessions",
                    "data": [int(d.get("google") or 0) for d in combined_daily],
                    "borderColor": "#4285f4",
                    "yAxisID": "y",
                },
                {
                    "label": "LinkedIn sessions",
                    "data": [int(d.get("linkedin") or 0) for d in combined_daily],
                    "borderColor": "#e67e22",
                    "yAxisID": "y",
                },
                {
                    "label": "Meta sessions",
                    "data": [int(d.get("meta") or 0) for d in combined_daily],
                    "borderColor": "#1877f2",
                    "yAxisID": "y",
                },
                {
                    "label": "Key events (all)",
                    "data": [int(d.get("key_events") or 0) for d in combined_daily],
                    "borderColor": "#b8922e",
                    "borderDash": [4, 4],
                    "yAxisID": "y1",
                },
            ],
        }
    )

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
    .card-site {{
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid var(--border);
      font-size: 0.78rem;
      color: var(--navy-light);
      line-height: 1.4;
    }}
    .site-impact {{
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .site-impact-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 16px;
      font-size: 0.84rem;
    }}
    .site-impact-label {{
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
    }}
    .site-impact-empty {{ font-size: 0.84rem; }}
    .site-impact-details {{
      margin-top: 10px;
      font-size: 0.84rem;
    }}
    .site-impact-details summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 600;
      user-select: none;
    }}
    .site-impact-details .table-wrap {{ margin-top: 10px; }}
    .data-table.compact th, .data-table.compact td {{ padding: 8px 10px; font-size: 0.82rem; }}
    .chart-stack {{ display: flex; flex-direction: column; gap: 24px; }}
    .chart-subhead {{
      margin: 0 0 8px;
      font-size: 0.88rem;
      font-weight: 600;
      color: var(--navy);
    }}
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
    .data-table th.sortable {{
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      transition: color 0.12s;
    }}
    .data-table th.sortable:hover {{ color: var(--accent); }}
    .data-table th.sortable.sort-active {{ color: var(--accent); }}
    .sort-icon {{
      display: inline-block;
      width: 0.9em;
      margin-left: 2px;
      opacity: 0.35;
      font-size: 0.85em;
    }}
    .data-table th.sortable.sort-active .sort-icon {{ opacity: 1; }}
    .data-table th.sort-active[data-sort-dir="asc"] .sort-icon::before {{ content: '↑'; }}
    .data-table th.sort-active[data-sort-dir="desc"] .sort-icon::before {{ content: '↓'; }}
    .data-table th.sortable:not(.sort-active) .sort-icon::before {{ content: '↕'; }}
    .data-table tbody tr:last-child td {{ border-bottom: none; }}
    .data-table th.ga4-col, .data-table td.ga4-col {{
      border-left: 1px solid var(--border);
      background: rgba(184, 146, 46, 0.04);
    }}
    .data-table thead th.ga4-col {{
      color: #8a6d1f;
      font-size: 0.78rem;
      white-space: nowrap;
    }}
    .data-table td.ga4-col.muted {{ color: #9aa3ad; }}
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
      display: block;
    }}
    .ad-thumb-btn {{
      position: relative;
      appearance: none;
      border: none;
      padding: 0;
      background: none;
      cursor: pointer;
      flex-shrink: 0;
      border-radius: 8px;
      line-height: 0;
    }}
    .ad-thumb-btn:hover .ad-thumb {{ box-shadow: 0 0 0 2px var(--accent); }}
    .ad-thumb-btn.has-video .ad-play-icon {{
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.65rem;
      color: #fff;
      background: rgba(0,0,0,0.45);
      border-radius: 8px;
      pointer-events: none;
    }}
    .creative-preview {{
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .creative-preview[hidden] {{ display: none !important; }}
    .creative-preview-backdrop {{
      position: absolute;
      inset: 0;
      background: rgba(10, 37, 64, 0.72);
    }}
    .creative-preview-dialog {{
      position: relative;
      background: #fff;
      border-radius: 14px;
      max-width: min(720px, 96vw);
      max-height: 90vh;
      overflow: hidden;
      box-shadow: var(--shadow);
      width: 100%;
    }}
    .creative-preview-close {{
      position: absolute;
      top: 8px;
      right: 10px;
      z-index: 2;
      appearance: none;
      border: none;
      background: rgba(0,0,0,0.55);
      color: #fff;
      width: 32px;
      height: 32px;
      border-radius: 999px;
      font-size: 1.25rem;
      line-height: 1;
      cursor: pointer;
    }}
    .creative-preview-body {{
      background: #000;
      min-height: 200px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .creative-preview-body iframe,
    .creative-preview-body video,
    .creative-preview-body img {{
      display: block;
      max-width: 100%;
      max-height: 80vh;
      width: 100%;
    }}
    .creative-preview-body img {{ background: #fff; }}
    .creative-preview-caption {{
      padding: 12px 16px;
      font-size: 0.84rem;
      color: var(--muted);
      border-top: 1px solid var(--border);
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
      border-radius: var(--radius);
      padding: 16px 18px;
      box-shadow: var(--shadow-sm);
    }}
    .bl-layout {{
      display: grid;
      grid-template-columns: 272px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }}
    .bl-sidebar {{
      position: sticky;
      top: 16px;
      align-self: start;
    }}
    .bl-filters h3 {{
      margin: 0 0 14px;
      font-size: 0.92rem;
      font-weight: 700;
      color: var(--navy);
    }}
    .bl-filters .filter-group + .filter-group {{
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
    }}
    .bl-main {{ min-width: 0; }}
    @media (max-width: 960px) {{
      .bl-layout {{ grid-template-columns: 1fr; }}
      .bl-sidebar {{ position: static; }}
    }}
    .filter-group-head {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .filter-group-head .filter-label {{
      min-width: 0;
      flex: 1;
    }}
    .filter-link {{
      appearance: none;
      border: none;
      background: none;
      color: var(--accent);
      font-size: 0.76rem;
      font-weight: 600;
      cursor: pointer;
      padding: 2px 4px;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .filter-link:hover {{ color: var(--navy); }}
    .filter-checks {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      max-height: none;
      overflow: visible;
      padding-right: 0;
    }}
    .filter-check {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.86rem;
      cursor: pointer;
      user-select: none;
      padding: 4px 6px;
      border-radius: 8px;
    }}
    .filter-check:hover {{ background: #f4f7fb; }}
    .filter-check input {{
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
      flex-shrink: 0;
    }}
    .filter-zero-spend {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      font-size: 0.84rem;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
    }}
    .filter-zero-spend input {{
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
    }}
    .filter-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px 20px;
    }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 10px;
      flex: 1;
      min-width: 260px;
    }}
    .filter-label {{
      font-size: 0.78rem;
      font-weight: 600;
      color: var(--muted);
      min-width: 88px;
    }}
    .filter-toggle {{
      appearance: none;
      border: 1.5px solid var(--border);
      background: #fff;
      color: var(--text);
      padding: 7px 14px;
      border-radius: 999px;
      font-size: 0.84rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, color 0.12s;
    }}
    .filter-toggle:hover {{ border-color: #b8c4d4; background: #f8fafc; }}
    .filter-toggle.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      font-weight: 600;
    }}
    .filter-toggle.t-google.active {{ background: #4285f4; border-color: #4285f4; }}
    .filter-toggle.t-meta.active {{ background: #1877f2; border-color: #1877f2; }}
    .filter-toggle.t-linkedin.active {{ background: #e67e22; border-color: #e67e22; }}
    .filter-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }}
    .filter-reset {{
      appearance: none;
      border: none;
      background: none;
      color: var(--accent);
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      padding: 6px 4px;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .filter-reset:hover {{ color: var(--navy); }}
    .filter-status {{
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      font-size: 0.82rem;
      color: var(--muted);
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
    .attr-platform-section {{ margin-top: 8px; }}
    .attr-platform-summary {{ margin: 16px 0; }}
    .attr-subhead {{
      margin: 20px 0 10px;
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--navy);
    }}
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
          {_summary_card("Google Ads", totals.get("google"), site_report=ga4_platforms.get("google"), note="Account total · expand campaigns below")}
          {_summary_card("LinkedIn", totals.get("linkedin"), site_report=ga4_platforms.get("linkedin"), note="Account total · expand groups below")}
          {_summary_card("Meta", totals.get("meta"), site_report=ga4_platforms.get("meta"), note="Account total · expand campaigns below")}
          {_summary_card("GA4 (all site)", totals.get("ga4"), note=ga4_note)}
        </div>

        <section class="panel">
          <div class="panel-head"><h2>Daily performance</h2></div>
          <div class="chart-stack">
            <div>
              <p class="chart-subhead">Ad spend (account level)</p>
              <canvas id="spendChart"></canvas>
            </div>
            <div>
              <p class="chart-subhead">On-site sessions attributed to ads (GA4)</p>
              <canvas id="siteSessionsChart"></canvas>
            </div>
          </div>
        </section>

        {breakdown_html}
      </div>

      <div id="tab-business-line" class="tab-panel" role="tabpanel">
        <div class="bl-layout">
          <aside class="bl-sidebar">
            <section class="filter-panel bl-filters">
              <h3>Filters</h3>
              <div class="filter-group">
                <div class="filter-group-head">
                  <span class="filter-label">Business line</span>
                  <button type="button" class="filter-link" id="blSelectAll">All</button>
                  <button type="button" class="filter-link" id="blClearAll">None</button>
                </div>
                <div id="blFilters" class="filter-checks"></div>
              </div>
              <div class="filter-group">
                <div class="filter-group-head">
                  <span class="filter-label">Channel</span>
                  <button type="button" class="filter-link" id="channelSelectAll">All</button>
                  <button type="button" class="filter-link" id="channelClearAll">None</button>
                </div>
                <div id="channelFilters" class="filter-checks"></div>
              </div>
              <label class="filter-zero-spend">
                <input type="checkbox" id="showZeroSpend">
                Show inactive / $0 spend
              </label>
              <div class="filter-status" id="filterStatus"></div>
            </section>
          </aside>

          <div class="bl-main">
            <div class="bl-summary" id="blSummary"></div>

            <section class="panel platform-panel">
              <div class="panel-head">
                <h2>Campaign performance</h2>
                <span class="badge" id="blRowCount">0 rows</span>
              </div>
              <p class="table-note">Select business lines and channels in the sidebar. Nothing is selected by default — check items to populate the table.</p>
              <div class="table-wrap">
                <table class="data-table" id="blTable">
                  <thead>
                    <tr>
                      <th class="sortable" data-sort="platform" scope="col" aria-sort="none">Platform<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="business_line" scope="col" aria-sort="none">Business line<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="name" scope="col" aria-sort="none">Campaign<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="spend" scope="col" aria-sort="none">Spend<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="clicks" scope="col" aria-sort="none">Clicks<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="impressions" scope="col" aria-sort="none">Impressions<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="ctr" scope="col" aria-sort="none">CTR<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="conversions" scope="col" aria-sort="none">Conv.<span class="sort-icon" aria-hidden="true"></span></th>
                      <th class="sortable" data-sort="cpc" scope="col" aria-sort="none">CPC<span class="sort-icon" aria-hidden="true"></span></th>
                    </tr>
                  </thead>
                  <tbody id="blTableBody"></tbody>
                </table>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div id="creativePreview" class="creative-preview" hidden>
    <div class="creative-preview-backdrop" data-close-preview></div>
    <div class="creative-preview-dialog" role="dialog" aria-modal="true" aria-label="Creative preview">
      <button type="button" class="creative-preview-close" data-close-preview aria-label="Close">×</button>
      <div class="creative-preview-body" id="creativePreviewBody"></div>
      <div class="creative-preview-caption" id="creativePreviewCaption"></div>
    </div>
  </div>
  <script type="application/json" id="chart-data">{chart_json}</script>
  <script type="application/json" id="ga4-attr-chart-data">{ga4_attr_chart_json}</script>
  <script type="application/json" id="breakdowns-data">{breakdowns_json}</script>
  <script type="application/json" id="ga4-campaign-metrics">{ga4_campaign_metrics_json}</script>
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
    const ga4CampaignMetrics = readJson('ga4-campaign-metrics', {{}});
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

    const channelState = new Set();
    const blState = new Set();
    const blSort = {{ key: 'spend', dir: 'desc' }};

    function blSortValue(r, key) {{
      switch (key) {{
        case 'platform':
          return String(r.platform_label || r.platform || '').toLowerCase();
        case 'business_line':
          return String(r.business_line_label || '').toLowerCase();
        case 'name':
          return String(r.name || '').toLowerCase();
        case 'spend':
          return Number(r.spend || 0);
        case 'clicks':
          return Number(r.clicks || 0);
        case 'impressions':
          return Number(r.impressions || 0);
        case 'ctr':
          return r.impressions ? Number(r.clicks || 0) / Number(r.impressions) : 0;
        case 'conversions':
          return Number(r.conversions || 0);
        case 'cpc':
          return r.clicks ? Number(r.spend || 0) / Number(r.clicks) : 0;
        default:
          return 0;
      }}
    }}

    function sortBlRows(rows) {{
      const {{ key, dir }} = blSort;
      const mul = dir === 'asc' ? 1 : -1;
      const textKeys = new Set(['platform', 'business_line', 'name']);
      return [...rows].sort((a, b) => {{
        const av = blSortValue(a, key);
        const bv = blSortValue(b, key);
        if (textKeys.has(key)) {{
          return mul * String(av).localeCompare(String(bv));
        }}
        return mul * (av - bv);
      }});
    }}

    function updateBlSortHeaders() {{
      document.querySelectorAll('#blTable thead th[data-sort]').forEach(th => {{
        const active = th.dataset.sort === blSort.key;
        th.classList.toggle('sort-active', active);
        if (active) {{
          th.dataset.sortDir = blSort.dir;
          th.setAttribute('aria-sort', blSort.dir === 'asc' ? 'ascending' : 'descending');
        }} else {{
          delete th.dataset.sortDir;
          th.setAttribute('aria-sort', 'none');
        }}
      }});
    }}

    function onBlSortClick(e) {{
      const th = e.target.closest('#blTable thead th[data-sort]');
      if (!th) return;
      const key = th.dataset.sort;
      if (blSort.key === key) {{
        blSort.dir = blSort.dir === 'asc' ? 'desc' : 'asc';
      }} else {{
        blSort.key = key;
        blSort.dir = (key === 'platform' || key === 'business_line' || key === 'name') ? 'asc' : 'desc';
      }}
      applyBlView();
    }}

    function initCheckboxGroup(wrapId, catalog, state, group) {{
      const wrap = document.getElementById(wrapId);
      if (!wrap) return;
      wrap.innerHTML = '';
      for (const item of catalog) {{
        const label = document.createElement('label');
        label.className = 'filter-check';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = state.has(item.id);
        input.dataset.group = group;
        input.dataset.id = item.id;
        label.appendChild(input);
        label.appendChild(document.createTextNode(item.label));
        wrap.appendChild(label);
      }}
    }}

    function syncCheckboxInputs() {{
      document.querySelectorAll('.filter-check input[type="checkbox"]').forEach(input => {{
        const state = input.dataset.group === 'channel' ? channelState : blState;
        input.checked = state.has(input.dataset.id);
      }});
    }}

    function setGroupSelection(group, ids) {{
      const state = group === 'channel' ? channelState : blState;
      state.clear();
      ids.forEach(id => state.add(id));
      syncCheckboxInputs();
      applyBlView();
    }}

    function onFilterChange(e) {{
      const input = e.target.closest('.filter-check input[type="checkbox"]');
      if (!input) return;
      const group = input.dataset.group;
      const id = input.dataset.id;
      const state = group === 'channel' ? channelState : blState;
      if (input.checked) {{
        state.add(id);
      }} else {{
        state.delete(id);
      }}
      applyBlView();
    }}

    function applyBlView() {{
      const showZeroSpend = !!document.getElementById('showZeroSpend')?.checked;
      const hasSelection = channelState.size > 0 && blState.size > 0;
      const filtered = hasSelection ? blCampaigns.filter(r => {{
        if (!channelState.has(r.platform) || !blState.has(r.business_line)) return false;
        if (!showZeroSpend && (r.spend || 0) < 0.01) return false;
        return true;
      }}) : [];
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
        if (!hasSelection) {{
          tbody.innerHTML = '<tr><td colspan="9" class="muted" style="padding:24px;text-align:center">Select at least one business line and one channel in the sidebar.</td></tr>';
        }} else if (!filtered.length) {{
          tbody.innerHTML = '<tr><td colspan="9" class="muted" style="padding:24px;text-align:center">No campaigns match — try other filters or enable $0 spend rows.</td></tr>';
        }} else {{
          const sorted = sortBlRows(filtered);
          tbody.innerHTML = sorted.map(r => {{
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
        const allCh = chLabels.length === platformCatalog.length && platformCatalog.length > 0;
        const allBl = blLabels.length === blCatalog.length && blCatalog.length > 0;
        const chText = channelState.size === 0
          ? 'No channels selected'
          : (allCh ? 'All channels' : chLabels.join(', '));
        const blText = blState.size === 0
          ? 'No business lines selected'
          : (allBl ? 'All business lines' : blLabels.join(', '));
        const zeroNote = showZeroSpend ? ' · incl. $0 spend' : '';
        status.textContent = `${{blText}} · ${{chText}} · ${{filtered.length}} campaign${{filtered.length === 1 ? '' : 's'}}${{zeroNote}}`;
      }}
      updateBlSortHeaders();
    }}

    initCheckboxGroup('channelFilters', platformCatalog, channelState, 'channel');
    initCheckboxGroup('blFilters', blCatalog, blState, 'bl');
    document.getElementById('channelFilters')?.addEventListener('change', onFilterChange);
    document.getElementById('blFilters')?.addEventListener('change', onFilterChange);
    document.getElementById('showZeroSpend')?.addEventListener('change', applyBlView);
    document.querySelector('#blTable thead')?.addEventListener('click', onBlSortClick);
    document.getElementById('channelSelectAll')?.addEventListener('click', () =>
      setGroupSelection('channel', platformCatalog.map(p => p.id))
    );
    document.getElementById('blSelectAll')?.addEventListener('click', () =>
      setGroupSelection('bl', blCatalog.map(b => b.id))
    );
    document.getElementById('channelClearAll')?.addEventListener('click', () =>
      setGroupSelection('channel', [])
    );
    document.getElementById('blClearAll')?.addEventListener('click', () =>
      setGroupSelection('bl', [])
    );
    applyBlView();

    const DRILL_MAP = {{
      'google:campaign': {{ childLevel: 'ad_group', childLabel: 'Ad group' }},
      'google:ad_group': {{ childLevel: 'ad', childLabel: 'Ad' }},
      'linkedin:campaign_group': {{ childLevel: 'campaign', childLabel: 'Campaign' }},
      'linkedin:campaign': {{ childLevel: 'creative', childLabel: 'Creative' }},
      'meta:campaign': {{ childLevel: 'adset', childLabel: 'Ad set' }},
      'meta:adset': {{ childLevel: 'ad', childLabel: 'Ad' }},
    }};
    const LEVEL_LABELS = {{
      campaign_group: 'Group',
      campaign: 'Campaign',
      ad_group: 'Ad group',
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

    function previewPayload(r) {{
      const embed = r.youtube_embed_url || '';
      if (embed) return {{ type: 'embed', url: embed }};
      const videoUrl = r.video_url || '';
      if (videoUrl && r.media_type === 'video') {{
        const ytMatch = videoUrl.match(/(?:youtube\\.com\\/embed\\/|youtu\\.be\\/)([a-zA-Z0-9_-]{{11}})/);
        if (ytMatch) return {{ type: 'embed', url: 'https://www.youtube.com/embed/' + ytMatch[1] }};
        return {{ type: 'video', url: videoUrl }};
      }}
      const img = r.image_url || r.thumbnail_url || '';
      if (img) return {{ type: 'image', url: img }};
      return null;
    }}

    function buildThumbButton(r) {{
      const thumbUrl = r.thumbnail_url || r.image_url || '';
      if (!thumbUrl) return '';
      const preview = previewPayload(r);
      const playable = preview && (preview.type === 'embed' || preview.type === 'video');
      const cls = 'ad-thumb-btn' + (playable ? ' has-video' : '');
      const attrs = preview
        ? ` data-preview-type="${{escHtml(preview.type)}}" data-preview-url="${{escHtml(preview.url)}}"`
        : ` data-preview-type="image" data-preview-url="${{escHtml(thumbUrl)}}"`;
      const play = playable ? '<span class="ad-play-icon" aria-hidden="true">▶</span>' : '';
      return `<button type="button" class="${{cls}}"${{attrs}} aria-label="Preview creative">${{play}}<img class="ad-thumb" src="${{escHtml(thumbUrl)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('button')?.remove()"></button>`;
    }}

    let previewPlayer = null;

    function closeCreativePreview() {{
      const modal = document.getElementById('creativePreview');
      const body = document.getElementById('creativePreviewBody');
      const caption = document.getElementById('creativePreviewCaption');
      if (!modal || !body) return;
      body.innerHTML = '';
      if (caption) caption.textContent = '';
      modal.hidden = true;
      previewPlayer = null;
    }}

    function openCreativePreview(type, url, label) {{
      const modal = document.getElementById('creativePreview');
      const body = document.getElementById('creativePreviewBody');
      const caption = document.getElementById('creativePreviewCaption');
      if (!modal || !body || !url) return;
      body.innerHTML = '';
      if (type === 'embed') {{
        const iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.allow = 'autoplay; encrypted-media; picture-in-picture';
        iframe.allowFullscreen = true;
        iframe.title = label || 'Video preview';
        body.appendChild(iframe);
      }} else if (type === 'video') {{
        const video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        body.appendChild(video);
        previewPlayer = video;
      }} else {{
        const img = document.createElement('img');
        img.src = url;
        img.alt = label || 'Creative preview';
        body.appendChild(img);
      }}
      if (caption) caption.textContent = label || '';
      modal.hidden = false;
    }}

    function buildNameCell(r, level, depth) {{
      const pad = 8 + depth * 20;
      const tag = LEVEL_LABELS[level]
        ? `<span class="entity-tag">${{escHtml(LEVEL_LABELS[level])}}</span>` : '';
      let inner = `${{tag}}${{escHtml(r.name || '—')}}`;
      if (level === 'ad' || level === 'creative') {{
        const thumb = buildThumbButton(r);
        const creativeSub = (r.creative_name && r.creative_name !== r.name)
          ? `<span class="ad-creative-sub">${{escHtml(r.creative_name)}}</span>` : '';
        const typeBadge = r.media_type
          ? `<span class="ad-creative-sub">${{escHtml(r.media_type)}}</span>` : '';
        inner = `<div class="name-inner">${{thumb}}<span><span>${{tag}}${{escHtml(r.name || '—')}}</span>${{creativeSub}}${{typeBadge}}</span></div>`;
      }}
      return `<td class="name" style="padding-left:${{pad}}px">${{inner}}</td>`;
    }}

    function buildGa4Cells(platform, level, rowId) {{
      const platformMetrics = ga4CampaignMetrics[platform];
      if (!platformMetrics) return '';
      if (level !== 'campaign') {{
        return '<td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td>';
      }}
      const metrics = platformMetrics[String(rowId || '')] || {{}};
      const sessions = metrics.sessions || 0;
      if (!sessions) {{
        return '<td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td>';
      }}
      const engaged = metrics.engaged_sessions || 0;
      const keyEvents = metrics.key_events || 0;
      return `<td class="num ga4-col">${{fmtInt(sessions)}}</td>`
        + `<td class="num ga4-col">${{fmtPct(engaged, sessions)}}</td>`
        + `<td class="num ga4-col">${{fmtInt(keyEvents)}}</td>`;
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
        <td class="num">${{cpc}}</td>
        ${{buildGa4Cells(platform, level, r.id)}}`;
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
        if (e.target.closest('.ad-thumb-btn')) return;
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

    document.body.addEventListener('click', e => {{
      const btn = e.target.closest('.ad-thumb-btn');
      if (btn) {{
        e.preventDefault();
        e.stopPropagation();
        const type = btn.dataset.previewType || 'image';
        const url = btn.dataset.previewUrl || '';
        const label = btn.closest('.name-inner')?.innerText?.trim() || 'Creative preview';
        openCreativePreview(type, url, label);
        return;
      }}
      if (e.target.closest('[data-close-preview]')) {{
        closeCreativePreview();
      }}
    }});
    document.addEventListener('keydown', e => {{
      if (e.key === 'Escape') closeCreativePreview();
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

    const ga4AttrChartPayload = readJson('ga4-attr-chart-data', {{ labels: [], datasets: [] }});
    const siteCtx = document.getElementById('siteSessionsChart');
    if (siteCtx && ga4AttrChartPayload.labels && ga4AttrChartPayload.labels.length) {{
      new Chart(siteCtx, {{
        type: 'line',
        data: ga4AttrChartPayload,
        options: {{
          responsive: true,
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            y: {{
              type: 'linear',
              position: 'left',
              beginAtZero: true,
              title: {{ display: true, text: 'Sessions' }},
            }},
            y1: {{
              type: 'linear',
              position: 'right',
              beginAtZero: true,
              grid: {{ drawOnChartArea: false }},
              title: {{ display: true, text: 'Key events' }},
            }},
            x: {{ grid: {{ display: false }} }},
          }},
          plugins: {{ legend: {{ position: 'bottom' }} }},
          elements: {{ line: {{ tension: 0.3, borderWidth: 2 }}, point: {{ radius: 0, hitRadius: 8 }} }},
        }},
      }});
    }}
  </script>
</body>
</html>"""
