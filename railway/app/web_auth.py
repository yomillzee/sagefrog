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
from web_users import WebUser

SESSION_USER_ID = "user_id"


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


def _render_dev_notes_section(*, dev_notes: list | None) -> str:
    if dev_notes is None:
        return """
    <section>
      <h2>Dev notes</h2>
      <p class="muted">Connect DATABASE_URL to store internal notes about updates, features, and fixes.</p>
    </section>"""

    category_options = ""
    for cat in ("update", "feature", "bug", "note"):
        category_options += f'<option value="{cat}">{cat}</option>\n'

    note_cards: list[str] = []
    for note in dev_notes:
        note_id = int(note.id)
        cat = _esc(note.category)
        title = _esc(note.title)
        body = _esc(note.body)
        when = _format_audit_time(note.updated_at or note.created_at)
        author = _esc(note.updated_by or note.created_by or "—")
        cat_selected = {
            c: ' selected' if c == note.category else ""
            for c in ("update", "feature", "bug", "note")
        }
        note_cards.append(
            f"""
        <article class="dev-note-card">
          <div class="dev-note-head">
            <span class="dev-note-cat cat-{cat}">{cat}</span>
            <strong>{title}</strong>
            <span class="dev-note-meta">{when} · {author}</span>
          </div>
          <div class="dev-note-body">{body if body else '<span class="muted">No details.</span>'}</div>
          <details class="dev-note-edit">
            <summary>Edit</summary>
            <form method="post" action="/admin/dev-notes/{note_id}" class="dev-note-form">
              <label for="edit-title-{note_id}">Title</label>
              <input id="edit-title-{note_id}" name="title" type="text" required maxlength="200" value="{title}">
              <label for="edit-category-{note_id}">Category</label>
              <select id="edit-category-{note_id}" name="category">
                <option value="update"{cat_selected["update"]}>update</option>
                <option value="feature"{cat_selected["feature"]}>feature</option>
                <option value="bug"{cat_selected["bug"]}>bug</option>
                <option value="note"{cat_selected["note"]}>note</option>
              </select>
              <label for="edit-body-{note_id}">Details</label>
              <textarea id="edit-body-{note_id}" name="body" rows="5" class="dev-note-textarea">{body}</textarea>
              <button type="submit" class="primary">Save changes</button>
            </form>
          </details>
          <form method="post" action="/admin/dev-notes/{note_id}/delete" class="dev-note-delete"
            onsubmit="return confirm('Delete this dev note?');">
            <button type="submit" class="link danger">Delete</button>
          </form>
        </article>"""
        )

    notes_html = (
        "\n".join(note_cards)
        if note_cards
        else '<p class="muted">No dev notes yet. Add one below.</p>'
    )

    return f"""
    <section>
      <h2>Dev notes</h2>
      <p class="muted">Internal notes for updates, features, bugs, and deployment reminders. Visible to admins only.</p>
      <div class="dev-notes-list">
{notes_html}
      </div>
      <form method="post" action="/admin/dev-notes" class="dev-note-add">
        <h3>Add note</h3>
        <div class="row">
          <div>
            <label for="dev-note-title">Title</label>
            <input id="dev-note-title" name="title" type="text" required maxlength="200"
              placeholder="Budget pacing chart shipped">
          </div>
          <div>
            <label for="dev-note-category">Category</label>
            <select id="dev-note-category" name="category">
{category_options}
            </select>
          </div>
        </div>
        <label for="dev-note-body">Details</label>
        <textarea id="dev-note-body" name="body" rows="5" class="dev-note-textarea"
          placeholder="What changed, follow-ups, client impact…"></textarea>
        <button type="submit" class="primary">Add note</button>
      </form>
    </section>"""


