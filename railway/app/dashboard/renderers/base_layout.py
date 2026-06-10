"""Shared dashboard chrome: favicon, topbar, shell layout."""

from __future__ import annotations

from typing import Any

import client_config
import dashboard_theme

from dashboard.utils.auth import min_refresh_seconds, refresh_cooldown_status
from dashboard.utils.formatting import esc as _esc
from dashboard.utils.urls import (
    client_switch_target_url as _client_switch_target_url,
    dashboard_page_url as _dashboard_page_url,
    files_page_url as _files_page_url,
    refresh_action_url as _refresh_action_url,
    settings_page_url as _settings_page_url,
    time_tracking_page_url as _time_tracking_page_url,
)

def favicon_head_html() -> str:
    return """
  <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
  <link rel="manifest" href="/static/site.webmanifest">
  <meta name="theme-color" content="#0a2540">"""


def session_account_html(*, email: str | None, is_admin: bool) -> str:
    """Signed-in user: email, admin link, sign out (sidebar footer)."""
    if not email:
        return ""
    admin_link = (
        '<a class="account-link" href="/admin">Admin</a><span class="account-sep">·</span>'
        if is_admin
        else ""
    )
    return f"""
    <div class="account-nav">
      <span class="account-email" title="{_esc(email)}">{_esc(email)}</span>
      <div class="account-actions">
        {admin_link}
        <form method="post" action="/logout" class="account-logout-form">
          <button type="submit" class="account-link">Sign out</button>
        </form>
      </div>
    </div>
    """


