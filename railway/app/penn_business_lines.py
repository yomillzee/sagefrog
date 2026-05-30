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
}


def business_line_catalog() -> list[dict[str, str]]:
    lines = [{"id": bid, "label": label} for bid, label, _ in BUSINESS_LINE_RULES]
    lines.append({"id": "other", "label": "Other"})
    return lines


def platform_catalog() -> list[dict[str, str]]:
    return [
        {"id": "google", "label": "Google Ads"},
        {"id": "meta", "label": "Meta"},
        {"id": "linkedin", "label": "LinkedIn"},
    ]


def classify_business_line(name: str) -> tuple[str, str]:
    lowered = (name or "").lower()
    for bid, label, keywords in BUSINESS_LINE_RULES:
        if any(kw in lowered for kw in keywords):
            return bid, label
    return "other", "Other"


def _campaign_rows_from_breakdowns(breakdowns: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect campaign-level rows from each paid platform (no LinkedIn group double-count)."""
    rows: list[dict[str, Any]] = []
    for platform in ("google", "linkedin", "meta"):
        for row in (breakdowns.get(platform) or {}).get("campaign") or []:
            rows.append({**row, "_platform": platform})
    return rows


def build_business_line_campaigns(breakdowns: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _campaign_rows_from_breakdowns(breakdowns):
        platform = str(row.get("_platform") or "")
        name = str(row.get("name") or "—")
        bid, blabel = classify_business_line(name)
        out.append(
            {
                "platform": platform,
                "platform_label": PLATFORM_LABELS.get(platform, platform),
                "id": str(row.get("id") or ""),
                "name": name,
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
