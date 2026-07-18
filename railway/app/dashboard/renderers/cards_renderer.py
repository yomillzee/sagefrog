"""Summary cards, hero metrics, and budget pacing panel HTML."""

from __future__ import annotations

from typing import Any

from penn_business_lines import PLATFORM_LABELS

from dashboard.utils.formatting import (
    esc as _esc,
    fmt_cpa as _fmt_cpa,
    fmt_int as _fmt_int,
    fmt_money as _fmt_money,
    fmt_pct as _fmt_pct,
    platform_title_html as _platform_title_html,
)

def summary_cards_html(totals: dict[str, Any], platform_ids: list[str]) -> str:
    if not platform_ids:
        return ""
    cards = [
        summary_card(PLATFORM_LABELS.get(platform, platform), totals.get(platform), platform=platform)
        for platform in platform_ids
    ]
    return "\n".join(cards)


def summary_card(
    label: str,
    totals: dict[str, Any] | None,
    *,
    platform: str | None = None,
) -> str:
    label_html = _platform_title_html(platform, label) if platform else _esc(label)
    if not totals:
        return f"""
        <div class="card card-empty">
          <div class="card-label">{label_html}</div>
          <div class="card-value muted">No data</div>
        </div>
        """
    spend = float(totals.get("spend") or 0)
    clicks = int(totals.get("clicks") or 0)
    impressions = int(totals.get("impressions") or 0)
    conversions = float(totals.get("conversions") or 0)
    if platform == "organic":
        return f"""
    <div class="card" data-platform="organic">
      <div class="card-label">{label_html}</div>
      <div class="card-value">{_fmt_int(clicks)}</div>
      <div class="card-stats">
        <span>{_fmt_int(impressions)} page views</span>
        <span>{_fmt_int(conversions)} key events</span>
        <span class="muted">GA4 organic search</span>
      </div>
    </div>
    """
    return f"""
    <div class="card" data-platform="{_esc(platform or '')}">
      <div class="card-label">{label_html}</div>
      <div class="card-value">{_fmt_money(spend)}</div>
      <div class="card-stats">
        <span>{_fmt_int(clicks)} clicks</span>
        <span>{_fmt_int(impressions)} impr.</span>
        <span>{_fmt_int(conversions)} conv.</span>
      </div>
    </div>
    """


def aggregated_card(totals: dict[str, Any]) -> str:
    """Deprecated — use _paid_ad_overview_html."""
    return paid_ad_overview_html(totals)


def ga4_paid_key_events(ga4_attr: dict[str, Any] | None) -> int:
    """Sum GA4 key events attributed to paid platforms (for verified CPA)."""
    if not ga4_attr:
        return 0
    platforms = ga4_attr.get("platforms")
    if platforms:
        total = 0
        for platform in ("google", "linkedin", "meta"):
            platform_totals = (platforms.get(platform) or {}).get("totals") or {}
            total += int(platform_totals.get("key_events") or 0)
        return total
    return int((ga4_attr.get("totals") or {}).get("key_events") or 0)


def paid_ad_overview_metrics(
    aggregated: dict[str, Any],
    ga4_attr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spend = float(aggregated.get("spend") or 0)
    clicks = int(aggregated.get("clicks") or 0)
    impressions = int(aggregated.get("impressions") or 0)
    conversions = float(aggregated.get("conversions") or 0)
    ga4_key_events = ga4_paid_key_events(ga4_attr)
    return {
        "spend": spend,
        "clicks": clicks,
        "impressions": impressions,
        "conversions": conversions,
        "ctr": (clicks / impressions) if impressions else None,
        "cpc": (spend / clicks) if clicks else None,
        "reported_cpa": (spend / conversions) if conversions else None,
        "verified_cpa": (spend / ga4_key_events) if ga4_key_events else None,
        "ga4_key_events": ga4_key_events,
    }


def paid_ad_overview_html(
    aggregated: dict[str, Any],
    ga4_attr: dict[str, Any] | None = None,
) -> str:
    if not aggregated:
        return ""
    m = paid_ad_overview_metrics(aggregated, ga4_attr)
    ga4_events = int(m["ga4_key_events"] or 0)
    verified_sub = (
        f"Using {_fmt_int(ga4_events)} paid GA4 TY event{'s' if ga4_events != 1 else ''}"
        if ga4_events
        else "No paid GA4 TY events in range"
    )
    return f"""
    <section class="paid-ad-overview" aria-label="Paid Ad Overview">
      <div class="paid-ad-metrics-grid">
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Spend</div>
          <div class="ga4-metric-value" id="heroSpend">{_fmt_money(m["spend"])}</div>
          <div class="ga4-metric-sub">Platform spend</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Impressions</div>
          <div class="ga4-metric-value" id="heroImpressions">{_fmt_int(m["impressions"])}</div>
          <div class="ga4-metric-sub">Paid delivery</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Clicks</div>
          <div class="ga4-metric-value" id="heroClicks">{_fmt_int(m["clicks"])}</div>
          <div class="ga4-metric-sub">Platform clicks</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">CTR</div>
          <div class="ga4-metric-value" id="heroCtr">{_fmt_pct(m["clicks"], m["impressions"]) if m["impressions"] else "—"}</div>
          <div class="ga4-metric-sub">Clicks ÷ impressions</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">CPC</div>
          <div class="ga4-metric-value" id="heroCpc">{_fmt_cpa(m["spend"], m["clicks"]) if m["clicks"] else "—"}</div>
          <div class="ga4-metric-sub">Spend ÷ clicks</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Reported conversions</div>
          <div class="ga4-metric-value" id="heroConversions">{_fmt_int(m["conversions"])}</div>
          <div class="ga4-metric-sub">Platform-defined</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Reported CPA</div>
          <div class="ga4-metric-value" id="heroReportedCpa">{_fmt_cpa(m["spend"], m["conversions"]) if m["conversions"] else "—"}</div>
          <div class="ga4-metric-sub">Platform basis</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Verified CPA</div>
          <div class="ga4-metric-value" id="heroVerifiedCpa">{_fmt_cpa(m["spend"], ga4_events) if ga4_events else "—"}</div>
          <div class="ga4-metric-sub" id="heroVerifiedCpaSub">{verified_sub}</div>
        </div>
      </div>
    </section>"""


