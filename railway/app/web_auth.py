"""Browser session auth for dashboards and admin pages."""

from __future__ import annotations

import hmac
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
import web_users
from security import is_production
from web_users import WebUser

SESSION_USER_ID = "user_id"
# When an admin is "viewing as" another user, this holds that target user's id.
# The real signed-in account stays in SESSION_USER_ID; only get_current_user
# resolves to the impersonated user so every downstream access check and page
# renders exactly as that user would see them.
SESSION_VIEW_AS_ID = "view_as_user_id"


def session_secret() -> str:
    secret = (
        (os.getenv("AUTH_SESSION_SECRET") or "").strip()
        or (os.getenv("CRON_SECRET") or "").strip()
    )
    if not secret:
        raise RuntimeError(
            "Set AUTH_SESSION_SECRET (recommended) or CRON_SECRET for signed session cookies."
        )
    return secret


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


def require_client_access(client_slug: str):
    slug = client_slug.strip().lower()

    async def _dep(request: Request) -> WebUser:
        user = await require_user(request)
        if not user.can_access_client(slug):
            raise HTTPException(status_code=403, detail=f"No access to client '{slug}'.")
        return user

    return _dep


def legacy_dashboard_key_ok(key: str | None) -> bool:
    from dashboard_service import configured_dashboard_secret

    expected = configured_dashboard_secret()
    if not expected:
        return False
    return bool(key and hmac.compare_digest(key.strip(), expected))


def resolve_client_dashboard_user(
    request: Request,
    *,
    client_slug: str,
    key: str | None,
) -> WebUser | None:
    """Session user with access, or None if legacy ?key= should be tried."""
    user = get_current_user(request)
    if user and user.can_access_client(client_slug):
        return user
    if legacy_dashboard_key_ok(key):
        return None
    return None


def redirect_to_login(request: Request, *, next_path: str | None = None) -> RedirectResponse:
    target = next_path or str(request.url.path)
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={quote(target, safe='')}", status_code=303)


@dataclass(frozen=True)
class DashboardAuth:
    access_key: str | None
    use_session: bool
    user: WebUser | None


def authenticate_dashboard(
    request: Request,
    *,
    client_slug: str,
    key: str | None,
) -> DashboardAuth | RedirectResponse:
    """Session login, legacy ?key=, or redirect to /login."""
    user = resolve_client_dashboard_user(request, client_slug=client_slug, key=key)
    if user is not None:
        return DashboardAuth(access_key=None, use_session=True, user=user)
    if legacy_dashboard_key_ok(key):
        return DashboardAuth(access_key=key, use_session=False, user=None)
    if get_current_user(request):
        raise HTTPException(status_code=403, detail="You do not have access to this dashboard.")
    return redirect_to_login(request)


