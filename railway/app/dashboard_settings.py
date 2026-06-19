"""Per-client dashboard settings page: auth, API, and data connections."""

from __future__ import annotations

import html
import os
from typing import Any
from urllib.parse import quote

import auth as google_auth
import bigquery_service
import client_config
import client_dashboard_config
import dashboard_features
import dashboard_service
import dashboard_snapshots
import dashboard_theme
import ga4_clients
from ga4_credentials import GLOBAL_GCP_CREDENTIALS_ENV
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


def _disconnected_oauth_statuses() -> dict[str, oauth_store.OAuthCredentialPublic]:
    return {
        platform: oauth_store.OAuthCredentialPublic(
            platform=platform,
            connected=False,
            source="unavailable",
            connected_by=None,
            connected_at=None,
            updated_at=None,
            scopes=None,
            metadata={},
        )
        for platform in sorted(oauth_store.PLATFORMS)
    }


def _unavailable_api_status() -> dict[str, Any]:
    return {
        "google": {"env": {}, "ok": False},
        "linkedin": {"env": {}, "ok": False},
        "meta": {"env": {}, "ok": False},
        "ga4": {"env": {}, "registry_count": 0, "ok": False},
        "overall_ok": False,
    }


def list_ga4_clients_for_settings() -> list[dict[str, Any]]:
    """GA4 registry entries for the settings dropdown (empty if BigQuery is not configured)."""
    try:
        registry = ga4_clients.load_client_registry()
        if registry:
            return ga4_clients.list_clients_public()

        ga4 = bigquery_service.env_summary()
        if not (
            ga4.get("has_gcp_service_account_json")
            and ga4.get("gcp_service_account_json_parse_ok")
            and ga4.get("has_bq_project_id")
        ):
            return []
        return ga4_clients.list_clients_public()
    except Exception:
        return []


def _ga4_client_label_for_key(
    client_key: str | None,
    clients: list[dict[str, Any]],
) -> str:
    key = str(client_key or "").strip()
    if not key:
        return "—"
    for row in clients:
        if str(row.get("client_key") or "") == key:
            label = str(row.get("label") or key)
            project = str(row.get("bq_project_id") or "")
            dataset = str(row.get("bq_dataset_id") or "")
            if project and dataset:
                return f"{label} · {project}/{dataset} (key {key})"
            return key
    return key



def _ga4_safe_credential_detail(
    *,
    client_key: str | None,
    credentials_env: str | None,
    client_email: str | None,
) -> str:
    selected_key = str(client_key or "").strip() or "—"
    env_name = str(credentials_env or "").strip() or GLOBAL_GCP_CREDENTIALS_ENV
    email = str(client_email or "").strip() or "unavailable"
    return f"client_key {selected_key} · credentials_env {env_name} · service account {email}"


def _ga4_safe_credential_summary(cfg: PennDashboardConfig) -> dict[str, Any]:
    key = str(cfg.ga4_client_key or "").strip()
    if not key:
        return {
            "ok": False,
            "client_key": None,
            "credentials_env": None,
            "client_email": None,
            "detail": "No GA4 client key mapped for this client.",
        }
    try:
        resolved = ga4_clients.resolve_client_config(client_key=key)
        env_name = resolved.credentials_env or GLOBAL_GCP_CREDENTIALS_ENV
        client_email = str(resolved.credentials.get("client_email") or "").strip() or None
        detail = _ga4_safe_credential_detail(
            client_key=resolved.client_key or key,
            credentials_env=env_name,
            client_email=client_email,
        )
        return {
            "ok": True,
            "client_key": resolved.client_key or key,
            "credentials_env": env_name,
            "client_email": client_email,
            "detail": detail,
        }
    except Exception as exc:
        try:
            target = ga4_clients.resolve_target(client_key=key)
            env_name = target.credentials_env or GLOBAL_GCP_CREDENTIALS_ENV
            selected_key = target.client_key or key
        except Exception:
            env_name = None
            selected_key = key
        detail = _ga4_safe_credential_detail(
            client_key=selected_key,
            credentials_env=env_name,
            client_email=None,
        )
        return {
            "ok": False,
            "client_key": selected_key,
            "credentials_env": env_name,
            "client_email": None,
            "detail": f"{detail} · credential error: {str(exc)[:300]}",
            "error": str(exc)[:300],
        }


