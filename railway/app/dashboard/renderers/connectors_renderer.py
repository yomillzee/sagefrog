"""HTML renderer for the Connectors directory and setup wizard pages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote as _url_quote

import connector_config_store
import connectors  # noqa: F401 — triggers handler registration
from connectors.base import CONNECTOR_ORDER, ConnectorHandler, all_handlers, get as get_handler
from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc

# ──────────────────────────────────────────────────────────────────────────────
# SVG icons per platform
# ──────────────────────────────────────────────────────────────────────────────

_PLATFORM_ICONS: dict[str, str] = {
    "linkedin_ads": (
        '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        '<path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 '
        "1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 "
        "3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 "
        "2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 "
        '0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 '
        '22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>'
    ),
    "meta_ads": (
        '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        '<path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 '
        "10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 "
        "1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 "
        '3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>'
    ),
    "google_ads": (
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<circle cx="12" cy="12" r="10" fill="#4285F4"/>'
        '<path d="M12 6.5A5.5 5.5 0 1 0 17.5 12" stroke="#fff" stroke-width="2" stroke-linecap="round"/>'
        '<circle cx="17.5" cy="6.5" r="2.5" fill="#FBBC04"/>'
        '</svg>'
    ),
    "ga4": (
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<rect width="24" height="24" rx="5" fill="#E37400"/>'
        '<path d="M7 17V10M12 17V7M17 17v-4" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/>'
        '</svg>'
    ),
    "gsc": (
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<circle cx="11" cy="11" r="8" stroke="#4285F4" stroke-width="2"/>'
        '<path d="M21 21l-4.35-4.35" stroke="#34A853" stroke-width="2" stroke-linecap="round"/>'
        '</svg>'
    ),
    "hubspot": (
        '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        '<path d="M22.13 10.52A5.27 5.27 0 0 0 17.5 5.29V3.21A1.47 1.47 0 0 0 16.03 '
        "1.74h-.06a1.47 1.47 0 0 0-1.47 1.47v2.08a5.27 5.27 0 1 0 4.74 8.12l2.39 "
        "2.39a1.47 1.47 0 1 0 2.08-2.08l-2.39-2.39a5.22 5.22 0 0 0 .81-2.81zm-5.26 "
        "3.25a2.26 2.26 0 1 1 0-4.52 2.26 2.26 0 0 1 0 4.52zM5.27 9.74A2.47 2.47 0 1 "
        '0 5.27 14.68 2.47 2.47 0 0 0 5.27 9.74z"/></svg>'
    ),
    "circle": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
        '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>'
        '</svg>'
    ),
}

_STATUS_LABELS = {
    "not_connected": "Not connected",
    "connected": "Connected",
    "syncing": "Syncing",
    "error": "Error",
    "disconnected": "Disconnected",
    "disabled": "Disabled",
}

_STATUS_CLASSES = {
    "not_connected": "status-neutral",
    "connected": "status-ok",
    "syncing": "status-syncing",
    "error": "status-error",
    "disconnected": "status-neutral",
    "disabled": "status-neutral",
}

_CONNECTOR_CSS = """
  .connectors-page-title { font-size: 1.45rem; font-weight: 700; color: var(--navy); margin: 0 0 6px; }
  .connectors-page-sub { font-size: 0.95rem; color: var(--muted); margin: 0 0 28px; }
  .connector-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px;
  }
  .connector-card {
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 22px 24px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    box-shadow: 0 1px 4px rgba(10,37,64,.04);
    transition: box-shadow .15s, border-color .15s;
  }
  .connector-card:hover { box-shadow: 0 4px 16px rgba(10,37,64,.08); border-color: #b8c4d4; }
  .connector-card-header { display: flex; align-items: center; gap: 14px; }
  .connector-icon {
    width: 44px; height: 44px; border-radius: 10px;
    background: #f0f4f9; display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; color: var(--navy);
  }
  .connector-icon svg { width: 26px; height: 26px; }
  .connector-card-name { font-size: 1rem; font-weight: 650; color: var(--navy); }
  .connector-status-row { display: flex; align-items: center; gap: 8px; min-height: 22px; }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }
  .status-neutral .status-dot { background: #9ca3af; }
  .status-ok .status-dot { background: #22c55e; }
  .status-syncing .status-dot { background: #3b82f6; animation: pulse-dot 1.2s infinite; }
  .status-error .status-dot { background: #ef4444; }
  @keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:.4} }
  .status-label { font-size: 0.85rem; color: var(--muted); }
  .status-ok .status-label { color: #16a34a; font-weight: 600; }
  .status-error .status-label { color: #dc2626; font-weight: 600; }
  .connector-last-sync { font-size: 0.78rem; color: var(--muted); }
  .connector-card-action { margin-top: auto; }
  .btn-connect {
    display: inline-block; padding: 8px 18px; border-radius: 8px;
    background: var(--accent); color: #fff; font-size: 0.88rem; font-weight: 650;
    text-decoration: none; border: none; cursor: pointer;
    transition: background .15s;
  }
  .btn-connect:hover { background: #1d4ed8; }
  .btn-manage {
    display: inline-block; padding: 8px 18px; border-radius: 8px;
    background: #f0f4f9; color: var(--navy); font-size: 0.88rem; font-weight: 650;
    text-decoration: none; border: 1px solid var(--border); cursor: pointer;
    transition: background .15s;
  }
  .btn-manage:hover { background: #e2e8f0; }

  /* ── Wizard / Management page ── */
  .connector-detail-header { display: flex; align-items: center; gap: 16px; margin-bottom: 28px; }
  .connector-detail-icon {
    width: 56px; height: 56px; border-radius: 13px;
    background: #f0f4f9; display: flex; align-items: center; justify-content: center; color: var(--navy);
  }
  .connector-detail-icon svg { width: 32px; height: 32px; }
  .connector-detail-title { font-size: 1.4rem; font-weight: 700; color: var(--navy); margin: 0 0 4px; }
  .connector-detail-status { display: flex; align-items: center; gap: 8px; }

  /* Stepper */
  .wizard-stepper {
    counter-reset: step;
    display: flex; flex-direction: column; gap: 0;
    border: 1px solid var(--border); border-radius: 14px;
    overflow: hidden; background: #fff;
    max-width: 680px;
  }
  .wizard-step {
    border-bottom: 1px solid var(--border);
    padding: 22px 28px;
  }
  .wizard-step:last-child { border-bottom: 0; }
  .wizard-step-header {
    display: flex; align-items: center; gap: 14px; cursor: default;
  }
  .wizard-step-num {
    counter-increment: step;
    width: 30px; height: 30px; border-radius: 50%;
    background: #e5eaf2; color: var(--navy);
    display: flex; align-items: center; justify-content: center;
    font-size: 0.85rem; font-weight: 700; flex-shrink: 0;
  }
  .wizard-step.step-done .wizard-step-num {
    background: #22c55e; color: #fff;
  }
  .wizard-step.step-active .wizard-step-num {
    background: var(--accent); color: #fff;
  }
  .wizard-step-title { font-size: 0.98rem; font-weight: 650; color: var(--navy); }
  .wizard-step-summary { font-size: 0.85rem; color: var(--muted); margin-top: 2px; }
  .wizard-step-body {
    display: none; margin-top: 18px; padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .wizard-step.step-active .wizard-step-body { display: block; }

  /* Account list */
  .account-list { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; }
  .account-item {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 16px; border: 1px solid var(--border); border-radius: 9px;
    cursor: pointer; transition: border-color .12s, background .12s;
  }
  .account-item:hover { border-color: var(--accent); background: #f0f7ff; }
  .account-item input[type=radio] { accent-color: var(--accent); flex-shrink: 0; }
  .account-item-name { font-size: 0.92rem; font-weight: 600; }
  .account-item-id { font-size: 0.78rem; color: var(--muted); }
  .account-item.selected { border-color: var(--accent); background: #eff6ff; }

  /* Destination fields */
  .dest-field { margin-bottom: 16px; }
  .dest-field label { display: block; font-size: 0.82rem; font-weight: 650; color: var(--navy); margin-bottom: 6px; }
  .dest-field input {
    width: 100%; padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px;
    font: inherit; font-size: 0.92rem; color: var(--navy); background: #fff;
  }
  .dest-field input:focus { outline: 2px solid var(--accent); border-color: transparent; }

  /* Backfill buttons */
  .backfill-options { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }
  .backfill-btn {
    padding: 9px 18px; border: 1px solid var(--border); border-radius: 8px;
    background: #fff; color: var(--navy); font: inherit; font-size: 0.9rem; font-weight: 600;
    cursor: pointer; transition: border-color .12s, background .12s;
  }
  .backfill-btn:hover { border-color: var(--accent); background: #f0f7ff; }
  .backfill-btn.selected { border-color: var(--accent); background: #eff6ff; color: var(--accent); }

  /* Action buttons */
  .wizard-actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
  .btn-primary {
    padding: 10px 22px; border-radius: 8px; background: var(--accent); color: #fff;
    font: inherit; font-size: 0.92rem; font-weight: 650; border: none; cursor: pointer;
    transition: background .15s;
  }
  .btn-primary:hover { background: #1d4ed8; }
  .btn-primary:disabled { background: #9ca3af; cursor: not-allowed; }
  .btn-secondary {
    padding: 10px 22px; border-radius: 8px; background: #fff; color: var(--navy);
    font: inherit; font-size: 0.92rem; font-weight: 600;
    border: 1px solid var(--border); cursor: pointer; transition: background .15s;
  }
  .btn-secondary:hover { background: #f4f7fb; }

  /* Spinner */
  .spinner {
    display: inline-block; width: 20px; height: 20px; border-radius: 50%;
    border: 2px solid #e5eaf2; border-top-color: var(--accent);
    animation: spin .7s linear infinite; vertical-align: middle; margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Test result */
  .test-result { padding: 14px 18px; border-radius: 9px; font-size: 0.9rem; margin-top: 14px; }
  .test-result.ok { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
  .test-result.err { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }

  /* Management view */
  .mgmt-section {
    background: #fff; border: 1px solid var(--border); border-radius: 14px;
    padding: 24px 28px; margin-bottom: 16px; max-width: 680px;
  }
  .mgmt-section-title { font-size: 0.92rem; font-weight: 700; color: var(--muted); letter-spacing:.04em; text-transform:uppercase; margin: 0 0 16px; }
  .mgmt-row { display: flex; align-items: baseline; gap: 14px; padding: 6px 0; border-bottom: 1px solid #f1f5f9; }
  .mgmt-row:last-child { border-bottom: 0; }
  .mgmt-label { font-size: 0.85rem; color: var(--muted); min-width: 160px; flex-shrink: 0; }
  .mgmt-value { font-size: 0.92rem; color: var(--navy); font-weight: 500; }
  .mgmt-actions { display: flex; flex-wrap: wrap; gap: 10px; max-width: 680px; margin-top: 8px; }
  .btn-danger {
    padding: 9px 18px; border-radius: 8px; background: #fff; color: #dc2626;
    font: inherit; font-size: 0.88rem; font-weight: 650;
    border: 1px solid #fca5a5; cursor: pointer; transition: background .15s;
  }
  .btn-danger:hover { background: #fef2f2; }

  /* Sync history table */
  .sync-history { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  .sync-history th {
    text-align: left; padding: 8px 12px; font-size: 0.78rem; font-weight: 700;
    color: var(--muted); letter-spacing:.04em; text-transform:uppercase;
    border-bottom: 1px solid var(--border);
  }
  .sync-history td { padding: 9px 12px; border-bottom: 1px solid #f1f5f9; color: var(--navy); }
  .sync-history tr:last-child td { border-bottom: 0; }
  .run-status-ok { color: #16a34a; font-weight: 600; }
  .run-status-failed { color: #dc2626; font-weight: 600; }
  .run-status-running { color: #3b82f6; }

  /* Disconnect modal */
  .modal-overlay {
    display: none; position: fixed; inset: 0; background: rgba(8,18,33,.5);
    z-index: 200; align-items: center; justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal-box {
    background: #fff; border-radius: 16px; padding: 32px; max-width: 440px; width: 90%;
    box-shadow: 0 20px 60px rgba(8,18,33,.2);
  }
  .modal-title { font-size: 1.1rem; font-weight: 700; color: var(--navy); margin: 0 0 12px; }
  .modal-body { font-size: 0.92rem; color: var(--muted); line-height: 1.6; margin-bottom: 24px; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }

  .back-link { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); font-size: 0.88rem; text-decoration: none; margin-bottom: 20px; }
  .back-link:hover { color: var(--navy); }
  .back-link svg { width: 14px; height: 14px; }
"""


# ──────────────────────────────────────────────────────────────────────────────
# Directory page
# ──────────────────────────────────────────────────────────────────────────────

def render_connectors_directory(
    *,
    client_slug: str,
    label: str,
    configs: list[connector_config_store.ConnectorConfig],
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
    flash_error: str | None = None,
) -> str:
    by_type = {c.connector_type: c for c in configs}
    handlers = all_handlers()
    cards: list[str] = []

    for ctype in CONNECTOR_ORDER:
        handler = handlers.get(ctype)
        if not handler:
            continue
        cfg = by_type.get(ctype)
        status = cfg.status if cfg else "not_connected"
        status_label = _STATUS_LABELS.get(status, status)
        status_cls = _STATUS_CLASSES.get(status, "status-neutral")
        icon = _PLATFORM_ICONS.get(ctype, "")
        detail_url = f"/dashboard/{client_slug}/connectors/{ctype}"

        last_sync_html = ""
        if cfg and cfg.last_success_at:
            ts = cfg.last_success_at
            last_sync_html = f'<div class="connector-last-sync">Last sync: {_fmt_dt(ts)}</div>'
        elif cfg and cfg.last_error_message:
            last_sync_html = f'<div class="connector-last-sync" style="color:#dc2626">Error: {_esc(cfg.last_error_message[:80])}</div>'

        if status in ("connected", "syncing", "error", "disabled"):
            action_btn = f'<a href="{_esc(detail_url)}" class="btn-manage">Manage</a>'
        else:
            action_btn = f'<a href="{_esc(detail_url)}" class="btn-connect">Connect</a>'

        cards.append(f"""
          <div class="connector-card">
            <div class="connector-card-header">
              <div class="connector-icon">{icon}</div>
              <div class="connector-card-name">{_esc(handler.display_name)}</div>
            </div>
            <div class="connector-status-row {_esc(status_cls)}">
              <span class="status-dot"></span>
              <span class="status-label">{_esc(status_label)}</span>
            </div>
            {last_sync_html}
            <div class="connector-card-action">{action_btn}</div>
          </div>
        """)

    flash_html = _flash_html(flash_message, flash_error)
    content = f"""
      {flash_html}
      <h1 class="connectors-page-title">Data Connectors</h1>
      <p class="connectors-page-sub">Connect your marketing platforms to enable reporting and daily data sync.</p>
      <div class="connector-grid">{"".join(cards)}</div>
    """
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="connectors",
        page_title="Connectors",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_CONNECTOR_CSS,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Connector detail page (wizard or management)
# ──────────────────────────────────────────────────────────────────────────────

def render_connector_detail(
    *,
    client_slug: str,
    label: str,
    connector_type: str,
    handler: ConnectorHandler,
    config: connector_config_store.ConnectorConfig | None,
    sync_runs: list[connector_config_store.ConnectorSyncRun],
    db_config: Any,  # ClientConfigRow | None
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    oauth_done: bool = False,
    oauth_error: str | None = None,
    flash_message: str | None = None,
    flash_error: str | None = None,
) -> str:
    icon = _PLATFORM_ICONS.get(connector_type, "")
    dir_url = f"/dashboard/{client_slug}/connectors"
    is_connected = config and config.status in ("connected", "syncing", "error", "disabled")

    if is_connected:
        content = _render_management_view(
            client_slug=client_slug,
            handler=handler,
            config=config,
            sync_runs=sync_runs,
            icon=icon,
            dir_url=dir_url,
            flash_message=flash_message,
            flash_error=flash_error,
        )
    else:
        content = _render_wizard(
            client_slug=client_slug,
            handler=handler,
            config=config,
            db_config=db_config,
            icon=icon,
            dir_url=dir_url,
            oauth_done=oauth_done,
            oauth_error=oauth_error,
            flash_message=flash_message,
            flash_error=flash_error,
        )

    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="connectors",
        page_title=handler.display_name,
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_CONNECTOR_CSS,
    )


def _render_wizard(
    *,
    client_slug: str,
    handler: ConnectorHandler,
    config: connector_config_store.ConnectorConfig | None,
    db_config: Any,
    icon: str,
    dir_url: str,
    oauth_done: bool,
    oauth_error: str | None,
    flash_message: str | None,
    flash_error: str | None,
) -> str:
    status = config.status if config else "not_connected"
    is_disconnected = status == "disconnected"
    has_oauth = config is not None and config.oauth_client_slug == client_slug

    # no_oauth connectors (GA4, GSC service-account) don't need authorization —
    # treat step 1 as always done.
    if handler.no_oauth:
        has_oauth = True
    else:
        has_oauth = has_oauth or oauth_done

    # Determine starting step
    start_step = 1
    if has_oauth:
        start_step = 2
    if config and config.source_account_id and has_oauth:
        start_step = 3

    oauth_error_html = ""
    if oauth_error:
        oauth_error_html = f'<div class="test-result err" style="margin-bottom:16px">{_esc(oauth_error)}</div>'

    flash_html = _flash_html(flash_message, flash_error)

    # OAuth start URL — return_to must be URL-encoded so ?oauth_done=1 isn't
    # parsed as an extra outer query param by the connect endpoint.
    _return_to = _url_quote(
        f"/dashboard/{client_slug}/connectors/{handler.connector_type}?oauth_done=1",
        safe="",
    )
    oauth_start_url = (
        f"/oauth/{handler.oauth_platform}/connect"
        f"?return_to={_return_to}"
        f"&client={_url_quote(client_slug, safe='')}"
    )

    # Pre-fill destination from existing config or db_config
    bq_project = (config and config.bq_project_id) or (db_config and db_config.gcp_project_id) or ""
    raw_ds = (config and config.raw_dataset_id) or handler.default_raw_dataset
    mart_ds = (config and config.mart_dataset_id) or handler.default_mart_dataset

    reconnect_note = ""
    if is_disconnected:
        reconnect_note = '<p style="color:var(--muted);font-size:.9rem;margin-bottom:20px">This connector was previously set up. Complete the steps below to reconnect.</p>'

    steps_html = f"""
    {reconnect_note}
    <div class="wizard-stepper" id="wizardStepper">

      <div class="wizard-step {'step-done' if has_oauth else ('step-active' if start_step == 1 else '')}" id="wizStep1">
        <div class="wizard-step-header">
          <div class="wizard-step-num">{'✓' if has_oauth else '1'}</div>
          <div>
            <div class="wizard-step-title">Connect {_esc(handler.display_name)}</div>
            {'<div class="wizard-step-summary">Authorized · <a href="/dashboard/' + client_slug + '/connectors/' + handler.connector_type + '/reauth" style="color:var(--muted);font-size:.85em">Re-authorize</a></div>' if has_oauth and not handler.no_oauth else ('<div class="wizard-step-summary">Service account configured</div>' if has_oauth and handler.no_oauth else '')}
          </div>
        </div>
        <div class="wizard-step-body">
          {oauth_error_html}
          {'<p style="color:var(--muted);font-size:.9rem">This connector uses a server-side service account — no authorization needed.</p>' if handler.no_oauth else f'<p style="color:var(--muted);font-size:.9rem;margin-bottom:16px">Authorize Sagefrog to access your {_esc(handler.display_name)} account.</p><a href="{_esc(oauth_start_url)}" class="btn-connect">Authorize {_esc(handler.display_name)}</a>'}
        </div>
      </div>

      <div class="wizard-step {'step-active' if start_step == 2 and not (config and config.source_account_id) else ('step-done' if config and config.source_account_id else '')}" id="wizStep2">
        <div class="wizard-step-header">
          <div class="wizard-step-num">{'✓' if config and config.source_account_id else '2'}</div>
          <div>
            <div class="wizard-step-title">Select account</div>
            {'<div class="wizard-step-summary">' + _esc(config.source_account_name or config.source_account_id or '') + '</div>' if config and config.source_account_id else ''}
          </div>
        </div>
        <div class="wizard-step-body">
          <div id="accountLoading" style="color:var(--muted);font-size:.9rem">
            <span class="spinner"></span>Loading accounts…
          </div>
          <div id="accountList" class="account-list" style="display:none"></div>
          <div id="accountError" class="test-result err" style="display:none"></div>
          <div class="wizard-actions" style="display:none" id="step2Actions">
            <button class="btn-primary" id="step2Next" onclick="confirmAccount()">Continue</button>
          </div>
        </div>
      </div>

      <div class="wizard-step {'step-active' if start_step >= 3 and not (config and config.bq_project_id) else ''}" id="wizStep3">
        <div class="wizard-step-header">
          <div class="wizard-step-num">3</div>
          <div><div class="wizard-step-title">Confirm destination</div></div>
        </div>
        <div class="wizard-step-body">
          <p style="color:var(--muted);font-size:.9rem;margin-bottom:18px">Where should synced data be written in BigQuery?</p>
          <div class="dest-field">
            <label for="bqProject">GCP project ID</label>
            <input id="bqProject" type="text" value="{_esc(bq_project)}" placeholder="your-gcp-project" />
          </div>
          <div class="dest-field">
            <label for="rawDataset">Raw dataset</label>
            <input id="rawDataset" type="text" value="{_esc(raw_ds)}" placeholder="{_esc(handler.default_raw_dataset)}" />
          </div>
          <div class="dest-field">
            <label for="martDataset">Mart dataset</label>
            <input id="martDataset" type="text" value="{_esc(mart_ds)}" placeholder="marketing_marts" />
          </div>
          <div class="wizard-actions">
            <button class="btn-primary" onclick="confirmDestination()">Continue</button>
          </div>
        </div>
      </div>

      <div class="wizard-step" id="wizStep4">
        <div class="wizard-step-header">
          <div class="wizard-step-num">4</div>
          <div><div class="wizard-step-title">Choose backfill range</div></div>
        </div>
        <div class="wizard-step-body">
          <p style="color:var(--muted);font-size:.9rem;margin-bottom:14px">How far back should we import historical data?</p>
          <div class="backfill-options">
            <button class="backfill-btn selected" data-days="30" onclick="selectBackfill(this, 30)">Last 30 days</button>
            <button class="backfill-btn" data-days="90" onclick="selectBackfill(this, 90)">Last 90 days</button>
            <button class="backfill-btn" data-days="180" onclick="selectBackfill(this, 180)">Last 180 days</button>
          </div>
          <div class="wizard-actions">
            <button class="btn-primary" onclick="confirmBackfill()">Continue</button>
          </div>
        </div>
      </div>

      <div class="wizard-step" id="wizStep5">
        <div class="wizard-step-header">
          <div class="wizard-step-num">5</div>
          <div><div class="wizard-step-title">Test connection</div></div>
        </div>
        <div class="wizard-step-body">
          <p style="color:var(--muted);font-size:.9rem;margin-bottom:16px">Verify the connection and run a quick data check before enabling daily sync.</p>
          <div class="wizard-actions">
            <button class="btn-primary" id="testBtn" onclick="runTest()">Run connection test</button>
          </div>
          <div id="testStatus" style="display:none"></div>
          <div class="wizard-actions" style="display:none" id="step5Next">
            <button class="btn-primary" onclick="activateStep(6)">Continue</button>
          </div>
        </div>
      </div>

      <div class="wizard-step" id="wizStep6">
        <div class="wizard-step-header">
          <div class="wizard-step-num">6</div>
          <div><div class="wizard-step-title">Enable daily sync</div></div>
        </div>
        <div class="wizard-step-body">
          <p style="color:var(--muted);font-size:.9rem;margin-bottom:16px">
            Your connection is set up. Enable daily sync to keep reporting data fresh.
          </p>
          <label style="display:flex;align-items:center;gap:10px;font-size:.92rem;font-weight:600;cursor:pointer">
            <input type="checkbox" id="syncToggle" checked style="accent-color:var(--accent);width:18px;height:18px">
            Schedule daily sync
          </label>
          <div class="wizard-actions" style="margin-top:20px">
            <button class="btn-primary" onclick="finishSetup()">Finish setup</button>
          </div>
        </div>
      </div>

    </div>
    """

    script = f"""
    <script>
    var _clientSlug = {_js_str(client_slug)};
    var _connType = {_js_str(handler.connector_type)};
    var _selectedAccountId = null;
    var _selectedAccountName = null;
    var _selectedBackfillDays = 30;
    var _configuredBqProject = null;
    var _configuredRawDs = null;
    var _configuredMartDs = null;

    function activateStep(n) {{
      document.querySelectorAll('.wizard-step').forEach((el, i) => {{
        var step = i + 1;
        if (step < n) {{
          el.classList.remove('step-active');
          el.classList.add('step-done');
          var num = el.querySelector('.wizard-step-num');
          if (num) num.textContent = '✓';
        }} else if (step === n) {{
          el.classList.add('step-active');
          el.classList.remove('step-done');
        }} else {{
          el.classList.remove('step-active','step-done');
        }}
      }});
      var target = document.getElementById('wizStep' + n);
      if (target) target.scrollIntoView({{behavior:'smooth', block:'nearest'}});
      if (n === 2) loadAccounts();
    }}

    // Allow clicking a completed step header to go back and change the selection
    document.addEventListener('DOMContentLoaded', function() {{
      document.querySelectorAll('.wizard-step').forEach(function(stepEl, i) {{
        var stepNum = i + 1;
        var header = stepEl.querySelector('.wizard-step-header');
        if (!header) return;
        header.style.cursor = 'pointer';
        header.addEventListener('click', function() {{
          if (stepEl.classList.contains('step-done')) {{
            activateStep(stepNum);
          }}
        }});
      }});
    }});

    function loadAccounts() {{
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/accounts';
      fetch(url).then(r => r.json()).then(data => {{
        document.getElementById('accountLoading').style.display = 'none';
        if (!data.ok) {{
          var err = document.getElementById('accountError');
          err.textContent = data.error || 'Failed to load accounts.';
          err.style.display = 'block';
          return;
        }}
        var list = document.getElementById('accountList');
        list.innerHTML = '';
        (data.accounts || []).forEach(acc => {{
          var item = document.createElement('label');
          item.className = 'account-item';
          item.innerHTML = '<input type="radio" name="account" value="' + acc.id + '" data-name="' + (acc.name||'') + '"> <div><div class="account-item-name">' + (acc.name||acc.id) + '</div><div class="account-item-id">ID: ' + acc.id + '</div></div>';
          item.querySelector('input').addEventListener('change', function() {{
            document.querySelectorAll('.account-item').forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
            _selectedAccountId = this.value;
            _selectedAccountName = this.dataset.name;
            document.getElementById('step2Actions').style.display = 'flex';
          }});
          list.appendChild(item);
        }});
        list.style.display = 'flex';
        if (!data.accounts || !data.accounts.length) {{
          list.innerHTML = '<p style="color:var(--muted);font-size:.9rem">No accounts found for this connection.</p>';
        }}
      }}).catch(err => {{
        document.getElementById('accountLoading').style.display = 'none';
        var errEl = document.getElementById('accountError');
        errEl.textContent = 'Failed to load accounts: ' + err.message;
        errEl.style.display = 'block';
      }});
    }}

    function confirmAccount() {{
      if (!_selectedAccountId) return;
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{source_account_id: _selectedAccountId, source_account_name: _selectedAccountName}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) activateStep(3);
        else alert(data.error || 'Failed to save account selection.');
      }});
    }}

    function confirmDestination() {{
      var proj = document.getElementById('bqProject').value.trim();
      var raw = document.getElementById('rawDataset').value.trim();
      var mart = document.getElementById('martDataset').value.trim();
      if (!proj) {{ alert('GCP project ID is required.'); return; }}
      _configuredBqProject = proj; _configuredRawDs = raw; _configuredMartDs = mart;
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{bq_project_id: proj, raw_dataset_id: raw, mart_dataset_id: mart}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) activateStep(4);
        else alert(data.error || 'Failed to save destination.');
      }});
    }}

    function selectBackfill(btn, days) {{
      document.querySelectorAll('.backfill-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      _selectedBackfillDays = days;
    }}

    function confirmBackfill() {{
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{backfill_days: _selectedBackfillDays}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) activateStep(5);
        else alert(data.error || 'Failed to save backfill range.');
      }});
    }}

    function runTest() {{
      var btn = document.getElementById('testBtn');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>Testing…';
      var statusEl = document.getElementById('testStatus');
      statusEl.style.display = 'none';
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/test';
      fetch(url, {{method:'POST'}}).then(r => r.json()).then(data => {{
        btn.disabled = false; btn.textContent = 'Run connection test';
        statusEl.className = 'test-result ' + (data.ok ? 'ok' : 'err');
        statusEl.textContent = data.message || (data.ok ? 'Connection verified.' : (data.error || 'Test failed.'));
        statusEl.style.display = 'block';
        if (data.ok) document.getElementById('step5Next').style.display = 'flex';
      }}).catch(err => {{
        btn.disabled = false; btn.textContent = 'Run connection test';
        statusEl.className = 'test-result err';
        statusEl.textContent = 'Test failed: ' + err.message;
        statusEl.style.display = 'block';
      }});
    }}

    function finishSetup() {{
      var syncEnabled = document.getElementById('syncToggle').checked;
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{sync_enabled: syncEnabled, status: 'connected'}})
      }}).then(() => {{
        window.location.href = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '?connected=1';
      }});
    }}

    // On load: activate the right step
    (function() {{
      var startStep = {start_step};
      activateStep(startStep);
      if (startStep === 2) loadAccounts();
    }})();
    </script>
    """

    return f"""
      <a href="{_esc(dir_url)}" class="back-link">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
        All connectors
      </a>
      <div class="connector-detail-header">
        <div class="connector-detail-icon">{icon}</div>
        <div>
          <h1 class="connector-detail-title">{_esc(handler.display_name)}</h1>
          {'<p style="color:var(--muted);font-size:.9rem;margin:0">Previously disconnected</p>' if is_disconnected else ''}
        </div>
      </div>
      {flash_html}
      {_flash_html(None, oauth_error)}
      {steps_html}
      {script}
    """


def _render_management_view(
    *,
    client_slug: str,
    handler: ConnectorHandler,
    config: connector_config_store.ConnectorConfig,
    sync_runs: list[connector_config_store.ConnectorSyncRun],
    icon: str,
    dir_url: str,
    flash_message: str | None,
    flash_error: str | None,
) -> str:
    status = config.status
    status_label = _STATUS_LABELS.get(status, status)
    status_cls = _STATUS_CLASSES.get(status, "status-neutral")

    last_sync_html = "Never"
    if config.last_success_at:
        last_sync_html = _fmt_dt(config.last_success_at)

    error_row = ""
    if config.last_error_message:
        error_row = f'<div class="test-result err" style="margin-bottom:16px">{_esc(config.last_error_message[:300])}</div>'

    runs_rows = ""
    for run in sync_runs:
        ts = _fmt_dt(run.started_at)
        dur = ""
        if run.completed_at and run.started_at:
            secs = int((run.completed_at - run.started_at).total_seconds())
            dur = f"{secs}s"
        rows_col = str(run.rows_loaded) if run.rows_loaded is not None else "—"
        status_map = {"completed": "run-status-ok", "failed": "run-status-failed", "running": "run-status-running"}
        status_cls_run = status_map.get(run.status, "")
        err_col = _esc(run.error_message[:60]) if run.error_message else ""
        runs_rows += f"<tr><td>{_esc(ts)}</td><td>{_esc(run.run_type)}</td><td class='{status_cls_run}'>{_esc(run.status)}</td><td>{rows_col}</td><td>{dur}</td><td style='color:#dc2626;font-size:.78rem'>{err_col}</td></tr>"

    runs_section = ""
    if sync_runs:
        runs_section = f"""
        <div class="mgmt-section">
          <div class="mgmt-section-title">Sync history</div>
          <table class="sync-history">
            <thead><tr><th>Started</th><th>Type</th><th>Status</th><th>Rows</th><th>Duration</th><th>Error</th></tr></thead>
            <tbody>{runs_rows}</tbody>
          </table>
        </div>
        """

    sync_url = f"/dashboard/{client_slug}/connectors/{handler.connector_type}/sync"
    disconnect_form = f"""
      <form id="disconnectForm" method="POST" action="/dashboard/{client_slug}/connectors/{handler.connector_type}/disconnect" style="display:none"></form>
    """

    return f"""
      <a href="{_esc(dir_url)}" class="back-link">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
        All connectors
      </a>
      <div class="connector-detail-header">
        <div class="connector-detail-icon">{icon}</div>
        <div>
          <h1 class="connector-detail-title">{_esc(handler.display_name)}</h1>
          <div class="connector-detail-status {_esc(status_cls)}">
            <span class="status-dot"></span>
            <span class="status-label">{_esc(status_label)}</span>
          </div>
        </div>
      </div>
      {_flash_html(flash_message, flash_error)}
      {error_row}

      <div class="mgmt-section">
        <div class="mgmt-section-title">Connection</div>
        <div class="mgmt-row">
          <span class="mgmt-label">Account</span>
          <span class="mgmt-value">{_esc(config.source_account_name or config.source_account_id or '—')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Account ID</span>
          <span class="mgmt-value">{_esc(config.source_account_id or '—')}</span>
        </div>
      </div>

      <div class="mgmt-section">
        <div class="mgmt-section-title">Destination</div>
        <div class="mgmt-row">
          <span class="mgmt-label">GCP project</span>
          <span class="mgmt-value">{_esc(config.bq_project_id or '—')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Raw dataset</span>
          <span class="mgmt-value">{_esc(config.raw_dataset_id or '—')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Mart dataset</span>
          <span class="mgmt-value">{_esc(config.mart_dataset_id or '—')}</span>
        </div>
      </div>

      <div class="mgmt-section">
        <div class="mgmt-section-title">Sync</div>
        <div class="mgmt-row">
          <span class="mgmt-label">Frequency</span>
          <span class="mgmt-value">{_esc(config.sync_frequency.capitalize() if config.sync_frequency else 'Daily')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Last successful sync</span>
          <span class="mgmt-value">{_esc(last_sync_html)}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Auto-sync enabled</span>
          <span class="mgmt-value">{'Yes' if config.sync_enabled else 'No'}</span>
        </div>
      </div>

      {runs_section}

      <div class="mgmt-actions">
        <button class="btn-secondary" id="syncNowBtn" onclick="runSyncNow()">Run sync now</button>
        {'<a href="/dashboard/' + client_slug + '/connectors/' + handler.connector_type + '/reauth" class="btn-secondary">Re-authorize</a>' if not handler.no_oauth else ''}
        <a href="/dashboard/{client_slug}/connectors/{handler.connector_type}" class="btn-secondary">Change account</a>
        <button class="btn-danger" onclick="showDisconnectModal()">Disconnect</button>
      </div>

      {disconnect_form}

      <div class="modal-overlay" id="disconnectModal">
        <div class="modal-box">
          <div class="modal-title">Disconnect {_esc(handler.display_name)}?</div>
          <div class="modal-body">
            This will stop future {_esc(handler.display_name)} syncs and remove saved authorization.
            Historical data already in reporting will remain available.
          </div>
          <div class="modal-actions">
            <button class="btn-secondary" onclick="hideDisconnectModal()">Cancel</button>
            <button class="btn-danger" onclick="document.getElementById('disconnectForm').submit()">Disconnect</button>
          </div>
        </div>
      </div>

      <div id="syncNowStatus" style="display:none;margin-top:12px"></div>

      <script>
      function showDisconnectModal() {{ document.getElementById('disconnectModal').classList.add('open'); }}
      function hideDisconnectModal() {{ document.getElementById('disconnectModal').classList.remove('open'); }}
      document.getElementById('disconnectModal').addEventListener('click', function(e) {{
        if (e.target === this) hideDisconnectModal();
      }});

      function runSyncNow() {{
        var btn = document.getElementById('syncNowBtn');
        var statusEl = document.getElementById('syncNowStatus');
        btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Syncing…';
        statusEl.style.display = 'none';
        fetch('/dashboard/{client_slug}/connectors/{handler.connector_type}/sync', {{method:'POST'}})
          .then(r => r.json()).then(data => {{
            btn.disabled = false; btn.textContent = 'Run sync now';
            statusEl.className = 'test-result ' + (data.ok ? 'ok' : 'err');
            statusEl.textContent = data.message || (data.ok ? 'Sync started.' : (data.error || 'Sync failed.'));
            statusEl.style.display = 'block';
          }}).catch(err => {{
            btn.disabled = false; btn.textContent = 'Run sync now';
            statusEl.className = 'test-result err';
            statusEl.textContent = 'Sync failed: ' + err.message;
            statusEl.style.display = 'block';
          }});
      }}
      </script>
    """


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _flash_html(message: str | None, error: str | None) -> str:
    if error:
        return f'<div class="test-result err" style="margin-bottom:16px">{_esc(str(error)[:300])}</div>'
    if message:
        return f'<div class="test-result ok" style="margin-bottom:16px">{_esc(str(message)[:300])}</div>'
    return ""


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    now = datetime.now(tz=UTC)
    try:
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return "Just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return dt.strftime("%b %d at %I:%M %p").replace(" 0", " ").replace(":0", ":")
    except Exception:
        return str(dt)[:16]


def _js_str(value: str) -> str:
    import json
    return json.dumps(str(value))
