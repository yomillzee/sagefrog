"""Browser session auth for dashboards and admin pages."""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

import audit_log
import client_config
import client_industries
import web_users
from security import is_production, session_signing_secret
from web_users import WebUser

SESSION_USER_ID = "user_id"
# When an admin is "viewing as" another user, this holds that target user's id.
# The real signed-in account stays in SESSION_USER_ID; only get_current_user
# resolves to the impersonated user so every downstream access check and page
# renders exactly as that user would see them.
SESSION_VIEW_AS_ID = "view_as_user_id"
# Epoch-seconds of the last time this session's activity was flushed to the
# user's last_seen_at. Used to throttle those writes (see touch_last_seen).
SESSION_LAST_SEEN_STAMP = "last_seen_stamp"
# Only flush activity to the DB this often per session. A long-lived session
# would otherwise write on every request; a few minutes keeps last_seen_at
# usefully fresh on the admin list without turning reads into writes.
LAST_SEEN_THROTTLE_SECONDS = 5 * 60


def session_secret() -> str:
    # Canonical resolver lives in security.session_signing_secret(): dedicated to
    # AUTH_SESSION_SECRET, fail-closed in production, dev-only fallback. Kept as a
    # thin wrapper so existing callers (add_session_middleware) stay unchanged.
    return session_signing_secret()


def https_only_cookies() -> bool:
    raw = (os.getenv("AUTH_SESSION_HTTPS_ONLY") or "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    # No explicit setting: secure cookies by default in any deployed environment.
    # Insecure cookies (local HTTP) now require an explicit AUTH_SESSION_HTTPS_ONLY=0,
    # so a custom host/proxy with the Railway vars absent stays secure instead of
    # silently downgrading.
    return is_production()


def add_session_middleware(app) -> None:
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(),
        session_cookie="eos_session",
        max_age=60 * 60 * 24 * 14,
        same_site="lax",
        https_only=https_only_cookies(),
    )


def auth_enabled() -> bool:
    return web_users.enabled()


def get_real_user(request: Request) -> WebUser | None:
    """The actually signed-in account, ignoring any active "view as"."""
    if not auth_enabled():
        return None
    raw = request.session.get(SESSION_USER_ID)
    if raw is None:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        request.session.clear()
        return None
    user = web_users.get_user_by_id(user_id)
    if not user:
        request.session.clear()
    return user


def _view_as_target(request: Request, real: WebUser | None) -> WebUser | None:
    """The user an admin is impersonating via "view as", or None.

    Only admins may impersonate. A stale/invalid/self target self-heals by
    clearing the session key so a deactivated or deleted target can't wedge the
    session.
    """
    if not real or real.role != "admin":
        return None
    raw = request.session.get(SESSION_VIEW_AS_ID)
    if raw is None:
        return None
    try:
        target_id = int(raw)
    except (TypeError, ValueError):
        request.session.pop(SESSION_VIEW_AS_ID, None)
        return None
    if target_id == real.id:
        request.session.pop(SESSION_VIEW_AS_ID, None)
        return None
    target = web_users.get_user_by_id(target_id)
    if not target:
        request.session.pop(SESSION_VIEW_AS_ID, None)
        return None
    return target


def get_current_user(request: Request) -> WebUser | None:
    """The effective user for access checks and rendering.

    Normally the signed-in account, but when an admin has an active "view as"
    this returns the impersonated user so the whole platform (which dashboards
    are visible, client scoping, admin-only UI) reflects that user's experience.
    """
    real = get_real_user(request)
    if real and real.role == "admin":
        target = _view_as_target(request, real)
        if target:
            return target
    return real


def current_view_as(request: Request) -> WebUser | None:
    """The user currently being viewed-as (admin impersonation), or None."""
    return _view_as_target(request, get_real_user(request))


def set_view_as(request: Request, target_user_id: int) -> None:
    request.session[SESSION_VIEW_AS_ID] = int(target_user_id)


def clear_view_as(request: Request) -> None:
    request.session.pop(SESSION_VIEW_AS_ID, None)


def login_user(request: Request, user: WebUser) -> None:
    request.session[SESSION_USER_ID] = user.id
    # A fresh login must never inherit a previous session's impersonation.
    request.session.pop(SESSION_VIEW_AS_ID, None)


def logout_user(request: Request) -> None:
    request.session.clear()


def touch_last_seen(request: Request) -> None:
    """Record that the signed-in account is active right now (throttled).

    Stamps the *real* signed-in user (SESSION_USER_ID), not any "view as"
    target, so impersonation never rewrites someone else's activity. Writes are
    throttled per session via SESSION_LAST_SEEN_STAMP so a busy session touches
    the DB at most once every LAST_SEEN_THROTTLE_SECONDS.

    Best-effort and never raises: activity tracking must never affect the
    request it rides along on."""
    try:
        if "session" not in request.scope:
            return
        raw = request.session.get(SESSION_USER_ID)
        if raw is None:
            return
        user_id = int(raw)
        now = int(datetime.now(tz=UTC).timestamp())
        last = request.session.get(SESSION_LAST_SEEN_STAMP)
        if isinstance(last, (int, float)) and now - int(last) < LAST_SEEN_THROTTLE_SECONDS:
            return
        # Update the throttle marker first so a slow/failed DB write doesn't
        # cause every subsequent request to retry the write.
        request.session[SESSION_LAST_SEEN_STAMP] = now
        web_users.record_activity(user_id)
    except Exception:
        pass


def impersonation_banner_html(request: Request) -> str:
    """Fixed "Viewing as …" bar with an Exit control, or "" when not active.

    Injected into every HTML page (see the middleware in main.py) so an admin
    can always leave "view as" from wherever they navigated. Uses inline styles
    so it renders even on pages whose CSS it isn't part of.
    """
    target = current_view_as(request)
    if not target:
        return ""
    who = _esc(target.email)
    role = _esc(target.role + (f" · {target.client_slug}" if target.client_slug else ""))
    return f"""
    <div style="position:fixed;left:0;right:0;bottom:0;z-index:2147483000;
      display:flex;align-items:center;justify-content:center;gap:14px;flex-wrap:wrap;
      padding:9px 16px;background:#7c2d12;color:#fff;font:600 .85rem/1.3 -apple-system,
      BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;box-shadow:0 -2px 12px rgba(0,0,0,.25);">
      <span style="display:inline-flex;align-items:center;gap:8px;">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
          stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>
        Viewing as <strong style="font-weight:800;">{who}</strong>
        <span style="opacity:.8;font-weight:600;">({role})</span>
      </span>
      <form method="post" action="/admin/view-as/exit" style="margin:0;">
        <button type="submit" style="appearance:none;border:1px solid rgba(255,255,255,.7);
          background:rgba(255,255,255,.12);color:#fff;font:inherit;font-weight:700;
          padding:5px 14px;border-radius:999px;cursor:pointer;">Exit view as</button>
      </form>
    </div>"""


async def require_user(request: Request) -> WebUser:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


async def require_admin(request: Request) -> WebUser:
    user = await require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def super_admin_emails() -> set[str]:
    """Admins allowed to perform destructive actions (e.g. deleting dashboards).

    Configurable via SUPER_ADMIN_EMAILS (comma-separated); mikem@sagefrog.com is
    always included.
    """
    raw = os.getenv("SUPER_ADMIN_EMAILS", "")
    emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    emails.add("mikem@sagefrog.com")
    return emails


def is_super_admin(user: WebUser | None) -> bool:
    return bool(
        user
        and user.role == "admin"
        and (user.email or "").strip().lower() in super_admin_emails()
    )


async def require_super_admin(request: Request) -> WebUser:
    user = await require_admin(request)
    if not is_super_admin(user):
        raise HTTPException(status_code=403, detail="Super admin access required.")
    return user


def require_client_access(client_slug: str):
    slug = client_slug.strip().lower()

    async def _dep(request: Request) -> WebUser:
        user = await require_user(request)
        if not user.can_access_client(slug):
            raise HTTPException(status_code=403, detail=f"No access to client '{slug}'.")
        return user

    return _dep


def redirect_to_login(request: Request, *, next_path: str | None = None) -> RedirectResponse:
    target = next_path or str(request.url.path)
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={quote(target, safe='')}", status_code=303)


@dataclass(frozen=True)
class DashboardAuth:
    # access_key / use_session are retained for renderer compatibility. The legacy
    # ?key= share-link mechanism has been retired, so dashboards are session-only:
    # access_key is always None and use_session is always True.
    access_key: str | None
    use_session: bool
    user: WebUser | None


def _require_session_enabled() -> None:
    if not web_users.enabled():
        raise HTTPException(
            status_code=503,
            detail="Dashboard access requires login (set DATABASE_URL to enable web users).",
        )


def authenticate_dashboard(
    request: Request,
    *,
    client_slug: str,
) -> DashboardAuth | RedirectResponse:
    """Require a signed-in user with access to this client's dashboard.

    The legacy ?key= share link has been retired — access is session-only. An
    authenticated user without access gets 403; an anonymous visitor is
    redirected to /login.
    """
    _require_session_enabled()
    user = get_current_user(request)
    if user and user.can_access_client(client_slug):
        return DashboardAuth(access_key=None, use_session=True, user=user)
    if user:
        raise HTTPException(status_code=403, detail="You do not have access to this dashboard.")
    return redirect_to_login(request)


def authenticate_dashboard_api(
    request: Request,
    *,
    client_slug: str,
) -> DashboardAuth:
    """Same as authenticate_dashboard but JSON-friendly errors (no redirect)."""
    _require_session_enabled()
    user = get_current_user(request)
    if user and user.can_access_client(client_slug):
        return DashboardAuth(access_key=None, use_session=True, user=user)
    if user:
        raise HTTPException(status_code=403, detail="You do not have access to this dashboard.")
    raise HTTPException(status_code=401, detail="Sign in required.")


def authenticate_dashboard_any(
    request: Request,
    *,
    client_slugs: tuple[str, ...],
) -> DashboardAuth | RedirectResponse:
    """Like authenticate_dashboard, but access to ANY of the given slugs opens
    the dashboard.

    Used where one portal is reachable under more than one registered slug —
    e.g. Nixon's dashboard is listed and granted under "nixon-bq-test" but its
    routes historically auth under the "nixon" connector/marketing key. Checking
    a single slug there wrongly 403s a user granted only the other one.
    """
    _require_session_enabled()
    user = get_current_user(request)
    if user and any(user.can_access_client(slug) for slug in client_slugs):
        return DashboardAuth(access_key=None, use_session=True, user=user)
    if user:
        raise HTTPException(status_code=403, detail="You do not have access to this dashboard.")
    return redirect_to_login(request)


def authenticate_dashboard_api_any(
    request: Request,
    *,
    client_slugs: tuple[str, ...],
) -> DashboardAuth:
    """Same as authenticate_dashboard_any but JSON-friendly errors (no redirect)."""
    _require_session_enabled()
    user = get_current_user(request)
    if user and any(user.can_access_client(slug) for slug in client_slugs):
        return DashboardAuth(access_key=None, use_session=True, user=user)
    if user:
        raise HTTPException(status_code=403, detail="You do not have access to this dashboard.")
    raise HTTPException(status_code=401, detail="Sign in required.")


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


# Shared visual system for the signed-out auth pages (sign-in and invite
# redemption). Kept as a plain string — NOT an f-string — so the CSS braces
# stay single here and are interpolated verbatim into each page's f-string.
_AUTH_PAGE_CSS = """    :root {
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#34b27b; --danger:#b42318;
    }
    * { box-sizing:border-box; }
    html,body { height:100%; }
    body {
      margin:0; padding:24px; color:var(--ink); display:grid; place-items:center;
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
      background:
        radial-gradient(1100px 560px at 16% -12%, rgba(52,178,123,.20), transparent 60%),
        radial-gradient(900px 520px at 100% 0%, rgba(37,99,235,.24), transparent 55%),
        linear-gradient(160deg,#0a2540 0%,#071b30 55%,#05121f 100%);
    }
    .wrap { width:min(420px,100%); position:relative; z-index:1; animation:rise .5s ease both; }
    @keyframes rise { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
    .brand { display:flex; flex-direction:column; align-items:center; gap:12px; margin-bottom:22px; }
    .logo { width:56px; height:56px; border-radius:16px; display:grid; place-items:center; overflow:hidden;
      background:#fff; box-shadow:0 10px 26px rgba(5,18,31,.5); }
    .logo img { width:100%; height:100%; object-fit:cover; }
    .brand h1 { margin:0; font-size:1.05rem; font-weight:700; color:#fff; letter-spacing:.2px; }
    .brand p { margin:0; font-size:.84rem; color:rgba(255,255,255,.6); }
    .card { background:#fff; border-radius:18px; padding:30px 28px;
      box-shadow:0 24px 60px rgba(3,12,24,.42), 0 2px 6px rgba(3,12,24,.2); }
    .card h2 { margin:0 0 4px; font-size:1.28rem; color:var(--navy); }
    .card .lead { margin:0 0 22px; color:var(--muted); font-size:.9rem; }
    label { display:block; font-size:.8rem; font-weight:600; color:#334155; margin:0 0 6px; }
    .field { margin-bottom:16px; position:relative; }
    input { width:100%; padding:12px 14px; border:1px solid var(--line); border-radius:10px;
      font-size:.98rem; color:var(--ink); background:#fbfcfe; transition:border-color .15s, box-shadow .15s; }
    input::placeholder { color:#9aa5b5; }
    input:focus { outline:none; border-color:var(--accent); background:#fff;
      box-shadow:0 0 0 3px rgba(37,99,235,.15); }
    .pw-wrap input { padding-right:62px; }
    .pw-toggle { position:absolute; right:8px; top:32px; border:0; background:transparent;
      color:var(--muted); font-size:.76rem; font-weight:600; cursor:pointer; padding:6px 8px;
      border-radius:6px; }
    .pw-toggle:hover { color:var(--accent); background:#eef4ff; }
    button.submit { width:100%; padding:13px; border:0; border-radius:10px; color:#fff;
      font-size:1rem; font-weight:700; cursor:pointer; letter-spacing:.2px; margin-top:4px;
      background:linear-gradient(135deg,var(--accent),var(--accent-d));
      box-shadow:0 6px 18px rgba(37,99,235,.35); transition:transform .06s, filter .15s; }
    button.submit:hover { filter:brightness(1.06); }
    button.submit:active { transform:translateY(1px); }
    .error { color:var(--danger); font-size:.87rem; margin:0 0 14px; padding:10px 12px;
      background:#fef3f2; border:1px solid #fecdc9; border-radius:9px; }
    .forgot { margin-top:18px; border-top:1px solid var(--line); padding-top:16px; }
    .forgot summary { list-style:none; cursor:pointer; font-size:.86rem; font-weight:600;
      color:var(--accent); }
    .forgot summary::-webkit-details-marker { display:none; }
    .forgot summary:hover { text-decoration:underline; }
    .forgot-body { margin-top:12px; font-size:.85rem; color:var(--muted); line-height:1.55; }
    .forgot-body a.ghost { display:inline-block; margin-top:12px; padding:9px 16px; border-radius:9px;
      border:1px solid var(--accent); color:var(--accent); text-decoration:none; font-weight:600;
      font-size:.85rem; transition:background .15s, color .15s; }
    .forgot-body a.ghost:hover { background:var(--accent); color:#fff; }
    .foot { display:flex; align-items:center; justify-content:center; gap:6px; margin-top:20px;
      font-size:.75rem; color:rgba(255,255,255,.5); }
    .foot svg { width:13px; height:13px; }"""


