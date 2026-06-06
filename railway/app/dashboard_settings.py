"""Per-client dashboard settings page: auth, API, and data connections."""

from __future__ import annotations

import html
import os
from typing import Any
from urllib.parse import quote

import auth as google_auth
import bigquery_service
import client_config
import dashboard_service
import dashboard_snapshots
import dashboard_theme
import ga4_clients
import linkedin_auth
import meta_auth
import oauth_flows
import oauth_store
import security
import web_users
from penn_config import PennDashboardConfig, load_penn_config


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else ""), quote=True)


def _status_badge(ok: bool, *, ok_label: str = "Connected", fail_label: str = "Not connected") -> str:
    cls = "badge ok" if ok else "badge err"
    label = ok_label if ok else fail_label
    return f'<span class="{cls}">{_esc(label)}</span>'


def _settings_url(*, client_slug: str, access_key: str | None, use_session: bool) -> str:
    base = f"/dashboard/{client_slug}/settings"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def _dashboard_url(*, client_slug: str, access_key: str | None, use_session: bool) -> str:
    base = f"/dashboard/{client_slug}"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def _favicon_head_html() -> str:
    return dashboard_service._favicon_head_html()


def build_auth_status() -> dict[str, Any]:
    session_enabled = web_users.enabled()
    api_key_set = bool(security.configured_api_key())
    dashboard_secret = bool(dashboard_service.configured_dashboard_secret())
    session_secret = bool(
        (os.getenv("AUTH_SESSION_SECRET") or os.getenv("CRON_SECRET") or os.getenv("API_KEY") or "").strip()
    )
    bootstrap = bool(
        (os.getenv("AUTH_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
        and (os.getenv("AUTH_BOOTSTRAP_ADMIN_PASSWORD") or "").strip()
    )
    return {
        "session_auth": session_enabled,
        "session_secret": session_secret,
        "api_key": api_key_set,
        "dashboard_secret": dashboard_secret,
        "bootstrap_admin_configured": bootstrap,
        "overall_ok": session_enabled and session_secret and api_key_set,
    }


def build_api_status() -> dict[str, Any]:
    google = google_auth.env_summary()
    linkedin = linkedin_auth.env_summary()
    meta = meta_auth.env_summary()
    ga4 = bigquery_service.env_summary()
    ga4_registry = ga4_clients.list_clients_public()

    google_ok = all(
        google.get(k)
        for k in ("has_developer_token", "has_client_id", "has_client_secret", "has_refresh_token")
    )
    linkedin_ok = all(
        linkedin.get(k) for k in ("has_client_id", "has_client_secret", "has_refresh_token")
    )
    meta_ok = all(meta.get(k) for k in ("has_app_id", "has_app_secret", "has_access_token", "has_business_id"))
    ga4_ok = bool(
        ga4.get("has_gcp_service_account_json")
        and ga4.get("gcp_service_account_json_parse_ok")
        and (ga4.get("has_bq_project_id") or ga4_registry)
    )

    return {
        "google": {"env": google, "ok": google_ok},
        "linkedin": {"env": linkedin, "ok": linkedin_ok},
        "meta": {"env": meta, "ok": meta_ok},
        "ga4": {"env": ga4, "registry_count": len(ga4_registry), "ok": ga4_ok},
        "overall_ok": google_ok and linkedin_ok and meta_ok and ga4_ok,
    }


def _oauth_platform_card_html(
    *,
    platform: str,
    label: str,
    settings_url: str,
    session_is_admin: bool,
    can_manage_oauth: bool = False,
    env_summary: dict[str, Any],
) -> str:
    prereq = oauth_flows.connect_prerequisites(platform)
    pub = oauth_store.public_status(platform)
    return_to = quote(settings_url, safe="")
    status_badge = _status_badge(pub.connected, ok_label="Connected", fail_label="Not connected")
    source = _esc(pub.source)
    connected_meta = ""
    if pub.connected_by or pub.connected_at:
        who = _esc(pub.connected_by or "—")
        when = _esc((pub.connected_at or "")[:19])
        connected_meta = f'<p class="muted">Token source: {source} · connected by {who} · {when} UTC</p>'

    actions = ""
    if can_manage_oauth and oauth_store.enabled():
        connect_url = f"/oauth/{platform}/connect?return_to={return_to}"
        if prereq.get("ready"):
            actions += f'<a class="btn primary" href="{connect_url}">Connect {label}</a> '
        else:
            missing = ", ".join(prereq.get("missing") or [])
            actions += f'<p class="muted">Set {_esc(missing)} in Railway before connecting.</p>'
        if pub.source == "database":
            actions += f"""
            <form method="post" action="/oauth/{platform}/disconnect" class="inline-form"
              onsubmit="return confirm('Disconnect {label}? Database token will be removed.');">
              <input type="hidden" name="return_to" value="{_esc(settings_url)}">
              <button type="submit" class="btn secondary">Disconnect</button>
            </form>
            """
    elif can_manage_oauth and not oauth_store.enabled():
        actions = '<p class="muted">Attach Postgres (DATABASE_URL) to store OAuth tokens from Connect.</p>'

    callback = _esc(oauth_flows.callback_url(platform))
    note = _esc(prereq.get("note") or "")
    dev_details = f"""
      <details class="settings-fold settings-fold--inline">
        <summary>Developer details</summary>
        <p class="hint mono">Callback: {callback}</p>
      </details>"""
    return f"""
    <div class="oauth-card">
      <div class="oauth-card-head">
        <h3>{_esc(label)}</h3>
        {status_badge}
      </div>
      {connected_meta}
      <p class="muted">{note}</p>
      <div class="oauth-actions">{actions}</div>
      {dev_details}
    </div>
    """


def _oauth_connect_section_html(
    *,
    settings_url: str,
    session_is_admin: bool,
    can_manage_oauth: bool = False,
    api: dict[str, Any],
) -> str:
    base = oauth_flows.public_base_url()
    cards = [
        _oauth_platform_card_html(
            platform="google_ads",
            label="Google Ads",
            settings_url=settings_url,
            session_is_admin=session_is_admin,
            can_manage_oauth=can_manage_oauth,
            env_summary=api["google"]["env"],
        ),
        _oauth_platform_card_html(
            platform="linkedin",
            label="LinkedIn",
            settings_url=settings_url,
            session_is_admin=session_is_admin,
            can_manage_oauth=can_manage_oauth,
            env_summary=api["linkedin"]["env"],
        ),
        _oauth_platform_card_html(
            platform="meta",
            label="Meta",
            settings_url=settings_url,
            session_is_admin=session_is_admin,
            can_manage_oauth=can_manage_oauth,
            env_summary=api["meta"]["env"],
        ),
    ]
    return f"""
    <section class="panel panel--primary">
      <h2>1. Connect ad platforms</h2>
      <p class="muted">Sign in as admin, then click Connect for each platform. App IDs stay in Railway; tokens save to Postgres only.</p>
      <div class="oauth-grid">{"".join(cards)}</div>
    </section>
    """


def _setup_checklist_html(*, api: dict[str, Any], cfg: PennDashboardConfig, ga4_ok: bool) -> str:
    steps = [
        ("Connect Google Ads", api["google"]["ok"]),
        ("Connect LinkedIn", api["linkedin"]["ok"]),
        ("Connect Meta", api["meta"]["ok"]),
        ("Map client account IDs", bool(cfg.google_customer_id or cfg.linkedin_account_id or cfg.meta_account_id)),
        ("Configure GA4 (Railway)", ga4_ok),
    ]
    items = []
    for label, done in steps:
        badge = _status_badge(done, ok_label="Done", fail_label="To do")
        items.append(f"<li><span>{_esc(label)}</span> {badge}</li>")
    return f'<ol class="setup-checklist">{"".join(items)}</ol>'


def _settings_page_css() -> str:
    return """
    .panel { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
    .panel--primary { border-color: #b8cfe8; box-shadow: 0 2px 12px rgba(11, 92, 171, 0.08); }
    .panel h2 { margin: 0 0 8px; font-size: 1.05rem; color: var(--navy); }
    .panel h3 { margin: 0 0 8px; font-size: .95rem; color: var(--navy); }
    p { margin: 0 0 10px; line-height: 1.45; }
    .muted { color: var(--muted); font-size: .92rem; }
    .hint { color: var(--muted); font-size: .82rem; margin: 4px 0 0; }
    .notice { padding: 10px 12px; border-radius: 8px; margin-bottom: 16px; font-size: .9rem; }
    .notice.ok { background: var(--ok-bg); color: var(--ok); }
    .notice.err { background: var(--err-bg); color: var(--err); }
    .setup-checklist { margin: 12px 0 0; padding: 0; list-style: none; }
    .setup-checklist li { display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 10px 0; border-bottom: 1px solid var(--border); font-size: .92rem; }
    .setup-checklist li:last-child { border-bottom: 0; }
    .oauth-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-top: 12px; }
    .oauth-card { border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: #fafbfc; }
    .oauth-card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
    .oauth-card h3 { margin: 0; font-size: .95rem; }
    .oauth-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600; }
    .badge.ok { background: var(--ok-bg); color: var(--ok); }
    .badge.err { background: var(--err-bg); color: var(--err); }
    .btn { display: inline-block; padding: 9px 16px; border-radius: 8px; border: 0; font-weight: 600;
      cursor: pointer; font-size: .88rem; text-decoration: none; }
    .btn.primary { background: var(--accent); color: #fff; }
    .btn.secondary { background: #fff; color: var(--accent); border: 1px solid var(--border); }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }
    .inline-form { display: inline; margin: 0; }
    .form-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; margin-bottom: 14px; }
    label { display: block; font-size: .85rem; font-weight: 600; margin-bottom: 6px; }
    input[type="text"], input[type="password"] { width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; }
    input[type="color"] { width: 100%; height: 42px; padding: 2px; border: 1px solid var(--border); border-radius: 8px; cursor: pointer; background: #fff; }
    .color-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin-bottom: 16px; }
    .color-field label { margin-bottom: 6px; }
    .theme-group-title { margin: 16px 0 10px; font-size: .88rem; font-weight: 700; color: var(--navy); text-transform: uppercase; letter-spacing: .04em; }
    .theme-group-title:first-of-type { margin-top: 0; }
    textarea.rules-textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
      font-family: ui-monospace, monospace; font-size: .84rem; line-height: 1.45; min-height: 160px; margin-bottom: 12px; }
    .status-table { width: 100%; border-collapse: collapse; font-size: .88rem; margin-top: 8px; }
    .status-table th, .status-table td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
    .status-table th { color: var(--muted); font-size: .75rem; text-transform: uppercase; }
    .mono { font-family: ui-monospace, monospace; font-size: .85rem; }
    .settings-fold { margin-top: 12px; border: 1px solid var(--border); border-radius: 10px; padding: 0 14px; background: #fafbfc; }
    .settings-fold--inline { margin-top: 10px; padding: 8px 10px; }
    .settings-fold summary { cursor: pointer; font-weight: 600; font-size: .9rem; color: var(--navy); padding: 12px 0; }
    .settings-fold[open] summary { border-bottom: 1px solid var(--border); margin-bottom: 10px; }
    .settings-fold .fold-body { padding-bottom: 14px; }
    .checklist { margin: 8px 0 0; padding-left: 1.2rem; line-height: 1.55; font-size: .88rem; }
    .error-list { margin: 8px 0 0; padding-left: 1.2rem; color: var(--err); font-size: .88rem; }
    .refresh-bar { margin-top: 8px; }
    .refresh-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .refresh-btn { padding: 9px 16px; border-radius: 8px; border: 1px solid var(--accent); background: var(--accent);
      color: #fff; cursor: pointer; font-weight: 600; font-size: .88rem; }
    .refresh-btn--secondary { background: #fff; color: var(--accent); }
    .refresh-btn:disabled { opacity: .5; cursor: not-allowed; }
    .refresh-form { display: inline; margin: 0; }
    .insights-editor { margin-top: 8px; }
    .insights-textarea { width: 100%; min-height: 120px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font: inherit; }
    .meta-manual { margin-top: 12px; }
    """


def probe_client_connections(cfg: PennDashboardConfig) -> dict[str, Any]:
    """Live connection tests for the configured client account IDs."""
    import google_ads_service
    import linkedin_service
    import meta_service

    results: dict[str, Any] = {}

    if cfg.google_customer_id:
        try:
            token = google_ads_service.test_refresh_token()
            if token.get("ok"):
                meta_row = google_ads_service.get_account_metadata(customer_id=cfg.google_customer_id)
                results["google"] = {
                    "ok": True,
                    "message": f"OAuth OK · account {meta_row.get('descriptive_name') or cfg.google_customer_id}",
                }
            else:
                results["google"] = {
                    "ok": False,
                    "message": token.get("error") or token.get("message") or "OAuth failed",
                }
        except Exception as exc:
            results["google"] = {"ok": False, "message": str(exc)[:300]}
    else:
        results["google"] = {"ok": False, "message": "No Google customer ID configured."}

    if cfg.linkedin_account_id:
        try:
            token = linkedin_service.test_refresh_token()
            if token.get("ok"):
                results["linkedin"] = {
                    "ok": True,
                    "message": f"OAuth OK · account {cfg.linkedin_account_id}",
                }
            else:
                results["linkedin"] = {
                    "ok": False,
                    "message": token.get("error") or token.get("message") or "OAuth failed",
                }
        except Exception as exc:
            results["linkedin"] = {"ok": False, "message": str(exc)[:300]}
    else:
        results["linkedin"] = {"ok": False, "message": "No LinkedIn account ID configured."}

    if cfg.meta_account_id:
        try:
            token = meta_service.test_access_token()
            if token.get("ok"):
                access = meta_service.test_ads_read_access(cfg.meta_account_id)
                if access.get("ok"):
                    results["meta"] = {
                        "ok": True,
                        "message": f"Token OK · ads_read verified for {cfg.meta_account_id}",
                    }
                else:
                    results["meta"] = {
                        "ok": False,
                        "message": access.get("error") or "ads_read check failed",
                    }
            else:
                results["meta"] = {
                    "ok": False,
                    "message": token.get("error") or token.get("message") or "Token failed",
                }
        except Exception as exc:
            results["meta"] = {"ok": False, "message": str(exc)[:300]}
    else:
        results["meta"] = {"ok": False, "message": "No Meta account ID configured."}

    if cfg.ga4_client_key:
        try:
            target = ga4_clients.resolve_target(client_key=cfg.ga4_client_key)
            client = bigquery_service.build_client(project_id=target.bq_project_id)
            client.query(
                f"SELECT 1 FROM `{target.bq_project_id}.{target.bq_dataset_id}.events_*` LIMIT 1"
            ).result(timeout=30)
            results["ga4"] = {
                "ok": True,
                "message": (
                    f"BigQuery OK · {target.bq_project_id}/{target.bq_dataset_id} "
                    f"(property {target.account_id})"
                ),
            }
        except Exception as exc:
            results["ga4"] = {"ok": False, "message": str(exc)[:300]}
    else:
        results["ga4"] = {"ok": False, "message": "No GA4 client key configured."}

    results["overall_ok"] = all(
        results.get(p, {}).get("ok") for p in ("google", "linkedin", "meta", "ga4")
    )
    return results


def _ga4_target_summary(cfg: PennDashboardConfig) -> dict[str, Any]:
    try:
        target = ga4_clients.resolve_target(client_key=cfg.ga4_client_key)
        return {
            "ok": True,
            "project": target.bq_project_id,
            "dataset": target.bq_dataset_id,
            "account_id": target.account_id,
            "label": target.label,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _sync_meta_html(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return '<p class="muted">No snapshot yet. Run a full refresh or wait for nightly cron.</p>'
    refreshed = snapshot.get("refreshed_at") or "—"
    sm = snapshot.get("sync_meta") or {}
    trigger = sm.get("trigger") or snapshot.get("refresh_mode") or "—"
    errors = snapshot.get("errors") or {}
    err_lines = []
    for platform, msg in errors.items():
        if msg:
            err_lines.append(f"<li><strong>{_esc(platform)}:</strong> {_esc(str(msg)[:200])}</li>")
    err_html = ""
    if err_lines:
        err_html = f'<ul class="error-list">{"".join(err_lines)}</ul>'
    warehouse = snapshot.get("warehouse_sync") or {}
    wh_bits = []
    for key, val in warehouse.items():
        if isinstance(val, dict) and val.get("rows_upserted") is not None:
            wh_bits.append(f"{_esc(key)}: {int(val.get('rows_upserted') or 0)} rows")
    wh_html = ""
    if wh_bits:
        wh_html = f'<p class="muted">Warehouse sync: {" · ".join(wh_bits)}</p>'
    return f"""
    <p><strong>Last refresh:</strong> {_esc(str(refreshed)[:19])} UTC · source {_esc(str(trigger))}</p>
    {wh_html}
    {err_html}
    """


def _connection_row(label: str, ok: bool, detail: str) -> str:
    return f"""
    <tr>
      <td>{_esc(label)}</td>
      <td>{_status_badge(ok)}</td>
      <td class="detail">{_esc(detail)}</td>
    </tr>"""


def _theme_color_input(name: str, label: str, value: str) -> str:
    return f"""
    <div class="color-field">
      <label for="theme_{_esc(name)}">{_esc(label)}</label>
      <input type="color" id="theme_{_esc(name)}" name="{_esc(name)}" value="{_esc(value)}">
    </div>"""


def _brand_colors_section_html(
    *,
    settings_url: str,
    theme: dict[str, str],
) -> str:
    return f"""
    <section class="panel">
      <h2>Brand colors</h2>
      <p class="muted">Customize the sidebar gradient and channel filter colors for this dashboard.</p>
      <form method="post" action="{settings_url}">
        <input type="hidden" name="action" value="save_theme">
        <p class="theme-group-title">Sidebar</p>
        <div class="color-grid">
          {_theme_color_input("sidebar_from", "Top color", theme["sidebar_from"])}
          {_theme_color_input("sidebar_to", "Bottom color", theme["sidebar_to"])}
        </div>
        <p class="theme-group-title">Google</p>
        <div class="color-grid">
          {_theme_color_input("google", "Filter color", theme["google"])}
          {_theme_color_input("google_bg", "Background tint", theme["google_bg"])}
        </div>
        <p class="theme-group-title">LinkedIn</p>
        <div class="color-grid">
          {_theme_color_input("linkedin", "Filter color", theme["linkedin"])}
          {_theme_color_input("linkedin_bg", "Background tint", theme["linkedin_bg"])}
        </div>
        <p class="theme-group-title">Meta</p>
        <div class="color-grid">
          {_theme_color_input("meta", "Filter color", theme["meta"])}
          {_theme_color_input("meta_bg", "Background tint", theme["meta_bg"])}
        </div>
        <p class="theme-group-title">Organic</p>
        <div class="color-grid">
          {_theme_color_input("organic", "Filter color", theme["organic"])}
          {_theme_color_input("organic_bg", "Background tint", theme["organic_bg"])}
        </div>
        <p class="theme-group-title">Business lines</p>
        <p class="hint">One color for all business line filters (Home Equity, Cash Bonus, etc.).</p>
        <div class="color-grid">
          {_theme_color_input("business_line", "Filter color", theme["business_line"])}
          {_theme_color_input("business_line_bg", "Background tint", theme["business_line_bg"])}
        </div>
        <button type="submit" class="btn primary">Save brand colors</button>
      </form>
    </section>"""


def render_settings_html(
    *,
    client_slug: str,
    cfg: PennDashboardConfig,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
    flash_error: str | None = None,
    probe_results: dict[str, Any] | None = None,
    db_config_updated_at: str | None = None,
    oauth_connected: str | None = None,
) -> str:
    slug = client_slug.strip().lower()
    settings_url = _settings_url(client_slug=slug, access_key=access_key, use_session=use_session)
    snapshot = dashboard_snapshots.get_snapshot(slug)
    auth = build_auth_status()
    api = build_api_status()
    ga4_target = _ga4_target_summary(cfg)
    can_edit = session_is_admin if use_session else bool(access_key)
    can_manage_oauth = session_is_admin and use_session
    db_editable = web_users.enabled() and session_is_admin

    notice = ""
    if oauth_connected:
        labels = {"google_ads": "Google Ads", "linkedin": "LinkedIn", "meta": "Meta"}
        label = labels.get(oauth_connected, oauth_connected)
        notice += f'<div class="notice ok">{_esc(label)} connected successfully.</div>'
    if flash_message:
        notice += f'<div class="notice ok">{_esc(flash_message)}</div>'
    if flash_error:
        notice += f'<div class="notice err">{_esc(flash_error)}</div>'

    auth_rows = [
        _connection_row(
            "Browser login (Postgres)",
            auth["session_auth"],
            "DATABASE_URL attached — users sign in at /login",
        ),
        _connection_row(
            "Session signing secret",
            auth["session_secret"],
            "AUTH_SESSION_SECRET or CRON_SECRET/API_KEY fallback",
        ),
        _connection_row(
            "ChatGPT / API key",
            auth["api_key"],
            "API_KEY in Railway — protects /google-ads, /linkedin, /meta, /ga4 routes",
        ),
        _connection_row(
            "Legacy dashboard key",
            auth["dashboard_secret"],
            "CRON_SECRET or DASHBOARD_SECRET for ?key= links",
        ),
    ]

    api_rows = [
        _connection_row(
            "Google Ads OAuth",
            api["google"]["ok"],
            "GOOGLE_ADS_* credentials in Railway",
        ),
        _connection_row(
            "LinkedIn OAuth",
            api["linkedin"]["ok"],
            "LINKEDIN_* refresh token in Railway",
        ),
        _connection_row(
            "Meta Business Manager",
            api["meta"]["ok"],
            "META_* access token in Railway",
        ),
        _connection_row(
            "GA4 / BigQuery",
            api["ga4"]["ok"],
            f"GCP service account + {api['ga4']['registry_count']} GA4_CLIENTS entries",
        ),
    ]

    ga4_detail = "—"
    if ga4_target.get("ok"):
        ga4_detail = (
            f"{ga4_target.get('project')}/{ga4_target.get('dataset')} "
            f"(property {ga4_target.get('account_id')})"
        )
    elif ga4_target.get("error"):
        ga4_detail = str(ga4_target.get("error"))

    account_rows = f"""
    <tr><td>Google Ads customer ID</td><td class="mono">{_esc(cfg.google_customer_id or "—")}</td></tr>
    <tr><td>LinkedIn account ID</td><td class="mono">{_esc(cfg.linkedin_account_id or "—")}</td></tr>
    <tr><td>Meta ad account ID</td><td class="mono">{_esc(cfg.meta_account_id or "—")}</td></tr>
    <tr><td>GA4 client key</td><td class="mono">{_esc(cfg.ga4_client_key or "—")}</td></tr>
    <tr><td>GA4 BigQuery target</td><td class="mono">{_esc(ga4_detail)}</td></tr>
    """

    edit_form = ""
    if db_editable:
        meta = ""
        if db_config_updated_at:
            meta = f'<p class="muted">Last saved: {_esc(db_config_updated_at[:19])} UTC</p>'
        edit_form = f"""
        <section class="panel panel--primary">
          <h2>2. Map client account IDs</h2>
          <p class="muted">Point this dashboard at the correct Google, LinkedIn, Meta, and GA4 accounts.</p>
          {meta}
          <form method="post" action="{settings_url}">
            <input type="hidden" name="action" value="save">
            <div class="form-grid">
              <div>
                <label for="label">Display label</label>
                <input id="label" name="label" type="text" value="{_esc(cfg.label)}" maxlength="120">
              </div>
              <div>
                <label for="google_customer_id">Google Ads customer ID</label>
                <input id="google_customer_id" name="google_customer_id" type="text"
                  value="{_esc(cfg.google_customer_id or '')}" placeholder="1549971930">
              </div>
              <div>
                <label for="linkedin_account_id">LinkedIn account ID</label>
                <input id="linkedin_account_id" name="linkedin_account_id" type="text"
                  value="{_esc(cfg.linkedin_account_id or '')}" placeholder="508590994">
              </div>
              <div>
                <label for="meta_account_id">Meta ad account ID</label>
                <input id="meta_account_id" name="meta_account_id" type="text"
                  value="{_esc(cfg.meta_account_id or '')}" placeholder="2581574002135957">
              </div>
              <div>
                <label for="ga4_client_key">GA4 client key</label>
                <input id="ga4_client_key" name="ga4_client_key" type="text"
                  value="{_esc(cfg.ga4_client_key or '')}" placeholder="penn">
                <p class="hint">Must match a key in GA4_CLIENTS (Railway).</p>
              </div>
            </div>
            <button type="submit" class="btn primary">Save mapping</button>
          </form>
        </section>
        """
    elif use_session and not session_is_admin:
        edit_form = """
        <section class="panel">
          <h2>2. Map client account IDs</h2>
          <p class="muted">Only admins can edit account IDs. Ask your agency admin to update connections.</p>
        </section>
        """
    else:
        edit_form = f"""
        <section class="panel">
          <h2>2. Map client account IDs</h2>
          <p class="muted">Current IDs: Google {_esc(cfg.google_customer_id or "—")}, LinkedIn {_esc(cfg.linkedin_account_id or "—")}, Meta {_esc(cfg.meta_account_id or "—")}.</p>
        </section>
        """

    test_btn = ""
    if can_edit:
        test_btn = f"""
        <form method="post" action="{settings_url}" class="inline-form">
          <input type="hidden" name="action" value="test">
          <button type="submit" class="btn secondary">Test connections</button>
        </form>
        """

    refresh_block = dashboard_service._refresh_toolbar(
        client_slug=slug,
        access_key=access_key,
        use_session=use_session,
        snapshot=snapshot,
        flash_message=None,
    )

    oauth_section = _oauth_connect_section_html(
        settings_url=settings_url,
        session_is_admin=session_is_admin if use_session else False,
        can_manage_oauth=can_manage_oauth,
        api=api,
    )

    checklist = _setup_checklist_html(api=api, cfg=cfg, ga4_ok=api["ga4"]["ok"])

    bl_rules_section = ""
    if slug == "penn" and db_editable:
        import business_line_rules as bl_rules

        rules_text = bl_rules.rules_to_text(bl_rules.get_rules(slug))
        bl_rules_section = f"""
        <section class="panel">
          <h2>Business line matching</h2>
          <p class="muted">Campaigns are categorized by keyword substring match. Custom rules run <strong>before</strong> built-in Penn defaults (Home Equity, Cash Bonus, HYS, CD / Certificate, Commercial). LinkedIn campaign group names are included when matching.</p>
          <p class="hint">One rule per line: <code>Business line name | keyword, keyword, keyword</code></p>
          <form method="post" action="{settings_url}">
            <input type="hidden" name="action" value="save_business_line_rules">
            <label for="business_line_rules">Custom keyword rules</label>
            <textarea id="business_line_rules" name="business_line_rules" rows="10" class="rules-textarea">{_esc(rules_text)}</textarea>
            <p class="hint">Example: <code>Spring Promo | spring promo, sp2026</code>. After saving, run a <strong>full refresh</strong> to re-classify campaigns.</p>
            <button type="submit" class="btn primary">Save business line rules</button>
          </form>
        </section>"""

    theme_section = ""
    if slug == "penn" and db_editable:
        theme_section = _brand_colors_section_html(
            settings_url=settings_url,
            theme=dashboard_theme.load_client_theme(slug),
        )

    insights_fold = ""
    if dashboard_service.can_edit_penn_insights(
        session_is_admin=session_is_admin,
        access_key=access_key,
    ):
        insights_fold = f"""
        <details class="settings-fold">
          <summary>Dashboard insights (admin notes)</summary>
          <div class="fold-body">
            {dashboard_service._insights_editor_html(client_slug=slug, access_key=access_key, use_session=use_session, snapshot=snapshot)}
          </div>
        </details>"""

    probe_fold = ""
    if probe_results:
        probe_rows = []
        for platform in ("google", "linkedin", "meta", "ga4"):
            row = probe_results.get(platform) or {}
            probe_rows.append(
                _connection_row(
                    platform.title(),
                    bool(row.get("ok")),
                    str(row.get("message") or "—"),
                )
            )
        probe_fold = f"""
        <section class="panel">
          <h2>Connection test results</h2>
          <table class="status-table">
            <thead><tr><th>Platform</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>{"".join(probe_rows)}</tbody>
          </table>
        </section>"""

    base_url = _esc(oauth_flows.public_base_url())
    advanced = f"""
    <details class="settings-fold">
      <summary>Auth &amp; API diagnostics</summary>
      <div class="fold-body">
        <table class="status-table">
          <thead><tr><th>Component</th><th>Status</th><th>Notes</th></tr></thead>
          <tbody>{"".join(auth_rows)}</tbody>
        </table>
        <table class="status-table">
          <thead><tr><th>Platform</th><th>Status</th><th>Notes</th></tr></thead>
          <tbody>{"".join(api_rows)}</tbody>
        </table>
      </div>
    </details>
    <details class="settings-fold">
      <summary>Sync status &amp; account details</summary>
      <div class="fold-body">
        <table class="status-table">
          <thead><tr><th>Field</th><th>Value</th></tr></thead>
          <tbody>{account_rows}</tbody>
        </table>
        {_sync_meta_html(snapshot)}
      </div>
    </details>
    <details class="settings-fold">
      <summary>Developer setup (Railway &amp; callbacks)</summary>
      <div class="fold-body">
        <p class="muted">Register OAuth callbacks in each provider console:</p>
        <ul class="checklist mono">
          <li>{base_url}/oauth/google_ads/callback</li>
          <li>{base_url}/oauth/linkedin/callback</li>
          <li>{base_url}/oauth/meta/callback</li>
        </ul>
        <ul class="checklist">
          <li><strong>GA4:</strong> GCP_SERVICE_ACCOUNT_JSON + GA4_CLIENTS in Railway</li>
          <li><strong>Google Ads:</strong> GOOGLE_ADS_DEVELOPER_TOKEN in Railway</li>
          <li><strong>Meta:</strong> META_BUSINESS_ID in Railway</li>
        </ul>
      </div>
    </details>
    {insights_fold}"""

    client_meta_tip = ""
    if snapshot:
        dr = snapshot.get("date_range") or {}
        refreshed = snapshot.get("refreshed_at") or "—"
        client_meta_tip = _esc(
            f"Date range: {dr.get('start', '')} → {dr.get('end', '')}\nLast refreshed: {refreshed} UTC"
        )

    content = f"""
    {notice}
    <section class="panel">
      <h2>Setup checklist</h2>
      <p class="muted">Work top to bottom. Green badges mean that step is done.</p>
      {checklist}
    </section>
    {oauth_section}
    {edit_form}
    {bl_rules_section}
    {theme_section}
    <section class="panel panel--primary">
      <h2>3. Refresh &amp; verify</h2>
      <p class="muted">Pull latest data after connecting. Quick refresh is cheaper; full refresh reloads campaign tables and GA4.</p>
      {refresh_block}
      <div class="toolbar">{test_btn}</div>
    </section>
    {probe_fold}
    {advanced}
    """

    return dashboard_service.render_client_shell_page(
        client_slug=slug,
        label=cfg.label,
        active_nav="settings",
        page_title="Settings",
        page_subtitle=f"{cfg.label} · connections & data",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        client_meta_tip=client_meta_tip,
        extra_css=_settings_page_css(),
    )


def load_settings_config(client_slug: str) -> PennDashboardConfig:
    slug = client_slug.strip().lower()
    if slug == "penn":
        return load_penn_config()
    return client_config.load_client_config(slug)
