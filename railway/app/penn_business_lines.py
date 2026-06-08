"""Classify Penn campaigns into business lines from naming conventions."""

from __future__ import annotations

import os
from typing import Any

# (id, label, keyword substrings — first match wins)
BUSINESS_LINE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("home_equity", "Home Equity", ("home equity", "heloc")),
    (
        "cash_bonus",
        "Cash Bonus",
        ("cash bonus", "$400 cash", "$475 cash", "400 cash bonus", "475 cash bonus"),
    ),
    ("hys", "HYS", ("hys", "high yield savings", "high-yield")),
    (
        "cd_certificate",
        "CD / Certificate",
        ("breakable cd", "lehigh cd", " cd ", " cd-", "- cd", "certificate"),
    ),
    ("commercial", "Commercial", ("commercial",)),
)

PLATFORM_LABELS: dict[str, str] = {
    "google": "Google Ads",
    "linkedin": "LinkedIn",
    "meta": "Meta",
    "organic": "Organic",
}


def platform_catalog(*, include_organic: bool = False) -> list[dict[str, str]]:
    ids = ("google", "linkedin", "meta") + (("organic",) if include_organic else ())
    return [{"id": pid, "label": PLATFORM_LABELS[pid]} for pid in ids if pid in PLATFORM_LABELS]


def business_line_catalog() -> list[dict[str, str]]:
    lines = [{"id": bid, "label": label} for bid, label, _ in BUSINESS_LINE_RULES]
    lines.append({"id": "other", "label": "Other"})
    return lines


