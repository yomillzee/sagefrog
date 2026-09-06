"""Postgres persistence for Web Mentions (Google Alerts RSS monitoring).

Two tables, mirroring the ``consent_store`` shape (config rows + result rows):

    web_mention_alerts   one row per monitored Google Alert: its name, the RSS
                         feed URL, what it is (brand / competitor / …), whether
                         it is still being polled, and the health of the last
                         fetch.
    web_mentions         one row per result the feed produced, normalized and
                         de-duplicated, keyed by a hash so a feed re-serving the
                         same article on every refresh never inserts it twice.

Two decisions worth knowing before reading further:

**The feed URL is a credential, not a setting.** A Google Alerts RSS URL embeds
the account's feed id — anyone holding it can read that alert. So it is
encrypted at rest when a key is available (:func:`_encrypt`), it never leaves
the server in any JSON response, and the admin UI only ever renders the masked
form (:func:`mask_feed_url`). Encryption is best-effort by design: with no key
configured the URL is stored in plaintext rather than making the whole feature
refuse to work, and a value that can no longer be decrypted surfaces as a feed
error instead of a crash.

**History outlives its alert.** Each mention carries its own copy of the alert's
name, category and subject, so deactivating an alert (or deleting one that has
not collected anything yet) never rewrites or removes what was already reported.
Deactivation only stops future polling.

Every function degrades gracefully when ``DATABASE_URL`` is unset (``enabled()``
is False): reads return empty, writes raise a clear error only where a caller
must know the save failed.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

import db
import db_migrate
import web_users

log = logging.getLogger(__name__)

# Closed set of alert categories. "brand" and "competitor" are the two that feed
# the share-of-mentions panel; the rest are reporting labels only.
CATEGORIES: tuple[str, ...] = ("brand", "competitor", "executive", "industry", "other")
CATEGORY_LABELS: dict[str, str] = {
    "brand": "Brand",
    "competitor": "Competitor",
    "executive": "Executive",
    "industry": "Industry",
    "other": "Other",
}
DEFAULT_CATEGORY = "other"

# Longest snippet we keep. Google Alerts descriptions are a sentence or two; the
# cap stops a malformed feed from writing a megabyte per row.
SNIPPET_MAX_CHARS = 600
TITLE_MAX_CHARS = 500
SOURCE_MAX_CHARS = 200

_BASELINE_SQL = [
    """
    CREATE TABLE IF NOT EXISTS web_mention_alerts (
      id SERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      name TEXT NOT NULL,
      subject TEXT NOT NULL DEFAULT '',
      feed_url TEXT NOT NULL,
      category TEXT NOT NULL DEFAULT 'other',
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by TEXT,
      last_checked_at TIMESTAMPTZ,
      last_success_at TIMESTAMPTZ,
      last_error_at TIMESTAMPTZ,
      last_error_message TEXT,
      last_new_count INTEGER,
      consecutive_failures INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS web_mention_alerts_client_feed_idx
      ON web_mention_alerts (client_slug, feed_url)
    """,
    """
    CREATE INDEX IF NOT EXISTS web_mention_alerts_client_idx
      ON web_mention_alerts (client_slug, active)
    """,
    """
    CREATE TABLE IF NOT EXISTS web_mentions (
      id BIGSERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      alert_id INTEGER,
      alert_name TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT 'other',
      subject TEXT NOT NULL DEFAULT '',
      dedupe_hash TEXT NOT NULL,
      title TEXT NOT NULL DEFAULT '',
      url TEXT NOT NULL DEFAULT '',
      google_url TEXT,
      source TEXT NOT NULL DEFAULT '',
      snippet TEXT NOT NULL DEFAULT '',
      published_at TIMESTAMPTZ,
      published_estimated BOOLEAN NOT NULL DEFAULT FALSE,
      mention_date DATE NOT NULL,
      discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      entry_id TEXT
    )
    """,
    # The dedupe guarantee. Scoped per client so two clients monitoring the same
    # term each keep their own copy of the article.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS web_mentions_dedupe_idx
      ON web_mentions (client_slug, dedupe_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS web_mentions_client_date_idx
      ON web_mentions (client_slug, mention_date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS web_mentions_alert_idx
      ON web_mentions (alert_id)
    """,
]