def _oauth_platform_card_html(
    *,
    platform: str,
    label: str,
    return_url: str,
    can_manage_oauth: bool = True,
    connection_details: list[str] | None = None,
    status_message: str | None = None,
    pub: oauth_store.OAuthCredentialPublic | None = None,
) -> str:
    prereq = oauth_flows.connect_prerequisites(platform)
    pub = pub or oauth_store.public_status(platform)
    return_to = quote(return_url, safe="")
    status_badge = _status_badge(pub.connected, ok_label="Connected", fail_label="Not connected")
    source = _esc(pub.source)
    connected_meta = ""
    if pub.connected_by or pub.connected_at:
        who = _esc(pub.connected_by or "—")
        when = _esc((pub.connected_at or "")[:19])
        connected_meta = f'<p class="muted">Connected by {who} · {when} UTC · source {source}</p>'

    details_html = ""
    if status_message:
        details_html += f'<p class="oauth-status-msg">{_esc(status_message)}</p>'
    if connection_details:
        items = "".join(f"<li>{_esc(line)}</li>" for line in connection_details)
        details_html += f'<ul class="oauth-details">{items}</ul>'

    actions = ""
    if can_manage_oauth and oauth_store.enabled():
        connect_url = f"/oauth/{platform}/connect?return_to={return_to}"
        if pub.connected:
            actions += f'<a class="btn secondary" href="{connect_url}">Reconnect {label}</a> '
        elif prereq.get("ready"):
            actions += f'<a class="btn primary" href="{connect_url}">Connect {label}</a> '
        else:
            missing = ", ".join(prereq.get("missing") or [])
            actions += f'<p class="muted">Set {_esc(missing)} in Railway before connecting.</p>'
        if pub.source == "database":
            actions += f"""
            <form method="post" action="/oauth/{platform}/disconnect" class="inline-form"
              onsubmit="return confirm('Disconnect {label}? The stored token will be removed.');">
              <input type="hidden" name="return_to" value="{_esc(return_url)}">
              <button type="submit" class="btn secondary">Disconnect</button>
            </form>
            """
    elif can_manage_oauth and not oauth_store.enabled():
        actions = '<p class="muted">Attach Postgres (DATABASE_URL) to store OAuth tokens.</p>'

    note = _esc(prereq.get("note") or "")
    callback = _esc(oauth_flows.callback_url(platform))
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
      {details_html}
      <p class="muted">{note}</p>
      <div class="oauth-actions">{actions}</div>
      {dev_details}
    </div>
    """


def _gsc_field_html(*, selected: str, properties: list[dict[str, str]], connected: bool) -> str:
    """GSC property field: live dropdown when OAuth-connected, free text otherwise."""
    if not connected:
        return f"""
            <label for="gsc_site_url">GSC site URL</label>
            <input id="gsc_site_url" name="gsc_site_url" type="text"
              value="{_esc(selected)}"
              placeholder="https://www.example.com/ or sc-domain:example.com">
            <p class="hint">
              Connect Google Search Console in <a href="/admin">Admin</a> to pick from a
              dropdown of properties instead of typing the URL.
            </p>"""

    options = ['<option value="">— none —</option>']
    known_urls = {p["site_url"] for p in properties}
    if selected and selected not in known_urls:
        options.append(f'<option value="{_esc(selected)}" selected>{_esc(selected)} (not in property list)</option>')
    for p in properties:
        url = p["site_url"]
        perm = p.get("permission_level") or ""
        sel = " selected" if url == selected else ""
        label_text = f"{url} · {perm}" if perm else url
        options.append(f'<option value="{_esc(url)}"{sel}>{_esc(label_text)}</option>')
    return f"""
            <label for="gsc_site_url">GSC property</label>
            <select id="gsc_site_url" name="gsc_site_url">{"".join(options)}</select>
            <p class="hint">
              From the agency Google Search Console login connected in
              <a href="/admin">Admin</a> — used by the GSC → BigQuery sync.
            </p>"""


def probe_agency_oauth_platform(platform: str) -> dict[str, Any]:
    """Live agency-wide OAuth status for the admin page."""
    import google_ads_service
    import linkedin_service
    import meta_service

    pub = oauth_store.public_status(platform)
    if not pub.connected:
        return {
            "ok": False,
            "connected": False,
            "message": "Not connected — click Connect to authorize this platform for the agency.",
            "details": [],
        }

    if platform == "google_ads":
        token = google_ads_service.test_refresh_token()
        if not token.get("ok"):
            return {
                "ok": False,
                "connected": True,
                "message": str(token.get("error") or token.get("message") or "OAuth refresh failed.")[:300],
                "details": [],
            }
        try:
            ids = google_ads_service.list_accessible_customer_ids()
            details: list[str] = []
            for cid in ids[:3]:
                try:
                    meta = google_ads_service.get_account_metadata(cid)
                    name = meta.get("descriptive_name") or cid
                    details.append(
                        f"{name} · ID {meta.get('customer_id') or cid} · "
                        f"{meta.get('currency_code') or '—'} · {meta.get('time_zone') or '—'}"
                    )
                except Exception:
                    details.append(f"Customer ID {cid}")
            extra = f" (+{len(ids) - 3} more)" if len(ids) > 3 else ""
            return {
                "ok": True,
                "connected": True,
                "message": f"OAuth active · {len(ids)} accessible Google Ads account(s){extra}",
                "details": details or [f"{len(ids)} accessible account(s)"],
            }
        except Exception as exc:
            return {
                "ok": False,
                "connected": True,
                "message": str(exc)[:300],
                "details": [],
            }

    if platform == "linkedin":
        token = linkedin_service.test_refresh_token()
        if not token.get("ok"):
            return {
                "ok": False,
                "connected": True,
                "message": str(token.get("error") or token.get("message") or "OAuth refresh failed.")[:300],
                "details": [],
            }
        try:
            accounts = linkedin_service.list_ad_accounts()
            details = [
                f"{row.get('name') or 'Account'} · ID {row.get('id')} · "
                f"{row.get('currency') or '—'} · {row.get('status') or '—'}"
                for row in accounts[:12]
            ]
            extra = f" (+{len(accounts) - 12} more)" if len(accounts) > 12 else ""
            return {
                "ok": True,
                "connected": True,
                "message": f"OAuth active · {len(accounts)} accessible LinkedIn ad account(s){extra}",
                "details": details or ["No ad accounts returned for this token."],
            }
        except Exception as exc:
            return {
                "ok": False,
                "connected": True,
                "message": str(exc)[:300],
                "details": [],
            }

    if platform == "gsc":
        import gsc_sync_service
        try:
            properties = gsc_sync_service.list_accessible_properties()
            details = [
                f"{row.get('site_url')} · {row.get('permission_level') or '—'}"
                for row in properties[:12]
            ]
            extra = f" (+{len(properties) - 12} more)" if len(properties) > 12 else ""
            return {
                "ok": True,
                "connected": True,
                "message": f"OAuth active · {len(properties)} accessible Search Console propert{'y' if len(properties) == 1 else 'ies'}{extra}",
                "details": details or ["No properties returned for this login."],
            }
        except Exception as exc:
            return {
                "ok": False,
                "connected": True,
                "message": str(exc)[:300],
                "details": [],
            }

    if platform == "meta":
        token = meta_service.test_access_token()
        if not token.get("ok"):
            return {
                "ok": False,
                "connected": True,
                "message": str(token.get("error") or token.get("message") or "Token check failed.")[:300],
                "details": [],
            }
        try:
            accounts = meta_service.list_ad_accounts()
            details = [
                f"{row.get('name') or 'Account'} · ID {row.get('id')} · "
                f"{row.get('currency') or '—'} · {row.get('account_status') or row.get('status') or '—'}"
                for row in accounts[:12]
            ]
            extra = f" (+{len(accounts) - 12} more)" if len(accounts) > 12 else ""
            return {
                "ok": True,
                "connected": True,
                "message": f"Token active · {len(accounts)} Meta ad account(s) under Business Manager{extra}",
                "details": details or ["No ad accounts returned for this Business Manager."],
            }
        except Exception as exc:
            return {
                "ok": False,
                "connected": True,
                "message": str(exc)[:300],
                "details": [],
            }

    return {"ok": False, "connected": False, "message": "Unknown platform.", "details": []}


def cached_agency_oauth_message(platform: str, pub: oauth_store.OAuthCredentialPublic) -> str:
    """Fast status line from stored credentials — no live API calls."""
    if not pub.connected:
        return "Not connected — click Connect to authorize this platform for the agency."
    who = pub.connected_by or "admin"
    when = (pub.connected_at or pub.updated_at or "")[:19] or "—"
    return f"Connected by {who} · {when} UTC · click Refresh status for live account list"


def render_admin_oauth_section(
    *,
    return_url: str = "/admin",
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
    live_probe: bool = False,
) -> str:
    notice = ""
    if oauth_connected:
        labels = {
            "google_ads": "Google Ads",
            "linkedin": "LinkedIn",
            "meta": "Meta",
            "gsc": "Google Search Console",
        }
        notice += (
            f'<div class="notice ok">{_esc(labels.get(oauth_connected, oauth_connected))} '
            f"connected successfully.</div>"
        )
    if oauth_error:
        notice += f'<div class="notice err">{_esc(oauth_error)}</div>'

    oauth_status = oauth_store.all_public_status()
    live_results: dict[str, dict[str, Any]] = {}
    if live_probe:
        live_results = {
            platform: probe_agency_oauth_platform(platform)
            for platform in ("google_ads", "linkedin", "meta", "gsc")
        }

    refresh_href = _esc(f"{return_url}?oauth_refresh=1")
    refresh_bar = (
        f'<div class="toolbar">'
        f'<a class="btn secondary" href="{refresh_href}">Refresh connection status</a>'
        f'<span class="muted">Live account lists load on demand — page opens instantly.</span>'
        f"</div>"
        if not live_probe
        else '<p class="muted">Showing live connection status (refreshed just now).</p>'
    )

    cards = []
    for platform, label in (
        ("google_ads", "Google Ads"),
        ("linkedin", "LinkedIn"),
        ("meta", "Meta"),
        ("gsc", "Google Search Console"),
    ):
        pub = oauth_status[platform]
        probe = live_results.get(platform) if live_probe else None
        cards.append(
            _oauth_platform_card_html(
                platform=platform,
                label=label,
                return_url=return_url,
                can_manage_oauth=True,
                pub=pub,
                connection_details=(probe.get("details") if probe else None) or None,
                status_message=str(
                    (probe or {}).get("message") or cached_agency_oauth_message(platform, pub)
                ),
            )
        )

    base = _esc(oauth_flows.public_base_url())
    return f"""
    {notice}
    <section class="admin-oauth-section">
      <h2>Platform connections</h2>
      <p class="muted">Connect once here for the whole agency. Each client dashboard then maps its own account IDs.</p>
      {refresh_bar}
      <div class="oauth-grid">{"".join(cards)}</div>
      <details class="admin-fold">
        <summary>OAuth callback URLs (developer setup)</summary>
        <ul class="checklist mono">
          <li>{base}/oauth/google_ads/callback</li>
          <li>{base}/oauth/linkedin/callback</li>
          <li>{base}/oauth/meta/callback</li>
          <li>{base}/oauth/gsc/callback</li>
        </ul>
      </details>
    </section>"""


def _agency_oauth_status_html(
    *,
    api: dict[str, Any],
    oauth_status: dict[str, oauth_store.OAuthCredentialPublic] | None = None,
    ga4_credential_summary: dict[str, Any] | None = None,
) -> str:
    oauth_status = oauth_status or oauth_store.all_public_status()
    rows = []
    for platform, label in (
        ("google_ads", "Google Ads"),
        ("linkedin", "LinkedIn"),
        ("meta", "Meta"),
        ("gsc", "Google Search Console"),
    ):
        pub = oauth_status[platform]
        detail = (
            cached_agency_oauth_message(platform, pub)
            if pub.connected
            else "Not connected — ask an admin to connect in Admin."
        )
        rows.append(_connection_row(label, pub.connected, detail))

    ga4_ok = api["ga4"]["ok"]
    ga4_detail = f"{api['ga4']['registry_count']} GA4_CLIENTS entries in Railway"
    if not ga4_ok:
        ga4_detail = "Set GCP_SERVICE_ACCOUNT_JSON + GA4_CLIENTS in Railway"
    rows.append(_connection_row("GA4 / BigQuery", ga4_ok, ga4_detail))
    if ga4_credential_summary is not None:
        rows.append(
            _connection_row(
                "Selected GA4 credential",
                bool(ga4_credential_summary.get("ok")),
                str(ga4_credential_summary.get("detail") or "—"),
            )
        )

    return f"""
    <section class="panel">
      <h2>Agency platform access</h2>
      <p class="muted">Google, LinkedIn, and Meta authenticate once for the whole agency in <a href="/admin">Admin</a>.
      This page maps which accounts belong to this client.</p>
      <table class="status-table">
        <thead><tr><th>Platform</th><th>Status</th><th>Details</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </section>"""


def _setup_checklist_html(
    *,
    api: dict[str, Any],
    cfg: PennDashboardConfig,
    ga4_ok: bool,
    oauth_status: dict[str, oauth_store.OAuthCredentialPublic] | None = None,
) -> str:
    oauth_status = oauth_status or oauth_store.all_public_status()
    agency_connected = all(oauth_status[p].connected for p in ("google_ads", "linkedin", "meta"))
    mapped = bool(cfg.google_customer_id or cfg.linkedin_account_id or cfg.meta_account_id)
    steps = [
        ("Agency platforms connected (Admin)", agency_connected),
        ("Client account IDs mapped", mapped),
        ("GA4 configured (Railway)", ga4_ok),
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
    .oauth-details { margin: 8px 0 0; padding-left: 1.1rem; font-size: .82rem; color: var(--navy); }
    .oauth-details li { margin: 4px 0; }
    .oauth-status-msg { margin: 8px 0 0; font-size: .88rem; color: var(--navy); font-weight: 600; }
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
    input[type="text"], input[type="password"], select { width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
    input[type="color"] { width: 100%; height: 42px; padding: 2px; border: 1px solid var(--border); border-radius: 8px; cursor: pointer; background: #fff; }
    .color-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin-bottom: 16px; }
    .color-field label { margin-bottom: 6px; }
    .theme-group-title { margin: 16px 0 10px; font-size: .88rem; font-weight: 700; color: var(--navy); text-transform: uppercase; letter-spacing: .04em; }
    .theme-group-title:first-of-type { margin-top: 0; }
    .feature-toggle-grid { display: grid; gap: 12px; margin-bottom: 16px; }
    .feature-toggle { display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px;
      border: 1px solid var(--border); border-radius: 10px; background: #fafbfc; cursor: pointer; }
    .feature-toggle input { margin-top: 3px; flex-shrink: 0; }
    .feature-toggle-text { display: block; min-width: 0; }
    .feature-toggle-text strong { display: block; margin-bottom: 4px; color: var(--navy); }
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
    """Live validation of this client's mapped account IDs against agency tokens."""
    import google_ads_service
    import linkedin_service
    import meta_service

    results: dict[str, Any] = {}
    configured: list[str] = []

    def _skip(platform: str, message: str) -> None:
        results[platform] = {"ok": None, "skipped": True, "message": message}

    if cfg.google_customer_id:
        configured.append("google")
        try:
            token = google_ads_service.test_refresh_token()
            if not token.get("ok"):
                results["google"] = {
                    "ok": False,
                    "message": str(token.get("error") or token.get("message") or "Google OAuth failed.")[:300],
                }
            else:
                meta_row = google_ads_service.get_account_metadata(customer_id=cfg.google_customer_id)
                name = meta_row.get("descriptive_name") or cfg.google_customer_id
                results["google"] = {
                    "ok": True,
                    "message": (
                        f"{name} · customer ID {meta_row.get('customer_id') or cfg.google_customer_id} · "
                        f"{meta_row.get('currency_code') or '—'} · {meta_row.get('time_zone') or '—'}"
                    ),
                }
        except Exception as exc:
            results["google"] = {"ok": False, "message": str(exc)[:300]}
    else:
        _skip("google", "No Google Ads customer ID mapped for this client.")

    if cfg.linkedin_account_id:
        configured.append("linkedin")
        target_id = linkedin_service._normalize_account_id(cfg.linkedin_account_id)
        try:
            token = linkedin_service.test_refresh_token()
            if not token.get("ok"):
                results["linkedin"] = {
                    "ok": False,
                    "message": str(token.get("error") or token.get("message") or "LinkedIn OAuth failed.")[:300],
                }
            else:
                accounts = linkedin_service.list_ad_accounts()
                match = next(
                    (row for row in accounts if linkedin_service._normalize_account_id(str(row.get("id") or "")) == target_id),
                    None,
                )
                if match:
                    results["linkedin"] = {
                        "ok": True,
                        "message": (
                            f"{match.get('name') or 'Account'} · ID {match.get('id')} · "
                            f"{match.get('currency') or '—'} · {match.get('status') or '—'}"
                        ),
                    }
                else:
                    sample = ", ".join(str(row.get("id") or "") for row in accounts[:6])
                    results["linkedin"] = {
                        "ok": False,
                        "message": (
                            f"Account ID {cfg.linkedin_account_id} was not found under the connected agency token. "
                            f"Accessible IDs include: {sample or 'none'}."
                        ),
                    }
        except Exception as exc:
            results["linkedin"] = {"ok": False, "message": str(exc)[:300]}
    else:
        _skip("linkedin", "No LinkedIn account ID mapped for this client.")

    if cfg.meta_account_id:
        configured.append("meta")
        target_id = meta_service._normalize_account_id(cfg.meta_account_id)
        try:
            token = meta_service.test_access_token()
            if not token.get("ok"):
                results["meta"] = {
                    "ok": False,
                    "message": str(token.get("error") or token.get("message") or "Meta token check failed.")[:300],
                }
            else:
                access = meta_service.test_ads_read_access(cfg.meta_account_id)
                match = None
                for row in meta_service.list_ad_accounts():
                    if meta_service._normalize_account_id(str(row.get("id") or "")) == target_id:
                        match = row
                        break
                name = (match or {}).get("name") or cfg.meta_account_id
                if access.get("ok"):
                    results["meta"] = {
                        "ok": True,
                        "message": f"{name} · account ID {target_id} · ads_read verified",
                    }
                else:
                    results["meta"] = {
                        "ok": False,
                        "message": str(access.get("message") or access.get("error") or "Meta ads_read failed.")[:300],
                    }
        except Exception as exc:
            results["meta"] = {"ok": False, "message": str(exc)[:300]}
    else:
        _skip("meta", "No Meta ad account ID mapped for this client.")

    if cfg.ga4_client_key:
        configured.append("ga4")
        try:
            resolved = ga4_clients.resolve_client_config(client_key=cfg.ga4_client_key)
            target = ga4_clients.resolve_target(client_key=cfg.ga4_client_key)
            credential_detail = _ga4_safe_credential_detail(
                client_key=resolved.client_key or cfg.ga4_client_key,
                credentials_env=resolved.credentials_env or GLOBAL_GCP_CREDENTIALS_ENV,
                client_email=str(resolved.credentials.get("client_email") or "").strip() or None,
            )
            client = bigquery_service.build_client(
                project_id=target.bq_project_id,
                credentials_info=resolved.credentials,
            )
            client.query(
                f"SELECT 1 FROM `{target.bq_project_id}.{target.bq_dataset_id}.events_*` LIMIT 1"
            ).result(timeout=30)
            results["ga4"] = {
                "ok": True,
                "message": (
                    f"{target.label or cfg.ga4_client_key} · {target.bq_project_id}/"
                    f"{target.bq_dataset_id} · property {target.account_id} · {credential_detail}"
                ),
                "client_key": resolved.client_key or cfg.ga4_client_key,
                "credentials_env": resolved.credentials_env or GLOBAL_GCP_CREDENTIALS_ENV,
                "client_email": str(resolved.credentials.get("client_email") or "").strip() or None,
            }
        except Exception as exc:
            credential_summary = _ga4_safe_credential_summary(cfg)
            results["ga4"] = {
                "ok": False,
                "message": f"{credential_summary.get('detail') or 'GA4 credentials unavailable'} · {str(exc)[:300]}",
                "client_key": credential_summary.get("client_key"),
                "credentials_env": credential_summary.get("credentials_env"),
                "client_email": credential_summary.get("client_email"),
            }
    else:
        _skip("ga4", "No GA4 client key mapped for this client.")

    results["overall_ok"] = bool(configured) and all(
        results[p]["ok"] for p in configured if results.get(p, {}).get("ok") is not None
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


def _dashboard_sections_section_html(
    *,
    settings_url: str,
    features: dashboard_features.DashboardFeatures,
) -> str:
    toggles: list[str] = []
    for key in dashboard_features.FEATURE_KEYS:
        checked = " checked" if features.get(key) else ""
        toggles.append(
            f"""
        <label class="feature-toggle">
          <input type="checkbox" name="feature_{key}" value="1"{checked}>
          <span class="feature-toggle-text">
            <strong>{_esc(dashboard_features.FEATURE_LABELS[key])}</strong>
            <span class="hint">{_esc(dashboard_features.FEATURE_HINTS[key])}</span>
          </span>
        </label>"""
        )
    return f"""
    <section class="panel">
      <h2>Dashboard sections</h2>
      <p class="muted">Turn overview panels and tabs on or off for this client. Defaults vary by client
      (for example, Penn starts with budget pacing off).</p>
      <form method="post" action="{settings_url}">
        <input type="hidden" name="action" value="save_dashboard_sections">
        <div class="feature-toggle-grid">{"".join(toggles)}</div>
        <button type="submit" class="btn primary">Save section visibility</button>
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
    probe_results: dict[str, Any] | None = None,  # kept for call-site compat, unused
    db_config_updated_at: str | None = None,
) -> str:
    slug = client_slug.strip().lower()
    settings_url = _settings_url(client_slug=slug, access_key=access_key, use_session=use_session)

    try:
        snapshot = dashboard_snapshots.get_snapshot_settings_context(slug)
    except Exception:
        snapshot = None

    db_editable = web_users.enabled() and session_is_admin

    notice = ""
    if flash_message:
        notice += f'<div class="notice ok">{_esc(flash_message)}</div>'
    if flash_error:
        notice += f'<div class="notice err">{_esc(flash_error)}</div>'

    # --- GA4 field (dropdown if registry available, otherwise text input) ---
    ga4_clients_list = list_ga4_clients_for_settings()
    if ga4_clients_list:
        options = ['<option value="">— None —</option>']
        selected_key = str(cfg.ga4_client_key or "").strip().lower()
        known_keys = {str(c.get("client_key") or "") for c in ga4_clients_list}
        if not selected_key and slug in known_keys:
            selected_key = slug
        if selected_key and selected_key not in known_keys:
            options.append(
                f'<option value="{_esc(selected_key)}" selected>'
                f"{_esc(selected_key)} (not in registry)</option>"
            )
        for c in sorted(
            ga4_clients_list,
            key=lambda c: str(c.get("label") or c.get("client_key") or "").lower(),
        ):
            ckey = str(c.get("client_key") or "")
            label_text = (
                f"{c.get('label') or ckey} · "
                f"{c.get('bq_project_id')}/{c.get('bq_dataset_id')}"
            )
            sel = " selected" if ckey == selected_key else ""
            options.append(f'<option value="{_esc(ckey)}"{sel}>{_esc(label_text)}</option>')
        ga4_field = f"""
          <div>
            <label for="ga4_client_key">GA4 client key</label>
            <select id="ga4_client_key" name="ga4_client_key">{"".join(options)}</select>
            <p class="hint">GA4_CLIENTS registry in Railway.</p>
          </div>"""
    else:
        ga4_field = f"""
          <div>
            <label for="ga4_client_key">GA4 client key</label>
            <input id="ga4_client_key" name="ga4_client_key" type="text"
              value="{_esc(cfg.ga4_client_key or '')}" placeholder="penn">
            <p class="hint">Set GA4_CLIENTS in Railway to load a dropdown.</p>
          </div>"""

    resolved_features = dashboard_features.resolve_features(
        slug, cfg=cfg, label=cfg.label, ga4_client_key=cfg.ga4_client_key,
    )

    # --- Try to load per-client DB config for pre-filled BQ fields ---
    try:
        _db_cfg = client_dashboard_config.get_config(slug)
    except Exception:
        _db_cfg = None
    _gsc_url_val = (_db_cfg.gsc_site_url if _db_cfg else None) or ""
    try:
        _gsc_connected = oauth_store.public_status("gsc").connected
    except Exception:
        _gsc_connected = False
    if _gsc_connected:
        try:
            import gsc_sync_service as _gsc_svc
            _gsc_properties = _gsc_svc.list_accessible_properties()
        except Exception:
            _gsc_properties = []
    else:
        _gsc_properties = []
    _semrush_val = (_db_cfg.semrush_domain if _db_cfg else None) or ""

    # --- Edit form ---
    if db_editable:
        meta_line = ""
        if db_config_updated_at:
            meta_line = f'<p class="muted">Last saved: {_esc(db_config_updated_at[:19])} UTC</p>'
        budget_field = ""
        if resolved_features.budget_pacing:
            bud_val = _esc(
                (f"{cfg.monthly_budget_usd:.2f}".rstrip("0").rstrip("."))
                if getattr(cfg, "monthly_budget_usd", None) is not None else ""
            )
            budget_field = f"""
          <div>
            <label for="monthly_budget_usd">Monthly budget (USD)</label>
            <input id="monthly_budget_usd" name="monthly_budget_usd" type="number" min="0" step="100"
              value="{bud_val}" placeholder="25000">
          </div>"""
        edit_form = f"""
        <section class="panel panel--primary">
          <h2>Client configuration</h2>
          {meta_line}
          <form method="post" action="{settings_url}">
            <input type="hidden" name="action" value="save">
            <div class="form-grid">
              <div>
                <label for="label">Display label</label>
                <input id="label" name="label" type="text" value="{_esc(cfg.label)}" maxlength="120">
              </div>
              <div>
                <label for="linkedin_account_id">LinkedIn account ID</label>
                <input id="linkedin_account_id" name="linkedin_account_id" type="text"
                  value="{_esc(cfg.linkedin_account_id or '')}" placeholder="508590994">
                <p class="hint">Numeric ID from the LinkedIn Campaign Manager URL.</p>
              </div>
              <div>
                <label for="meta_account_id">Meta ad account ID</label>
                <input id="meta_account_id" name="meta_account_id" type="text"
                  value="{_esc(cfg.meta_account_id or '')}" placeholder="2581574002135957">
                <p class="hint">act_XXXXXXXX from Meta Business Manager.</p>
              </div>
              {ga4_field}
              {budget_field}
              <div>
                {_gsc_field_html(selected=_gsc_url_val, properties=_gsc_properties, connected=_gsc_connected)}
              </div>
              <div>
                <label for="semrush_domain">SEMrush domain</label>
                <input id="semrush_domain" name="semrush_domain" type="text"
                  value="{_esc(_semrush_val)}"
                  placeholder="example.com">
                <p class="hint">Root domain (no www, no https://) for SEMrush lookups.</p>
              </div>
            </div>
            <button type="submit" class="btn primary">Save settings</button>
          </form>
        </section>"""
    elif use_session and not session_is_admin:
        edit_form = """
        <section class="panel">
          <p class="muted">Only admins can edit settings. Ask your agency admin to update this client's configuration.</p>
        </section>"""
    else:
        edit_form = f"""
        <section class="panel">
          <p class="muted">LinkedIn: {_esc(cfg.linkedin_account_id or "—")} &nbsp;·&nbsp;
          Meta: {_esc(cfg.meta_account_id or "—")} &nbsp;·&nbsp;
          GA4: {_esc(cfg.ga4_client_key or "—")}</p>
        </section>"""

    # --- Refresh toolbar ---
    refresh_block = dashboard_service._refresh_toolbar(
        client_slug=slug,
        access_key=access_key,
        use_session=use_session,
        snapshot=snapshot,
        flash_message=None,
    )

    # --- Data sync ---
    key_param = f"?key={quote(access_key, safe='')}" if (not use_session and access_key) else ""
    post_url = f"/dashboard/{slug}/settings{key_param}"
    data_sync_section = f"""
    <section class="panel">
      <h2>Data sync</h2>
      <form method="post" action="{_esc(post_url)}" style="margin:0">
        <input type="hidden" name="action" value="gsc_sync">
        <button type="submit" class="btn secondary">Sync GSC → BigQuery</button>
      </form>
      <p class="hint" style="margin-top:8px">
        Fills missing days in <code>fact_gsc_query_daily</code> &amp; <code>fact_gsc_page_daily</code>.
        If the tables are empty, a full 480-day backfill starts in the background (~15 min).
      </p>
    </section>"""

    # --- BigQuery Mart info ---
    bq_mart_section = ""
    try:
        import bq_mart_service
        bm = bq_mart_service.env_summary()
        bq_mart_section = f"""
    <section class="panel">
      <h2>BigQuery mart</h2>
      <table class="status-table">
        <thead><tr><th>Setting</th><th>Value</th><th>Env var</th></tr></thead>
        <tbody>
          <tr><td>Project</td><td class="mono">{_esc(bm["project_id"])}</td><td class="mono muted">BQ_MART_PROJECT_ID</td></tr>
          <tr><td>Dataset</td><td class="mono">{_esc(bm["dataset_id"])}</td><td class="mono muted">BQ_MART_DATASET_ID</td></tr>
          <tr><td>Google table</td><td class="mono">{_esc(bm["google_table"])}</td><td class="mono muted">BQ_MART_TABLE</td></tr>
          <tr><td>LinkedIn table</td><td class="mono">{_esc(bm["linkedin_table"])}</td><td class="mono muted">BQ_MART_LINKEDIN_TABLE</td></tr>
        </tbody>
      </table>
    </section>"""
    except Exception:
        pass

    # --- Dashboard sections ---
    sections_section = ""
    if db_editable:
        sections_section = _dashboard_sections_section_html(
            settings_url=settings_url,
            features=resolved_features,
        )

    # --- Campaign grouping rules ---
    bl_rules_section = ""
    if db_editable:
        import business_line_rules as bl_rules
        rules_text = bl_rules.rules_to_text(bl_rules.get_rules(slug))
        builtin_note = (
            " Custom rules run <strong>before</strong> built-in defaults (Home Equity, Cash Bonus, HYS, CD / Certificate, Commercial)."
            if slug == "penn" else ""
        )
        bl_rules_section = f"""
        <section class="panel">
          <h2>Campaign grouping rules</h2>
          <p class="muted">Map campaigns to custom labels using keyword substring matching.{builtin_note}</p>
          <p class="hint">One rule per line: <code>keyword, keyword = Label</code></p>
          <form method="post" action="{settings_url}">
            <input type="hidden" name="action" value="save_business_line_rules">
            <label for="business_line_rules">Grouping rules</label>
            <textarea id="business_line_rules" name="business_line_rules" rows="10" class="rules-textarea"
              placeholder="home equity, heloc = Home Equity&#10;commercial, comm = Commercial">{_esc(rules_text)}</textarea>
            <p class="hint">After saving, click <strong>Refresh</strong> on the dashboard to re-classify campaigns.</p>
            <button type="submit" class="btn primary">Save grouping rules</button>
          </form>
        </section>"""

    # --- Brand colors (Penn only) ---
    theme_section = ""
    if slug == "penn" and db_editable:
        theme_section = _brand_colors_section_html(
            settings_url=settings_url,
            theme=dashboard_theme.load_client_theme(slug),
        )

    # --- Insights editor ---
    insights_fold = ""
    if dashboard_service.can_edit_penn_insights(session_is_admin=session_is_admin, access_key=access_key):
        insights_fold = f"""
        <details class="settings-fold">
          <summary>Dashboard insights (admin notes)</summary>
          <div class="fold-body">
            {dashboard_service._insights_editor_html(client_slug=slug, access_key=access_key, use_session=use_session, snapshot=snapshot)}
          </div>
        </details>"""

    client_meta_tip = ""
    if snapshot:
        dr = snapshot.get("date_range") or {}
        refreshed = snapshot.get("refreshed_at") or "—"
        client_meta_tip = _esc(
            f"Date range: {dr.get('start', '')} → {dr.get('end', '')}\nLast refreshed: {refreshed} UTC"
        )

    content = f"""
    {notice}
    {edit_form}
    <section class="panel panel--primary">
      <h2>Refresh data</h2>
      {refresh_block}
    </section>
    {data_sync_section}
    {bq_mart_section}
    {sections_section}
    {bl_rules_section}
    {theme_section}
    {insights_fold}
    """

    return dashboard_service.render_client_shell_page(
        client_slug=slug,
        label=cfg.label,
        active_nav="settings",
        page_title="Settings",
        page_subtitle=f"{cfg.label} · settings",
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