def _render_registry_section(
    *,
    ga4_registry: list[dict] | None,
    gsc_registry: list[dict] | None,
) -> str:
    if ga4_registry is None and gsc_registry is None:
        return ""

    def _trunc(val: str, n: int = 30) -> str:
        s = _esc(val or "")
        return f'<span class="trunc mono" title="{s}">{s}</span>' if len(val or "") > n else f'<span class="mono">{s}</span>'

    # --- GA4 table ---
    ga4_rows_html = ""
    for e in (ga4_registry or []):
        slug = _esc(e["slug"])
        src = e.get("source", "env")
        badge = f'<span class="src-db">database</span>' if src == "database" else f'<span class="src-env">env var</span>'
        remove_cell = (
            f'<form method="post" action="/admin/client-registry/ga4/{slug}/delete" style="display:inline">'
            f'<button type="submit" class="link danger" '
            f'onclick="return confirm(\'Remove GA4 config for {slug}?\')">Remove</button></form>'
            if src == "database"
            else '<span class="muted hint">Edit GA4_CLIENTS env var</span>'
        )
        ga4_rows_html += (
            f"<tr>"
            f"<td class=\"mono\">{slug}</td>"
            f"<td>{_trunc(e.get('bq_project_id', ''))}</td>"
            f"<td>{_trunc(e.get('bq_dataset_id', ''))}</td>"
            f"<td>{_trunc(e.get('credentials_env', '') or '(global default)')}</td>"
            f"<td>{badge}</td>"
            f"<td>{remove_cell}</td>"
            f"</tr>\n"
        )
    if not ga4_rows_html:
        ga4_rows_html = '<tr><td colspan="6" class="muted">No GA4 entries yet.</td></tr>'

    # --- GSC table ---
    gsc_rows_html = ""
    for e in (gsc_registry or []):
        slug = _esc(e["slug"])
        src = e.get("source", "env")
        badge = f'<span class="src-db">database</span>' if src == "database" else f'<span class="src-env">env var</span>'
        remove_cell = (
            f'<form method="post" action="/admin/client-registry/gsc/{slug}/delete" style="display:inline">'
            f'<button type="submit" class="link danger" '
            f'onclick="return confirm(\'Remove GSC config for {slug}?\')">Remove</button></form>'
            if src == "database"
            else '<span class="muted hint">Edit GSC_CLIENTS env var</span>'
        )
        native = _esc(e.get("native_dataset_id") or "—")
        gsc_rows_html += (
            f"<tr>"
            f"<td class=\"mono\">{slug}</td>"
            f"<td>{_trunc(e.get('bq_project_id', ''))}</td>"
            f"<td>{_trunc(e.get('bq_dataset_id', ''))}</td>"
            f"<td>{_trunc(e.get('credentials_env', ''))}</td>"
            f"<td class=\"mono\">{native}</td>"
            f"<td>{badge}</td>"
            f"<td>{remove_cell}</td>"
            f"</tr>\n"
        )
    if not gsc_rows_html:
        gsc_rows_html = '<tr><td colspan="7" class="muted">No GSC entries yet.</td></tr>'

    return f"""
    <section>
      <h2>Client BQ Registry</h2>
      <p class="muted" style="margin:0 0 14px">Maps each dashboard to its BigQuery sources. Add a row when onboarding a new client. Entries saved here override the <code>GA4_CLIENTS</code> / <code>GSC_CLIENTS</code> env vars for the same slug.</p>

      <details class="admin-fold" open>
        <summary>GA4 (Google Analytics 4 → BigQuery)</summary>
        <table class="registry-table">
          <thead><tr>
            <th>Slug</th><th>GCP Project</th><th>BQ Dataset</th>
            <th>Credentials Env</th><th>Source</th><th></th>
          </tr></thead>
          <tbody>{ga4_rows_html}</tbody>
        </table>
        <details class="admin-fold" style="margin-top:14px">
          <summary>+ Add / update GA4 entry</summary>
          <form method="post" action="/admin/client-registry/ga4" class="registry-form">
            <div class="row">
              <div>
                <label>Client slug</label>
                <input name="client_slug" type="text" required placeholder="nixon" pattern="[a-z0-9-]+">
                <p class="hint">Must match the dashboard slug exactly.</p>
              </div>
              <div>
                <label>GCP Project ID</label>
                <input name="bq_project_id" type="text" required placeholder="my-gcp-project-id">
              </div>
            </div>
            <div class="row">
              <div>
                <label>BigQuery Dataset ID</label>
                <input name="bq_dataset_id" type="text" required placeholder="analytics_313855909">
                <p class="hint">Usually <code>analytics_&lt;GA4_property_id&gt;</code></p>
              </div>
              <div>
                <label>Credentials Env <span class="muted">(optional)</span></label>
                <input name="credentials_env" type="text" placeholder="GCP_CREDS_CLIENT_BASE64">
                <p class="hint">Railway env var holding base64 service account JSON. Leave blank to use <code>GCP_SERVICE_ACCOUNT_JSON</code>.</p>
              </div>
            </div>
            <button type="submit" class="primary">Save GA4 entry</button>
          </form>
        </details>
      </details>

      <details class="admin-fold" style="margin-top:16px" open>
        <summary>GSC (Search Console → BigQuery sync destination)</summary>
        <table class="registry-table">
          <thead><tr>
            <th>Slug</th><th>GCP Project</th><th>Mart Dataset</th>
            <th>Credentials Env</th><th>Native Dataset</th><th>Source</th><th></th>
          </tr></thead>
          <tbody>{gsc_rows_html}</tbody>
        </table>
        <details class="admin-fold" style="margin-top:14px">
          <summary>+ Add / update GSC entry</summary>
          <form method="post" action="/admin/client-registry/gsc" class="registry-form">
            <div class="row">
              <div>
                <label>Client slug</label>
                <input name="client_slug" type="text" required placeholder="nixon" pattern="[a-z0-9-]+">
              </div>
              <div>
                <label>GCP Project ID</label>
                <input name="bq_project_id" type="text" required placeholder="my-gcp-project-id">
              </div>
            </div>
            <div class="row">
              <div>
                <label>Mart Dataset ID</label>
                <input name="bq_dataset_id" type="text" required placeholder="marketing_marts">
                <p class="hint">BigQuery dataset where GSC data will be written.</p>
              </div>
              <div>
                <label>Credentials Env</label>
                <input name="credentials_env" type="text" placeholder="GCP_SERVICE_ACCOUNT_JSON" value="GCP_SERVICE_ACCOUNT_JSON">
                <p class="hint">Railway env var holding base64 service account JSON.</p>
              </div>
            </div>
            <div class="row">
              <div>
                <label>Native Dataset ID <span class="muted">(optional)</span></label>
                <input name="native_dataset_id" type="text" placeholder="searchconsole_nixon">
                <p class="hint">Only if client has Google's native GSC → BQ bulk export enabled.</p>
              </div>
            </div>
            <button type="submit" class="primary">Save GSC entry</button>
          </form>
        </details>
      </details>

      <div class="registry-cron-note">
        <strong>After adding a new client:</strong> register a daily cron in Railway pointing to<br>
        <code>POST /internal/sync-bq/{{client-slug}}</code> &nbsp;with header&nbsp;
        <code>X-Cron-Secret: &lt;CRON_SECRET&gt;</code>
      </div>
    </section>"""


