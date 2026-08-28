"""Web Mentions routes (Google Alerts RSS monitoring).

    GET  /dashboard/{slug}/web-mentions                    the report page (HTML)
    GET  /dashboard/{slug}/web-mentions.json               the same data as JSON
    POST /dashboard/{slug}/web-mentions/alerts             add an alert (admin)
    POST /dashboard/{slug}/web-mentions/alerts/{id}        edit one (admin)
    POST /dashboard/{slug}/web-mentions/alerts/{id}/delete remove an empty one (admin)
    POST /dashboard/{slug}/web-mentions/sync               poll this client's feeds now (admin)
    POST /internal/web-mentions/ingest-due                 cron: poll every client's feeds

The admin actions are plain HTML form posts that redirect back to the page with
a flash message — the same shape the ``/admin/dashboards/*`` actions use — so the
alert manager needs no client-side state. The manual sync mirrors the connector
pattern: it hands the work to a BackgroundTask and returns immediately, because
a client with a dozen feeds would otherwise hold the request open for a minute.

Nothing here ever returns a feed URL. The page renders only the masked form for
admins, and the JSON API omits it entirely.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import audit_log
import web_auth
import web_mentions_service as service
import web_mentions_store as store
import web_users
from cron_security import require_cron_secret
from dashboard.renderers.web_mentions_renderer import render_web_mentions_page
from dashboard.routes.helpers import validate_client_slug
from dashboard.utils.urls import web_mentions_page_url

_log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

_INGEST_LOCK = "web-mentions-ingest"


def _label_for(slug: str) -> str:
    try:
        import client_dashboard_config

        cfg = client_dashboard_config.get_config(slug)
        return (getattr(cfg, "label", None) or slug).strip() or slug
    except Exception:
        return slug


def _int_or_none(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _outcome_message(alert_name: str, outcome: dict) -> tuple[str, str]:
    """Turn one poll's result into (saved_message, error_message).

    Exactly one is non-empty. A feed that could not be read is still a *saved*
    alert, so the message says both — losing the row because Google was briefly
    unreachable would be the worse outcome.
    """
    name = f"Alert \u201c{alert_name}\u201d"
    if not outcome.get("ok"):
        reason = str(outcome.get("error") or "the feed could not be read")
        return "", f"{name} saved, but {reason[0].lower()}{reason[1:]}"
    new = int(outcome.get("new") or 0)
    if new:
        return f"{name} saved \u2014 {new} mention{'' if new == 1 else 's'} found.", ""
    if outcome.get("seen"):
        return f"{name} saved \u2014 feed checked, nothing new.", ""
    return (
        f"{name} saved \u2014 the feed is reachable but empty right now. "
        "Results appear as Google finds them.", ""
    )


def _require_admin(request: Request, slug: str):
    """Auth for the alert-management actions. Returns (auth, email).

    Alerts decide what the whole account reports on, so editing them is an admin
    action — the same bar ``consent`` puts on its scan configuration.
    """
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    if web_users.enabled() and not (user and user.role == "admin"):
        raise HTTPException(status_code=403, detail="Only admins can manage Web Mentions alerts.")
    return auth, (user.email if user else None)


def _back(slug: str, *, saved: str = "", error: str = "") -> RedirectResponse:
    """Back to the page with one flash message — the admin forms' only response."""
    url = (
        web_mentions_page_url(client_slug=slug, access_key=None, use_session=True)
        or f"/dashboard/{slug}/web-mentions"
    )
    if error:
        url = f"{url}?error={quote(error[:200], safe='')}"
    elif saved:
        url = f"{url}?saved={quote(saved[:200], safe='')}"
    return RedirectResponse(url=url, status_code=303)


# ── Report page ─────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/web-mentions", response_class=HTMLResponse)
def web_mentions_page(client_slug: str, request: Request):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    user = auth.user
    params = request.query_params
    report = service.build_report(
        slug,
        label=_label_for(slug),
        range_days=service.sanitize_range_days(params.get("range")),
        alert_id=_int_or_none(params.get("alert")),
        category=(params.get("category") or "").strip() or None,
        source=(params.get("source") or "").strip() or None,
    )
    return HTMLResponse(render_web_mentions_page(
        client_slug=slug,
        label=report.label,
        report=report,
        access_key=auth.access_key,
        use_session=auth.use_session,
        session_email=user.email if user else None,
        session_is_admin=bool(user and user.role == "admin"),
        flash=(params.get("saved") or "").strip()[:200],
        flash_error=(params.get("error") or "").strip()[:200],
    ))


