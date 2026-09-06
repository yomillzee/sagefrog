"""Custom business-line keyword rules stored in Postgres (admin-editable)."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import db

import web_users

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS client_business_line_rules (
      client_slug TEXT PRIMARY KEY,
      rules_json JSONB NOT NULL DEFAULT '[]'::jsonb,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by TEXT
    )
    """,
]


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").strip().lower()).strip("_")
    return slug or "rule"


def parse_rules_text(text: str) -> list[dict[str, Any]]:
    """Parse textarea lines.

    Supported formats (both accepted):
      ``keywords, keywords = Label``   (preferred — keywords left, label right)
      ``Label | keyword, keyword``     (legacy)
    Lines starting with ``#`` are comments and are ignored.
    """
    rules: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            kw_part, label_part = line.split("=", 1)
        elif "|" in line:
            label_part, kw_part = line.split("|", 1)
        else:
            label_part, kw_part = line, line
        label = label_part.strip()
        if not label:
            continue
        keywords = [k.strip().lower() for k in kw_part.split(",") if k.strip()]
        if not keywords:
            keywords = [label.lower()]
        rule_id = _slugify(label)
        base = rule_id
        n = 2
        while rule_id in seen_ids:
            rule_id = f"{base}_{n}"
            n += 1
        seen_ids.add(rule_id)
        rules.append({"id": rule_id, "label": label, "keywords": keywords})
    return rules


def rules_to_text(rules: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for rule in rules:
        label = str(rule.get("label") or "").strip()
        keywords = rule.get("keywords") or []
        if not label:
            continue
        kw_text = ", ".join(str(k).strip() for k in keywords if str(k).strip())
        lines.append(f"{label} | {kw_text}" if kw_text else label)
    return "\n".join(lines)


def get_rules(client_slug: str) -> list[dict[str, Any]]:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return []
    ensure_schema()
    url = _get_db_url()
    if not url:
        return []
    with db.connection() as conn:
        row = conn.execute(
            "SELECT rules_json FROM client_business_line_rules WHERE client_slug = %s",
            (slug,),
        ).fetchone()
    if not row:
        return []
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        rule_id = str(item.get("id") or _slugify(label)).strip()
        keywords = [str(k).strip().lower() for k in (item.get("keywords") or []) if str(k).strip()]
        if label and keywords:
            out.append({"id": rule_id, "label": label, "keywords": keywords})
    return out


def save_rules(
    client_slug: str,
    rules_text: str,
    *,
    updated_by: str | None = None,
) -> list[dict[str, Any]]:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save business line rules.")
    rules = parse_rules_text(rules_text)
    ensure_schema()
    now = datetime.now(tz=UTC)
    url = _get_db_url()
    assert url
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_business_line_rules (client_slug, rules_json, updated_at, updated_by)
            VALUES (%s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              rules_json = EXCLUDED.rules_json,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, json.dumps(rules), now, (updated_by or "").strip() or None),
        )
    return rules


def rules_as_tuples(client_slug: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """Return custom rules as (id, label, keywords) tuples for classification."""
    return [
        (str(r["id"]), str(r["label"]), tuple(r.get("keywords") or ()))
        for r in get_rules(client_slug)
    ]


def has_rules(client_slug: str) -> bool:
    """Return True if the client has at least one custom business line rule saved."""
    return bool(get_rules(client_slug))
