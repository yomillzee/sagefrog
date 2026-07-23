"""Accessibility (ADA / WCAG) scoping-audit routes.

    GET  /dashboard/{slug}/accessibility          the audit page (HTML); ?run=<id>
                                                   reopens a stored run, else the latest
    POST /dashboard/{slug}/accessibility/scan      run an on-demand axe-core scan
    GET  /dashboard/{slug}/accessibility/export     download the issues (csv | json)
    GET  /dashboard/{slug}/accessibility/status     latest-run summary (for the card pill)

The scan is synchronous (Playwright's *sync* API, which FastAPI runs in its worker
threadpool for a plain ``def`` handler, so a blocking scan doesn't stall the event
loop). When ``DATABASE_URL`` is set, each finished audit is **persisted** so the
report can be reopened from the Insights page and exported later; without a DB the
route degrades to the old stateless behaviour (renders the report inline once).

Pages are seeded from the client's Consent-scan config when present, so a client
that's already set up for consent scanning needs no extra configuration to audit.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import audit_log
import web_auth
import a11y_scanner
import a11y_store
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


def _worst_impact(agg: dict) -> str | None:
    by = agg.get("totals_by_impact") or {}
    for impact in a11y_scanner.IMPACT_ORDER:
        if by.get(impact):
            return impact
    return None


def _page_url(slug: str, *, access_key: str | None, use_session: bool, run: int | None = None) -> str:
    base = f"/dashboard/{slug}/accessibility"
    params = []
    if run:
        params.append(f"run={run}")
    if not use_session and access_key:
        params.append(f"key={quote(access_key, safe='')}")
    return base + ("?" + "&".join(params) if params else "")


def _export_url(slug: str, *, access_key: str | None, use_session: bool, run: int, fmt: str) -> str:
    params = [f"run={run}", f"format={fmt}"]
    if not use_session and access_key:
        params.append(f"key={quote(access_key, safe='')}")
    return f"/dashboard/{slug}/accessibility/export?" + "&".join(params)


def _when(dt: datetime | None) -> str:
    return dt.strftime("%b %-d, %Y %-I:%M %p UTC") if dt else ""


def _history_view(slug: str, *, access_key: str | None, use_session: bool,
                  current_id: int | None) -> list[dict]:
    out = []
    for r in a11y_store.list_runs(slug, limit=20):
        out.append({
            "id": r.id, "when": _when(r.created_at), "band": r.band,
            "total_nodes": r.total_nodes, "total_violations": r.total_violations,
            "root_cause_count": r.root_cause_count, "by": r.created_by or "",
            "href": _page_url(slug, access_key=access_key, use_session=use_session, run=r.id),
            "is_current": (r.id == current_id),
        })
    return out


@router.get("/dashboard/{client_slug}/accessibility", response_class=HTMLResponse)
def accessibility_page(client_slug: str, request: Request, run: int | None = None):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    user = auth.user

    stored = None
    if a11y_store.enabled():
        stored = a11y_store.get_run(run) if run else a11y_store.latest_run(slug)

    scan = agg = run_meta = None
    submitted_urls = None
    export_csv_url = export_json_url = None
    if stored and stored.result:
        scan = stored.result
        agg = stored.summary or a11y_scanner.aggregate(scan)
        submitted_urls = stored.urls or None
        run_meta = {"id": stored.id, "when": _when(stored.created_at),
                    "by": stored.created_by or "", "band": stored.band}
        export_csv_url = _export_url(slug, access_key=auth.access_key,
                                     use_session=auth.use_session, run=stored.id, fmt="csv")
        export_json_url = _export_url(slug, access_key=auth.access_key,
                                      use_session=auth.use_session, run=stored.id, fmt="json")

    return HTMLResponse(render_accessibility_page(
        client_slug=slug,
        label=_label_for(slug),
        access_key=auth.access_key,
        use_session=auth.use_session,
        session_email=user.email if user else None,
        session_is_admin=bool(user and user.role == "admin"),
        default_urls=_seed_urls(slug),
        submitted_urls=submitted_urls,
        scan=scan, agg=agg, run_meta=run_meta,
        history=_history_view(slug, access_key=auth.access_key, use_session=auth.use_session,
                              current_id=stored.id if stored else None),
        export_csv_url=export_csv_url, export_json_url=export_json_url,
    ))


@router.post("/dashboard/{client_slug}/accessibility/scan")
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

    def _render_inline(*, scan=None, agg=None, scan_error=None) -> HTMLResponse:
        return HTMLResponse(render_accessibility_page(
            client_slug=slug, label=label,
            access_key=auth.access_key, use_session=auth.use_session,
            session_email=user.email if user else None, session_is_admin=is_admin,
            default_urls=_seed_urls(slug), submitted_urls=requested,
            scan=scan, agg=agg, scan_error=scan_error,
            history=_history_view(slug, access_key=auth.access_key,
                                  use_session=auth.use_session, current_id=None),
        ))

    if not requested:
        return _render_inline(scan_error="Add at least one page URL to scan.")
    if len(requested) > _MAX_PAGES:
        return _render_inline(scan_error=f"Please scan at most {_MAX_PAGES} pages at a time "
                                         f"(you listed {len(requested)}). Trim the list and re-run.")

    try:
        scan = a11y_scanner.scan_pages(requested)
    except Exception:
        _log.exception("accessibility scan crashed [%s]", slug)
        return _render_inline(scan_error="The scan crashed unexpectedly. Please try again.")

    if not scan.get("available"):
        return _render_inline(scan_error=scan.get("error") or "A browser was not available to run the scan.")

    agg = a11y_scanner.aggregate(scan)
    clusters = a11y_scanner.cluster_components(scan)
    band = a11y_scanner.size_band(agg)
    audit_log.record(
        action="accessibility.scan_ran",
        actor_email=user.email if user else None,
        detail={"client_slug": slug, "pages": agg["pages_scanned"],
                "violations": agg["total_violations"]},
        **audit_log.request_context(request),
    )

    # Persist and redirect (Post/Redirect/Get) so the report lives at a stable URL
    # and a refresh doesn't re-scan. Without a DB, fall back to rendering inline.
    if a11y_store.enabled():
        run_id = a11y_store.save_run(
            slug, trigger="manual", created_by=user.email if user else None,
            urls=requested, band=band, worst_impact=_worst_impact(agg),
            root_cause_count=len(clusters), summary=agg, result=scan,
        )
        if run_id:
            return RedirectResponse(
                url=_page_url(slug, access_key=auth.access_key, use_session=auth.use_session, run=run_id),
                status_code=303,
            )
    return _render_inline(scan=scan, agg=agg)


@router.get("/dashboard/{client_slug}/accessibility/export")
def accessibility_export(client_slug: str, request: Request, run: int | None = None, format: str = "csv"):
    slug = validate_client_slug(client_slug)
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    if not a11y_store.enabled():
        raise HTTPException(status_code=404, detail="No stored audits to export.")
    stored = a11y_store.get_run(run) if run else a11y_store.latest_run(slug)
    if not stored or not stored.result:
        raise HTTPException(status_code=404, detail="Audit not found.")

    date = stored.created_at.strftime("%Y%m%d") if stored.created_at else "audit"
    fmt = (format or "csv").lower()
    if fmt == "json":
        import json
        body = json.dumps({"client_slug": slug, "run_id": stored.id,
                           "summary": stored.summary, "scan": stored.result}, indent=2)
        return Response(
            content=body, media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="a11y-{slug}-{date}.json"'},
        )
    csv_text = a11y_scanner.issues_csv(stored.result)
    return Response(
        content=csv_text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="a11y-{slug}-{date}.csv"'},
    )


@router.get("/dashboard/{client_slug}/accessibility/status")
def accessibility_status(client_slug: str, request: Request):
    """Latest-run summary for the Insights-page card pill."""
    slug = validate_client_slug(client_slug)
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    if not a11y_store.enabled():
        return JSONResponse({"status": "none"})
    r = a11y_store.latest_run(slug, include_result=True)
    if not r:
        return JSONResponse({"status": "none"})
    aa = {"issues": 0, "elements": 0}
    if r.result:
        aa = a11y_scanner.conformance_summary(r.result)["to_reach"].get("AA", aa)
    return JSONResponse({
        "status": r.status, "band": r.band,
        "total_violations": r.total_violations, "total_nodes": r.total_nodes,
        "root_cause_count": r.root_cause_count,
        "aa_issues": aa["issues"], "aa_elements": aa["elements"],
        "when": _when(r.created_at),
    })