def refresh_toolbar(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool = False,
    snapshot: dict[str, Any] | None,
    flash_message: str | None = None,
) -> str:
    refresh_url = _refresh_action_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    if not refresh_url:
        return ""
    quick_allowed, quick_remaining = refresh_cooldown_status(snapshot, quick=True)
    full_allowed, full_remaining = refresh_cooldown_status(snapshot, quick=False)
    notice = ""
    if flash_message:
        notice = f'<div class="notice">{_esc(flash_message)}</div>'
    elif min_refresh_seconds(quick=False) > 0 and not quick_allowed and not full_allowed:
        mins = max(1, (min(quick_remaining, full_remaining) + 59) // 60)
        notice = f'<div class="notice muted">Refresh available in ~{mins} min.</div>'
    if quick_allowed:
        quick_btn = (
            f'<form method="post" action="{refresh_url}" class="refresh-form">'
            f'<input type="hidden" name="quick" value="1">'
            f'<button type="submit" class="refresh-btn">Quick refresh</button></form>'
        )
    else:
        quick_btn = '<button type="button" class="refresh-btn" disabled>Quick refresh</button>'
    if full_allowed:
        full_btn = (
            f'<form method="post" action="{refresh_url}" class="refresh-form">'
            f'<button type="submit" class="refresh-btn refresh-btn--secondary">'
            f"Full refresh</button></form>"
        )
    else:
        full_btn = (
            '<button type="button" class="refresh-btn refresh-btn--secondary" disabled>'
            "Full refresh</button>"
        )
    buttons = f'<div class="refresh-actions">{quick_btn}{full_btn}</div>'
    return f'<div class="refresh-bar">{notice}{buttons}</div>'


def topbar_client_selector_html(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
    session_is_admin: bool,
) -> str:
    if session_is_admin and use_session:
        import client_config

        options = []
        current = (client_slug or "").strip().lower()
        for slug, client_label in client_config.list_dashboard_clients():
            selected = " selected" if slug == current else ""
            dest = _client_switch_target_url(
                client_slug=slug,
                active_nav=active_nav,
                access_key=access_key,
                use_session=use_session,
            )
            options.append(
                f'<option value="{_esc(dest)}"{selected}>{_esc(client_label)}</option>'
            )
        return f"""
          <label class="sr-only" for="clientSwitcher">Client</label>
          <select id="clientSwitcher" class="topbar-client-switcher" aria-label="Switch client">
            {"".join(options)}
          </select>"""
    return f'<span class="topbar-client-label" id="topbarClientLabel">{_esc(label)}</span>'


def dash_top_header_html(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
    session_is_admin: bool,
    session_email: str | None,
    show_files: bool,
    show_time_tracking: bool | None = None,
) -> str:
    overview_url = _dashboard_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    ) or "#"
    settings_url = _settings_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    ) or "#"
    files_url = _files_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    ) or "#"
    time_tracking_url = _time_tracking_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    ) or "#"

    try:
        import oauth_store

        harvest_connected = oauth_store.public_status("harvest").connected
    except Exception:
        harvest_connected = False
    if show_time_tracking is None:
        show_time_tracking = harvest_connected
    else:
        show_time_tracking = bool(show_time_tracking) and harvest_connected

    client_selector = topbar_client_selector_html(
        client_slug=client_slug,
        label=label,
        active_nav=active_nav,
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
    )

    icon_files = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
        '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>'
        '<path d="M14 2v6h6"/></svg>'
    )
    icon_settings = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>'
        '</svg>'
    )
    icon_time = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M12 7v5l3 2"/>'
        '</svg>'
    )

    files_btn = ""
    if show_files:
        files_active = active_nav in ("files", "insights-upload")
        if files_active:
            files_btn = (
                f'<span class="dash-top-btn active" aria-current="page" title="Files">{icon_files}</span>'
            )
        else:
            files_btn = f'<a href="{files_url}" class="dash-top-btn" title="Files">{icon_files}</a>'

    time_btn = ""
    if show_time_tracking:
        if active_nav == "time-tracking":
            time_btn = (
                f'<span class="dash-top-btn active" aria-current="page" title="Time Tracking">{icon_time}</span>'
            )
        else:
            time_btn = f'<a href="{time_tracking_url}" class="dash-top-btn" title="Time Tracking">{icon_time}</a>'

    if active_nav == "settings":
        settings_btn = (
            f'<span class="dash-top-btn active" aria-current="page" title="Settings">{icon_settings}</span>'
        )
    else:
        settings_btn = f'<a href="{settings_url}" class="dash-top-btn" title="Settings">{icon_settings}</a>'

    account_html = ""
    if session_email:
        admin_link = (
            '<a class="dash-top-account-link" href="/admin">Admin</a><span class="dash-top-account-sep">·</span>'
            if session_is_admin
            else ""
        )
        account_html = f"""
        <div class="dash-top-account">
          <span class="dash-top-account-email" title="{_esc(session_email)}">{_esc(session_email)}</span>
          <div class="dash-top-account-actions">
            {admin_link}
            <form method="post" action="/logout" class="dash-top-logout-form">
              <button type="submit" class="dash-top-account-link">Sign out</button>
            </form>
          </div>
        </div>"""

    return f"""
    <header class="dash-top-header" role="banner">
      <div class="dash-top-inner">
        <div class="dash-top-left">
          <div class="dash-logo-lockup">
            <a href="{overview_url}" class="dash-logo" aria-label="Sagefrog home">
              <img class="dash-logo-img" src="/static/sagefrog-logo.png" alt="Sagefrog" width="272" height="92" />
              <img class="dash-logo-icon" src="/static/apple-touch-icon.png" alt="" width="180" height="180" aria-hidden="true" />
            </a>
            <span class="dash-logo-divider" aria-hidden="true"></span>
            <span class="dash-beta-tag">Beta</span>
          </div>
        </div>
        <div class="dash-top-right">
          {client_selector}
          {time_btn}
          {files_btn}
          {settings_btn}
          {account_html}
        </div>
      </div>
    </header>"""


