"""Dated events overlaid on the dashboard's trend charts.

A chart shows that traffic fell 22% in March. It cannot show that the site was
migrated on the 12th — so every quarter someone reconstructs that from memory on
a call, or worse, doesn't. An annotation is that missing half: a date, a short
title, and the category of thing that happened, pinned to the timeline where the
number moved.

Two properties matter and are enforced here rather than in the UI:

* **Only the agency writes.** Annotations are the agency's account of what it
  did; a client-role user can read the shared ones but never create, edit or
  delete. The routes gate on role, and nothing in this module invents an author.
* **Internal by default.** ``visibility`` is ``internal`` unless someone
  deliberately shares it, because the natural first draft of "budget cut 5/1" is
  a private note, and a note that leaks to the client the moment it is typed is
  a note nobody will type. :func:`list_annotations` takes the caller's audience
  and filters in SQL, so a client's dashboard cannot receive an internal row and
  hide it client-side.

Storage mirrors ``client_notes`` — one table, raw SQL via ``db``, idempotent
``ensure_schema()``, every read and write scoped by ``client_slug``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import db
import web_users

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS client_annotations (
      id BIGSERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      event_date DATE NOT NULL,
      end_date DATE,
      title TEXT NOT NULL,
      body TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT 'other',
      visibility TEXT NOT NULL DEFAULT 'internal',
      charts TEXT NOT NULL DEFAULT 'both',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT,
      updated_by TEXT
    )
    """,
    """
    ALTER TABLE client_annotations
      ADD COLUMN IF NOT EXISTS charts TEXT NOT NULL DEFAULT 'both'
    """,
    """
    CREATE INDEX IF NOT EXISTS client_annotations_slug_date_idx
      ON client_annotations (client_slug, event_date DESC)
    """,
]

MAX_TITLE_LEN = 120
MAX_BODY_LEN = 4000

# Category key → (label, colour). Closed set so the chart never has to draw an
# unknown marker; anything else stored is coerced to "other" on read. Colours are
# picked to stay legible against the chart's light plot area and to read as
# different *kinds* of event, not as good/bad — an annotation is context, not a
# verdict.
CATEGORIES: dict[str, tuple[str, str]] = {
    "campaign": ("Campaign", "#1769aa"),
    "website": ("Website", "#7c3aed"),
    "budget": ("Budget", "#d97706"),
    "tracking": ("Tracking", "#0891b2"),
    "external": ("External", "#64748b"),
    "other": ("Other", "#0a7f3f"),
}
DEFAULT_CATEGORY = "other"

# Who may see a given annotation. "internal" rows never leave the agency; only
# "shared" rows reach a client-role user's dashboard.
VISIBILITIES: tuple[str, ...] = ("internal", "shared")
DEFAULT_VISIBILITY = "internal"

# Audiences a caller can read as. "agency" sees everything; "client" sees shared.
AUDIENCES: tuple[str, ...] = ("agency", "client")

# Which family of trend charts an event belongs on. A Google Ads budget change
# explains a spend line, not a sessions line; a site migration explains the
# sessions line and usually both. Defaulting to "both" keeps the old behaviour
# for every event stored before this field existed.
CHART_SCOPES: dict[str, str] = {
    "both": "Ads and analytics trends",
    "ads": "Ads trends only",
    "analytics": "Analytics trends only",
}
DEFAULT_CHARTS = "both"


@dataclass(frozen=True)
class Annotation:
    id: int
    client_slug: str
    event_date: str
    end_date: str | None
    title: str
    body: str
    category: str
    visibility: str
    charts: str
    created_at: str | None
    updated_at: str | None
    created_by: str | None
    updated_by: str | None

    def to_dict(self) -> dict[str, Any]:
        label, color = CATEGORIES.get(self.category, CATEGORIES[DEFAULT_CATEGORY])
        return {
            "id": self.id,
            "event_date": self.event_date,
            "end_date": self.end_date,
            "title": self.title,
            "body": self.body,
            "category": self.category,
            "category_label": label,
            "color": color,
            "visibility": self.visibility,
            "charts": self.charts,
            "charts_label": CHART_SCOPES.get(self.charts, CHART_SCOPES[DEFAULT_CHARTS]),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }


def category_choices() -> list[dict[str, str]]:
    """The category list for the editor, in a stable order."""
    return [
        {"key": key, "label": label, "color": color}
        for key, (label, color) in CATEGORIES.items()
    ]


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    """Annotations need the same Postgres-backed user layer as the dashboards."""
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _clean_slug(client_slug: str) -> str:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    return slug


def _clean_title(title: str | None) -> str:
    clean = (title or "").strip()
    if not clean:
        raise ValueError("A title is required.")
    return clean[:MAX_TITLE_LEN]


def _clean_body(body: str | None) -> str:
    return (body or "").strip()[:MAX_BODY_LEN]


def clean_category(category: str | None) -> str:
    key = (category or "").strip().lower()
    return key if key in CATEGORIES else DEFAULT_CATEGORY


def clean_visibility(visibility: str | None) -> str:
    key = (visibility or "").strip().lower()
    return key if key in VISIBILITIES else DEFAULT_VISIBILITY


def clean_charts(charts: str | None) -> str:
    key = (charts or "").strip().lower()
    return key if key in CHART_SCOPES else DEFAULT_CHARTS


def chart_scope_choices() -> list[dict[str, str]]:
    """The chart-scope list for the editor, in a stable order."""
    return [{"key": key, "label": label} for key, label in CHART_SCOPES.items()]


