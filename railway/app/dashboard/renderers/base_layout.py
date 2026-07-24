"""Shared dashboard chrome: favicon, topbar, shell layout."""

from __future__ import annotations

import dashboard_theme

from dashboard.utils.formatting import esc as _esc
from dashboard.utils.urls import (
    client_switch_target_url as _client_switch_target_url,
    connectors_page_url as _connectors_page_url,
    consent_page_url as _consent_page_url,
    dashboard_page_url as _dashboard_page_url,
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

    // ── Sidebar footer admin tools: icon buttons + popovers ───────────────
    (function() {
      const tools = document.querySelector('.dash-sidebar-tools');
      if (!tools) return;
      const btns = Array.prototype.slice.call(tools.querySelectorAll('.dash-tool-btn[data-pop]'));
      const pops = Array.prototype.slice.call(tools.querySelectorAll('.dash-tool-pop'));
      function refreshAria() {
        btns.forEach((b) => {
          const p = tools.querySelector('#pop-' + b.dataset.pop);
          b.setAttribute('aria-expanded', (p && !p.hidden) ? 'true' : 'false');
        });
      }
      function closeAll(except) {
        pops.forEach((p) => { if (p !== except) p.hidden = true; });
        refreshAria();
      }
      btns.forEach((b) => b.addEventListener('click', (e) => {
        e.stopPropagation();
        const pop = tools.querySelector('#pop-' + b.dataset.pop);
        if (!pop) return;
        const willOpen = pop.hidden;
        closeAll(willOpen ? pop : null);
        pop.hidden = !willOpen;
        refreshAria();
        if (willOpen && b.dataset.pop === 'editSidebar') syncTabs();
      }));
      pops.forEach((p) => {
        p.addEventListener('click', (e) => e.stopPropagation());
        const x = p.querySelector('.dash-tool-pop-x');
        if (x) x.addEventListener('click', () => closeAll(null));
      });
      document.addEventListener('click', () => closeAll(null));
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(null); });

      // Edit sidebar — gradient colors (live preview + save) and tab toggles.
      const editPop = tools.querySelector('#pop-editSidebar');
      const liveSidebar = document.getElementById('dashSidebar');
      const from = document.getElementById('sbFrom'), to = document.getElementById('sbTo');
      function applyColors() {
        if (!liveSidebar || !from || !to) return;
        liveSidebar.style.setProperty('--sidebar-from', from.value);
        liveSidebar.style.setProperty('--sidebar-to', to.value);
      }
      function setStat(el, txt, err) { if (!el) return; el.textContent = txt; el.classList.toggle('err', !!err); }
      if (editPop && from && to) {
        from.addEventListener('input', applyColors);
        to.addEventListener('input', applyColors);
        const saveBtn = document.getElementById('sbSave');
        const resetBtn = document.getElementById('sbReset');
        const stat = document.getElementById('sbStatus');
        if (resetBtn) resetBtn.addEventListener('click', () => {
          from.value = editPop.dataset.defFrom; to.value = editPop.dataset.defTo; applyColors();
        });
        if (saveBtn) saveBtn.addEventListener('click', async () => {
          saveBtn.disabled = true; setStat(stat, 'Saving…', false);
          try {
            const r = await fetch(editPop.dataset.themeUrl, {
              method: 'POST', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
              body: new URLSearchParams({ sidebar_from: from.value, sidebar_to: to.value }),
            });
            const b = await r.json().catch(() => ({}));
            if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP ' + r.status));
            setStat(stat, 'Saved. Reload other pages to see it.', false);
          } catch (err) { setStat(stat, 'Save failed: ' + (err.message || err), true); }
          finally { saveBtn.disabled = false; }
        });
      }
      const lsKey = editPop ? editPop.dataset.lsKey : null;
      function getTabs() { try { return JSON.parse(localStorage.getItem(lsKey) || '{}'); } catch (e) { return {}; } }
      function applyTabsToNav() {
        const m = getTabs();
        document.querySelectorAll('.dash-sidebar-nav .dash-view-btn[data-tab]').forEach((el) => {
          el.style.display = (m[el.dataset.tab] === false) ? 'none' : '';
        });
      }
      function syncTabs() {
        const m = getTabs();
        tools.querySelectorAll('.dash-tab-toggle').forEach((inp) => { inp.checked = m[inp.dataset.tab] !== false; });
      }
      tools.querySelectorAll('.dash-tab-toggle').forEach((inp) => inp.addEventListener('change', () => {
        const m = getTabs(); m[inp.dataset.tab] = inp.checked;
        try { localStorage.setItem(lsKey, JSON.stringify(m)); } catch (e) {}
        applyTabsToNav();
      }));

      // BigQuery connection — verify button drives the status pill.
      const bqPop = tools.querySelector('#pop-bqConn');
      if (bqPop) {
        const vb = bqPop.querySelector('.dash-bq-verify');
        const pill = bqPop.querySelector('.dash-bq-pill');
        if (vb && pill) {
          const setPill = (s, t) => { pill.dataset.state = s; pill.textContent = t; };
          vb.addEventListener('click', async () => {
            vb.disabled = true; setPill('checking', 'Checking…');
            try {
              const r = await fetch(vb.dataset.url, { method: 'POST', credentials: 'same-origin' });
              const b = await r.json().catch(() => ({}));
              if (b.ok) setPill('ok', b.message || 'Connected & readable');
              else setPill('err', b.error || 'Verification failed');
            } catch (err) { setPill('err', 'Failed: ' + (err.message || err)); }
            finally { vb.disabled = false; }
          });
        }
      }
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
    "linkedin-organic": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h16v16H4z"/><path d="M8 11v5"/><path d="M8 8v.01"/><path d="M12 16v-3a2 2 0 014 0v3"/><path d="M12 16v-5"/></svg>',
    "event-tracking": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
    "site-performance": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20a8 8 0 10-8-8"/><path d="M4 12a8 8 0 018-8"/><line x1="12" y1="12" x2="16" y2="9"/><circle cx="12" cy="12" r="1.6"/></svg>',
    "consent": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/></svg>',
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
    show_consent: bool = False,
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
    # Consent & Tracking Health is a standalone page → always an anchor link.
    if show_consent:
        cu = _consent_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        c_active = " active" if active_view == "consent" else ""
        items.append(
            f'<a class="dash-view-btn{c_active}" href="{_esc(cu)}">'
            f'{_VIEW_ICONS["consent"]}<span class="dash-view-label">Consent Health</span></a>'
        )
    role = "" if as_links else ' role="tablist"'
    return (
        f'<nav class="dash-sidebar-nav"{role} aria-label="Dashboard views">'
        f'{"".join(items)}</nav>'
    )


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
    dash_url = _dashboard_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    core = [
        ("overview", "Overview", _VIEW_ICONS["overview"]),
        ("explorer", "Campaign Explorer", _VIEW_ICONS["campaigns"]),
        ("analytics", "Website Analytics", _VIEW_ICONS["website"]),
        ("ai_traffic", "AI Traffic", _VIEW_ICONS["ai-traffic"]),
        ("gsc", "Search Console", _VIEW_ICONS["gsc"]),
    ]
    # Site Performance (PageSpeed Insights) is a same-page tab like Search Console,
    # gated on the pagespeed connector so it only appears once a client has it.
    if pflags.get("show_pagespeed"):
        core.append(
            ("site_performance", "Site Performance", _VIEW_ICONS["site-performance"])
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
            # Deep-link to the specific dashboard tab (?view=…) so clicking e.g.
            # "Search Console" from Settings/Files/Connectors lands on that tab,
            # not always Overview. The dashboard reads ?view= on load.
            href = dash_url if tab == "overview" else (
                f"{dash_url}{'&' if '?' in dash_url else '?'}view={tab}"
            )
            items.append(f'<a class="dash-view-btn" data-tab="{tab}" href="{_esc(href)}">{inner}</a>')
    if pflags.get("show_lead_tracking"):
        lt = _lead_tracking_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" href="{_esc(lt)}">'
            f'{_VIEW_ICONS["lead-tracking"]}<span>Lead Tracking</span></a>'
        )
    if pflags.get("show_linkedin_organic"):
        lo = _linkedin_organic_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" href="{_esc(lo)}">'
            f'{_VIEW_ICONS["linkedin-organic"]}<span>LinkedIn Organic</span></a>'
        )
    if pflags.get("show_gtm"):
        et = _gtm_page_url(
            client_slug=client_slug, access_key=access_key, use_session=use_session
        ) or "#"
        items.append(
            f'<a class="dash-view-btn" href="{_esc(et)}">'
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
    # Client-side page-visibility prefs from Settings > "Sidebar pages"
    # (localStorage 'nixon_sidebar_pages:<client_slug>'). Hides the core nav items
    # the user turned off, on every page that renders this nav so the sidebar stays
    # consistent. The key is SCOPED PER CLIENT — an unscoped global key leaked a
    # toggle to every portal in the browser. Uses style.display, NOT the hidden
    # attribute, because .dash-view-btn sets display:flex which would override
    # [hidden]. The dashboard additionally falls back off a hidden active tab (see
    # its deep-link init). Lead/Event Tracking items have no data-tab and are
    # untouched (they gate on connector state instead).
    prefs_script = (
        "<script>(function(){try{"
        f"var p=JSON.parse(localStorage.getItem('nixon_sidebar_pages:{client_slug}')||'{{}}');"
        "document.querySelectorAll('.dash-sidebar-nav .dash-view-btn[data-tab]')"
        ".forEach(function(el){if(p[el.dataset.tab]===false)el.style.display='none';});"
        "}catch(e){}})();</script>"
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
        "show_linkedin_organic": _connected("linkedin_organic"),
        "show_gsc": _connected("gsc"),
        "show_gtm": _connected("gtm"),
        "show_pagespeed": _connected("pagespeed"),
        "show_semrush": _connected("semrush"),
        # Consent & Tracking Health is opt-in per client. Most clients don't need
        # to see it — it only clutters their sidebar — so it stays hidden unless an
        # admin turns it on from Settings ("Show on client sidebar"). Admins reach
        # the page (to configure / run scans) via the Settings link regardless.
        "show_consent": _consent_sidebar_enabled(client_slug),
    }


def _consent_sidebar_enabled(client_slug: str) -> bool:
    try:
        import client_dashboard_config
        cfg = client_dashboard_config.get_config(client_slug)
        return bool(cfg and cfg.consent_sidebar_enabled)
    except Exception:
        return False


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
_TOOL_ICON_BQ = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v6c0 1.66 4 3 9 3s9-1.34 9-3V5"/>'
    '<path d="M3 11v6c0 1.66 4 3 9 3s9-1.34 9-3v-6"/></svg>'
)
_TOOL_ICON_SIGNOUT = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/>'
    '<line x1="21" y1="12" x2="9" y2="12"/></svg>'
)

