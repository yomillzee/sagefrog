"""Number, text, JSON, and HTML formatting helpers."""

from __future__ import annotations

import html
import json
import os
from typing import Any


def fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def fmt_pct(num: float, den: float) -> str:
    if not den:
        return "—"
    return f"{100.0 * num / den:.2f}%"


def fmt_cpa(spend: float, count: float | int) -> str:
    if not count:
        return "—"
    return fmt_money(spend / float(count))


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def json_for_html_script(data: Any) -> str:
    """Embed JSON in HTML without breaking out of a script tag."""
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def platform_error(exc: Exception) -> str:
    return str(exc)[:500]


def fmt_file_size(num_bytes: int) -> str:
    size = int(num_bytes or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{round(size / (1024 * 1024), 1)} MB"


def fmt_short_date(iso_value: str | None) -> str:
    text = (iso_value or "").strip()
    if not text:
        return "—"
    return text[:10] if len(text) >= 10 else text


def entity_level_label(level: str, *, platform: str | None = None) -> str:
    """Human label for an entity row; LinkedIn uses Campaign Manager UI names (2025+)."""
    if platform == "linkedin":
        linkedin_labels = {
            "campaign_group": "campaign group",
            "campaign": "ad set",
            "creative": "ad",
        }
        if level in linkedin_labels:
            return linkedin_labels[level]
    labels = {
        "campaign": "campaign",
        "campaign_group": "campaign group",
        "ad_group": "ad group",
        "adset": "ad set",
        "ad": "ad",
        "creative": "creative",
    }
    return labels.get(level, level.replace("_", " "))


_PLATFORM_FAVICONS: dict[str, str] = {
    "google": "https://www.google.com/s2/favicons?domain=google.com&sz=32",
    "linkedin": "https://www.google.com/s2/favicons?domain=linkedin.com&sz=32",
    "meta": "https://www.google.com/s2/favicons?domain=facebook.com&sz=32",
}


def platform_title_html(platform: str, label: str) -> str:
    icon = _PLATFORM_FAVICONS.get(platform, "")
    icon_html = (
        f'<img class="platform-favicon" src="{icon}" alt="" width="20" height="20" loading="lazy" />'
        if icon
        else ""
    )
    return (
        f'<span class="platform-title">'
        f"{icon_html}"
        f"<span>{esc(label)}</span></span>"
    )


def file_type_icon_html(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        return (
            '<svg class="files-item-icon files-item-icon--pdf" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
            '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>'
            '<path d="M14 2v6h6"/><text x="7" y="17" font-size="6" fill="currentColor" stroke="none">PDF</text></svg>'
        )
    if ext == ".docx":
        return (
            '<svg class="files-item-icon files-item-icon--doc" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
            '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>'
            '<path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/></svg>'
        )
    return (
        '<svg class="files-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.5" aria-hidden="true">'
        '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>'
    )


def folder_icon_html() -> str:
    return (
        '<svg class="files-item-icon files-item-icon--folder" viewBox="0 0 24 24" fill="currentColor" '
        'stroke="none" aria-hidden="true">'
        '<path d="M10 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V8a2 2 0 00-2-2h-8l-2-2z"/>'
        "</svg>"
    )
