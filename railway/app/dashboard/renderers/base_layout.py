"""Shared dashboard chrome: favicon, topbar, shell layout."""

from __future__ import annotations

from datetime import date

import dashboard_theme

from dashboard.utils.formatting import esc as _esc
from dashboard.utils.urls import (
    client_switch_target_url as _client_switch_target_url,
    connectors_page_url as _connectors_page_url,
    consent_page_url as _consent_page_url,
    dashboard_page_url as _dashboard_page_url,
    email_performance_page_url as _email_performance_page_url,
    files_page_url as _files_page_url,
    gtm_page_url as _gtm_page_url,
    lead_tracking_page_url as _lead_tracking_page_url,
    linkedin_organic_page_url as _linkedin_organic_page_url,
    settings_page_url as _settings_page_url,
)

def favicon_head_html() -> str:
    return """
  <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
  <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
  <link rel="manifest" href="/static/site.webmanifest">
  <meta name="theme-color" content="#0a2540">"""


def site_footer_html() -> str:
    """Low-profile copyright line closing out every in-app page.

    Sits at the foot of the main column (after the page content, inside
    ``.dash-main``) on the dashboard and every child page. Styling lives in
    ``SITE_FOOTER_CSS``, which rides along with ``SIDEBAR_CSS`` so any page that
    already pulls in the shared chrome gets it for free. The year is the current
    one so the line never goes stale.
    """
    return (
        '<footer class="site-footer">'
        f"<span>&copy; {date.today().year} Sagefrog Marketing Group. "
        "All rights reserved.</span>"
        "</footer>"
    )


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


def topbar_client_selector_html(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
    session_is_admin: bool,
    session_can_switch_clients: bool = False,
) -> str:
    # Admins and `standard` (agency-wide) users can switch between all client
    # dashboards; `client`-role users stay pinned to their own (plain label).
    if (session_is_admin or session_can_switch_clients) and use_session:
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


# A small, stable palette for client avatar chips. Each client gets a colour
# derived from its slug so the same client always reads the same — a quiet way
# to make a long list scannable (à la Slack/Linear workspace avatars).
_CLIENT_AVATAR_COLORS = (
    "#0ea5e9", "#6366f1", "#8b5cf6", "#d946ef", "#ec4899",
    "#f43f5e", "#f59e0b", "#10b981", "#14b8a6", "#3b82f6",
)


def _client_avatar_color(slug: str) -> str:
    key = (slug or "").strip().lower() or "?"
    return _CLIENT_AVATAR_COLORS[sum(ord(c) for c in key) % len(_CLIENT_AVATAR_COLORS)]


def _client_avatar_initial(label: str) -> str:
    return _esc((label or "?").strip()[:1].upper() or "?")


def _client_avatar_html(
    *, slug: str, label: str, logo: str | None, extra_cls: str = ""
) -> str:
    """Avatar chip for a client: the admin-uploaded logo when one exists, else a
    colour-coded initial (colour derived from the slug so it stays stable). Same
    logo/initials fallback the admin dashboards table uses."""
    cls = "client-switch-ava" + (f" {extra_cls}" if extra_cls else "")
    if logo:
        return (
            f'<span class="{cls} client-switch-ava--img" aria-hidden="true">'
            f'<img src="{_esc(str(logo))}" alt="" loading="lazy"></span>'
        )
    return (
        f'<span class="{cls}" style="background:{_client_avatar_color(slug)}" '
        f'aria-hidden="true">{_client_avatar_initial(label)}</span>'
    )


def _parent_avatar_html(*, extra_cls: str = "") -> str:
    """Avatar chip for the 'Admin panel' parent row: a navy tile with a layered
    shield mark so the agency parent reads distinctly from the client avatars."""
    cls = "client-switch-ava client-switch-ava--parent" + (f" {extra_cls}" if extra_cls else "")
    icon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/>'
        '<path d="M9.5 12l1.8 1.8 3.4-3.6"/></svg>'
    )
    return f'<span class="{cls}" aria-hidden="true">{icon}</span>'


def sidebar_client_switcher_html(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
    session_is_admin: bool,
    session_can_switch_clients: bool = False,
    admin_context: bool = False,
) -> str:
    """Modern client switcher for the sidebar footer.

    Renders a trigger row (current client's avatar + name) that opens a
    slide-out secondary drawer with a search box and a scrollable, keyboard-
    navigable client list. Replaces the native ``<select>`` whose OS dropdown
    looked dated and didn't scale past a handful of clients. ``client``-role
    users who can't switch keep the plain label. The drawer + backdrop markup is
    relocated to ``<body>`` on init (see ``dashboard_topbar_js``) so it escapes
    the sidebar's ``overflow:hidden`` / mobile ``transform`` and overlays the
    whole viewport.

    Admins see an "Admin panel" parent row pinned at the top of the drawer (an
    "Agency" group above the "Clients" group), so the whole portal reads as
    Admin → clients rather than admin being a separate section. ``admin_context``
    marks that we're currently *in* the admin environment: the trigger then shows
    "Admin panel" as the current selection and the parent row is checked.
    """
    if not ((session_is_admin or session_can_switch_clients) and use_session):
        return f'<span class="topbar-client-label" id="topbarClientLabel">{_esc(label)}</span>'

    import client_config

    # Scope to new-build (connector platform) clients, mirroring the old select;
    # legacy dashboards stay reachable by URL but drop out of the list. Always
    # keep the current client so visiting a legacy page directly still works.
    try:
        import connector_config_store
        _platform_slugs = connector_config_store.client_slugs_with_configs()
    except Exception:
        _platform_slugs = set()

    # Pull admin-uploaded client logos (data URIs on the dashboard registry) so
    # the switcher shows real brand marks, falling back to a colour-coded initial
    # for clients without one. Registry may be disabled → plain initials.
    logos: dict[str, str] = {}
    try:
        import dashboard_registry
        if dashboard_registry.enabled():
            logos = {
                row.client_slug: row.logo
                for row in dashboard_registry.list_clients()
                if getattr(row, "logo", None)
            }
    except Exception:
        logos = {}

    current = (client_slug or "").strip().lower()
    current_label = label
    items: list[str] = []
    for slug, client_label in client_config.list_dashboard_clients():
        if _platform_slugs and slug not in _platform_slugs and slug != current:
            continue
        # In the admin environment no client is "current" — the parent row is.
        is_current = (not admin_context) and slug == current
        if is_current:
            current_label = client_label
        dest = _client_switch_target_url(
            client_slug=slug,
            active_nav=active_nav,
            access_key=access_key,
            use_session=use_session,
        )
        cur_cls = " is-current" if is_current else ""
        aria_cur = ' aria-current="true"' if is_current else ""
        avatar = _client_avatar_html(
            slug=slug, label=client_label, logo=logos.get(slug)
        )
        items.append(
            f'<a class="client-switch-item{cur_cls}" role="option"{aria_cur} '
            f'href="{_esc(dest)}" data-name="{_esc(client_label)}">'
            f'{avatar}'
            f'<span class="client-switch-name">{_esc(client_label)}</span>'
            f'<svg class="client-switch-check" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>'
            f'</a>'
        )

    count = len(items)

    # Admins get an "Admin panel" parent row pinned above the client list. It
    # always carries data-name="Admin panel" so the search box matches it too, and
    # it's the current selection when we're inside the admin environment.
    admin_block = ""
    if session_is_admin:
        admin_cur_cls = " is-current" if admin_context else ""
        admin_aria = ' aria-current="true"' if admin_context else ""
        admin_block = (
            '<div class="client-switch-group">Agency</div>'
            f'<a class="client-switch-item client-switch-item--parent{admin_cur_cls}" '
            f'role="option"{admin_aria} href="/admin" data-name="Admin panel">'
            f'{_parent_avatar_html()}'
            f'<span class="client-switch-name">Admin panel</span>'
            f'<svg class="client-switch-check" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>'
            f'</a>'
            '<div class="client-switch-group">Clients</div>'
        )

    if admin_context:
        trigger_avatar = _parent_avatar_html(extra_cls="client-switch-ava--trigger")
        trigger_eyebrow = "Agency"
        trigger_name = "Admin panel"
    else:
        trigger_avatar = _client_avatar_html(
            slug=current, label=current_label, logo=logos.get(current),
            extra_cls="client-switch-ava--trigger",
        )
        trigger_eyebrow = "Client"
        trigger_name = current_label
    icon_search = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
    )
    icon_close = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
    )
    icon_chevrons = (
        '<svg class="client-switch-caret" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true"><polyline points="8 9 12 5 16 9"/>'
        '<polyline points="16 15 12 19 8 15"/></svg>'
    )

    return f"""
      <button type="button" class="client-switch-trigger" id="clientSwitchTrigger"
        aria-haspopup="dialog" aria-expanded="false" aria-controls="clientSwitchDrawer">
        {trigger_avatar}
        <span class="client-switch-trigger-text">
          <span class="client-switch-eyebrow">{_esc(trigger_eyebrow)}</span>
          <span class="client-switch-trigger-name">{_esc(trigger_name)}</span>
        </span>
        {icon_chevrons}
      </button>
      <div class="client-switch-backdrop" id="clientSwitchBackdrop" hidden></div>
      <aside class="client-switch-drawer" id="clientSwitchDrawer" role="dialog"
        aria-modal="true" aria-label="Switch workspace" aria-hidden="true">
        <header class="client-switch-head">
          <div class="client-switch-head-titles">
            <h2 class="client-switch-title">Switch workspace</h2>
            <span class="client-switch-count">{count} {"client" if count == 1 else "clients"}</span>
          </div>
          <button type="button" class="client-switch-close" aria-label="Close">{icon_close}</button>
        </header>
        <div class="client-switch-search">
          {icon_search}
          <input type="text" class="client-switch-search-input" placeholder="Search clients…"
            aria-label="Search clients" autocomplete="off" spellcheck="false" />
        </div>
        <div class="client-switch-list" role="listbox" aria-label="Workspaces" tabindex="-1">
          {admin_block}
          {"".join(items)}
          <p class="client-switch-empty" hidden>No clients match your search.</p>
        </div>
      </aside>"""


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
    session_can_switch_clients: bool = False,
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
        session_can_switch_clients=session_can_switch_clients,
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
      let url = e.target.value;
      if (url) {
        // Carry the current dashboard tab (?view=) to the client we're switching
        // to, so leaving Client A on AI Traffic lands on Client B's AI Traffic
        // instead of snapping back to Overview. The dashboard reads ?view= on
        // load and falls back to Overview if that page is hidden/absent, so this
        // is safe even when the target client doesn't have the same tab. On
        // Settings/Files/etc. there is no ?view= in the URL, so this is a no-op.
        try {
          const view = new URLSearchParams(location.search).get('view');
          if (view) {
            const u = new URL(url, location.origin);
            if (!u.searchParams.has('view')) u.searchParams.set('view', view);
            url = u.pathname + u.search + u.hash;
          }
        } catch (err) { /* ignore */ }
        window.location.href = url;
      }
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

    // ── Desktop sidebar collapse/expand ──────────────────────────────────
    // A discreet chevron at the bottom of the sidebar tucks the rail away; a
    // small floating button brings it back. The state is remembered per browser,
    // and an inline script in the sidebar markup applies it before paint (no flash).
    (function() {
      const shell = document.querySelector('.app-shell');
      const collapseBtn = document.getElementById('sidebarCollapse');
      const reopenBtn = document.getElementById('sidebarReopen');
      if (!shell || !collapseBtn || !reopenBtn) return;
      const KEY = 'sf_sidebar_collapsed';
      function setCollapsed(collapsed) {
        shell.classList.toggle('sidebar-collapsed', collapsed);
        collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        try { localStorage.setItem(KEY, collapsed ? '1' : '0'); } catch (e) {}
      }
      // Reconcile the button state with whatever the pre-paint script applied.
      collapseBtn.setAttribute(
        'aria-expanded', shell.classList.contains('sidebar-collapsed') ? 'false' : 'true'
      );
      collapseBtn.addEventListener('click', () => setCollapsed(true));
      reopenBtn.addEventListener('click', () => {
        setCollapsed(false);
        // Return focus to the collapse control so keyboard users aren't stranded.
        requestAnimationFrame(() => collapseBtn.focus());
      });
    })();

    // ── Modern client switcher: slide-out drawer with search ──────────────
    (function() {
      const trigger = document.getElementById('clientSwitchTrigger');
      const drawer = document.getElementById('clientSwitchDrawer');
      const backdrop = document.getElementById('clientSwitchBackdrop');
      if (!trigger || !drawer || !backdrop) return;
      // Relocate the overlay to <body> so it escapes the sidebar's
      // overflow:hidden and the mobile translateX() (a transformed ancestor
      // would otherwise clip / re-anchor these position:fixed nodes).
      document.body.appendChild(backdrop);
      document.body.appendChild(drawer);

      const search = drawer.querySelector('.client-switch-search-input');
      const list = drawer.querySelector('.client-switch-list');
      const empty = drawer.querySelector('.client-switch-empty');
      const closeBtn = drawer.querySelector('.client-switch-close');
      const items = Array.prototype.slice.call(
        drawer.querySelectorAll('.client-switch-item')
      );
      let lastFocus = null;

      // Carry the current dashboard tab (?view=) to the client we switch to, so
      // leaving Client A on AI Traffic lands on Client B's AI Traffic instead of
      // snapping back to Overview. Mirrors the legacy <select> handler.
      function carryView(url) {
        try {
          const view = new URLSearchParams(location.search).get('view');
          if (view) {
            const u = new URL(url, location.origin);
            if (!u.searchParams.has('view')) u.searchParams.set('view', view);
            return u.pathname + u.search + u.hash;
          }
        } catch (e) { /* ignore */ }
        return url;
      }
      items.forEach((a) => {
        a.addEventListener('click', (e) => {
          e.preventDefault();
          window.location.href = carryView(a.getAttribute('href'));
        });
      });

      function isOpen() { return document.body.classList.contains('client-switch-active'); }

      const groups = Array.prototype.slice.call(
        drawer.querySelectorAll('.client-switch-group')
      );
      function filter(q) {
        q = (q || '').trim().toLowerCase();
        let shown = 0;
        items.forEach((a) => {
          const match = !q || (a.dataset.name || '').toLowerCase().indexOf(q) !== -1;
          a.hidden = !match;
          if (match) shown += 1;
        });
        // Hide a group heading (Agency / Clients) when everything under it is
        // filtered out, so search never leaves a lone label with no rows.
        groups.forEach((g) => {
          let hasVisible = false;
          let n = g.nextElementSibling;
          while (n && !n.classList.contains('client-switch-group')) {
            if (n.classList.contains('client-switch-item') && !n.hidden) { hasVisible = true; break; }
            n = n.nextElementSibling;
          }
          g.hidden = !hasVisible;
        });
        if (empty) empty.hidden = shown > 0;
      }

      function open() {
        lastFocus = document.activeElement;
        backdrop.hidden = false;
        drawer.setAttribute('aria-hidden', 'false');
        trigger.setAttribute('aria-expanded', 'true');
        // Force reflow so the enter transition runs from the off-canvas state.
        void drawer.offsetWidth;
        document.body.classList.add('client-switch-active');
        if (search) { search.value = ''; }
        filter('');
        requestAnimationFrame(() => { if (search) search.focus(); });
        const cur = drawer.querySelector('.client-switch-item.is-current');
        if (cur) cur.scrollIntoView({ block: 'center' });
      }

      function close() {
        document.body.classList.remove('client-switch-active');
        drawer.setAttribute('aria-hidden', 'true');
        trigger.setAttribute('aria-expanded', 'false');
        const onEnd = () => {
          if (!isOpen()) backdrop.hidden = true;
          drawer.removeEventListener('transitionend', onEnd);
        };
        drawer.addEventListener('transitionend', onEnd);
        if (lastFocus && lastFocus.focus) lastFocus.focus();
      }

      trigger.addEventListener('click', () => { isOpen() ? close() : open(); });
      backdrop.addEventListener('click', close);
      if (closeBtn) closeBtn.addEventListener('click', close);
      if (search) search.addEventListener('input', () => filter(search.value));

      // Keyboard: Esc closes; Up/Down roves through the visible rows.
      drawer.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.preventDefault(); close(); return; }
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          const visible = items.filter((a) => !a.hidden);
          if (!visible.length) return;
          e.preventDefault();
          let idx = visible.indexOf(document.activeElement);
          if (e.key === 'ArrowDown') idx = idx < 0 ? 0 : (idx + 1) % visible.length;
          else idx = idx <= 0 ? visible.length - 1 : idx - 1;
          visible[idx].focus();
          return;
        }
        // Focus trap.
        if (e.key === 'Tab') {
          const focusables = [search].concat(items.filter((a) => !a.hidden));
          if (closeBtn) focusables.push(closeBtn);
          const f = focusables.filter(Boolean);
          if (!f.length) return;
          const first = f[0], last = f[f.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      });
    })();

    // ── Account profile dropdown (sidebar footer) ─────────────────────────
    // Toggles the Sign out popover; closes on outside click or Esc.
    (function() {
      const wrap = document.querySelector('.dash-profile');
      const trigger = document.getElementById('dashProfileTrigger');
      const menu = document.getElementById('dashProfileMenu');
      if (!wrap || !trigger || !menu) return;
      function setOpen(open) {
        wrap.classList.toggle('is-open', open);
        trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        menu.hidden = !open;
      }
      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        setOpen(menu.hidden);
      });
      document.addEventListener('click', (e) => {
        if (!wrap.contains(e.target)) setOpen(false);
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !menu.hidden) { setOpen(false); trigger.focus(); }
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
    "ai-traffic": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/><path d="M19 14v3M17.5 15.5h3"/></svg>',
    "lead-tracking": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>',
    "email-performance": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
    "linkedin-organic": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h16v16H4z"/><path d="M8 11v5"/><path d="M8 8v.01"/><path d="M12 16v-3a2 2 0 014 0v3"/><path d="M12 16v-5"/></svg>',
    "event-tracking": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
    "site-performance": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20a8 8 0 10-8-8"/><path d="M4 12a8 8 0 018-8"/><line x1="12" y1="12" x2="16" y2="9"/><circle cx="12" cy="12" r="1.6"/></svg>',
    "consent": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/></svg>',
}