def active_business_line_catalog(campaigns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return business lines that appear in campaign data, preserving catalog order."""
    present = {str(c.get("business_line") or "") for c in campaigns}
    catalog = business_line_catalog()
    return [item for item in catalog if item["id"] in present]


def active_platform_catalog(
    campaigns: list[dict[str, Any]],
    *,
    include_organic: bool = False,
) -> list[dict[str, str]]:
    present = {str(c.get("platform") or "") for c in campaigns}
    catalog = platform_catalog(include_organic=include_organic)
    out = [item for item in catalog if item["id"] in present]
    if include_organic and "organic" not in present:
        out.append({"id": "organic", "label": PLATFORM_LABELS["organic"]})
    return out


def _breakdown_has_platform_data(platform_data: dict[str, Any] | None) -> bool:
    if not platform_data:
        return False
    return any(bool(v) for v in platform_data.values())


def _totals_have_metrics(totals: dict[str, Any] | None) -> bool:
    if not totals:
        return False
    return any(
        float(totals.get(key) or 0) for key in ("spend", "clicks", "impressions", "conversions")
    )


def platforms_present_in_snapshot(
    snapshot: dict[str, Any],
    *,
    cfg: Any | None = None,
    include_configured: bool = True,
    include_organic: bool = False,
) -> set[str]:
    """Detect paid platforms from snapshot data, mapped accounts, and optional config."""
    present: set[str] = set()
    totals = snapshot.get("platform_totals") or {}
    daily = snapshot.get("daily_metrics") or {}
    breakdowns = snapshot.get("breakdowns") or {}
    if not breakdowns:
        legacy = snapshot.get("campaigns") or {}
        if isinstance(legacy, dict):
            breakdowns = {platform: {"campaign": rows} for platform, rows in legacy.items()}
    accounts = snapshot.get("accounts") or {}

    for platform in ("google", "linkedin", "meta"):
        if _totals_have_metrics(totals.get(platform)):
            present.add(platform)
            continue
        if daily.get(platform):
            present.add(platform)
            continue
        if _breakdown_has_platform_data(breakdowns.get(platform)):
            present.add(platform)
            continue
        if accounts.get(platform):
            present.add(platform)

    if include_configured and cfg is not None:
        for platform, attr in (
            ("google", "google_customer_id"),
            ("linkedin", "linkedin_account_id"),
            ("meta", "meta_account_id"),
        ):
            if getattr(cfg, attr, None):
                present.add(platform)

    if include_organic:
        organic_totals = totals.get("organic")
        if _totals_have_metrics(organic_totals) or daily.get("organic") or daily.get("ga4"):
            present.add("organic")
        elif accounts.get("ga4_client_key") or accounts.get("ga4"):
            present.add("organic")

    return present


def _effective_rules(
    custom_rules: list[tuple[str, str, tuple[str, ...]]] | None,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    merged: list[tuple[str, str, tuple[str, ...]]] = []
    if custom_rules:
        merged.extend(custom_rules)
    merged.extend(BUSINESS_LINE_RULES)
    return tuple(merged)


def classify_business_line(
    name: str,
    *,
    extra_names: tuple[str, ...] = (),
    custom_rules: list[tuple[str, str, tuple[str, ...]]] | None = None,
) -> tuple[str, str]:
    """Match business line from campaign / group names (custom rules first)."""
    candidates: list[str] = []
    for text in (*extra_names, name):
        cleaned = (text or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    if len(candidates) > 1:
        combined = " | ".join(candidates)
        if combined not in candidates:
            candidates.insert(0, combined)

    rules = _effective_rules(custom_rules)
    for text in candidates:
        lowered = text.lower()
        for bid, label, keywords in rules:
            if any(kw in lowered for kw in keywords):
                return bid, label
    return "other", "Other"


def _classification_names(row: dict[str, Any]) -> tuple[str, ...]:
    """Names to try for business-line matching (group/parent before campaign)."""
    ordered_keys = (
        "campaign_group_name",
        "parent_name",
        "campaign_name",
        "adset_name",
        "ad_group_name",
        "name",
    )
    names: list[str] = []
    for key in ordered_keys:
        val = str(row.get(key) or "").strip()
        if val and val not in names:
            names.append(val)
    return tuple(names)


def _aggregate_linkedin_campaign_groups(platform_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Top-level LinkedIn rows for business-line matching (API campaign groups).

    LinkedIn Campaign Manager (Oct 2025+) renamed entities in the UI:
    API campaign_group → UI "Campaign"; API campaign → UI "Ad set"; creative → UI "Ad".
    The Marketing API resource names are unchanged.
    """
    groups = platform_data.get("campaign_group") or []
    if groups:
        return groups

    campaigns = platform_data.get("campaign") or []
    if not campaigns:
        return []

    by_group: dict[str, dict[str, Any]] = {}
    for row in campaigns:
        gid = str(row.get("parent_id") or row.get("campaign_group_id") or "").strip()
        if not gid:
            continue
        gname = str(row.get("parent_name") or row.get("campaign_group_name") or "").strip()
        bucket = by_group.get(gid)
        if not bucket:
            bucket = {
                "id": gid,
                "name": gname or f"Campaign group {gid}",
                "entity_level": "campaign_group",
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
            }
            by_group[gid] = bucket
        elif gname and bucket.get("name", "").startswith("Campaign group "):
            bucket["name"] = gname
        bucket["spend"] += float(row.get("spend") or 0)
        bucket["clicks"] += int(row.get("clicks") or 0)
        bucket["impressions"] += int(row.get("impressions") or 0)
        bucket["conversions"] += float(row.get("conversions") or 0)
    return list(by_group.values())


def _campaign_rows_from_breakdowns(breakdowns: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect campaign-level rows from each paid platform.

    LinkedIn uses API campaign groups at the top (UI "Campaign"), not ad sets
    (API campaigns, e.g. "St Rocco's Video").
    """
    rows: list[dict[str, Any]] = []
    for platform in ("google", "linkedin", "meta"):
        platform_data = breakdowns.get(platform) or {}
        if platform == "linkedin":
            source = _aggregate_linkedin_campaign_groups(platform_data)
        else:
            source = platform_data.get("campaign") or []
        for row in source:
            rows.append({**row, "_platform": platform})
    return rows


def _nixon_filter_slugs() -> set[str]:
    raw = (os.getenv("NIXON_FILTER_SLUGS") or "nixon,demo").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def client_filter_profile(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> str | None:
    """Return filter profile: ``penn`` (business lines), ``nixon`` (regions), or None."""
    slug = (client_slug or "").strip().lower()
    if slug == "penn":
        return "penn"
    if slug in _nixon_filter_slugs():
        return "nixon"

    ga4 = (ga4_client_key or "").strip().lower()
    if not ga4 and cfg is not None:
        ga4 = str(getattr(cfg, "ga4_client_key", "") or "").strip().lower()
    if ga4 == "nixon":
        return "nixon"

    resolved_label = (label or "").strip().lower()
    if not resolved_label and cfg is not None:
        resolved_label = str(getattr(cfg, "label", "") or "").strip().lower()
    if "nixon" in resolved_label:
        return "nixon"
    return None


def client_has_segment_filters(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> bool:
    return client_filter_profile(
        client_slug,
        cfg=cfg,
        label=label,
        ga4_client_key=ga4_client_key,
    ) is not None


def segment_filter_label(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> str:
    profile = client_filter_profile(
        client_slug,
        cfg=cfg,
        label=label,
        ga4_client_key=ga4_client_key,
    )
    if profile == "nixon":
        return "Region"
    return "Business line"


def segment_column_label(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> str:
    return segment_filter_label(
        client_slug,
        cfg=cfg,
        label=label,
        ga4_client_key=ga4_client_key,
    )


def client_has_product_line_filters(
    client_slug: str,
    *,
    cfg: Any | None = None,
    label: str | None = None,
    ga4_client_key: str | None = None,
) -> bool:
    return (
        client_filter_profile(
            client_slug,
            cfg=cfg,
            label=label,
            ga4_client_key=ga4_client_key,
        )
        == "nixon"
    )


def active_client_product_line_catalog(
    campaigns: list[dict[str, Any]],
    *,
    client_slug: str,
    filter_profile: str | None = None,
) -> list[dict[str, str]]:
    profile = filter_profile or client_filter_profile(client_slug)
    if profile == "nixon":
        from nixon_regions import active_product_line_catalog

        return active_product_line_catalog(campaigns)
    return []


def build_client_segment_campaigns(
    breakdowns: dict[str, Any],
    *,
    client_slug: str = "penn",
    filter_profile: str | None = None,
) -> list[dict[str, Any]]:
    slug = (client_slug or "").strip().lower()
    profile = filter_profile or client_filter_profile(slug)
    if profile == "nixon":
        from nixon_regions import build_nixon_region_campaigns

        return build_nixon_region_campaigns(breakdowns)
    return build_business_line_campaigns(breakdowns, client_slug=slug)


def active_client_segment_catalog(
    campaigns: list[dict[str, Any]],
    *,
    client_slug: str,
    filter_profile: str | None = None,
) -> list[dict[str, str]]:
    profile = filter_profile or client_filter_profile(client_slug)
    if profile == "nixon":
        from nixon_regions import active_region_catalog

        return active_region_catalog(campaigns)
    return active_business_line_catalog(campaigns)


def build_business_line_campaigns(
    breakdowns: dict[str, Any],
    *,
    client_slug: str = "penn",
) -> list[dict[str, Any]]:
    custom_rules: list[tuple[str, str, tuple[str, ...]]] | None = None
    if (client_slug or "").strip().lower() == "penn":
        try:
            import business_line_rules as bl_rules

            custom_rules = bl_rules.rules_as_tuples(client_slug)
        except Exception:
            custom_rules = None

    out: list[dict[str, Any]] = []
    for row in _campaign_rows_from_breakdowns(breakdowns):
        platform = str(row.get("_platform") or "")
        names = _classification_names(row)
        primary = names[0] if names else "—"
        extras = names[1:]
        bid, blabel = classify_business_line(
            primary,
            extra_names=extras,
            custom_rules=custom_rules,
        )
        out.append(
            {
                "platform": platform,
                "platform_label": PLATFORM_LABELS.get(platform, platform),
                "entity_level": str(row.get("entity_level") or "campaign"),
                "id": str(row.get("id") or ""),
                "name": primary,
                "business_line": bid,
                "business_line_label": blabel,
                "spend": float(row.get("spend") or 0),
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
                "conversions": float(row.get("conversions") or 0),
            }
        )
    out.sort(key=lambda r: (r["business_line_label"], r["platform"], -r["spend"]))
    return out
