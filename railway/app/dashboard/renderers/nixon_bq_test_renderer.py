"""Internal Nixon BigQuery lab page.

This renderer is intentionally plain: it validates server-side BigQuery API
responses without touching Postgres snapshots or the main dashboard UI.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from dashboard.renderers.base_layout import favicon_head_html
from dashboard.utils.formatting import esc as _esc


def _api_url(path: str, *, access_key: str | None) -> str:
    if not access_key:
        return path
    return f"{path}?{urlencode({'key': access_key})}"


# Self-contained copy of the portal's navy drawer sidebar (mirrors
# base_layout.SIDEBAR_CSS) so this standalone page matches the main dashboard
# chrome without importing the psycopg-heavy base_layout chain — which keeps
# the offline .preview/ renderer working. Plain string: single braces.
_SIDEBAR_CSS = """
    .app-shell { display: flex; flex-direction: row; min-height: 100vh; }
    .dash-main { flex: 1 1 auto; min-width: 0; }
    .dash-sidebar {
      flex-shrink: 0; width: 252px; align-self: flex-start; position: sticky; top: 0;
      height: 100vh; display: flex; flex-direction: column;
      background: linear-gradient(180deg, var(--sidebar-from), var(--sidebar-to));
      color: #e6edf6; z-index: 90; overflow: hidden;
    }
    .dash-sidebar-head { display: flex; align-items: center; gap: 10px; padding: 20px 20px 16px; flex-shrink: 0; }
    .dash-sidebar-logo { display: inline-flex; align-items: center; gap: 10px; text-decoration: none; min-width: 0; }
    .dash-sidebar-logo-icon { width: 34px; height: 34px; border-radius: 9px; flex-shrink: 0; box-shadow: 0 1px 4px rgba(0,0,0,.25); }
    .dash-sidebar-wordmark { font-size: 1.18rem; font-weight: 700; letter-spacing: -.01em; color: #fff; white-space: nowrap; }
    .dash-sidebar-beta { display: inline-flex; align-items: center; padding: 3px 9px; border-radius: 7px; background: rgba(255,255,255,.14); color: #ffd9a8; font-size: .66rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; line-height: 1; }
    .dash-sidebar-nav { display: flex; flex-direction: column; gap: 3px; padding: 8px 12px; flex: 1 1 auto; overflow-y: auto; min-height: 0; }
    .dash-sidebar-nav .dash-view-btn { display: flex; align-items: center; gap: 12px; width: 100%; padding: 10px 13px; border: 0; border-radius: 9px; background: transparent; color: #c3d2e6; font-size: .92rem; font-weight: 600; text-align: left; text-decoration: none; cursor: pointer; transition: background .15s, color .15s; }
    .dash-sidebar-nav .dash-view-btn svg { width: 19px; height: 19px; flex-shrink: 0; opacity: .85; }
    .dash-sidebar-nav .dash-view-btn:hover { background: rgba(255,255,255,.08); color: #fff; }
    .dash-sidebar-nav .dash-view-btn.active { background: rgba(255,255,255,.15); color: #fff; box-shadow: inset 3px 0 0 #7dd3fc; }
    .dash-sidebar-nav .dash-view-btn.active svg { opacity: 1; }
    .dash-sidebar-footer { margin-top: auto; flex-shrink: 0; padding: 14px 14px 16px; border-top: 1px solid rgba(255,255,255,.12); background: rgba(0,0,0,.12); }
    .dash-sidebar-client { margin-bottom: 10px; }
    .dash-sidebar-client .topbar-client-label { display: block; width: 100%; box-sizing: border-box; border-radius: 9px; border: 1px solid rgba(255,255,255,.18); background-color: rgba(255,255,255,.1); color: #fff; font-size: .9rem; font-weight: 650; padding: 9px 13px; }
    .dash-sidebar-links { display: flex; flex-direction: column; gap: 2px; }
    .dash-sidebar-link { display: flex; align-items: center; gap: 11px; padding: 9px 12px; border-radius: 9px; color: #c3d2e6; font-size: .9rem; font-weight: 600; text-decoration: none; transition: background .15s, color .15s; }
    .dash-sidebar-link svg { width: 18px; height: 18px; flex-shrink: 0; opacity: .85; }
    .dash-sidebar-link:hover { background: rgba(255,255,255,.08); color: #fff; }
    .dash-sidebar-account { margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(255,255,255,.12); }
    .dash-sidebar-account-email { display: block; font-size: .76rem; color: #9fb3cc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .dash-sidebar-account-actions { display: flex; align-items: center; gap: 6px; margin-top: 3px; font-size: .8rem; }
    .dash-sidebar-account-link { appearance: none; border: 0; background: none; padding: 0; font: inherit; color: #9ecbf5; text-decoration: none; cursor: pointer; }
    .dash-sidebar-account-link:hover { text-decoration: underline; color: #cfe5fb; }
    .dash-sidebar-account-sep { color: #64768f; }
    .dash-sidebar-logout-form { display: inline; margin: 0; }
    .dash-sidebar-toggle { display: none; position: fixed; top: 12px; left: 12px; z-index: 95; width: 42px; height: 42px; align-items: center; justify-content: center; border-radius: 10px; border: 1px solid var(--line); background: #fff; color: var(--navy); cursor: pointer; box-shadow: 0 1px 3px rgba(16,33,67,.12); }
    .dash-sidebar-toggle svg { width: 20px; height: 20px; }
    .dash-sidebar-backdrop { display: none; }
    @media (max-width: 900px) {
      .dash-sidebar { position: fixed; top: 0; left: 0; height: 100vh; width: 264px; transform: translateX(-100%); transition: transform .25s ease; box-shadow: 4px 0 24px rgba(0,0,0,.28); }
      .app-shell.sidebar-open .dash-sidebar { transform: translateX(0); }
      .app-shell.sidebar-open .dash-sidebar-backdrop { display: block; position: fixed; inset: 0; z-index: 88; background: rgba(8,18,33,.5); }
      .dash-sidebar-toggle { display: inline-flex; }
      main { padding-top: 60px; }
    }
"""


def render_nixon_bigquery_test_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    # Default to the "Last 30 days" preset: 30 complete days ending yesterday
    # (today is partial, excluded).
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=30)
    account_html = ""
    if use_session and session_email:
        admin_link = (
            '<a class="dash-sidebar-account-link" href="/admin">Admin</a>'
            '<span class="dash-sidebar-account-sep">·</span>'
            if session_is_admin else ""
        )
        account_html = f"""
        <div class="dash-sidebar-account">
          <span class="dash-sidebar-account-email">{_esc(session_email)}</span>
          <div class="dash-sidebar-account-actions">
            {admin_link}
            <form class="dash-sidebar-logout-form" method="post" action="/logout"><button type="submit" class="dash-sidebar-account-link">Sign out</button></form>
          </div>
        </div>"""

    _ICON_MENU = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>'
    _ICON_OVERVIEW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'
    _ICON_EXPLORER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>'
    _ICON_WEBSITE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/></svg>'
    _ICON_SETTINGS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
    settings_url   = _api_url("/dashboard/nixon-bq-test/settings", access_key=access_key)
    analytics_url  = _api_url("/dashboard/nixon-bq-test/analytics", access_key=access_key)
    sidebar_html = f"""
    <button type="button" class="dash-sidebar-toggle" id="sidebarToggle" aria-label="Open navigation" aria-expanded="false" aria-controls="dashSidebar">{_ICON_MENU}</button>
    <div class="dash-sidebar-backdrop" id="sidebarBackdrop" hidden></div>
    <aside class="dash-sidebar" id="dashSidebar" aria-label="Primary navigation">
      <div class="dash-sidebar-head">
        <a href="#sec-overview" class="dash-sidebar-logo" aria-label="Sagefrog home">
          <img class="dash-sidebar-logo-icon" src="/static/apple-touch-icon.png" alt="" width="34" height="34" onerror="this.remove()" />
          <span class="dash-sidebar-wordmark">Sagefrog</span>
        </a>
        <span class="dash-sidebar-beta">Beta</span>
      </div>
      <nav class="dash-sidebar-nav" aria-label="Sections">
        <a class="dash-view-btn active" href="#sec-overview" data-nav="sec-overview">{_ICON_OVERVIEW}<span>Overview</span></a>
        <a class="dash-view-btn" href="#sec-explorer" data-nav="sec-explorer">{_ICON_EXPLORER}<span>Campaign Explorer</span></a>
      </nav>
      <div class="dash-sidebar-footer">
        <div class="dash-sidebar-client"><span class="topbar-client-label">Nixon — BQ Test</span></div>
        <nav class="dash-sidebar-links" aria-label="Account navigation">
          <a href="{analytics_url}" class="dash-sidebar-link">{_ICON_WEBSITE}<span>Website Analytics</span></a>
          <a href="{settings_url}" class="dash-sidebar-link">{_ICON_SETTINGS}<span>Settings</span></a>
        </nav>
        {account_html}
      </div>
    </aside>"""

    admin_class = "is-admin" if session_is_admin else ""
    backfill_html = ""
    if session_is_admin:
        backfill_html = """
    <div class="admin-bar">
      <button type="button" class="primary" id="backfillBtn">Backfill LinkedIn — 180 days</button>
      <span class="status" id="backfillStatus">Admin only · pulls ~180 days of LinkedIn into BigQuery and rebuilds the marts.</span>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nixon BigQuery Test</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --blue:#1769aa; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    {_SIDEBAR_CSS}
    /* Debug surfaces — admins only (toggled by body.is-admin). */
    .debug-only, .src-note, .raw-json {{ display:none; }}
    .is-admin .debug-only, .is-admin .src-note, .is-admin .raw-json {{ display:block; }}
    .page-head {{ margin-bottom:24px; }}
    .src-note {{ margin:0 0 14px; font-size:.72rem; color:var(--muted); }}
    .src-note code {{ background:#eef4fb; padding:1px 6px; border-radius:5px; font-size:.7rem; color:#33506f; }}
    .src-note .arrow {{ color:#9aa7bd; margin:0 4px; }}
    .admin-bar {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:20px; padding:12px 16px; border:1px solid #ecd9a6; border-radius:var(--radius-sm); background:linear-gradient(180deg,#fffaf0,#fff6e3); }}
    .admin-bar .status {{ margin:0; }}
    main {{ max-width:1320px; margin:0 auto; padding:30px 28px 56px; }}
    h1 {{ margin:0; color:var(--navy); font-size:1.5rem; font-weight:800; letter-spacing:-.01em; }}
    h2 {{ margin:0; color:var(--navy); font-size:1.1rem; font-weight:750; letter-spacing:-.005em; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    .page-head p {{ font-size:.9rem; }}
    .toolbar {{ display:flex; flex-wrap:wrap; gap:14px; align-items:end; margin-bottom:14px; }}
    label {{ display:grid; gap:6px; color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; }}
    input {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:9px 12px; font:inherit; background:#fff; color:#102033; }}
    input:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    button.primary {{ border:0; border-radius:var(--radius-sm); padding:10px 16px; background:var(--accent); color:#fff; font-weight:700; cursor:pointer; box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    button.primary:hover {{ background:#1a62b8; }}
    button.primary:disabled {{ opacity:.55; cursor:default; }}
    section {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:20px 22px; margin-bottom:20px; box-shadow:var(--shadow); }}
    section > h2 {{ margin-bottom:14px; }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr)); gap:12px; margin-top:2px; }}
    .card {{ border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:14px 15px; background:linear-gradient(180deg,#fbfdff,#f5f9fd); }}
    .card-title {{ color:var(--muted); font-size:.67rem; text-transform:uppercase; font-weight:800; letter-spacing:.05em; }}
    .card-value {{ margin-top:8px; font-size:1.45rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.01em; }}
    .status {{ color:var(--muted); font-size:.84rem; margin:0 0 14px; }}
    .status.error {{ color:var(--bad); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line-soft); border-radius:var(--radius-sm); }}
    table {{ border-collapse:collapse; width:100%; min-width:780px; font-size:.86rem; }}
    th,td {{ padding:11px 13px; border-bottom:1px solid var(--line-soft); text-align:right; white-space:nowrap; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    tbody tr:hover td {{ background:#f7faff; }}
    th {{ background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.68rem; letter-spacing:.04em; font-weight:800; position:sticky; top:0; }}
    th.left,td.left {{ text-align:left; }}
    .empty {{ color:var(--muted); padding:26px; text-align:center; }}
    details.raw-json {{ margin-top:14px; }}
    summary {{ color:var(--accent); cursor:pointer; font-weight:700; font-size:.82rem; }}
    pre {{ max-height:360px; overflow:auto; background:#0b1020; color:#d7e3ff; border-radius:var(--radius-sm); padding:12px; font-size:.78rem; margin-top:10px; }}
    code {{ background:#eef4fb; padding:2px 5px; border-radius:4px; }}
    .page-path {{ font-weight:600; color:#1f2d40; word-break:break-all; }}
    table.compact {{ min-width:0; font-size:.84rem; }}
    table.compact th, table.compact td {{ padding:8px 12px; }}
    .pager {{ display:flex; align-items:center; justify-content:flex-end; gap:12px; margin-top:12px; }}
    .pager-btn {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:var(--radius-sm); padding:6px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s; }}
    .pager-btn:hover:not(:disabled) {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .pager-btn:disabled {{ opacity:.45; cursor:default; }}
    .pager-info {{ font-size:.8rem; color:var(--muted); font-weight:600; }}
    .chart-wrap {{ position:relative; border:1px solid var(--line); border-radius:10px; padding:10px 12px; background:#fff; }}
    .trend-svg {{ width:100%; height:260px; display:block; }}
    .chart-note {{ font-size:.76rem; color:var(--muted); margin-top:8px; }}
    .chart-tip {{ position:absolute; pointer-events:none; background:#0b1020; color:#e8eefc; font-size:.74rem; line-height:1.5; padding:7px 9px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,.25); transform:translate(-50%,-112%); white-space:nowrap; z-index:5; }}
    .metric-swatch {{ width:10px; height:10px; border-radius:2px; display:inline-block; vertical-align:middle; margin-right:4px; }}
    .filter-row {{ display:flex; flex-wrap:wrap; gap:20px; margin-bottom:12px; align-items:center; }}
    .filter-group {{ display:flex; align-items:center; gap:8px; }}
    .filter-label {{ color:var(--muted); font-size:.74rem; font-weight:800; text-transform:uppercase; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .chip {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:5px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s, color .12s; }}
    .chip:hover {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .chip.active {{ background:var(--navy); color:#fff; border-color:var(--navy); }}
    .chip.active:hover {{ background:#0d2c4d; }}
    .tree-row[data-expandable] {{ cursor:pointer; }}
    .tree-row[data-expandable]:hover {{ background:#f3f8ff; }}
    .caret {{ display:inline-block; width:14px; color:var(--muted); font-size:.8rem; }}
    .tree-row[data-expandable] .caret::before {{ content:'\\25B8'; }}
    .tree-row[data-expandable].open .caret::before {{ content:'\\25BE'; }}
    .bar-row {{ display:flex; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid var(--line-soft); }}
    .bar-row:last-child {{ border-bottom:0; }}
    .bar-label {{ flex:0 0 160px; font-size:.84rem; color:var(--navy); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .bar-track {{ flex:1 1 auto; height:8px; background:var(--line-soft); border-radius:4px; overflow:hidden; }}
    .bar-fill {{ height:100%; background:var(--accent); border-radius:4px; transition:width .3s; }}
    .bar-count {{ flex:0 0 100px; font-size:.82rem; color:var(--navy); text-align:right; font-variant-numeric:tabular-nums; }}
    .bar-pct {{ color:var(--muted); font-size:.76rem; margin-left:4px; }}
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:14px; }}
    .col-panel {{ border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:14px 16px; }}
    .col-panel h3 {{ margin:0 0 12px; font-size:.88rem; font-weight:750; color:var(--navy); }}
    .subsec-h3 {{ margin:16px 0 10px; font-size:.88rem; font-weight:750; color:var(--navy); }}
    .trend-sm-svg {{ width:100%; height:130px; display:block; }}
    @media (max-width:900px) {{ .two-col {{ grid-template-columns:1fr; }} }}
    .indent1 {{ display:inline-block; width:18px; }}
    .indent2 {{ display:inline-block; width:36px; }}
    .lvl-campaign .tree-name {{ font-weight:800; color:var(--navy); }}
    .lvl-group .tree-name {{ font-weight:600; }}
    .lvl-ad td.left {{ color:var(--muted); }}
    .pill {{ display:inline-block; padding:1px 7px; border-radius:999px; font-size:.66rem; font-weight:800; letter-spacing:.03em; text-transform:uppercase; vertical-align:middle; margin-right:7px; }}
    .pill-google {{ background:#e8f0fe; color:#1a73e8; }}
    .pill-linkedin {{ background:#e6f0f8; color:#0a66c2; }}
    .ad-cell {{ display:inline-flex; align-items:center; gap:9px; vertical-align:middle; }}
    .ad-thumb {{ width:34px; height:34px; border-radius:5px; object-fit:cover; border:1px solid var(--line); background:#f0f3f8; flex:0 0 auto; }}
    .ad-meta {{ display:flex; flex-direction:column; line-height:1.25; }}
    .ad-label {{ font-weight:600; color:#1f2d40; }}
    .ad-type {{ font-size:.68rem; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }}
    .ad-copy {{ display:flex; flex-direction:column; gap:1px; margin-top:3px; }}
    .ad-copy-line {{ font-size:.78rem; color:var(--muted); white-space:normal; }}
    .ad-copy-tag {{ display:inline-block; min-width:34px; color:#9aa7bd; font-weight:700; font-size:.66rem; text-transform:uppercase; margin-right:5px; }}
    @media (max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,minmax(120px,1fr)); }} header {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    {sidebar_html}
    <div class="dash-main">
  <main>
    <div class="page-head">
      <h1>Nixon Medical</h1>
      <p class="debug-only">Internal validation page. Browser fetches Railway API endpoints only; Railway queries <code>marketing_marts</code>.</p>
    </div>
    <form class="toolbar" id="filters">
      <label>Start date<input id="startDate" type="date" value="{start.isoformat()}"></label>
      <label>End date<input id="endDate" type="date" value="{end.isoformat()}"></label>
      <button class="primary" type="submit">Refetch all modules</button>
    </form>
    <div class="filter-row" id="datePresetsRow">
      <div class="filter-group">
        <span class="filter-label">Quick range</span>
        <div class="chips" id="datePresets">
          <button type="button" class="chip" data-preset="this_month">This month</button>
          <button type="button" class="chip" data-preset="last_month">Last month</button>
          <button type="button" class="chip" data-preset="last_7">Last 7 days</button>
          <button type="button" class="chip" data-preset="last_30">Last 30 days</button>
          <button type="button" class="chip" data-preset="last_90">Last 90 days</button>
        </div>
      </div>
      <div class="filter-group">
        <span class="filter-label">Platform</span>
        <div class="chips" id="platformChips"></div>
      </div>
    </div>
    {backfill_html}

    <section id="sec-overview">
      <h2>Summary</h2>
      <p class="src-note"><code>GET /api/clients/nixon/summary</code><span class="arrow">→</span><code>marketing_marts.vw_paid_media_daily</code></p>
      <div class="status" id="summaryStatus">Waiting…</div>
      <div class="cards" id="summaryCards"></div>
      <details class="raw-json"><summary>Raw summary JSON</summary><pre id="summaryJson">{{}}</pre></details>
    </section>

    <section>
      <h2>Trends</h2>
      <p class="src-note"><code>GET /api/clients/nixon/summary</code> (daily)<span class="arrow">→</span><code>marketing_marts.vw_paid_media_daily</code></p>
      <div class="filter-row">
        <div class="filter-group">
          <span class="filter-label">Metrics</span>
          <div class="chips" id="metricChips"></div>
        </div>
      </div>
      <div class="status" id="chartStatus">Waiting…</div>
      <div class="chart-wrap" id="trendChartWrap">
        <svg id="trendChart" class="trend-svg" preserveAspectRatio="none"></svg>
        <div id="chartTip" class="chart-tip" hidden></div>
      </div>
      <p class="chart-note">Each line is normalized to its own min–max, so the overlay compares trend shapes, not absolute scale. Hover for actual values.</p>
    </section>

    <section>
      <h2>Mart health</h2>
      <p class="src-note"><code>GET /api/clients/nixon/marketing/health</code><span class="arrow">→</span><code>marketing_marts.mart_health</code></p>
      <div class="status" id="healthStatus">Waiting…</div>
      <div class="table-wrap"><table id="healthTable"></table></div>
      <details class="raw-json"><summary>Raw health JSON</summary><pre id="healthJson">{{}}</pre></details>
    </section>

    <section id="sec-explorer">
      <h2>Campaign explorer</h2>
      <p class="src-note"><code>GET .../google-ads/explorer</code><span class="arrow">→</span><code>explorer_google_ads_daily</code> · <code>GET .../linkedin/explorer</code><span class="arrow">→</span><code>fact_linkedin_ads_creative_daily</code></p>
      <div class="filter-row" id="explorerFilters">
        <div class="filter-group">
          <span class="filter-label">Product</span>
          <div class="chips" id="productChips"></div>
        </div>
        <div class="filter-group">
          <span class="filter-label">Region</span>
          <div class="chips" id="regionChips"></div>
        </div>
      </div>
      <div class="status" id="explorerStatus">Waiting…</div>
      <div class="table-wrap"><table id="explorerTable"></table></div>
      <details class="raw-json"><summary>Raw explorer JSON</summary><pre id="explorerJson">{{}}</pre></details>
    </section>

  </main>
    </div>
  </div>
  <script>
    const SUMMARY_API = "{_api_url('/api/clients/nixon/summary', access_key=access_key)}";
    const HEALTH_API = "{_api_url('/api/clients/nixon/marketing/health', access_key=access_key)}";
    const EXPLORER_API = "{_api_url('/api/clients/nixon/google-ads/explorer', access_key=access_key)}";
    const LINKEDIN_EXPLORER_API = "{_api_url('/api/clients/nixon/linkedin/explorer', access_key=access_key)}";
    const BACKFILL_API = "{_api_url('/api/clients/nixon/backfill-linkedin', access_key=access_key)}";
    const dollars = new Intl.NumberFormat('en-US', {{ style:'currency', currency:'USD', maximumFractionDigits:2 }});
    const nums = new Intl.NumberFormat('en-US');
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({{ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }}[c]));
    const num = v => Number(v || 0);
    const money = v => dollars.format(num(v));
    const count = v => nums.format(Math.round(num(v)));
    const pct = v => `${{num(v).toFixed(2)}}%`;
    function withDates(base) {{
      const sep = base.includes('?') ? '&' : '?';
      const params = new URLSearchParams({{ start_date:startDate.value, end_date:endDate.value }});
      return base + sep + params.toString();
    }}
    async function getJson(url) {{
      const resp = await fetch(url, {{ credentials:'same-origin' }});
      const body = await resp.json().catch(() => ({{ detail:resp.statusText }}));
      if (!resp.ok) throw new Error(body.detail || resp.statusText || 'Request failed');
      return body;
    }}
    function setStatus(id, text, isError=false) {{
      const el = document.getElementById(id);
      el.textContent = text;
      el.className = isError ? 'status error' : 'status';
    }}
    function setRaw(id, payload) {{
      document.getElementById(id).textContent = JSON.stringify(payload, null, 2);
    }}
    function renderTable(id, columns, rows, emptyText) {{
      const el = document.getElementById(id);
      if (!rows || !rows.length) {{
        el.innerHTML = `<tbody><tr><td class="empty">${{esc(emptyText)}}</td></tr></tbody>`;
        return;
      }}
      el.innerHTML = `<thead><tr>${{columns.map(c => `<th class="${{c.left ? 'left' : ''}}">${{esc(c.label)}}</th>`).join('')}}</tr></thead>` +
        `<tbody>${{rows.map(row => `<tr>${{columns.map(c => `<td class="${{c.left ? 'left' : ''}}">${{esc(c.format ? c.format(row[c.key], row) : row[c.key])}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
    }}
    const SUMMARY_CARDS = [
      ['spend', 'Spend', money],
      ['impressions', 'Impressions', count],
      ['clicks', 'Clicks', count],
      ['conversions', 'Conversions', count],
      ['cpc', 'CPC', money],
      ['cpa', 'CPA', money],
      ['ctr', 'CTR', pct],
    ];
    // Platform filter is shared by the summary cards and the explorer. Stored as
    // display labels ('Google'/'LinkedIn'); lowercased to match source keys.
    const platformFilter = new Set();
    let summaryPayload = null;
    function selectedSummary() {{
      if (!summaryPayload) return {{}};
      const by = summaryPayload.by_source || null;
      // No per-source breakdown (live endpoint) or no platform selected → combined.
      if (!by || platformFilter.size === 0) return summaryPayload.summary || {{}};
      const acc = {{ spend:0, impressions:0, clicks:0, conversions:0 }};
      // Tolerant match: chip 'Google' matches source_platform 'paid_google' (and
      // legacy 'google'); 'LinkedIn' matches 'paid_linkedin' / 'linkedin'.
      const needles = [...platformFilter].map(p => p.toLowerCase());
      for (const k of Object.keys(by)) {{
        if (!needles.some(nd => k.toLowerCase().includes(nd))) continue;
        const src = by[k];
        acc.spend += num(src.spend); acc.impressions += num(src.impressions);
        acc.clicks += num(src.clicks); acc.conversions += num(src.conversions);
      }}
      return {{ ...acc,
        cpc: acc.clicks ? acc.spend / acc.clicks : 0,
        cpa: acc.conversions ? acc.spend / acc.conversions : 0,
        ctr: acc.impressions ? acc.clicks / acc.impressions * 100 : 0 }};
    }}
    function renderSummary() {{
      const s = selectedSummary();
      summaryCards.innerHTML = SUMMARY_CARDS.map(([key, label, format]) => `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{format(s[key])}}</div></div>`).join('');
    }}
    // ---- Trend chart (overlay normalized daily series) ----
    const CHART_METRICS = [
      {{ key:'spend', label:'Spend', color:'#1769aa', fmt:money }},
      {{ key:'impressions', label:'Impressions', color:'#7c3aed', fmt:count }},
      {{ key:'clicks', label:'Clicks', color:'#0a7f3f', fmt:count }},
      {{ key:'cpc', label:'CPC', color:'#d97706', fmt:money }},
      {{ key:'cpa', label:'CPA', color:'#dc2626', fmt:money }},
      {{ key:'ctr', label:'CTR', color:'#0891b2', fmt:pct }},
    ];
    const chartMetrics = new Set(['spend', 'clicks']);
    let chartDaily = [];
    function buildChartDaily() {{
      // Per-date-per-source rows from the summary endpoint → filter by platform,
      // sum per day, derive cpc/cpa/ctr. Mirrors the summary-card platform logic.
      const daily = (summaryPayload && summaryPayload.daily) ? summaryPayload.daily : [];
      const needles = platformFilter.size ? [...platformFilter].map(p => p.toLowerCase()) : null;
      const byDate = new Map();
      for (const r of daily) {{
        if (needles && !needles.some(nd => String(r.source || '').toLowerCase().includes(nd))) continue;
        let d = byDate.get(r.date);
        if (!d) {{ d = {{ date:r.date, spend:0, impressions:0, clicks:0, conversions:0 }}; byDate.set(r.date, d); }}
        d.spend += num(r.spend); d.impressions += num(r.impressions); d.clicks += num(r.clicks); d.conversions += num(r.conversions);
      }}
      const out = [...byDate.values()].sort((a, b) => a.date < b.date ? -1 : 1);
      for (const d of out) {{
        d.cpc = d.clicks ? d.spend / d.clicks : 0;
        d.cpa = d.conversions ? d.spend / d.conversions : 0;
        d.ctr = d.impressions ? d.clicks / d.impressions * 100 : 0;
      }}
      return out;
    }}
    function renderChart() {{
      chartDaily = buildChartDaily();
      const svg = document.getElementById('trendChart');
      const W = 800, H = 260, padL = 12, padR = 12, padT = 14, padB = 26;
      const plotW = W - padL - padR, plotH = H - padT - padB;
      const n = chartDaily.length;
      svg.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
      if (!n) {{ svg.innerHTML = ''; setStatus('chartStatus', 'No data for this range.'); return; }}
      const active = CHART_METRICS.filter(m => chartMetrics.has(m.key));
      const xAt = i => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
      const parts = [
        `<line x1="${{padL}}" y1="${{padT}}" x2="${{padL}}" y2="${{padT + plotH}}" stroke="#eef2f7"/>`,
        `<line x1="${{padL}}" y1="${{padT + plotH}}" x2="${{padL + plotW}}" y2="${{padT + plotH}}" stroke="#e3e9f1"/>`,
      ];
      for (const m of active) {{
        const vals = chartDaily.map(d => num(d[m.key]));
        const mn = Math.min(...vals), mx = Math.max(...vals), span = (mx - mn) || 1;
        const pts = vals.map((v, i) => `${{xAt(i).toFixed(1)}},${{(padT + (1 - (v - mn) / span) * plotH).toFixed(1)}}`).join(' ');
        parts.push(`<polyline fill="none" stroke="${{m.color}}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${{pts}}"/>`);
      }}
      const lblIdx = n === 1 ? [0] : [0, Math.floor((n - 1) / 2), n - 1];
      for (const i of lblIdx) {{
        const anchor = i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle');
        parts.push(`<text x="${{xAt(i).toFixed(1)}}" y="${{H - 8}}" font-size="10" fill="#66758f" text-anchor="${{anchor}}">${{esc(String(chartDaily[i].date).slice(5))}}</text>`);
      }}
      svg.innerHTML = parts.join('');
      setStatus('chartStatus', `${{n}} day(s) · ${{active.length}} metric(s) overlaid.`);
    }}
    function buildMetricChips() {{
      const el = document.getElementById('metricChips');
      el.innerHTML = CHART_METRICS.map(m => `<button type="button" class="chip" data-key="${{m.key}}"><span class="metric-swatch" style="background:${{m.color}}"></span>${{esc(m.label)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const k = btn.dataset.key;
        if (chartMetrics.has(k)) chartMetrics.delete(k); else chartMetrics.add(k);
        syncMetricChips();
        renderChart();
      }}));
      syncMetricChips();
    }}
    function syncMetricChips() {{
      document.querySelectorAll('#metricChips .chip').forEach(btn => btn.classList.toggle('active', chartMetrics.has(btn.dataset.key)));
    }}
    function setupChartHover() {{
      const wrap = document.getElementById('trendChartWrap');
      const svg = document.getElementById('trendChart');
      const tip = document.getElementById('chartTip');
      svg.addEventListener('mousemove', ev => {{
        if (!chartDaily.length) {{ tip.hidden = true; return; }}
        const rect = svg.getBoundingClientRect();
        const W = 800, padL = 12, padR = 12;
        const frac = ((ev.clientX - rect.left) / rect.width * W - padL) / (W - padL - padR);
        let i = Math.round(frac * (chartDaily.length - 1));
        i = Math.max(0, Math.min(chartDaily.length - 1, i));
        const d = chartDaily[i];
        const active = CHART_METRICS.filter(m => chartMetrics.has(m.key));
        if (!active.length) {{ tip.hidden = true; return; }}
        tip.innerHTML = `<strong>${{esc(d.date)}}</strong>` + active.map(m => `<br><span class="metric-swatch" style="background:${{m.color}}"></span>${{esc(m.label)}}: ${{m.fmt(d[m.key])}}`).join('');
        tip.hidden = false;
        const wrapRect = wrap.getBoundingClientRect();
        tip.style.left = (ev.clientX - wrapRect.left) + 'px';
        tip.style.top = (ev.clientY - wrapRect.top) + 'px';
      }});
      svg.addEventListener('mouseleave', () => {{ tip.hidden = true; }});
    }}
    async function loadSummary() {{
      setStatus('summaryStatus', 'Loading summary...');
      try {{
        summaryPayload = await getJson(withDates(SUMMARY_API));
        renderSummary();
        renderChart();
        setRaw('summaryJson', summaryPayload);
        const note = summaryPayload.by_source ? '' : ' (no per-platform breakdown — showing combined)';
        setStatus('summaryStatus', `Loaded ${{summaryPayload.start_date}} to ${{summaryPayload.end_date}} from fact_marketing_daily.${{note}}`);
      }} catch (err) {{
        summaryPayload = null;
        setStatus('summaryStatus', err.message || String(err), true);
        setRaw('summaryJson', {{ error: err.message || String(err) }});
      }}
    }}
    async function loadHealth() {{
      setStatus('healthStatus', 'Loading mart health...');
      try {{
        const payload = await getJson(withDates(HEALTH_API));
        const rows = payload.rows || [];
        const SOURCE_LABELS = {{ google:'Google Ads', linkedin:'LinkedIn', google_analytics:'Google Analytics' }};
        const srcLabel = v => SOURCE_LABELS[String(v || '').toLowerCase()] || v;
        const moneyD = v => (v == null ? '\\u2014' : money(v));
        const countD = v => (v == null ? '\\u2014' : count(v));
        renderTable('healthTable', [
          {{ key:'source', label:'Source', left:true, format:srcLabel }},
          {{ key:'row_count', label:'Rows', format:count }},
          {{ key:'earliest_date', label:'Earliest', left:true }},
          {{ key:'latest_date', label:'Latest', left:true }},
          {{ key:'spend', label:'Spend', format:moneyD }},
          {{ key:'impressions', label:'Impr.', format:countD }},
          {{ key:'clicks', label:'Clicks', format:countD }},
          {{ key:'conversions', label:'Conv.', format:countD }},
        ], rows, 'No mart health rows found.');
        setRaw('healthJson', payload);
        setStatus('healthStatus', rows.length ? `Loaded ${{rows.length}} health row(s).` : 'No health rows found.');
      }} catch (err) {{
        setStatus('healthStatus', err.message || String(err), true);
        setRaw('healthJson', {{ error: err.message || String(err) }});
      }}
    }}
    const METRIC_COLS = [
      {{ key:'spend', label:'Spend', format:money }},
      {{ key:'impressions', label:'Impr.', format:count }},
      {{ key:'clicks', label:'Clicks', format:count }},
      {{ key:'conversions', label:'Conv.', format:count }},
      {{ key:'ctr', label:'CTR', format:pct }},
    ];
    // Fuzzy campaign-name match. Region tokens use word boundaries so "Patient
    // Apparel - TX & FL" matches both TX and FL without false hits inside words.
    const PRODUCT_RULES = {{ Apparel:/apparel/i, Scrubs:/scrub/i, Linens:/linen/i }};
    const REGION_RULES = {{ TX:/\\bTX\\b/i, FL:/\\bFL\\b/i, MA:/\\bMA\\b/i }};
    const productFilter = new Set();
    const regionFilter = new Set();
    let explorerRows = [];
    function buildChips(containerId, keys, stateSet, onChange) {{
      const el = document.getElementById(containerId);
      el.innerHTML = ['All', ...keys].map(k => `<button type="button" class="chip" data-key="${{esc(k)}}">${{esc(k)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key = btn.dataset.key;
        if (key === 'All') stateSet.clear();
        else if (stateSet.has(key)) stateSet.delete(key);
        else stateSet.add(key);
        syncChips(el, stateSet);
        (onChange || renderExplorer)();
      }}));
      syncChips(el, stateSet);
    }}
    function syncChips(el, stateSet) {{
      el.querySelectorAll('.chip').forEach(btn => {{
        const key = btn.dataset.key;
        const active = key === 'All' ? stateSet.size === 0 : stateSet.has(key);
        btn.classList.toggle('active', active);
      }});
    }}
    function explorerRowMatches(row) {{
      const name = String(row.campaign_name || '');
      const prodOk = !productFilter.size || [...productFilter].some(k => PRODUCT_RULES[k].test(name));
      const regOk = !regionFilter.size || [...regionFilter].some(k => REGION_RULES[k].test(name));
      const platOk = !platformFilter.size || [...platformFilter].some(k => k.toLowerCase() === (row.platform || ''));
      return prodOk && regOk && platOk;
    }}
    // Group flat ad rows into campaign > ad group > ad, summing metrics at each level.
    function zeroMetrics() {{ return {{ spend:0, impressions:0, clicks:0, conversions:0 }}; }}
    function addMetrics(acc, r) {{
      acc.spend += num(r.spend); acc.impressions += num(r.impressions);
      acc.clicks += num(r.clicks); acc.conversions += num(r.conversions);
    }}
    function withCtr(m) {{ return {{ ...m, ctr: m.impressions ? (num(m.clicks) / num(m.impressions) * 100) : 0 }}; }}
    function buildExplorerTree(rows) {{
      const campaigns = new Map();
      for (const r of rows) {{
        const cName = r.campaign_name || '\\u2014';
        const platform = (r.platform || 'google').toLowerCase();
        const cKey = platform + '|' + cName;
        if (!campaigns.has(cKey)) campaigns.set(cKey, {{ name:cName, platform, metrics:zeroMetrics(), groups:new Map() }});
        const camp = campaigns.get(cKey);
        addMetrics(camp.metrics, r);
        const gName = r.ad_group_name || '\\u2014';
        if (!camp.groups.has(gName)) camp.groups.set(gName, {{ name:gName, metrics:zeroMetrics(), ads:[] }});
        const grp = camp.groups.get(gName);
        addMetrics(grp.metrics, r);
        grp.ads.push(r);
      }}
      // Highest-spend campaigns first, platforms intermixed.
      return new Map([...campaigns.entries()].sort((a, b) => b[1].metrics.spend - a[1].metrics.spend));
    }}
    function metricCells(m) {{
      const withc = withCtr(m);
      return METRIC_COLS.map(c => `<td>${{c.format(withc[c.key])}}</td>`).join('');
    }}
    function platformPill(p) {{
      const key = (p || 'google').toLowerCase() === 'linkedin' ? 'linkedin' : 'google';
      return `<span class="pill pill-${{key}}">${{key === 'linkedin' ? 'LinkedIn' : 'Google'}}</span>`;
    }}
    function adCell(ad) {{
      const label = esc(ad.ad_label || ad.ad_name || '\\u2014');
      const type = ad.media_type ? `<span class="ad-type">${{esc(ad.media_type)}}</span>` : '';
      const thumb = ad.thumbnail_url
        ? `<img class="ad-thumb" src="${{esc(ad.thumbnail_url)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`
        : '';
      // Responsive search ad copy beneath the ad: headlines + first description.
      const copyLines = [
        ['H1', ad.headline_1], ['H2', ad.headline_2], ['H3', ad.headline_3], ['Desc', ad.description_1],
      ].filter(([, v]) => v).map(
        ([tag, v]) => `<span class="ad-copy-line"><span class="ad-copy-tag">${{tag}}</span>${{esc(v)}}</span>`
      ).join('');
      const copy = copyLines ? `<div class="ad-copy">${{copyLines}}</div>` : '';
      return `<div class="ad-cell">${{thumb}}<span class="ad-meta"><span class="ad-label">${{label}}</span>${{type}}${{copy}}</span></div>`;
    }}
    function renderExplorer() {{
      const filtered = explorerRows.filter(explorerRowMatches);
      const el = document.getElementById('explorerTable');
      const tree = buildExplorerTree(filtered);
      if (!tree.size) {{
        el.innerHTML = `<tbody><tr><td class="empty">No campaigns match these filters.</td></tr></tbody>`;
      }} else {{
        const head = `<thead><tr><th class="left">Campaign / Ad group / Ad</th>${{METRIC_COLS.map(c => `<th>${{esc(c.label)}}</th>`).join('')}}</tr></thead>`;
        let body = '';
        let cIdx = 0;
        for (const camp of tree.values()) {{
          const cId = 'c' + (cIdx++);
          const gCount = camp.groups.size;
          body += `<tr class="tree-row lvl-campaign" data-id="${{cId}}" data-expandable="1"><td class="left"><span class="caret"></span>${{platformPill(camp.platform)}}<span class="tree-name">${{esc(camp.name)}}</span> <span class="muted">(${{gCount}} ad group${{gCount === 1 ? '' : 's'}})</span></td>${{metricCells(camp.metrics)}}</tr>`;
          let gIdx = 0;
          for (const grp of camp.groups.values()) {{
            const gId = cId + 'g' + (gIdx++);
            const aCount = grp.ads.length;
            body += `<tr class="tree-row lvl-group" data-id="${{gId}}" data-parent="${{cId}}" data-expandable="1" hidden><td class="left"><span class="indent1"></span><span class="caret"></span><span class="tree-name">${{esc(grp.name)}}</span> <span class="muted">(${{aCount}} ad${{aCount === 1 ? '' : 's'}})</span></td>${{metricCells(grp.metrics)}}</tr>`;
            for (const ad of grp.ads) {{
              body += `<tr class="tree-row lvl-ad" data-parent="${{gId}}" hidden><td class="left"><span class="indent2"></span>${{adCell(ad)}}</td>${{metricCells(ad)}}</tr>`;
            }}
          }}
        }}
        el.innerHTML = head + `<tbody>${{body}}</tbody>`;
      }}
      const filterActive = productFilter.size || regionFilter.size;
      const totalCampaigns = new Set(explorerRows.map(r => r.campaign_name || '\\u2014')).size;
      setStatus('explorerStatus', explorerRows.length
        ? (filterActive
            ? `Showing ${{tree.size}} of ${{totalCampaigns}} campaign(s).`
            : `Loaded ${{tree.size}} campaign(s) across ${{explorerRows.length}} ads.`)
        : 'No explorer rows found.');
    }}
    // Expand/collapse a node; collapsing also hides its whole descendant subtree.
    function toggleExplorerRow(row) {{
      const id = row.dataset.id;
      const table = row.closest('table');
      const expanded = row.classList.toggle('open');
      if (expanded) {{
        table.querySelectorAll(`tr[data-parent="${{id}}"]`).forEach(c => {{ c.hidden = false; }});
      }} else {{
        const stack = [id];
        while (stack.length) {{
          const pid = stack.pop();
          table.querySelectorAll(`tr[data-parent="${{pid}}"]`).forEach(c => {{
            c.hidden = true;
            c.classList.remove('open');
            if (c.dataset.id) stack.push(c.dataset.id);
          }});
        }}
      }}
    }}
    // Normalize Google + LinkedIn rows to a common shape. LinkedIn's hierarchy
    // (campaign group > campaign/ad set > creative) maps onto the Google tree
    // levels (campaign > ad group > ad); creatives carry a thumbnail.
    function normalizeExplorerRows(google, linkedin) {{
      const out = [];
      for (const r of (google && google.rows ? google.rows : [])) {{
        out.push({{ platform:'google', campaign_name:r.campaign_name, ad_group_name:r.ad_group_name, ad_label:r.ad_label,
          headline_1:r.headline_1, headline_2:r.headline_2, headline_3:r.headline_3,
          description_1:r.description_1, description_2:r.description_2,
          ad_name:r.ad_name, final_url:r.final_url, ad_type:r.ad_type,
          thumbnail_url:'', media_type:r.ad_type || '', spend:num(r.spend), impressions:num(r.impressions), clicks:num(r.clicks), conversions:num(r.conversions) }});
      }}
      for (const r of (linkedin && linkedin.rows ? linkedin.rows : [])) {{
        out.push({{ platform:'linkedin', campaign_name:r.campaign_group_name || r.campaign_name, ad_group_name:r.campaign_name, ad_label:r.creative_name, thumbnail_url:r.thumbnail_url || r.image_url || '', media_type:r.media_type || '', spend:num(r.spend), impressions:num(r.impressions), clicks:num(r.clicks), conversions:num(r.conversions) }});
      }}
      return out;
    }}
    async function loadExplorer() {{
      setStatus('explorerStatus', 'Loading campaign explorer...');
      // LinkedIn endpoint is optional — if it 404s (not yet built), show Google only.
      const [g, l] = await Promise.all([
        getJson(withDates(EXPLORER_API)).catch(() => ({{ rows: [] }})),
        getJson(withDates(LINKEDIN_EXPLORER_API)).catch(() => ({{ rows: [] }})),
      ]);
      explorerRows = normalizeExplorerRows(g, l);
      renderExplorer();
      setRaw('explorerJson', {{ google: g, linkedin: l }});
    }}
    function loadAll() {{
      loadSummary();
      loadHealth();
      loadExplorer();
    }}
    buildChips('productChips', ['Apparel', 'Scrubs', 'Linens'], productFilter);
    buildChips('regionChips', ['TX', 'FL', 'MA'], regionFilter);
    buildChips('platformChips', ['Google', 'LinkedIn'], platformFilter, () => {{ renderSummary(); renderChart(); renderExplorer(); }});
    buildMetricChips();
    setupChartHover();
    document.getElementById('explorerTable').addEventListener('click', event => {{
      const row = event.target.closest('tr[data-expandable]');
      if (row) toggleExplorerRow(row);
    }});
    // Date presets — compute [start,end] locally, set the inputs, then refetch.
    const fmtDate = d => `${{d.getFullYear()}}-${{String(d.getMonth() + 1).padStart(2, '0')}}-${{String(d.getDate()).padStart(2, '0')}}`;
    function presetRange(name) {{
      const today = new Date();
      let s, e = today;
      // "Last N days" = N complete days ending yesterday (today is partial, excluded).
      // "This month" is month-to-date and "Last month" is the full prior month.
      const lastNDays = n => {{
        e = new Date(today); e.setDate(today.getDate() - 1);
        s = new Date(today); s.setDate(today.getDate() - n);
      }};
      if (name === 'this_month') s = new Date(today.getFullYear(), today.getMonth(), 1);
      else if (name === 'last_month') {{ s = new Date(today.getFullYear(), today.getMonth() - 1, 1); e = new Date(today.getFullYear(), today.getMonth(), 0); }}
      else if (name === 'last_7') lastNDays(7);
      else if (name === 'last_30') lastNDays(30);
      else if (name === 'last_90') lastNDays(90);
      else return null;
      return {{ start: fmtDate(s), end: fmtDate(e) }};
    }}
    function highlightPreset(name) {{
      document.querySelectorAll('#datePresets .chip').forEach(b => b.classList.toggle('active', b.dataset.preset === name));
    }}
    document.getElementById('datePresets').addEventListener('click', event => {{
      const btn = event.target.closest('[data-preset]');
      if (!btn) return;
      const range = presetRange(btn.dataset.preset);
      if (!range) return;
      startDate.value = range.start;
      endDate.value = range.end;
      highlightPreset(btn.dataset.preset);
      loadAll();
    }});
    // Manually editing a date clears the active preset highlight.
    [startDate, endDate].forEach(inp => inp.addEventListener('change', () => highlightPreset(null)));
    filters.addEventListener('submit', event => {{
      event.preventDefault();
      highlightPreset(null);
      loadAll();
    }});
    loadAll();
    // Admin-only LinkedIn backfill button (uses the signed-in session, no secret).
    (function() {{
      const btn = document.getElementById('backfillBtn');
      if (!btn) return;
      const st = document.getElementById('backfillStatus');
      btn.addEventListener('click', async () => {{
        if (!confirm('Backfill ~180 days of LinkedIn into BigQuery for Nixon?\\nThis calls the LinkedIn API and rebuilds the marts (1–2 min).')) return;
        btn.disabled = true;
        st.className = 'status';
        st.textContent = 'Backfilling… this can take 1–2 minutes. Leave this tab open.';
        const t0 = Date.now();
        try {{
          const r = await fetch(BACKFILL_API, {{ method: 'POST', credentials: 'same-origin' }});
          const body = await r.json().catch(() => ({{}}));
          if (!r.ok) throw new Error((body && (body.detail && body.detail.error || body.detail)) || r.statusText);
          const li = body.linkedin || {{}};
          const secs = Math.round((Date.now() - t0) / 1000);
          st.textContent = `Done in ${{secs}}s · LinkedIn ${{li.status || 'ok'}} — ${{li.rows_fetched ?? '—'}} rows fetched, through ${{li.data_through ?? '—'}}. Refreshing…`;
          loadAll();
        }} catch (err) {{
          st.className = 'status error';
          st.textContent = 'Backfill failed: ' + (err.message || err);
        }} finally {{
          btn.disabled = false;
        }}
      }});
    }})();
    // Mobile drawer toggle (mirrors the portal sidebar behaviour).
    (function() {{
      const shell = document.querySelector('.app-shell');
      const toggle = document.getElementById('sidebarToggle');
      const backdrop = document.getElementById('sidebarBackdrop');
      if (!shell || !toggle) return;
      const setOpen = open => {{
        shell.classList.toggle('sidebar-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (backdrop) backdrop.hidden = !open;
      }};
      toggle.addEventListener('click', () => setOpen(!shell.classList.contains('sidebar-open')));
      if (backdrop) backdrop.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', e => {{ if (e.key === 'Escape') setOpen(false); }});
      shell.querySelectorAll('.dash-sidebar a').forEach(a => a.addEventListener('click', () => {{ if (window.innerWidth <= 900) setOpen(false); }}));
    }})();
    // Scrollspy: highlight the nav item for the section currently in view.
    (function() {{
      const links = [...document.querySelectorAll('.dash-sidebar-nav .dash-view-btn')];
      const map = new Map(links.map(a => [a.dataset.nav, a]));
      const obs = new IntersectionObserver(entries => {{
        entries.forEach(en => {{
          if (en.isIntersecting) {{
            links.forEach(l => l.classList.remove('active'));
            const a = map.get(en.target.id);
            if (a) a.classList.add('active');
          }}
        }});
      }}, {{ rootMargin: '-45% 0px -50% 0px' }});
      links.forEach(a => {{ const el = document.getElementById(a.dataset.nav); if (el) obs.observe(el); }});
    }})();
  </script>
</body>
</html>"""
