"""Per-client dashboard brand colors (sidebar + platform filters)."""

from __future__ import annotations

import re
from typing import Any

import client_dashboard_config

DEFAULT_THEME: dict[str, str] = {
    "sidebar_from": "#0a2540",
    "sidebar_to": "#123456",
    "google": "#4285f4",
    "google_bg": "#eef4ff",
    "linkedin": "#0a66c2",
    "linkedin_bg": "#eff6ff",
    "meta": "#9333ea",
    "meta_bg": "#f5f3ff",
    "organic": "#16a34a",
    "organic_bg": "#ecfdf3",
    "business_line": "#c9a227",
    "business_line_bg": "#faf6e8",
}

THEME_KEYS: tuple[str, ...] = tuple(DEFAULT_THEME.keys())
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Sidebar gradient split: the % down the sidebar where the top color hands off to
# the bottom color (so 50 ≈ an even split). Stored alongside the theme colors but
# kept out of THEME_KEYS since it isn't a hex value. A soft transition band of
# ±_SIDEBAR_BLEND_BAND% is centered on the split.
DEFAULT_SIDEBAR_BLEND = 50
_SIDEBAR_BLEND_BAND = 10


def normalize_blend(value: object) -> int:
    try:
        n = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_SIDEBAR_BLEND
    return max(0, min(100, n))


def sidebar_gradient_stops(blend: object) -> tuple[int, int]:
    """(a, b) stop percentages for the gradient: solid top color to a%, solid
    bottom color from b%, smooth transition between."""
    p = normalize_blend(blend)
    a = max(0, min(100, p - _SIDEBAR_BLEND_BAND))
    b = max(0, min(100, p + _SIDEBAR_BLEND_BAND))
    return a, b


def normalize_hex(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    if _HEX_RE.match(text):
        return text.lower()
    return None


def merge_theme(overrides: dict[str, Any] | None) -> dict[str, str]:
    merged = dict(DEFAULT_THEME)
    if not overrides:
        return merged
    for key in THEME_KEYS:
        normalized = normalize_hex(str(overrides.get(key) or ""))
        if normalized:
            merged[key] = normalized
    return merged


def load_client_theme(client_slug: str) -> dict[str, str]:
    slug = (client_slug or "").strip().lower()
    stored = client_dashboard_config.get_theme(slug) if slug else None
    merged = merge_theme(stored)
    # sidebar_blend rides alongside the hex keys but isn't validated as hex, so
    # merge_theme skips it; pull it through here (as an int) for the sidebar.
    blend_raw = stored.get("sidebar_blend") if isinstance(stored, dict) else None
    merged["sidebar_blend"] = str(normalize_blend(blend_raw))
    return merged


def parse_theme_form(**raw: str) -> dict[str, str]:
    """Validate theme fields from a settings form; raises ValueError on bad input."""
    parsed: dict[str, str] = {}
    for key in THEME_KEYS:
        normalized = normalize_hex(raw.get(key))
        if not normalized:
            raise ValueError(f"Invalid color for {key.replace('_', ' ')}.")
        parsed[key] = normalized
    return parsed


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    text = normalize_hex(hex_color) or "#000000"
    r = int(text[1:3], 16)
    g = int(text[3:5], 16)
    b = int(text[5:7], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def root_css_block(theme: dict[str, str] | None = None) -> str:
    t = merge_theme(theme)
    return f""":root {{
      --bg: #f4f6f9;
      --panel: #fff;
      --surface: #f8fafc;
      --text: #0f172a;
      --muted: #64748b;
      --border: #e2e8f0;
      --accent: #0b5cab;
      --navy: {t["sidebar_from"]};
      --navy-light: {t["sidebar_to"]};
      --sidebar-from: {t["sidebar_from"]};
      --sidebar-to: {t["sidebar_to"]};
      --gold: {t["business_line"]};
      --business-line: {t["business_line"]};
      --business-line-bg: {t["business_line_bg"]};
      --shadow: 0 4px 24px rgba(10, 37, 64, 0.08);
      --shadow-sm: 0 1px 3px rgba(10, 37, 64, 0.06);
      --radius: 14px;
      --ok: #15803d;
      --ok-bg: #ecfdf3;
      --err: #b42318;
      --err-bg: #fef2f2;
      --google: {t["google"]};
      --google-bg: {t["google_bg"]};
      --linkedin: {t["linkedin"]};
      --linkedin-bg: {t["linkedin_bg"]};
      --meta: {t["meta"]};
      --meta-bg: {t["meta_bg"]};
      --organic: {t["organic"]};
      --organic-bg: {t["organic_bg"]};
    }}"""


def chart_metric_defs(theme: dict[str, str] | None = None) -> list[dict[str, str]]:
    t = merge_theme(theme)
    return [
        {
            "id": "spend",
            "label": "Spend",
            "color": t["sidebar_from"],
            "fill": hex_to_rgba(t["sidebar_from"], 0.08),
            "yAxisID": "y",
            "format": "money",
        },
        {
            "id": "clicks",
            "label": "Clicks",
            "color": t["google"],
            "fill": hex_to_rgba(t["google"], 0.08),
            "yAxisID": "y",
            "format": "int",
        },
        {
            "id": "impressions",
            "label": "Impressions",
            "color": t["linkedin"],
            "fill": hex_to_rgba(t["linkedin"], 0.08),
            "yAxisID": "y",
            "format": "int",
        },
        {
            "id": "conversions",
            "label": "Conversions",
            "color": t["organic"],
            "fill": hex_to_rgba(t["organic"], 0.08),
            "yAxisID": "y",
            "format": "int",
        },
    ]
