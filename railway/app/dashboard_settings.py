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
    return_url: str,
    can_manage_oauth: bool = True,
    connection_details: list[str] | None = None,
    status_message: str | None = None,
) -> str:
    prereq = oauth_flows.connect_prerequisites(platform)
    pub = oauth_store.public_status(platform)
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
            for cid in ids[:12]:
                try:
                    meta = google_ads_service.get_account_metadata(cid)
                    name = meta.get("descriptive_name") or cid
                    details.append(
                        f"{name} · ID {meta.get('customer_id') or cid} · "
                        f"{meta.get('currency_code') or '—'} · {meta.get('time_zone') or '—'}"
                    )
                except Exception:
                    details.append(f"Customer ID {cid}")
            extra = f" (+{len(ids) - 12} more)" if len(ids) > 12 else ""
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


def render_admin_oauth_section(
    *,
    return_url: str = "/admin",
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
) -> str:
    notice = ""
    if oauth_connected:
        labels = {"google_ads": "Google Ads", "linkedin": "LinkedIn", "meta": "Meta"}
        notice += (
            f'<div class="notice ok">{_esc(labels.get(oauth_connected, oauth_connected))} '
            f"connected successfully.</div>"
        )
    if oauth_error:
        notice += f'<div class="notice err">{_esc(oauth_error)}</div>'

    cards = []
    for platform, label in (
        ("google_ads", "Google Ads"),
        ("linkedin", "LinkedIn"),
        ("meta", "Meta"),
    ):
        probe = probe_agency_oauth_platform(platform)
        cards.append(
            _oauth_platform_card_html(
                platform=platform,
                label=label,
                return_url=return_url,
                can_manage_oauth=True,
                connection_details=probe.get("details") or None,
                status_message=str(probe.get("message") or ""),
            )
        )

    base = _esc(oauth_flows.public_base_url())
    return f"""
    {notice}
    <section class="admin-oauth-section">
      <h2>Ad platform connections</h2>
      <p class="muted">Connect once here for the whole agency. Each client dashboard then maps its own account IDs.</p>
      <div class="oauth-grid">{"".join(cards)}</div>
      <details class="admin-fold">
        <summary>OAuth callback URLs (developer setup)</summary>
        <ul class="checklist mono">
          <li>{base}/oauth/google_ads/callback</li>
          <li>{base}/oauth/linkedin/callback</li>
          <li>{base}/oauth/meta/callback</li>
        </ul>
      </details>
    </section>"""


def _agency_oauth_status_html(*, api: dict[str, Any]) -> str:
    rows = []
    for platform, label in (
        ("google_ads", "Google Ads"),
        ("linkedin", "LinkedIn"),
        ("meta", "Meta"),
    ):
        pub = oauth_store.public_status(platform)
        probe = probe_agency_oauth_platform(platform) if pub.connected else None
        detail = "Not connected — ask an admin to connect in Admin."
        if pub.connected and probe:
            detail = str(probe.get("message") or "Connected")
        elif pub.connected:
            detail = "Connected"
        rows.append(_connection_row(label, pub.connected, detail))

    ga4_ok = api["ga4"]["ok"]
    ga4_detail = f"{api['ga4']['registry_count']} GA4_CLIENTS entries in Railway"
    if not ga4_ok:
        ga4_detail = "Set GCP_SERVICE_ACCOUNT_JSON + GA4_CLIENTS in Railway"
    rows.append(_connection_row("GA4 / BigQuery", ga4_ok, ga4_detail))

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