# Sidebar section tabs that expose an admin "⋮" kebab menu for editing that
# tab in place. Both Overview and Campaign Explorer edit their panel layout the
# same way (hide / show / drag to reorder). The menu content per tab is built by
# ``_edit_menu_items_html``; adding a tab here plus a menu-items branch (and its
# page JS action) lights up the same kebab on that tab.
_EDIT_MENU_TABS: frozenset[str] = frozenset({"overview", "explorer"})

# Kebab (vertical dots) shown on hover of an editable nav item, and the pencil
# used by its "Edit layout" menu entry.
_NAV_ICON_KEBAB = (
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" '
    'aria-hidden="true"><circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/>'
    '<circle cx="12" cy="19" r="1.7"/></svg>'
)
_NAV_ICON_EDIT_LAYOUT = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>'
)


def _edit_menu_items_html(tab: str) -> str:
    """The kebab menu's item(s) for an editable sidebar tab.

    Both editable tabs get the same "Edit layout" entry, which drops the admin
    into that tab's panel edit mode (hide / show / drag to reorder). The action
    is wired by the dashboard page JS, which reads the tab off the nav item.
    Returns '' for tabs with no menu."""
    if tab in _EDIT_MENU_TABS:
        return (
            '<button type="button" class="dash-view-menu-item" role="menuitem" '
            f'data-action="edit-layout">{_NAV_ICON_EDIT_LAYOUT}'
            '<span>Edit layout</span></button>'
        )
    return ""


def dashboard_sidebar_view_nav_html(
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
    # Admin-hidden core tabs (server-side, per client). We render these already
    # hidden (no client-side flash) so a tab an admin turns off stays off for
    # every user of this client's portal in every browser.
    hidden_tabs = set(_sidebar_hidden_tabs(client_slug))
    dash_url = _dashboard_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    core = [
        ("overview", "Overview", _VIEW_ICONS["overview"]),
        ("explorer", "Campaign Explorer", _VIEW_ICONS["campaigns"]),
        ("analytics", "Website Analytics", _VIEW_ICONS["website"]),
        ("ai_traffic", "AI Traffic", _VIEW_ICONS["ai-traffic"]),
    ]
    # Search Console is connector-gated like Site Performance below: a client
    # without the GSC connector has no BQ search mart, so the tab only loaded a
    # table-not-found error (e.g. the GA4-only clients). Fail OPEN when the
    # connector read itself failed, so a transient Postgres blip can't blank a
    # core tab for a client that does have GSC.
    if pflags.get("show_gsc") or not pflags.get("_connector_read_ok", True):
        core.append(("gsc", "Search Console", _VIEW_ICONS["gsc"]))
    # Site Performance (PageSpeed Insights) is a same-page tab like Search Console,
    # gated on the pagespeed connector so it only appears once a client has it.
    if pflags.get("show_pagespeed"):
        core.append(
            ("site_performance", "Site Performance", _VIEW_ICONS["site-performance"])
        )
    items: list[str] = []
    for i, (tab, label, icon) in enumerate(core):
        inner = f'{icon}<span>{_esc(label)}</span>'
        hide_attr = ' style="display:none" data-hidden="1"' if tab in hidden_tabs else ""
        if as_tabs:
            active = " active" if i == 0 else ""
            btn = (
                f'<button type="button" class="dash-view-btn tab-btn{active}" '
                f'data-tab="{tab}">{inner}</button>'
            )
            if tab in _EDIT_MENU_TABS:
                # Editable tab: wrap the button with a hover-revealed kebab (⋮)
                # whose menu drops the admin into that tab's edit mode. The menu
                # is admin-only via CSS; open/close + actions are wired by the
                # dashboard page JS. The hide attr moves to the wrapper so hiding
                # the tab hides the whole row (kebab included).
                items.append(
                    f'<div class="dash-view-item" data-view-item="{tab}"{hide_attr}>'
                    f'{btn}'
                    f'<button type="button" class="dash-view-kebab" data-edit-tab="{tab}" '
                    f'aria-haspopup="true" aria-expanded="false" '
                    f'aria-label="{_esc(label)} options" title="{_esc(label)} options">'
                    f'{_NAV_ICON_KEBAB}</button>'
                    f'<div class="dash-view-menu" role="menu" hidden>'
                    f'{_edit_menu_items_html(tab)}'
                    f'</div></div>'
                )
            else:
                items.append(
                    f'<button type="button" class="dash-view-btn tab-btn{active}" '
                    f'data-tab="{tab}"{hide_attr}>{inner}</button>'
                )
        else:
            # Deep-link to the specific dashboard tab (?view=…) so clicking e.g.
            # "Search Console" from Settings/Files/Connectors lands on that tab,
            # not always Overview. The dashboard reads ?view= on load.
            href = dash_url if tab == "overview" else (
                f"{dash_url}{'&' if '?' in dash_url else '?'}view={tab}"
            )
            items.append(f'<a class="dash-view-btn" data-tab="{tab}" href="{_esc(href)}"{hide_attr}>{inner}</a>')
    # The standalone connector pages carry a stable data-tab so an admin can
    # hide them from Advanced too (same server-side hidden set as the core tabs);
    # a hidden one renders already display:none. Consent Health is intentionally
    # not in the hidden set — it has its own opt-in visibility control.
    def _tab_hide(tab_key: str) -> str:
        return ' style="display:none" data-hidden="1"' if tab_key in hidden_tabs else ""

    if pflags.get("show_lead_tracking"):
        lt = _lead_tracking_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" data-tab="lead_tracking" href="{_esc(lt)}"{_tab_hide("lead_tracking")}>'
            f'{_VIEW_ICONS["lead-tracking"]}<span>Lead Tracking</span></a>'
        )
    if pflags.get("show_email_performance"):
        ep = _email_performance_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" data-tab="email_performance" href="{_esc(ep)}"{_tab_hide("email_performance")}>'
            f'{_VIEW_ICONS["email-performance"]}<span>Email Performance</span></a>'
        )
    if pflags.get("show_linkedin_organic"):
        lo = _linkedin_organic_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" data-tab="linkedin_organic" href="{_esc(lo)}"{_tab_hide("linkedin_organic")}>'
            f'{_VIEW_ICONS["linkedin-organic"]}<span>LinkedIn Organic</span></a>'
        )
    if pflags.get("show_gtm"):
        et = _gtm_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" data-tab="event_tracking" href="{_esc(et)}"{_tab_hide("event_tracking")}>'
            f'{_VIEW_ICONS["event-tracking"]}<span>Event Tracking</span></a>'
        )
    if pflags.get("show_consent"):
        cu = _consent_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" href="{_esc(cu)}">'
            f'{_VIEW_ICONS["consent"]}<span>Consent Health</span></a>'
        )
    # Tabs an admin hid are already rendered hidden server-side (per client, for
    # every user and browser). We publish the hidden set as a global so the
    # dashboard's deep-link init can fall back off a hidden active tab. Replaces
    # the old per-browser localStorage 'nixon_sidebar_pages:<slug>' prefs, which
    # only hid tabs in the one browser that toggled them. Consent Health has no
    # data-tab (its visibility is its own opt-in, not part of this hidden set).
    import json as _json
    prefs_script = (
        "<script>window.__sfHiddenTabs="
        f"{_json.dumps(sorted(hidden_tabs))};</script>"
    )
    return (
        f'<nav class="dash-sidebar-nav" aria-label="Sections">{"".join(items)}</nav>'
        + prefs_script
    )


