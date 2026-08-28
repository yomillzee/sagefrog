"""Google Alerts RSS ingestion and the Web Mentions report.

Google Alerts has no results API. What it does have is a per-alert RSS/Atom
feed, which is what this module reads: fetch, parse, normalize, insert what is
new, and record the outcome on the alert. Nothing here scrapes Google search
results or drives a Google login, and nothing here needs credentials — a feed
URL *is* the credential, which is why ``web_mentions_store`` treats it as one.

Three shapes of real-world feed damage drive most of the code below:

*   **Google's redirect wrapper.** Every link comes through
    ``google.com/url?...&url=<real link>&ct=…&usg=…``. The wrapper changes on
    every refresh, so storing it would defeat de-duplication and would send a
    reader through a tracking hop. :func:`unwrap_link` pulls the destination out
    and strips campaign parameters; the original is kept alongside it.
*   **Missing fields.** Feeds routinely omit the publisher, and occasionally the
    date. A missing publisher falls back to the destination's domain; a missing
    date falls back to the day we discovered it, flagged so the page can say so
    rather than inventing a publication date.
*   **Feeds that simply break.** A deleted alert 404s, a slow one times out, and
    a truncated one fails to parse. Each is caught per feed, recorded on that
    alert, and skipped — one client's broken feed never stops another client's
    ingest, and a failing feed never removes what it already collected.

Tunables: ``WEB_MENTIONS_TIMEOUT_SECONDS`` (default 20),
``WEB_MENTIONS_MAX_ENTRIES`` per feed per run (default 100),
``WEB_MENTIONS_ALLOW_ANY_FEED=1`` to accept non-Google feed URLs.
"""

from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse, urlencode

import web_mentions_store as store

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20.0
_DEFAULT_MAX_ENTRIES = 100
_MAX_FEED_BYTES = 5 * 1024 * 1024

_USER_AGENT = "sagefrog-portal-web-mentions/1.0 (+https://sagefrog.com)"

_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Query parameters that identify a campaign or a click, not a document. Stripped
# from the destination URL so the same article always hashes to the same key.
_TRACKING_PARAMS = frozenset({
    "ct", "cd", "usg", "usq", "rct", "sa", "ved", "source", "sourceid",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src",
})

# Date-range presets for the page filter: (query value, days, label).
RANGE_PRESETS: tuple[tuple[str, int, str], ...] = (
    ("7", 7, "Last 7 days"),
    ("30", 30, "Last 30 days"),
    ("90", 90, "Last 90 days"),
    ("180", 180, "Last 180 days"),
    ("365", 365, "Last 365 days"),
)
DEFAULT_RANGE_DAYS = 30

# How many rows the Recent Mentions table renders. Enough to scroll a quarter of
# heavy coverage; not enough to build a megabyte of HTML.
MENTION_TABLE_LIMIT = 200

# Subjects shown individually in the share-of-mentions panel before the tail is
# rolled into "Other".
SHARE_MAX_SUBJECTS = 6


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Feed URL validation
# ---------------------------------------------------------------------------

def validate_feed_url(url: str) -> tuple[bool, str]:
    """Validate a pasted Google Alerts feed URL. Returns (ok, normalized_or_error).

    Restricted to ``https://www.google.com/alerts/feeds/...`` on purpose. It is
    the only URL shape this feature is for, and pinning the host means an admin
    typo can never point the server's fetcher at an internal address. Set
    ``WEB_MENTIONS_ALLOW_ANY_FEED=1`` to accept other https feeds (still never a
    private or loopback host).
    """
    raw = (url or "").strip()
    if not raw:
        return False, "Paste the RSS feed URL from Google Alerts."
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return False, "That does not look like a URL."
    if parsed.scheme not in ("http", "https"):
        return False, "The feed URL must start with https://."
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "The feed URL has no hostname."

    if (os.getenv("WEB_MENTIONS_ALLOW_ANY_FEED") or "").strip().lower() in ("1", "true", "yes"):
        import ipaddress

        if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
            return False, f"Internal hosts are not allowed: {host}"
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"Private/loopback addresses are not allowed: {host}"
        except ValueError:
            pass
        return True, raw

    if host not in ("www.google.com", "google.com"):
        return False, (
            "That is not a Google Alerts feed. In Google Alerts, open the alert's "
            "pencil icon, set “Deliver to” to RSS feed, then copy the feed link."
        )
    if not (parsed.path or "").startswith("/alerts/feeds/"):
        return False, (
            "That Google URL is not an alert feed. The link should look like "
            "https://www.google.com/alerts/feeds/<id>/<id>."
        )
    return True, urlunparse(("https", "www.google.com", parsed.path, "", parsed.query, ""))


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_markup(value: str | None) -> str:
    """Feed text as plain text. Google bolds the matched term inside titles."""
    text = html.unescape(str(value or ""))
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def unwrap_link(href: str | None) -> tuple[str, str | None]:
    """Split a feed link into (destination, google_redirect_or_None).

    Returns the destination with tracking parameters removed. A link that is not
    a Google redirect comes back unchanged with ``None`` alongside it.
    """
    raw = (href or "").strip()
    if not raw:
        return "", None
    google_url: str | None = None
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw, None
    host = (parsed.hostname or "").lower()
    if host.endswith("google.com") and (parsed.path or "").startswith("/url"):
        target = parse_qs(parsed.query or "").get("url") or parse_qs(parsed.query or "").get("q")
        if target and target[0].strip():
            google_url = raw
            raw = target[0].strip()
    return _strip_tracking_params(raw), google_url