# Uniqueness moved off ``feed_url`` and onto a hash of it. Encryption is
# authenticated and randomised, so the same feed URL encrypts to a different
# ciphertext every time — a unique index on the stored column could never match,
# and one feed added twice would be polled twice and count every result twice.
# ``feed_key`` is a plain digest of the normalized URL: deterministic, so the
# index works, and it reveals nothing the URL itself did not.
_FEED_KEY_SQL = [
    "DROP INDEX IF EXISTS web_mention_alerts_client_feed_idx",
    "ALTER TABLE web_mention_alerts ADD COLUMN IF NOT EXISTS feed_key TEXT",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS web_mention_alerts_client_key_idx
      ON web_mention_alerts (client_slug, feed_key)
    """,
]

SCHEMA_SQL_STATEMENTS = _BASELINE_SQL + _FEED_KEY_SQL

db_migrate.register([
    db_migrate.Migration(id="web_mentions:0001_baseline", statements=tuple(_BASELINE_SQL)),
    db_migrate.Migration(id="web_mentions:0002_feed_key", statements=tuple(_FEED_KEY_SQL)),
])

_schema_ready = False
_schema_lock = threading.Lock()


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    """Idempotent DDL, run at most once per process (the runner does it at boot)."""
    global _schema_ready
    if not _get_db_url():
        return False
    if _schema_ready:
        return True
    with _schema_lock:
        if _schema_ready:
            return True
        with db.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s)", (db_migrate.SCHEMA_ADVISORY_LOCK_KEY,)
            )
            for stmt in SCHEMA_SQL_STATEMENTS:
                conn.execute(stmt)
        _schema_ready = True
    return True


# ---------------------------------------------------------------------------
# Feed URL secrecy
# ---------------------------------------------------------------------------

_ENC_PREFIX = "fernet:"


def _encryption_secret() -> str:
    """Secret the feed URLs are encrypted under, or '' when none is configured.

    Prefers a dedicated key, then the OAuth token key, then the session secret —
    the same order the rest of the app derives at-rest keys in. Unlike
    ``oauth_store`` this never raises: a portal with no key set still stores and
    polls feeds, just in plaintext, because a missing key must not take an
    already-configured client's reporting offline.
    """
    for name in ("WEB_MENTIONS_ENCRYPTION_KEY", "OAUTH_TOKEN_ENCRYPTION_KEY", "AUTH_SESSION_SECRET"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _fernet_for(secret: str):
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    secret = _encryption_secret()
    if not secret:
        return text
    try:
        token = _fernet_for(secret).encrypt(text.encode("utf-8")).decode("ascii")
    except Exception:
        log.exception("web mentions: feed URL encryption failed; storing plaintext")
        return text
    return _ENC_PREFIX + token


def _decrypt(stored: str | None) -> str:
    """Plaintext feed URL, or '' when the stored ciphertext cannot be read.

    Returning '' rather than raising is deliberate: an unreadable URL becomes a
    per-alert fetch error the admin can see and fix by re-pasting the feed, and
    every other alert in the account keeps polling.
    """
    text = (stored or "").strip()
    if not text:
        return ""
    if not text.startswith(_ENC_PREFIX):
        return text
    secret = _encryption_secret()
    if not secret:
        log.error("web mentions: encrypted feed URL but no encryption key configured")
        return ""
    try:
        return _fernet_for(secret).decrypt(text[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        log.error("web mentions: feed URL could not be decrypted (encryption key changed?)")
        return ""


def feed_key(url: str) -> str:
    """Deterministic per-URL lookup key, so "is this feed already here?" works.

    The stored URL is encrypted (and Fernet is randomised), so it cannot itself
    carry a uniqueness constraint. This digest can.
    """
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()


def mask_feed_url(url: str) -> str:
    """A feed URL rendered safe to show: host + path shape, ids blanked out.

    ``https://www.google.com/alerts/feeds/01234567890123456789/9876543210``
    becomes ``google.com/alerts/feeds/…6789/…3210`` — enough for an admin to tell
    two alerts apart, useless to anyone who copies it out of the page.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return "…"
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # Any path segment carrying a digit is an id, however short — mask it. The
    # literal parts of the path ("alerts", "feeds") are what makes the masked
    # value recognisable, so they stay.
    shown = [
        f"…{part[-4:]}" if any(ch.isdigit() for ch in part) else part
        for part in (parsed.path or "").split("/")
        if part
    ]
    return "/".join([host] + shown) if host else "…"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    id: int
    client_slug: str
    name: str
    subject: str
    feed_url: str
    category: str
    active: bool
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_message: str | None = None
    last_new_count: int | None = None
    consecutive_failures: int = 0

    @property
    def masked_feed_url(self) -> str:
        return mask_feed_url(self.feed_url)

    def public_dict(self, *, mention_count: int | None = None) -> dict[str, Any]:
        """Everything about this alert that may cross to a browser.

        The feed URL is deliberately absent — only its masked shape ships, and
        only the page's admin panel renders even that.
        """
        return {
            "id": self.id,
            "name": self.name,
            "subject": self.subject or self.name,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category.title()),
            "active": self.active,
            "feed_url_masked": self.masked_feed_url,
            "created_at": _iso(self.created_at),
            "last_checked_at": _iso(self.last_checked_at),
            "last_success_at": _iso(self.last_success_at),
            "last_error_message": self.last_error_message,
            "consecutive_failures": self.consecutive_failures,
            "last_new_count": self.last_new_count,
            "mention_count": mention_count,
        }