@router.get("/dashboard/{client_slug}/web-mentions.json")
def web_mentions_json(client_slug: str, request: Request):
    """The page's data as JSON. Feed URLs are never part of the payload."""
    slug = validate_client_slug(client_slug)
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    params = request.query_params
    report = service.build_report(
        slug,
        label=_label_for(slug),
        range_days=service.sanitize_range_days(params.get("range")),
        alert_id=_int_or_none(params.get("alert")),
        category=(params.get("category") or "").strip() or None,
        source=(params.get("source") or "").strip() or None,
    )
    return JSONResponse({
        "client_slug": slug,
        "configured": report.configured,
        "range_days": report.range_days,
        "start": report.start.isoformat() if report.start else None,
        "end": report.end.isoformat() if report.end else None,
        "summary": {
            "total": report.total,
            "previous_total": report.prev_total,
            "brand": report.brand,
            "competitor": report.competitor,
            "sources": report.sources,
        },
        "daily": [
            {"date": row["date"].isoformat(), "count": row["count"]} for row in report.daily
        ],
        "share_of_mentions": [
            {"subject": r.subject, "category": r.category, "count": r.count,
             "pct": round(r.pct, 1)}
            for r in report.share
        ],
        "mentions": [m.public_dict() for m in report.mentions],
        "alerts": [
            a.public_dict(mention_count=report.alert_counts.get(a.id, 0)) for a in report.alerts
        ],
    })


# ── Alert management (admin) ────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/web-mentions/alerts")
def web_mentions_add_alert(
    client_slug: str,
    request: Request,
    name: str = Form(...),
    feed_url: str = Form(...),
    category: str = Form("other"),
    subject: str = Form(""),
):
    slug = validate_client_slug(client_slug)
    _auth, email = _require_admin(request, slug)
    if not store.enabled():
        return _back(slug, error="DATABASE_URL is required to save an alert.")

    ok, normalized = service.validate_feed_url(feed_url)
    if not ok:
        return _back(slug, error=normalized)
    try:
        alert = store.create_alert(
            slug,
            name=name,
            feed_url=normalized,
            category=category,
            subject=subject,
            created_by=email,
        )
    except (ValueError, RuntimeError) as exc:
        return _back(slug, error=str(exc)[:200])

    audit_log.record(
        action="web_mentions.alert_saved",
        actor_email=email,
        # The feed URL is a credential; only its masked shape is ever logged.
        detail={"client_slug": slug, "alert_id": alert.id, "name": alert.name,
                "category": alert.category, "feed": alert.masked_feed_url},
        **audit_log.request_context(request),
    )
    # Check the feed now, while the admin is still looking at the form. The most
    # useful moment to learn a URL is wrong is the moment you paste it — not
    # tomorrow, after the scheduled run quietly recorded a 404.
    outcome = service.ingest_alert(alert, timeout=service.inline_timeout())
    saved_msg, error_msg = _outcome_message(alert.name, outcome)
    return _back(slug, saved=saved_msg, error=error_msg)


@router.post("/dashboard/{client_slug}/web-mentions/alerts/{alert_id}")
def web_mentions_update_alert(
    client_slug: str,
    alert_id: int,
    request: Request,
    name: str | None = Form(None),
    category: str | None = Form(None),
    subject: str | None = Form(None),
    active: str | None = Form(None),
    feed_url: str | None = Form(None),
):
    slug = validate_client_slug(client_slug)
    _auth, email = _require_admin(request, slug)

    normalized_feed: str | None = None
    if feed_url is not None and feed_url.strip():
        ok, normalized_feed = service.validate_feed_url(feed_url)
        if not ok:
            return _back(slug, error=normalized_feed)

    try:
        alert = store.update_alert(
            alert_id,
            slug,
            name=name,
            subject=subject,
            category=category,
            active=(active.strip() in ("1", "true", "on", "yes")) if active is not None else None,
            feed_url=normalized_feed,
            updated_by=email,
        )
    except ValueError as exc:
        return _back(slug, error=str(exc)[:200])
    if alert is None:
        return _back(slug, error="That alert no longer exists.")

    audit_log.record(
        action="web_mentions.alert_updated",
        actor_email=email,
        detail={"client_slug": slug, "alert_id": alert.id, "active": alert.active,
                "category": alert.category},
        **audit_log.request_context(request),
    )
    state = "activated" if alert.active else "deactivated"
    message = (
        f"Alert “{alert.name}” {state}."
        if active is not None
        else f"Alert “{alert.name}” updated."
    )
    if active is not None and not alert.active:
        message += " Mentions it already collected are kept."
    return _back(slug, saved=message)