def _strip_tracking_params(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    if not parsed.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
        if not (k.lower().startswith("utm_") or k.lower() in _TRACKING_PARAMS)
    ]
    flat = [(k, item) for k, values in kept for item in values]
    return urlunparse(parsed._replace(query=urlencode(flat)))


def _parse_datetime(value: str | None) -> datetime | None:
    """Best-effort date parse across the formats feeds actually use."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except Exception:
        pass
    return None


def _source_from_url(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def parse_feed(payload: bytes | str) -> list[dict[str, Any]]:
    """Normalize an Atom (Google Alerts) or RSS 2.0 document into entry dicts.

    Raises ``ValueError`` on a document that is not parseable XML, which the
    caller records as that feed's error. Individual entries that are malformed
    are skipped rather than failing the whole feed — a feed is usually only
    partly broken.
    """
    import xml.etree.ElementTree as ET

    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not data:
        raise ValueError("The feed returned an empty response.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"The feed is not valid XML ({str(exc)[:120]}).") from exc

    nodes = root.findall(f".//{_ATOM_NS}entry") or root.findall(".//item")
    entries: list[dict[str, Any]] = []
    for node in nodes:
        try:
            entry = _entry_from_node(node)
        except Exception:
            log.debug("web mentions: skipped an unparseable feed entry", exc_info=True)
            continue
        if entry:
            entries.append(entry)
    return entries


def _text(node, *names: str) -> str:
    """First non-empty child text among ``names``, Atom namespace or bare."""
    for name in names:
        for tag in (f"{_ATOM_NS}{name}", name):
            found = node.find(tag)
            if found is not None:
                # Atom carries type="html" content whose markup is escaped text;
                # itertext() also picks up any inline markup left as real nodes.
                text = "".join(found.itertext())
                if text and text.strip():
                    return text
    return ""


def _entry_from_node(node) -> dict[str, Any] | None:
    href = ""
    for tag in (f"{_ATOM_NS}link", "link"):
        link = node.find(tag)
        if link is not None:
            href = (link.get("href") or link.text or "").strip()
            if href:
                break
    url, google_url = unwrap_link(href)
    title = strip_markup(_text(node, "title"))
    if not url and not title:
        return None

    snippet = strip_markup(_text(node, "content", "summary", "description"))
    published = _parse_datetime(
        _text(node, "published", "pubDate", "updated") or None
    )

    source = ""
    for tag in (f"{_ATOM_NS}author", "author"):
        author = node.find(tag)
        if author is not None:
            source = strip_markup(_text(author, "name") or author.text or "")
            if source:
                break
    if not source:
        for tag in (f"{_ATOM_NS}source", "source"):
            src = node.find(tag)
            if src is not None:
                source = strip_markup(_text(src, "title") or src.text or "")
                if source:
                    break
    if not source:
        source = _source_from_url(url)

    return {
        "title": title,
        "url": url,
        "google_url": google_url,
        "source": source,
        "snippet": snippet,
        "published_at": published,
        "entry_id": strip_markup(_text(node, "id", "guid")),
    }


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_feed(url: str) -> bytes:
    """GET one feed. Raises ``RuntimeError`` with a message fit for the admin UI."""
    import httpx

    timeout = _env_float("WEB_MENTIONS_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9, */*;q=0.5"})
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"The feed timed out after {timeout:.0f}s.") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach the feed: {str(exc)[:160]}") from exc

    if resp.status_code == 404:
        raise RuntimeError(
            "The feed returned 404 — the Google Alert may have been deleted. "
            "Re-create it in Google Alerts and paste the new feed URL."
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"The feed returned HTTP {resp.status_code}.")
    body = resp.content or b""
    if len(body) > _MAX_FEED_BYTES:
        raise RuntimeError("The feed response was unexpectedly large; skipped.")
    if not body.strip():
        raise RuntimeError("The feed returned an empty response.")
    return body


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_alert(alert: store.Alert) -> dict[str, Any]:
    """Poll one feed and store what is new. Never raises.

    The result dict is what both the cron summary and the admin panel's "Sync
    now" report back, so a failure is a value here, not an exception — the caller
    is always a loop over other people's feeds.
    """
    outcome: dict[str, Any] = {
        "alert_id": alert.id,
        "alert_name": alert.name,
        "ok": False,
        "new": 0,
        "seen": 0,
        "error": None,
    }
    if not alert.feed_url:
        outcome["error"] = (
            "The stored feed URL could not be read. Re-paste it to fix this alert."
        )
        store.record_fetch_result(alert.id, ok=False, error_message=outcome["error"])
        return outcome
    try:
        payload = fetch_feed(alert.feed_url)
        entries = parse_feed(payload)
        max_entries = _env_int("WEB_MENTIONS_MAX_ENTRIES", _DEFAULT_MAX_ENTRIES)
        entries = entries[:max_entries]
        outcome["seen"] = len(entries)
        outcome["new"] = store.insert_mentions(alert.client_slug, alert, entries)
        outcome["ok"] = True
        store.record_fetch_result(alert.id, ok=True, new_count=outcome["new"])
    except Exception as exc:
        message = str(exc)[:400] or exc.__class__.__name__
        outcome["error"] = message
        log.warning("web mentions: feed failed [%s / %s]: %s", alert.client_slug, alert.name, message)
        store.record_fetch_result(alert.id, ok=False, error_message=message)
    return outcome


def ingest_client(client_slug: str, *, alert_id: int | None = None) -> dict[str, Any]:
    """Poll every active feed for one client. One bad feed never stops the rest."""
    slug = (client_slug or "").strip().lower()
    alerts = store.list_alerts(slug, active_only=True)
    if alert_id:
        alerts = [a for a in alerts if a.id == int(alert_id)]
    results = [ingest_alert(alert) for alert in alerts]
    return {
        "client_slug": slug,
        "alerts": len(results),
        "new_mentions": sum(r["new"] for r in results),
        "failed": sum(0 if r["ok"] else 1 for r in results),
        "results": results,
    }


def ingest_all() -> dict[str, Any]:
    """Poll every client with at least one active alert.

    A client whose ingest blows up entirely (not just one feed) is logged and
    skipped so the rest of the run still completes.
    """
    slugs = store.slugs_with_active_alerts()
    per_client: list[dict[str, Any]] = []
    for slug in slugs:
        try:
            per_client.append(ingest_client(slug))
        except Exception:
            log.exception("web mentions: client ingest failed [%s]", slug)
            per_client.append(
                {"client_slug": slug, "alerts": 0, "new_mentions": 0, "failed": 1, "results": []}
            )
    return {
        "clients": len(per_client),
        "new_mentions": sum(c["new_mentions"] for c in per_client),
        "failed_feeds": sum(c["failed"] for c in per_client),
        "per_client": per_client,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class ShareRow:
    subject: str
    category: str
    count: int
    pct: float


@dataclass
class WebMentionsReport:
    """Everything the Web Mentions page renders, for one client and window."""

    client_slug: str
    label: str
    configured: bool = False
    range_days: int = DEFAULT_RANGE_DAYS
    start: date | None = None
    end: date | None = None
    prev_start: date | None = None
    prev_end: date | None = None

    total: int = 0
    prev_total: int = 0
    brand: int = 0
    competitor: int = 0
    sources: int = 0

    daily: list[dict[str, Any]] = field(default_factory=list)
    mentions: list[store.Mention] = field(default_factory=list)
    share: list[ShareRow] = field(default_factory=list)
    share_total: int = 0

    alerts: list[store.Alert] = field(default_factory=list)
    alert_counts: dict[int, int] = field(default_factory=dict)
    source_options: list[str] = field(default_factory=list)

    # Applied filters, echoed back so the page can re-render its own controls.
    alert_id: int | None = None
    category: str | None = None
    source: str | None = None
    truncated: bool = False

    @property
    def active_alerts(self) -> list[store.Alert]:
        return [a for a in self.alerts if a.active]

    @property
    def failing_alerts(self) -> list[store.Alert]:
        return [a for a in self.alerts if a.active and a.last_error_message]

    @property
    def last_checked_at(self) -> datetime | None:
        stamps = [a.last_success_at for a in self.alerts if a.last_success_at]
        return max(stamps) if stamps else None

    @property
    def never_synced(self) -> bool:
        return bool(self.alerts) and not any(a.last_checked_at for a in self.alerts)


def sanitize_range_days(raw: Any) -> int:
    if raw is None:
        return DEFAULT_RANGE_DAYS
    token = str(raw).strip()
    for value, days, _label in RANGE_PRESETS:
        if token == value:
            return days
    return DEFAULT_RANGE_DAYS


def _share_rows(raw: list[dict[str, Any]]) -> tuple[list[ShareRow], int]:
    """Percentages over the monitored brand/competitor names, tail rolled up."""
    total = sum(int(r["count"]) for r in raw)
    if not total:
        return [], 0
    head = raw[:SHARE_MAX_SUBJECTS]
    tail = raw[SHARE_MAX_SUBJECTS:]
    rows = [
        ShareRow(
            subject=r["subject"],
            category=r["category"],
            count=int(r["count"]),
            pct=100.0 * int(r["count"]) / total,
        )
        for r in head
    ]
    tail_count = sum(int(r["count"]) for r in tail)
    if tail_count:
        rows.append(
            ShareRow(subject="Other", category="other", count=tail_count,
                     pct=100.0 * tail_count / total)
        )
    return rows, total


def build_report(
    client_slug: str,
    *,
    label: str = "",
    range_days: int = DEFAULT_RANGE_DAYS,
    alert_id: int | None = None,
    category: str | None = None,
    source: str | None = None,
    today: date | None = None,
) -> WebMentionsReport:
    """Assemble the page's data. Degrades to an empty, configured=False report."""
    slug = (client_slug or "").strip().lower()
    days = sanitize_range_days(str(range_days))
    end = today or datetime.now(tz=UTC).date()
    start = end - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    report = WebMentionsReport(
        client_slug=slug,
        label=label or slug,
        range_days=days,
        start=start,
        end=end,
        prev_start=prev_start,
        prev_end=prev_end,
        alert_id=alert_id,
        category=store.normalize_category(category) if category else None,
        source=(source or "").strip() or None,
    )

    alerts = store.list_alerts(slug)
    report.alerts = alerts
    report.configured = bool(alerts)
    if not alerts:
        return report

    # An alert filter pointing at an alert this client does not own is ignored
    # rather than trusted into the query.
    if alert_id and not any(a.id == int(alert_id) for a in alerts):
        report.alert_id = None
        alert_id = None

    filters = {
        "alert_id": report.alert_id,
        "category": report.category,
        "source": report.source,
    }
    totals = store.summary(slug, start=start, end=end, **filters)
    report.total = totals["total"]
    report.brand = totals["brand"]
    report.competitor = totals["competitor"]
    report.sources = totals["sources"]
    report.prev_total = store.count_mentions(slug, start=prev_start, end=prev_end, **filters)
    report.daily = store.daily_counts(slug, start=start, end=end, **filters)
    report.mentions = store.list_mentions(
        slug, start=start, end=end, limit=MENTION_TABLE_LIMIT, **filters
    )
    report.truncated = len(report.mentions) >= MENTION_TABLE_LIMIT
    report.alert_counts = store.mention_counts_by_alert(slug)
    report.source_options = [
        row["source"] for row in store.top_sources(slug, start=start, end=end, limit=200)
    ]
    report.share, report.share_total = _share_rows(
        store.share_of_mentions(slug, start=start, end=end)
    )
    return report