def _setup_checklist_html(*, api: dict[str, Any], cfg: PennDashboardConfig, ga4_ok: bool) -> str:
    agency_connected = all(
        oauth_store.public_status(platform).connected
        for platform in ("google_ads", "linkedin", "meta")
    )
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
            target = ga4_clients.resolve_target(client_key=cfg.ga4_client_key)
            client = bigquery_service.build_client(project_id=target.bq_project_id)
            client.query(
                f"SELECT 1 FROM `{target.bq_project_id}.{target.bq_dataset_id}.events_*` LIMIT 1"
            ).result(timeout=30)
            results["ga4"] = {
                "ok": True,
                "message": (
                    f"{target.label or cfg.ga4_client_key} · {target.bq_project_id}/"
                    f"{target.bq_dataset_id} · property {target.account_id}"
                ),
            }
        except Exception as exc:
            results["ga4"] = {"ok": False, "message": str(exc)[:300]}
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
) -> str:
    slug = client_slug.strip().lower()
    settings_url = _settings_url(client_slug=slug, access_key=access_key, use_session=use_session)
    snapshot = dashboard_snapshots.get_snapshot(slug)
    auth = build_auth_status()
    api = build_api_status()
    ga4_target = _ga4_target_summary(cfg)
    can_edit = session_is_admin if use_session else bool(access_key)
    db_editable = web_users.enabled() and session_is_admin

    notice = ""
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
          <h2>1. Map client account IDs</h2>
          <p class="muted">Enter the Google, LinkedIn, Meta, and GA4 identifiers for <strong>{_esc(cfg.label)}</strong>.
          After saving, we verify each ID against the agency connection and show account details below.</p>
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
                <p class="hint">Find IDs in Admin → Google Ads → accessible accounts list.</p>
              </div>
              <div>
                <label for="linkedin_account_id">LinkedIn account ID</label>
                <input id="linkedin_account_id" name="linkedin_account_id" type="text"
                  value="{_esc(cfg.linkedin_account_id or '')}" placeholder="508590994">
                <p class="hint">Find IDs in Admin → LinkedIn → accessible accounts list.</p>
              </div>
              <div>
                <label for="meta_account_id">Meta ad account ID</label>
                <input id="meta_account_id" name="meta_account_id" type="text"
                  value="{_esc(cfg.meta_account_id or '')}" placeholder="2581574002135957">
                <p class="hint">Find IDs in Admin → Meta → accessible accounts list.</p>
              </div>
              <div>
                <label for="ga4_client_key">GA4 client key</label>
                <input id="ga4_client_key" name="ga4_client_key" type="text"
                  value="{_esc(cfg.ga4_client_key or '')}" placeholder="penn">
                <p class="hint">Must match a key in GA4_CLIENTS (Railway).</p>
              </div>
            </div>
            <button type="submit" class="btn primary">Save &amp; verify mapping</button>
          </form>
        </section>
        """
    elif use_session and not session_is_admin:
        edit_form = """
        <section class="panel">
          <h2>1. Map client account IDs</h2>
          <p class="muted">Only admins can edit account IDs. Ask your agency admin to update this client's mapping in Settings.</p>
        </section>
        """
    else:
        edit_form = f"""
        <section class="panel">
          <h2>2. Map client account IDs</h2>
          <p class="muted">Current IDs: Google {_esc(cfg.google_customer_id or "—")}, LinkedIn {_esc(cfg.linkedin_account_id or "—")}, Meta {_esc(cfg.meta_account_id or "—")}.</p>
        </section>
        """

        edit_form = f"""
        <section class="panel">
          <h2>1. Map client account IDs</h2>
          <p class="muted">Current IDs: Google {_esc(cfg.google_customer_id or "—")}, LinkedIn {_esc(cfg.linkedin_account_id or "—")}, Meta {_esc(cfg.meta_account_id or "—")}.</p>
        </section>
        """

    test_btn = ""
    if can_edit:
        test_btn = f"""
        <form method="post" action="{settings_url}" class="inline-form">
          <input type="hidden" name="action" value="test">
          <button type="submit" class="btn secondary">Re-verify mapped accounts</button>
        </form>
        """

    refresh_block = dashboard_service._refresh_toolbar(
        client_slug=slug,
        access_key=access_key,
        use_session=use_session,
        snapshot=snapshot,
        flash_message=None,
    )

    agency_status = _agency_oauth_status_html(api=api)

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
        labels = {"google": "Google Ads", "linkedin": "LinkedIn", "meta": "Meta", "ga4": "GA4"}
        for platform in ("google", "linkedin", "meta", "ga4"):
            row = probe_results.get(platform) or {}
            if row.get("skipped"):
                probe_rows.append(
                    f"<tr><td>{_esc(labels[platform])}</td>"
                    f'<td><span class="badge err">Not mapped</span></td>'
                    f'<td class="detail">{_esc(str(row.get("message") or "—"))}</td></tr>'
                )
                continue
            probe_rows.append(
                _connection_row(
                    labels[platform],
                    bool(row.get("ok")),
                    str(row.get("message") or "—"),
                )
            )
        summary = "All mapped accounts verified." if probe_results.get("overall_ok") else "Some mapped accounts need attention."
        probe_fold = f"""
        <section class="panel panel--primary">
          <h2>Account verification</h2>
          <p class="muted">{_esc(summary)} Details come from the agency connection in Admin.</p>
          <table class="status-table">
            <thead><tr><th>Platform</th><th>Status</th><th>Account details</th></tr></thead>
            <tbody>{"".join(probe_rows)}</tbody>
          </table>
        </section>"""

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
      <p class="muted">Connect platforms in <a href="/admin">Admin</a>, map this client's account IDs below, then refresh data.</p>
      {checklist}
    </section>
    {agency_status}
    {edit_form}
    {bl_rules_section}
    {theme_section}
    {probe_fold}
    <section class="panel panel--primary">
      <h2>2. Refresh data</h2>
      <p class="muted">After mapping account IDs, run a full refresh to pull campaign data into this dashboard.</p>
      {refresh_block}
      <div class="toolbar">{test_btn}</div>
    </section>
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
