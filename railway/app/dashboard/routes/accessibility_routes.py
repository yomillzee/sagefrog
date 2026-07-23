"""Accessibility (ADA / WCAG) scoping-audit routes.

    GET  /dashboard/{slug}/accessibility        the audit page (HTML)
    POST /dashboard/{slug}/accessibility/scan    run an on-demand axe-core scan

Unlike Consent Health, this is intentionally **on-demand and stateless**: an admin
edits the page list and runs a scan synchronously, and the result is rendered
straight back — no DB store, no background worker, no cron. The scanner uses
Playwright's *sync* API, which FastAPI runs in its worker threadpool for a plain
``def`` handler, so a blocking scan here doesn't stall the event loop.

Pages are seeded from the client's Consent-scan config when present, so a client
that's already set up for consent scanning needs no extra configuration to audit.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import audit_log
import web_auth
import a11y_scanner
from dashboard.renderers.accessibility_renderer import render_accessibility_page
from dashboard.routes.helpers import validate_client_slug
from dashboard.utils.auth import can_edit_penn_insights

_log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

# Cap how many pages one scan will drive — the request blocks for the duration, so
# keep it bounded. Scoping only needs a representative handful of templates.
_MAX_PAGES = 12


def _label_for(slug: str) -> str:
    try:
        import client_dashboard_config
        cfg = client_dashboard_config.get_config(slug)
        return (getattr(cfg, "label", None) or slug).strip() or slug
    except Exception:
        return slug


def _seed_urls(slug: str) -> list[str]:
    """Default page list — reuse the client's configured Consent-scan pages."""
    try:
        import consent_store
        cfg = consent_store.get_config(slug)
        if cfg and cfg.pages:
            return list(cfg.pages)
    except Exception:
        pass
    return []


def _parse_urls(raw: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in (raw or "").splitlines():
        u = line.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


@router.get("/dashboard/{client_slug}/accessibility", response_class=HTMLResponse)
def accessibility_page(client_slug: str, request: Request):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    user = auth.user
    return HTMLResponse(render_accessibility_page(
        client_slug=slug,
        label=_label_for(slug),
        access_key=auth.access_key,
        use_session=auth.use_session,
        session_email=user.email if user else None,
        session_is_admin=bool(user and user.role == "admin"),
        default_urls=_seed_urls(slug),
    ))


@router.post("/dashboard/{client_slug}/accessibility/scan", response_class=HTMLResponse)
def accessibility_scan(client_slug: str, request: Request, urls: str = Form("")):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    user = auth.user
    is_admin = bool(user and user.role == "admin")
    if not can_edit_penn_insights(session_is_admin=is_admin, access_key=auth.access_key):
        raise HTTPException(status_code=403, detail="Only admins can run accessibility scans.")

    label = _label_for(slug)
    requested = _parse_urls(urls)

    def _render(*, scan=None, agg=None, scan_error=None) -> HTMLResponse:
        return HTMLResponse(render_accessibility_page(
            client_slug=slug, label=label,
            access_key=auth.access_key, use_session=auth.use_session,
            session_email=user.email if user else None, session_is_admin=is_admin,
            default_urls=_seed_urls(slug), submitted_urls=requested,
            scan=scan, agg=agg, scan_error=scan_error,
        ))

    if not requested:
        return _render(scan_error="Add at least one page URL to scan.")
    if len(requested) > _MAX_PAGES:
        return _render(scan_error=f"Please scan at most {_MAX_PAGES} pages at a time "
                                  f"(you listed {len(requested)}). Trim the list and re-run.")

    try:
        scan = a11y_scanner.scan_pages(requested)
    except Exception:
        _log.exception("accessibility scan crashed [%s]", slug)
        return _render(scan_error="The scan crashed unexpectedly. Please try again.")

    if not scan.get("available"):
        return _render(scan_error=scan.get("error") or "A browser was not available to run the scan.")

    agg = a11y_scanner.aggregate(scan)
    audit_log.record(
        action="accessibility.scan_ran",
        actor_email=user.email if user else None,
        detail={"client_slug": slug, "pages": agg["pages_scanned"],
                "violations": agg["total_violations"]},
        **audit_log.request_context(request),
    )
    return _render(scan=scan, agg=agg)