def authenticate_dashboard_api(
    request: Request,
    *,
    client_slug: str,
    key: str | None,
) -> DashboardAuth:
    """Same as authenticate_dashboard but JSON-friendly errors (no redirect)."""
    user = resolve_client_dashboard_user(request, client_slug=client_slug, key=key)
    if user is not None:
        return DashboardAuth(access_key=None, use_session=True, user=user)
    if legacy_dashboard_key_ok(key):
        return DashboardAuth(access_key=key, use_session=False, user=None)
    if get_current_user(request):
        raise HTTPException(status_code=403, detail="You do not have access to this dashboard.")
    raise HTTPException(status_code=401, detail="Sign in required.")


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


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
    .wrap {{ width:min(420px,100%); animation:rise .5s ease both; }}
    @keyframes rise {{ from {{ opacity:0; transform:translateY(10px); }} to {{ opacity:1; transform:none; }} }}
    .brand {{ display:flex; flex-direction:column; align-items:center; gap:12px; margin-bottom:22px; }}
    .logo {{ width:56px; height:56px; border-radius:16px; display:grid; place-items:center;
      background:linear-gradient(135deg,var(--green),#0a2540); box-shadow:0 10px 26px rgba(5,18,31,.5); }}
    .logo svg {{ width:30px; height:30px; }}
    .brand h1 {{ margin:0; font-size:1.05rem; font-weight:700; color:#fff; letter-spacing:.2px; }}
    .brand p {{ margin:0; font-size:.84rem; color:rgba(255,255,255,.6); }}
    .card {{ background:#fff; border-radius:18px; padding:30px 28px;
      box-shadow:0 24px 60px rgba(3,12,24,.42), 0 2px 6px rgba(3,12,24,.2); }}
    .card h2 {{ margin:0 0 4px; font-size:1.28rem; color:var(--navy); }}
    .card .lead {{ margin:0 0 22px; color:var(--muted); font-size:.9rem; }}
    label {{ display:block; font-size:.8rem; font-weight:600; color:#334155; margin:0 0 6px; }}
    .field {{ margin-bottom:16px; position:relative; }}
    input {{ width:100%; padding:12px 14px; border:1px solid var(--line); border-radius:10px;
      font-size:.98rem; color:var(--ink); background:#fbfcfe; transition:border-color .15s, box-shadow .15s; }}
    input::placeholder {{ color:#9aa5b5; }}
    input:focus {{ outline:none; border-color:var(--accent); background:#fff;
      box-shadow:0 0 0 3px rgba(37,99,235,.15); }}
    .pw-wrap input {{ padding-right:62px; }}
    .pw-toggle {{ position:absolute; right:8px; top:32px; border:0; background:transparent;
      color:var(--muted); font-size:.76rem; font-weight:600; cursor:pointer; padding:6px 8px;
      border-radius:6px; }}
    .pw-toggle:hover {{ color:var(--accent); background:#eef4ff; }}
    button.submit {{ width:100%; padding:13px; border:0; border-radius:10px; color:#fff;
      font-size:1rem; font-weight:700; cursor:pointer; letter-spacing:.2px; margin-top:4px;
      background:linear-gradient(135deg,var(--accent),var(--accent-d));
      box-shadow:0 6px 18px rgba(37,99,235,.35); transition:transform .06s, filter .15s; }}
    button.submit:hover {{ filter:brightness(1.06); }}
    button.submit:active {{ transform:translateY(1px); }}
    .error {{ color:var(--danger); font-size:.87rem; margin:0 0 14px; padding:10px 12px;
      background:#fef3f2; border:1px solid #fecdc9; border-radius:9px; }}
    .forgot {{ margin-top:18px; border-top:1px solid var(--line); padding-top:16px; }}
    .forgot summary {{ list-style:none; cursor:pointer; font-size:.86rem; font-weight:600;
      color:var(--accent); }}
    .forgot summary::-webkit-details-marker {{ display:none; }}
    .forgot summary:hover {{ text-decoration:underline; }}
    .forgot-body {{ margin-top:12px; font-size:.85rem; color:var(--muted); line-height:1.55; }}
    .forgot-body a.ghost {{ display:inline-block; margin-top:12px; padding:9px 16px; border-radius:9px;
      border:1px solid var(--accent); color:var(--accent); text-decoration:none; font-weight:600;
      font-size:.85rem; transition:background .15s, color .15s; }}
    .forgot-body a.ghost:hover {{ background:var(--accent); color:#fff; }}
    .foot {{ display:flex; align-items:center; justify-content:center; gap:6px; margin-top:20px;
      font-size:.75rem; color:rgba(255,255,255,.5); }}
    .foot svg {{ width:13px; height:13px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <div class="logo">
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/>
          <path d="M2 21c0-3 1.85-5.36 5.08-6"/>
        </svg>
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
    .logo {{ width:56px; height:56px; border-radius:16px; display:grid; place-items:center;
      background:linear-gradient(135deg,var(--green),#0a2540); box-shadow:0 10px 26px rgba(5,18,31,.5); }}
    .logo svg {{ width:30px; height:30px; }}
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
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/>
          <path d="M2 21c0-3 1.85-5.36 5.08-6"/>
        </svg>
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


def render_admin_page(
    *,
    user: WebUser,
    users: list[dict],
    audit_events: list[dict] | None = None,
    message: str | None = None,
    error: str | None = None,
    oauth_section_html: str = "",
    credentials_section_html: str = "",
) -> str:
    notice = ""
    if message:
        notice += f'<div class="notice ok">{_esc(message)}</div>'
    if error:
        notice += f'<div class="notice err">{_esc(error)}</div>'

    rows = []
    for u in users:
        slug = u.get("client_slug") or "—"
        uid = int(u["id"])
        reset_html = f"""
        <details class="dash-delete-fold">
          <summary class="link">Reset password…</summary>
          <form method="post" action="/admin/users/{uid}/reset-password" class="dash-delete-form"
            onsubmit="return confirm('Reset this user\'s password?');">
            <input type="password" name="new_password" placeholder="New password (min 10 chars)"
              minlength="10" required autocomplete="new-password">
            <button type="submit" class="link">Reset</button>
          </form>
        </details>"""
        role_html = ""
        deactivate_html = ""
        if uid != user.id:
            raw_slug = _esc(u.get("client_slug") or "")
            role_opts = "".join(
                f'<option value="{r}"{" selected" if u.get("role") == r else ""}>{r}</option>'
                for r in ("admin", "client", "standard")
            )
            role_html = f"""
        <details class="dash-delete-fold">
          <summary class="link">Change role…</summary>
          <form method="post" action="/admin/users/{uid}/role" class="dash-delete-form">
            <select name="role" aria-label="Role">{role_opts}</select>
            <input type="text" name="client_slug" placeholder="client slug (client role only)"
              value="{raw_slug}" autocomplete="off">
            <button type="submit" class="link">Update role</button>
          </form>
        </details>"""
            deactivate_html = (
                f'<form method="post" action="/admin/users/{uid}/deactivate" '
                f'onsubmit="return confirm(\'Deactivate this user?\');">'
                f'<button type="submit" class="link danger">Deactivate</button></form>'
            )
        actions = reset_html + role_html + deactivate_html
        rows.append(
            f"<tr><td>{_esc(u['email'])}</td><td>{_esc(u['role'])}</td>"
            f"<td>{_esc(str(slug))}</td>"
            f"<td>{'yes' if u.get('is_active') else 'no'}</td>"
            f"<td>{actions}</td></tr>"
        )
    user_rows = "\n".join(rows) or '<tr><td colspan="5" class="muted">No users yet.</td></tr>'

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
    dash_delete_js = ""
    try:
        import dashboard_registry
        import client_dashboard_config as _cdc

        if dashboard_registry.enabled():
            dash_rows = []
            for row in dashboard_registry.list_clients():
                slug = row.client_slug
                label = row.label

                # Template column: bigquery_nixon = the connector-driven Nixon
                # template; anything else (api/bigquery) is the older snapshot
                # dashboard. Offer a one-click convert for legacy dashboards so
                # they don't need a manual DB update.
                try:
                    _mode = (_cdc.get_config(slug).dashboard_mode or "api")
                except Exception:
                    _mode = "api"
                if _mode == "bigquery_nixon":
                    template_cell = '<span class="badge ok">New template</span>'
                elif slug == "penn":
                    template_cell = '<span class="muted">Snapshot (protected)</span>'
                else:
                    template_cell = (
                        f'<span class="muted">Snapshot</span> · '
                        f'<form method="post" action="/admin/dashboards/{_esc(slug)}/mode" style="display:inline">'
                        f'<button type="submit" class="link" '
                        f"onclick=\"return confirm('Convert {_esc(label)} to the new connector template?')\">"
                        f'Use new template</button></form>'
                    )

                delete_cell = ""
                if slug == "penn":
                    delete_cell = '<span class="muted">Protected</span>'
                else:
                    field_id = f"confirm-{slug.replace('-', '_')}"
                    btn_id = f"delete-btn-{slug.replace('-', '_')}"
                    delete_cell = f"""
                    <details class="dash-delete-fold">
                      <summary class="link danger">Delete…</summary>
                      <form method="post" action="/admin/dashboards/{_esc(slug)}/delete" class="dash-delete-form">
                        <p class="hint">Type <strong>{_esc(label)}</strong> to confirm.</p>
                        <input type="text" id="{field_id}" name="confirm_label"
                          placeholder="{_esc(label)}" autocomplete="off"
                          data-expected="{_esc(label)}" data-btn-id="{btn_id}">
                        <button type="submit" id="{btn_id}" class="link danger" disabled>Delete dashboard</button>
                      </form>
                    </details>"""
                clear_snapshot_form = (
                    f'<form method="post" action="/admin/snapshot/{_esc(slug)}/delete" style="display:inline">'
                    f'<button type="submit" class="link" onclick="return confirm(\'Clear cached snapshot for {_esc(label)}?\')">Clear snapshot</button>'
                    f"</form>"
                )
                dash_rows.append(
                    f"<tr>"
                    f'<td class="mono">{_esc(slug)}</td>'
                    f"<td>{_esc(label)}</td>"
                    f"<td>{template_cell}</td>"
                    f'<td><a href="/dashboard/{_esc(slug)}">Open</a> · '
                    f'<a href="/dashboard/{_esc(slug)}/settings">Settings</a> · '
                    f"{clear_snapshot_form}</td>"
                    f"<td>{delete_cell}</td>"
                    f"</tr>"
                )
            dash_table = (
                "\n".join(dash_rows)
                or '<tr><td colspan="5" class="muted">No dashboards yet.</td></tr>'
            )
            for slug, _label in client_config.list_dashboard_clients():
                client_slug_options += f'<option value="{_esc(slug)}"></option>\n'
            if client_slug_options:
                client_slug_datalist = f'<datalist id="clientSlugOptions">{client_slug_options}</datalist>'
            dashboard_manage_html = f"""
    <section class="dash-section">
      <div class="dash-section-head">
        <h2>Dashboards</h2>
        <details class="dash-add-fold">
          <summary class="btn primary btn-sm">+ Add dashboard</summary>
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
            <button type="submit" class="primary">Add dashboard</button>
          </form>
        </details>
      </div>
      <table class="dash-table">
        <thead><tr><th>Slug</th><th>Name</th><th>Template</th><th>Links</th><th></th></tr></thead>
        <tbody>{dash_table}</tbody>
      </table>
    </section>"""
            dash_delete_js = """
    document.querySelectorAll('.dash-delete-form input[name="confirm_label"]').forEach((input) => {
      const expected = input.dataset.expected || '';
      const btn = document.getElementById(input.dataset.btnId || '');
      const sync = () => {
        if (btn) btn.disabled = input.value.trim() !== expected;
      };
      input.addEventListener('input', sync);
      sync();
    });"""
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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin · Sagefrog Marketing Group</title>
  <style>
    :root {{
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#34b27b; --danger:#b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--ink);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
      background: linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment: fixed; min-height: 100vh; }}
    header {{ color: #fff; padding: 16px 24px; display: flex;
      align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
      background: linear-gradient(120deg,#0a2540 0%,#0d2f57 100%); box-shadow: 0 2px 14px rgba(5,18,31,.28); }}
    .brand-head {{ display: flex; align-items: center; gap: 12px; }}
    .brand-mark {{ width: 40px; height: 40px; border-radius: 11px; display: grid; place-items: center;
      background: linear-gradient(135deg,var(--green),#0a2540); box-shadow: 0 6px 16px rgba(5,18,31,.4); flex-shrink: 0; }}
    .brand-mark svg {{ width: 22px; height: 22px; }}
    header h1 {{ margin: 0; font-size: 1.05rem; letter-spacing: .2px; }}
    header .who {{ font-size: .82rem; opacity: .7; }}
  header a, header button.link {{ color: #fff; text-decoration: none; background: none; border: 0;
      cursor: pointer; font: inherit; opacity: .9; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 26px 20px 56px; }}
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
    .dash-delete-fold {{ margin: 0; font-size: .88rem; }}
    .dash-delete-fold summary {{ cursor: pointer; list-style: none; }}
    .dash-delete-fold summary::-webkit-details-marker {{ display: none; }}
    .dash-delete-form {{ margin-top: 8px; padding: 10px; background: #fafbfc; border-radius: 8px; border: 1px solid var(--border); }}
    .dash-delete-form input {{ max-width: 100%; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <header>
    <div class="brand-head">
      <div class="brand-mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/>
          <path d="M2 21c0-3 1.85-5.36 5.08-6"/>
        </svg>
      </div>
      <div>
        <h1>Sagefrog Marketing Group · Admin</h1>
        <span class="who">Signed in as {_esc(user.email)}</span>
      </div>
    </div>
    <div>
      <form method="post" action="/logout" style="display:inline"><button type="submit" class="link">Sign out</button></form>
    </div>
  </header>
  <main>
    {notice}
    {dashboard_manage_html}
    {oauth_section_html}
    {credentials_section_html}
    <section>
      <h2>Create user</h2>
      <form method="post" action="/admin/users">
        <div class="row">
          <div>
            <label for="email">Email</label>
            <input id="email" name="email" type="email" required>
          </div>
          <div>
            <label for="password">Password (min 10 chars)</label>
            <input id="password" name="password" type="password" required minlength="10">
          </div>
        </div>
        <div class="row">
          <div>
            <label for="role">Role</label>
            <select id="role" name="role">
              <option value="client">client</option>
              <option value="standard">standard (Sagefrog staff, view-only)</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <div>
            <label for="client_slug">Client slug (for client role, e.g. penn)</label>
            <input id="client_slug" name="client_slug" type="text" placeholder="penn"
              list="clientSlugOptions">
            {client_slug_datalist}
          </div>
        </div>
        <button type="submit" class="primary">Create user</button>
      </form>
    </section>
    <section>
      <h2>Users</h2>
      <table>
        <thead><tr><th>Email</th><th>Role</th><th>Client</th><th>Active</th><th></th></tr></thead>
        <tbody>{user_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>Audit log</h2>
      <p class="muted" style="margin:0 0 12px;font-size:.9rem">Sign-ins, sign-outs, and admin user changes (latest 150).</p>
      <div class="audit-wrap">
        <table>
          <thead><tr><th>When</th><th>Event</th><th>Actor</th><th>Details</th><th class="mono">IP</th></tr></thead>
          <tbody>{audit_table}</tbody>
        </table>
      </div>
    </section>
  </main>
  <script>{dash_delete_js}</script>
</body>
</html>"""