def dashboard_topbar_js() -> str:
    return """
    document.getElementById('clientSwitcher')?.addEventListener('change', (e) => {
      const url = e.target.value;
      if (url) window.location.href = url;
    });
    let andreClicks = 0;
    let andreTimer = null;
    document.getElementById('topbarClientLabel')?.addEventListener('click', () => {
      andreClicks += 1;
      clearTimeout(andreTimer);
      andreTimer = setTimeout(() => { andreClicks = 0; }, 1600);
      if (andreClicks >= 3) {
        andreClicks = 0;
        if (typeof showAndreToast === 'function') showAndreToast();
      }
    });
    """


def dashboard_view_tabs_html(*, show_website: bool, show_campaigns: bool = True) -> str:
    website_tab = ""
    if show_website:
        website_tab = (
            '<button type="button" class="dash-view-btn" data-view="website" role="tab" '
            'aria-selected="false">Website Analytics</button>'
        )
    campaigns_tab = ""
    if show_campaigns:
        campaigns_tab = (
            '<button type="button" class="dash-view-btn" data-view="campaigns" role="tab" '
            'aria-selected="false">Campaign Explorer</button>'
        )
    return f"""
      <nav class="dash-view-nav" role="tablist" aria-label="Dashboard views">
        <button type="button" class="dash-view-btn active" data-view="overview" role="tab" aria-selected="true">Overview</button>
        {campaigns_tab}
        {website_tab}
      </nav>"""


def render_client_shell_page(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    page_title: str,
    page_subtitle: str,
    content_html: str,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    client_meta_tip: str = "",
    extra_css: str = "",
    show_business_line: bool | None = None,
    show_files: bool | None = None,
) -> str:
    """Shared dashboard chrome for settings, files, and other child pages."""
    del page_subtitle, client_meta_tip, show_business_line
    theme = dashboard_theme.load_client_theme(client_slug)
    if show_files is None:
        import client_insight_documents as docs

        show_files = docs.enabled()
    top_header = dash_top_header_html(
        client_slug=client_slug,
        label=label,
        active_nav=active_nav,
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=show_files,
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — {_esc(page_title)}</title>
  {favicon_head_html()}
  <style>
    {dashboard_theme.root_css_block(theme)}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }}
    .app-shell {{ display: flex; flex-direction: column; min-height: 100vh; }}
    .dash-main {{ flex: 1; min-width: 0; display: flex; flex-direction: column; width: 100%; }}
    .dash-content {{ flex: 1; width: 100%; padding: 28px 32px 48px; }}
    .wrap {{ width: 100%; max-width: none; min-width: 0; }}
    {DASH_TOPBAR_CSS}
    {extra_css}
  </style>
</head>
<body>
  <div class="app-shell">
    {top_header}
    <div class="dash-main">
      <div class="dash-content">
        <div class="wrap">
          {content_html}
        </div>
      </div>
    </div>
  </div>
  <script>{dashboard_topbar_js()}</script>