def platform_nav_flags(client_slug: str) -> dict[str, bool]:
    """Per-client platform nav visibility, derived from connector state.

    Replaces the hardcoded CONNECTORS_PILOT_SLUGS gating so the connectors +
    Lead Tracking platform scales to any client: an item appears once its source
    connector is connected ("show only what has data"). Onboarding a new client
    is then just connecting their sources — no code change.
    """
    # Read the connector state, retrying once on error. The read can throw on a
    # transient Postgres hiccup (e.g. the DeadlockDetected the deploy notes call
    # out under load); a heavy page like Overview fires many concurrent queries
    # and is the most likely to hit one. Without the retry that single blip would
    # blank Lead Tracking / Email Performance from the sidebar on that page load
    # while a lighter page (the Email Performance page itself) still showed them —
    # exactly the "they disappear when I click Overview" symptom.
    configs = None
    for _attempt in range(2):
        try:
            import connector_config_store
            configs = connector_config_store.list_configs(client_slug)
            break
        except Exception:
            configs = None
    if configs is None:
        # Still couldn't read connector state. Return a COMPLETE flag set (every
        # key present, all False) rather than a partial dict, so a caller's
        # ``.get()`` is consistent and no item is dropped by a missing key.
        # ``_connector_read_ok`` marks this as "couldn't tell", distinct from a
        # successful read that found nothing connected — callers gating a CORE
        # tab (one every client normally has) use it to fail open rather than
        # blank the tab on a transient blip.
        return {
            "_connector_read_ok": False,
            "show_connectors": False,
            "show_lead_tracking": False,
            "show_email_performance": False,
            "show_linkedin_organic": False,
            "show_gsc": False,
            "show_gtm": False,
            "show_pagespeed": False,
            "show_semrush": False,
            "show_consent": False,
        }
    status_by_type = {c.connector_type: c.status for c in configs}

    def _connected(ctype: str) -> bool:
        return status_by_type.get(ctype) in ("connected", "syncing")

    # The demo client's data is synthetic (see demo_data.py, which serves the
    # gsc.* keys), so it has no connector rows at all and would otherwise lose
    # its Search Console tab to the gate below. Treat its synthetic sources as
    # connected — same carve-out the dashboard renderer already applies to
    # has_connectors/has_paid_ads. Only show_gsc is forced: the demo has no
    # pagespeed/hubspot/gtm data, so those tabs stay off exactly as they are now.
    _is_demo = False
    try:
        import demo_client
        _is_demo = demo_client.is_demo(client_slug)
    except Exception:
        _is_demo = False

    # HubSpot can be connected while only pulling some of its objects (an admin
    # can switch off contacts/deals or emails to keep them out of BigQuery), so
    # each HubSpot tab is gated on the data that actually backs it.
    _hs_objects = _hubspot_sync_objects(configs)

    return {
        "_connector_read_ok": True,
        "show_connectors": bool(configs),
        "show_lead_tracking": _connected("hubspot") and (_hs_objects["contacts"] or _hs_objects["deals"]),
        "show_email_performance": _connected("hubspot") and _hs_objects["emails"],
        "show_linkedin_organic": _connected("linkedin_organic"),
        "show_gsc": _connected("gsc") or _is_demo,
        "show_gtm": _connected("gtm"),
        "show_pagespeed": _connected("pagespeed"),
        "show_semrush": _connected("semrush"),
        # Consent & Tracking Health is opt-in per client. Most clients don't need
        # to see it — it only clutters their sidebar — so it stays hidden unless an
        # admin turns it on from Settings ("Show on client sidebar"). Admins reach
        # the page (to configure / run scans) via the Settings link regardless.
        "show_consent": _consent_sidebar_enabled(client_slug),
    }


def _hubspot_sync_objects(configs) -> dict[str, bool]:
    """Which HubSpot objects this client's connector is set to pull.

    Fails open to all three: an unset selection (every connector configured
    before the option existed) syncs everything, and an unreadable one should
    never be the reason a tab disappears.
    """
    try:
        raw = next((getattr(c, "sync_options", None) for c in configs
                    if c.connector_type == "hubspot"), None)
        import hubspot_sync_service
        return hubspot_sync_service.parse_sync_options(raw)["sync_objects"]
    except Exception:
        return {"contacts": True, "deals": True, "emails": True}


def _consent_sidebar_enabled(client_slug: str) -> bool:
    try:
        import client_dashboard_config
        cfg = client_dashboard_config.get_config(client_slug)
        return bool(cfg and cfg.consent_sidebar_enabled)
    except Exception:
        return False


def _sidebar_hidden_tabs(client_slug: str) -> tuple[str, ...]:
    """Core sidebar tabs an admin has hidden for this client (server-side, per
    client). Empty on any error so a config hiccup never blanks the nav."""
    try:
        import client_dashboard_config
        cfg = client_dashboard_config.get_config(client_slug)
        return cfg.sidebar_hidden_tabs if cfg else ()
    except Exception:
        return ()


def _has_consent_config(client_slug: str) -> bool:
    try:
        import consent_store
        return consent_store.get_config(client_slug) is not None
    except Exception:
        return False

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

# Double-chevron pointing left — the discreet "collapse the sidebar" affordance
# in the sidebar head (desktop only).
_NAV_ICON_COLLAPSE = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="11 17 6 12 11 7"/><polyline points="18 17 13 12 18 7"/></svg>'
)
# Sidebar-panel glyph for the floating button that brings the rail back.
_NAV_ICON_SIDEBAR = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>'
)


# Compact tool icons for the sidebar footer toolbar (match the nav icon style).
_TOOL_ICON_THEME = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>'
)
_TOOL_ICON_VIEWAS = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
)
_TOOL_ICON_SIGNOUT = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/>'
    '<line x1="21" y1="12" x2="9" y2="12"/></svg>'
)

# Shield glyph for the "Admin" button + the Admin page's tab-strip title, echoing
# the "Admin panel" parent avatar in the client switcher so both admin
# affordances read as the same family.
_ADMIN_SECTION_ICON = (
    '<svg class="dash-admin-shield" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/>'
    '<path d="M9.5 12l1.8 1.8 3.4-3.6"/></svg>'
)

# Client-facing tabs that admins can show/hide from the Advanced admin tab.
# Keys mirror the data-tab attributes in dashboard_sidebar_view_nav_html and the
# server-side client_dashboard_config.sidebar_hidden_tabs list (persisted per
# client, so a hidden tab stays hidden for every user in every browser). Every
# section the sidebar can render is listed so any can be included/excluded — the
# always-present core tabs plus the connector-gated standalone pages. A tab whose
# connector isn't connected still lists here (checkbox = "not admin-hidden"); it
# only appears in the sidebar once its data source is connected. Kept in step
# with client_dashboard_config.SIDEBAR_TOGGLEABLE_TABS. (Consent Health is
# excluded — it has its own opt-in "show on client sidebar" control.)
_SIDEBAR_TAB_EDIT_ITEMS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("explorer", "Campaign Explorer"),
    ("analytics", "Website Analytics"),
    ("ai_traffic", "AI Traffic"),
    ("gsc", "Search Console"),
    ("site_performance", "Site Performance"),
    ("lead_tracking", "Lead Tracking"),
    ("email_performance", "Email Performance"),
    ("linkedin_organic", "LinkedIn Organic"),
    ("event_tracking", "Event Tracking"),
)


def _sidebar_view_as_options(*, current_email: str | None) -> str:
    """<option> markup for the "View as user" tool (every other user)."""
    try:
        import web_users
        if not web_users.enabled():
            return ""
        users = web_users.list_users()
    except Exception:
        return ""
    opts = []
    for u in users:
        addr = str(u.get("email") or "")
        if current_email and addr == current_email:
            continue
        role = str(u.get("role") or "")
        slug = str(u.get("client_slug") or "").strip()
        meta = role + (f" · {slug}" if slug else "")
        label_txt = f"{addr} — {meta}" if meta else addr
        opts.append(f'<option value="{int(u["id"])}">{_esc(label_txt)}</option>')
    return "".join(opts)


# ── Admin surface: one page with tabs across the top ────────────────────────
# The per-client agency options used to live in a collapsible sidebar section.
# They now live on a single "Admin" page whose tabs (rendered by
# ``admin_top_tabs_html``) span the top of the content area. The first three tabs
# reuse the existing per-client pages; the last two are lightweight tool pages
# served from ``/dashboard/<slug>/admin/<tab>`` (see settings_routes).
_ADMIN_TAB_ITEMS: tuple[tuple[str, str], ...] = (
    ("connectors", "Connectors"),
    ("insights", "Insights"),
    ("consent", "Consent Health"),
    ("sidebar", "Advanced"),
    ("view-as", "View As"),
)

# active_nav → the admin tab it belongs to (so the sidebar "Admin" button and the
# top tab strip both light up on every admin page, however it was reached).
_ADMIN_NAV_TO_TAB: dict[str, str] = {
    "connectors": "connectors",
    "settings": "insights",
    "insights-upload": "insights",
    "consent": "consent",
    "admin-sidebar": "sidebar",
    "admin-view-as": "view-as",
}