@dataclass
class Mention:
    id: int
    client_slug: str
    alert_id: int | None
    alert_name: str
    category: str
    subject: str
    title: str
    url: str
    google_url: str | None
    source: str
    snippet: str
    published_at: datetime | None
    published_estimated: bool
    mention_date: date
    discovered_at: datetime | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "alert_id": self.alert_id,
            "alert_name": self.alert_name,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category.title()),
            "subject": self.subject,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "snippet": self.snippet,
            "date": self.mention_date.isoformat() if self.mention_date else None,
            "published_estimated": self.published_estimated,
            "discovered_at": _iso(self.discovered_at),
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def normalize_category(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in CATEGORIES else DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

_ALERT_COLUMNS = (
    "id, client_slug, name, subject, feed_url, category, active, created_at, created_by, "
    "updated_at, updated_by, last_checked_at, last_success_at, last_error_at, "
    "last_error_message, last_new_count, consecutive_failures"
)


def _row_to_alert(row: tuple) -> Alert:
    return Alert(
        id=int(row[0]),
        client_slug=str(row[1]),
        name=str(row[2] or ""),
        subject=str(row[3] or ""),
        feed_url=_decrypt(row[4]),
        category=normalize_category(row[5]),
        active=bool(row[6]),
        created_at=row[7],
        created_by=row[8],
        updated_at=row[9],
        updated_by=row[10],
        last_checked_at=row[11],
        last_success_at=row[12],
        last_error_at=row[13],
        last_error_message=row[14],
        last_new_count=row[15],
        consecutive_failures=int(row[16] or 0),
    )


def _clean_slug(client_slug: str | None) -> str:
    return (client_slug or "").strip().lower()


def list_alerts(client_slug: str, *, active_only: bool = False) -> list[Alert]:
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return []
    ensure_schema()
    sql = f"SELECT {_ALERT_COLUMNS} FROM web_mention_alerts WHERE client_slug = %s"
    if active_only:
        sql += " AND active"
    sql += " ORDER BY active DESC, category, name"
    with db.connection() as conn:
        rows = conn.execute(sql, (slug,)).fetchall()
    return [_row_to_alert(r) for r in rows]


def get_alert(alert_id: int, *, client_slug: str | None = None) -> Alert | None:
    if not enabled():
        return None
    ensure_schema()
    sql = f"SELECT {_ALERT_COLUMNS} FROM web_mention_alerts WHERE id = %s"
    params: list[Any] = [int(alert_id)]
    if client_slug:
        sql += " AND client_slug = %s"
        params.append(_clean_slug(client_slug))
    with db.connection() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return _row_to_alert(row) if row else None


def has_alerts(client_slug: str) -> bool:
    """Whether this account has any Web Mentions alert at all (nav gating)."""
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM web_mention_alerts WHERE client_slug = %s LIMIT 1", (slug,)
        ).fetchone()
    return row is not None