@router.post("/dashboard/{client_slug}/web-mentions/alerts/{alert_id}/delete")
def web_mentions_delete_alert(client_slug: str, alert_id: int, request: Request):
    slug = validate_client_slug(client_slug)
    _auth, email = _require_admin(request, slug)
    try:
        removed = store.delete_alert(alert_id, slug)
    except ValueError as exc:
        # Refused because it holds history — the store's rule, surfaced as-is.
        return _back(slug, error=str(exc)[:200])
    if not removed:
        return _back(slug, error="That alert no longer exists.")
    audit_log.record(
        action="web_mentions.alert_deleted",
        actor_email=email,
        detail={"client_slug": slug, "alert_id": int(alert_id)},
        **audit_log.request_context(request),
    )
    return _back(slug, saved="Alert deleted.")


# ── Manual sync (admin) ─────────────────────────────────────────────────────

def _sync_bg(slug: str, alert_id: int | None) -> None:
    try:
        service.ingest_client(slug, alert_id=alert_id)
    except Exception:
        _log.exception("web mentions manual sync failed [%s]", slug)


@router.post("/dashboard/{client_slug}/web-mentions/sync")
def web_mentions_sync(client_slug: str, request: Request, background_tasks: BackgroundTasks):
    slug = validate_client_slug(client_slug)
    _auth, email = _require_admin(request, slug)
    alert_id = _int_or_none(request.query_params.get("alert"))

    # One row's Sync button. Polled inline so the row's status updates on the
    # redirect — one feed is quick, and the whole point is immediate feedback.
    if alert_id:
        alert = store.get_alert(alert_id, client_slug=slug)
        if alert is None:
            return _back(slug, error="That alert no longer exists.")
        if not alert.active:
            return _back(slug, error=f"Alert “{alert.name}” is inactive. Activate it to sync.")
        audit_log.record(
            action="web_mentions.sync_started",
            actor_email=email,
            detail={"client_slug": slug, "alerts": 1, "alert_id": alert.id},
            **audit_log.request_context(request),
        )
        outcome = service.ingest_alert(alert, timeout=service.inline_timeout())
        saved_msg, error_msg = _outcome_message(alert.name, outcome)
        # _outcome_message is phrased for a save; this path only re-checked.
        return _back(
            slug,
            saved=saved_msg.replace(" saved — ", " — ", 1),
            error=error_msg.replace(" saved, but ", " — ", 1),
        )

    alerts = store.list_alerts(slug, active_only=True)
    if not alerts:
        return _back(slug, error="No active alerts to sync.")
    audit_log.record(
        action="web_mentions.sync_started",
        actor_email=email,
        detail={"client_slug": slug, "alerts": len(alerts), "alert_id": None},
        **audit_log.request_context(request),
    )
    background_tasks.add_task(_sync_bg, slug, None)
    return _back(
        slug,
        saved=f"Syncing {len(alerts)} feed{'' if len(alerts) == 1 else 's'} — "
              "refresh in a moment to see new mentions.",
    )


# ── Scheduled ingestion (cron) ──────────────────────────────────────────────

@router.post("/internal/web-mentions/ingest-due", dependencies=[Depends(require_cron_secret)])
def internal_web_mentions_ingest(background_tasks: BackgroundTasks) -> dict:
    """Poll every active Google Alerts feed. Hands-off, like sync-bq-all.

    Returns immediately; the fetches run as a background batch. A Postgres lock
    keeps a slow run from stacking on the next cron tick, and one client's
    failure never stops the others (see ``web_mentions_service.ingest_all``).
    """
    import cron_locks

    if not store.enabled():
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")

    slugs = store.slugs_with_active_alerts()
    if not slugs:
        return {"status": "ok", "clients": 0, "slugs": []}
    if not cron_locks.try_acquire(_INGEST_LOCK, ttl_seconds=2 * 3600, locked_by="web-mentions"):
        return {"status": "skipped", "reason": "previous run still in progress", "slugs": slugs}

    def _run_all() -> None:
        try:
            result = service.ingest_all()
            _log.info(
                "web mentions ingest: %s client(s), %s new mention(s), %s failed feed(s)",
                result["clients"], result["new_mentions"], result["failed_feeds"],
            )
        except Exception:
            _log.exception("web mentions scheduled ingest failed")
        finally:
            cron_locks.release(_INGEST_LOCK)

    background_tasks.add_task(_run_all)
    return {"status": "started", "clients": len(slugs), "slugs": slugs}
