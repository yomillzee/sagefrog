from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

import dashboard_snapshots
from admin import register_admin_routes
from admin.shared import AVATAR_MAX_CHARS as _AVATAR_MAX_CHARS
from dashboard.routes import register_dashboard_routes
from platforms import register_platform_routes
import db_cache
import warehouse
from security import configured_api_key, is_production, require_api_key
import audit_log
import feature_requests
import client_dashboard_config
import dashboard_registry
import business_line_rules
import client_insight_documents
import dashboard_settings
import ga4_credentials
import railway_api
import login_rate_limit
import not_found_page
import connector_config_store
import oauth_flows
import oauth_store
import user_invites
import web_auth
import web_security
import web_users
from models import (
    CacheHealthResponse,
    HealthResponse,
    HealthReadyResponse,
)


load_dotenv()

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Give the app's loggers somewhere to write.

    Every module here does ``logging.getLogger(__name__)``, but nothing ever
    configured the root logger. Uvicorn only sets up its own ``uvicorn.*``
    loggers, so ours inherited the default level of WARNING with no handler
    attached — which silently discarded every ``log.info`` and ``log.debug`` in
    the codebase (133 call sites: sync progress, GTM quota decisions, cache
    hits). Warnings and errors made it out only via logging's last-resort
    handler, without timestamps.

    LOG_LEVEL overrides the default; set it to DEBUG when chasing something.
    ``force=True`` because a dependency importing logging first would otherwise
    win and this call would quietly do nothing.
    """
    level = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        force=True,
    )
    # Turning INFO on globally also turns it on for our dependencies, and a few
    # of them narrate every HTTP call — httpx alone would log a line per request
    # to every Google/LinkedIn/Meta endpoint, burying the app's own output. Hold
    # those at WARNING; LOG_LEVEL=DEBUG still overrides everything below.
    if level != "DEBUG":
        for noisy in (
            "httpx", "httpcore", "urllib3", "asyncio", "charset_normalizer",
            "google", "google.auth", "google.api_core", "google_auth_httplib2",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()


def _production_hide_api_docs() -> bool:
    """Hide Swagger/OpenAPI UI on Railway unless DISABLE_API_DOCS=0."""
    raw = (os.getenv("DISABLE_API_DOCS") or "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return bool(
        (os.getenv("RAILWAY_ENVIRONMENT") or "").strip()
        or (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    )


_hide_api_docs = _production_hide_api_docs()


def _require_api_key_configured() -> None:
    """Refuse to start in production when API_KEY is not set.

    Without API_KEY every platform endpoint (/google-ads/*, /linkedin/*, /meta/*,
    /ga4/*, /indeed/*, /warehouse/*) is publicly accessible. Fail hard here rather
    than silently exposing live marketing data.
    """
    if is_production() and not configured_api_key():
        import sys

        print(
            "FATAL: API_KEY is not set. All platform API routes (/google-ads/*, /linkedin/*, "
            "/meta/*, /ga4/*, /indeed/*, /warehouse/*) would be publicly accessible. "
            "Set API_KEY in Railway environment variables and redeploy.",
            file=sys.stderr,
        )
        sys.exit(1)


_require_api_key_configured()

app = FastAPI(
    title="EOS Ads + GA4 Service",
    version="0.2.0",
    description=(
        "All /google-ads/*, /linkedin/*, /meta/*, /ga4/*, /indeed/*, and /warehouse/* routes "
        "require Authorization: Bearer <API_KEY> or header X-API-Key: <API_KEY>. "
        "GET /health stays public for load balancers."
    ),
    docs_url=None if _hide_api_docs else "/docs",
    redoc_url=None if _hide_api_docs else "/redoc",
    openapi_url=None if _hide_api_docs else "/openapi.json",
)

try:
    # Central, versioned migration runner (Phase 0/1). Applies registered
    # baselines (currently web_users) once, recorded in schema_migrations, under a
    # shared advisory lock. Runs alongside the per-module ensure_schema() calls
    # below, which stay in place until the runner is proven.
    import db_migrate
    # Imported for its side effect: registers the schema teardown for features
    # that have been removed, so their tables/columns are dropped by the run
    # below. Must be imported before run_migrations().
    import retired_features  # noqa: F401
    db_migrate.run_migrations()
    db_cache.ensure_schema()
    warehouse.ensure_schema()
    dashboard_snapshots.ensure_schema()
    web_users.ensure_schema()
    audit_log.ensure_schema()
    client_dashboard_config.ensure_schema()
    dashboard_registry.ensure_schema()
    # Grandfather existing 'standard' users to every current client so the switch
    # to per-client scoping doesn't lock them out; runs once per user (IS NULL).
    try:
        _granted = web_users.backfill_standard_all_access(dashboard_registry.list_slugs())
        if _granted:
            print(f"Startup: granted all-client access to {_granted} pre-scoping standard user(s).")
    except Exception as _bf_exc:
        import sys as _sys
        print(f"WARNING: standard-user access backfill failed: {_bf_exc}", file=_sys.stderr)
    # One-time backfill: segment_filter_profile replaced runtime slug/label
    # inference for the campaign/segment filters. Seed the clients that predate
    # the column (only where unset) so their filters keep working; new clients
    # set this from Settings. Idempotent — safe to run every boot.
    try:
        for _seed_slug in ("nixon", "nixon-bq-test"):
            client_dashboard_config.backfill_segment_filter_profile(_seed_slug, "regions")
            # Also persist Nixon's mart destination onto its config row so HQ /
            # agency reads resolve it from config instead of a client-name fallback.
            client_dashboard_config.backfill_marketing_mart_destination(
                _seed_slug, "nixon-medical", "marketing_marts"
            )
    except Exception as _seg_exc:
        import sys as _sys
        print(f"WARNING: client config backfill failed: {_seg_exc}", file=_sys.stderr)
    # Built-in demo client: an always-available dashboard populated entirely
    # with synthetic sample data (no GCP project / connectors / live data), for
    # pitching prospects, walking clients through the portal, and training
    # staff. Idempotent; opt out with DEMO_CLIENT_ENABLED=0. Never fatal.
    try:
        import demo_client
        if demo_client.seed_demo_client():
            print(f"Startup: demo client '{demo_client.DEMO_SLUG}' ready.")
    except Exception as _demo_exc:
        import sys as _sys
        print(f"WARNING: demo client seed failed: {_demo_exc}", file=_sys.stderr)
    business_line_rules.ensure_schema()
    client_insight_documents.ensure_schema()
    oauth_store.ensure_schema()
    connector_config_store.ensure_schema()
    # Close out any sync-run left 'running' by a redeploy/crash mid-sync — the
    # BackgroundTask that would have finished it died with the old process, so
    # without this the connector stays stuck showing 'syncing' forever.
    try:
        _orphan_min = int((os.getenv("CONNECTOR_SYNC_ORPHAN_MINUTES") or "0").strip() or "0")
    except ValueError:
        _orphan_min = 0
    _orphaned = connector_config_store.fail_orphaned_sync_runs(older_than_minutes=_orphan_min)
    if _orphaned:
        print(f"Startup: closed {_orphaned} orphaned connector sync run(s).")
    login_rate_limit.ensure_schema()
    try:
        import gtm_quota
        gtm_quota.ensure_schema()
    except Exception as _gtm_quota_exc:
        import sys as _sys
        print(f"WARNING: GTM quota schema init failed: {_gtm_quota_exc}", file=_sys.stderr)
    boot = web_users.bootstrap_admin_from_env()
    if boot:
        audit_log.record(
            action="user.bootstrap_admin",
            subject_email=boot.email,
            detail={"source": "AUTH_BOOTSTRAP_ADMIN_*"},
        )
except Exception as _boot_exc:
    import sys as _sys
    print(f"WARNING: DB schema/bootstrap error at startup: {_boot_exc}", file=_sys.stderr)

@app.middleware("http")
async def _inject_html_extras(request: Request, call_next):
    """Rewrite HTML responses to add the impersonation bar and CSRF plumbing.

    Registered before the session middleware so, in Starlette's stack, the
    session middleware ends up outermost and request.session is populated by
    the time this runs. Doing it here (rather than in each renderer) keeps the
    exit affordance and CSRF token on every page — dashboard, settings, files,
    connectors, the dashboards picker — with no per-renderer plumbing.

    Seeding the CSRF token only on HTML responses confines the session cookie
    to browser page loads; the mutation lands in request.session before the
    outer SessionMiddleware serializes the cookie on the way out.
    """
    response = await call_next(request)
    if "text/html" not in (response.headers.get("content-type") or "").lower():
        return response
    try:
        banner = web_auth.impersonation_banner_html(request)
    except Exception:
        banner = ""
    csrf_token: str | None = None
    if "session" in request.scope:
        try:
            csrf_token = web_security.ensure_csrf_token(request.session)
        except Exception:
            csrf_token = None
    if not banner and not csrf_token:
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    text = body.decode(response.charset or "utf-8")
    text = web_security.assemble_page_html(text, banner=banner, csrf_token=csrf_token)
    headers = {
        k: v
        for k, v in response.headers.items()
        if k.lower() not in ("content-length", "content-encoding")
    }
    return Response(
        content=text,
        status_code=response.status_code,
        headers=headers,
        media_type="text/html",
    )


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach a framework-safe set of security headers to every response."""
    response = await call_next(request)
    https = request.url.scheme == "https" or is_production()
    web_security.apply_security_headers(response, https=https)
    return response


@app.middleware("http")
async def _track_last_seen(request: Request, call_next):
    """Stamp the signed-in user's last-activity time (throttled, best-effort).

    Long-lived sessions mean last_login_at alone can't show whether a user has
    returned since; this keeps last_seen_at current from any authenticated
    request. touch_last_seen throttles the write and swallows its own errors, so
    this never blocks or fails the request. Runs inner to the session
    middleware, so request.session is populated and the throttle marker it sets
    is serialized back into the cookie on the way out.
    """
    response = await call_next(request)
    web_auth.touch_last_seen(request)
    return response


@app.middleware("http")
async def _csrf_protect(request: Request, call_next):
    """Reject cookie-authenticated state changes that lack a valid CSRF token."""
    if web_security.requires_csrf(request):
        if not await web_security.validate_csrf(request):
            return Response("CSRF verification failed.", status_code=403)
    return await call_next(request)


# Compress responses on the way out. Added before the session middleware so it
# ends up outermost and compresses whatever the inner layers produced.
#
# The dashboard page itself is no longer the main beneficiary — moving its CSS
# and JS into cached assets took it from 584 KB to 96 KB — but the JSON the
# explorer and the analytics panes fetch is still sizeable and highly
# compressible, as are the asset responses themselves on a cold cache.
# minimum_size skips the small JSON replies where a compression pass costs more
# than it saves.
app.add_middleware(GZipMiddleware, minimum_size=1000)

if web_users.enabled():
    try:
        web_auth.add_session_middleware(app)
    except RuntimeError as _sess_exc:
        # No session signing secret: browser login is disabled. Surface it loudly
        # rather than silently — in production this means AUTH_SESSION_SECRET is
        # unset (it no longer falls back to CRON_SECRET / API_KEY).
        import sys as _sys
        print(f"WARNING: session auth disabled — {_sess_exc}", file=_sys.stderr)

register_dashboard_routes(app)
register_admin_routes(app)
register_platform_routes(app)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_HTML_404_API_PREFIXES = (
    "/google-ads",
    "/linkedin",
    "/meta",
    "/ga4",
    "/indeed",
    "/warehouse",
    "/openapi",
    "/health",
    "/cron",
)


def _request_wants_html_error(request: Request) -> bool:
    """True for a top-level browser navigation (as opposed to an API/XHR call).

    Used to decide whether an error should render as a friendly HTML page or a
    JSON body: API prefixes and JSON-only Accept headers stay JSON.
    """
    path = request.url.path
    if any(path.startswith(prefix) for prefix in _HTML_404_API_PREFIXES):
        return False
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return False
    return True


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> HTMLResponse | JSONResponse:
    # Stays async deliberately: the error pages are built from strings
    # (not_found_page imports nothing but `html`), so there is no blocking work
    # here to move off the loop.
    detail = exc.detail
    if not isinstance(detail, str):
        detail = str(detail)
    if _request_wants_html_error(request):
        if exc.status_code == 404:
            return HTMLResponse(
                not_found_page.render_not_found_page(path=request.url.path),
                status_code=404,
            )
        # A browser navigation that 401/403s (e.g. an admin stuck impersonating
        # a user without access to this page) must not dead-end on a JSON blob:
        # render a navigable HTML page so the injected "Exit view as" banner and
        # the back-links give a way out.
        if exc.status_code in (401, 403):
            return HTMLResponse(
                not_found_page.render_error_page(status_code=exc.status_code, detail=detail),
                status_code=exc.status_code,
            )
    return JSONResponse({"detail": detail}, status_code=exc.status_code)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["BearerAuth"] = {"type": "http", "scheme": "bearer", "description": "Same value as Railway `API_KEY`."}
    schemes["ApiKeyHeader"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Same value as Railway `API_KEY`.",
    }
    for path, item in schema.get("paths", {}).items():
        if not (
            path.startswith(("/google-ads", "/linkedin", "/meta", "/ga4", "/indeed", "/warehouse"))
        ):
            continue
        for method in ("get", "post", "put", "delete", "patch"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            # Either Bearer or X-API-Key (OpenAPI: alternatives are OR).
            op["security"] = [{"BearerAuth": []}, {"ApiKeyHeader": []}]
    # Advertise a root-level `servers` URL (FastAPI omits it by default) so the
    # published schema resolves absolute paths for external API clients.
    base_url = (
        os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        or "https://sagefrog-production.up.railway.app"
    )
    schema["servers"] = [{"url": base_url}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/")
def root() -> dict:
    out = {
        "service": "EOS Ads + GA4 Service",
        "health": "/health",
        "google_ads_test_token": "/google-ads/test-token",
        "youtube_videos": "/google-ads/youtube-videos",
        "linkedin_env": "/linkedin/env",
        "linkedin_test_token": "/linkedin/test-token",
        "linkedin_accounts": "/linkedin/accounts",
        "linkedin_performance": "/linkedin/performance",
        "linkedin_campaign_groups": "/linkedin/campaign-groups",
        "linkedin_campaign_groups_performance": "/linkedin/campaign-groups/performance",
        "linkedin_creatives_performance": "/linkedin/creatives/performance",
        "linkedin_videos": "/linkedin/videos",
        "linkedin_warehouse_sync": "/linkedin/warehouse/sync",
        "meta_env": "/meta/env",
        "meta_test_token": "/meta/test-token",
        "meta_test_ads_access": "/meta/test-ads-access",
        "meta_accounts": "/meta/accounts",
        "meta_performance": "/meta/performance",
        "meta_adsets_performance": "/meta/adsets/performance",
        "meta_videos": "/meta/videos",
        "meta_warehouse_sync": "/meta/warehouse/sync",
        "indeed_env": "/indeed/env",
        "indeed_test_token": "/indeed/test-token",
        "indeed_job_postings": "/indeed/postings",
        "indeed_job_posting_detail": "/indeed/postings/{posting_id}",
        "indeed_registration_analytics": "/indeed/analytics",
        "google_ads_warehouse_sync": "/google-ads/warehouse/sync",
        "ga4_warehouse_sync": "/ga4/warehouse/sync",
        "warehouse_status": "/warehouse/status",
        "warehouse_metrics": "/warehouse/metrics",
        "login": "/login",
        "admin": "/admin",
        "dashboard_client_settings": "/dashboard/{client_slug}/settings",
        "ga4_env": "/ga4/env",
    }
    if not _hide_api_docs:
        out["docs"] = "/docs"
    return out


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/health/ready", response_model=HealthReadyResponse)
def health_ready(response: Response) -> HealthReadyResponse:
    """Readiness probe: verifies DB connectivity so a broken deploy is not
    reported healthy. `/health` stays liveness-only."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        response.status_code = 503
        return HealthReadyResponse(status="error", database=False, detail="DATABASE_URL is not set.")
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return HealthReadyResponse(status="ok", database=True)
    except Exception as exc:
        response.status_code = 503
        return HealthReadyResponse(status="error", database=False, detail=str(exc)[:200])


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    path = STATIC_DIR / "favicon.ico"
    if not path.is_file():
        path = STATIC_DIR / "favicon-32x32.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Favicon not found")
    media_type = "image/x-icon" if path.suffix == ".ico" else "image/png"
    return FileResponse(path, media_type=media_type)


@app.get(
    "/cache/health",
    response_model=CacheHealthResponse,
    dependencies=[Depends(require_api_key)],
)
def cache_health() -> CacheHealthResponse:
    return CacheHealthResponse(**db_cache.status())


# ============================================================================
# INDEED ENDPOINTS
# ============================================================================


# ============================================================================
# GOOGLE ADS ENDPOINTS
# ============================================================================

@app.get("/login", include_in_schema=False, response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None, error: str | None = None) -> HTMLResponse:
    if not web_users.enabled():
        raise HTTPException(
            status_code=503,
            detail="User login requires DATABASE_URL (Postgres).",
        )
    existing = web_auth.get_current_user(request)
    if existing:
        return RedirectResponse(url=_post_login_target(existing, next), status_code=303)
    target = oauth_flows.validate_return_to(next)
    ctx = audit_log.request_context(request)
    rl = login_rate_limit.check_login_allowed(ip=ctx.get("ip_address"))
    if not rl.allowed:
        return HTMLResponse(
            web_auth.render_login_page(error=rl.message or error, next_path=target),
            status_code=429,
        )
    return HTMLResponse(web_auth.render_login_page(error=error, next_path=target))


@app.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
):
    if not web_users.enabled():
        raise HTTPException(status_code=503, detail="User login requires Postgres.")
    ctx = audit_log.request_context(request)
    rl = login_rate_limit.check_login_allowed(ip=ctx.get("ip_address"), email=email)
    if not rl.allowed:
        return HTMLResponse(
            web_auth.render_login_page(error=rl.message, next_path=next),
            status_code=429,
        )
    user = web_users.authenticate(email, password)
    if not user:
        login_rate_limit.record_login_failure(ip=ctx.get("ip_address"), email=email)
        audit_log.record(
            action="login.failed",
            actor_email=email,
            subject_email=email,
            detail={"reason": "invalid credentials"},
            **ctx,
        )
        return HTMLResponse(
            web_auth.render_login_page(error="Invalid email or password.", next_path=next),
            status_code=401,
        )
    login_rate_limit.clear_login_limits(ip=ctx.get("ip_address"), email=email)
    web_auth.login_user(request, user)
    web_users.record_login(user.id)
    audit_log.record(
        action="login.success",
        actor_user_id=user.id,
        actor_email=user.email,
        detail={"role": user.role, "client_slug": user.client_slug},
        **ctx,
    )
    target = _post_login_target(user, next)
    return RedirectResponse(url=target, status_code=303)


def _invite_expires_in() -> str:
    """Human phrasing for how long a freshly minted invite link lasts.

    Read off the configured TTL rather than a timestamp: this is only ever
    shown next to a link created moments earlier, so "3 days" is both accurate
    and easier to act on than a wall-clock time in the admin's local zone."""
    hours = user_invites.ttl_hours()
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        return f"{days} day" if days == 1 else f"{days} days"
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


@app.get("/invite/{token}", include_in_schema=False, response_class=HTMLResponse)
def invite_page(request: Request, token: str) -> HTMLResponse:
    """Landing page for an invite link: pick a password, or a dead end.

    Every invalid token renders the same "expired" page, so this can't be used
    to probe which tokens exist. Signing the visitor out first keeps a shared
    machine from silently redeeming the invite into somebody else's session.
    """
    if not web_users.enabled():
        raise HTTPException(status_code=503, detail="User login requires Postgres.")
    ctx = audit_log.request_context(request)
    rl = login_rate_limit.check_login_allowed(ip=ctx.get("ip_address"))
    if not rl.allowed:
        return HTMLResponse(
            web_auth.render_login_page(error=rl.message, next_path="/dashboards"),
            status_code=429,
        )
    invite = user_invites.resolve_invite(token)
    if not invite:
        return HTMLResponse(
            web_auth.render_invite_page(token=token, email="", expired=True), status_code=410
        )
    web_auth.logout_user(request)
    return HTMLResponse(web_auth.render_invite_page(token=token, email=invite.email))


@app.post("/invite/{token}", include_in_schema=False)
def invite_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    confirm: str = Form(""),
):
    """Redeem an invite: set the password, burn the token, sign the user in."""
    if not web_users.enabled():
        raise HTTPException(status_code=503, detail="User login requires Postgres.")
    ctx = audit_log.request_context(request)
    # Share the login limiter so a token guesser hits the same wall a password
    # guesser does. The token is the credential here, so failures count.
    rl = login_rate_limit.check_login_allowed(ip=ctx.get("ip_address"))
    if not rl.allowed:
        return HTMLResponse(
            web_auth.render_login_page(error=rl.message, next_path="/dashboards"),
            status_code=429,
        )
    invite = user_invites.resolve_invite(token)
    if not invite:
        login_rate_limit.record_login_failure(ip=ctx.get("ip_address"))
        audit_log.record(action="user.invite_rejected", detail={"reason": "invalid token"}, **ctx)
        return HTMLResponse(
            web_auth.render_invite_page(token=token, email="", expired=True), status_code=410
        )
    if confirm and password != confirm:
        return HTMLResponse(
            web_auth.render_invite_page(
                token=token, email=invite.email, error="Those passwords don't match."
            ),
            status_code=400,
        )
    try:
        accepted = user_invites.accept_invite(token, password)
    except ValueError as exc:
        return HTMLResponse(
            web_auth.render_invite_page(token=token, email=invite.email, error=str(exc)),
            status_code=400,
        )
    if not accepted:
        # Lost the race against a concurrent redemption of the same link.
        return HTMLResponse(
            web_auth.render_invite_page(token=token, email="", expired=True), status_code=410
        )
    user = web_users.get_user_by_id(accepted.user_id)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    login_rate_limit.clear_login_limits(ip=ctx.get("ip_address"), email=user.email)
    web_auth.login_user(request, user)
    web_users.record_login(user.id)
    audit_log.record(
        action="user.invite_accepted",
        actor_user_id=user.id,
        actor_email=user.email,
        subject_email=user.email,
        detail={"role": user.role},
        **ctx,
    )
    return RedirectResponse(url=_post_login_target(user, "/dashboards"), status_code=303)


@app.post("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    # Attribute the logout to the real account, not any user being viewed-as.
    user = web_auth.get_real_user(request)
    ctx = audit_log.request_context(request)
    if user:
        audit_log.record(
            action="logout",
            actor_user_id=user.id,
            actor_email=user.email,
            **ctx,
        )
    web_auth.logout_user(request)
    return RedirectResponse(url="/login", status_code=303)


@app.post("/admin/view-as", include_in_schema=False)
def admin_view_as_start(
    request: Request,
    user_id: int = Form(...),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
) -> RedirectResponse:
    """Admin: begin viewing the platform as another registered user."""
    target = web_users.get_user_record(int(user_id))
    ctx = audit_log.request_context(request)
    if not target or not target.is_active:
        return RedirectResponse(url="/dashboards", status_code=303)
    if target.id == admin.id:
        # Viewing as yourself is a no-op; just clear any prior impersonation.
        web_auth.clear_view_as(request)
        return RedirectResponse(url="/dashboards", status_code=303)
    web_auth.set_view_as(request, target.id)
    audit_log.record(
        action="admin.view_as.start",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=target.email,
        detail={"target_role": target.role, "target_slug": target.client_slug},
        **ctx,
    )
    # Land where the target user lands after login (their dashboards picker /
    # single dashboard) so the admin sees exactly that user's entry point.
    return RedirectResponse(url="/dashboards", status_code=303)


@app.post("/admin/view-as/exit", include_in_schema=False)
def admin_view_as_exit(request: Request) -> RedirectResponse:
    """Leave "view as" and return to the real admin account."""
    real = web_auth.get_real_user(request)
    target = web_auth.current_view_as(request)
    web_auth.clear_view_as(request)
    if real and target:
        audit_log.record(
            action="admin.view_as.stop",
            actor_user_id=real.id,
            actor_email=real.email,
            subject_email=target.email,
            **audit_log.request_context(request),
        )
    return RedirectResponse(url="/dashboards", status_code=303)


def _gcp_credentials_section_html() -> str:
    # One shared service account (marketing-data-reader@sagefrog.iam.gserviceaccount.com)
    # is granted BigQuery access on every client's GCP project via IAM, so there is
    # exactly one credential to manage. No per-client override — if a client ever
    # genuinely needs an isolated service account, set that env var directly in
    # Railway and pass credentials_env explicitly wherever that client's data is read.
    summary = railway_api.env_summary()
    if summary["ready"]:
        status = (
            '<p class="muted">Railway API connected. Uploading a key sets the '
            "variable and redeploys this service.</p>"
        )
        disabled = ""
    else:
        missing = [
            env
            for env, present in (
                ("RAILWAY_API_TOKEN", summary["has_token"]),
                ("RAILWAY_PROJECT_ID", summary["has_project_id"]),
                ("RAILWAY_ENVIRONMENT_ID", summary["has_environment_id"]),
                ("RAILWAY_SERVICE_ID", summary["has_service_id"]),
            )
            if not present
        ]
        status = (
            '<div class="notice err">Railway API not configured. Set these Railway '
            f'variables on this service, then reload: <span class="mono">{", ".join(missing)}</span></div>'
        )
        disabled = " disabled"
    return f"""
    <section>
      <h2>GCP service account credentials</h2>
      <p class="muted">Upload the shared agency service account's JSON key
      (<span class="mono">{ga4_credentials.GLOBAL_GCP_CREDENTIALS_ENV}</span>). It's validated,
      base64-encoded, and written to Railway. Railway then redeploys this service so the
      credential goes live (~1–2 min).</p>
      {status}
      <form method="post" action="/admin/gcp-credentials" enctype="multipart/form-data"
        onsubmit="return confirm('Set {ga4_credentials.GLOBAL_GCP_CREDENTIALS_ENV} on Railway? The service will redeploy.');">
        <label for="cred_file">Service account JSON</label>
        <input id="cred_file" name="credentials_file" type="file"
          accept="application/json,.json" required>
        <button type="submit" class="primary"{disabled}>Upload &amp; set credential</button>
      </form>
    </section>"""


def _post_login_target(user: web_users.WebUser, next_value: str | None) -> str:
    """Where to send a user after login. Non-admins can't use /admin (the default
    `next`), so route them to the dashboards picker instead of a 403 dead-end."""
    target = oauth_flows.validate_return_to(next_value)
    if user.role != "admin" and target == "/admin":
        return "/dashboards"
    return target


@app.get("/dashboards", include_in_schema=False, response_class=HTMLResponse)
def dashboards_home(request: Request):
    """Landing page listing the client dashboards the signed-in user can open."""
    if not web_users.enabled():
        raise HTTPException(
            status_code=503, detail="User login requires DATABASE_URL (Postgres)."
        )
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/dashboards")
    import dashboard_registry

    items = [
        (row.client_slug, row.label or row.client_slug)
        # Slugs and labels only — no need to drag every client's logo data URI
        # out of Postgres to render a list of links.
        for row in dashboard_registry.list_clients(with_logos=False)
        if user.can_access_client(row.client_slug)
    ]
    # A client user tied to a single dashboard: skip the picker, go straight in.
    if user.role == "client" and len(items) == 1:
        return RedirectResponse(url=f"/dashboard/{items[0][0]}", status_code=303)
    return HTMLResponse(web_auth.render_dashboards_page(user=user, dashboards=items))


def _render_admin_page(
    request: Request,
    *,
    page: str,
    msg: str | None = None,
    err: str | None = None,
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
    oauth_disconnected: str | None = None,
):
    """Shared renderer for the split-out admin destinations (Clients, Users,
    Feature requests, Advanced settings). Each lives at its own /admin/* route
    but shares auth, data loading, and the navy-sidebar shell."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path=f"/admin/{page}")
    if user.role != "admin":
        # Logged in but not an admin: 403, never bounce back to /login. The
        # login page redirects an already-authenticated user straight to `next`,
        # so redirecting here would ping-pong /admin <-> /login forever.
        raise HTTPException(status_code=403, detail="Admin access required.")
    users = web_users.list_users(include_inactive=False)
    events = audit_log.list_recent(limit=40)
    # OAuth + GCP credentials only surface on the Advanced page, so only build
    # those (and read their return URL) there.
    oauth_html = ""
    credentials_html = ""
    if page == "advanced":
        oauth_html = dashboard_settings.render_admin_oauth_section(
            return_url="/admin/advanced",
            oauth_connected=oauth_connected,
            oauth_error=(oauth_error or "").strip()[:300] or None,
        )
        credentials_html = _gcp_credentials_section_html()
    flash = msg
    if oauth_disconnected and not flash:
        labels = {"google_ads": "Google Ads", "linkedin": "LinkedIn", "meta": "Meta", "indeed": "Indeed", "harvest": "Harvest"}
        flash = f"{labels.get(oauth_disconnected, oauth_disconnected)} disconnected."

    return HTMLResponse(
        web_auth.render_admin_page(
            user=user,
            users=users,
            groups=web_users.list_groups(include_inactive=False),
            audit_events=events,
            message=flash,
            error=err,
            oauth_section_html=oauth_html,
            credentials_section_html=credentials_html,
            is_super_admin=web_auth.is_super_admin(user),
            dashboard_cache_ttl=_dashboard_cache_ttl_seconds(),
            page=page,
        )
    )


@app.get("/admin", include_in_schema=False)
def admin_home(request: Request) -> RedirectResponse:
    """The old catch-all Overview has been split into focused pages. Land on
    Clients so bookmarks to /admin keep working."""
    return RedirectResponse(url="/admin/clients", status_code=307)


@app.get("/admin/clients", include_in_schema=False, response_class=HTMLResponse)
def admin_clients(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    return _render_admin_page(request, page="clients", msg=msg, err=err)


@app.get("/admin/users", include_in_schema=False, response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    return _render_admin_page(request, page="users", msg=msg, err=err)


@app.get("/admin/feature-requests", include_in_schema=False, response_class=HTMLResponse)
def admin_feature_requests_page(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    return _render_admin_page(request, page="feature-requests", msg=msg, err=err)


@app.get("/admin/advanced", include_in_schema=False, response_class=HTMLResponse)
def admin_advanced_page(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
    oauth_disconnected: str | None = None,
):
    return _render_admin_page(
        request,
        page="advanced",
        msg=msg,
        err=err,
        oauth_connected=oauth_connected,
        oauth_error=oauth_error,
        oauth_disconnected=oauth_disconnected,
    )


def _dashboard_cache_ttl_seconds() -> int:
    try:
        import app_settings

        return app_settings.dashboard_cache_ttl_seconds()
    except Exception:
        return 0


@app.post("/admin/settings/dashboard-cache", include_in_schema=False)
def admin_set_dashboard_cache_ttl(
    request: Request,
    ttl_seconds: int = Form(...),
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Persist the dashboard read-cache TTL floor (super admins only)."""
    import app_settings

    try:
        app_settings.set_dashboard_cache_ttl_seconds(int(ttl_seconds), updated_by=admin.email)
    except Exception:
        return RedirectResponse(
            url="/admin/advanced?err=Could+not+update+cache+setting", status_code=303
        )
    return RedirectResponse(
        url="/admin/advanced?msg=Dashboard+cache+duration+updated", status_code=303
    )


@app.post("/admin/feature-requests/{request_id}/done", include_in_schema=False)
def admin_feature_request_done(
    request_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Mark a team feature request handled — clears it from the notification badge."""
    try:
        feature_requests.mark_done(request_id, resolved_by=admin.email)
    except Exception:
        return RedirectResponse(
            url="/admin/feature-requests?err=Could+not+update+feature+request",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/feature-requests?msg=Feature+request+marked+done", status_code=303
    )


@app.post("/admin/feature-requests/{request_id}/archive", include_in_schema=False)
def admin_feature_request_archive(
    request_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Archive a team feature request — dismisses it from the inbox, keeps the row."""
    try:
        feature_requests.archive_request(request_id, archived_by=admin.email)
    except Exception:
        return RedirectResponse(
            url="/admin/feature-requests?err=Could+not+archive+feature+request",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/feature-requests?msg=Feature+request+archived", status_code=303
    )


@app.post("/admin/feature-requests/{request_id}/delete", include_in_schema=False)
def admin_feature_request_delete(
    request_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    """Permanently delete a team feature request — can't be undone."""
    try:
        feature_requests.delete_request(request_id)
    except Exception:
        return RedirectResponse(
            url="/admin/feature-requests?err=Could+not+delete+feature+request",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/feature-requests?msg=Feature+request+deleted", status_code=303
    )


# ---------------------------------------------------------------------------
# Client Hours share links: read-only, no-login views of the burn-up page.
# Admins mint/list/revoke unguessable tokens (…/share*); the public routes
# (/share/client-hours/{token}) render the same page read-only and serve a
# cache-only data feed so a leaked link can't force live Harvest pulls.
# ---------------------------------------------------------------------------


@app.get("/share/client-hours/{token}", include_in_schema=False, response_class=HTMLResponse)
def shared_client_hours(token: str):
    """Public, read-only Client Hours view for a valid share token — no login.
    An invalid or revoked token renders a plain 404 page."""
    import client_hours_share
    from dashboard.renderers.client_hours_renderer import (
        render_shared_client_hours_page,
    )

    link = client_hours_share.resolve_share_token(token)
    if not link:
        return HTMLResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Link unavailable</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:520px;margin:80px auto;"
            "padding:0 20px;color:#0f1c2e'>"
            "<h1 style='font-size:1.3rem'>This link isn’t available</h1>"
            "<p style='color:#5a6578'>The share link is invalid or has been revoked. "
            "Ask the person who shared it for a new one.</p></body></html>",
            status_code=404,
        )
    return HTMLResponse(
        render_shared_client_hours_page(token=link["token"], label=link.get("label"))
    )


@app.get("/share/client-hours/{token}/data", include_in_schema=False)
def shared_client_hours_data(token: str) -> JSONResponse:
    """Public JSON feed for a shared Client Hours view. Cache-only — never forces
    a live Harvest pull — so a leaked link can't be used to hammer the API."""
    import client_hours_share

    link = client_hours_share.resolve_share_token(token, record_view=False)
    if not link:
        return JSONResponse({"error": "This link is invalid or has been revoked."}, status_code=404)
    import harvest_service

    return JSONResponse(harvest_service.build_client_hours_overview(use_cache=True))


@app.get("/admin/docs", include_in_schema=False, response_class=HTMLResponse)
def admin_docs(request: Request):
    """Admin-only 'Docs': how to set up a new client dashboard, in the portal."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/docs")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.docs_renderer import render_docs_page

    return HTMLResponse(render_docs_page(user_email=user.email))


@app.get("/admin/changelog", include_in_schema=False, response_class=HTMLResponse)
def admin_changelog(request: Request):
    """Admin-only "What's New": every user-visible portal change, newest first."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/changelog")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.changelog_renderer import render_changelog_page

    return HTMLResponse(render_changelog_page(user_email=user.email))


@app.post("/admin/gcp-credentials", include_in_schema=False)
async def admin_set_gcp_credentials(
    request: Request,
    credentials_file: UploadFile = File(...),
):
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin")
    if user.role != "admin":
        # Logged in but not an admin: 403, never bounce back to /login. The
        # login page redirects an already-authenticated user straight to `next`,
        # so redirecting here would ping-pong /admin <-> /login forever.
        raise HTTPException(status_code=403, detail="Admin access required.")

    name = ga4_credentials.GLOBAL_GCP_CREDENTIALS_ENV
    if not railway_api.enabled():
        return RedirectResponse(
            url="/admin/advanced?err=" + quote(
                "Railway API is not configured "
                "(set RAILWAY_API_TOKEN / RAILWAY_PROJECT_ID / RAILWAY_ENVIRONMENT_ID / RAILWAY_SERVICE_ID)."
            ),
            status_code=303,
        )
    try:
        raw = (await credentials_file.read()).decode("utf-8")
        encoded, client_email = ga4_credentials.validate_and_encode_service_account(raw)
        railway_api.set_variable(name, encoded)
    except Exception as exc:
        return RedirectResponse(
            url="/admin/advanced?err=" + quote(f"Upload failed: {str(exc)[:200]}"),
            status_code=303,
        )

    audit_log.record(
        action="admin.gcp_credentials_set",
        actor_email=user.email,
        detail={"env_var": name, "service_account": client_email},
        **audit_log.request_context(request),
    )
    return RedirectResponse(
        url="/admin/advanced?msg=" + quote(
            f"Set {name} for {client_email}. Railway is redeploying — live in ~1–2 min."
        ),
        status_code=303,
    )


def _parse_group_id(raw: str | None) -> int | None:
    """Coerce a form-submitted group id to int, treating blank/'none' as None.

    The Create-user and role dropdowns submit an empty value for the "Ungrouped"
    option, so an unparseable value simply means no group."""
    val = (raw or "").strip()
    if not val or val.lower() in ("none", "0", "-"):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


@app.post("/admin/groups", include_in_schema=False)
def admin_create_group(
    request: Request,
    name: str = Form(...),
    client_slugs: list[str] = Form(default=[]),
    description: str | None = Form(None),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        created = web_users.create_group(
            name=name, client_slugs=client_slugs, description=description
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    audit_log.record(
        action="group.created",
        actor_user_id=admin.id,
        actor_email=admin.email,
        detail={"name": created["name"], "client_slugs": created["client_slugs"]},
        **ctx,
    )
    return RedirectResponse(url="/admin/users?msg=Group+created", status_code=303)


@app.post("/admin/groups/{group_id}", include_in_schema=False)
def admin_update_group(
    group_id: int,
    request: Request,
    name: str = Form(...),
    client_slugs: list[str] = Form(default=[]),
    description: str | None = Form(None),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        updated = web_users.update_group(
            group_id, name=name, client_slugs=client_slugs, description=description
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    if not updated:
        return RedirectResponse(url="/admin/users?err=Group+not+found", status_code=303)
    audit_log.record(
        action="group.updated",
        actor_user_id=admin.id,
        actor_email=admin.email,
        detail={"name": updated["name"], "client_slugs": updated["client_slugs"]},
        **ctx,
    )
    return RedirectResponse(url="/admin/users?msg=Group+updated", status_code=303)


@app.post("/admin/groups/{group_id}/delete", include_in_schema=False)
def admin_delete_group(
    group_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    target = web_users.get_group(group_id)
    if not target:
        return RedirectResponse(url="/admin/users?err=Group+not+found", status_code=303)
    if not web_users.delete_group(group_id):
        return RedirectResponse(
            url="/admin/users?err=Remove+its+members+before+deleting+the+group",
            status_code=303,
        )
    audit_log.record(
        action="group.deleted",
        actor_user_id=admin.id,
        actor_email=admin.email,
        detail={"name": target["name"]},
        **ctx,
    )
    return RedirectResponse(url="/admin/users?msg=Group+deleted", status_code=303)


def _render_users_page(
    *,
    admin: web_users.WebUser,
    error: str | None = None,
    message: str | None = None,
    invite_link: str | None = None,
    invite_email: str | None = None,
    invite_expires_in: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the Users admin page in place.

    Used instead of a redirect whenever the page must carry something that has
    no business in a URL — an invite link above all: the raw token is shown
    exactly once and must not land in browser history, server logs, or a
    Referer header."""
    return HTMLResponse(
        web_auth.render_admin_page(
            user=admin,
            users=web_users.list_users(include_inactive=False),
            audit_events=audit_log.list_recent(limit=150),
            error=error,
            message=message,
            invite_link=invite_link,
            invite_email=invite_email,
            invite_expires_in=invite_expires_in,
            page="users",
        ),
        status_code=status_code,
    )


@app.post("/admin/users", include_in_schema=False)
def admin_create_user(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(""),
    password: str = Form(""),
    setup_mode: str = Form("invite"),
    role: str = Form("client"),
    client_slug: str | None = Form(None),
    allowed_client_slugs: list[str] = Form(default=[]),
    group_id: str | None = Form(None),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    # Default to the invite flow: the admin never handles a password, and the
    # new user picks their own. 'password' keeps the original behaviour for
    # cases where an admin genuinely wants to hand credentials over directly.
    invite_mode = (setup_mode or "invite").strip().lower() != "password"
    if not invite_mode and not password:
        return _render_users_page(
            admin=user, error="Enter a password, or switch to an invite link.", status_code=400
        )
    try:
        created = web_users.create_user(
            email=email,
            password=None if invite_mode else password,
            role=role,
            client_slug=client_slug,
            allowed_client_slugs=allowed_client_slugs,
            group_id=_parse_group_id(group_id),
            full_name=full_name,
        )
    except ValueError as e:
        return _render_users_page(admin=user, error=str(e), status_code=400)
    audit_log.record(
        action="user.created",
        actor_user_id=user.id,
        actor_email=user.email,
        subject_email=created.email,
        detail={
            "role": created.role,
            "client_slug": created.client_slug,
            "allowed_client_slugs": list(created.allowed_client_slugs),
            "group_id": created.group_id,
            "setup": "invite" if invite_mode else "password",
        },
        **ctx,
    )
    if not invite_mode:
        return RedirectResponse(url="/admin/users?msg=User+created", status_code=303)

    try:
        token, expires_at = user_invites.mint_invite(
            user_id=created.id, email=created.email, created_by=user.email
        )
    except Exception:
        # The account exists and is scoped correctly; only the link failed. Say
        # so plainly rather than implying nothing happened — the admin can mint
        # one from the row action.
        return _render_users_page(
            admin=user,
            error=(
                f"{created.email} was created, but the invite link could not be "
                "generated. Use “Send invite link” on their row to try again."
            ),
            status_code=500,
        )
    audit_log.record(
        action="user.invited",
        actor_user_id=user.id,
        actor_email=user.email,
        subject_email=created.email,
        detail={"role": created.role, "expires_at": expires_at.isoformat()},
        **ctx,
    )
    return _render_users_page(
        admin=user,
        message=f"{created.email} created.",
        invite_link=f"{oauth_flows.public_base_url()}{user_invites.invite_path(token)}",
        invite_email=created.email,
        invite_expires_in=_invite_expires_in(),
    )


@app.post("/admin/users/{user_id}/invite", include_in_schema=False)
def admin_create_invite_link(
    user_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    """Mint a fresh invite link for an existing user.

    Covers three cases with one action: an invite that was never redeemed, a
    link the admin lost (the raw token is unrecoverable by design), and a
    password reset the admin doesn't have to pick a password for. Minting
    revokes any outstanding link for that user, so only the newest one works.
    """
    ctx = audit_log.request_context(request)
    target = web_users.get_user_record(user_id)
    if not target or not target.is_active:
        return _render_users_page(admin=admin, error="User not found.", status_code=404)
    try:
        token, expires_at = user_invites.mint_invite(
            user_id=target.id, email=target.email, created_by=admin.email
        )
    except Exception:
        return _render_users_page(
            admin=admin, error="Could not generate an invite link.", status_code=500
        )
    audit_log.record(
        action="user.invited",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=target.email,
        detail={"role": target.role, "expires_at": expires_at.isoformat(), "reissued": True},
        **ctx,
    )
    return _render_users_page(
        admin=admin,
        invite_link=f"{oauth_flows.public_base_url()}{user_invites.invite_path(token)}",
        invite_email=target.email,
        invite_expires_in=_invite_expires_in(),
    )


@app.post("/admin/users/{user_id}/deactivate", include_in_schema=False)
def admin_deactivate_user(
    user_id: int,
    request: Request,
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    if user_id == admin.id:
        return RedirectResponse(url="/admin/users?err=Cannot+deactivate+your+own+account", status_code=303)
    target = web_users.get_user_record(user_id)
    if target and target.role == "admin" and web_users.count_admins() <= 1:
        return RedirectResponse(url="/admin/users?err=Cannot+deactivate+the+only+admin", status_code=303)
    if target and web_users.deactivate_user(user_id):
        # resolve_invite already requires an active account, so this is belt and
        # braces — but it also means a later reactivation can't silently revive
        # an old link.
        try:
            user_invites.revoke_for_user(user_id)
        except Exception as exc:
            # Not fatal — resolve_invite already refuses an inactive account —
            # but a link that outlives the account it belongs to is worth
            # knowing about.
            log.warning("could not revoke invites for deactivated user %s: %s", user_id, exc)
        audit_log.record(
            action="user.deactivated",
            actor_user_id=admin.id,
            actor_email=admin.email,
            subject_email=target.email,
            detail={"role": target.role, "client_slug": target.client_slug},
            **ctx,
        )
    return RedirectResponse(url="/admin/users?msg=User+deactivated", status_code=303)


@app.post("/admin/users/{user_id}/reset-password", include_in_schema=False)
def admin_reset_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    target = web_users.get_user_record(user_id)
    if not target:
        return RedirectResponse(url="/admin/users?err=User+not+found", status_code=303)
    try:
        ok = web_users.set_password(user_id, new_password)
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    if ok:
        # An outstanding invite link would let its holder overwrite the password
        # just set. Setting a password explicitly supersedes any pending invite.
        try:
            user_invites.revoke_for_user(user_id)
        except Exception as exc:
            # This one has teeth: the password is already changed, so a surviving
            # invite link still lets its holder overwrite it.
            log.warning(
                "password reset for user %s but its invites could not be revoked "
                "— an outstanding link may still be usable: %s",
                user_id, exc,
            )
        audit_log.record(
            action="user.password_reset",
            actor_user_id=admin.id,
            actor_email=admin.email,
            subject_email=target.email,
            **ctx,
        )
        return RedirectResponse(
            url=f"/admin/users?msg=Password+reset+for+{quote(target.email)}", status_code=303
        )
    return RedirectResponse(url="/admin/users?err=Password+reset+failed", status_code=303)


# Cap the stored data URI. Avatars are resized client-side to ~160px, so a real
# headshot lands well under this; the cap just stops an oversized paste from
# bloating the row.


@app.post("/admin/users/{user_id}/avatar", include_in_schema=False)
def admin_set_user_avatar(
    user_id: int,
    request: Request,
    avatar: str = Form(""),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
) -> JSONResponse:
    """Set (or clear) a user's avatar. Expects a resized ``data:image/...`` URI."""
    target = web_users.get_user_record(user_id)
    if not target:
        return JSONResponse({"ok": False, "error": "User not found."}, status_code=404)
    value = (avatar or "").strip()
    if value:
        if not value.startswith("data:image/"):
            return JSONResponse(
                {"ok": False, "error": "Avatar must be an image."}, status_code=400
            )
        if len(value) > _AVATAR_MAX_CHARS:
            return JSONResponse(
                {"ok": False, "error": "Image is too large — try a smaller crop."},
                status_code=413,
            )
    stored = value or None
    if not web_users.set_avatar(user_id, stored):
        return JSONResponse({"ok": False, "error": "Save failed."}, status_code=400)
    audit_log.record(
        action="user.avatar_updated",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=target.email,
        detail={"cleared": stored is None},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "avatar": stored})


@app.post("/admin/users/{user_id}/name", include_in_schema=False)
def admin_set_user_name(
    user_id: int,
    request: Request,
    full_name: str = Form(""),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    """Set (or clear) a user's display name — the name their account chip and
    the roster show instead of the email's local part."""
    target = web_users.get_user_record(user_id)
    if not target:
        return RedirectResponse(url="/admin/users?err=User+not+found", status_code=303)
    try:
        ok = web_users.set_full_name(user_id, full_name)
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    if not ok:
        return RedirectResponse(url="/admin/users?err=Name+update+failed", status_code=303)
    cleared = not (full_name or "").strip()
    audit_log.record(
        action="user.name_changed",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=target.email,
        detail={"full_name": (full_name or "").strip() or None, "cleared": cleared},
        **audit_log.request_context(request),
    )
    msg = "Name cleared" if cleared else "Name updated"
    return RedirectResponse(url=f"/admin/users?msg={quote(msg)}", status_code=303)


@app.post("/admin/users/{user_id}/role", include_in_schema=False)
def admin_set_user_role(
    user_id: int,
    request: Request,
    role: str = Form(...),
    client_slug: str | None = Form(None),
    allowed_client_slugs: list[str] = Form(default=[]),
    group_id: str | None = Form(None),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    if user_id == admin.id:
        return RedirectResponse(url="/admin/users?err=Cannot+change+your+own+role", status_code=303)
    target = web_users.get_user_record(user_id)
    if not target:
        return RedirectResponse(url="/admin/users?err=User+not+found", status_code=303)
    new_role = (role or "").strip().lower()
    # Never let the last remaining admin be demoted out of the admin role.
    if target.role == "admin" and new_role != "admin" and web_users.count_admins() <= 1:
        return RedirectResponse(url="/admin/users?err=Cannot+change+the+only+admin%27s+role", status_code=303)
    try:
        updated = web_users.set_role(
            user_id, new_role, client_slug, allowed_client_slugs, _parse_group_id(group_id)
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/users?err={quote(str(exc))}", status_code=303)
    if not updated:
        return RedirectResponse(url="/admin/users?err=Role+update+failed", status_code=303)
    audit_log.record(
        action="user.role_changed",
        actor_user_id=admin.id,
        actor_email=admin.email,
        subject_email=updated.email,
        detail={
            "role": updated.role,
            "client_slug": updated.client_slug,
            "allowed_client_slugs": list(updated.allowed_client_slugs),
            "group_id": updated.group_id,
        },
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin/users?msg=Role+updated+for+{quote(updated.email)}", status_code=303
    )


@app.get(
    "/oauth/{platform}/connect",
    summary="Start OAuth connect flow (admin)",
    include_in_schema=False,
)
def oauth_connect(platform: str, request: Request, return_to: str = "/admin", client: str = ""):
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")
    web_auth.require_admin(request)
    dest = oauth_flows.validate_return_to(return_to)
    prereq = oauth_flows.connect_prerequisites(slug)
    if not prereq.get("ready"):
        missing = ", ".join(prereq.get("missing") or [])
        raise HTTPException(
            status_code=503,
            detail=f"Set {missing} in Railway before connecting {slug}.",
        )
    if not oauth_store.enabled():
        raise HTTPException(status_code=503, detail="DATABASE_URL is required to store OAuth tokens.")
    state = oauth_flows.make_state()
    oauth_flows.store_oauth_state(request, platform=slug, state=state, return_to=dest, client_slug=client.strip())
    try:
        url = oauth_flows.build_authorize_url(slug, state=state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=303)


@app.get(
    "/connect/{platform}/{client_slug}",
    summary="No-login connect link (signed) — start OAuth for one client",
    include_in_schema=False,
)
def connect_link(platform: str, client_slug: str, request: Request, t: str = ""):
    """Public, signed-token connect link. Lets a specialist/client authorize one
    client's connector (e.g. their HubSpot portal) without a portal login."""
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")
    verified = oauth_flows.verify_connect_state(t)
    if not verified or verified[0] != client_slug.strip().lower() or verified[1] != slug:
        return HTMLResponse(
            "<div style='font-family:system-ui;max-width:520px;margin:80px auto;text-align:center'>"
            "<h2>This connect link is invalid or has expired.</h2>"
            "<p style='color:#555'>Ask your Sagefrog contact for a fresh link.</p></div>",
            status_code=400,
        )
    prereq = oauth_flows.connect_prerequisites(slug)
    if not prereq.get("ready"):
        missing = ", ".join(prereq.get("missing") or [])
        return HTMLResponse(
            f"<div style='font-family:system-ui;max-width:520px;margin:80px auto;text-align:center'>"
            f"<h2>{slug.title()} isn't configured yet.</h2><p style='color:#555'>Missing: {missing}</p></div>",
            status_code=503,
        )
    try:
        # Reuse the signed token as the OAuth state; the callback verifies it.
        url = oauth_flows.build_authorize_url(slug, state=t)
    except Exception as exc:
        return HTMLResponse(
            f"<div style='font-family:system-ui;max-width:520px;margin:80px auto;text-align:center'>"
            f"<h2>Couldn't start the connection.</h2><p style='color:#555'>{quote(str(exc)[:200])}</p></div>",
            status_code=400,
        )
    return RedirectResponse(url=url, status_code=303)


@app.get(
    "/oauth/{platform}/callback",
    summary="OAuth provider callback",
    include_in_schema=False,
)
def oauth_callback(
    platform: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")

    # Signed connect-link flow (no session): the OAuth `state` is a signed token
    # carrying the client_slug, so a specialist/client can authorize without login.
    link_state = oauth_flows.verify_connect_state(state or "")
    if link_state and link_state[1] == slug:
        link_slug = link_state[0]
        dest = f"/dashboard/{link_slug}/connectors/{slug}"
        sep = "?"
        if error:
            msg = (error_description or error or "OAuth denied")[:200]
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(msg)}", status_code=303)
        if not code:
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote('Missing authorization code.')}", status_code=303)
        try:
            tokens = oauth_flows.exchange_code(slug, code=code.strip())
            verify_error = oauth_flows.verify_connected_account(slug, tokens, client_slug=link_slug)
            if verify_error:
                audit_log.record(
                    action="oauth.rejected",
                    actor_email="connect-link",
                    detail={"platform": slug, "client_slug": link_slug, "via": "connect_link", "reason": verify_error},
                    **audit_log.request_context(request),
                )
                return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(verify_error[:200])}", status_code=303)
            oauth_store.save_tokens(
                slug,
                refresh_token=tokens.get("refresh_token"),
                access_token=tokens.get("access_token"),
                token_expires_at=tokens.get("token_expires_at"),
                scopes=tokens.get("scopes"),
                metadata=tokens.get("metadata"),
                connected_by="connect-link",
                client_slug=link_slug,
            )
            audit_log.record(
                action="oauth.connected",
                actor_email="connect-link",
                detail={"platform": slug, "client_slug": link_slug, "via": "connect_link"},
                **audit_log.request_context(request),
            )
        except Exception as exc:
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(str(exc)[:200])}", status_code=303)
        return RedirectResponse(url=f"{dest}{sep}oauth_connected={quote(slug)}", status_code=303)

    expected_state, return_to, oauth_client_slug = oauth_flows.pop_oauth_state(request, platform=slug)
    dest = oauth_flows.validate_return_to(return_to)
    sep = "&" if "?" in dest else "?"
    if error:
        msg = (error_description or error or "OAuth denied")[:200]
        return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(msg)}", status_code=303)
    if not code or not state or not expected_state or state != expected_state:
        return RedirectResponse(
            url=f"{dest}{sep}oauth_error={quote('Invalid OAuth state. Try connecting again.')}",
            status_code=303,
        )
    user = web_auth.get_current_user(request)
    actor = user.email if user else None
    try:
        tokens = oauth_flows.exchange_code(slug, code=code.strip())
        verify_error = oauth_flows.verify_connected_account(slug, tokens, client_slug=oauth_client_slug)
        if verify_error:
            audit_log.record(
                action="oauth.rejected",
                actor_email=actor,
                detail={"platform": slug, "client_slug": oauth_client_slug or "global", "reason": verify_error},
                **audit_log.request_context(request),
            )
            return RedirectResponse(url=f"{dest}{sep}oauth_error={quote(verify_error[:200])}", status_code=303)
        oauth_store.save_tokens(
            slug,
            refresh_token=tokens.get("refresh_token"),
            access_token=tokens.get("access_token"),
            token_expires_at=tokens.get("token_expires_at"),
            scopes=tokens.get("scopes"),
            metadata=tokens.get("metadata"),
            connected_by=actor,
            client_slug=oauth_client_slug,
        )
        audit_log.record(
            action="oauth.connected",
            actor_email=actor,
            detail={"platform": slug, "client_slug": oauth_client_slug or "global"},
            **audit_log.request_context(request),
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"{dest}{sep}oauth_error={quote(str(exc)[:200])}",
            status_code=303,
        )
    return RedirectResponse(url=f"{dest}{sep}oauth_connected={quote(slug)}", status_code=303)


def _register_microsoft_ads_callback() -> None:
    """Mount the Microsoft Ads OAuth callback at the exact path Google redirects to.

    Any path shaped ``/oauth/<slug>/callback`` is already served by the generic
    ``/oauth/{platform}/callback`` route (which normalizes hyphens to
    underscores, so ``/oauth/microsoft-ads/callback`` resolves to the
    ``microsoft_ads`` platform). Only a genuinely custom redirect path needs an
    explicit alias — and it must be inserted AHEAD of the parameterized routes,
    since Starlette matches in registration order and the generic route would
    otherwise win and 404 on an unknown platform segment.
    """
    try:
        path = oauth_flows.microsoft_ads_callback_path()
    except Exception:
        return
    if not path:
        return
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "oauth" and parts[2] == "callback":
        return  # /oauth/<slug>/callback — handled by the generic route

    def _ms_ads_callback_alias(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ):
        return oauth_callback(
            "microsoft_ads",
            request,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        )

    app.add_api_route(path, _ms_ads_callback_alias, methods=["GET"], include_in_schema=False)
    # Move the freshly-appended route to the front so it precedes the generic
    # /oauth/{platform}/callback route in match order.
    app.router.routes.insert(0, app.router.routes.pop())


_register_microsoft_ads_callback()


@app.post(
    "/oauth/{platform}/disconnect",
    summary="Remove stored OAuth token (admin)",
    include_in_schema=False,
)
def oauth_disconnect(
    platform: str,
    request: Request,
    return_to: str = Form("/admin"),
):
    # Accept hyphen or underscore in the platform segment. Redirect URIs are
    # often registered with hyphens (e.g. /oauth/microsoft-ads/callback) while
    # our platform keys use underscores (microsoft_ads); normalize so Google's
    # redirect to the hyphenated path resolves instead of 404-ing.
    slug = platform.strip().lower().replace("-", "_")
    if slug not in oauth_flows.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown OAuth platform.")
    user = web_auth.require_admin(request)
    oauth_store.delete_platform(slug)
    audit_log.record(
        action="oauth.disconnected",
        actor_email=user.email,
        detail={"platform": slug},
        **audit_log.request_context(request),
    )
    dest = oauth_flows.validate_return_to(return_to)
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(url=f"{dest}{sep}oauth_disconnected={quote(slug)}", status_code=303)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