# Client-facing tabs that admins can show/hide from the sidebar "Edit sidebar"
# tool. Keys mirror the data-tab attributes in dashboard_sidebar_view_nav_html
# and the localStorage 'nixon_sidebar_pages:<slug>' map the nav reads on load.
_SIDEBAR_TAB_EDIT_ITEMS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("explorer", "Campaign Explorer"),
    ("analytics", "Website Analytics"),
    ("ai_traffic", "AI Traffic"),
    ("gsc", "Search Console"),
)


def _sidebar_bq_routing(client_slug: str) -> tuple[str | None, str | None]:
    """(project, marts_dataset) for the BigQuery-connection tool, or (None, None).

    Resolved from client_dashboard_config so the shared sidebar stays self-
    sufficient (no new plumbing through every page renderer)."""
    try:
        import client_dashboard_config as cdc
        if not cdc.enabled():
            return (None, None)
        cfg = cdc.get_config(client_slug)
        if not cfg:
            return (None, None)
        return (cfg.gcp_project_id, cfg.bq_mart_dataset_id)
    except Exception:
        return (None, None)


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


def _sidebar_footer_tools_html(
    *,
    client_slug: str,
    email: str | None,
    is_admin: bool,
    access_key: str | None,
    sidebar_from: str,
    sidebar_to: str,
    sidebar_default_from: str,
    sidebar_default_to: str,
    project: str | None,
    marts_dataset: str | None,
    view_as_options_html: str,
) -> str:
    """Sidebar footer toolbar: admin quick-tools as icon buttons with popovers,
    plus an Admin-panel link (admin) and Sign out (any signed-in user).

    Replaces the old email/Admin/Sign-out text row. Popovers relocate three
    controls that used to live on other pages: the sidebar colour + tabs editor
    and the BigQuery-connection check (both from Settings), and the "View as
    user" impersonation tool (from Admin)."""
    if not email:
        return ""

    def _key(path: str) -> str:
        if not access_key:
            return path
        from urllib.parse import urlencode
        return f"{path}?{urlencode({'key': access_key})}"

    tool_buttons = ""
    popovers = ""
    if is_admin:
        # ---- Edit sidebar (gradient + tabs) ----
        theme_url = _key(f"/dashboard/{client_slug}/sidebar-theme")
        tabs_html = "".join(
            f'<label class="dash-pop-toggle"><span>{_esc(lbl)}</span>'
            f'<input type="checkbox" class="dash-tab-toggle" data-tab="{key}" checked></label>'
            for key, lbl in _SIDEBAR_TAB_EDIT_ITEMS
        )
        popovers += f"""
        <div class="dash-tool-pop" id="pop-editSidebar" role="dialog" aria-label="Edit sidebar"
             data-theme-url="{_esc(theme_url)}" data-def-from="{_esc(sidebar_default_from)}"
             data-def-to="{_esc(sidebar_default_to)}" data-ls-key="nixon_sidebar_pages:{_esc(client_slug)}" hidden>
          <div class="dash-tool-pop-head"><span>Edit sidebar</span>
            <button type="button" class="dash-tool-pop-x" aria-label="Close">&times;</button></div>
          <div class="dash-tool-pop-body">
            <div class="dash-pop-label">Gradient</div>
            <div class="dash-pop-colors">
              <label class="dash-pop-color">Top<input type="color" id="sbFrom" value="{_esc(sidebar_from)}"></label>
              <label class="dash-pop-color">Bottom<input type="color" id="sbTo" value="{_esc(sidebar_to)}"></label>
            </div>
            <div class="dash-pop-actions">
              <button type="button" class="dash-pop-btn primary" id="sbSave">Save colors</button>
              <button type="button" class="dash-pop-btn" id="sbReset">Reset</button>
            </div>
            <span class="dash-pop-status" id="sbStatus"></span>
            <div class="dash-pop-label" style="margin-top:14px">Tabs</div>
            <div class="dash-pop-tabs">{tabs_html}</div>
            <span class="dash-pop-hint">Tabs are saved in this browser.</span>
          </div>
        </div>"""

        # ---- View as user ----
        view_as_body = (
            f"""<form method="post" action="/admin/view-as" class="dash-pop-form">
              <label for="sbViewAs">User</label>
              <select id="sbViewAs" name="user_id" required>
                <option value="" disabled selected>Select a user…</option>
                {view_as_options_html}
              </select>
              <div class="dash-pop-actions"><button type="submit" class="dash-pop-btn primary">View as user</button></div>
            </form>"""
            if view_as_options_html
            else '<p class="dash-pop-empty">No other users to view as yet.</p>'
        )
        popovers += f"""
        <div class="dash-tool-pop" id="pop-viewAs" role="dialog" aria-label="View as user" hidden>
          <div class="dash-tool-pop-head"><span>View as user</span>
            <button type="button" class="dash-tool-pop-x" aria-label="Close">&times;</button></div>
          <div class="dash-tool-pop-body">
            <p class="dash-pop-desc">See the platform exactly as another user does. A banner keeps you one click from exiting.</p>
            {view_as_body}
          </div>
        </div>"""

        # ---- BigQuery connection ----
        verify_url = _key(f"/api/clients/{client_slug}/bq-verify")
        popovers += f"""
        <div class="dash-tool-pop" id="pop-bqConn" role="dialog" aria-label="BigQuery connection" hidden>
          <div class="dash-tool-pop-head"><span>BigQuery connection</span>
            <button type="button" class="dash-tool-pop-x" aria-label="Close">&times;</button></div>
          <div class="dash-tool-pop-body">
            <div class="dash-pop-kv">
              <div><span class="k">Project</span><span class="v">{_esc(project or '—')}</span></div>
              <div><span class="k">Marts dataset</span><span class="v">{_esc(marts_dataset or 'marketing_marts')}</span></div>
            </div>
            <button type="button" class="dash-pop-btn primary dash-bq-verify" data-url="{_esc(verify_url)}">Verify connection</button>
            <span class="dash-bq-pill" data-state="idle">Not verified yet</span>
          </div>
        </div>"""

        tool_buttons = (
            '<button type="button" class="dash-tool-btn" data-pop="editSidebar" aria-haspopup="dialog" '
            f'aria-expanded="false" title="Edit sidebar" aria-label="Edit sidebar">{_TOOL_ICON_THEME}</button>'
            '<button type="button" class="dash-tool-btn" data-pop="viewAs" aria-haspopup="dialog" '
            f'aria-expanded="false" title="View as user" aria-label="View as user">{_TOOL_ICON_VIEWAS}</button>'
            '<button type="button" class="dash-tool-btn" data-pop="bqConn" aria-haspopup="dialog" '
            f'aria-expanded="false" title="BigQuery connection" aria-label="BigQuery connection">{_TOOL_ICON_BQ}</button>'
        )

    # Consent Health is a full page, so this is a link (not a popover). Admin only,
    # and always offered here so admins can reach it to configure/run scans even
    # when it's hidden from the client's own sidebar. Admin panel isn't repeated
    # here — it already lives in the client switcher above.
    consent_link_btn = (
        f'<a class="dash-tool-btn" href="{_esc(_key(f"/dashboard/{client_slug}/consent"))}" '
        f'title="Consent Health" aria-label="Consent Health">{_VIEW_ICONS["consent"]}</a>'
        if is_admin
        else ""
    )

    return f"""
        <div class="dash-sidebar-tools" role="group" aria-label="Account tools">
          <div class="dash-sidebar-tools-row">
            {tool_buttons}
            <span class="dash-tools-grow"></span>
            {consent_link_btn}
            <form method="post" action="/logout" class="dash-signout-form">
              <button type="submit" class="dash-tool-btn" title="Sign out" aria-label="Sign out">{_TOOL_ICON_SIGNOUT}</button>
            </form>
          </div>
          {popovers}
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

    ``admin_context=True`` renders the same chrome for the Admin environment: the
    top nav is the admin menu (passed in ``view_nav_html``), the footer drops the
    per-client Files/Connectors/Settings links, and the switcher shows "Admin
    panel" as the current workspace.
    """
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

    connectors_btn = ""
    if (show_connectors or active_nav == "connectors") and not admin_context:
        connectors_active = " active" if active_nav == "connectors" else ""
        connectors_aria = ' aria-current="page"' if connectors_active else ""
        connectors_btn = (
            f'<a href="{_esc(connectors_url)}" class="dash-sidebar-link{connectors_active}"{connectors_aria}>'
            f'{_NAV_ICON_CONNECTORS}<span>Connectors</span></a>'
        )

    settings_btn = ""
    if not admin_context:
        settings_active = " active" if active_nav == "settings" else ""
        settings_aria = ' aria-current="page"' if settings_active else ""
        settings_btn = (
            f'<a href="{_esc(settings_url)}" class="dash-sidebar-link{settings_active}"{settings_aria}>'
            f'{_NAV_ICON_SETTINGS}<span>Insights</span></a>'
        )

    # Apply the client's saved sidebar gradient inline (as custom properties on the
    # aside itself) so it themes every page — including the dashboard/settings pages
    # whose :root hardcodes the default --sidebar-from/--sidebar-to. Editable from
    # the footer "Edit sidebar" tool.
    _theme = dashboard_theme.load_client_theme(client_slug)
    _sb_from = _theme.get("sidebar_from", "#0a2540")
    _sb_to = _theme.get("sidebar_to", "#123456")
    sidebar_style = f"--sidebar-from:{_sb_from};--sidebar-to:{_sb_to}"

    # Footer toolbar: admin quick-tools (edit sidebar, view-as, BigQuery
    # connection) plus Admin-panel + Sign-out icons. In the admin environment the
    # switcher already carries "Admin panel", so we drop the admin tools there.
    _tools_admin = session_is_admin and not admin_context
    _bq_project = _bq_marts = None
    _view_as_opts = ""
    if _tools_admin:
        _bq_project, _bq_marts = _sidebar_bq_routing(client_slug)
        _view_as_opts = _sidebar_view_as_options(current_email=session_email)
    account_html = _sidebar_footer_tools_html(
        client_slug=client_slug,
        email=session_email,
        is_admin=_tools_admin,
        access_key=access_key,
        sidebar_from=_sb_from,
        sidebar_to=_sb_to,
        sidebar_default_from=dashboard_theme.DEFAULT_THEME["sidebar_from"],
        sidebar_default_to=dashboard_theme.DEFAULT_THEME["sidebar_to"],
        project=_bq_project,
        marts_dataset=_bq_marts,
        view_as_options_html=_view_as_opts,
    )

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
        view_nav_html = dashboard_sidebar_view_nav_html(
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
            show_consent=pflags.get("show_consent", False),
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
  <script src="/static/vendor/htmx.min.js" defer></script>
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
          {content_html}
        </div>
      </div>
    </div>
  </div>
  <script>{dashboard_topbar_js()}</script>
</body>
</html>"""


# ── Admin environment: same navy-sidebar shell as the client dashboards ──────
# Admin is the parent of every client, so its pages live inside the identical
# app-shell chrome (navy sidebar + client switcher) rather than a standalone
# section. The sidebar's top nav is the admin menu below; the switcher carries
# the "Admin panel" parent entry (see sidebar_client_switcher_html).

_ADMIN_NAV_ICONS: dict[str, str] = {
    "overview": _VIEW_ICONS["overview"],
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
}

_ADMIN_NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("overview", "Overview", "/admin"),
    # "HQ" is the DuckDB-backed agency overview (formerly "Agency Trends"). The
    # legacy "Budget HQ" (/admin/hq) has been phased out of the nav; its route
    # still redirects there so old links keep working.
    ("trends", "HQ", "/admin/agency-trends"),
    # "Client Hours" is the Harvest burn-up view (hours logged vs monthly goal
    # for every client this month).
    ("hours", "Client Hours", "/admin/client-hours"),
    ("docs", "Docs", "/admin/docs"),
)


def admin_sidebar_nav_html(*, active_nav: str) -> str:
    """Admin section nav for the sidebar — the counterpart to the client view nav.

    ``active_nav`` is one of overview / hq / trends / docs (falls back to no
    active item). Rendered as anchor links so it looks identical on every admin
    page, mirroring the client dashboards' consistent sidebar.
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
    /* Footer toolbar: admin quick-tools + Admin/Sign-out as icon buttons */
    .dash-sidebar-tools {
      position: relative;
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px solid rgba(255, 255, 255, 0.12);
    }
    .dash-sidebar-tools-row { display: flex; align-items: center; gap: 3px; }
    .dash-tools-grow { flex: 1 1 auto; }
    .dash-signout-form { display: inline-flex; margin: 0; }
    .dash-tool-btn {
      appearance: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      padding: 0;
      border: 0;
      border-radius: 9px;
      background: transparent;
      color: #c3d2e6;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .dash-tool-btn svg { width: 18px; height: 18px; }
    .dash-tool-btn:hover { background: rgba(255, 255, 255, 0.10); color: #fff; }
    .dash-tool-btn[aria-expanded="true"] { background: rgba(255, 255, 255, 0.16); color: #fff; }
    .dash-tool-btn:focus-visible { outline: 2px solid rgba(158, 203, 245, 0.9); outline-offset: 1px; }
    /* Popover cards float above the toolbar (light cards on the navy rail) */
    .dash-tool-pop {
      position: absolute;
      bottom: calc(100% + 8px);
      left: 0;
      right: 0;
      background: #fff;
      color: #172337;
      border-radius: 12px;
      box-shadow: 0 14px 38px rgba(4, 12, 26, 0.45);
      z-index: 60;
      overflow: hidden;
    }
    .dash-tool-pop-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 13px;
      border-bottom: 1px solid #eef2f7;
      font-size: 0.82rem;
      font-weight: 750;
      color: #0a2540;
    }
    .dash-tool-pop-x {
      appearance: none;
      border: 0;
      background: none;
      font-size: 1.2rem;
      line-height: 1;
      color: #8a97a8;
      cursor: pointer;
      padding: 0 2px;
    }
    .dash-tool-pop-x:hover { color: #172337; }
    .dash-tool-pop-body { padding: 12px 13px; max-height: 62vh; overflow-y: auto; }
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
    .dash-bq-pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      margin-top: 10px;
      font-size: 0.8rem;
      font-weight: 600;
      padding: 4px 11px;
      border-radius: 999px;
      border: 1px solid #e2e8f0;
      background: #f7f9fc;
      color: #6b7a90;
    }
    .dash-bq-pill::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: #c5cdd9; flex-shrink: 0; }
    .dash-bq-pill[data-state="checking"] { color: #1d6fd0; border-color: #bcd4f0; background: #eef5fd; }
    .dash-bq-pill[data-state="checking"]::before { background: #1d6fd0; }
    .dash-bq-pill[data-state="ok"] { color: #0a7f3f; border-color: #b8dfc8; background: #e9f7ef; }
    .dash-bq-pill[data-state="ok"]::before { background: #0a7f3f; }
    .dash-bq-pill[data-state="err"] { color: #b42318; border-color: #f3c0bb; background: #fdecea; }
    .dash-bq-pill[data-state="err"]::before { background: #b42318; }

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
      /* The brand lives in the top bar now, so drop the drawer's own logo head. */
      .dash-sidebar-head { display: none; }
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


