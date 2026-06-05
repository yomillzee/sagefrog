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


def _oauth_env_has_token(platform: str) -> bool:
    if platform == "google_ads":
        return bool(google_auth._get_env(*google_auth._ENV_ALIASES["refresh_token"]))
    if platform == "linkedin":
        return bool(linkedin_auth._get_env(*linkedin_auth._ENV_ALIASES["refresh_token"]))
    if platform == "meta":
        return bool(meta_auth._get_env(*meta_auth._ENV_ALIASES["access_token"]))
    return False


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
    env_has = _oauth_env_has_token(platform)
    pub = oauth_store.public_status(platform, env_has_token=env_has)
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
    return f"""
    <div class="oauth-card">
      <div class="oauth-card-head">
        <h3>{_esc(label)}</h3>
        {status_badge}
      </div>
      {connected_meta}
      <p class="muted">{note}</p>
      <p class="hint mono">Callback URL: {callback}</p>
      <div class="oauth-actions">{actions}</div>
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
    meta_manual = ""
    if can_manage_oauth and oauth_store.enabled():
        meta_manual = f"""
        <details class="meta-manual">
          <summary>Or paste a Meta system-user token manually</summary>
          <form method="post" action="{settings_url}" class="meta-token-form">
            <input type="hidden" name="action" value="save_meta_token">
            <label for="meta_access_token">META_ACCESS_TOKEN</label>
            <input id="meta_access_token" name="meta_access_token" type="password"
              autocomplete="off" placeholder="Long-lived token from Business Manager">
            <button type="submit" class="btn secondary">Save Meta token</button>
          </form>
        </details>
        """
    return f"""
    <section class="panel">
      <h2>Connect platform OAuth</h2>
      <p class="muted">OAuth app IDs and secrets stay in Railway. Connect stores refresh/access tokens encrypted in Postgres (agency-wide).</p>
      <p class="hint">Register these callback URLs in each provider console: base <span class="mono">{_esc(base)}</span></p>
      <div class="oauth-grid">{"".join(cards)}</div>
      {meta_manual}
      <ul class="checklist muted">
        <li><strong>GA4:</strong> upload GCP_SERVICE_ACCOUNT_JSON and GA4_CLIENTS in Railway (service account, not OAuth).</li>
        <li><strong>Google Ads:</strong> GOOGLE_ADS_DEVELOPER_TOKEN remains in Railway after Connect.</li>
        <li><strong>Meta:</strong> META_BUSINESS_ID remains in Railway.</li>
      </ul>
    </section>
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
    dashboard_url = _dashboard_url(client_slug=slug, access_key=access_key, use_session=use_session)
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

    probe_block = ""
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
        probe_block = f"""
        <section class="panel">
          <h2>Live connection test</h2>
          <p class="muted">Checked configured account IDs for {_esc(cfg.label)} just now.</p>
          <table class="status-table">
            <thead><tr><th>Platform</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>{"".join(probe_rows)}</tbody>
          </table>
        </section>
        """

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
            meta = f'<p class="muted">Last saved in database: {_esc(db_config_updated_at[:19])} UTC</p>'
        edit_form = f"""
        <section class="panel">
          <h2>Edit client data mapping</h2>
          <p class="muted">OAuth tokens stay in Railway env or Postgres (via Connect). Map this client to the correct ad accounts and GA4 property.</p>
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
                <p class="hint">Must match a key in GA4_CLIENTS (Railway env).</p>
              </div>
            </div>
            <button type="submit" class="btn primary">Save client mapping</button>
          </form>
        </section>
        """
    elif use_session and not session_is_admin:
        edit_form = """
        <section class="panel">
          <h2>Client data mapping</h2>
          <p class="muted">Only admins can edit account IDs. Contact your agency admin to update connections.</p>
        </section>
        """
    else:
        edit_form = """
        <section class="panel">
          <h2>Client data mapping</h2>
          <p class="muted">Sign in as admin to edit account IDs, or set PENN_DASHBOARD / DASHBOARD_CLIENTS in Railway.</p>
        </section>
        """

    account_nav = ""
    if use_session and session_email:
        admin_link = (
            '<a href="/admin">Admin</a><span>·</span>' if session_is_admin else ""
        )
        account_nav = f"""
        <div class="account-bar">
          <span>{_esc(session_email)}</span>
          {admin_link}
          <form method="post" action="/logout" class="inline-form">
            <button type="submit" class="link-btn">Sign out</button>
          </form>
        </div>
        """

    test_btn = ""
    if can_edit:
        test_btn = f"""
        <form method="post" action="{settings_url}" class="inline-form">
          <input type="hidden" name="action" value="test">
          <button type="submit" class="btn secondary">Test all connections</button>
        </form>
        """

    oauth_section = _oauth_connect_section_html(
        settings_url=settings_url,
        session_is_admin=session_is_admin if use_session else False,
        can_manage_oauth=can_manage_oauth,
        api=api,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Settings · {_esc(cfg.label)}</title>
  {_favicon_head_html()}
  <style>
    :root {{
      --navy: #0a2540; --accent: #0b5cab; --border: #d8dee8; --muted: #5a6578;
      --ok: #1b7f4a; --ok-bg: #e8f5ee; --err: #b42318; --err-bg: #fdecea;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #eef1f5; color: #0f1c2e; }}
    header {{
      background: var(--navy); color: #fff; padding: 16px 24px;
      display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
    }}
    header h1 {{ margin: 0; font-size: 1.15rem; }}
    header .sub {{ margin: 4px 0 0; font-size: .88rem; opacity: .85; }}
    header nav {{ display: flex; align-items: center; gap: 12px; font-size: .9rem; }}
    header a {{ color: #fff; text-decoration: none; opacity: .92; }}
    header a:hover {{ opacity: 1; text-decoration: underline; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 24px 20px 48px; }}
    .panel {{
      background: #fff; border: 1px solid var(--border); border-radius: 12px;
      padding: 20px; margin-bottom: 20px;
    }}
    .muted-panel {{ background: #f8fafc; }}
    h2 {{ margin: 0 0 12px; font-size: 1.02rem; color: var(--navy); }}
    p {{ margin: 0 0 10px; line-height: 1.45; }}
    .muted {{ color: var(--muted); font-size: .92rem; }}
    .hint {{ color: var(--muted); font-size: .82rem; margin: 4px 0 0; }}
    .notice {{ padding: 10px 12px; border-radius: 8px; margin-bottom: 16px; font-size: .9rem; }}
    .notice.ok {{ background: var(--ok-bg); color: var(--ok); }}
    .notice.err {{ background: var(--err-bg); color: var(--err); }}
    .status-table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
    .status-table th, .status-table td {{
      text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top;
    }}
    .status-table th {{ color: var(--muted); font-weight: 600; font-size: .78rem; text-transform: uppercase; }}
    .status-table td.detail {{ color: var(--muted); font-size: .88rem; }}
    .mono {{ font-family: ui-monospace, monospace; font-size: .88rem; }}
    .badge {{
      display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600;
    }}
    .badge.ok {{ background: var(--ok-bg); color: var(--ok); }}
    .badge.err {{ background: var(--err-bg); color: var(--err); }}
    .checklist {{ margin: 0; padding-left: 1.2rem; line-height: 1.55; }}
    .form-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; margin-bottom: 16px;
    }}
    label {{ display: block; font-size: .85rem; font-weight: 600; margin-bottom: 6px; }}
    input[type="text"] {{
      width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
    }}
    input[type="password"] {{
      width: 100%; max-width: 480px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
      margin-bottom: 10px;
    }}
    .oauth-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin: 16px 0;
    }}
    .oauth-card {{
      border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: #fafbfc;
    }}
    .oauth-card-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }}
    .oauth-card h3 {{ margin: 0; font-size: .95rem; color: var(--navy); }}
    .oauth-actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }}
    .meta-manual {{ margin-top: 12px; font-size: .9rem; }}
    .meta-token-form {{ margin-top: 10px; }}
    .btn {{
      display: inline-block; padding: 10px 18px; border-radius: 8px; border: 0;
      font-weight: 600; cursor: pointer; font-size: .92rem;
    }}
    .btn.primary {{ background: var(--accent); color: #fff; }}
    .btn.secondary {{ background: #fff; color: var(--accent); border: 1px solid var(--border); }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 16px; }}
    .inline-form {{ display: inline; margin: 0; }}
    .link-btn {{
      background: none; border: 0; color: #fff; cursor: pointer; font: inherit; opacity: .9; padding: 0;
    }}
    .account-bar {{ display: flex; align-items: center; gap: 8px; font-size: .88rem; opacity: .9; }}
    .account-bar span {{ opacity: .85; }}
    .error-list {{ margin: 8px 0 0; padding-left: 1.2rem; color: var(--err); font-size: .88rem; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Dashboard settings</h1>
      <p class="sub">{_esc(cfg.label)} · connect auth, API, and client data</p>
    </div>
    <nav>
      <a href="{dashboard_url}">← Back to dashboard</a>
      {account_nav}
    </nav>
  </header>
  <main>
    {notice}
    <div class="toolbar">
      {test_btn}
      {_status_badge(auth["overall_ok"] and api["overall_ok"], ok_label="Credentials configured", fail_label="Setup incomplete")}
    </div>

    {oauth_section}

    <section class="panel">
      <h2>Auth</h2>
      <p class="muted">Browser login and API protection for this deployment.</p>
      <table class="status-table">
        <thead><tr><th>Component</th><th>Status</th><th>Notes</th></tr></thead>
        <tbody>{"".join(auth_rows)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Platform API credentials</h2>
      <p class="muted">Agency-wide OAuth tokens shared across all client dashboards.</p>
      <table class="status-table">
        <thead><tr><th>Platform</th><th>Status</th><th>Notes</th></tr></thead>
        <tbody>{"".join(api_rows)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Client data</h2>
      <p class="muted">Account IDs used when refreshing {_esc(cfg.label)}.</p>
      <table class="status-table">
        <thead><tr><th>Field</th><th>Value</th></tr></thead>
        <tbody>{account_rows}</tbody>
      </table>
      <h3 style="margin:20px 0 8px;font-size:.95rem;color:var(--navy)">Sync status</h3>
      {_sync_meta_html(snapshot)}
    </section>

    {edit_form}
    {probe_block}
  </main>
</body>
</html>"""


def load_settings_config(client_slug: str) -> PennDashboardConfig:
    slug = client_slug.strip().lower()
    if slug == "penn":
        return load_penn_config()
    return client_config.load_client_config(slug)