def slugs_with_active_alerts() -> list[str]:
    """Every client the scheduled ingest should poll."""
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT client_slug FROM web_mention_alerts WHERE active ORDER BY client_slug"
        ).fetchall()
    return [str(r[0]) for r in rows]


def create_alert(
    client_slug: str,
    *,
    name: str,
    feed_url: str,
    category: str = DEFAULT_CATEGORY,
    subject: str = "",
    active: bool = True,
    created_by: str | None = None,
) -> Alert:
    """Add an alert, or update the one already holding this feed URL.

    Re-adding a feed the account already monitors edits that alert instead of
    creating a second one — two rows on one feed would poll it twice and count
    every result twice.
    """
    slug = _clean_slug(client_slug)
    clean_name = (name or "").strip()[:200]
    clean_url = (feed_url or "").strip()
    if not slug:
        raise ValueError("A client slug is required.")
    if not clean_name:
        raise ValueError("Give the alert a name.")
    if not clean_url:
        raise ValueError("A Google Alerts RSS feed URL is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save an alert.")
    ensure_schema()

    stored_url = _encrypt(clean_url)
    cat = normalize_category(category)
    subj = (subject or "").strip()[:200] or clean_name
    actor = (created_by or "").strip() or None

    with db.connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO web_mention_alerts
              (client_slug, name, subject, feed_url, feed_key, category, active,
               created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_slug, feed_key) DO UPDATE SET
              feed_url = EXCLUDED.feed_url,
              name = EXCLUDED.name,
              subject = EXCLUDED.subject,
              category = EXCLUDED.category,
              active = EXCLUDED.active,
              updated_at = NOW(),
              updated_by = EXCLUDED.updated_by
            RETURNING {_ALERT_COLUMNS}
            """,
            (slug, clean_name, subj, stored_url, feed_key(clean_url), cat, bool(active),
             actor, actor),
        ).fetchone()
    return _row_to_alert(row)


def update_alert(
    alert_id: int,
    client_slug: str,
    *,
    name: str | None = None,
    subject: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    feed_url: str | None = None,
    updated_by: str | None = None,
) -> Alert | None:
    """Patch an alert. Only the fields passed are written."""
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return None
    ensure_schema()
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        clean = (name or "").strip()[:200]
        if not clean:
            raise ValueError("Give the alert a name.")
        sets.append("name = %s")
        params.append(clean)
    if subject is not None:
        sets.append("subject = %s")
        params.append((subject or "").strip()[:200])
    if category is not None:
        sets.append("category = %s")
        params.append(normalize_category(category))
    if active is not None:
        sets.append("active = %s")
        params.append(bool(active))
    if feed_url is not None:
        clean_url = (feed_url or "").strip()
        if not clean_url:
            raise ValueError("A Google Alerts RSS feed URL is required.")
        sets.append("feed_url = %s")
        params.append(_encrypt(clean_url))
        sets.append("feed_key = %s")
        params.append(feed_key(clean_url))
        # A re-pasted URL is the fix for a broken feed; clear the old failure so
        # the admin sees the next real result rather than a stale error.
        sets.append("last_error_message = NULL")
        sets.append("consecutive_failures = 0")
    if not sets:
        return get_alert(alert_id, client_slug=slug)
    sets.append("updated_at = NOW()")
    sets.append("updated_by = %s")
    params.append((updated_by or "").strip() or None)
    params.extend([int(alert_id), slug])
    with db.connection() as conn:
        row = conn.execute(
            f"UPDATE web_mention_alerts SET {', '.join(sets)} "
            f"WHERE id = %s AND client_slug = %s RETURNING {_ALERT_COLUMNS}",
            tuple(params),
        ).fetchone()
    return _row_to_alert(row) if row else None


def delete_alert(alert_id: int, client_slug: str) -> bool:
    """Remove an alert that has never collected anything.

    An alert with mentions is never deleted — the reporting history behind it
    would go with it. Deactivate that one instead; polling stops and every
    mention it already found stays on the page.
    """
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM web_mentions WHERE client_slug = %s AND alert_id = %s LIMIT 1",
            (slug, int(alert_id)),
        ).fetchone()
        if row is not None:
            raise ValueError(
                "This alert has collected mentions. Deactivate it instead — "
                "deleting it would remove reporting history."
            )
        deleted = conn.execute(
            "DELETE FROM web_mention_alerts WHERE id = %s AND client_slug = %s RETURNING id",
            (int(alert_id), slug),
        ).fetchone()
    return deleted is not None


def record_fetch_result(
    alert_id: int,
    *,
    ok: bool,
    new_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Stamp the outcome of one feed poll onto its alert.

    ``last_checked_at`` moves on every attempt; ``last_success_at`` only on a
    clean fetch, so "last successfully checked" stays honest while a feed is
    failing. Never raises — a bookkeeping failure must not abort the run.
    """
    if not enabled():
        return
    try:
        ensure_schema()
        with db.connection() as conn:
            if ok:
                conn.execute(
                    """
                    UPDATE web_mention_alerts
                       SET last_checked_at = NOW(), last_success_at = NOW(),
                           last_new_count = %s, last_error_message = NULL,
                           consecutive_failures = 0
                     WHERE id = %s
                    """,
                    (int(new_count), int(alert_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE web_mention_alerts
                       SET last_checked_at = NOW(), last_error_at = NOW(),
                           last_error_message = %s,
                           consecutive_failures = consecutive_failures + 1
                     WHERE id = %s
                    """,
                    (str(error_message or "Feed fetch failed.")[:500], int(alert_id)),
                )
    except Exception:
        log.exception("web mentions: could not record fetch result for alert %s", alert_id)


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------

def dedupe_hash(*, alert_id: int, url: str, title: str) -> str:
    """Stable identity for one result within one alert.

    Keyed on the *destination* URL (already unwrapped from Google's redirect and
    stripped of tracking params), so the same article re-served on every refresh
    — with a fresh Google redirect wrapper each time — hashes the same. A feed
    entry with no usable link falls back to its headline. The alert id is part of
    the key on purpose: two alerts catching one article are two monitored terms
    being mentioned, which is what share of mentions counts.
    """
    key = (url or "").strip().lower() or f"title:{(title or '').strip().lower()}"
    return hashlib.sha256(f"{int(alert_id)}|{key}".encode()).hexdigest()


def insert_mentions(client_slug: str, alert: Alert, entries: list[dict[str, Any]]) -> int:
    """Store the new results from one feed. Returns how many were actually new.

    Duplicates are dropped by the unique index (``ON CONFLICT DO NOTHING``)
    rather than by a pre-read, so two workers polling the same feed at once
    cannot both decide an entry is new.
    """
    slug = _clean_slug(client_slug)
    if not slug or not entries or not enabled():
        return 0
    ensure_schema()
    inserted = 0
    with db.connection() as conn:
        for entry in entries:
            url = str(entry.get("url") or "")[:2000]
            title = str(entry.get("title") or "")[:TITLE_MAX_CHARS]
            if not url and not title:
                continue
            published_at = entry.get("published_at")
            estimated = not isinstance(published_at, datetime)
            mention_day = (
                published_at.astimezone(UTC).date()
                if isinstance(published_at, datetime)
                else datetime.now(tz=UTC).date()
            )
            row = conn.execute(
                """
                INSERT INTO web_mentions
                  (client_slug, alert_id, alert_name, category, subject, dedupe_hash,
                   title, url, google_url, source, snippet, published_at,
                   published_estimated, mention_date, entry_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_slug, dedupe_hash) DO NOTHING
                RETURNING id
                """,
                (
                    slug,
                    alert.id,
                    alert.name,
                    alert.category,
                    alert.subject or alert.name,
                    dedupe_hash(alert_id=alert.id, url=url, title=title),
                    title,
                    url,
                    str(entry.get("google_url") or "")[:2000] or None,
                    str(entry.get("source") or "")[:SOURCE_MAX_CHARS],
                    str(entry.get("snippet") or "")[:SNIPPET_MAX_CHARS],
                    published_at if isinstance(published_at, datetime) else None,
                    estimated,
                    mention_day,
                    str(entry.get("entry_id") or "")[:500] or None,
                ),
            ).fetchone()
            if row is not None:
                inserted += 1
    return inserted


def _filter_sql(
    *,
    start: date | None,
    end: date | None,
    alert_id: int | None,
    category: str | None,
    source: str | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("mention_date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("mention_date <= %s")
        params.append(end)
    if alert_id:
        clauses.append("alert_id = %s")
        params.append(int(alert_id))
    if category:
        clauses.append("category = %s")
        params.append(normalize_category(category))
    if source:
        clauses.append("source = %s")
        params.append(str(source)[:SOURCE_MAX_CHARS])
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def count_mentions(
    client_slug: str,
    *,
    start: date | None = None,
    end: date | None = None,
    alert_id: int | None = None,
    category: str | None = None,
    source: str | None = None,
) -> int:
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return 0
    ensure_schema()
    where, params = _filter_sql(
        start=start, end=end, alert_id=alert_id, category=category, source=source
    )
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM web_mentions WHERE client_slug = %s{where}",
            tuple([slug] + params),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def summary(
    client_slug: str,
    *,
    start: date | None = None,
    end: date | None = None,
    alert_id: int | None = None,
    category: str | None = None,
    source: str | None = None,
) -> dict[str, int]:
    """Headline counts for one window, in a single pass over the rows."""
    slug = _clean_slug(client_slug)
    empty = {"total": 0, "brand": 0, "competitor": 0, "sources": 0}
    if not slug or not enabled():
        return empty
    ensure_schema()
    where, params = _filter_sql(
        start=start, end=end, alert_id=alert_id, category=category, source=source
    )
    with db.connection() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE category = 'brand'),
                   COUNT(*) FILTER (WHERE category = 'competitor'),
                   COUNT(DISTINCT NULLIF(source, ''))
              FROM web_mentions
             WHERE client_slug = %s{where}
            """,
            tuple([slug] + params),
        ).fetchone()
    if not row:
        return empty
    return {
        "total": int(row[0] or 0),
        "brand": int(row[1] or 0),
        "competitor": int(row[2] or 0),
        "sources": int(row[3] or 0),
    }


def daily_counts(
    client_slug: str,
    *,
    start: date | None = None,
    end: date | None = None,
    alert_id: int | None = None,
    category: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return []
    ensure_schema()
    where, params = _filter_sql(
        start=start, end=end, alert_id=alert_id, category=category, source=source
    )
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT mention_date, COUNT(*)
              FROM web_mentions
             WHERE client_slug = %s{where}
             GROUP BY mention_date
             ORDER BY mention_date
            """,
            tuple([slug] + params),
        ).fetchall()
    return [{"date": r[0], "count": int(r[1] or 0)} for r in rows]


def share_of_mentions(
    client_slug: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Mentions per monitored subject, brand and competitor alerts only.

    Deliberately ignores the alert / category / source filters: the share panel
    answers "how do the monitored names compare", which only means anything when
    every one of them is in the denominator.
    """
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return []
    ensure_schema()
    clauses = ["client_slug = %s", "category IN ('brand','competitor')"]
    params: list[Any] = [slug]
    if start is not None:
        clauses.append("mention_date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("mention_date <= %s")
        params.append(end)
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(subject, ''), alert_name) AS name,
                   MIN(category) AS category,
                   COUNT(*)
              FROM web_mentions
             WHERE {' AND '.join(clauses)}
             GROUP BY 1
             ORDER BY 3 DESC, 1
            """,
            tuple(params),
        ).fetchall()
    return [
        {"subject": str(r[0] or ""), "category": normalize_category(r[1]), "count": int(r[2] or 0)}
        for r in rows
    ]


def top_sources(
    client_slug: str,
    *,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Publications seen in the window — powers the Source filter and the count."""
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return []
    ensure_schema()
    where, params = _filter_sql(
        start=start, end=end, alert_id=None, category=None, source=None
    )
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT source, COUNT(*)
              FROM web_mentions
             WHERE client_slug = %s AND source <> ''{where}
             GROUP BY source
             ORDER BY 2 DESC, 1
             LIMIT %s
            """,
            tuple([slug] + params + [int(limit)]),
        ).fetchall()
    return [{"source": str(r[0]), "count": int(r[1] or 0)} for r in rows]


_MENTION_COLUMNS = (
    "id, client_slug, alert_id, alert_name, category, subject, title, url, google_url, "
    "source, snippet, published_at, published_estimated, mention_date, discovered_at"
)


def _row_to_mention(row: tuple) -> Mention:
    return Mention(
        id=int(row[0]),
        client_slug=str(row[1]),
        alert_id=int(row[2]) if row[2] is not None else None,
        alert_name=str(row[3] or ""),
        category=normalize_category(row[4]),
        subject=str(row[5] or ""),
        title=str(row[6] or ""),
        url=str(row[7] or ""),
        google_url=row[8],
        source=str(row[9] or ""),
        snippet=str(row[10] or ""),
        published_at=row[11],
        published_estimated=bool(row[12]),
        mention_date=row[13],
        discovered_at=row[14],
    )


def list_mentions(
    client_slug: str,
    *,
    start: date | None = None,
    end: date | None = None,
    alert_id: int | None = None,
    category: str | None = None,
    source: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Mention]:
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return []
    ensure_schema()
    where, params = _filter_sql(
        start=start, end=end, alert_id=alert_id, category=category, source=source
    )
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_MENTION_COLUMNS}
              FROM web_mentions
             WHERE client_slug = %s{where}
             ORDER BY mention_date DESC, discovered_at DESC, id DESC
             LIMIT %s OFFSET %s
            """,
            tuple([slug] + params + [int(limit), max(0, int(offset))]),
        ).fetchall()
    return [_row_to_mention(r) for r in rows]


def mention_counts_by_alert(client_slug: str) -> dict[int, int]:
    """Lifetime mention count per alert — the admin table's "collected" column."""
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return {}
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT alert_id, COUNT(*) FROM web_mentions WHERE client_slug = %s "
            "AND alert_id IS NOT NULL GROUP BY alert_id",
            (slug,),
        ).fetchall()
    return {int(r[0]): int(r[1] or 0) for r in rows}


def latest_mention_date(client_slug: str) -> date | None:
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT MAX(mention_date) FROM web_mentions WHERE client_slug = %s", (slug,)
        ).fetchone()
    return row[0] if row and row[0] else None
