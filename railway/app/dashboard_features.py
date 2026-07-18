"""Per-client dashboard section visibility (stored in Postgres, admin-editable)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import client_dashboard_config
from penn_business_lines import (
    client_filter_profile,
    client_has_product_line_filters,
    client_has_segment_filters,
)

FEATURE_KEYS: tuple[str, ...] = (
    "overview",
    "budget_pacing",
    "performance_trend",
    "campaign_explorer",
    "website_analytics",
    "segment_filters",
    "product_line_filters",
    "organic_channel",
)

FEATURE_LABELS: dict[str, str] = {
    "overview": "Overview tab",
    "budget_pacing": "Budget pacing chart",
    "performance_trend": "Daily paid performance trend",
    "campaign_explorer": "Campaign Explorer tab",
    "website_analytics": "Website Analytics tab",
    "segment_filters": "Business line / region filters",
    "product_line_filters": "Product line filters",
    "organic_channel": "Organic channel (GA4)",
}

FEATURE_HINTS: dict[str, str] = {
    "overview": "Overview page with performance summary and key metrics.",
    "budget_pacing": "MTD spend vs monthly budget on the overview tab.",
    "performance_trend": "Daily spend/clicks/impressions/conversions chart on overview.",
    "campaign_explorer": "Campaign-level table and drill-down view.",
    "website_analytics": "GA4 page performance and landing-page traffic filters.",
    "segment_filters": "Business line or region toggles on overview, campaigns, and website.",
    "product_line_filters": "Product line toggles (Nixon and similar profiles).",
    "organic_channel": "Organic sessions card and channel filter when GA4 is connected.",
}


@dataclass(frozen=True)
class DashboardFeatures:
    overview: bool = True
    budget_pacing: bool = True
    performance_trend: bool = True
    campaign_explorer: bool = True
    website_analytics: bool = True
    segment_filters: bool = True
    product_line_filters: bool = True
    organic_channel: bool = True

    def as_dict(self) -> dict[str, bool]:
        return {key: bool(getattr(self, key)) for key in FEATURE_KEYS}

    def get(self, key: str, default: bool = False) -> bool:
        if key in FEATURE_KEYS:
            return bool(getattr(self, key))
        return default


def _filter_kwargs(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> dict[str, Any]:
    ga4 = (ga4_client_key or "").strip()
    if not ga4 and cfg is not None:
        ga4 = str(getattr(cfg, "ga4_client_key", "") or "").strip()
    resolved_label = (label or "").strip()
    if not resolved_label and cfg is not None:
        resolved_label = str(getattr(cfg, "label", "") or "").strip()
    return {
        "cfg": cfg,
        "label": resolved_label,
        "ga4_client_key": ga4,
    }


def default_features(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> DashboardFeatures:
    """Slug-aware feature defaults."""
    slug = (client_slug or "").strip().lower()
    fk = _filter_kwargs(slug, cfg=cfg, label=label, ga4_client_key=ga4_client_key)
    return DashboardFeatures(
        overview=True,
        budget_pacing=True,
        performance_trend=True,
        campaign_explorer=True,
        website_analytics=True,
        segment_filters=client_has_segment_filters(slug, **fk),
        product_line_filters=client_has_product_line_filters(slug, **fk),
        organic_channel=True,
    )


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return None


def _features_from_mapping(
    raw: dict[str, Any] | None,
    *,
    base: DashboardFeatures,
) -> DashboardFeatures:
    if not raw:
        return base
    updates: dict[str, bool] = {}
    for key in FEATURE_KEYS:
        if key not in raw:
            continue
        parsed = _coerce_bool(raw.get(key))
        if parsed is not None:
            updates[key] = parsed
    if not updates:
        return base
    return DashboardFeatures(**{**base.as_dict(), **updates})


def get_stored_features(client_slug: str) -> dict[str, Any] | None:
    return client_dashboard_config.get_features(client_slug)


def resolve_features(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> DashboardFeatures:
    """Merge Postgres overrides onto slug defaults."""
    slug = (client_slug or "").strip().lower()
    base = default_features(slug, cfg=cfg, label=label, ga4_client_key=ga4_client_key)
    stored = get_stored_features(slug)
    resolved = _features_from_mapping(stored, base=base)

    fk = _filter_kwargs(slug, cfg=cfg, label=label, ga4_client_key=ga4_client_key)
    profile = client_filter_profile(slug, **fk)
    segment_ok = client_has_segment_filters(slug, **fk)
    product_ok = client_has_product_line_filters(slug, **fk)

    return DashboardFeatures(
        overview=resolved.overview,
        budget_pacing=resolved.budget_pacing,
        performance_trend=resolved.performance_trend,
        campaign_explorer=resolved.campaign_explorer,
        website_analytics=resolved.website_analytics,
        segment_filters=resolved.segment_filters and segment_ok,
        product_line_filters=resolved.product_line_filters and product_ok,
        organic_channel=resolved.organic_channel,
    )


def features_from_form(**posted: str) -> dict[str, bool]:
    """Parse checkbox form fields (missing = off). Keys are feature ids without prefix."""
    return {key: bool((posted.get(key) or "").strip()) for key in FEATURE_KEYS}


def save_features(
    client_slug: str,
    features: dict[str, bool],
    *,
    updated_by: str | None = None,
) -> DashboardFeatures:
    slug = (client_slug or "").strip().lower()
    clean = {key: bool(features.get(key)) for key in FEATURE_KEYS}
    client_dashboard_config.save_features(slug, clean, updated_by=updated_by)
    return resolve_features(slug)