def render_login_page(*, error: str | None = None, next_path: str = "/admin") -> str:
    err = f'<p class="error" role="alert">{_esc(error)}</p>' if error else ""
    nxt = _esc(next_path or "/admin")
    # Where the "Request a reset" button emails — resets are admin-driven (there is
    # no self-service email flow). Change this to your support inbox.
    support_email = "sagefrogmarketinggroup@gmail.com"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · Sagefrog Marketing Group</title>
  <style>
{_AUTH_PAGE_CSS}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <div class="logo">
        <img src="/static/apple-touch-icon.png" alt="Sagefrog" width="56" height="56">
      </div>
      <h1>Sagefrog Marketing Group</h1>
      <p>Client dashboards</p>
    </div>
    <div class="card">
      <h2>Welcome back</h2>
      <p class="lead">Sign in to access your reporting dashboards.</p>
      {err}
      <form method="post" action="/login">
        <input type="hidden" name="next" value="{nxt}">
        <div class="field">
          <label for="email">Email</label>
          <input id="email" name="email" type="email" autocomplete="username" placeholder="you@company.com" required>
        </div>
        <div class="field pw-wrap">
          <label for="password">Password</label>
          <input id="password" name="password" type="password" autocomplete="current-password" placeholder="••••••••" required>
          <button type="button" class="pw-toggle" id="pwToggle" aria-label="Show password">Show</button>
        </div>
        <button type="submit" class="submit">Sign in</button>
      </form>
      <details class="forgot">
        <summary>Forgot your password?</summary>
        <div class="forgot-body">
          Password resets are handled by your Sagefrog administrator. Send us a note and we'll get you back in.
          <a class="ghost" href="mailto:{support_email}?subject=Password%20reset%20request">Request a reset</a>
        </div>
      </details>
    </div>
    <div class="foot">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
      Secure sign-in · &copy; 2026 Sagefrog Marketing Group
    </div>
  </div>
  <script>
    (function(){{
      var t=document.getElementById('pwToggle'), p=document.getElementById('password');
      if(t&&p) t.addEventListener('click',function(){{
        var show=p.type==='password'; p.type=show?'text':'password';
        t.textContent=show?'Hide':'Show';
        t.setAttribute('aria-label',show?'Hide password':'Show password');
      }});
    }})();
  </script>
  <script src="/static/login-bg.js" defer></script>
</body>
</html>"""


def render_invite_page(
    *, token: str, email: str, error: str | None = None, expired: bool = False
) -> str:
    """The page an invitee lands on from their magic link.

    Two states share one renderer: ``expired`` renders a dead end pointing back
    at sign-in (used for any invalid token — wrong, used, revoked, or genuinely
    expired, so the page never tells a prober which), and the normal state
    renders the choose-a-password form. The form posts back to the same
    tokenized URL; the CSRF middleware stamps its hidden field on the way out.
    """
    err = f'<p class="error" role="alert">{_esc(error)}</p>' if error else ""
    tok = _esc(token or "")
    who = _esc(email or "")

    if expired:
        body = """
      <h2>This link has expired</h2>
      <p class="lead">Invite links are single-use and time-limited, so this one
        can't be used anymore &mdash; it may have already been redeemed.</p>
      <div class="forgot-body" style="margin:0">
        Ask your Sagefrog administrator for a fresh invite link.
        <a class="ghost" href="/login">Go to sign in</a>
      </div>"""
    else:
        body = f"""
      <h2>Set your password</h2>
      <p class="lead">Welcome! Choose a password for <strong>{who}</strong> to finish
        setting up your account.</p>
      {err}
      <form method="post" action="/invite/{tok}">
        <div class="field pw-wrap">
          <label for="password">New password</label>
          <input id="password" name="password" type="password" autocomplete="new-password"
            placeholder="At least 10 characters" minlength="10" required autofocus>
          <button type="button" class="pw-toggle" id="pwToggle" aria-label="Show password">Show</button>
        </div>
        <div class="field">
          <label for="confirm">Confirm password</label>
          <input id="confirm" name="confirm" type="password" autocomplete="new-password"
            placeholder="Type it again" minlength="10" required>
        </div>
        <button type="submit" class="submit">Set password &amp; sign in</button>
      </form>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <!-- Never leak the token in a Referer to anything, same-origin included. -->
  <meta name="referrer" content="no-referrer">
  <title>Set your password · Sagefrog Marketing Group</title>
  <style>
{_AUTH_PAGE_CSS}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <div class="logo">
        <img src="/static/apple-touch-icon.png" alt="Sagefrog" width="56" height="56">
      </div>
      <h1>Sagefrog Marketing Group</h1>
      <p>Client dashboards</p>
    </div>
    <div class="card">{body}
    </div>
    <div class="foot">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
      Secure sign-in · &copy; 2026 Sagefrog Marketing Group
    </div>
  </div>
  <script>
    (function(){{
      var t=document.getElementById('pwToggle'), p=document.getElementById('password');
      if(t&&p) t.addEventListener('click',function(){{
        var show=p.type==='password'; p.type=show?'text':'password';
        t.textContent=show?'Hide':'Show';
        t.setAttribute('aria-label',show?'Hide password':'Show password');
      }});
      // Catch the mismatch in the browser so the user isn't bounced through a
      // round trip; the server re-checks regardless.
      var f=p&&p.form, c=document.getElementById('confirm');
      if(f&&c) f.addEventListener('submit',function(e){{
        if(p.value!==c.value){{
          e.preventDefault();
          c.setCustomValidity('Passwords do not match.');
          c.reportValidity();
        }}
      }});
      if(c) c.addEventListener('input',function(){{ c.setCustomValidity(''); }});
    }})();
  </script>
  <script src="/static/login-bg.js" defer></script>
</body>
</html>"""


def render_dashboards_page(*, user: WebUser, dashboards: list[tuple[str, str]]) -> str:
    """Landing page for non-admin users: the client dashboards they can open.

    ``dashboards`` is a list of ``(slug, label)`` the caller has already filtered
    to what this user may access.
    """
    if dashboards:
        items = "\n".join(
            f'<li><a class="dash-link" href="/dashboard/{_esc(slug)}">{_esc(label or slug)}</a></li>'
            for slug, label in dashboards
        )
        body = f'<ul class="dash-list">{items}</ul>'
    else:
        body = (
            '<p class="sub">No dashboards are available for your account yet. '
            "Ask an admin to grant access.</p>"
        )
    admin_link = (
        '<a class="foot-link" href="/admin">Admin</a>' if user.role == "admin" else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboards · Sagefrog Marketing Group</title>
  <style>
    :root {{
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#34b27b; --danger:#b42318;
    }}
    * {{ box-sizing:border-box; }}
    html,body {{ height:100%; }}
    body {{
      margin:0; padding:24px; color:var(--ink); display:grid; place-items:center;
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
      background:
        radial-gradient(1100px 560px at 16% -12%, rgba(52,178,123,.20), transparent 60%),
        radial-gradient(900px 520px at 100% 0%, rgba(37,99,235,.24), transparent 55%),
        linear-gradient(160deg,#0a2540 0%,#071b30 55%,#05121f 100%);
    }}
    .wrap {{ width:min(440px,100%); animation:rise .5s ease both; }}
    @keyframes rise {{ from {{ opacity:0; transform:translateY(10px); }} to {{ opacity:1; transform:none; }} }}
    .brand {{ display:flex; flex-direction:column; align-items:center; gap:12px; margin-bottom:22px; }}
    .logo {{ width:56px; height:56px; border-radius:16px; display:grid; place-items:center; overflow:hidden;
      background:#fff; box-shadow:0 10px 26px rgba(5,18,31,.5); }}
    .logo img {{ width:100%; height:100%; object-fit:cover; }}
    .brand h1 {{ margin:0; font-size:1.05rem; font-weight:700; color:#fff; letter-spacing:.2px; }}
    .brand > p {{ margin:0; font-size:.84rem; color:rgba(255,255,255,.6); }}
    .card {{ background:#fff; border-radius:18px; padding:30px 28px;
      box-shadow:0 24px 60px rgba(3,12,24,.42), 0 2px 6px rgba(3,12,24,.2); }}
    .card h2 {{ margin:0 0 4px; font-size:1.28rem; color:var(--navy); }}
    p.sub {{ margin:0 0 20px; color:var(--muted); font-size:.9rem; }}
    .dash-list {{ list-style:none; margin:0; padding:0; display:grid; gap:9px; }}
    .dash-link {{ display:flex; align-items:center; justify-content:space-between; padding:13px 15px;
      border:1px solid var(--line); border-radius:11px; text-decoration:none; color:var(--navy);
      font-weight:600; background:#fbfcfe; transition:border-color .15s, background .15s, transform .06s; }}
    .dash-link::after {{ content:"→"; color:var(--accent); opacity:0; transition:opacity .15s; }}
    .dash-link:hover {{ border-color:var(--accent); background:#f2f7ff; }}
    .dash-link:hover::after {{ opacity:1; }}
    .foot {{ display:flex; justify-content:space-between; align-items:center;
      margin-top:20px; padding-top:16px; border-top:1px solid var(--line); }}
    .foot-link {{ color:var(--accent); font-size:.9rem; text-decoration:none; font-weight:600; }}
    .foot-link:hover {{ text-decoration:underline; }}
    .foot form {{ margin:0; }}
    .foot button {{ border:0; background:none; color:var(--muted); font-size:.9rem; font-weight:600;
      cursor:pointer; padding:0; }}
    .foot button:hover {{ color:var(--danger); }}
    .pagefoot {{ display:flex; align-items:center; justify-content:center; gap:6px; margin-top:20px;
      font-size:.75rem; color:rgba(255,255,255,.5); }}
    .pagefoot svg {{ width:13px; height:13px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <div class="logo">
        <img src="/static/apple-touch-icon.png" alt="Sagefrog" width="56" height="56">
      </div>
      <h1>Sagefrog Marketing Group</h1>
      <p>Client dashboards</p>
    </div>
    <div class="card">
      <h2>Your dashboards</h2>
      <p class="sub">Signed in as {_esc(user.email)}</p>
      {body}
      <div class="foot">
        {admin_link or '<span></span>'}
        <form method="post" action="/logout"><button type="submit">Sign out</button></form>
      </div>
    </div>
    <div class="pagefoot">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
      Secure sign-in · &copy; 2026 Sagefrog Marketing Group
    </div>
  </div>
</body>
</html>"""


def _parse_iso_utc(iso: str | None) -> datetime | None:
    if not iso:
        return None
    text = str(iso).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _format_last_seen(iso: str | None) -> tuple[str, str]:
    """Return (relative label, absolute tooltip) for a last-activity timestamp.

    Relative keeps the column scannable ("2h ago"); the tooltip carries the
    exact UTC time for when precision matters."""
    dt = _parse_iso_utc(iso)
    if dt is None:
        return "Never", "Has not signed in yet"
    now = datetime.now(tz=UTC)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        rel = "Just now"
    elif secs < 3600:
        mins = secs // 60
        rel = f"{mins}m ago"
    elif secs < 86400:
        rel = f"{secs // 3600}h ago"
    elif secs < 86400 * 7:
        rel = f"{secs // 86400}d ago"
    elif secs < 86400 * 30:
        rel = f"{secs // (86400 * 7)}w ago"
    else:
        # Built by hand rather than with %-d: that padding flag is glibc-only
        # and raises on Windows.
        rel = f"{dt:%b} {dt.day}, {dt.year}"
    return rel, dt.strftime("%Y-%m-%d %H:%M UTC")


def _format_audit_time(iso: str | None) -> str:
    if not iso:
        return "—"
    text = str(iso).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso[:19] if iso else "—"


# Client-side avatar upload: resize the chosen file to a small square JPEG and
# POST it to the per-user endpoint, then swap the avatar in place. Plain string
# (not an f-string) — it's injected verbatim into the admin page.
_ROLE_TOGGLE_JS = """
(function () {
  // Live "this user will see …" preview under each client-group picker. This is
  // the redundancy check: the admin confirms the exact dashboards a group grants
  // before saving, so nobody is bound to the wrong portal by a mistyped slug.
  function syncGroupPreview(sel) {
    var preview = sel.parentNode.querySelector('.group-preview');
    if (!preview) return;
    var opt = sel.options[sel.selectedIndex];
    var labels = (opt && opt.getAttribute('data-labels')) || '';
    if (!sel.value) {
      preview.hidden = true;
      preview.innerHTML = '';
      return;
    }
    preview.hidden = false;
    if (labels) {
      var chips = labels.split(' · ').map(function (l) {
        return '<span class="gp-chip">' + l.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</span>';
      }).join('');
      preview.innerHTML = '<span class="gp-label">Grants access to</span>' + chips;
      preview.className = 'group-preview';
    } else {
      preview.innerHTML = '<span class="gp-warn">This group has no dashboards yet — the user would see nothing.</span>';
      preview.className = 'group-preview is-warn';
    }
  }
  function sync(form) {
    var sel = form.querySelector('.role-select');
    if (!sel) return;
    var role = sel.value;
    form.querySelectorAll('.client-only').forEach(function (el) {
      el.style.display = (role === 'client') ? '' : 'none';
    });
    form.querySelectorAll('.standard-only').forEach(function (el) {
      el.style.display = (role === 'standard') ? '' : 'none';
    });
  }
  document.querySelectorAll('form.role-form').forEach(function (form) {
    var sel = form.querySelector('.role-select');
    if (sel) sel.addEventListener('change', function () { sync(form); });
    sync(form);
    form.querySelectorAll('.group-select').forEach(function (g) {
      g.addEventListener('change', function () { syncGroupPreview(g); });
      syncGroupPreview(g);
    });
  });

  // Create-user: invite link vs admin-chosen password. The password field is
  // only required in 'password' mode — leaving it required while hidden would
  // make the form silently unsubmittable.
  (function () {
    var form = document.getElementById('createUserForm');
    if (!form) return;
    var mode = form.querySelector('.setup-select');
    var pw = form.querySelector('#password');
    var btn = document.getElementById('createUserBtn');
    if (!mode) return;
    function syncSetup() {
      var invite = mode.value === 'invite';
      form.querySelectorAll('.password-only').forEach(function (el) {
        el.style.display = invite ? 'none' : '';
      });
      form.querySelectorAll('.setup-hint-invite').forEach(function (el) {
        el.style.display = invite ? '' : 'none';
      });
      if (pw) {
        pw.required = !invite;
        if (invite) pw.value = '';
      }
      if (btn) btn.textContent = invite ? 'Create user & get invite link' : 'Create user';
    }
    mode.addEventListener('change', syncSetup);
    syncSetup();
  })();

  // One-shot copy button for a freshly minted invite link.
  (function () {
    var btn = document.getElementById('inviteCopyBtn');
    var field = document.getElementById('inviteLink');
    if (!btn || !field) return;
    btn.addEventListener('click', function () {
      field.select();
      var done = function () {
        btn.textContent = 'Copied';
        setTimeout(function () { btn.textContent = 'Copy'; }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(field.value).then(done, function () {
          try { document.execCommand('copy'); done(); } catch (e) {}
        });
      } else {
        try { document.execCommand('copy'); done(); } catch (e) {}
      }
    });
  })();
})();
"""

