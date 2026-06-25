"""Settings page for the ARG BQ-test client.

Lets admins configure the BigQuery connection (project + dataset) and
account IDs for the ARG test dashboard. Lightweight — no BQ calls here.
"""

from __future__ import annotations

from dashboard.renderers.arg_bq_test_renderer import _SIDEBAR_CSS, _api_url
from dashboard.renderers.base_layout import favicon_head_html
from dashboard.utils.formatting import esc as _esc

_ICON_MENU = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>'
_ICON_OVERVIEW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'
_ICON_EXPLORER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>'
_ICON_WEBSITE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/></svg>'
_ICON_SETTINGS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'


def render_arg_bq_settings_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    bq_project_id: str = "",
    bq_dataset_id: str = "",
    account_ids: dict | None = None,
    flash: str | None = None,
    flash_error: str | None = None,
) -> str:
    account_ids = account_ids or {}
    admin_class = "is-admin" if session_is_admin else ""
    dash_url = _api_url("/dashboard/arg-bq-test", access_key=access_key)
    this_url = _api_url("/dashboard/arg-bq-test/settings", access_key=access_key)
    save_action = _api_url("/dashboard/arg-bq-test/settings", access_key=access_key)

    account_html = ""
    if use_session and session_email:
        admin_link = (
            '<a class="dash-sidebar-account-link" href="/admin">Admin</a>'
            '<span class="dash-sidebar-account-sep">·</span>' if session_is_admin else ""
        )
        account_html = f"""
        <div class="dash-sidebar-account">
          <span class="dash-sidebar-account-email">{_esc(session_email)}</span>
          <div class="dash-sidebar-account-actions">
            {admin_link}
            <form class="dash-sidebar-logout-form" method="post" action="/logout"><button type="submit" class="dash-sidebar-account-link">Sign out</button></form>
          </div>
        </div>"""

    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{_esc(flash)}</div>'
    elif flash_error:
        flash_html = f'<div class="flash err">{_esc(flash_error)}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ARG — BQ Test Settings</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    {_SIDEBAR_CSS}
    main {{ max-width:1000px; margin:0 auto; padding:30px 28px 56px; }}
    .page-head {{ margin-bottom:24px; }}
    h1 {{ margin:0; color:var(--navy); font-size:1.5rem; font-weight:800; letter-spacing:-.01em; }}
    h2 {{ margin:0 0 4px; color:var(--navy); font-size:1.1rem; font-weight:750; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    .hint {{ font-size:.82rem; color:var(--muted); margin:4px 0 12px; }}
    .hint code {{ background:#eef4fb; padding:1px 5px; border-radius:4px; }}
    section {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:20px 22px; margin-bottom:20px; box-shadow:var(--shadow); }}
    .flash {{ padding:11px 14px; border-radius:var(--radius-sm); margin-bottom:18px; font-size:.9rem; background:#e9f7ef; border:1px solid #b8dfc8; color:var(--ok); }}
    .flash.err {{ background:#fdecea; border-color:#f3c0bb; color:var(--bad); }}
    .form-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; margin-top:14px; }}
    .form-actions {{ grid-column:1 / -1; margin-top:4px; }}
    label {{ display:grid; gap:6px; color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; }}
    .label-sub {{ font-size:.68rem; color:var(--muted); font-weight:600; text-transform:none; letter-spacing:0; margin-top:2px; }}
    input, select {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:9px 12px; font:inherit; font-weight:500; text-transform:none; letter-spacing:0; background:#fff; color:#102033; width:100%; }}
    input:focus-visible, select:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    button.primary {{ border:0; border-radius:var(--radius-sm); padding:10px 18px; background:var(--accent); color:#fff; font-weight:700; cursor:pointer; box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    button.primary:hover {{ background:#1a62b8; }}
    .current-val {{ font-size:.78rem; color:var(--muted); margin-top:5px; }}
    .current-val code {{ background:#f0f4fa; padding:1px 5px; border-radius:4px; font-size:.75rem; color:#33506f; }}
  </style>
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    <button type="button" class="dash-sidebar-toggle" id="sidebarToggle" aria-label="Open navigation" aria-expanded="false" aria-controls="dashSidebar">{_ICON_MENU}</button>
    <div class="dash-sidebar-backdrop" id="sidebarBackdrop" hidden></div>
    <aside class="dash-sidebar" id="dashSidebar" aria-label="Primary navigation">
      <div class="dash-sidebar-head">
        <a href="{dash_url}" class="dash-sidebar-logo" aria-label="Sagefrog home">
          <img class="dash-sidebar-logo-icon" src="/static/apple-touch-icon.png" alt="" width="34" height="34" onerror="this.remove()" />
          <span class="dash-sidebar-wordmark">Sagefrog</span>
        </a>
        <span class="dash-sidebar-beta">Beta</span>
      </div>
      <nav class="dash-sidebar-nav" aria-label="Sections">
        <a class="dash-view-btn" href="{dash_url}#sec-overview">{_ICON_OVERVIEW}<span>Overview</span></a>
        <a class="dash-view-btn" href="{dash_url}#sec-explorer">{_ICON_EXPLORER}<span>Campaign Explorer</span></a>
        <a class="dash-view-btn" href="{dash_url}#sec-pages">{_ICON_WEBSITE}<span>Website Analytics</span></a>
      </nav>
      <div class="dash-sidebar-footer">
        <div class="dash-sidebar-client"><span class="topbar-client-label">ARG — BQ Test</span></div>
        <nav class="dash-sidebar-links" aria-label="Account navigation">
          <a href="{this_url}" class="dash-sidebar-link active">{_ICON_SETTINGS}<span>Settings</span></a>
        </nav>
        {account_html}
      </div>
    </aside>
    <div class="dash-main">
  <main>
    <div class="page-head">
      <h1>ARG — Settings</h1>
    </div>
    {flash_html}

    <section>
      <h2>BigQuery connection</h2>
      <p class="hint">Set the GCP project and dataset that the ARG dashboard reads from. These are saved per-client and override the Railway-level defaults.</p>
      <form class="form-grid" method="post" action="{save_action}">
        <input type="hidden" name="action" value="save_bq">
        <label>
          GCP Project ID
          <input name="gcp_project_id" value="{_esc(bq_project_id)}" placeholder="my-gcp-project-123456" spellcheck="false" autocomplete="off">
          <span class="label-sub">e.g. <code>penn-community-b-1699391543298</code></span>
        </label>
        <label>
          BQ Dataset ID
          <input name="bq_mart_dataset_id" value="{_esc(bq_dataset_id)}" placeholder="marketing_marts" spellcheck="false" autocomplete="off">
          <span class="label-sub">Default: <code>marketing_marts</code></span>
        </label>
        <div class="form-actions"><button type="submit" class="primary">Save BQ connection</button></div>
      </form>
    </section>

    <section>
      <h2>Account mapping</h2>
      <p class="hint">Account IDs used when ingesting data for ARG.</p>
      <form class="form-grid" method="post" action="{save_action}">
        <input type="hidden" name="action" value="save_accounts">
        <label>LinkedIn account ID<input name="linkedin_account_id" value="{_esc(account_ids.get('linkedin_account_id') or '')}" placeholder="503285948"></label>
        <label>Google customer ID<input name="google_customer_id" value="{_esc(account_ids.get('google_customer_id') or '')}" placeholder="8032778786"></label>
        <label>Meta account ID<input name="meta_account_id" value="{_esc(account_ids.get('meta_account_id') or '')}"></label>
        <label>GA4 client key<input name="ga4_client_key" value="{_esc(account_ids.get('ga4_client_key') or '')}" placeholder="arg"></label>
        <div class="form-actions"><button type="submit" class="primary">Save account IDs</button></div>
      </form>
    </section>

    <section>
      <h2>Provision BQ tables</h2>
      <p class="hint">Creates the <code>marketing_marts</code> dataset and all 6 mart tables in your configured GCP project (safe to re-run — existing tables are left untouched). Uses the agency service account. Save your BQ connection above first.</p>
      <form method="post" action="{save_action}">
        <input type="hidden" name="action" value="provision_bq">
        <button type="submit" class="primary">Provision mart tables</button>
      </form>
    </section>
  </main>
    </div>
  </div>
  <script>
    (function() {{
      const shell = document.querySelector('.app-shell');
      const toggle = document.getElementById('sidebarToggle');
      const backdrop = document.getElementById('sidebarBackdrop');
      if (!shell || !toggle) return;
      const setOpen = open => {{ shell.classList.toggle('sidebar-open', open); toggle.setAttribute('aria-expanded', open ? 'true' : 'false'); if (backdrop) backdrop.hidden = !open; }};
      toggle.addEventListener('click', () => setOpen(!shell.classList.contains('sidebar-open')));
      if (backdrop) backdrop.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', e => {{ if (e.key === 'Escape') setOpen(false); }});
    }})();
  </script>
</body>
</html>"""