def admin_tab_url(
    *, tab: str, client_slug: str, access_key: str | None, use_session: bool
) -> str:
    """Destination for one admin tab. Connectors/Insights/Consent reuse the
    existing per-client pages; the three tool tabs live under ``/admin/<tab>``."""
    if tab == "connectors":
        return _connectors_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{client_slug}/connectors"
    if tab == "insights":
        return _settings_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{client_slug}/settings"
    if tab == "consent":
        return _consent_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{client_slug}/consent"
    base = f"/dashboard/{client_slug}/admin/{tab}"
    if not use_session and access_key:
        from urllib.parse import quote
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def admin_top_tabs_html(
    *, client_slug: str, active_tab: str, access_key: str | None, use_session: bool
) -> str:
    """The Admin page's top tab strip (admins only). ``active_tab`` is one of the
    keys in ``_ADMIN_TAB_ITEMS``; the matching tab is marked current."""
    tabs = []
    for key, label in _ADMIN_TAB_ITEMS:
        url = admin_tab_url(
            tab=key, client_slug=client_slug, access_key=access_key, use_session=use_session
        )
        active = " active" if key == active_tab else ""
        aria = ' aria-current="page"' if key == active_tab else ""
        tabs.append(
            f'<a class="admin-top-tab{active}"{aria} href="{_esc(url)}">{_esc(label)}</a>'
        )
    return f"""
      <div class="admin-surface">
        <div class="admin-surface-title">{_ADMIN_SECTION_ICON}<span>Admin</span></div>
        <nav class="admin-top-tabs" aria-label="Admin sections">{"".join(tabs)}</nav>
      </div>"""


def _person_initials(email: str) -> str:
    """Up to two initials from an email's local part (avatar fallback)."""
    local = (email or "").split("@")[0]
    for sep in (".", "-", "_", "+"):
        local = local.replace(sep, " ")
    parts = [p for p in local.split(" ") if p]
    if len(parts) >= 2:
        return _esc((parts[0][0] + parts[1][0]).upper())
    return _esc((local[:2] or "?").upper())


def _display_name_from_email(email: str) -> str:
    """A human-ish display name derived from an email (there is no stored name):
    the local part split on separators and title-cased — "mike.miller@x" → "Mike
    Miller". Falls back to the raw email when the local part is empty."""
    local = (email or "").split("@")[0]
    for sep in (".", "-", "_", "+"):
        local = local.replace(sep, " ")
    parts = [p for p in local.split() if p]
    if not parts:
        return email or ""
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def _person_avatar_html(*, email: str, avatar: str | None) -> str:
    """Avatar chip for the signed-in person: their uploaded headshot when one
    exists, else colour-coded initials (colour derived from the email so it stays
    stable). Mirrors the client-switcher avatar treatment."""
    if avatar:
        return (
            '<span class="dash-profile-ava dash-profile-ava--img" aria-hidden="true">'
            f'<img src="{_esc(str(avatar))}" alt="" loading="lazy"></span>'
        )
    return (
        f'<span class="dash-profile-ava" style="background:{_client_avatar_color(email)}" '
        f'aria-hidden="true">{_person_initials(email)}</span>'
    )


def _sidebar_admin_nav_html(
    *,
    email: str | None,
    session_is_admin: bool,
    active_nav: str,
    connectors_url: str,
    admin_context: bool = False,
) -> str:
    """The "Admin" bucket at the bottom of the sidebar's section nav.

    A labelled group (divider + eyebrow, matching the section nav's own rows)
    holding **Settings** — the Admin page with Connectors, Insights, Consent
    Health, Advanced and View As across the top. It used to live
    inside the footer's profile popover, which buried an entry point admins reach
    constantly; here it sits in the nav where it reads as a section of the portal.

    Visibility is unchanged by the move: internal users only (admins + agency-wide
    "standard" accounts), never client-role logins, and dropped inside the admin
    environment where the workspace switcher already carries "Admin panel". The
    role lookup is self-sufficient (like the other sidebar helpers) so callers
    don't thread it through, and fails closed — an unknown role only sees the
    bucket if the session's admin flag says so. Under "view as" the email is the
    impersonated user's, so this renders as they'd see it.
    """
    if admin_context:
        return ""

    role = "admin" if session_is_admin else ""
    try:
        import web_users
        u = web_users.get_user_by_email(email) if (email and web_users.enabled()) else None
        if u:
            role = u.role
    except Exception:
        pass
    if not ((session_is_admin or role in ("admin", "standard")) and role != "client"):
        return ""

    active = " active" if active_nav in _ADMIN_NAV_TO_TAB else ""
    aria = ' aria-current="page"' if active else ""
    return (
        '<nav class="dash-sidebar-nav dash-sidebar-nav--admin" aria-label="Admin">'
        '<div class="dash-sidebar-group">Admin</div>'
        f'<a class="dash-view-btn{active}"{aria} data-nav="settings" '
        f'href="{_esc(connectors_url)}">{_NAV_ICON_SETTINGS}<span>Settings</span></a>'
        '</nav>'
    )


def _sidebar_footer_tools_html(*, email: str | None) -> str:
    """Sidebar footer: a profile dropdown (avatar + name) holding Sign out.

    The trigger shows the signed-in person's avatar and name; clicking it opens a
    small popover. Settings used to live in here too — it now sits in the
    sidebar's own "Admin" bucket (see ``_sidebar_admin_nav_html``), so the popover
    is purely account actions. **Sign out** is available to every signed-in user.
    """
    if not email:
        return ""

    # Resolve the signed-in person's avatar so the trigger can show a real
    # headshot; falls back to initials when the lookup is unavailable.
    avatar: str | None = None
    try:
        import web_users
        u = web_users.get_user_by_email(email) if web_users.enabled() else None
        if u:
            avatar = u.avatar
    except Exception:
        pass

    name = _display_name_from_email(email)
    avatar_html = _person_avatar_html(email=email, avatar=avatar)

    signout_item = (
        '<form method="post" action="/logout" class="dash-profile-signout">'
        '<button type="submit" class="dash-profile-item" role="menuitem">'
        f'{_TOOL_ICON_SIGNOUT}<span>Sign out</span></button></form>'
    )

    caret = (
        '<svg class="dash-profile-caret" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true"><polyline points="6 15 12 9 18 15"/></svg>'
    )

    return f"""
        <div class="dash-profile" role="group" aria-label="Account">
          <button type="button" class="dash-profile-trigger" id="dashProfileTrigger"
            aria-haspopup="true" aria-expanded="false" aria-controls="dashProfileMenu">
            {avatar_html}
            <span class="dash-profile-text">
              <span class="dash-profile-name">{_esc(name)}</span>
              <span class="dash-profile-email" title="{_esc(email)}">{_esc(email)}</span>
            </span>
            {caret}
          </button>
          <div class="dash-profile-menu" id="dashProfileMenu" role="menu" hidden>
            {signout_item}
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
    session_can_switch_clients: bool = False,
    admin_context: bool = False,
) -> str:
    """Navy drawer sidebar shared by the dashboard, settings, files, and connectors pages.

    Top to bottom: the Sagefrog logo, the client (workspace) switcher directly
    under it, the scrolling section nav with the internal-only "Admin" bucket at
    its foot, then the footer — Files, the account dropdown, and the collapse
    control pinned at the very bottom.

    ``admin_context=True`` renders the same chrome for the Admin environment: the
    top nav is the admin menu (passed in ``view_nav_html``), the per-client
    Files/Admin entries drop out, and the switcher shows "Admin panel" as the
    current workspace.
    """
    overview_url = _dashboard_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    files_url = _files_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    # The footer "Admin" button lands on the Admin page's first tab (Connectors).
    connectors_url = _connectors_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"

    client_selector = sidebar_client_switcher_html(
        client_slug=client_slug,
        label=label,
        active_nav=active_nav,
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_can_switch_clients=session_can_switch_clients,
        admin_context=admin_context,
    )

    files_btn = ""
    if show_files and not admin_context:
        files_active = " active" if active_nav in ("files", "insights-upload") else ""
        aria = ' aria-current="page"' if files_active else ""
        files_btn = (
            f'<a href="{_esc(files_url)}" class="dash-sidebar-link{files_active}"{aria}>'
            f'{_NAV_ICON_FILES}<span>Files</span></a>'
        )

    # Connectors + Insights (Settings) are agency-only, so they no longer live in
    # the always-visible footer nav — they're reached via the "Admin" bucket in
    # the section nav, rendered by _sidebar_admin_nav_html (internal users only).
    # ``show_connectors`` is kept in the signature for callers but no longer
    # changes the footer.
    del show_connectors

    # Apply the client's saved sidebar gradient inline (as custom properties on the
    # aside itself) so it themes every page — including the dashboard/settings pages
    # whose :root hardcodes the default --sidebar-from/--sidebar-to. Editable from
    # the footer "Edit sidebar" tool.
    _theme = dashboard_theme.load_client_theme(client_slug)
    _sb_from = _theme.get("sidebar_from", "#0a2540")
    _sb_to = _theme.get("sidebar_to", "#123456")
    sidebar_style = f"--sidebar-from:{_sb_from};--sidebar-to:{_sb_to}"

    # "Admin" bucket under the section nav, holding Settings. Shown to internal
    # users only; in the admin environment it's dropped because the switcher
    # already carries "Admin panel". Role gating lives in the helper.
    admin_nav_html = _sidebar_admin_nav_html(
        email=session_email,
        session_is_admin=session_is_admin,
        active_nav=active_nav,
        connectors_url=connectors_url,
        admin_context=admin_context,
    )

    # Footer: a profile dropdown (avatar + name) holding Sign out.
    account_html = _sidebar_footer_tools_html(email=session_email)

    return f"""
    <div class="dash-mobile-bar">
      <button type="button" class="dash-sidebar-toggle" id="sidebarToggle"
        aria-label="Open navigation" aria-expanded="false" aria-controls="dashSidebar">{_NAV_ICON_MENU}</button>
      <a href="{_esc(overview_url)}" class="dash-mobile-brand" aria-label="Sagefrog home">
        <img src="/static/apple-touch-icon.png" alt="" width="30" height="30">
        <span>Sagefrog</span>
      </a>
    </div>
    <div class="dash-sidebar-backdrop" id="sidebarBackdrop" hidden></div>
    <aside class="dash-sidebar" id="dashSidebar" aria-label="Primary navigation" style="{sidebar_style}">
      <div class="dash-sidebar-head">
        <a href="{_esc(overview_url)}" class="dash-sidebar-logo" aria-label="Sagefrog home">
          <img class="dash-sidebar-logo-icon" src="/static/apple-touch-icon.png" alt="" width="36" height="36" />
          <span class="dash-sidebar-wordmark">Sagefrog</span>
        </a>
        <span class="dash-sidebar-beta">Beta</span>
      </div>
      <div class="dash-sidebar-client">{client_selector}</div>
      <div class="dash-sidebar-scroll">
        {view_nav_html}
        {admin_nav_html}
      </div>
      <div class="dash-sidebar-collapse-row">
        <button type="button" class="dash-sidebar-collapse" id="sidebarCollapse"
          aria-label="Collapse sidebar" aria-expanded="true" title="Collapse sidebar">{_NAV_ICON_COLLAPSE}</button>
      </div>
      <div class="dash-sidebar-footer">
        <nav class="dash-sidebar-links" aria-label="Account navigation">
          {files_btn}
        </nav>
        {account_html}
      </div>
    </aside>
    <button type="button" class="dash-sidebar-reopen" id="sidebarReopen"
      aria-label="Open sidebar" title="Open sidebar">{_NAV_ICON_SIDEBAR}</button>
    <script>(function(){{try{{if(localStorage.getItem('sf_sidebar_collapsed')==='1'){{var s=document.currentScript.closest('.app-shell');if(s)s.classList.add('sidebar-collapsed');}}}}catch(e){{}}}})();</script>"""


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
    admin_tab: str | None = None,
    include_chartjs: bool = False,
) -> str:
    """Shared dashboard chrome for settings, files, and other child pages.

    ``admin_tab`` (one of the ``_ADMIN_TAB_ITEMS`` keys) renders the Admin page's
    top tab strip above the content for admins — used by the Connectors, Consent,
    and admin-tool pages so they read as tabs of one Admin surface.

    ``include_chartjs`` vendors the same Chart.js the Overview dashboard uses, so
    a child page (e.g. Lead Tracking) can render charts identical to Overview's.
    """
    del page_subtitle, client_meta_tip, show_business_line, show_connectors
    theme = dashboard_theme.load_client_theme(client_slug)
    if show_files is None:
        import client_insight_documents as docs

        show_files = docs.enabled()

    # Every dashboard uses ONE canonical section nav on every child page, so the
    # sidebar is identical to the dashboard's (same sections + Connectors always
    # reachable) and never changes shape between pages. There is no per-client nav
    # variant — this is the single nav for every client.
    show_connectors = True
    view_nav_html = dashboard_sidebar_view_nav_html(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
        as_tabs=False,
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
    admin_tabs_html = ""
    if admin_tab and session_is_admin:
        admin_tabs_html = admin_top_tabs_html(
            client_slug=client_slug,
            active_tab=admin_tab,
            access_key=access_key,
            use_session=use_session,
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — {_esc(page_title)}</title>
  {favicon_head_html()}
  <script src="/static/vendor/htmx.min.js" defer></script>
  {'<script src="/static/vendor/chart.umd.min.js"></script>' if include_chartjs else ''}
  <style>
    {dashboard_theme.root_css_block(theme)}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; -webkit-font-smoothing: antialiased; }}
    .app-shell {{ display: flex; flex-direction: row; min-height: 100vh; }}
    .dash-main {{ flex: 1; min-width: 0; display: flex; flex-direction: column; width: 100%; }}
    .dash-content {{ flex: 1; width: 100%; padding: 28px 32px 48px; }}
    .wrap {{ width: 100%; max-width: none; min-width: 0; }}
    @media (max-width: 720px) {{ .dash-content {{ padding: 18px 16px 40px; }} }}
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
          {admin_tabs_html}
          {content_html}
        </div>
      </div>
      {site_footer_html()}
    </div>
  </div>
  <script>{dashboard_topbar_js()}</script>
</body>
</html>"""


