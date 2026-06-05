"""Classify Penn campaigns into business lines from naming conventions."""

from __future__ import annotations

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


def _campaign_rows_from_breakdowns(breakdowns: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect campaign-level rows from each paid platform.

    LinkedIn API ``campaign`` rows are ad sets (e.g. "St Rocco's Video"); business-line
    rules match on campaign group names (e.g. "Commercial Retargeting - LinkedIn - 2026").
    """
    rows: list[dict[str, Any]] = []
    for platform in ("google", "linkedin", "meta"):
        platform_data = breakdowns.get(platform) or {}
        if platform == "linkedin":
            source = platform_data.get("campaign_group") or []
        else:
            source = platform_data.get("campaign") or []
        for row in source:
            rows.append({**row, "_platform": platform})
    return rows


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
