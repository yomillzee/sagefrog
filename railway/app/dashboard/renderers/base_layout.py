"""Shared dashboard chrome: favicon, topbar, shell layout."""

from __future__ import annotations

from typing import Any

import client_config
import dashboard_theme

from dashboard.utils.auth import min_refresh_seconds, refresh_cooldown_status
from dashboard.utils.formatting import esc as _esc
from dashboard.utils.urls import (
    client_switch_target_url as _client_switch_target_url,
    connectors_page_url as _connectors_page_url,
    dashboard_page_url as _dashboard_page_url,
    files_page_url as _files_page_url,
    gtm_page_url as _gtm_page_url,
    lead_tracking_page_url as _lead_tracking_page_url,
    refresh_action_url as _refresh_action_url,
    settings_page_url as _settings_page_url,
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

        # Scope the switcher to new-build (connector platform) clients; legacy
        # dashboards drop out of the dropdown but stay reachable by URL. Always
        # keep the current client so visiting a legacy page directly still works.
        try:
            import connector_config_store
            _platform_slugs = connector_config_store.client_slugs_with_configs()
        except Exception:
            _platform_slugs = set()

        options = []
        current = (client_slug or "").strip().lower()
        for slug, client_label in client_config.list_dashboard_clients():
            if _platform_slugs and slug not in _platform_slugs and slug != current:
                continue
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
    files_btn = ""
    if show_files:
        files_active = active_nav in ("files", "insights-upload")
        if files_active:
            files_btn = (
                f'<span class="dash-top-btn active" aria-current="page" title="Files">{icon_files}</span>'
            )
        else:
            files_btn = f'<a href="{files_url}" class="dash-top-btn" title="Files">{icon_files}</a>'

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
    (function() {
      const shell = document.querySelector('.app-shell');
      const toggle = document.getElementById('sidebarToggle');
      const backdrop = document.getElementById('sidebarBackdrop');
      if (!shell || !toggle) return;
      const setOpen = (open) => {
        shell.classList.toggle('sidebar-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (backdrop) backdrop.hidden = !open;
      };
      toggle.addEventListener('click', () => setOpen(!shell.classList.contains('sidebar-open')));
      backdrop?.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
      // Close the drawer after tapping a nav link on mobile.
      shell.querySelectorAll('.dash-sidebar a').forEach((a) => {
        a.addEventListener('click', () => { if (window.innerWidth <= 900) setOpen(false); });
      });
    })();
    """


def dashboard_view_tabs_html(
    *, show_website: bool, show_campaigns: bool = True, show_gsc: bool = False,
    show_semrush: bool = False,
) -> str:
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
    gsc_tab = ""
    if show_gsc:
        gsc_tab = (
            '<button type="button" class="dash-view-btn" data-view="gsc" role="tab" '
            'aria-selected="false">Search Console</button>'
        )
    semrush_tab = ""
    if show_semrush:
        semrush_tab = (
            '<button type="button" class="dash-view-btn" data-view="semrush" role="tab" '
            'aria-selected="false">SEMrush</button>'
        )
    return f"""
      <nav class="dash-view-nav" role="tablist" aria-label="Dashboard views">
        <button type="button" class="dash-view-btn active" data-view="overview" role="tab" aria-selected="true">Overview</button>
        {campaigns_tab}
        {website_tab}
        {gsc_tab}
        {semrush_tab}
      </nav>"""


# Compact line icons for the sidebar view nav (stroke, inherits currentColor).
_VIEW_ICONS: dict[str, str] = {
    "overview": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    "campaigns": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="6" y1="20" x2="6" y2="13"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="18" y1="20" x2="18" y2="9"/></svg>',
    "gsc": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    "website": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/><path d="M12 3a14 14 0 010 18 14 14 0 010-18z"/></svg>',
    "semrush": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="15 7 21 7 21 13"/></svg>',
    "lead-tracking": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>',
    "event-tracking": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
}

_VIEW_LABELS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("campaigns", "Campaign Explorer"),
    ("website", "Website Analytics"),
    ("gsc", "Search Console"),
    ("semrush", "SEMrush"),
)


def sidebar_view_nav_html(
    *,
    show_website: bool,
    show_campaigns: bool = True,
    show_gsc: bool = False,
    show_semrush: bool = False,
    show_lead_tracking: bool = False,
    show_event_tracking: bool = False,
    as_links: bool = False,
    client_slug: str = "penn",
    access_key: str | None = None,
    use_session: bool = False,
    active_view: str | None = None,
) -> str:
    """Primary view navigation for the sidebar.

    ``as_links=False`` (dashboard page): JS-driven tab buttons that switch the
    in-page ``.view-panel`` sections. ``as_links=True`` (settings/files pages):
    anchor links back to ``/dashboard?view=…`` so the sidebar looks identical
    everywhere and one click returns to any dashboard view.
    """
    visible = {
        "overview": True,
        "campaigns": show_campaigns,
        "gsc": show_gsc,
        "website": show_website,
        "semrush": show_semrush,
    }
    base_url = _dashboard_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    )
    items: list[str] = []
    for view, label in _VIEW_LABELS:
        if not visible.get(view):
            continue
        icon = _VIEW_ICONS.get(view, "")
        inner = f'{icon}<span class="dash-view-label">{_esc(label)}</span>'
        if as_links:
            href = base_url
            if view != "overview":
                sep = "&" if "?" in href else "?"
                href = f"{href}{sep}view={view}"
            items.append(
                f'<a class="dash-view-btn" href="{_esc(href)}">{inner}</a>'
            )
        else:
            active = " active" if view == "overview" else ""
            selected = "true" if view == "overview" else "false"
            items.append(
                f'<button type="button" class="dash-view-btn{active}" data-view="{view}" '
                f'role="tab" aria-selected="{selected}">{inner}</button>'
            )
    # Lead Tracking is a standalone page, so it is ALWAYS an anchor link (even on
    # the dashboard where the other items are JS tabs) and appears last.
    if show_lead_tracking:
        lt_url = _lead_tracking_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        lt_active = " active" if active_view == "lead-tracking" else ""
        lt_icon = _VIEW_ICONS.get("lead-tracking", "")
        items.append(
            f'<a class="dash-view-btn{lt_active}" href="{_esc(lt_url)}">'
            f'{lt_icon}<span class="dash-view-label">Lead Tracking</span></a>'
        )
    # Event Tracking (GTM) is likewise a standalone page → always an anchor link.
    if show_event_tracking:
        et_url = _gtm_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        et_active = " active" if active_view == "event-tracking" else ""
        et_icon = _VIEW_ICONS.get("event-tracking", "")
        items.append(
            f'<a class="dash-view-btn{et_active}" href="{_esc(et_url)}">'
            f'{et_icon}<span class="dash-view-label">Event Tracking</span></a>'
        )
    role = "" if as_links else ' role="tablist"'
    return (
        f'<nav class="dash-sidebar-nav"{role} aria-label="Dashboard views">'
        f'{"".join(items)}</nav>'
    )


def nixon_sidebar_view_nav_html(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
    as_tabs: bool,
) -> str:
    """Canonical section nav for Nixon-style (bigquery_nixon) dashboards.

    Renders the SAME four sections on every page so the sidebar never changes
    shape between the dashboard, settings, connectors, and files pages. On the
    dashboard itself the four are JS tab buttons (as_tabs=True, driven by
    switchTab); on every other page they are links back to the dashboard
    (as_tabs=False). Lead / Event Tracking are standalone pages, so they are
    always links and appear only when their connector is connected.
    """
    pflags = platform_nav_flags(client_slug)
    dash_url = _dashboard_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    core = (
        ("overview", "Overview", _VIEW_ICONS["overview"]),
        ("explorer", "Explorer", _VIEW_ICONS["campaigns"]),
        ("analytics", "Website Analytics", _VIEW_ICONS["website"]),
        ("gsc", "Search Console", _VIEW_ICONS["gsc"]),
    )
    items: list[str] = []
    for i, (tab, label, icon) in enumerate(core):
        inner = f'{icon}<span>{_esc(label)}</span>'
        if as_tabs:
            active = " active" if i == 0 else ""
            items.append(
                f'<button type="button" class="dash-view-btn tab-btn{active}" data-tab="{tab}">{inner}</button>'
            )
        else:
            items.append(f'<a class="dash-view-btn" href="{_esc(dash_url)}">{inner}</a>')
    if pflags.get("show_lead_tracking"):
        lt = _lead_tracking_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" href="{_esc(lt)}">'
            f'{_VIEW_ICONS["lead-tracking"]}<span>Lead Tracking</span></a>'
        )
    if pflags.get("show_gtm"):
        et = _gtm_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" href="{_esc(et)}">'
            f'{_VIEW_ICONS["event-tracking"]}<span>Event Tracking</span></a>'
        )
    return f'<nav class="dash-sidebar-nav" aria-label="Sections">{"".join(items)}</nav>'


def platform_nav_flags(client_slug: str) -> dict[str, bool]:
    """Per-client platform nav visibility, derived from connector state.

    Replaces the hardcoded CONNECTORS_PILOT_SLUGS gating so the connectors +
    Lead Tracking platform scales to any client: an item appears once its source
    connector is connected ("show only what has data"). Onboarding a new client
    is then just connecting their sources — no code change.
    """
    try:
        import connector_config_store
        configs = connector_config_store.list_configs(client_slug)
    except Exception:
        return {"show_connectors": False, "show_lead_tracking": False, "show_gsc": False}
    status_by_type = {c.connector_type: c.status for c in configs}

    def _connected(ctype: str) -> bool:
        return status_by_type.get(ctype) in ("connected", "syncing")

    return {
        "show_connectors": bool(configs),
        "show_lead_tracking": _connected("hubspot"),
        "show_gsc": _connected("gsc"),
        "show_gtm": _connected("gtm"),
    }

_NAV_ICON_FILES = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>'
    '<path d="M14 2v6h6"/></svg>'
)
_NAV_ICON_CONNECTORS = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M18 3a3 3 0 00-3 3v1H9V6a3 3 0 10-3 3v1H3v2h3v1a3 3 0 103 3v-1h6v1a3 3 0 103-3v-1h3v-2h-3V9a3 3 0 000-6z"/>'
    '</svg>'
)
_NAV_ICON_LEADS = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>'
)
_NAV_ICON_SETTINGS = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z"/>'
    '<circle cx="12" cy="12" r="3"/></svg>'
)
_NAV_ICON_MENU = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" aria-hidden="true"><line x1="3" y1="6" x2="21" y2="6"/>'
    '<line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>'
)


def _sidebar_account_html(*, email: str | None, is_admin: bool) -> str:
    if not email:
        return ""
    admin_link = (
        '<a class="dash-sidebar-account-link" href="/admin">Admin</a>'
        '<span class="dash-sidebar-account-sep">·</span>'
        if is_admin
        else ""
    )
    return f"""
        <div class="dash-sidebar-account">
          <span class="dash-sidebar-account-email" title="{_esc(email)}">{_esc(email)}</span>
          <div class="dash-sidebar-account-actions">
            {admin_link}
            <form method="post" action="/logout" class="dash-sidebar-logout-form">
              <button type="submit" class="dash-sidebar-account-link">Sign out</button>
            </form>
          </div>
        </div>"""


def render_sidebar(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
    session_is_admin: bool,
    session_email: str | None,
    show_files: bool,
    show_connectors: bool = False,
    view_nav_html: str,
) -> str:
    """Navy drawer sidebar shared by the dashboard, settings, files, and connectors pages."""
    overview_url = _dashboard_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    settings_url = _settings_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    files_url = _files_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    connectors_url = _connectors_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"

    client_selector = topbar_client_selector_html(
        client_slug=client_slug,
        label=label,
        active_nav=active_nav,
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
    )

    files_btn = ""
    if show_files:
        files_active = " active" if active_nav in ("files", "insights-upload") else ""
        aria = ' aria-current="page"' if files_active else ""
        files_btn = (
            f'<a href="{_esc(files_url)}" class="dash-sidebar-link{files_active}"{aria}>'
            f'{_NAV_ICON_FILES}<span>Files</span></a>'
        )

    connectors_btn = ""
    if show_connectors or active_nav == "connectors":
        connectors_active = " active" if active_nav == "connectors" else ""
        connectors_aria = ' aria-current="page"' if connectors_active else ""
        connectors_btn = (
            f'<a href="{_esc(connectors_url)}" class="dash-sidebar-link{connectors_active}"{connectors_aria}>'
            f'{_NAV_ICON_CONNECTORS}<span>Connectors</span></a>'
        )

    settings_active = " active" if active_nav == "settings" else ""
    settings_aria = ' aria-current="page"' if settings_active else ""
    settings_btn = (
        f'<a href="{_esc(settings_url)}" class="dash-sidebar-link{settings_active}"{settings_aria}>'
        f'{_NAV_ICON_SETTINGS}<span>Settings</span></a>'
    )

    account_html = _sidebar_account_html(email=session_email, is_admin=session_is_admin)

    return f"""
    <button type="button" class="dash-sidebar-toggle" id="sidebarToggle"
      aria-label="Open navigation" aria-expanded="false" aria-controls="dashSidebar">{_NAV_ICON_MENU}</button>
    <div class="dash-sidebar-backdrop" id="sidebarBackdrop" hidden></div>
    <aside class="dash-sidebar" id="dashSidebar" aria-label="Primary navigation">
      <div class="dash-sidebar-head">
        <a href="{_esc(overview_url)}" class="dash-sidebar-logo" aria-label="Sagefrog home">
          <img class="dash-sidebar-logo-icon" src="/static/apple-touch-icon.png" alt="" width="36" height="36" />
          <span class="dash-sidebar-wordmark">Sagefrog</span>
        </a>
        <span class="dash-sidebar-beta">Beta</span>
      </div>
      {view_nav_html}
      <div class="dash-sidebar-footer">
        <div class="dash-sidebar-client">{client_selector}</div>
        <nav class="dash-sidebar-links" aria-label="Account navigation">
          {files_btn}
          {connectors_btn}
          {settings_btn}
        </nav>
        {account_html}
      </div>
    </aside>"""


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
    show_connectors: bool | None = None,
    show_campaigns: bool | None = None,
    show_website: bool | None = None,
    show_gsc: bool | None = None,
    show_semrush: bool | None = None,
) -> str:
    """Shared dashboard chrome for settings, files, and other child pages."""
    del page_subtitle, client_meta_tip, show_business_line
    theme = dashboard_theme.load_client_theme(client_slug)
    if show_files is None:
        import client_insight_documents as docs

        show_files = docs.enabled()

    # Resolve the sidebar flags the SAME way the dashboard does so the sidebar is
    # identical on every page for a given client (fixes items appearing/vanishing
    # between pages). Platform items come from connector state; view tabs from the
    # per-client feature config.
    pflags = platform_nav_flags(client_slug)
    if show_connectors is None:
        show_connectors = pflags["show_connectors"]
    if show_campaigns is None or show_website is None:
        try:
            import dashboard_features
            feats = dashboard_features.resolve_features(client_slug)
        except Exception:
            feats = None
        if show_campaigns is None:
            show_campaigns = bool(getattr(feats, "campaign_explorer", True)) if feats else True
        if show_website is None:
            show_website = bool(getattr(feats, "website_analytics", True)) if feats else True
    if show_gsc is None:
        show_gsc = pflags["show_gsc"]
    if show_semrush is None:
        show_semrush = False

    # Nixon-style (bigquery_nixon) clients use one canonical section nav on every
    # child page so the sidebar is identical to their dashboard's (same four
    # sections + Connectors always reachable), rather than the feature-flag-driven
    # nav which would show a different item set per page.
    try:
        import client_dashboard_config as _cdc
        _row = _cdc.get_config(client_slug)
        _is_nixon_mode = bool(_row and _row.dashboard_mode == "bigquery_nixon")
    except Exception:
        _is_nixon_mode = False

    if _is_nixon_mode:
        show_connectors = True
        view_nav_html = nixon_sidebar_view_nav_html(
            client_slug=client_slug,
            access_key=access_key,
            use_session=use_session,
            as_tabs=False,
        )
    else:
        view_nav_html = sidebar_view_nav_html(
            show_website=show_website,
            show_campaigns=show_campaigns,
            show_gsc=show_gsc,
            show_semrush=show_semrush,
            show_lead_tracking=pflags["show_lead_tracking"],
            show_event_tracking=pflags["show_gtm"],
            as_links=True,
            client_slug=client_slug,
            access_key=access_key,
            use_session=use_session,
            active_view=active_nav,
        )
    sidebar = render_sidebar(
        client_slug=client_slug,
        label=label,
        active_nav=active_nav,
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=show_files,
        show_connectors=show_connectors,
        view_nav_html=view_nav_html,
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
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; -webkit-font-smoothing: antialiased; }}
    .app-shell {{ display: flex; flex-direction: row; min-height: 100vh; }}
    .dash-main {{ flex: 1; min-width: 0; display: flex; flex-direction: column; width: 100%; }}
    .dash-content {{ flex: 1; width: 100%; padding: 28px 32px 48px; }}
    .wrap {{ width: 100%; max-width: none; min-width: 0; }}
    {SIDEBAR_CSS}
    {extra_css}
  </style>
</head>
<body>
  <div class="app-shell">
    {sidebar}
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


SIDEBAR_CSS = """
    /* ====== Navy drawer sidebar ====== */
    .dash-sidebar {
      flex-shrink: 0;
      width: 252px;
      align-self: flex-start;
      position: sticky;
      top: 0;
      height: 100vh;
      display: flex;
      flex-direction: column;
      background: linear-gradient(180deg, var(--sidebar-from), var(--sidebar-to));
      color: #e6edf6;
      z-index: 90;
      overflow: hidden;
    }
    .dash-sidebar-head {
      display: flex;
      align-items: center;
      gap: 10px;
      /* Left padding pins the logo icon to the same 25px rail as the section-nav
         and footer icons below it, so the Sagefrog mark lines up with the nav
         instead of sitting a few px to its left. */
      padding: 20px 20px 16px 25px;
      flex-shrink: 0;
    }
    .dash-sidebar-logo {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      min-width: 0;
    }
    .dash-sidebar-logo-icon {
      width: 34px;
      height: 34px;
      border-radius: 9px;
      flex-shrink: 0;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
    }
    .dash-sidebar-wordmark {
      font-size: 1.18rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #fff;
      white-space: nowrap;
    }
    .dash-sidebar-beta {
      display: inline-flex;
      align-items: center;
      padding: 3px 9px;
      border-radius: 7px;
      background: rgba(255, 255, 255, 0.14);
      color: #ffd9a8;
      font-size: 0.66rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      line-height: 1;
    }

    /* Primary view nav */
    .dash-sidebar-nav {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: 8px 12px;
      flex: 1 1 auto;
      overflow-y: auto;
      min-height: 0;
    }
    .dash-sidebar-nav .dash-view-btn {
      display: flex;
      align-items: center;
      gap: 12px;
      width: 100%;
      padding: 10px 13px;
      border: 0;
      border-radius: 9px;
      background: transparent;
      color: #c3d2e6;
      /* font-family + line-height are pinned (not inherited) so a <button> item
         (dashboard tabs) and an <a> item (settings/connectors/files) render
         identically, regardless of the host page's body line-height. Without
         these, buttons fell back to the UA font ("thicker") and shell pages
         with body line-height:1.5 made the rows taller. */
      font-family: inherit;
      font-size: 0.92rem;
      font-weight: 600;
      line-height: 1.2;
      text-align: left;
      text-decoration: none;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .dash-sidebar-nav .dash-view-btn svg {
      width: 19px;
      height: 19px;
      flex-shrink: 0;
      opacity: 0.85;
    }
    .dash-sidebar-nav .dash-view-btn:hover {
      background: rgba(255, 255, 255, 0.08);
      color: #fff;
    }
    .dash-sidebar-nav .dash-view-btn.active {
      background: rgba(255, 255, 255, 0.15);
      color: #fff;
      box-shadow: inset 3px 0 0 #7dd3fc;
    }
    .dash-sidebar-nav .dash-view-btn.active svg { opacity: 1; }
    .dash-sidebar-nav .dash-view-btn:focus-visible {
      outline: 2px solid #7dd3fc;
      outline-offset: -2px;
    }

    /* Footer: client selector + Files / Settings + account */
    .dash-sidebar-footer {
      margin-top: auto;
      flex-shrink: 0;
      /* 13px left so the footer items' icons land on the same 25px rail as the
         section nav (13 + the link's 12px inner padding = 25). */
      padding: 14px 13px 16px;
      border-top: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(0, 0, 0, 0.12);
    }
    .dash-sidebar-client { margin-bottom: 10px; }
    .dash-sidebar-client .topbar-client-switcher,
    .dash-sidebar-client .topbar-client-label {
      display: block;
      width: 100%;
      max-width: none;
      min-width: 0;
      box-sizing: border-box;
      border-radius: 9px;
      border: 1px solid rgba(255, 255, 255, 0.18);
      background-color: rgba(255, 255, 255, 0.1);
      color: #fff;
      font: inherit;
      font-size: 0.9rem;
      font-weight: 650;
      padding: 9px 34px 9px 12px;
    }
    .dash-sidebar-client .topbar-client-switcher {
      appearance: none;
      -webkit-appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23ffffff' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 13px center;
      cursor: pointer;
    }
    .dash-sidebar-client .topbar-client-label { padding-right: 13px; }
    .dash-sidebar-client .topbar-client-switcher:hover,
    .dash-sidebar-client .topbar-client-switcher:focus-visible {
      border-color: rgba(255, 255, 255, 0.4);
      background-color: rgba(255, 255, 255, 0.16);
      outline: none;
    }
    .dash-sidebar-client .topbar-client-switcher option { color: #0f172a; }
    .dash-sidebar .sr-only {
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
    .dash-sidebar-links { display: flex; flex-direction: column; gap: 3px; }
    .dash-sidebar-link {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 12px;
      border-radius: 9px;
      color: #c3d2e6;
      font-family: inherit;
      font-size: 0.9rem;
      font-weight: 600;
      line-height: 1.2;
      text-decoration: none;
      transition: background 0.15s, color 0.15s;
    }
    .dash-sidebar-link svg { width: 18px; height: 18px; flex-shrink: 0; opacity: 0.85; }
    .dash-sidebar-link:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
    .dash-sidebar-link.active { background: rgba(255, 255, 255, 0.15); color: #fff; }
    .dash-sidebar-account {
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid rgba(255, 255, 255, 0.12);
    }
    .dash-sidebar-account-email {
      display: block;
      font-size: 0.76rem;
      color: #9fb3cc;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .dash-sidebar-account-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-top: 3px;
      font-size: 0.8rem;
    }
    .dash-sidebar-account-link {
      appearance: none;
      border: 0;
      background: none;
      padding: 0;
      font: inherit;
      color: #9ecbf5;
      text-decoration: none;
      cursor: pointer;
    }
    .dash-sidebar-account-link:hover { text-decoration: underline; color: #cfe5fb; }
    .dash-sidebar-account-sep { color: #64768f; }
    .dash-sidebar-logout-form { display: inline; margin: 0; }

    /* Mobile hamburger + backdrop (hidden on desktop) */
    .dash-sidebar-toggle {
      display: none;
      position: fixed;
      top: 12px;
      left: 12px;
      z-index: 95;
      width: 42px;
      height: 42px;
      align-items: center;
      justify-content: center;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--navy);
      cursor: pointer;
      box-shadow: var(--shadow-sm);
    }
    .dash-sidebar-toggle svg { width: 20px; height: 20px; }
    .dash-sidebar-backdrop { display: none; }

    @media (max-width: 900px) {
      .dash-sidebar {
        position: fixed;
        top: 0;
        left: 0;
        height: 100vh;
        width: 264px;
        transform: translateX(-100%);
        transition: transform 0.25s ease;
        box-shadow: 4px 0 24px rgba(0, 0, 0, 0.28);
      }
      .app-shell.sidebar-open .dash-sidebar { transform: translateX(0); }
      .app-shell.sidebar-open .dash-sidebar-backdrop {
        display: block;
        position: fixed;
        inset: 0;
        z-index: 88;
        background: rgba(8, 18, 33, 0.5);
      }
      .dash-sidebar-toggle { display: inline-flex; }
      .dash-main { padding-top: 60px; }
    }
"""