# Page-specific CSS for the admin tool tabs. The inner controls reuse the
# .dash-pop-* classes already in SIDEBAR_CSS; this only styles the card wrapper
# the panels sit in.
_ADMIN_TOOLS_CSS = """
    .admin-tool-card {
      max-width: 560px;
      background: #fff;
      border: 1px solid var(--border, #e2e8f0);
      border-radius: 14px;
      padding: 22px 24px 24px;
      box-shadow: 0 1px 2px rgba(10, 37, 64, 0.04);
    }
    .admin-tool-title { margin: 0 0 6px; font-size: 1.15rem; font-weight: 750; color: var(--navy, #0a2540); }
    .admin-tool-desc { margin: 0 0 18px; font-size: 0.88rem; line-height: 1.5; color: #6b7a90; }
"""


def _admin_tools_js() -> str:
    """Behaviour for the Advanced (sidebar) tool tab: live gradient preview +
    save and tab-visibility toggles. Guards on its card so it's a no-op on the
    other tabs."""
    return """
    (function(){
      // Advanced — sidebar gradient (live preview + save) and tab toggles.
      var card = document.getElementById('adminSidebarCard');
      if (card) {
        var live = document.getElementById('dashSidebar');
        var from = document.getElementById('sbFrom'), to = document.getElementById('sbTo');
        function applyColors(){ if(live&&from&&to){ live.style.setProperty('--sidebar-from', from.value); live.style.setProperty('--sidebar-to', to.value); } }
        function setStat(el,t,e){ if(!el)return; el.textContent=t; el.classList.toggle('err', !!e); }
        if(from&&to){ from.addEventListener('input', applyColors); to.addEventListener('input', applyColors); }
        var saveBtn=document.getElementById('sbSave'), resetBtn=document.getElementById('sbReset'), stat=document.getElementById('sbStatus');
        if(resetBtn) resetBtn.addEventListener('click', function(){ from.value=card.dataset.defFrom; to.value=card.dataset.defTo; applyColors(); });
        if(saveBtn) saveBtn.addEventListener('click', async function(){
          saveBtn.disabled=true; setStat(stat,'Saving…',false);
          try {
            var r=await fetch(card.dataset.themeUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({sidebar_from:from.value,sidebar_to:to.value})});
            var b=await r.json().catch(function(){return {};});
            if(!r.ok||!b.ok) throw new Error(b.error||('HTTP '+r.status));
            setStat(stat,'Saved. Reload other pages to see it.',false);
          } catch(err){ setStat(stat,'Save failed: '+(err.message||err),true); }
          finally{ saveBtn.disabled=false; }
        });
        // Tab visibility is server-side + per client: each toggle POSTs the full
        // hidden set so the change sticks for every user in every browser. The
        // live sidebar updates immediately; unchecked = hidden.
        var tabsUrl=card.dataset.tabsUrl, tabsStat=document.getElementById('sbTabsStatus');
        function hiddenList(){ var out=[]; card.querySelectorAll('.dash-tab-toggle').forEach(function(inp){ if(!inp.checked) out.push(inp.dataset.tab); }); return out; }
        function applyTabVisibility(){
          var hidden=hiddenList();
          document.querySelectorAll('.dash-sidebar-nav .dash-view-btn[data-tab]').forEach(function(el){
            var target=el.closest('.dash-view-item')||el;
            target.style.display=(hidden.indexOf(el.dataset.tab)!==-1)?'none':'';
          });
        }
        card.querySelectorAll('.dash-tab-toggle').forEach(function(inp){ inp.addEventListener('change', async function(){
          applyTabVisibility();
          setStat(tabsStat,'Saving…',false);
          try {
            var r=await fetch(tabsUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({hidden:hiddenList().join(',')})});
            var b=await r.json().catch(function(){return {};});
            if(!r.ok||!b.ok) throw new Error(b.error||('HTTP '+r.status));
            setStat(tabsStat,'Saved for all users.',false);
          } catch(err){ setStat(tabsStat,'Save failed: '+(err.message||err),true); }
        }); });
      }
    })();
    """


def render_admin_tools_page(
    *,
    client_slug: str,
    label: str,
    tab: str,
    access_key: str | None = None,
    use_session: bool = True,
    session_email: str | None = None,
    session_is_admin: bool = True,
) -> str:
    """One of the Admin page's tool tabs — Advanced (sidebar) or View As —
    rendered as a card inside the shared client shell (so the top tab strip and
    sidebar are identical to the Connectors/Insights/Consent tabs).
    """
    def _key(path: str) -> str:
        if not access_key:
            return path
        from urllib.parse import urlencode
        return f"{path}?{urlencode({'key': access_key})}"

    if tab == "sidebar":
        active_nav = "admin-sidebar"
        page_title = "Admin — Advanced"
        theme = dashboard_theme.load_client_theme(client_slug)
        sb_from = theme.get("sidebar_from", "#0a2540")
        sb_to = theme.get("sidebar_to", "#123456")
        def_from = dashboard_theme.DEFAULT_THEME["sidebar_from"]
        def_to = dashboard_theme.DEFAULT_THEME["sidebar_to"]
        theme_url = _key(f"/dashboard/{client_slug}/sidebar-theme")
        tabs_url = _key(f"/dashboard/{client_slug}/sidebar-tabs")
        hidden_tabs = set(_sidebar_hidden_tabs(client_slug))
        tabs_html = "".join(
            f'<label class="dash-pop-toggle"><span>{_esc(lbl)}</span>'
            f'<input type="checkbox" class="dash-tab-toggle" data-tab="{key}"'
            f'{"" if key in hidden_tabs else " checked"}></label>'
            for key, lbl in _SIDEBAR_TAB_EDIT_ITEMS
        )
        content = f"""
        <div class="admin-tool-card" id="adminSidebarCard"
             data-theme-url="{_esc(theme_url)}" data-def-from="{_esc(def_from)}"
             data-def-to="{_esc(def_to)}" data-tabs-url="{_esc(tabs_url)}">
          <h1 class="admin-tool-title">Advanced</h1>
          <p class="admin-tool-desc">Fine-tune this client's sidebar — its gradient colours and which section tabs appear.</p>
          <div class="dash-pop-label">Gradient</div>
          <div class="dash-pop-colors">
            <label class="dash-pop-color">Top<input type="color" id="sbFrom" value="{_esc(sb_from)}"></label>
            <label class="dash-pop-color">Bottom<input type="color" id="sbTo" value="{_esc(sb_to)}"></label>
          </div>
          <div class="dash-pop-actions">
            <button type="button" class="dash-pop-btn primary" id="sbSave">Save colors</button>
            <button type="button" class="dash-pop-btn" id="sbReset">Reset</button>
          </div>
          <span class="dash-pop-status" id="sbStatus"></span>
          <div class="dash-pop-label" style="margin-top:18px">Tabs</div>
          <div class="dash-pop-tabs">{tabs_html}</div>
          <span class="dash-pop-status" id="sbTabsStatus"></span>
          <span class="dash-pop-hint">Tabs are saved for this client — the change applies to every user, in every browser.</span>
        </div>
        <script>{_admin_tools_js()}</script>"""
    elif tab == "view-as":
        active_nav = "admin-view-as"
        page_title = "Admin — View As"
        opts = _sidebar_view_as_options(current_email=session_email)
        body = (
            f"""<form method="post" action="/admin/view-as" class="dash-pop-form">
                  <label for="sbViewAs">User</label>
                  <select id="sbViewAs" name="user_id" required>
                    <option value="" disabled selected>Select a user…</option>
                    {opts}
                  </select>
                  <div class="dash-pop-actions"><button type="submit" class="dash-pop-btn primary">View as user</button></div>
                </form>"""
            if opts
            else '<p class="dash-pop-empty">No other users to view as yet.</p>'
        )
        content = f"""
        <div class="admin-tool-card" id="adminViewAsCard">
          <h1 class="admin-tool-title">View As</h1>
          <p class="admin-tool-desc">See the platform exactly as another user does. A banner keeps you one click from exiting.</p>
          {body}
        </div>"""
    else:
        active_nav = "admin-sidebar"
        page_title = "Admin"
        content = '<div class="admin-tool-card"><p class="dash-pop-empty">Unknown admin tab.</p></div>'

    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav=active_nav,
        page_title=page_title,
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        admin_tab=tab,
        extra_css=_ADMIN_TOOLS_CSS,
    )


# ── Admin environment: same navy-sidebar shell as the client dashboards ──────
# Admin is the parent of every client, so its pages live inside the identical
# app-shell chrome (navy sidebar + client switcher) rather than a standalone
# section. The sidebar's top nav is the admin menu below; the switcher carries
# the "Admin panel" parent entry (see sidebar_client_switcher_html).

_ADMIN_NAV_ICONS: dict[str, str] = {
    "overview": _VIEW_ICONS["overview"],
    # "Clients" — the dashboards register (view + add client dashboards).
    "clients": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'
    ),
    # "Users" — team + client portal logins and client groups.
    "users": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>'
    ),
    # "Feature requests" — the team's inbox from the notes FAB.
    "feature-requests": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>'
        '<path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>'
    ),
    # "Advanced settings" — debug tools, connections, credentials, audit log.
    "advanced": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
    ),
    "hq": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/>'
        '<path d="M7 3h10a2 2 0 0 1 2 2v1H5V5a2 2 0 0 1 2-2z"/></svg>'
    ),
    "trends": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M3 3v18h18"/><polyline points="7 14 11 10 14 13 20 7"/>'
        '<polyline points="20 11 20 7 16 7"/></svg>'
    ),
    "docs": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>'
        '<path d="M14 3v5h5"/><line x1="8" y1="13" x2="16" y2="13"/>'
        '<line x1="8" y1="17" x2="13" y2="17"/></svg>'
    ),
    "hours": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>'
    ),
    # What's new: a spark/star, i.e. "here is the new thing".
    "changelog": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M12 3l2.3 4.9 5.2.7-3.8 3.7.9 5.3-4.6-2.5-4.6 2.5.9-5.3L4.5 8.6l5.2-.7z"/></svg>'
    ),
    # Benchmarks: bar-chart columns, i.e. one bucket compared against another.
    "benchmarks": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<line x1="6" y1="20" x2="6" y2="13"/><line x1="12" y1="20" x2="12" y2="5"/>'
        '<line x1="18" y1="20" x2="18" y2="10"/><line x1="3" y1="20" x2="21" y2="20"/></svg>'
    ),
}

