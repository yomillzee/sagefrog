"""Classify Nixon campaigns into geographic regions from naming conventions."""

from __future__ import annotations

from typing import Any

from penn_business_lines import (
    PLATFORM_LABELS,
    _campaign_rows_from_breakdowns,
    _classification_names,
)

# (id, label, keyword substrings — all matches apply; a campaign can belong to multiple regions)
REGION_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("texas", "Texas", ("- tx", " tx &", "& tx", " texas")),
    ("mid_atlantic", "Mid Atlantic", ("- ma", " mid atlantic", " mid-atlantic")),
    ("florida", "Florida", ("- fl", " florida", " tx & fl", "& fl")),
    ("northeast", "Northeast", ("- ne", " northeast", " north east", " north-east")),
)


def _keyword_in_text(keyword: str, lowered: str) -> bool:
    """Match keyword without treating region codes as prefixes (e.g. - ma vs - may)."""
    idx = 0
    while True:
        pos = lowered.find(keyword, idx)
        if pos == -1:
            return False
        end = pos + len(keyword)
        if end >= len(lowered) or lowered[end] not in "abcdefghijklmnopqrstuvwxyz":
            return True
        idx = pos + 1


# (id, label, keyword substrings — first match wins)
PRODUCT_LINE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("patient_apparel", "Patient Apparel", ("patient apparel",)),
    ("laundry_linens", "Laundry & Linens", ("laundry & linens", "laundry and linens")),
    ("scrubs", "Scrubs", ("scrubs",)),
)


def product_line_catalog() -> list[dict[str, str]]:
    lines = [{"id": pid, "label": label} for pid, label, _ in PRODUCT_LINE_RULES]
    lines.append({"id": "other", "label": "Other"})
    return lines


def active_product_line_catalog(campaigns: list[dict[str, Any]]) -> list[dict[str, str]]:
    present: set[str] = set()
    for row in campaigns:
        pid = row.get("product_line")
        if pid:
            present.add(str(pid))
    catalog = product_line_catalog()
    return [item for item in catalog if item["id"] in present]


def classify_product_line(
    name: str,
    *,
    extra_names: tuple[str, ...] = (),
) -> tuple[str, str]:
    """Match one product line from campaign / group names."""
    candidates: list[str] = []
    for text in (*extra_names, name):
        cleaned = (text or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    if len(candidates) > 1:
        combined = " | ".join(candidates)
        if combined not in candidates:
            candidates.insert(0, combined)

    for text in candidates:
        lowered = text.lower()
        for pid, label, keywords in PRODUCT_LINE_RULES:
            if any(kw in lowered for kw in keywords):
                return pid, label
    return "other", "Other"


def region_catalog() -> list[dict[str, str]]:
    lines = [{"id": rid, "label": label} for rid, label, _ in REGION_RULES]
    lines.append({"id": "other", "label": "Other"})
    return lines


def active_region_catalog(campaigns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return regions that appear in campaign data, preserving catalog order."""
    present: set[str] = set()
    for row in campaigns:
        for rid in row.get("region_ids") or [row.get("business_line")]:
            if rid:
                present.add(str(rid))
    catalog = region_catalog()
    return [item for item in catalog if item["id"] in present]


def classify_regions(
    name: str,
    *,
    extra_names: tuple[str, ...] = (),
) -> tuple[list[str], str]:
    """Match all regions from campaign / group names. Returns (ids, display label)."""
    candidates: list[str] = []
    for text in (*extra_names, name):
        cleaned = (text or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    if len(candidates) > 1:
        combined = " | ".join(candidates)
        if combined not in candidates:
            candidates.insert(0, combined)

    matched: list[tuple[str, str]] = []
    seen: set[str] = set()
    for text in candidates:
        lowered = text.lower()
        for rid, label, keywords in REGION_RULES:
            if rid in seen:
                continue
            if any(_keyword_in_text(kw, lowered) for kw in keywords):
                matched.append((rid, label))
                seen.add(rid)

    if not matched:
        return ["other"], "Other"

    order = {rid: idx for idx, (rid, _, _) in enumerate(REGION_RULES)}
    matched.sort(key=lambda pair: order.get(pair[0], 999))
    ids = [rid for rid, _ in matched]
    labels = ", ".join(label for _, label in matched)
    return ids, labels


def build_nixon_region_campaigns(breakdowns: dict[str, Any]) -> list[dict[str, Any]]:
    """Campaign rows with region and product line tags for Nixon dashboard filters."""
    out: list[dict[str, Any]] = []
    for row in _campaign_rows_from_breakdowns(breakdowns):
        platform = str(row.get("_platform") or "")
        names = _classification_names(row)
        primary = names[0] if names else "—"
        extras = names[1:]
        region_ids, region_label = classify_regions(primary, extra_names=extras)
        product_line_id, product_line_label = classify_product_line(primary, extra_names=extras)
        primary_region = region_ids[0] if region_ids else "other"
        out.append(
            {
                "platform": platform,
                "platform_label": PLATFORM_LABELS.get(platform, platform),
                "entity_level": str(row.get("entity_level") or "campaign"),
                "id": str(row.get("id") or ""),
                "name": primary,
                "business_line": primary_region,
                "business_line_label": region_label,
                "region_ids": region_ids,
                "product_line": product_line_id,
                "product_line_label": product_line_label,
                "product_line_ids": [product_line_id],
                "spend": float(row.get("spend") or 0),
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
                "conversions": float(row.get("conversions") or 0),
            }
        )
    out.sort(
        key=lambda r: (
            r["product_line_label"],
            r["business_line_label"],
            r["platform"],
            -r["spend"],
        )
    )
    return out