# Client-side filter over the two user tables so a growing roster stays
# scannable. A segmented control scopes to the Sagefrog team, the clients, or
# both; the search box matches each row's data-search key within the shown
# panels. The header count reflects what's currently visible.
_USER_SEARCH_JS = """
(function () {
  var input = document.getElementById('userSearch');
  var count = document.getElementById('userCount');
  var panels = Array.prototype.slice.call(document.querySelectorAll('.user-group'));
  var segBtns = Array.prototype.slice.call(document.querySelectorAll('.seg-btn'));
  if (!panels.length) return;
  var scope = 'all';
  function apply() {
    var q = (input ? input.value : '').trim().toLowerCase();
    var total = 0;
    panels.forEach(function (panel) {
      var group = panel.getAttribute('data-group');
      var inScope = (scope === 'all' || scope === group);
      panel.hidden = !inScope;
      var shown = 0;
      var rows = panel.querySelectorAll('tr.user-row');
      rows.forEach(function (row) {
        var hit = inScope && (!q || (row.getAttribute('data-search') || '').indexOf(q) !== -1);
        row.hidden = !hit;
        if (hit) shown++;
      });
      var empty = panel.querySelector('.users-empty');
      if (empty) empty.hidden = !(inScope && rows.length && shown === 0);
      if (inScope) total += shown;
    });
    if (count) count.textContent = total;
  }
  segBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      scope = btn.getAttribute('data-scope') || 'all';
      segBtns.forEach(function (b) {
        var on = (b === btn);
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      apply();
    });
  });
  if (input) input.addEventListener('input', apply);
  apply();
})();
"""

# Client-side filter for the Accounts grid: hides account cards whose name/slug
# don't match the query, keeps the count chip live, and toggles an empty state.
_DASH_SEARCH_JS = """
(function () {
  var input = document.getElementById('dashSearch');
  var count = document.getElementById('dashCount');
  var empty = document.getElementById('dashEmpty');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.dash-list .dash-row'));
  if (!input || !cards.length) return;
  function apply() {
    var q = input.value.trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (card) {
      var hit = !q || (card.getAttribute('data-search') || '').indexOf(q) !== -1;
      card.hidden = !hit;
      if (hit) shown++;
    });
    if (count) count.textContent = shown;
    if (empty) empty.hidden = shown !== 0;
  }
  input.addEventListener('input', apply);
  apply();
})();
"""

# Row kebab (⋮) menus, shared by the Accounts rows and the Users rows: swap
# between the action list and an inline panel, gate a delete on a matching
# confirmation, and close on outside click or Escape (a native <details>
# won't do that on its own).
_KEBAB_JS = """
    // Kebab row menus: swap between the action list and the rename/delete panels,
    // gate the delete button on a matching confirmation, and close on outside
    // click or Escape (a native <details> won't do that on its own).
    document.querySelectorAll('.dash-kebab').forEach((kebab) => {
      const panels = kebab.querySelectorAll('.dash-kebab-menu [data-panel]');
      const show = (name) => {
        panels.forEach((p) => { p.hidden = p.dataset.panel !== name; });
        if (name !== 'menu') {
          const inp = kebab.querySelector('[data-panel="' + name + '"] input');
          if (inp) requestAnimationFrame(() => inp.focus());
        }
      };
      kebab.querySelectorAll('[data-kebab-panel]').forEach((b) => {
        b.addEventListener('click', () => show(b.dataset.kebabPanel));
      });
      // Always (re)open on the action list, and reset to it on close.
      kebab.addEventListener('toggle', () => show('menu'));
    });
    document.querySelectorAll('.dash-kebab input[name="confirm_label"]').forEach((input) => {
      const expected = input.dataset.expected || '';
      const btn = document.getElementById(input.dataset.btnId || '');
      const sync = () => {
        if (btn) btn.disabled = input.value.trim() !== expected;
      };
      input.addEventListener('input', sync);
      sync();
    });
    document.addEventListener('click', (e) => {
      document.querySelectorAll('.dash-kebab[open]').forEach((k) => {
        if (!k.contains(e.target)) k.open = false;
      });
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.querySelectorAll('.dash-kebab[open]').forEach((k) => { k.open = false; });
      }
    });"""


_ADMIN_AVATAR_JS = """
function _avatarResize(file, size) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const c = document.createElement('canvas'); c.width = size; c.height = size;
      const ctx = c.getContext('2d');
      const s = Math.min(img.width, img.height);
      const sx = (img.width - s) / 2, sy = (img.height - s) / 2;
      ctx.drawImage(img, sx, sy, s, s, 0, 0, size, size);
      resolve(c.toDataURL('image/jpeg', 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read image')); };
    img.src = url;
  });
}
document.querySelectorAll('.avatar-file').forEach(inp => {
  inp.addEventListener('change', async () => {
    const file = inp.files && inp.files[0];
    if (!file) return;
    const uid = inp.dataset.userId;
    const label = inp.closest('.avatar');
    if (label) label.classList.add('is-uploading');
    try {
      const dataUri = await _avatarResize(file, 160);
      const r = await fetch('/admin/users/' + uid + '/avatar', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ avatar: dataUri }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.ok) throw new Error(body.error || ('HTTP ' + r.status));
      const holder = document.getElementById('avimg-' + uid);
      if (holder) {
        const img = document.createElement('img');
        img.src = body.avatar; img.alt = ''; img.className = 'avatar-img'; img.id = 'avimg-' + uid;
        holder.replaceWith(img);
      }
    } catch (err) {
      alert('Avatar upload failed: ' + (err.message || err));
    } finally {
      if (label) label.classList.remove('is-uploading');
      inp.value = '';
    }
  });
});
document.querySelectorAll('.dash-logo-file').forEach(inp => {
  inp.addEventListener('change', async () => {
    const file = inp.files && inp.files[0];
    if (!file) return;
    const slug = inp.dataset.slug;
    const label = inp.closest('.dash-logo');
    if (label) label.classList.add('is-uploading');
    try {
      const dataUri = await _avatarResize(file, 160);
      const r = await fetch('/admin/dashboards/' + encodeURIComponent(slug) + '/logo', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ logo: dataUri }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.ok) throw new Error(body.error || ('HTTP ' + r.status));
      const holder = document.getElementById('dashlogo-' + slug);
      if (holder) {
        const img = document.createElement('img');
        img.src = body.logo; img.alt = ''; img.className = 'logo-img'; img.id = 'dashlogo-' + slug;
        holder.replaceWith(img);
      }
    } catch (err) {
      alert('Logo upload failed: ' + (err.message || err));
    } finally {
      if (label) label.classList.remove('is-uploading');
      inp.value = '';
    }
  });
});
"""


# Inline icons for the row kebab menus and the avatar hover affordance. Kept as
# module constants so a 200-row roster doesn't rebuild the same markup per row.
_SVG_KEBAB = (
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">'
    '<circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/></svg>'
)
_SVG_STROKE = (
    'viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
)
_SVG_BACK = (
    f'<svg {_SVG_STROKE} aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>'
)
_SVG_KEY = (
    f'<svg {_SVG_STROKE} aria-hidden="true"><circle cx="7.5" cy="15.5" r="4.5"/>'
    '<path d="m10.7 12.3 8.6-8.6"/><path d="m17 6 3 3"/><path d="m14 9 3 3"/></svg>'
)
_SVG_MAIL = (
    f'<svg {_SVG_STROKE} aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/>'
    '<path d="m3.5 6.5 8.5 6 8.5-6"/></svg>'
)
_SVG_SHIELD = (
    f'<svg {_SVG_STROKE} aria-hidden="true">'
    '<path d="M12 3l7.5 3v5.5c0 4.4-3.1 8.2-7.5 9.5-4.4-1.3-7.5-5.1-7.5-9.5V6z"/>'
    '<path d="m9.2 12.2 2 2 3.6-3.9"/></svg>'
)
_SVG_BAN = (
    f'<svg {_SVG_STROKE} aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
    '<path d="m5.6 5.6 12.8 12.8"/></svg>'
)
_SVG_PENCIL = (
    f'<svg {_SVG_STROKE} aria-hidden="true"><path d="M12 20h9"/>'
    '<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>'
)
_SVG_TRASH = (
    f'<svg {_SVG_STROKE} aria-hidden="true"><path d="M3 6h18"/>'
    '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>'
)
_SVG_PLUS = (
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 5v14"/><path d="M5 12h14"/></svg>'
)
_SVG_SEARCH = (
    f'<svg {_SVG_STROKE} aria-hidden="true"><circle cx="11" cy="11" r="7"/>'
    '<path d="m21 21-4.3-4.3"/></svg>'
)
_SVG_PENCIL_SM = (
    '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>'
)