def parse_date(value: str | date | None, *, field: str = "date") -> date:
    """Coerce an ISO date string (or date) into a date, or raise ValueError."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"A {field} is required.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).") from exc


def _clean_range(
    event_date: str | date | None, end_date: str | date | None
) -> tuple[date, date | None]:
    """(start, end) for an annotation, where end is optional but never earlier.

    A blank end means a point event (a launch, a migration). A range that ends
    before it starts is a typo, not an intent, so it is rejected rather than
    silently swapped — the editor shows the message.
    """
    start = parse_date(event_date, field="event date")
    if end_date in (None, ""):
        return start, None
    end = parse_date(end_date, field="end date")
    if end < start:
        raise ValueError("The end date cannot be before the event date.")
    return start, (None if end == start else end)


_SELECT_COLS = (
    "id, client_slug, event_date, end_date, title, body, category, visibility, "
    "charts, created_at, updated_at, created_by, updated_by"
)


def _row_to_annotation(row: tuple[Any, ...]) -> Annotation:
    created, updated = row[9], row[10]
    return Annotation(
        id=int(row[0]),
        client_slug=str(row[1] or ""),
        event_date=row[2].isoformat() if row[2] else "",
        end_date=row[3].isoformat() if row[3] else None,
        title=str(row[4] or ""),
        body=str(row[5] or ""),
        category=clean_category(row[6]),
        visibility=clean_visibility(row[7]),
        charts=clean_charts(row[8]),
        created_at=created.isoformat() if created else None,
        updated_at=updated.isoformat() if updated else None,
        created_by=str(row[11]) if row[11] else None,
        updated_by=str(row[12]) if row[12] else None,
    )


def list_annotations(
    client_slug: str,
    *,
    audience: str = "agency",
    start: str | date | None = None,
    end: str | date | None = None,
    limit: int = 500,
) -> list[Annotation]:
    """Annotations for a client, newest first, filtered to what ``audience`` may see.

    ``start``/``end`` bound the window a chart is showing. A ranged annotation
    counts as in-window when it *overlaps* the window at all, so a campaign that
    started before the range still marks the chart it runs across.

    Anything other than ``agency`` is treated as a client audience — an unknown
    or missing audience must fail closed, showing only shared rows.
    """
    if not enabled():
        return []
    ensure_schema()
    slug = _clean_slug(client_slug)
    limit = max(1, min(int(limit), 1000))

    where = ["client_slug = %s"]
    params: list[Any] = [slug]
    if audience != "agency":
        where.append("visibility = 'shared'")
    if start is not None:
        where.append("COALESCE(end_date, event_date) >= %s")
        params.append(parse_date(start, field="start"))
    if end is not None:
        where.append("event_date <= %s")
        params.append(parse_date(end, field="end"))
    params.append(limit)

    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM client_annotations
            WHERE {" AND ".join(where)}
            ORDER BY event_date DESC, id DESC
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()
    return [_row_to_annotation(row) for row in rows]


def get_annotation(client_slug: str, annotation_id: int) -> Annotation | None:
    if not enabled():
        return None
    ensure_schema()
    slug = _clean_slug(client_slug)
    with db.connection() as conn:
        row = conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM client_annotations
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(annotation_id)),
        ).fetchone()
    return _row_to_annotation(row) if row else None


def create_annotation(
    client_slug: str,
    *,
    event_date: str | date,
    title: str,
    end_date: str | date | None = None,
    body: str | None = None,
    category: str | None = None,
    visibility: str | None = None,
    charts: str | None = None,
    created_by: str | None = None,
) -> Annotation:
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save annotations.")
    ensure_schema()
    slug = _clean_slug(client_slug)
    start, end = _clean_range(event_date, end_date)
    now = datetime.now(tz=UTC)
    author = (created_by or "").strip() or None
    with db.connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO client_annotations
              (client_slug, event_date, end_date, title, body, category, visibility,
               charts, created_at, updated_at, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {_SELECT_COLS}
            """,
            (
                slug, start, end, _clean_title(title), _clean_body(body),
                clean_category(category), clean_visibility(visibility),
                clean_charts(charts), now, now, author, author,
            ),
        ).fetchone()
    assert row
    return _row_to_annotation(row)


def update_annotation(
    client_slug: str,
    annotation_id: int,
    *,
    event_date: str | date,
    title: str,
    end_date: str | date | None = None,
    body: str | None = None,
    category: str | None = None,
    visibility: str | None = None,
    charts: str | None = None,
    updated_by: str | None = None,
) -> Annotation:
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save annotations.")
    ensure_schema()
    slug = _clean_slug(client_slug)
    start, end = _clean_range(event_date, end_date)
    now = datetime.now(tz=UTC)
    editor = (updated_by or "").strip() or None
    with db.connection() as conn:
        row = conn.execute(
            f"""
            UPDATE client_annotations
            SET event_date = %s, end_date = %s, title = %s, body = %s,
                category = %s, visibility = %s, charts = %s,
                updated_at = %s, updated_by = %s
            WHERE client_slug = %s AND id = %s
            RETURNING {_SELECT_COLS}
            """,
            (
                start, end, _clean_title(title), _clean_body(body),
                clean_category(category), clean_visibility(visibility),
                clean_charts(charts), now, editor, slug, int(annotation_id),
            ),
        ).fetchone()
    if not row:
        raise ValueError("Annotation not found.")
    return _row_to_annotation(row)


def delete_annotation(client_slug: str, annotation_id: int) -> bool:
    if not enabled():
        return False
    ensure_schema()
    slug = _clean_slug(client_slug)
    with db.connection() as conn:
        cur = conn.execute(
            "DELETE FROM client_annotations WHERE client_slug = %s AND id = %s",
            (slug, int(annotation_id)),
        )
    return bool(cur.rowcount)