</body>
</html>"""


DASH_TOPBAR_CSS = """
    .dash-top-header {
      position: sticky;
      top: 0;
      z-index: 80;
      background: #fff;
      border-bottom: 1px solid var(--border);
      box-shadow: 0 1px 0 rgba(10, 37, 64, 0.04);
    }
    .dash-top-inner {
      width: 100%;
      margin: 0 auto;
      padding: 14px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }
    .dash-top-left { display: flex; align-items: center; min-width: 0; flex-shrink: 0; }
    .dash-top-right {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .dash-logo-lockup {
      display: inline-flex;
      align-items: center;
      min-width: 0;
      flex-shrink: 0;
    }
    .dash-logo {
      display: inline-flex;
      align-items: center;
      text-decoration: none;
      flex-shrink: 0;
    }
    .dash-logo-img {
      display: block;
      height: 56px;
      width: auto;
      max-width: min(320px, 52vw);
    }
    .dash-logo-icon {
      display: none;
      width: 32px;
      height: 32px;
      border-radius: 999px;
      flex-shrink: 0;
    }
    .dash-logo-divider {
      width: 1px;
      height: 44px;
      margin: 0 14px;
      background: #d1d5db;
      flex-shrink: 0;
    }
    .dash-beta-tag {
      display: inline-flex;
      align-items: center;
      padding: 6px 12px;
      border-radius: 8px;
      background: #fef3e7;
      color: #b45309;
      font-size: 0.8rem;
      font-weight: 650;
      letter-spacing: 0.02em;
      line-height: 1;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .topbar-client-switcher {
      min-width: 160px;
      max-width: 240px;
      appearance: none;
      border: 1px solid #bae6fd;
      border-radius: 999px;
      background: #f0f9ff
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%231e40af' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")
        no-repeat right 14px center;
      color: var(--navy);
      font: inherit;
      font-size: 0.9rem;
      font-weight: 650;
      padding: 10px 36px 10px 16px;
      cursor: pointer;
    }
    .topbar-client-switcher:hover,
    .topbar-client-switcher:focus-visible {
      border-color: #7dd3fc;
      background-color: #e0f2fe;
      outline: none;
    }
    .topbar-client-label {
      font-size: 0.92rem;
      font-weight: 650;
      color: var(--navy);
      padding: 10px 16px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--surface);
    }
    .dash-top-btn {
      appearance: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 42px;
      height: 42px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--navy);
      text-decoration: none;
      cursor: pointer;
      flex-shrink: 0;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
    }
    .dash-top-btn svg { width: 18px; height: 18px; }
    .dash-top-btn:hover {
      background: #f4f7fb;
      border-color: #b8c4d4;
    }
    .dash-top-btn.active {
      background: #f0f9ff;
      border-color: #93c5fd;
      color: var(--accent);
    }
    .dash-top-account {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 2px;
      margin-left: 4px;
      max-width: 200px;
    }
    .dash-top-account-email {
      font-size: 0.78rem;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 100%;
    }
    .dash-top-account-actions {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 0.78rem;
    }
    .dash-top-account-link {
      appearance: none;
      border: 0;
      background: none;
      padding: 0;
      font: inherit;
      color: var(--accent);
      text-decoration: none;
      cursor: pointer;
    }
    .dash-top-account-link:hover { text-decoration: underline; }
    .dash-top-account-sep { color: var(--muted); }
    .dash-top-logout-form { display: inline; margin: 0; }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
    @media (max-width: 720px) {
      .dash-top-inner { padding: 10px 12px; gap: 8px; flex-wrap: nowrap; }
      .dash-top-left { flex: 0 0 auto; }
      .dash-top-right { flex: 1 1 auto; flex-wrap: nowrap; gap: 6px; }
      .dash-logo-lockup { gap: 6px; }
      .dash-logo-img { display: none; }
      .dash-logo-icon { display: block; width: 30px; height: 30px; }
      .dash-logo-divider { display: none; }
      .dash-beta-tag { padding: 5px 8px; font-size: 0.7rem; border-radius: 999px; }
      .topbar-client-switcher {
        min-width: 0;
        width: clamp(82px, 28vw, 128px);
        max-width: 128px;
        padding: 8px 28px 8px 10px;
        background-position: right 10px center;
        font-size: 0.82rem;
      }
      .topbar-client-label {
        min-width: 0;
        max-width: 116px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        padding: 8px 10px;
        font-size: 0.82rem;
      }
      .dash-top-btn { width: 36px; height: 36px; }
      .dash-top-btn svg { width: 16px; height: 16px; }
      .dash-top-account { display: none; }
    }
    @media (max-width: 380px) {
      .dash-top-inner { padding-left: 8px; padding-right: 8px; gap: 6px; }
      .dash-logo-lockup { gap: 4px; }
      .dash-logo-icon { width: 28px; height: 28px; }
      .dash-beta-tag { padding: 4px 6px; font-size: 0.66rem; }
      .dash-top-right { gap: 4px; }
      .topbar-client-switcher { width: clamp(72px, 25vw, 104px); max-width: 104px; }
      .topbar-client-label { max-width: 96px; }
      .dash-top-btn { width: 34px; height: 34px; }
    }
"""