def _label_initials(label: str) -> str:
    """Up to two initials from a display label (dashboard card icon)."""
    parts = [p for p in (label or "").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return ((label or "?")[:2]).upper()


def _user_initials(email: str) -> str:
    """Up to two initials from an email's local part (fallback avatar)."""
    local = (email or "").split("@")[0]
    for sep in (".", "-", "_", "+"):
        local = local.replace(sep, " ")
    parts = [p for p in local.split(" ") if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (local[:2] or "?").upper()


def render_admin_page(
    *,
    user: WebUser,
    users: list[dict],
    groups: list[dict] | None = None,
    audit_events: list[dict] | None = None,
    message: str | None = None,
    error: str | None = None,
    oauth_section_html: str = "",
    credentials_section_html: str = "",
    is_super_admin: bool = False,
    dashboard_cache_ttl: int = 0,
    page: str = "clients",
    invite_link: str | None = None,
    invite_email: str | None = None,
    invite_expires_in: str | None = None,
) -> str:
    """Render one admin destination.

    The old single Overview has been split into focused pages, selected by
    ``page``: ``clients`` (dashboards register), ``users`` (team + client
    logins + groups), ``feature-requests`` (the team inbox), and ``advanced``
    (debug / platform settings). All sections are still built below regardless;
    ``page`` only chooses which get placed into the rendered body.
    """
    page = (page or "clients").strip().lower()
    if page not in ("clients", "users", "feature-requests", "advanced"):
        page = "clients"
    notice = ""
    if message:
        notice += f'<div class="notice ok">{_esc(message)}</div>'
    if error:
        notice += f'<div class="notice err">{_esc(error)}</div>'

    # Client groups the admin can assign 'client' users to. Fetched here when the
    # caller didn't pass them so error re-renders don't have to thread it through.
    if groups is None:
        try:
            groups = web_users.list_groups(include_inactive=False)
        except Exception:
            groups = []

    # Available clients for the per-client access checkboxes (standard users and
    # group dashboards). Sourced from the dashboard registry so it matches what
    # can_access_client gates on.
    client_choices: list[tuple[str, str]] = []
    try:
        import dashboard_registry as _dreg

        if _dreg.enabled():
            client_choices = [
                (r.client_slug, r.label or r.client_slug) for r in _dreg.list_clients()
            ]
    except Exception:
        client_choices = []
    client_labels = {slug: label for slug, label in client_choices}

    def _client_checkboxes(
        selected: set[str], *, field: str = "allowed_client_slugs"
    ) -> str:
        if not client_choices:
            return '<p class="muted" style="margin:.35rem 0 0">No dashboards yet.</p>'
        items = []
        for cslug, clabel in client_choices:
            checked = " checked" if cslug in selected else ""
            items.append(
                f'<label class="ckbx"><input type="checkbox" name="{field}" '
                f'value="{_esc(cslug)}"{checked}><span>{_esc(clabel)}</span></label>'
            )
        return '<div class="client-checks">' + "".join(items) + "</div>"

    def _group_labels(g: dict) -> list[str]:
        return [client_labels.get(s, s) for s in (g.get("client_slugs") or [])]

    def _group_select(selected_id: int | None) -> str:
        opts = ['<option value="" data-labels="">Ungrouped — use a single client slug</option>']
        for g in groups or []:
            labels = _group_labels(g)
            n = len(labels)
            plural = "" if n == 1 else "s"
            sel = " selected" if selected_id is not None and int(g["id"]) == int(selected_id) else ""
            data_labels = _esc(" · ".join(labels))
            opts.append(
                f'<option value="{int(g["id"])}"{sel} data-labels="{data_labels}">'
                f'{_esc(g["name"])} &middot; {n} dashboard{plural}</option>'
            )
        return (
            '<select name="group_id" class="group-select">'
            + "".join(opts)
            + "</select>"
            + '<div class="group-preview" aria-live="polite" hidden></div>'
        )

    def _labels_for(slugs) -> list[str]:
        return [client_labels.get(s, s) for s in (slugs or [])]

    def _access_chips(labels: list[str], *, empty: str) -> str:
        if not labels:
            return f'<span class="chip-none">{_esc(empty)}</span>'
        return '<div class="chip-row">' + "".join(
            f'<span class="access-chip">{_esc(lbl)}</span>' for lbl in labels
        ) + "</div>"

    def _user_row(u: dict, kind: str) -> str:
        """Render one user <tr>. ``kind`` is 'team' (admin/standard, internal)
        or 'client' (external portal login); the two tables carry columns tuned
        to their audience."""
        uid = int(u["id"])
        email = str(u.get("email") or "")
        role = str(u.get("role") or "")
        group_name = u.get("group_name")
        avatar = u.get("avatar")
        av_inner = (
            f'<img src="{_esc(str(avatar))}" alt="" class="avatar-img" id="avimg-{uid}">'
            if avatar
            else f'<span class="avatar-initials" id="avimg-{uid}">{_esc(_user_initials(email))}</span>'
        )
        avatar_cell = (
            f'<label class="avatar" title="Upload headshot">'
            f'<input type="file" accept="image/*" class="avatar-file" data-user-id="{uid}" hidden>'
            f'{av_inner}'
            f'<span class="avatar-edit" aria-hidden="true">{_SVG_PENCIL_SM}</span></label>'
        )
        invite_pending = bool(u.get("invite_pending"))
        if not u.get("is_active"):
            status_badge = '<span class="pill pill-off">Inactive</span>'
        elif invite_pending:
            # The account exists and is scoped, but has no usable password until
            # the invite is redeemed — worth calling out so an admin doesn't
            # wonder why the user has never signed in.
            status_badge = (
                '<span class="pill pill-invite" title="Waiting on the invite link to be '
                'redeemed — this account cannot sign in yet">Invite pending</span>'
            )
        else:
            status_badge = '<span class="pill pill-on">Active</span>'
        # Prefer last activity (any authenticated request) over last login: a
        # long-lived session means a week-old login can hide someone who's been
        # back every day. Fall back to the login stamp for rows recorded before
        # activity tracking existed.
        last_activity = u.get("last_seen_at") or u.get("last_login_at")
        ll_rel, ll_abs = _format_last_seen(last_activity)
        never = last_activity is None
        ll_cls = "last-login never" if never else "last-login"
        last_login_cell = (
            f'<span class="{ll_cls}" title="{_esc(ll_abs)}">{_esc(ll_rel)}</span>'
        )

        # ---- Row actions: one kebab (vertical dots) menu, not four loose links ----
        # Every action opens a panel inside that menu, so a row never grows a
        # stack of half-open forms in the middle of the table. Same component as
        # the Accounts rows, so both registers are operated the same way.
        items: list[str] = []
        panels: list[str] = []

        items.append(
            '<button type="button" class="dash-kebab-item" role="menuitem" '
            f'data-kebab-panel="pw-{uid}">{_SVG_KEY}<span>Reset password…</span></button>'
        )
        panels.append(f"""
                <form method="post" action="/admin/users/{uid}/reset-password" class="dash-kebab-panel" data-panel="pw-{uid}" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_SVG_BACK}<span>Back</span></button>
                  <label class="dash-kebab-label">New password</label>
                  <input type="password" name="new_password" placeholder="At least 10 characters"
                    minlength="10" required autocomplete="new-password" aria-label="New password">
                  <p class="hint">Takes effect immediately — tell them the new password yourself,
                    or send an invite link instead and let them pick one.</p>
                  <button type="submit" class="dash-kebab-submit primary">Reset password</button>
                </form>""")

        # Mint a fresh link — for an invite that was never redeemed, one the
        # admin lost (the raw token is unrecoverable by design), or as a
        # password reset the admin doesn't have to choose a password for.
        invite_label = "New invite link" if invite_pending else "Send invite link"
        invite_note = (
            "Generates a fresh one-time link. Any previous link for this user stops working."
            if invite_pending
            else "Generates a one-time link they use to set their own password. Any previous "
            "link stops working; their current password keeps working until they redeem it."
        )
        items.append(
            '<button type="button" class="dash-kebab-item" role="menuitem" '
            f'data-kebab-panel="invite-{uid}">{_SVG_MAIL}<span>{invite_label}…</span></button>'
        )
        panels.append(f"""
                <form method="post" action="/admin/users/{uid}/invite" class="dash-kebab-panel" data-panel="invite-{uid}" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_SVG_BACK}<span>Back</span></button>
                  <label class="dash-kebab-label">{_esc(invite_label)}</label>
                  <p class="hint">{_esc(invite_note)}</p>
                  <button type="submit" class="dash-kebab-submit primary">{_esc(invite_label)}</button>
                </form>""")

        if uid != user.id:
            raw_slug = _esc(u.get("client_slug") or "")
            role_opts = "".join(
                f'<option value="{r}"{" selected" if role == r else ""}>{r}</option>'
                for r in ("admin", "client", "standard")
            )
            row_checks = _client_checkboxes(set(u.get("allowed_client_slugs") or []))
            row_group_select = _group_select(u.get("group_id"))
            items.append(
                '<button type="button" class="dash-kebab-item" role="menuitem" '
                f'data-kebab-panel="role-{uid}">{_SVG_SHIELD}<span>Role &amp; access…</span></button>'
            )
            panels.append(f"""
                <form method="post" action="/admin/users/{uid}/role" class="dash-kebab-panel role-form" data-panel="role-{uid}" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_SVG_BACK}<span>Back</span></button>
                  <label class="dash-kebab-label">Role</label>
                  <select name="role" aria-label="Role" class="role-select">{role_opts}</select>
                  <div class="client-only">
                    <span class="dash-kebab-label">Client group</span>
                    {row_group_select}
                    <input type="text" name="client_slug" class="slug-fallback"
                      placeholder="or a single client slug"
                      value="{raw_slug}" autocomplete="off">
                  </div>
                  <div class="standard-only">
                    <span class="dash-kebab-label">Clients this user can access</span>
                    {row_checks}
                  </div>
                  <button type="submit" class="dash-kebab-submit primary">Save role &amp; access</button>
                </form>""")
            items.append(
                '<button type="button" class="dash-kebab-item danger" role="menuitem" '
                f'data-kebab-panel="off-{uid}">{_SVG_BAN}<span>Deactivate…</span></button>'
            )
            panels.append(f"""
                <form method="post" action="/admin/users/{uid}/deactivate" class="dash-kebab-panel" data-panel="off-{uid}" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_SVG_BACK}<span>Back</span></button>
                  <p class="hint">Deactivate <strong>{_esc(email)}</strong>? They are signed out and
                    can no longer sign in. The account and its access settings are kept.</p>
                  <button type="submit" class="dash-kebab-submit danger">Deactivate user</button>
                </form>""")
        else:
            # An admin can reset their own password but not re-role or disable
            # themselves — say so in the menu rather than leaving it half-empty.
            items.append(
                '<div class="dash-kebab-note">Your own role can only be changed '
                "by another admin</div>"
            )

        actions = f"""
            <details class="dash-kebab user-kebab">
              <summary class="dash-icon-btn" title="Actions" aria-label="Actions for {_esc(email)}" aria-haspopup="menu">{_SVG_KEBAB}</summary>
              <div class="dash-kebab-menu">
                <div class="dash-kebab-list" data-panel="menu" role="menu">{"".join(items)}</div>
                {"".join(panels)}
              </div>
            </details>"""

        # Resolve the human-readable dashboards this user can reach (used for
        # both the visible access cell and the search key).
        if role == "admin":
            access_labels = ["All clients"]
        elif role == "standard":
            access_labels = _labels_for(u.get("allowed_client_slugs"))
        elif u.get("group_id"):
            access_labels = _labels_for(u.get("group_client_slugs"))
        else:
            single = u.get("client_slug")
            access_labels = _labels_for([single] if single else [])
        search_key = _esc(
            " ".join(
                filter(
                    None,
                    (email, role, str(group_name or ""), " ".join(access_labels)),
                )
            ).lower()
        )

        # Avatar and email ride in one identity cell, so the row opens with a
        # person rather than with a picture in a column of its own.
        you_chip = '<span class="you-chip">You</span>' if uid == user.id else ""
        identity_cell = (
            f'<td class="col-user" data-label="User"><div class="user-cell">{avatar_cell}'
            f'<span class="user-ident"><span class="user-email">{_esc(email)}</span>'
            f'{you_chip}</span></div></td>'
        )

        if kind == "team":
            role_badge = f'<span class="role-badge role-{_esc(role)}">{_esc(role)}</span>'
            access_cell = (
                '<span class="chip-all">All clients</span>'
                if role == "admin"
                else _access_chips(access_labels, empty="No access")
            )
            cells = (
                f"{identity_cell}"
                f'<td data-label="Role">{role_badge}</td>'
                f'<td class="col-access" data-label="Client access">{access_cell}</td>'
                f'<td data-label="Last session">{last_login_cell}</td>'
                f'<td data-label="Status">{status_badge}</td>'
                f'<td class="col-actions">{actions}</td>'
            )
        else:  # client
            group_cell = (
                f'<span class="group-badge">{_esc(str(group_name))}</span>'
                if group_name
                else '<span class="chip-none">Ungrouped</span>'
            )
            access_cell = _access_chips(access_labels, empty="No dashboards")
            cells = (
                f"{identity_cell}"
                f'<td data-label="Group">{group_cell}</td>'
                f'<td class="col-access" data-label="Dashboards">{access_cell}</td>'
                f'<td data-label="Last session">{last_login_cell}</td>'
                f'<td data-label="Status">{status_badge}</td>'
                f'<td class="col-actions">{actions}</td>'
            )
        return f'<tr class="user-row" data-search="{search_key}">{cells}</tr>'

    # Split the roster in two: internal Sagefrog accounts (admin + standard) vs
    # external client portal logins. Each gets its own audience-tuned table so
    # the two audiences never blur together.
    team_rows = [_user_row(u, "team") for u in users if str(u.get("role") or "") != "client"]
    client_rows = [_user_row(u, "client") for u in users if str(u.get("role") or "") == "client"]
    team_count = len(team_rows)
    client_count = len(client_rows)
    user_count = len(users)
    team_body = "\n".join(team_rows) or (
        '<tr class="empty-row"><td colspan="6" class="muted">No Sagefrog team members yet.</td></tr>'
    )
    client_body = "\n".join(client_rows) or (
        '<tr class="empty-row"><td colspan="6" class="muted">No client portal users yet.</td></tr>'
    )

    # NOTE: the "View as user" card that used to live here has been removed —
    # impersonation is now offered per-client from the client shell's "View As"
    # tool tab (see admin_tool_card_html), so a global picker on this page is
    # redundant. The /admin/view-as backend route stays for that per-client tab.

    # ---- Client groups management section ----
    group_rows_html = []
    for g in groups or []:
        gid = int(g["id"])
        gname = _esc(g["name"])
        gslugs = g.get("client_slugs") or []
        members = int(g.get("member_count") or 0)
        chips = "".join(
            f'<span class="grp-chip">{_esc(client_labels.get(s, s))}</span>' for s in gslugs
        ) or '<span class="chip-none">No dashboards yet</span>'
        member_note = f"{members} member" + ("" if members == 1 else "s")
        # A group with members still grants access, so deleting it would silently
        # strip those logins — offer the delete only once it's empty, and say why.
        if members == 0:
            delete_item = (
                '<button type="button" class="dash-kebab-item danger" role="menuitem" '
                f'data-kebab-panel="gdel-{gid}">{_SVG_TRASH}<span>Delete group…</span></button>'
            )
            delete_panel = f"""
                <form method="post" action="/admin/groups/{gid}/delete" class="dash-kebab-panel" data-panel="gdel-{gid}" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_SVG_BACK}<span>Back</span></button>
                  <p class="hint">Delete <strong>{gname}</strong>? It has no members, so no
                    login loses access.</p>
                  <button type="submit" class="dash-kebab-submit danger">Delete group</button>
                </form>"""
        else:
            delete_item = (
                '<div class="dash-kebab-note">Reassign its members before '
                "deleting this group</div>"
            )
            delete_panel = ""
        edit_checks = _client_checkboxes(set(gslugs), field="client_slugs")
        desc_val = _esc(g.get("description") or "")
        desc_line = (
            f'<span class="group-row-desc">{desc_val}</span>' if desc_val else ""
        )
        group_rows_html.append(f"""
        <div class="group-row">
          <div class="group-row-head">
            <div class="group-row-main">
              <span class="group-row-name">{gname}</span>
              <span class="group-row-meta">{member_note}{desc_line}</span>
            </div>
            <div class="group-chips">{chips}</div>
            <div class="group-row-actions">
              <details class="dash-kebab group-kebab">
                <summary class="dash-icon-btn" title="Actions" aria-label="Actions for {gname}" aria-haspopup="menu">{_SVG_KEBAB}</summary>
                <div class="dash-kebab-menu">
                  <div class="dash-kebab-list" data-panel="menu" role="menu">
                    <button type="button" class="dash-kebab-item" role="menuitem" data-kebab-panel="gedit-{gid}">{_SVG_PENCIL}<span>Edit group…</span></button>
                    {delete_item}
                  </div>
                  <form method="post" action="/admin/groups/{gid}" class="dash-kebab-panel" data-panel="gedit-{gid}" hidden>
                    <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_SVG_BACK}<span>Back</span></button>
                    <label class="dash-kebab-label">Group name</label>
                    <input type="text" name="name" value="{gname}" required maxlength="120">
                    <label class="dash-kebab-label">Description</label>
                    <input type="text" name="description" value="{desc_val}" maxlength="200"
                      placeholder="optional">
                    <span class="dash-kebab-label">Dashboards in this group</span>
                    {edit_checks}
                    <button type="submit" class="dash-kebab-submit primary">Save group</button>
                  </form>
                  {delete_panel}
                </div>
              </details>
            </div>
          </div>
        </div>""")
    group_list_html = "".join(group_rows_html) or (
        '<p class="muted" style="margin:0">No client groups yet. Create one to bundle '
        "dashboards and assign client users to it.</p>"
    )
    add_group_checks = _client_checkboxes(set(), field="client_slugs")
    groups_section_html = f"""
    <section class="dash-section groups-section">
      <div class="dash-section-head">
        <h2>Client groups</h2>
        <details class="dash-add-fold">
          <summary class="add-dash-btn"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>Add group</summary>
          <form method="post" action="/admin/groups" class="dash-add-form">
            <label for="group_name">Group name</label>
            <input id="group_name" name="name" type="text" required maxlength="120"
              placeholder="Penn Medical">
            <label for="group_desc" style="margin-top:8px">Description (optional)</label>
            <input id="group_desc" name="description" type="text" maxlength="200">
            <span class="ckbx-legend">Dashboards in this group</span>
            {add_group_checks}
            <button type="submit" class="primary">Create group</button>
          </form>
        </details>
      </div>
      <p class="muted" style="margin:-4px 0 14px;font-size:.86rem">Bundle one or more dashboards
        into a group, then assign client users to it — access comes from the group, so there's
        no slug to mistype.</p>
      <div class="group-list">{group_list_html}</div>
    </section>"""

    audit_rows = []
    for ev in audit_events or []:
        when = _format_audit_time(ev.get("created_at"))
        label = _esc(ev.get("action_label") or ev.get("action") or "")
        actor = _esc(ev.get("actor_email") or "—")
        detail = _esc(audit_log.format_detail(ev))
        ip = _esc(ev.get("ip_address") or "—")
        audit_rows.append(
            f"<tr><td>{when}</td><td>{label}</td><td>{actor}</td>"
            f"<td>{detail}</td><td class=\"mono\">{ip}</td></tr>"
        )
    audit_table = (
        "\n".join(audit_rows)
        or '<tr><td colspan="5" class="muted">No events yet.</td></tr>'
    )

    dashboard_manage_html = ""
    client_slug_options = ""
    client_slug_datalist = ""
    try:
        import dashboard_registry

        if dashboard_registry.enabled():
            dash_rows = []
            for row in dashboard_registry.list_clients():
                slug = row.client_slug
                label = row.label
                initials = _esc(_label_initials(label))
                logo = getattr(row, "logo", None)
                logo_inner = (
                    f'<img src="{_esc(str(logo))}" alt="" class="logo-img" id="dashlogo-{_esc(slug)}">'
                    if logo
                    else f'<span class="logo-initials" id="dashlogo-{_esc(slug)}">{initials}</span>'
                )
                logo_cell = (
                    f'<label class="dash-logo" title="Upload logo">'
                    f'<input type="file" accept="image/*" class="dash-logo-file" data-slug="{_esc(slug)}" hidden>'
                    f'{logo_inner}'
                    f'<span class="logo-edit" aria-hidden="true"><svg viewBox="0 0 24 24" width="10" height="10" '
                    f'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                    f'<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg></span></label>'
                )

                # All row actions live in one kebab (⋮) menu: Open, Rename, and —
                # for super admins — Delete. Rename/Delete swap the menu for an
                # inline panel (see the kebab JS) so no popover floats loose over
                # the card grid.
                _svg_kebab = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">'
                             '<circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/></svg>')
                _svg_open = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                            '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/></svg>')
                _svg_pencil = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                              '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>')
                _svg_trash = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                             '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>')
                _svg_back = ('<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                            '<path d="M15 18l-6-6 6-6"/></svg>')
                _svg_tag = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                           '<path d="M20.6 13.4 12 22l-9-9V4a1 1 0 0 1 1-1h8z"/><circle cx="7.5" cy="7.5" r="1.3"/></svg>')

                # Industry tags: a chip per bucket on the row (so the roster
                # reads as a bucketed book at a glance) plus a kebab panel to
                # change them. An account can sit in several buckets — plenty of
                # B2B clients genuinely straddle two markets — so the panel is a
                # checkbox list, not a single-choice dropdown. The taxonomy comes
                # from client_industries; nothing is hardcoded here, so adding a
                # bucket needs no change to this file.
                industry_keys = tuple(getattr(row, "industries", ()) or ())
                industry_labels = client_industries.labels_for(industry_keys)
                industry_chip = (
                    '<span class="dash-industries">'
                    + (
                        "".join(
                            f'<span class="dash-industry" title="Industry — used for agency benchmarks">'
                            f"{_esc(name)}</span>"
                            for name in industry_labels
                        )
                        if industry_keys
                        else '<span class="dash-industry unset" title="No industry set — this account is '
                             'left out of the per-industry benchmarks">Unassigned</span>'
                    )
                    + "</span>"
                )
                industry_options = "".join(
                    f'<label class="dash-kebab-check">'
                    f'<input type="checkbox" name="industries" value="{_esc(key)}"'
                    f'{" checked" if key in industry_keys else ""}>'
                    f"<span>{_esc(name)}</span></label>"
                    for key, name in client_industries.choices()
                )
                industry_panel = f"""
                <form method="post" action="/admin/dashboards/{_esc(slug)}/industry" class="dash-kebab-panel" data-panel="industry" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_svg_back}<span>Back</span></button>
                  <label class="dash-kebab-label">Industries</label>
                  <div class="dash-kebab-checks" role="group" aria-label="Industries">
                    {industry_options}
                  </div>
                  <p class="hint">Pick every bucket this account belongs in — it is benchmarked
                    against each on the <a href="/admin/benchmarks">Benchmarks</a> page.
                    Tick none to leave it Unassigned.</p>
                  <button type="submit" class="dash-kebab-submit primary">Save industries</button>
                </form>"""

                # Delete is destructive → super admins only; "penn" is protected.
                if slug == "penn":
                    delete_item = '<div class="dash-kebab-note">Protected — can’t be deleted</div>'
                    delete_panel = ""
                elif not is_super_admin:
                    delete_item = ""
                    delete_panel = ""
                else:
                    field_id = f"confirm-{slug.replace('-', '_')}"
                    btn_id = f"delete-btn-{slug.replace('-', '_')}"
                    delete_item = (
                        '<button type="button" class="dash-kebab-item danger" role="menuitem" data-kebab-panel="delete">'
                        f'{_svg_trash}<span>Delete…</span></button>'
                    )
                    delete_panel = f"""
                <form method="post" action="/admin/dashboards/{_esc(slug)}/delete" class="dash-kebab-panel" data-panel="delete" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_svg_back}<span>Back</span></button>
                  <p class="hint">Type <strong>{_esc(label)}</strong> to confirm deletion.</p>
                  <input type="text" id="{field_id}" name="confirm_label" placeholder="{_esc(label)}"
                    autocomplete="off" data-expected="{_esc(label)}" data-btn-id="{btn_id}">
                  <button type="submit" id="{btn_id}" class="dash-kebab-submit danger" disabled>Delete dashboard</button>
                </form>"""

                # Every industry joins the filter text so "Filter accounts…"
                # doubles as "show me the manufacturing book" — and a
                # multi-tagged account turns up under either of its buckets.
                search_terms = " ".join(industry_labels) or client_industries.UNASSIGNED_LABEL
                search_key = _esc(f"{label} {slug} {search_terms}".lower())
                dash_rows.append(f"""
        <div class="dash-row" data-search="{search_key}">
          {logo_cell}
          <a class="dash-row-main" href="/dashboard/{_esc(slug)}">
            <span class="dash-row-name">{_esc(label)}</span>
            <span class="dash-row-slug mono">/dashboard/{_esc(slug)}</span>
            {industry_chip}
          </a>
          <div class="dash-row-actions">
            <details class="dash-kebab">
              <summary class="dash-icon-btn" title="Actions" aria-label="Actions" aria-haspopup="menu">{_svg_kebab}</summary>
              <div class="dash-kebab-menu">
                <div class="dash-kebab-list" data-panel="menu" role="menu">
                  <a class="dash-kebab-item" role="menuitem" href="/dashboard/{_esc(slug)}">{_svg_open}<span>Open dashboard</span></a>
                  <button type="button" class="dash-kebab-item" role="menuitem" data-kebab-panel="rename">{_svg_pencil}<span>Rename…</span></button>
                  <button type="button" class="dash-kebab-item" role="menuitem" data-kebab-panel="industry">{_svg_tag}<span>Industry…</span></button>
                  {delete_item}
                </div>
                <form method="post" action="/admin/dashboards/{_esc(slug)}/rename" class="dash-kebab-panel" data-panel="rename" hidden>
                  <button type="button" class="dash-kebab-back" data-kebab-panel="menu">{_svg_back}<span>Back</span></button>
                  <label class="dash-kebab-label">Display name</label>
                  <input type="text" name="label" value="{_esc(label)}" maxlength="120" required autocomplete="off" aria-label="New display name">
                  <p class="hint">The URL <code>/dashboard/{_esc(slug)}</code> stays the same.</p>
                  <button type="submit" class="dash-kebab-submit primary">Save name</button>
                </form>
                {industry_panel}
                {delete_panel}
              </div>
            </details>
          </div>
        </div>""")
            dash_count = len(dash_rows)
            dash_list = (
                "".join(dash_rows)
                or '<p class="muted" style="margin:0">No dashboards yet.</p>'
            )
            for slug, _label in client_config.list_dashboard_clients():
                client_slug_options += f'<option value="{_esc(slug)}"></option>\n'
            if client_slug_options:
                client_slug_datalist = f'<datalist id="clientSlugOptions">{client_slug_options}</datalist>'
            dashboard_manage_html = f"""
    <section class="dash-section">
      <div class="dash-section-head">
        <h2 style="margin:0">All accounts <span class="count-chip" id="dashCount">{dash_count}</span></h2>
        <div class="dash-head-right">
          <div class="dash-search">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
            <input type="search" id="dashSearch" placeholder="Filter accounts…"
              autocomplete="off" aria-label="Filter accounts">
          </div>
          <details class="dash-add-fold">
            <summary class="add-dash-btn"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>Add account</summary>
            <form method="post" action="/admin/dashboards" class="dash-add-form">
              <div class="row">
                <div>
                  <label for="dash_slug">Slug</label>
                  <input id="dash_slug" name="client_slug" type="text" required
                    pattern="[a-z0-9-]+" placeholder="nixon" maxlength="64">
                  <p class="hint">Lowercase URL segment, e.g. <code>nixon</code> → /dashboard/nixon</p>
                </div>
                <div>
                  <label for="dash_label">Display name</label>
                  <input id="dash_label" name="label" type="text" required maxlength="120"
                    placeholder="Nixon Medical">
                </div>
              </div>
              <button type="submit" class="primary">Add account</button>
            </form>
          </details>
        </div>
      </div>
      <div class="dash-list">{dash_list}</div>
      <p class="dash-empty muted" id="dashEmpty" hidden>No accounts match your filter.</p>
    </section>"""
        else:
            dashboard_links = "\n".join(
                f'        <li><a href="/dashboard/{_esc(slug)}">{_esc(label)}</a></li>'
                for slug, label in client_config.list_dashboard_clients()
            ) or '        <li class="muted">No dashboards configured.</li>'
            dashboard_manage_html = f"""
    <section>
      <h2>Dashboards</h2>
      <p class="muted">Connect DATABASE_URL to add or remove dashboards from Admin.</p>
      <ul class="links">
{dashboard_links}
      </ul>
    </section>"""
    except Exception:
        dashboard_links = "\n".join(
            f'        <li><a href="/dashboard/{_esc(slug)}">{_esc(label)}</a></li>'
            for slug, label in client_config.list_dashboard_clients()
        ) or '        <li class="muted">No dashboards configured.</li>'
        dashboard_manage_html = f"""
    <section>
      <h2>Dashboards</h2>
      <ul class="links">
{dashboard_links}
      </ul>
    </section>"""

    # ---- Feature requests inbox (super admins only) ----
    # Requests raised from the floating notes FAB on any client dashboard land
    # here, on the Notifications page. They are intentionally NOT surfaced as a
    # cross-page banner — the inbox lives only on its own page.
    feature_requests_html = ""
    if is_super_admin:
        try:
            import feature_requests as _freq

            reqs = _freq.list_requests(limit=200)
        except Exception:
            reqs = []
        new_reqs = [r for r in reqs if r.status == "new"]
        new_count = len(new_reqs)

        def _fr_row(r) -> str:
            when = _format_audit_time(r.created_at)
            who = _esc(r.created_by or "—")
            page_label = r.page_label or r.client_slug or "—"
            page_path = r.page_path or ""
            page_link = (
                f'<a class="fr-page-link" href="{_esc(page_path)}">{_esc(str(page_label))}</a>'
                if page_path
                else f'<span>{_esc(str(page_label))}</span>'
            )
            path_note = (
                f'<span class="fr-path mono">{_esc(page_path)}</span>' if page_path else ""
            )
            is_new = r.status == "new"
            badge = (
                '<span class="fr-badge fr-badge-new">New</span>'
                if is_new
                else '<span class="fr-badge fr-badge-done">Done</span>'
            )
            # Scope: a "client" request is only for the dashboard it came from;
            # anything else is a global ask against the shared dashboard.
            if r.scope == "client":
                scope_slug = _esc(r.client_slug or "this client")
                scope_badge = (
                    f'<span class="fr-badge fr-badge-scope-client" '
                    f'title="Requested for {scope_slug} only">{scope_slug} only</span>'
                )
            else:
                scope_badge = (
                    '<span class="fr-badge fr-badge-scope-global" '
                    'title="Applies to every client dashboard">Global</span>'
                )
            if is_new:
                primary = (
                    f'<form method="post" action="/admin/feature-requests/{r.id}/done" class="inline-form">'
                    f'<button type="submit" class="link">Mark done</button></form>'
                )
            else:
                resolved = _esc(r.resolved_by or "")
                by = f" by {resolved}" if resolved else ""
                primary = f'<span class="fr-resolved">Resolved{by}</span>'
            # Archive dismisses the request from the inbox (keeps the row);
            # delete removes it for good behind a confirm.
            archive_action = (
                f'<form method="post" action="/admin/feature-requests/{r.id}/archive" class="inline-form">'
                f'<button type="submit" class="link fr-archive">Archive</button></form>'
            )
            delete_action = (
                f'<form method="post" action="/admin/feature-requests/{r.id}/delete" class="inline-form" '
                f"onsubmit=\"return confirm('Delete this feature request? This can\\'t be undone.');\">"
                f'<button type="submit" class="link fr-delete">Delete</button></form>'
            )
            action = f"{primary}{archive_action}{delete_action}"
            return f"""
        <div class="fr-row {'is-new' if is_new else 'is-done'}">
          <div class="fr-row-head">
            <div class="fr-row-where">{badge}{scope_badge}{page_link}</div>
            <div class="fr-row-actions">{action}</div>
          </div>
          {path_note}
          <p class="fr-body">{_esc(r.body)}</p>
          <div class="fr-row-meta"><span>{who}</span><span>{when}</span></div>
        </div>"""

        fr_rows = "".join(_fr_row(r) for r in reqs) or (
            '<p class="muted" style="margin:0">No feature requests yet. They arrive '
            "from the notes FAB on any client dashboard.</p>"
        )
        count_chip = (
            f'<span class="count-chip fr-count-new">{new_count} new</span>'
            if new_count
            else '<span class="count-chip">0 new</span>'
        )
        feature_requests_html = f"""
    <section class="fr-section" id="feature-requests">
      <div class="dash-section-head">
        <h2 style="margin:0">Feature requests {count_chip}</h2>
      </div>
      <p class="muted" style="margin:-4px 0 14px;font-size:.86rem">Raised by the team
        from the notes FAB on client dashboards. Each one records the page it came from.</p>
      <div class="fr-list">{fr_rows}</div>
    </section>"""

    # ---- Dashboard performance (super admins only) ----
    # Instance-wide read-cache TTL floor for the BigQuery dashboard cards. A
    # longer floor keeps warmed/viewed cards warm between syncs; a sync
    # invalidates the cache immediately, so it adds no staleness. See
    # app_settings.dashboard_cache_ttl_seconds and api_routes._cached_bq_read.
    dashboard_perf_html = ""
    if is_super_admin:
        try:
            import app_settings

            choices = app_settings.DASHBOARD_CACHE_TTL_CHOICES
        except Exception:
            choices = [(0, "Default (per-card, ~15 min)")]
        opts = "".join(
            f'<option value="{secs}"{" selected" if int(dashboard_cache_ttl) == secs else ""}>'
            f"{_esc(label)}</option>"
            for secs, label in choices
        )
        dashboard_perf_html = f"""
        <section class="dash-perf-section">
          <h2>Dashboard performance</h2>
          <p class="muted" style="margin:0 0 12px;font-size:.9rem">How long a client's
            dashboard cards stay cached between syncs. A longer window keeps the
            morning-synced Overview instant all day; because a sync clears the cache
            immediately, it never serves stale data — only the first view after a sync
            is ever slow.</p>
          <form method="post" action="/admin/settings/dashboard-cache" class="role-form">
            <div class="row">
              <div>
                <label for="dashboard_cache_ttl">Cache duration</label>
                <select id="dashboard_cache_ttl" name="ttl_seconds" class="role-select">
                  {opts}
                </select>
              </div>
            </div>
            <button type="submit" class="primary">Save</button>
          </form>
        </section>"""

    from dashboard.renderers.base_layout import render_admin_shell_page

    # Admin home renders inside the shared navy-sidebar shell (see
    # render_admin_shell_page): its component styles ride along as extra_css, the
    # sections as content, and the row/avatar scripts as body_end. The standalone
    # top bar is gone — HQ / Trends / Docs live in the sidebar, and the switcher
    # carries the "Admin panel" parent entry above every client.
    admin_css = f"""
    :root {{
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#34b27b; --danger:#b42318;
    }}
    body {{ color: var(--ink);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
      background: linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment: fixed; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 26px 24px 56px; }}
    /* The Accounts page runs wider so its account-card grid gets multiple
       columns. width:100% is required because <main> sits in a flex-column
       shell with margin:0 auto — without an explicit width it shrinks to fit
       its content instead of filling (and capping at) the wide canvas. */
    main.admin-main--wide {{ max-width: 1240px; width: 100%; }}
    /* Per-page header — the split-out admin destinations each get a clear title. */
    .admin-page-head {{ margin: 2px 0 18px; }}
    .admin-page-head h1 {{ margin: 0; font-size: 1.5rem; color: var(--navy); letter-spacing: -.01em; }}
    .admin-page-head p {{ margin: 6px 0 0; color: var(--muted); font-size: .92rem; max-width: 60ch; }}
    /* Keep the switcher search field clear of the generic input margins below. */
    .client-switch-search-input {{ margin-bottom: 0; }}
    section {{ background: #fff; border: 1px solid var(--line); border-radius: 16px;
      padding: 22px 24px; margin-bottom: 18px; box-shadow: 0 6px 22px rgba(10,37,64,.06); }}
    h2 {{ margin: 0 0 16px; font-size: 1.05rem; color: var(--navy); }}
    table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
    th, td {{ text-align: left; padding: 11px 8px; border-bottom: 1px solid var(--line); }}
    th {{ color: var(--muted); font-weight: 600; font-size: .78rem; text-transform: uppercase; letter-spacing: .3px; }}
    td.mono, th.mono {{ font-family: ui-monospace, monospace; font-size: .82rem; }}
    .audit-wrap {{ max-height: 420px; overflow: auto; }}
    label {{ display: block; font-size: .8rem; font-weight: 600; color: #334155; margin-bottom: 6px; }}
    input, select {{ width: 100%; max-width: 320px; padding: 10px 12px; border: 1px solid var(--line);
      border-radius: 10px; margin-bottom: 12px; font-size: .95rem; background: #fbfcfe;
      transition: border-color .15s, box-shadow .15s; }}
    input:focus, select:focus {{ outline: none; border-color: var(--accent); background: #fff;
      box-shadow: 0 0 0 3px rgba(37,99,235,.15); }}
    .row {{ display: flex; flex-wrap: wrap; gap: 16px; }}
    .row > div {{ flex: 1; min-width: 180px; }}
    button.primary {{ padding: 11px 20px; border: 0; border-radius: 10px; color: #fff; font-weight: 700;
      cursor: pointer; background: linear-gradient(135deg,var(--accent),var(--accent-d));
      box-shadow: 0 5px 14px rgba(37,99,235,.3); transition: filter .15s, transform .06s; }}
    button.primary:hover {{ filter: brightness(1.06); }}
    button.primary:active {{ transform: translateY(1px); }}
    button.link {{ background: none; border: 0; color: var(--accent); cursor: pointer; padding: 0; font-weight: 600; }}
    button.link.danger {{ color: #b42318; }}
    .notice {{ padding: 11px 14px; border-radius: 10px; margin-bottom: 16px; font-size: .9rem; }}
    .notice.ok {{ background: #e8f5e9; color: #1b5e20; }}
    .notice.err {{ background: #fdecea; color: #b42318; }}
    .muted {{ color: var(--muted); }}
    ul.links {{ margin: 0; padding-left: 1.2rem; }}
    ul.links a {{ color: var(--accent); }}
    .admin-oauth-section {{ background: #fff; border: 1px solid #b8cfe8; border-radius: 12px;
      padding: 16px 20px; margin-bottom: 20px; box-shadow: 0 2px 12px rgba(11, 92, 171, 0.08); }}
    .admin-oauth-section h2 {{ margin: 0 0 10px; font-size: 1.05rem; color: var(--navy); }}
    .oauth-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }}
    .oauth-card {{ border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: #fafbfc; }}
    .oauth-card-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }}
    .oauth-card h3 {{ margin: 0; font-size: .88rem; color: var(--navy); }}
    .oauth-actions {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }}
    .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600; }}
    .badge.ok {{ background: #e8f5e9; color: #1b5e20; }}
    .badge.err {{ background: #fdecea; color: #b42318; }}
    .btn {{ display: inline-block; padding: 9px 16px; border-radius: 8px; border: 0; font-weight: 600;
      cursor: pointer; font-size: .88rem; text-decoration: none; }}
    .btn.primary {{ background: var(--accent); color: #fff; }}
    .btn.secondary {{ background: #fff; color: var(--accent); border: 1px solid var(--border); }}
    .btn-sm {{ padding: 5px 10px; font-size: .8rem; border-radius: 6px; }}
    .inline-form {{ display: inline; margin: 0; }}
    ul.checklist {{ margin: 8px 0 0; padding-left: 1.2rem; }}
    ul.checklist.mono {{ font-family: ui-monospace, monospace; font-size: .82rem; }}
    .settings-fold {{ margin-top: 8px; font-size: .82rem; }}
    .settings-fold summary {{ cursor: pointer; color: var(--muted); }}
    .hint {{ color: var(--muted); font-size: .82rem; margin: 4px 0 0; }}
    .hint.mono {{ font-family: ui-monospace, monospace; }}
    /* ---- Feature requests inbox (Notifications page) ---- */
    .fr-section {{ border-top: 3px solid #7c3aed; }}
    .fr-count-new {{ background: #ede9fe; color: #6d28d9; }}
    .fr-list {{ display: flex; flex-direction: column; gap: 10px; }}
    .fr-row {{ border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 13px 15px;
      transition: border-color .15s, box-shadow .15s; }}
    .fr-row.is-new {{ border-color: #ddd6fe; background: #fbfaff; }}
    .fr-row.is-done {{ opacity: .72; }}
    .fr-row-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .fr-row-actions {{ display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
    .fr-archive {{ color: var(--muted); }}
    .fr-archive:hover {{ color: var(--navy); }}
    .fr-delete {{ color: #b91c1c; }}
    .fr-delete:hover {{ color: #7f1d1d; }}
    .fr-row-where {{ display: flex; align-items: center; gap: 9px; min-width: 0; }}
    .fr-badge {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .68rem; font-weight: 800;
      text-transform: uppercase; letter-spacing: .04em; flex-shrink: 0; }}
    .fr-badge-new {{ background: #ede9fe; color: #6d28d9; }}
    .fr-badge-done {{ background: #f1f5f9; color: #64748b; }}
    .fr-badge-scope-global {{ background: #dbeafe; color: #1d4ed8; }}
    .fr-badge-scope-client {{ background: #fef3c7; color: #b45309; text-transform: none; letter-spacing: 0;
      max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .fr-page-link {{ font-weight: 700; color: var(--navy); text-decoration: none; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; }}
    .fr-page-link:hover {{ color: var(--accent); }}
    .fr-path {{ display: block; font-size: .74rem; color: var(--muted); margin: 4px 0 0; overflow-wrap: anywhere; }}
    .fr-body {{ margin: 8px 0 0; font-size: .92rem; color: var(--ink); white-space: pre-wrap; overflow-wrap: anywhere; }}
    .fr-row-meta {{ display: flex; gap: 14px; margin-top: 10px; font-size: .78rem; color: var(--muted); }}
    .fr-resolved {{ font-size: .8rem; color: var(--muted); }}
    .dash-section {{ border-top: 3px solid var(--accent); }}
    .dash-section-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
    .dash-section-head h2 {{ margin: 0; }}
    .dash-add-fold {{ position: relative; }}
    .dash-add-fold summary {{ list-style: none; }}
    .dash-add-fold summary::-webkit-details-marker {{ display: none; }}
    .dash-add-form {{ margin-top: 10px; padding: 14px; background: #fafbfc; border: 1px solid var(--border);
      border-radius: 8px; position: absolute; right: 0; top: 100%; z-index: 5; width: 380px; max-width: 90vw;
      box-shadow: 0 4px 20px rgba(16,33,67,.12); }}
    .dash-table {{ margin-top: 4px; }}
    .dash-table td {{ vertical-align: middle; }}
    /* ---- Account row kebab (⋮) menu ---- */
    .dash-kebab {{ position: relative; margin: 0; font-size: .88rem; }}
    .dash-kebab > summary {{ cursor: pointer; list-style: none; }}
    .dash-kebab > summary::-webkit-details-marker {{ display: none; }}
    .dash-kebab[open] > .dash-icon-btn {{ background: #f4f8fd; border-color: #b9c8dc; color: var(--accent); }}
    .dash-kebab-menu {{ position: absolute; right: 0; top: calc(100% + 6px); z-index: 20; width: 244px; max-width: 80vw;
      background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 6px;
      box-shadow: 0 12px 32px rgba(16,33,67,.18); }}
    .dash-kebab-list {{ display: flex; flex-direction: column; gap: 2px; }}
    /* A class selector's display beats the UA [hidden] rule, so re-assert it with
       higher specificity — otherwise the rename/delete panels never hide. */
    .dash-kebab-list[hidden], .dash-kebab-panel[hidden] {{ display: none; }}
    .dash-kebab-item {{ display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 10px; border: 0;
      border-radius: 8px; background: transparent; color: var(--navy); font-size: .88rem; font-weight: 600;
      text-align: left; text-decoration: none; cursor: pointer; transition: background .12s, color .12s; }}
    .dash-kebab-item svg {{ flex-shrink: 0; }}
    .dash-kebab-item:hover {{ background: #f4f8fd; color: var(--accent); }}
    .dash-kebab-item.danger {{ color: var(--danger); }}
    .dash-kebab-item.danger:hover {{ background: #fef2f2; color: var(--danger); }}
    .dash-kebab-note {{ padding: 9px 10px; font-size: .76rem; color: var(--muted); font-weight: 600; }}
    .dash-kebab-panel {{ display: flex; flex-direction: column; gap: 8px; padding: 6px 6px 4px; }}
    .dash-kebab-panel .hint {{ margin: 0; }}
    .dash-kebab-panel input {{ max-width: 100%; margin: 0; }}
    .dash-kebab-label {{ font-size: .68rem; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
    .dash-kebab-back {{ display: inline-flex; align-items: center; gap: 5px; align-self: flex-start; border: 0; background: transparent;
      color: var(--muted); font-size: .78rem; font-weight: 600; cursor: pointer; padding: 2px 0; }}
    .dash-kebab-back:hover {{ color: var(--accent); }}
    .dash-kebab-submit {{ width: 100%; padding: 8px; border-radius: 8px; border: 0; font-weight: 700; font-size: .85rem;
      cursor: pointer; transition: filter .12s, opacity .12s; }}
    .dash-kebab-submit.primary {{ background: linear-gradient(135deg, var(--accent), var(--accent-d)); color: #fff; }}
    .dash-kebab-submit.primary:hover {{ filter: brightness(1.06); }}
    .dash-kebab-submit.danger {{ background: var(--danger); color: #fff; }}
    .dash-kebab-submit.danger:disabled {{ opacity: .5; cursor: not-allowed; }}
    .dash-kebab-submit.danger:not(:disabled):hover {{ filter: brightness(1.06); }}
    /* ---- Users table ---- */
    /* overflow stays visible so a row's kebab menu can hang outside the table;
       the cells wrap instead of forcing a sideways scroller, and below 640px the
       rows become cards anyway. */
    .user-table-wrap {{ overflow: visible; }}
    .user-table {{ font-size: .92rem; }}
    .user-table th {{ padding-top: 0; white-space: nowrap; }}
    .user-table td {{ vertical-align: middle; padding-top: 9px; padding-bottom: 9px; }}
    .user-table tbody tr.user-row {{ transition: background .12s; }}
    .user-table tbody tr.user-row:hover {{ background: #f7fafd; }}
    .user-table tbody tr.user-row:last-child td {{ border-bottom: 0; }}
    /* Identity cell: avatar + email as one unit, so the eye lands on the person. */
    .user-cell {{ display: flex; align-items: center; gap: 11px; min-width: 0; }}
    .user-ident {{ display: flex; align-items: center; gap: 7px; min-width: 0; flex-wrap: wrap; }}
    .user-email {{ font-weight: 650; color: var(--navy); overflow-wrap: anywhere; }}
    .you-chip {{ padding: 1px 7px; border-radius: 999px; background: #eef2f7; color: var(--muted);
      font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }}
    /* The users kebab carries a role picker and a dashboard checklist, so it
       needs a little more room than the account rows' rename/delete panels. */
    .user-kebab .dash-kebab-menu {{ width: 296px; }}
    .user-kebab .dash-kebab-panel select,
    .user-kebab .dash-kebab-panel input {{ width: 100%; max-width: 100%; margin: 0; }}
    .user-kebab .dash-kebab-panel .client-checks {{ max-height: 190px; overflow-y: auto; }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; overflow: hidden;
      clip: rect(0 0 0 0); white-space: nowrap; }}
    .avatar {{ position: relative; display: inline-flex; width: 40px; height: 40px; cursor: pointer; flex-shrink: 0; }}
    .avatar-img, .avatar-initials {{ width: 40px; height: 40px; border-radius: 50%; object-fit: cover; display: grid; place-items: center; }}
    .avatar-initials {{ background: linear-gradient(135deg, #dbe7f7, #eef4fb); color: #35507a; font-weight: 800; font-size: .82rem; letter-spacing: .02em; border: 1px solid var(--border); }}
    .avatar-img {{ border: 1px solid var(--border); background: #f0f3f8; }}
    .avatar-edit {{ position: absolute; right: -2px; bottom: -2px; width: 17px; height: 17px; border-radius: 50%;
      background: var(--accent); color: #fff; display: grid; place-items: center; box-shadow: 0 1px 3px rgba(5,18,31,.35);
      opacity: 0; transition: opacity .12s; }}
    .avatar:hover .avatar-edit {{ opacity: 1; }}
    .avatar.is-uploading {{ opacity: .5; pointer-events: none; }}
    .role-badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .74rem; font-weight: 700;
      text-transform: capitalize; }}
    .role-admin {{ background: #eef2ff; color: #4338ca; }}
    .role-client {{ background: #ecfdf3; color: #15803d; }}
    .role-standard {{ background: #fff7ed; color: #b45309; }}
    .pill {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .74rem; font-weight: 700; }}
    .pill-on {{ background: #e8f5e9; color: #1b5e20; }}
    .pill-off {{ background: #f1f5f9; color: #64748b; }}
    .pill-invite {{ background: #fff7ed; color: #b45309; }}
    /* One-shot invite link panel: the raw token is never stored, so this is the
       only place it is ever shown. Loud on purpose. */
    .invite-panel {{ margin: 0 0 18px; padding: 14px 16px; border-radius: 12px;
      border: 1px solid #bfdbfe; background: linear-gradient(180deg,#eff6ff,#f8fbff); }}
    .invite-head {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }}
    .invite-head strong {{ font-size: .95rem; color: #1e3a8a; }}
    .invite-note {{ font-size: .8rem; color: #b45309; font-weight: 600; }}
    .invite-copy {{ display: flex; gap: 8px; margin-top: 10px; }}
    .invite-copy input {{ flex: 1 1 auto; min-width: 0; font-family: ui-monospace, SFMono-Regular,
      Menlo, monospace; font-size: .82rem; background: #fff; max-width: none; margin: 0; }}
    .invite-copy button {{ flex: 0 0 auto; }}
    .invite-panel .hint {{ margin-top: 8px; }}
    .ckbx-legend {{ display: block; font-size: .7rem; font-weight: 700; color: var(--muted);
      text-transform: uppercase; letter-spacing: .04em; margin: 2px 0 6px; }}
    .client-checks {{ display: flex; flex-direction: column; gap: 5px; max-height: 190px; overflow-y: auto;
      padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: #fff; margin-bottom: 8px; }}
    .ckbx {{ display: flex; align-items: center; gap: 8px; font-size: .86rem; font-weight: 500; cursor: pointer; margin: 0; }}
    .ckbx input {{ margin: 0; width: auto; max-width: none; }}
    .col-actions {{ text-align: right; }}
    td.col-actions {{ position: relative; }}
    .group-badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .74rem; font-weight: 700;
      background: #eef2ff; color: #3730a3; }}
    .group-select, .slug-fallback {{ width: 100%; max-width: 100%; margin-bottom: 8px; }}
    .slug-fallback {{ font-size: .86rem; }}
    /* ---- Users: header, count chip, search, segmented control ---- */
    .users-head {{ display: flex; align-items: center; justify-content: space-between; gap: 14px;
      margin-bottom: 14px; flex-wrap: wrap; }}
    .users-head-right {{ display: flex; align-items: center; gap: 10px; flex-shrink: 0; }}
    .users-showing {{ font-size: .8rem; color: var(--muted); white-space: nowrap; }}
    .users-showing b {{ color: var(--navy); }}
    .count-chip {{ display: inline-block; min-width: 22px; padding: 1px 9px; border-radius: 999px;
      background: #eaf0f9; color: var(--accent); font-size: .78rem; font-weight: 700;
      vertical-align: 2px; }}
    .seg {{ display: inline-flex; padding: 3px; background: #eef2f7; border-radius: 11px; gap: 2px; }}
    .seg-btn {{ display: inline-flex; align-items: center; gap: 6px; border: 0; background: transparent;
      padding: 7px 13px; border-radius: 9px; font-size: .84rem; font-weight: 650; color: var(--muted);
      cursor: pointer; transition: background .14s, color .14s, box-shadow .14s; }}
    .seg-btn:hover {{ color: var(--navy); }}
    .seg-btn.is-active {{ background: #fff; color: var(--navy); box-shadow: 0 1px 3px rgba(10,37,64,.12); }}
    .seg-count {{ display: inline-block; min-width: 18px; padding: 0 6px; border-radius: 999px; font-size: .72rem;
      font-weight: 700; background: #dfe6f0; color: #64748b; }}
    .seg-btn.is-active .seg-count {{ background: #e6edf7; color: var(--accent); }}
    /* The add-user form carries more fields than "Add account", so it opens a
       little wider and keeps one field per line. */
    .user-add-form {{ width: 420px; display: flex; flex-direction: column; gap: 12px; }}
    .user-add-form > div {{ min-width: 0; }}
    .user-add-form label {{ display: block; }}
    .user-add-form input, .user-add-form select {{ width: 100%; max-width: 100%; margin: 0; }}
    .user-add-form .hint {{ margin: 6px 0 0; }}
    .user-add-form button.primary {{ width: 100%; margin: 0; }}
    .users-empty {{ padding: 18px 8px; text-align: center; font-size: .9rem; }}
    /* ---- Audience panels (team vs clients) ---- */
    .user-group {{ margin-top: 8px; }}
    .user-group + .user-group {{ margin-top: 22px; }}
    .user-group-head {{ display: flex; align-items: center; gap: 11px; padding: 0 2px 10px;
      border-bottom: 1px solid var(--line); margin-bottom: 4px; }}
    .ug-icon {{ display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px; flex-shrink: 0; }}
    .ug-team {{ background: #eef2ff; color: #4338ca; }}
    .ug-client {{ background: #ecfdf3; color: #15803d; }}
    .ug-text {{ display: flex; flex-direction: column; gap: 1px; flex: 1; min-width: 0; }}
    .ug-title {{ font-weight: 750; font-size: .96rem; color: var(--navy); }}
    .ug-sub {{ font-size: .77rem; color: var(--muted); }}
    .user-group[hidden] {{ display: none; }}
    /* ---- Access / dashboard chips ---- */
    .col-access {{ max-width: 340px; }}
    .chip-row {{ display: flex; flex-wrap: wrap; gap: 5px; }}
    .access-chip {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
      background: #f1f5f9; color: #334155; border: 1px solid var(--border); white-space: nowrap; }}
    .chip-all {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem; font-weight: 700;
      background: #eef2ff; color: #4338ca; }}
    .chip-none {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem; font-weight: 600;
      background: #f8fafc; color: #94a3b8; border: 1px dashed #cbd5e1; }}
    /* ---- Last session (last-activity) column ---- */
    .last-login {{ font-size: .84rem; color: #334155; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .last-login.never {{ color: #94a3b8; font-style: italic; }}
    /* ---- Group access preview (redundancy check) ---- */
    .group-preview {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: -2px 0 10px;
      padding: 8px 10px; border-radius: 9px; background: #f0f7f2; border: 1px solid #cbe6d5; }}
    .group-preview.is-warn {{ background: #fff7ed; border-color: #f5d3ac; }}
    .gp-label {{ font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
      color: #15803d; margin-right: 2px; }}
    .gp-chip {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
      background: #fff; color: #15803d; border: 1px solid #cbe6d5; }}
    .gp-warn {{ font-size: .78rem; color: #b45309; font-weight: 600; }}
    /* ---- Client groups ---- */
    .groups-section {{ border-top: 3px solid #6366f1; }}
    .group-list {{ display: flex; flex-direction: column; gap: 8px; }}
    .group-row {{ border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px;
      transition: border-color .15s, box-shadow .15s; }}
    .group-row:hover {{ border-color: #c6d5ea; box-shadow: 0 4px 16px rgba(16,33,67,.08); }}
    .group-row-head {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }}
    .group-row-main {{ display: flex; flex-direction: column; min-width: 140px; gap: 1px; }}
    .group-row-name {{ font-weight: 700; font-size: .98rem; color: var(--navy); }}
    .group-row-meta {{ font-size: .76rem; color: var(--muted); }}
    .group-chips {{ display: flex; flex-wrap: wrap; gap: 6px; flex: 1; }}
    .grp-chip {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
      background: #f1f5f9; color: #334155; border: 1px solid var(--border); }}
    .group-row-actions {{ display: flex; align-items: center; flex-shrink: 0; position: relative; }}
    .group-row-desc {{ margin-left: 8px; padding-left: 8px; border-left: 1px solid var(--border); }}
    .group-kebab .dash-kebab-menu {{ width: 296px; }}
    .group-kebab .dash-kebab-panel input {{ width: 100%; }}
    /* ---- Accounts: filterable head + responsive card grid ---- */
    .dash-head-right {{ display: flex; align-items: center; gap: 10px; flex-shrink: 0; }}
    .dash-search {{ display: flex; align-items: center; gap: 7px; padding: 7px 12px; border: 1px solid var(--line);
      border-radius: 10px; background: #fff; color: var(--muted); transition: border-color .12s, box-shadow .12s; }}
    .dash-search:focus-within {{ border-color: #b9c8dc; box-shadow: 0 0 0 3px rgba(37,99,235,.12); }}
    .dash-search svg {{ flex-shrink: 0; }}
    .dash-search input {{ border: 0; outline: 0; background: transparent; font-size: .88rem; color: var(--ink);
      width: 190px; max-width: 40vw; margin: 0; padding: 0; }}
    .dash-empty {{ margin: 14px 2px 2px; }}
    /* Cards flow into as many columns as the (wider) Accounts canvas allows. */
    .dash-list {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 10px; margin-top: 4px; }}
    .dash-row {{ display: flex; align-items: center; gap: 14px; padding: 12px 14px; border: 1px solid var(--line);
      border-radius: 12px; background: #fff; transition: border-color .15s, box-shadow .15s, transform .1s; }}
    .dash-row[hidden] {{ display: none; }}
    .dash-row:hover {{ border-color: #c6d5ea; box-shadow: 0 6px 18px rgba(16,33,67,.09); transform: translateY(-1px); }}
    .dash-logo {{ position: relative; display: inline-flex; width: 40px; height: 40px; cursor: pointer; flex-shrink: 0; }}
    .logo-img, .logo-initials {{ width: 40px; height: 40px; border-radius: 10px; object-fit: cover; display: grid; place-items: center; }}
    .logo-initials {{ color: #fff; font-weight: 800; font-size: .86rem; letter-spacing: .02em;
      background: linear-gradient(135deg, var(--accent), #1e3a8a); }}
    .logo-img {{ border: 1px solid var(--border); background: #fff; }}
    .logo-edit {{ position: absolute; right: -3px; bottom: -3px; width: 16px; height: 16px; border-radius: 50%;
      background: var(--navy); color: #fff; display: grid; place-items: center; box-shadow: 0 1px 3px rgba(5,18,31,.35);
      opacity: 0; transition: opacity .12s; }}
    .dash-logo:hover .logo-edit {{ opacity: 1; }}
    .dash-logo.is-uploading {{ opacity: .5; pointer-events: none; }}
    .dash-row-main {{ display: flex; flex-direction: column; min-width: 0; flex: 1; gap: 2px; text-decoration: none; }}
    .dash-row-name {{ font-weight: 700; font-size: .98rem; color: var(--navy); overflow: hidden;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.25; }}
    .dash-row-main:hover .dash-row-name {{ color: var(--accent); }}
    .dash-row-slug {{ font-size: .76rem; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    /* Industry tags: a third line inside the card's text column, so a long
       bucket name ellipses instead of squeezing the account name. An account can
       carry several, so the chips wrap onto their own lines rather than pushing
       the card wider. */
    .dash-industries {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 3px; max-width: 100%; }}
    .dash-industry {{ max-width: 100%; padding: 2px 8px;
      border-radius: 999px; background: #eef4fd; color: var(--accent-d); font-size: .7rem; font-weight: 700;
      letter-spacing: .01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .dash-industry.unset {{ background: #f1f4f8; color: var(--muted); font-weight: 600; }}
    /* Industry picker: a scrollable checkbox list inside the kebab panel — the
       whole taxonomy has to be reachable without the popover running off-card. */
    .dash-kebab-checks {{ display: flex; flex-direction: column; gap: 1px; max-height: 208px;
      overflow-y: auto; -webkit-overflow-scrolling: touch; padding: 3px; margin: 0 -3px;
      border: 1px solid var(--line); border-radius: 9px; background: #fbfcfe; }}
    .dash-kebab-check {{ display: flex; align-items: flex-start; gap: 8px; padding: 5px 7px; border-radius: 7px;
      font-size: .82rem; font-weight: 600; color: var(--navy); cursor: pointer; }}
    .dash-kebab-check:hover {{ background: #eef4fd; }}
    .dash-kebab-check input {{ flex: 0 0 auto; width: 14px; height: 14px; margin: 2px 0 0; accent-color: var(--accent); cursor: pointer; }}
    /* Long bucket names wrap instead of ellipsing — in a 244px popover, a
       truncated "Architecture, Engineering & Con…" is not a choosable option. */
    .dash-kebab-check span {{ min-width: 0; line-height: 1.3; }}
    .dash-row-actions {{ display: flex; align-items: center; gap: 6px; flex-shrink: 0; position: relative; }}
    .dash-icon-btn {{ display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px;
      border-radius: 9px; border: 1px solid var(--line); background: #fff; color: var(--navy); cursor: pointer;
      text-decoration: none; list-style: none; transition: background .12s, border-color .12s, color .12s; }}
    .dash-icon-btn::-webkit-details-marker {{ display: none; }}
    .dash-icon-btn:hover {{ background: #f4f8fd; border-color: #b9c8dc; color: var(--accent); }}
    .dash-icon-btn.danger:hover {{ background: #fef2f2; border-color: #f3c0bb; color: var(--danger); }}
    /* Modern "Add dashboard" button */
    .add-dash-btn {{ display: inline-flex; align-items: center; gap: 7px; padding: 9px 16px; border-radius: 10px;
      background: linear-gradient(135deg, var(--accent), var(--accent-d)); color: #fff; font-size: .88rem; font-weight: 700;
      cursor: pointer; list-style: none; box-shadow: 0 5px 14px rgba(37,99,235,.3); transition: filter .15s, transform .06s; }}
    .add-dash-btn::-webkit-details-marker {{ display: none; }}
    .add-dash-btn:hover {{ filter: brightness(1.06); }}
    .add-dash-btn:active {{ transform: translateY(1px); }}

    /* ============================================================
       Mobile (<= 640px). The navy sidebar already collapses to a
       drawer in the shared shell; this reflows the admin content
       itself — tighter chrome, full-width forms, and the user
       tables re-laid out as stacked cards instead of a wide,
       horizontally-scrolling grid.
       ============================================================ */
    @media (max-width: 640px) {{
      /* main is a column flexbox item in the shell. Its desktop `margin:0 auto`
         gives it auto cross-axis margins, which disable flex `stretch` and let
         it size to content — so a wide child (the audit log table) drags the
         whole page wider than the screen. Reset the margins and pin it to the
         viewport width so inner scroll containers scroll instead. */
      main {{ padding: 16px 13px 44px; margin: 0; width: 100%; min-width: 0; }}
      section {{ padding: 17px 15px; border-radius: 14px; }}
      h2 {{ font-size: 1rem; }}
      /* Let form controls use the full column width on a phone. */
      input, select {{ max-width: 100%; }}
      .row {{ gap: 12px; }}
      .row > div {{ min-width: 0; flex-basis: 100%; }}
      button.primary {{ width: 100%; }}

      /* Users section header: stack the title, search, Add button, and the
         audience filter so nothing is squeezed off the right edge. */
      .users-head {{ flex-direction: column; gap: 14px; }}
      .users-head-right {{ flex-direction: column; align-items: stretch; width: 100%; }}
      .users-head-right .dash-search {{ width: 100%; }}
      .users-showing {{ display: none; }}
      .seg {{ width: 100%; justify-content: space-between; }}
      .seg-btn {{ flex: 1; justify-content: center; padding: 8px 6px; font-size: .8rem; }}
      .seg-btn .seg-count {{ display: none; }}

      /* User tables → cards. Drop the header row and let each user
         become a bordered card: avatar + email on top, then one
         labelled line per field, actions along the bottom. */
      .user-table-wrap {{ overflow-x: visible; }}
      .user-table thead {{ display: none; }}
      .user-table, .user-table tbody {{ display: block; }}
      .user-table tr.user-row {{
        display: flex; flex-wrap: wrap; align-items: center;
        border: 1px solid var(--line); border-radius: 12px; background: #fff;
        padding: 12px 14px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(10,37,64,.05);
      }}
      .user-table td {{ display: block; border: none; padding: 0; }}
      /* The identity cell and the kebab share the card's top line; every other
         field gets a full-width line of its own. Two details matter: `order`
         pulls the actions cell up next to the identity cell (flex lines pack in
         order, and the full-width fields sit between them in the markup), and
         the identity cell needs a real basis — `flex: 1` resolves to a 0 basis,
         which lets the next field crowd onto the line and squash it to nothing. */
      .user-table td.col-user {{ order: 1; flex: 1 1 50%; min-width: 0; font-size: .95rem;
        overflow-wrap: anywhere; }}
      .user-table td.col-user::before {{ display: none; }}
      .user-table td:not(.col-user):not(.col-actions) {{
        order: 3; flex: 0 0 100%; display: flex; align-items: center; justify-content: space-between;
        gap: 14px; padding: 9px 0 0; margin-top: 9px; border-top: 1px solid var(--line);
        text-align: right; font-size: .86rem;
      }}
      .user-table td:not(.col-user):not(.col-actions)::before {{
        content: attr(data-label); flex-shrink: 0; text-align: left;
        font-size: .7rem; font-weight: 700; text-transform: uppercase;
        letter-spacing: .3px; color: var(--muted);
      }}
      .user-table td.col-access {{ align-items: flex-start; }}
      .user-table td.col-access .chip-row {{ justify-content: flex-end; }}
      /* Actions sit beside the email on a card, not on a labelled line of
         their own — the kebab is a 34px button, it needs no row. */
      .user-table td.col-actions {{ order: 2; flex: 0 0 auto; padding: 0 0 0 10px; margin: 0; border-top: 0; }}
      .user-table td.col-actions::before {{ content: none; }}
      .user-add-form {{ width: auto; }}
      .user-table tr.empty-row {{ display: block; padding: 14px 4px; }}
      .user-table tr.empty-row td {{ display: block; width: 100%; }}

      /* Section head with an inline "Add" button (Dashboards, Groups). */
      .dash-section-head {{ flex-wrap: wrap; }}

      /* Absolutely-positioned popovers (reset password, change role,
         add/delete dashboard, edit group) can't fit beside their
         trigger on a phone — float them as a full-width bottom sheet. */
      .dash-add-fold .dash-add-form,
      .dash-kebab .dash-kebab-menu,
      .dash-add-form {{
        position: fixed; left: 12px; right: 12px; top: auto; bottom: 12px;
        width: auto; max-width: none; max-height: 72vh; overflow-y: auto;
        z-index: 130; box-shadow: 0 -6px 28px rgba(16,33,67,.22);
      }}
      /* The bottom sheet already scrolls; a second scroller inside it for the
         industry checklist would just be a scroll trap under a thumb. */
      .dash-kebab-checks {{ max-height: none; overflow-y: visible; }}

      /* Audit log stays a table but scrolls sideways rather than crushing. */
      .audit-wrap table {{ min-width: 540px; }}
    }}
"""
    # ---- Create-user section (lives on the Users page) ----
    # The freshly minted invite link, shown once. It is never persisted in
    # readable form (user_invites stores only a hash), so this panel is the only
    # chance to copy it — hence the prominent placement and the copy button.
    invite_panel = ""
    if invite_link:
        invite_panel = f"""
      <div class="invite-panel">
        <div class="invite-head">
          <strong>Invite link ready{f" for {_esc(invite_email)}" if invite_email else ""}</strong>
          <span class="invite-note">Copy it now — it is shown only once, and expires
            {"in " + _esc(invite_expires_in) if invite_expires_in else "shortly"}.</span>
        </div>
        <div class="invite-copy">
          <input type="text" id="inviteLink" value="{_esc(invite_link)}" readonly
            onfocus="this.select()" aria-label="Invite link">
          <button type="button" class="primary" id="inviteCopyBtn">Copy</button>
        </div>
        <p class="hint">Send this to the new user however you like. Opening it lets
          them set their own password and signs them in. It works once.</p>
      </div>"""

    # ---- Add-user form (folds out of the roster header) ----
    # Adding a user is occasional; reading the roster is constant. So the form
    # lives behind the header's "Add user" button rather than pushing the roster
    # down the page, exactly as "Add account" does on Accounts.
    add_user_html = f"""
          <details class="dash-add-fold">
            <summary class="add-dash-btn">{_SVG_PLUS}Add user</summary>
            <form method="post" action="/admin/users" class="dash-add-form user-add-form role-form" id="createUserForm">
              <div>
                <label for="email">Email</label>
                <input id="email" name="email" type="email" required placeholder="name@company.com">
              </div>
              <div>
                <label for="role">Role</label>
                <select id="role" name="role" class="role-select">
                  <option value="client">client — external portal login</option>
                  <option value="standard">standard — Sagefrog staff, view-only</option>
                  <option value="admin">admin — full access</option>
                </select>
              </div>
              <div class="client-only">
                <label for="group_id">Client group (recommended)</label>
                {_group_select(None)}
                <p class="hint">Pick a group to grant its dashboards — no slug to mistype.
                  Leave <em>Ungrouped</em> to bind a single dashboard by slug below.</p>
                <label for="client_slug" style="margin-top:8px">Client slug (ungrouped only)</label>
                <input id="client_slug" name="client_slug" type="text" placeholder="penn"
                  list="clientSlugOptions">
                {client_slug_datalist}
              </div>
              <div class="standard-only">
                <span class="ckbx-legend">Clients this user can access</span>
                {_client_checkboxes(set())}
              </div>
              <div>
                <label for="setup_mode">How they get in</label>
                <select id="setup_mode" name="setup_mode" class="setup-select">
                  <option value="invite">Send an invite link (they pick their password)</option>
                  <option value="password">Set a password myself</option>
                </select>
                <p class="hint setup-hint-invite">You'll get a one-time link to pass along.
                  The account can't be signed into until they redeem it.</p>
                <div class="password-only" style="margin-top:8px">
                  <label for="password">Password (min 10 chars)</label>
                  <input id="password" name="password" type="password" minlength="10">
                </div>
              </div>
              <button type="submit" class="primary" id="createUserBtn">Create user &amp; get invite link</button>
            </form>
          </details>"""

    # ---- Users roster section (lives on the Users page) ----
    users_html = f"""
    <section class="dash-section users-section">
      {invite_panel}
      <div class="users-head">
        <div class="seg" role="tablist" aria-label="Filter by audience">
          <button type="button" class="seg-btn is-active" data-scope="all" role="tab" aria-selected="true">All <span class="seg-count">{user_count}</span></button>
          <button type="button" class="seg-btn" data-scope="team" role="tab" aria-selected="false">Sagefrog team <span class="seg-count">{team_count}</span></button>
          <button type="button" class="seg-btn" data-scope="client" role="tab" aria-selected="false">Clients <span class="seg-count">{client_count}</span></button>
        </div>
        <div class="users-head-right">
          <span class="users-showing" aria-live="polite"><b id="userCount">{user_count}</b> shown</span>
          <div class="dash-search">
            {_SVG_SEARCH}
            <input type="search" id="userSearch" placeholder="Filter by email, group, or dashboard…"
              autocomplete="off" aria-label="Filter users">
          </div>
          {add_user_html}
        </div>
      </div>

      <div class="user-group" data-group="team">
        <div class="user-group-head">
          <span class="ug-icon ug-team" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg></span>
          <div class="ug-text">
            <span class="ug-title">Sagefrog team</span>
            <span class="ug-sub">Internal accounts — full admins and view-only staff</span>
          </div>
          <span class="count-chip">{team_count}</span>
        </div>
        <div class="user-table-wrap">
          <table class="user-table">
            <thead><tr><th>User</th><th>Role</th><th>Client access</th><th>Last session</th><th>Status</th><th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead>
            <tbody>{team_body}</tbody>
          </table>
          <p class="users-empty muted" hidden>No Sagefrog team members match your filter.</p>
        </div>
      </div>

      <div class="user-group" data-group="client">
        <div class="user-group-head">
          <span class="ug-icon ug-client" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M3 9h18"/><path d="M8 21h8"/></svg></span>
          <div class="ug-text">
            <span class="ug-title">Client portal users</span>
            <span class="ug-sub">External logins — scoped to their group's dashboards only</span>
          </div>
          <span class="count-chip">{client_count}</span>
        </div>
        <div class="user-table-wrap">
          <table class="user-table">
            <thead><tr><th>User</th><th>Group</th><th>Dashboards</th><th>Last session</th><th>Status</th><th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead>
            <tbody>{client_body}</tbody>
          </table>
          <p class="users-empty muted" hidden>No client portal users match your filter.</p>
        </div>
      </div>
    </section>"""

    # ---- Advanced settings sections (lives on the Advanced page) ----
    # The audit log + platform connections + GCP credentials + dashboard cache
    # used to hide behind a collapsible fold on Overview; they now get their own
    # page, so we render the sections straight (no fold).
    audit_section_html = f"""
        <section>
          <h2>Audit log</h2>
          <p class="muted" style="margin:0 0 12px;font-size:.9rem">Sign-ins, sign-outs, and admin user changes (latest 150).</p>
          <div class="audit-wrap">
            <table>
              <thead><tr><th>When</th><th>Event</th><th>Actor</th><th>Details</th><th class="mono">IP</th></tr></thead>
              <tbody>{audit_table}</tbody>
            </table>
          </div>
        </section>"""
    advanced_html = (
        f"{dashboard_perf_html}{oauth_section_html}"
        f"{credentials_section_html}{audit_section_html}"
    )

    # ---- Per-page header + body assembly ----
    # A single render function serves four sidebar destinations; `page` selects
    # which sections land in the body, and each gets a matching page header.
    _PAGE_HEADS = {
        "clients": (
            "Accounts",
            "Every client account in one place — open a dashboard, or add a new one.",
        ),
        "users": (
            "Users",
            "Sagefrog staff and client portal logins, plus the client groups that grant dashboard access.",
        ),
        "feature-requests": (
            "Notifications",
            "The team's inbox — requests raised from the notes FAB on any client dashboard.",
        ),
        "advanced": (
            "Advanced",
            "Debug and platform plumbing — dashboard cache, connections, GCP credentials, and the audit log.",
        ),
    }
    head_title, head_sub = _PAGE_HEADS.get(page, _PAGE_HEADS["clients"])
    page_head_html = (
        f'<header class="admin-page-head">'
        f'<h1>{_esc(head_title)}</h1>'
        f'<p>{_esc(head_sub)}</p></header>'
    )

    if page == "users":
        body = f"{users_html}{groups_section_html}"
    elif page == "feature-requests":
        body = feature_requests_html or (
            '<section><p class="muted" style="margin:0">Feature requests are only '
            "available to super admins.</p></section>"
        )
    elif page == "advanced":
        body = advanced_html
    else:  # clients
        body = dashboard_manage_html

    # The Accounts page uses a wider canvas so its card grid can breathe; the
    # other pages keep the narrower reading width.
    main_cls = " admin-main--wide" if page == "clients" else ""
    content = f"""
  <main class="admin-main{main_cls}">
    {notice}
    {page_head_html}
    {body}
  </main>"""
    scripts = (
        f"<script>{_KEBAB_JS}</script>"
        f"<script>{_ADMIN_AVATAR_JS}</script>"
        f"<script>{_ROLE_TOGGLE_JS}</script>"
        f"<script>{_USER_SEARCH_JS}</script>"
        f"<script>{_DASH_SEARCH_JS}</script>"
    )
    return render_admin_shell_page(
        active_nav=page,
        page_title=f"Admin · {head_title}",
        content_html=content,
        session_email=user.email,
        session_is_admin=True,
        extra_css=admin_css,
        body_end_html=scripts,
    )
