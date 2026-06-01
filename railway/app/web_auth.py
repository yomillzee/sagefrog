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

import web_users
import audit_log
from web_users import WebUser

SESSION_USER_ID = "user_id"


def session_secret() -> str:
    secret = (
        (os.getenv("AUTH_SESSION_SECRET") or "").strip()
        or (os.getenv("CRON_SECRET") or "").strip()
        or (os.getenv("API_KEY") or "").strip()
    )
    if not secret:
        raise RuntimeError(
            "Set AUTH_SESSION_SECRET (recommended) or CRON_SECRET/API_KEY for signed session cookies."
        )
    return secret


def https_only_cookies() -> bool:
    raw = (os.getenv("AUTH_SESSION_HTTPS_ONLY") or "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    return bool((os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip())


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


def get_current_user(request: Request) -> WebUser | None:
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


def login_user(request: Request, user: WebUser) -> None:
    request.session[SESSION_USER_ID] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


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
    return bool(key and key.strip() == expected)


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
    err = f'<p class="error">{_esc(error)}</p>' if error else ""
    nxt = _esc(next_path or "/admin")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · EOS Ads</title>
  <style>
    :root {{ --navy: #0a2540; --accent: #0b5cab; --border: #d8dee8; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
      font-family: system-ui, sans-serif; background: #eef1f5; color: #0f1c2e; }}
    .card {{ width: min(400px, 92vw); background: #fff; border: 1px solid var(--border);
      border-radius: 12px; padding: 28px 24px; box-shadow: 0 8px 32px rgba(10,37,64,.08); }}
    h1 {{ margin: 0 0 8px; font-size: 1.35rem; color: var(--navy); }}
    p.sub {{ margin: 0 0 20px; color: #5a6578; font-size: .92rem; }}
    label {{ display: block; font-size: .85rem; font-weight: 600; margin-bottom: 6px; }}
    input {{ width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
      font-size: 1rem; margin-bottom: 14px; }}
    button {{ width: 100%; padding: 12px; border: 0; border-radius: 8px; background: var(--accent);
      color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; }}
    button:hover {{ filter: brightness(1.05); }}
    .error {{ color: #b42318; font-size: .9rem; margin: 0 0 12px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Sign in</h1>
    <p class="sub">EOS Ads dashboards</p>
    {err}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{nxt}">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="username" required>
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
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
) -> str:
    notice = ""
    if message:
        notice += f'<div class="notice ok">{_esc(message)}</div>'
    if error:
        notice += f'<div class="notice err">{_esc(error)}</div>'

    rows = []
    for u in users:
        slug = u.get("client_slug") or "—"
        actions = ""
        if int(u["id"]) != user.id:
            actions = (
                f'<form method="post" action="/admin/users/{int(u["id"])}/deactivate" '
                f'onsubmit="return confirm(\'Deactivate this user?\');">'
                f'<button type="submit" class="link danger">Deactivate</button></form>'
            )
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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin · EOS Ads</title>
  <style>
    :root {{ --navy: #0a2540; --accent: #0b5cab; --border: #d8dee8; --muted: #5a6578; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #eef1f5; color: #0f1c2e; }}
    header {{ background: var(--navy); color: #fff; padding: 16px 24px; display: flex;
      align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
    header h1 {{ margin: 0; font-size: 1.2rem; }}
    header .who {{ font-size: .88rem; opacity: .85; }}
  header a, header button.link {{ color: #fff; text-decoration: none; background: none; border: 0;
      cursor: pointer; font: inherit; opacity: .9; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 24px 20px 48px; }}
    section {{ background: #fff; border: 1px solid var(--border); border-radius: 12px;
      padding: 20px; margin-bottom: 20px; }}
    h2 {{ margin: 0 0 16px; font-size: 1.05rem; color: var(--navy); }}
    table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
    th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--muted); font-weight: 600; font-size: .8rem; text-transform: uppercase; }}
    td.mono, th.mono {{ font-family: ui-monospace, monospace; font-size: .82rem; }}
    .audit-wrap {{ max-height: 420px; overflow: auto; }}
    label {{ display: block; font-size: .85rem; font-weight: 600; margin-bottom: 6px; }}
    input, select {{ width: 100%; max-width: 320px; padding: 8px 10px; border: 1px solid var(--border);
      border-radius: 8px; margin-bottom: 12px; }}
    .row {{ display: flex; flex-wrap: wrap; gap: 16px; }}
    .row > div {{ flex: 1; min-width: 180px; }}
    button.primary {{ padding: 10px 18px; border: 0; border-radius: 8px; background: var(--accent);
      color: #fff; font-weight: 600; cursor: pointer; }}
    button.link {{ background: none; border: 0; color: var(--accent); cursor: pointer; padding: 0; }}
    button.link.danger {{ color: #b42318; }}
    .notice {{ padding: 10px 12px; border-radius: 8px; margin-bottom: 16px; font-size: .9rem; }}
    .notice.ok {{ background: #e8f5e9; color: #1b5e20; }}
    .notice.err {{ background: #fdecea; color: #b42318; }}
    .muted {{ color: var(--muted); }}
    ul.links {{ margin: 0; padding-left: 1.2rem; }}
    ul.links a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Admin</h1>
      <span class="who">Signed in as {_esc(user.email)}</span>
    </div>
    <div>
      <a href="/dashboard/penn">Penn dashboard</a>
      ·
      <form method="post" action="/logout" style="display:inline"><button type="submit" class="link">Sign out</button></form>
    </div>
  </header>
  <main>
    {notice}
    <section>
      <h2>Dashboards</h2>
      <ul class="links">
        <li><a href="/dashboard/penn">Penn Community Bank</a></li>
      </ul>
    </section>
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
              <option value="admin">admin</option>
            </select>
          </div>
          <div>
            <label for="client_slug">Client slug (for client role, e.g. penn)</label>
            <input id="client_slug" name="client_slug" type="text" placeholder="penn">
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
</body>
</html>"""