def render_admin_page(
    *,
    user: WebUser,
    users: list[dict],
    audit_events: list[dict] | None = None,
    dev_notes: list | None = None,
    message: str | None = None,
    error: str | None = None,
    oauth_section_html: str = "",
    ga4_registry: list[dict] | None = None,
    gsc_registry: list[dict] | None = None,
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

    dashboard_manage_html = ""
    client_slug_options = ""
    client_slug_datalist = ""
    dash_delete_js = ""
    try:
        import dashboard_registry

        if dashboard_registry.enabled():
            dash_rows = []
            for row in dashboard_registry.list_clients():
                slug = row.client_slug
                label = row.label
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
                    f'<td><a href="/dashboard/{_esc(slug)}">Open</a> · '
                    f'<a href="/dashboard/{_esc(slug)}/settings">Settings</a> · '
                    f"{clear_snapshot_form}</td>"
                    f"<td>{delete_cell}</td>"
                    f"</tr>"
                )
            dash_table = (
                "\n".join(dash_rows)
                or '<tr><td colspan="4" class="muted">No dashboards yet.</td></tr>'
            )
            for slug, _label in client_config.list_dashboard_clients():
                client_slug_options += f'<option value="{_esc(slug)}"></option>\n'
            if client_slug_options:
                client_slug_datalist = f'<datalist id="clientSlugOptions">{client_slug_options}</datalist>'
            dashboard_manage_html = f"""
    <section>
      <h2>Dashboards</h2>
      <p class="muted">Add client dashboards here. Map ad accounts and GA4 in each dashboard's Settings.</p>
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
      <table class="dash-table">
        <thead><tr><th>Slug</th><th>Name</th><th>Links</th><th></th></tr></thead>
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

    registry_section_html = _render_registry_section(
        ga4_registry=ga4_registry,
        gsc_registry=gsc_registry,
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
    .admin-oauth-section {{ background: #fff; border: 1px solid #b8cfe8; border-radius: 12px;
      padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 12px rgba(11, 92, 171, 0.08); }}
    .admin-oauth-section h2 {{ margin: 0 0 8px; font-size: 1.05rem; color: var(--navy); }}
    .oauth-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-top: 12px; }}
    .oauth-card {{ border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: #fafbfc; }}
    .oauth-card-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }}
    .oauth-card h3 {{ margin: 0; font-size: .95rem; color: var(--navy); }}
    .oauth-actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }}
    .oauth-details {{ margin: 8px 0 0; padding-left: 1.1rem; font-size: .82rem; color: var(--navy); }}
    .oauth-details li {{ margin: 4px 0; }}
    .oauth-status-msg {{ margin: 8px 0 0; font-size: .88rem; color: var(--navy); font-weight: 600; }}
    .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600; }}
    .badge.ok {{ background: #e8f5e9; color: #1b5e20; }}
    .badge.err {{ background: #fdecea; color: #b42318; }}
    .btn {{ display: inline-block; padding: 9px 16px; border-radius: 8px; border: 0; font-weight: 600;
      cursor: pointer; font-size: .88rem; text-decoration: none; }}
    .btn.primary {{ background: var(--accent); color: #fff; }}
    .btn.secondary {{ background: #fff; color: var(--accent); border: 1px solid var(--border); }}
    .inline-form {{ display: inline; margin: 0; }}
    .admin-fold {{ margin-top: 14px; font-size: .88rem; }}
    .admin-fold summary {{ cursor: pointer; font-weight: 600; color: var(--navy); }}
    ul.checklist {{ margin: 8px 0 0; padding-left: 1.2rem; }}
    ul.checklist.mono {{ font-family: ui-monospace, monospace; font-size: .82rem; }}
    .settings-fold {{ margin-top: 8px; font-size: .82rem; }}
    .settings-fold summary {{ cursor: pointer; color: var(--muted); }}
    .hint {{ color: var(--muted); font-size: .82rem; margin: 4px 0 0; }}
    .hint.mono {{ font-family: ui-monospace, monospace; }}
    .dash-add-form {{ margin-bottom: 18px; }}
    .dash-table {{ margin-top: 8px; }}
    .dash-delete-fold {{ margin: 0; font-size: .88rem; }}
    .dash-delete-fold summary {{ cursor: pointer; list-style: none; }}
    .dash-delete-fold summary::-webkit-details-marker {{ display: none; }}
    .dash-delete-form {{ margin-top: 8px; padding: 10px; background: #fafbfc; border-radius: 8px; border: 1px solid var(--border); }}
    .dash-delete-form input {{ max-width: 100%; margin-bottom: 8px; }}
    .dev-notes-list {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 20px; }}
    .dev-note-card {{ border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: #fafbfc; }}
    .dev-note-head {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }}
    .dev-note-meta {{ margin-left: auto; font-size: .78rem; color: var(--muted); }}
    .dev-note-cat {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: .72rem;
      font-weight: 700; text-transform: uppercase; letter-spacing: .03em; }}
    .dev-note-cat.cat-update {{ background: #e3f2fd; color: #1565c0; }}
    .dev-note-cat.cat-feature {{ background: #e8f5e9; color: #2e7d32; }}
    .dev-note-cat.cat-bug {{ background: #fdecea; color: #b42318; }}
    .dev-note-cat.cat-note {{ background: #eceff3; color: #5a6578; }}
    .dev-note-body {{ font-size: .9rem; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
    .dev-note-edit {{ margin-top: 10px; font-size: .88rem; }}
    .dev-note-edit summary {{ cursor: pointer; font-weight: 600; color: var(--accent); }}
    .dev-note-form, .dev-note-add {{ margin-top: 12px; }}
    .dev-note-add h3 {{ margin: 0 0 12px; font-size: .95rem; color: var(--navy); }}
    .dev-note-textarea {{ width: 100%; max-width: 100%; min-height: 100px; padding: 10px;
      border: 1px solid var(--border); border-radius: 8px; font: inherit; resize: vertical; }}
    .dev-note-delete {{ margin-top: 8px; }}
    .registry-table {{ margin: 12px 0; font-size: .88rem; }}
    .registry-table td {{ vertical-align: middle; }}
    .registry-form {{ margin-top: 14px; padding: 14px; background: #fafbfc; border: 1px solid var(--border); border-radius: 8px; }}
    .registry-form input {{ max-width: 280px; }}
    .registry-cron-note {{ margin-top: 16px; padding: 12px 14px; background: #f0f4ff; border: 1px solid #c7d7f5; border-radius: 8px; font-size: .88rem; line-height: 1.5; }}
    .registry-cron-note code {{ background: #e8eef8; padding: 2px 5px; border-radius: 4px; font-family: ui-monospace, monospace; font-size: .82rem; }}
    .src-db {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: .72rem; font-weight: 700; background: #e3f2fd; color: #1565c0; }}
    .src-env {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: .72rem; font-weight: 700; background: #eceff3; color: #5a6578; }}
    .trunc {{ max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Admin</h1>
      <span class="who">Signed in as {_esc(user.email)}</span>
    </div>
    <div>
      <form method="post" action="/logout" style="display:inline"><button type="submit" class="link">Sign out</button></form>
    </div>
  </header>
  <main>
    {notice}
    {oauth_section_html}
    {dashboard_manage_html}
    {registry_section_html}
    {_render_dev_notes_section(dev_notes=dev_notes)}
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