_ADMIN_NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    # The Overview page has been split into focused destinations so its mixed
    # bag of tools (accounts, users, notifications, debug settings) each gets
    # its own page. /admin still resolves — it redirects to Accounts.
    # NOTE: the internal keys (route segments) are unchanged; only the visible
    # labels and their order differ, so URLs and active-state keys stay stable.
    ("clients", "Accounts", "/admin/clients"),
    ("users", "Users", "/admin/users"),
    ("docs", "Docs", "/admin/docs"),
    # "What's new" is the portal's release notes (changelog.ENTRIES) — every
    # user-visible change, newest first, so the team hears it here first.
    ("changelog", "What's new", "/admin/changelog"),
    # "Health" is the DuckDB-backed agency overview (formerly "HQ" / "Agency
    # Trends"). The legacy "Budget HQ" (/admin/hq) has been phased out of the
    # nav; its route still redirects there so old links keep working.
    ("trends", "Health", "/admin/agency-trends"),
    # "Benchmarks" is the agency-averages view: every metric distributed across
    # the industry tag each account carries (see client_industries).
    ("benchmarks", "Benchmarks", "/admin/benchmarks"),
    # "Hours" is the Harvest burn-up view (hours logged vs monthly goal for
    # every client this month).
    ("hours", "Hours", "/admin/client-hours"),
    # "Notifications" is the team inbox raised from the notes FAB.
    ("feature-requests", "Notifications", "/admin/feature-requests"),
    # "Advanced" collects the debug / platform-plumbing tools that used to hide
    # behind a fold on Overview.
    ("advanced", "Advanced", "/admin/advanced"),
)


def admin_sidebar_nav_html(*, active_nav: str) -> str:
    """Admin section nav for the sidebar — the counterpart to the client view nav.

    ``active_nav`` is one of clients / users / feature-requests / trends /
    hours / docs / advanced (falls back to no active item). Rendered as anchor
    links so it looks identical on every admin page, mirroring the client
    dashboards' consistent sidebar.
    """
    active = (active_nav or "").strip().lower()
    items: list[str] = []
    for key, label, href in _ADMIN_NAV_ITEMS:
        is_active = key == active
        cls = " active" if is_active else ""
        aria = ' aria-current="page"' if is_active else ""
        icon = _ADMIN_NAV_ICONS.get(key, "")
        items.append(
            f'<a class="dash-view-btn{cls}"{aria} href="{href}">'
            f'{icon}<span>{_esc(label)}</span></a>'
        )
    return (
        '<nav class="dash-sidebar-nav" aria-label="Admin sections">'
        f'{"".join(items)}</nav>'
    )


def render_admin_shell_page(
    *,
    active_nav: str,
    page_title: str,
    content_html: str,
    session_email: str | None = None,
    session_is_admin: bool = True,
    extra_css: str = "",
    body_end_html: str = "",
) -> str:
    """Full HTML for an admin page rendered inside the shared navy-sidebar shell.

    ``content_html`` is the page body (its own ``<main>`` / sections), ``extra_css``
    the page-specific styles, and ``body_end_html`` any trailing ``<script>`` the
    page needs (loaded after the shared switcher JS). The sidebar top nav is the
    admin menu; the footer switcher shows "Admin panel" as the current workspace
    and lists every client beneath it.
    """
    sidebar = render_sidebar(
        client_slug="",
        label="Admin panel",
        active_nav=active_nav,
        access_key=None,
        use_session=True,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=False,
        show_connectors=False,
        view_nav_html=admin_sidebar_nav_html(active_nav=active_nav),
        admin_context=True,
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(page_title)} · Sagefrog Marketing Group</title>
  {favicon_head_html()}
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      background: var(--bg, #eef2f7); color: var(--ink, #0f1c2e); line-height: 1.5; -webkit-font-smoothing: antialiased; }}
    .app-shell {{ display: flex; flex-direction: row; min-height: 100vh; }}
    .dash-main {{ flex: 1; min-width: 0; display: flex; flex-direction: column; width: 100%; }}
    {SIDEBAR_CSS}
    {extra_css}
  </style>
</head>
<body>
  <div class="app-shell">
    {sidebar}
    <div class="dash-main">
      {content_html}
      {site_footer_html()}
    </div>
  </div>
  <script>{dashboard_topbar_js()}</script>
  {body_end_html}
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
         instead of sitting a few px to its left. The client switcher sits
         directly beneath, so the bottom padding is tighter than the top. */
      padding: 20px 20px 12px 25px;
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

    /* Client (workspace) switcher — pinned directly under the logo, above the
       section nav. Its 12px side padding matches the nav rows' so the trigger
       and the nav items share one left edge. */
    .dash-sidebar-client {
      flex-shrink: 0;
      padding: 0 12px 10px;
    }

    /* Scroll region holding the section nav + the "Admin" bucket, so a long nav
       scrolls as one and the footer stays pinned to the bottom of the rail. */
    .dash-sidebar-scroll {
      display: flex;
      flex-direction: column;
      flex: 1 1 auto;
      min-height: 0;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      /* A slim, translucent pill instead of the chunky light-grey OS bar, which
         read as a foreign element bolted onto the navy rail. Translucent white
         means it tints with whatever the gradient is doing behind it. */
      scrollbar-width: thin;
      scrollbar-color: rgba(226, 236, 248, 0.28) transparent;
    }
    .dash-sidebar-scroll::-webkit-scrollbar {
      width: 10px;
    }
    .dash-sidebar-scroll::-webkit-scrollbar-track {
      background: transparent;
    }
    .dash-sidebar-scroll::-webkit-scrollbar-thumb {
      background: rgba(226, 236, 248, 0.28);
      border-radius: 999px;
      /* Transparent border painted over the thumb via background-clip insets it
         from the rail's edge, so it floats rather than butting the wall. */
      border: 3px solid transparent;
      background-clip: padding-box;
    }
    /* Idle, the thumb is a faint hint; it firms up once the pointer is in the
       rail, so it's findable when wanted and quiet when not. */
    .dash-sidebar-scroll:hover {
      scrollbar-color: rgba(226, 236, 248, 0.45) transparent;
    }
    .dash-sidebar-scroll:hover::-webkit-scrollbar-thumb {
      background: rgba(226, 236, 248, 0.45);
      background-clip: padding-box;
    }
    .dash-sidebar-scroll::-webkit-scrollbar-thumb:hover {
      background: rgba(226, 236, 248, 0.62);
      background-clip: padding-box;
    }

    /* Primary view nav */
    .dash-sidebar-nav {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: 8px 12px;
      flex: 0 0 auto;
    }

    /* The "Admin" bucket: a hairline-separated group at the foot of the nav
       (internal users only). The divider is inset to the nav rows' left edge. */
    .dash-sidebar-nav--admin {
      position: relative;
      margin-top: 6px;
      padding-top: 12px;
    }
    .dash-sidebar-nav--admin::before {
      content: "";
      position: absolute;
      top: 0;
      left: 13px;
      right: 13px;
      height: 1px;
      background: rgba(255, 255, 255, 0.12);
    }
    .dash-sidebar-group {
      padding: 0 13px 7px;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: rgba(226, 236, 248, 0.52);
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

    /* Editable-tab kebab (⋮): a subtle affordance that fades in on hover/focus
       of the nav row, admin-only. Its menu drops the admin into edit mode. */
    .dash-sidebar-nav .dash-view-item { position: relative; }
    .dash-sidebar-nav .dash-view-item .dash-view-btn { width: 100%; }
    .dash-view-kebab { display: none; }
    .is-admin .dash-sidebar-nav .dash-view-item .dash-view-kebab {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      position: absolute;
      top: 50%;
      right: 6px;
      transform: translateY(-50%);
      width: 26px;
      height: 26px;
      padding: 0;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: #c3d2e6;
      cursor: pointer;
      opacity: 0;
      transition: opacity .14s, background .14s, color .14s;
    }
    .is-admin .dash-sidebar-nav .dash-view-item:hover .dash-view-kebab,
    .is-admin .dash-sidebar-nav .dash-view-item:focus-within .dash-view-kebab,
    .is-admin .dash-sidebar-nav .dash-view-item.menu-open .dash-view-kebab { opacity: 1; }
    .is-admin .dash-sidebar-nav .dash-view-item .dash-view-kebab:hover {
      background: rgba(255,255,255,.16);
      color: #fff;
    }
    .is-admin .dash-sidebar-nav .dash-view-item.menu-open .dash-view-kebab {
      background: rgba(255,255,255,.16);
      color: #fff;
    }
    /* Give the row a touch of right padding on hover so the label never tucks
       under the kebab. */
    .is-admin .dash-sidebar-nav .dash-view-item:hover .dash-view-btn,
    .is-admin .dash-sidebar-nav .dash-view-item.menu-open .dash-view-btn { padding-right: 36px; }
    .dash-view-menu {
      position: absolute;
      top: calc(100% + 4px);
      right: 4px;
      z-index: 60;
      min-width: 176px;
      padding: 6px;
      background: #0d2a49;
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 10px;
      box-shadow: 0 12px 30px rgba(0,0,0,.38);
    }
    .dash-view-menu[hidden] { display: none; }
    .dash-view-menu-item {
      display: flex;
      align-items: center;
      gap: 9px;
      width: 100%;
      padding: 8px 10px;
      border: 0;
      border-radius: 7px;
      background: transparent;
      color: #e6eef8;
      font-family: inherit;
      font-size: .85rem;
      font-weight: 600;
      text-align: left;
      cursor: pointer;
    }
    .dash-view-menu-item:hover { background: rgba(255,255,255,.10); }
    .dash-view-menu-item svg { flex-shrink: 0; opacity: .85; }
    /* Collapsed (icon-only) sidebar: drop the kebab so it never overlaps the
       centered icon; edit mode is still reachable once the sidebar is expanded. */
    .app-shell.sidebar-collapsed .dash-view-kebab,
    .app-shell.sidebar-collapsed .dash-view-menu { display: none !important; }
    .app-shell.sidebar-collapsed .dash-sidebar-nav .dash-view-item:hover .dash-view-btn { padding-right: 13px; }

    /* Footer: Files + account + the collapse control */
    .dash-sidebar-footer {
      margin-top: auto;
      flex-shrink: 0;
      /* 13px left so the footer items' icons land on the same 25px rail as the
         section nav (13 + the link's 12px inner padding = 25). */
      padding: 14px 13px 12px;
      border-top: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(0, 0, 0, 0.12);
    }
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

    /* ====== Modern client switcher (trigger + slide-out drawer) ====== */
    /* Avatar chip — a colour-coded initial shared by the trigger and list rows. */
    .client-switch-ava {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      flex-shrink: 0;
      border-radius: 9px;
      color: #fff;
      font-size: 0.82rem;
      font-weight: 700;
      line-height: 1;
      letter-spacing: 0.01em;
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14);
      overflow: hidden;
    }
    /* Logo variant: a white tile holding the admin-uploaded brand mark. */
    .client-switch-ava--img {
      background: #fff;
      box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.12);
    }
    .client-switch-ava--img img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      border-radius: inherit;
      display: block;
    }

    /* Trigger row that lives in the sidebar footer. */
    .client-switch-trigger {
      display: flex;
      align-items: center;
      gap: 11px;
      width: 100%;
      box-sizing: border-box;
      padding: 8px 10px;
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 11px;
      background: rgba(255, 255, 255, 0.08);
      color: #fff;
      font-family: inherit;
      cursor: pointer;
      text-align: left;
      transition: background 0.15s, border-color 0.15s;
    }
    .client-switch-trigger:hover {
      background: rgba(255, 255, 255, 0.14);
      border-color: rgba(255, 255, 255, 0.32);
    }
    .client-switch-trigger:focus-visible {
      outline: 2px solid #7dd3fc;
      outline-offset: 2px;
    }
    .client-switch-trigger-text {
      display: flex;
      flex-direction: column;
      gap: 1px;
      min-width: 0;
      flex: 1 1 auto;
    }
    .client-switch-eyebrow {
      font-size: 0.62rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: rgba(226, 236, 248, 0.62);
    }
    .client-switch-trigger-name {
      font-size: 0.92rem;
      font-weight: 650;
      color: #fff;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .client-switch-caret {
      width: 16px;
      height: 16px;
      flex-shrink: 0;
      color: rgba(226, 236, 248, 0.6);
    }
    .client-switch-trigger:hover .client-switch-caret { color: #fff; }

    /* Backdrop + off-canvas drawer, relocated to <body> by JS. */
    .client-switch-backdrop {
      position: fixed;
      inset: 0;
      z-index: 199;
      background: rgba(8, 18, 33, 0.46);
      opacity: 0;
      transition: opacity 0.24s ease;
      -webkit-backdrop-filter: blur(2px);
      backdrop-filter: blur(2px);
    }
    body.client-switch-active .client-switch-backdrop { opacity: 1; }

    .client-switch-drawer {
      position: fixed;
      top: 0;
      left: 0;
      z-index: 200;
      display: flex;
      flex-direction: column;
      width: min(360px, 88vw);
      height: 100vh;
      height: 100dvh;
      background: #f8fafc;
      transform: translateX(-102%);
      transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
      will-change: transform;
    }
    /* Only cast the shadow while open — docked off-canvas the 40px blur reaches
       back past the edge and bleeds onto the visible screen. */
    body.client-switch-active .client-switch-drawer {
      transform: translateX(0);
      box-shadow: 12px 0 40px rgba(8, 18, 33, 0.32);
    }

    .client-switch-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 20px 18px 14px;
      background: linear-gradient(180deg, var(--sidebar-from, #0a2540), var(--sidebar-to, #123456));
      color: #fff;
      flex-shrink: 0;
    }
    .client-switch-head-titles { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
    .client-switch-title {
      margin: 0;
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #fff;
    }
    .client-switch-count {
      font-size: 0.72rem;
      font-weight: 600;
      color: rgba(226, 236, 248, 0.7);
      white-space: nowrap;
    }
    .client-switch-close {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      flex-shrink: 0;
      border: 0;
      border-radius: 9px;
      background: rgba(255, 255, 255, 0.12);
      color: #fff;
      cursor: pointer;
      transition: background 0.15s;
    }
    .client-switch-close:hover { background: rgba(255, 255, 255, 0.24); }
    .client-switch-close:focus-visible { outline: 2px solid #7dd3fc; outline-offset: 2px; }
    .client-switch-close svg { width: 18px; height: 18px; }

    .client-switch-search {
      position: relative;
      display: flex;
      align-items: center;
      padding: 14px 16px 10px;
      flex-shrink: 0;
    }
    .client-switch-search svg {
      position: absolute;
      left: 28px;
      width: 17px;
      height: 17px;
      color: #94a3b8;
      pointer-events: none;
    }
    .client-switch-search-input {
      width: 100%;
      box-sizing: border-box;
      padding: 10px 14px 10px 40px;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      background: #fff;
      color: #0f172a;
      font: inherit;
      font-size: 0.92rem;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .client-switch-search-input::placeholder { color: #94a3b8; }
    .client-switch-search-input:focus-visible {
      outline: none;
      border-color: #7dd3fc;
      box-shadow: 0 0 0 3px rgba(125, 211, 252, 0.35);
    }

    .client-switch-list {
      flex: 1 1 auto;
      min-height: 0;
      overflow-y: auto;
      padding: 4px 12px 16px;
      display: flex;
      flex-direction: column;
      gap: 2px;
      -webkit-overflow-scrolling: touch;
      /* Reserve the track's width so the row list doesn't reflow when the
         scrollbar appears, and keep a slim, rounded thumb that echoes the
         drawer's slate palette rather than the chunky OS default. */
      scrollbar-gutter: stable;
      scrollbar-width: thin;
      scrollbar-color: #cbd5e1 transparent;
    }
    .client-switch-list::-webkit-scrollbar {
      width: 8px;
    }
    .client-switch-list::-webkit-scrollbar-track {
      background: transparent;
    }
    .client-switch-list::-webkit-scrollbar-thumb {
      background: #cbd5e1;
      border-radius: 999px;
      /* Transparent border painted over the thumb via background-clip insets it
         from the track edge, so it reads as a floating pill. */
      border: 2px solid transparent;
      background-clip: padding-box;
    }
    .client-switch-list::-webkit-scrollbar-thumb:hover {
      background: #94a3b8;
      background-clip: padding-box;
    }
    .client-switch-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 9px 10px;
      border-radius: 10px;
      color: #0f172a;
      text-decoration: none;
      font-size: 0.92rem;
      font-weight: 550;
      cursor: pointer;
      transition: background 0.12s;
    }
    /* Explicit — the class sets display:flex, which would otherwise override the
       [hidden] UA rule and leave filtered-out rows visible during search. */
    .client-switch-item[hidden] { display: none; }
    .client-switch-item:hover { background: #eef2f7; }
    .client-switch-item:focus-visible {
      outline: none;
      background: #e6eefc;
      box-shadow: inset 0 0 0 2px #7dd3fc;
    }
    .client-switch-name {
      flex: 1 1 auto;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .client-switch-check {
      width: 18px;
      height: 18px;
      flex-shrink: 0;
      color: #1d6fd0;
      opacity: 0;
    }
    .client-switch-item.is-current {
      background: #e8f1fd;
      font-weight: 700;
    }
    .client-switch-item.is-current .client-switch-check { opacity: 1; }
    /* Group headings (Agency / Clients) that structure the admin-aware list. */
    .client-switch-group {
      padding: 12px 12px 4px;
      font-size: 0.66rem;
      font-weight: 800;
      letter-spacing: 0.07em;
      text-transform: uppercase;
      color: #94a3b8;
    }
    .client-switch-group[hidden] { display: none; }
    .client-switch-group:first-child { padding-top: 4px; }
    /* The "Admin panel" parent row: bordered card so the agency parent reads a
       tier above the client rows it sits over. */
    .client-switch-item--parent {
      border: 1px solid #d7e0ee;
      background: #fff;
      font-weight: 650;
      margin-bottom: 2px;
    }
    .client-switch-item--parent:hover { background: #f3f7ff; border-color: #b9cdea; }
    .client-switch-item--parent.is-current { background: #e8f1fd; border-color: #b9cdea; }
    .client-switch-ava--parent {
      background: linear-gradient(135deg, #0a2540, #123456);
      color: #fff;
    }
    .client-switch-ava--parent svg { width: 17px; height: 17px; }
    .client-switch-empty {
      margin: 18px 8px;
      color: #94a3b8;
      font-size: 0.9rem;
      text-align: center;
    }
    @media (prefers-reduced-motion: reduce) {
      .client-switch-drawer, .client-switch-backdrop { transition: none; }
    }

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
    /* Shield glyph shared by the client switcher's "Admin panel" row and the
       Admin page's tab-strip title. */
    .dash-admin-shield { width: 18px; height: 18px; flex-shrink: 0; opacity: 0.85; }
    /* Footer: profile dropdown (avatar + name) holding Sign out. No divider of
       its own — the footer's border-top already fences this region off, and the
       Files link above it may be absent for some clients. */
    .dash-profile {
      position: relative;
      margin-top: 10px;
    }
    .dash-profile-trigger {
      display: flex;
      align-items: center;
      gap: 11px;
      width: 100%;
      box-sizing: border-box;
      padding: 8px 10px;
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 11px;
      background: rgba(255, 255, 255, 0.08);
      color: #fff;
      font-family: inherit;
      cursor: pointer;
      text-align: left;
      transition: background 0.15s, border-color 0.15s;
    }
    .dash-profile-trigger:hover {
      background: rgba(255, 255, 255, 0.14);
      border-color: rgba(255, 255, 255, 0.32);
    }
    .dash-profile-trigger:focus-visible { outline: 2px solid #7dd3fc; outline-offset: 2px; }
    .dash-profile-ava {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      flex-shrink: 0;
      border-radius: 9px;
      color: #fff;
      font-size: 0.8rem;
      font-weight: 700;
      line-height: 1;
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14);
      overflow: hidden;
    }
    .dash-profile-ava--img { background: #fff; box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.12); }
    .dash-profile-ava--img img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      border-radius: inherit;
      display: block;
    }
    .dash-profile-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1 1 auto; }
    .dash-profile-name {
      font-size: 0.9rem;
      font-weight: 650;
      color: #fff;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .dash-profile-email {
      font-size: 0.72rem;
      color: rgba(226, 236, 248, 0.62);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .dash-profile-caret {
      width: 16px;
      height: 16px;
      flex-shrink: 0;
      color: rgba(226, 236, 248, 0.6);
      transition: transform 0.18s ease;
    }
    .dash-profile-trigger:hover .dash-profile-caret { color: #fff; }
    .dash-profile.is-open .dash-profile-caret { transform: rotate(180deg); }
    /* Pop-up menu, anchored above the trigger (the footer sits at the sidebar
       bottom, so the menu opens upward). */
    .dash-profile-menu {
      position: absolute;
      left: 0;
      right: 0;
      bottom: calc(100% + 8px);
      z-index: 60;
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: 6px;
      border-radius: 12px;
      border: 1px solid rgba(255, 255, 255, 0.14);
      background: #0d2a49;
      box-shadow: 0 -10px 30px rgba(3, 12, 24, 0.5);
    }
    .dash-profile-menu[hidden] { display: none; }
    .dash-profile-signout { display: block; margin: 0; }
    .dash-profile-item {
      display: flex;
      align-items: center;
      gap: 12px;
      width: 100%;
      box-sizing: border-box;
      padding: 10px 12px;
      border: 0;
      border-radius: 9px;
      background: transparent;
      color: #c3d2e6;
      font-family: inherit;
      font-size: 0.88rem;
      font-weight: 600;
      line-height: 1.2;
      text-align: left;
      text-decoration: none;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .dash-profile-item svg { width: 18px; height: 18px; flex-shrink: 0; opacity: 0.85; }
    .dash-profile-item:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
    .dash-profile-item.is-active { background: rgba(255, 255, 255, 0.12); color: #fff; }
    .dash-profile-item:focus-visible { outline: 2px solid #7dd3fc; outline-offset: -2px; }
    /* The Admin page's top tab strip (light content area). Rendered above the
       Connectors / Insights / Consent / Advanced / View As / BQ tabs so they all
       read as one Admin surface. .dash-pop-* below style the tool-card controls
       (reused from the old popovers). */
    .admin-surface {
      margin: 0 0 24px;
      border-bottom: 1px solid var(--border, #e2e8f0);
    }
    .admin-surface-title {
      display: flex;
      align-items: center;
      gap: 9px;
      font-size: 0.72rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--navy, #0a2540);
      margin-bottom: 12px;
    }
    .admin-surface-title .dash-admin-shield { width: 17px; height: 17px; opacity: 1; }
    .admin-top-tabs { display: flex; gap: 4px; flex-wrap: wrap; }
    .admin-top-tab {
      display: inline-flex;
      align-items: center;
      padding: 9px 14px;
      border-radius: 9px 9px 0 0;
      font-size: 0.9rem;
      font-weight: 650;
      color: #5b6b82;
      text-decoration: none;
      border-bottom: 2px solid transparent;
      transition: background 0.13s, color 0.13s, border-color 0.13s;
    }
    .admin-top-tab:hover { background: rgba(10, 37, 64, 0.05); color: var(--navy, #0a2540); }
    .admin-top-tab.active {
      color: var(--accent, #1d6fd0);
      border-bottom-color: var(--accent, #1d6fd0);
    }
    .dash-pop-label {
      font-size: 0.62rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 800;
      color: #6b7a90;
      margin-bottom: 6px;
    }
    .dash-pop-desc, .dash-pop-empty {
      margin: 0 0 10px;
      font-size: 0.78rem;
      line-height: 1.45;
      color: #6b7a90;
    }
    .dash-pop-hint {
      display: block;
      margin-top: 8px;
      font-size: 0.72rem;
      color: #97a3b4;
    }
    .dash-pop-colors { display: flex; gap: 10px; }
    .dash-pop-color {
      display: flex;
      flex-direction: column;
      gap: 4px;
      flex: 1;
      font-size: 0.72rem;
      font-weight: 700;
      color: #6b7a90;
    }
    .dash-pop-color input[type=color] {
      width: 100%;
      height: 30px;
      padding: 0;
      border: 1px solid #e2e8f0;
      border-radius: 7px;
      background: #fff;
      cursor: pointer;
    }
    .dash-pop-tabs { display: flex; flex-direction: column; gap: 1px; }
    .dash-pop-toggle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 5px 6px;
      border-radius: 7px;
      font-size: 0.82rem;
      font-weight: 600;
      color: #172337;
      cursor: pointer;
    }
    .dash-pop-toggle:hover { background: #f4f7fb; }
    .dash-pop-toggle input { accent-color: #1d6fd0; width: 16px; height: 16px; }
    .dash-pop-actions { display: flex; gap: 8px; margin-top: 12px; }
    .dash-pop-btn {
      appearance: none;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 7px 12px;
      font: inherit;
      font-size: 0.8rem;
      font-weight: 700;
      background: #fff;
      color: #0a2540;
      cursor: pointer;
    }
    .dash-pop-btn:hover:not(:disabled) { border-color: #b9c8dc; background: #f4f8fd; }
    .dash-pop-btn.primary { background: #1d6fd0; border-color: #1d6fd0; color: #fff; }
    .dash-pop-btn.primary:hover:not(:disabled) { background: #1a62b8; }
    .dash-pop-btn:disabled { opacity: 0.55; cursor: default; }
    .dash-pop-status { display: block; margin-top: 8px; font-size: 0.76rem; color: #6b7a90; }
    .dash-pop-status.err { color: #b42318; }
    .dash-pop-kv { display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; }
    .dash-pop-kv > div { display: flex; flex-direction: column; gap: 2px; }
    .dash-pop-kv .k {
      font-size: 0.62rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 800;
      color: #6b7a90;
    }
    .dash-pop-kv .v {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.8rem;
      font-weight: 600;
      color: #0a2540;
      word-break: break-all;
    }
    .dash-pop-form label {
      display: block;
      font-size: 0.62rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 800;
      color: #6b7a90;
      margin-bottom: 5px;
    }
    .dash-pop-form select {
      width: 100%;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 8px 10px;
      font: inherit;
      font-size: 0.84rem;
      background: #fff;
      color: #172337;
    }

    /* Desktop collapse: a discreet chevron at the very bottom of the sidebar
       tucks the whole rail away; a small floating button brings it back. The
       choice is persisted per browser (localStorage 'sf_sidebar_collapsed').
       Mobile has its own hamburger drawer, so both controls are hidden there
       (see the max-width:900px block). */
    .dash-sidebar-collapse-row {
      display: flex;
      justify-content: flex-end;
      flex-shrink: 0;
      padding: 0 13px;
      margin: 10px 0;
    }
    .dash-sidebar-collapse {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      flex-shrink: 0;
      border: 0;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.08);
      color: #c3d2e6;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .dash-sidebar-collapse:hover { background: rgba(255, 255, 255, 0.16); color: #fff; }
    .dash-sidebar-collapse svg { width: 18px; height: 18px; }
    .dash-sidebar-collapse:focus-visible { outline: 2px solid #7dd3fc; outline-offset: 2px; }

    .dash-sidebar-reopen {
      display: none;
      position: fixed;
      top: 14px;
      left: 14px;
      z-index: 96;
      width: 38px;
      height: 38px;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fff;
      color: var(--navy);
      cursor: pointer;
      box-shadow: 0 2px 8px rgba(10, 37, 64, 0.12);
      transition: background 0.12s, border-color 0.12s, color 0.12s;
    }
    .dash-sidebar-reopen:hover { background: #f0f6ff; border-color: #93c5fd; color: var(--accent, #1d6fd0); }
    .dash-sidebar-reopen svg { width: 20px; height: 20px; }

    /* Collapsed state — desktop only; the mobile drawer flow is untouched. */
    @media (min-width: 901px) {
      .app-shell.sidebar-collapsed .dash-sidebar { display: none; }
      .app-shell.sidebar-collapsed .dash-sidebar-reopen { display: inline-flex; }
    }

    /* Mobile top bar: hamburger + brand (the bar is hidden on desktop, so the
       toggle inside it is too). Replaces the old lone floating hamburger. */
    .dash-mobile-bar { display: none; }
    .dash-mobile-brand {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      text-decoration: none;
      color: var(--navy);
      font-weight: 750;
      font-size: 1.02rem;
      letter-spacing: -0.01em;
    }
    .dash-mobile-brand img { border-radius: 7px; }
    .dash-sidebar-toggle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 40px;
      height: 40px;
      flex-shrink: 0;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--navy);
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, color 0.12s;
    }
    .dash-sidebar-toggle svg { width: 20px; height: 20px; }
    .app-shell.sidebar-open .dash-sidebar-toggle {
      background: #f0f6ff;
      border-color: #93c5fd;
      color: var(--accent, #1d6fd0);
    }
    .dash-sidebar-backdrop { display: none; }

    @media (max-width: 900px) {
      .dash-mobile-bar {
        display: flex;
        align-items: center;
        gap: 12px;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 52px;
        padding: 0 12px;
        background: #fff;
        border-bottom: 1px solid var(--border);
        z-index: 95;
        box-shadow: 0 1px 3px rgba(10, 37, 64, 0.06);
      }
      .dash-sidebar {
        position: fixed;
        top: 52px;
        left: 0;
        height: calc(100vh - 52px);
        width: 264px;
        transform: translateX(-100%);
        transition: transform 0.25s ease;
      }
      /* The brand lives in the top bar now, so drop the drawer's own logo head.
         The collapse control is desktop-only (mobile uses the hamburger), and it
         now sits in the footer rather than the head, so hide it explicitly. */
      .dash-sidebar-head { display: none; }
      .dash-sidebar-collapse-row { display: none; }
      /* Give the switcher a little breathing room from the mobile top bar. */
      .dash-sidebar-client { padding-top: 12px; }
      /* Only cast the shadow while open — docked off-canvas it bleeds onto the
         visible edge and reads as a distracting line. */
      .app-shell.sidebar-open .dash-sidebar {
        transform: translateX(0);
        box-shadow: 4px 0 24px rgba(0, 0, 0, 0.28);
      }
      .app-shell.sidebar-open .dash-sidebar-backdrop {
        display: block;
        position: fixed;
        top: 52px;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 88;
        background: rgba(8, 18, 33, 0.5);
      }
      .dash-main { padding-top: 52px; }
    }
"""



# Low-profile copyright rule closing out the main column (see site_footer_html).
# The colour/hairline tokens are written with fallbacks because two token sets are
# in play: the standalone dashboard pages define --line/--muted, the shared shell
# defines --border/--text-muted, and a page that defines neither still gets the
# literal greys.
SITE_FOOTER_CSS = """
    .site-footer {
      padding: 16px 32px 22px;
      border-top: 1px solid var(--line, var(--border, #e5eaf1));
      color: var(--muted, #6b7a90);
      font-size: 0.72rem;
      line-height: 1.4;
      letter-spacing: 0.01em;
      text-align: center;
      opacity: 0.85;
    }
    @media (max-width: 720px) {
      .site-footer { padding: 14px 16px 18px; }
    }
    @media print {
      .site-footer { border-top: 0; opacity: 1; }
    }
"""

# Scrollers on the light content surfaces (the navy rail is handled inside
# SIDEBAR_CSS, where the thumb has to be light-on-dark instead).
#
# The OS default bar is a chunky grey slab that reads as browser chrome parked on
# top of the card rather than part of it. These get a slim rounded thumb in the
# card palette, faint until the pointer is over the scroller.
#
# ``.table-wrap`` is the shared table scroller used by ~15 renderers. The rest
# are named scrollers on individual pages, opted in here so there is one place to
# change the treatment; ``.scroll-slim`` is the utility to reach for on anything
# new rather than adding another selector to this list.
LIGHT_SCROLLBAR_CSS = """
    .table-wrap, .ke-dd-list, .ec-list, .scroll-slim {
      scrollbar-width: thin;
      scrollbar-color: rgba(90, 107, 130, 0.24) transparent;
    }
    .table-wrap:hover, .ke-dd-list:hover, .ec-list:hover, .scroll-slim:hover {
      scrollbar-color: rgba(90, 107, 130, 0.42) transparent;
    }
    .table-wrap::-webkit-scrollbar,
    .ke-dd-list::-webkit-scrollbar,
    .ec-list::-webkit-scrollbar,
    .scroll-slim::-webkit-scrollbar { width: 10px; height: 10px; }
    .table-wrap::-webkit-scrollbar-track,
    .ke-dd-list::-webkit-scrollbar-track,
    .ec-list::-webkit-scrollbar-track,
    .scroll-slim::-webkit-scrollbar-track { background: transparent; }
    /* The transparent border plus background-clip insets the thumb from the
       track edge, so it floats inside the card's rounded border rather than
       butting up against it. */
    .table-wrap::-webkit-scrollbar-thumb,
    .ke-dd-list::-webkit-scrollbar-thumb,
    .ec-list::-webkit-scrollbar-thumb,
    .scroll-slim::-webkit-scrollbar-thumb {
      background: rgba(90, 107, 130, 0.24);
      border-radius: 999px;
      border: 3px solid transparent;
      background-clip: padding-box;
    }
    .table-wrap:hover::-webkit-scrollbar-thumb,
    .ke-dd-list:hover::-webkit-scrollbar-thumb,
    .ec-list:hover::-webkit-scrollbar-thumb,
    .scroll-slim:hover::-webkit-scrollbar-thumb {
      background: rgba(90, 107, 130, 0.42);
      background-clip: padding-box;
    }
    .table-wrap::-webkit-scrollbar-thumb:hover,
    .ke-dd-list::-webkit-scrollbar-thumb:hover,
    .ec-list::-webkit-scrollbar-thumb:hover,
    .scroll-slim::-webkit-scrollbar-thumb:hover {
      background: rgba(90, 107, 130, 0.6);
      background-clip: padding-box;
    }
    /* A table can scroll on both axes, so without this the corner shows as a
       grey square notched out of the card. */
    .table-wrap::-webkit-scrollbar-corner,
    .scroll-slim::-webkit-scrollbar-corner { background: transparent; }
    @media print {
      .table-wrap, .scroll-slim { scrollbar-width: none; }
    }
"""

# Ships with the shared chrome, so every page that already pulls in SIDEBAR_CSS
# styles the footer and its scrollers without a second import.
SIDEBAR_CSS += SITE_FOOTER_CSS + LIGHT_SCROLLBAR_CSS
