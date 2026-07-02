"""Nixon Medical dashboard — paid media (Overview / Explorer) + Website Analytics tabs."""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from dashboard.renderers.base_layout import (
    SIDEBAR_CSS,
    dashboard_topbar_js,
    favicon_head_html,
    platform_nav_flags,
    render_sidebar,
)


def _api_url(path: str, *, access_key: str | None) -> str:
    if not access_key:
        return path
    return f"{path}?{urlencode({'key': access_key})}"


def _docs_enabled() -> bool:
    import client_insight_documents as docs
    return docs.enabled()


def render_nixon_bigquery_test_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=30)

    _ICON_OVERVIEW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'
    _ICON_EXPLORER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>'
    _ICON_WEBSITE  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/></svg>'
    _ICON_SEARCH   = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
    _ICON_TAGS       = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>'
    _ICON_LEADS      = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>'

    gtm_url = _api_url("/dashboard/nixon-bq-test/gtm", access_key=access_key)

    pflags = platform_nav_flags("nixon-bq-test")
    lead_tracking_link = ""
    if pflags.get("show_lead_tracking"):
        lead_tracking_url = _api_url("/dashboard/nixon-bq-test/lead-tracking", access_key=access_key)
        lead_tracking_link = (
            f'<a class="dash-view-btn" href="{lead_tracking_url}" '
            'style="margin-top:6px;border-top:1px solid rgba(255,255,255,.1);padding-top:14px">'
            f'{_ICON_LEADS}<span>Lead Tracking</span></a>'
        )

    # Page-specific JS tab buttons (Overview/Explorer/Analytics/GSC, driven by
    # switchTab() further down) + the Website-Analytics sub-nav, passed straight
    # through to the shared sidebar via view_nav_html — render_sidebar() inserts
    # this raw, so it must supply its own <nav class="dash-sidebar-nav"> wrapper.
    view_nav_html = f"""
      <nav class="dash-sidebar-nav" aria-label="Sections">
        <button class="dash-view-btn tab-btn active" data-tab="overview">{_ICON_OVERVIEW}<span>Overview</span></button>
        <button class="dash-view-btn tab-btn" data-tab="explorer">{_ICON_EXPLORER}<span>Explorer</span></button>
        <button class="dash-view-btn tab-btn" data-tab="analytics">{_ICON_WEBSITE}<span>Website Analytics</span></button>
        <button class="dash-view-btn tab-btn" data-tab="gsc">{_ICON_SEARCH}<span>Search Console</span></button>
        <div id="analyticsSubnav" hidden>
          <a class="dash-view-btn analytics-sub" href="#sec-pages"   data-nav="sec-pages"   data-module="top_pages">Top Pages</a>
          <a class="dash-view-btn analytics-sub" href="#sec-traffic"  data-nav="sec-traffic"  data-module="traffic">Traffic</a>
          <a class="dash-view-btn analytics-sub" href="#sec-audience" data-nav="sec-audience" data-module="audience">Audience</a>
          <a class="dash-view-btn analytics-sub" href="#sec-landing"  data-nav="sec-landing"  data-module="landing">Landing Pages</a>
          <a class="dash-view-btn analytics-sub" href="#sec-conversions" data-nav="sec-conversions" data-module="conversions">Conversions</a>
          <a class="dash-view-btn analytics-sub" href="#sec-useracq"  data-nav="sec-useracq"  data-module="user_acquisition">User Acquisition</a>
          <a class="dash-view-btn analytics-sub" href="#sec-demographics" data-nav="sec-demographics" data-module="demographics">Demographics</a>
        </div>
        {lead_tracking_link}
        <a class="dash-view-btn" href="{gtm_url}"{'' if lead_tracking_link else ' style="margin-top:6px;border-top:1px solid rgba(255,255,255,.1);padding-top:14px"'}>{_ICON_TAGS}<span>Event Tracking</span></a>
      </nav>
    """

    sidebar_html = render_sidebar(
        client_slug="nixon-bq-test",
        label="Nixon Medical",
        active_nav="overview",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=_docs_enabled(),
        show_connectors=pflags["show_connectors"],
        view_nav_html=view_nav_html,
    )

    admin_class = "is-admin" if session_is_admin else ""
    _ICON_ADMIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
    admin_panel_html = ""
    if session_is_admin:
        admin_panel_html = f"""
    <button class="admin-fab" id="adminFab" title="Admin tools" aria-label="Admin tools">{_ICON_ADMIN}</button>
    <div class="admin-panel" id="adminPanel">
      <div class="admin-panel-head">
        <span class="admin-panel-title">Admin tools</span>
        <button class="admin-panel-close" id="adminPanelClose" aria-label="Close">&#x2715;</button>
      </div>
      <div class="admin-panel-body">
        <p class="admin-panel-note">Nixon Medical · admin only</p>
        <button type="button" class="primary" id="adminRefreshBtn" style="width:100%">Refresh data</button>
        <span class="status" id="adminRefreshStatus" style="display:block;margin-top:8px">Re-runs all BQ queries for the current date range.</span>
      </div>
    </div>"""

    def _aurl(path: str) -> str:
        return _api_url(path, access_key=access_key)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nixon Medical</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --blue:#1769aa; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    {SIDEBAR_CSS}
    main {{ max-width:1320px; margin:0 auto; padding:24px 28px 56px; }}
    h2 {{ margin:0; color:var(--navy); font-size:1.05rem; font-weight:750; letter-spacing:-.005em; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    /* ---- Date bar (sticky) ---- */
    .date-bar {{ position:sticky; top:0; z-index:40; background:var(--card); border:1px solid var(--line); border-bottom-left-radius:var(--radius); border-bottom-right-radius:var(--radius); border-top-left-radius:0; border-top-right-radius:0; padding:14px 18px 12px; margin-bottom:20px; box-shadow:0 4px 16px rgba(16,33,67,.08); display:flex; flex-direction:column; gap:10px; }}
    /* ---- Admin FAB + slideout panel ---- */
    .admin-fab {{ position:fixed; bottom:24px; right:24px; z-index:200; width:42px; height:42px; border-radius:50%; background:var(--navy); color:#fff; border:0; cursor:pointer; display:flex; align-items:center; justify-content:center; box-shadow:0 3px 14px rgba(0,0,0,.28); transition:transform .15s,box-shadow .15s; }}
    .admin-fab:hover {{ transform:scale(1.08); box-shadow:0 5px 20px rgba(0,0,0,.35); }}
    .admin-fab svg {{ width:18px; height:18px; }}
    .admin-panel {{ position:fixed; bottom:0; right:0; z-index:199; width:300px; background:var(--card); border:1px solid var(--line); border-radius:14px 0 0 0; box-shadow:-4px 0 28px rgba(0,0,0,.12); transform:translateY(calc(100% + 4px)); transition:transform .25s cubic-bezier(.4,0,.2,1); }}
    .admin-panel.open {{ transform:translateY(0); }}
    .admin-panel-head {{ display:flex; align-items:center; justify-content:space-between; padding:12px 16px 10px; border-bottom:1px solid var(--line); }}
    .admin-panel-title {{ font-weight:700; font-size:.82rem; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); }}
    .admin-panel-close {{ background:none; border:0; cursor:pointer; color:var(--muted); font-size:1rem; line-height:1; padding:2px 4px; border-radius:4px; }}
    .admin-panel-close:hover {{ background:var(--row-alt); color:var(--text); }}
    .admin-panel-body {{ padding:16px; display:flex; flex-direction:column; gap:8px; }}
    .admin-panel-note {{ margin:0; font-size:.76rem; color:var(--muted); }}
    .date-bar-top {{ display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }}
    .date-bar-bottom {{ display:flex; flex-wrap:wrap; gap:20px; align-items:center; }}
    label {{ display:grid; gap:5px; color:var(--muted); font-size:.68rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em; }}
    input[type=date] {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:8px 11px; font:inherit; font-size:.88rem; background:#fff; color:#102033; }}
    input[type=date]:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    button.primary {{ border:0; border-radius:var(--radius-sm); padding:9px 16px; background:var(--accent); color:#fff; font-weight:700; font-size:.88rem; cursor:pointer; box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    button.primary:hover {{ background:#1a62b8; }}
    button.primary:disabled {{ opacity:.55; cursor:default; }}
    .filter-group {{ display:flex; align-items:center; gap:8px; }}
    .filter-label {{ color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; white-space:nowrap; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:5px; }}
    .chip {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:4px 12px; font:inherit; font-size:.8rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s, color .12s; }}
    .chip:hover {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .chip.active {{ background:var(--navy); color:#fff; border-color:var(--navy); }}
    .chip.active:hover {{ background:#0d2c4d; }}
    /* ---- Sections ---- */
    section {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:18px 20px 20px; margin-bottom:16px; box-shadow:var(--shadow); }}
    .sec-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:16px; }}
    .sec-head h2 {{ margin:0; }}
    .sec-head .status {{ margin:0; font-size:.76rem; text-align:right; flex-shrink:0; }}
    .status {{ color:var(--muted); font-size:.82rem; margin:0 0 12px; }}
    .status.error {{ color:var(--bad); }}
    /* ---- Metric cards ---- */
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(128px,1fr)); gap:10px; }}
    .card {{ border:1px solid var(--line-soft); border-top:3px solid var(--accent); border-radius:var(--radius-sm); padding:13px 14px 14px; background:#fff; }}
    .card-title {{ color:var(--muted); font-size:.65rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }}
    .card-value {{ margin-top:7px; font-size:1.5rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; }}
    /* ---- Tables ---- */
    .table-wrap {{ overflow:auto; border:1px solid var(--line-soft); border-radius:var(--radius-sm); }}
    table {{ border-collapse:collapse; width:100%; min-width:600px; font-size:.86rem; }}
    th,td {{ padding:10px 13px; border-bottom:1px solid var(--line-soft); text-align:right; white-space:nowrap; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    tbody tr:hover td {{ background:#f7faff; }}
    th {{ background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; position:sticky; top:0; }}
    th.left,td.left {{ text-align:left; }}
    .empty {{ color:var(--muted); padding:26px; text-align:center; }}
    code {{ background:#eef4fb; padding:2px 5px; border-radius:4px; font-size:.85em; }}
    .muted {{ color:var(--muted); font-size:.78rem; margin-left:6px; }}
    .page-path {{ font-weight:600; color:#1f2d40; word-break:break-all; }}
    table.compact {{ min-width:0; font-size:.84rem; }}
    table.compact th, table.compact td {{ padding:8px 12px; }}
    /* ---- Pager ---- */
    .pager {{ display:flex; align-items:center; justify-content:flex-end; gap:12px; margin-top:12px; }}
    .pager-btn {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:var(--radius-sm); padding:6px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s; }}
    .pager-btn:hover:not(:disabled) {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .pager-btn:disabled {{ opacity:.45; cursor:default; }}
    .pager-info {{ font-size:.8rem; color:var(--muted); font-weight:600; }}
    /* ---- Trend chart ---- */
    .chart-wrap {{ position:relative; border:1px solid var(--line-soft); border-radius:10px; padding:10px 12px; background:#fafcff; }}
    .trend-svg {{ width:100%; height:260px; display:block; }}
    .chart-note {{ font-size:.74rem; color:var(--muted); margin-top:8px; }}
    .chart-tip {{ position:absolute; pointer-events:none; background:#0b1020; color:#e8eefc; font-size:.74rem; line-height:1.5; padding:7px 9px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,.25); transform:translate(-50%,-112%); white-space:nowrap; z-index:5; }}
    .metric-swatch {{ width:10px; height:10px; border-radius:2px; display:inline-block; vertical-align:middle; margin-right:4px; }}
    /* ---- Bar lists ---- */
    .bar-row {{ display:flex; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid var(--line-soft); }}
    .bar-row:last-child {{ border-bottom:0; }}
    .bar-label {{ flex:0 0 160px; font-size:.84rem; color:var(--navy); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .bar-track {{ flex:1 1 auto; height:7px; background:var(--line-soft); border-radius:4px; overflow:hidden; }}
    .bar-fill {{ height:100%; background:var(--accent); border-radius:4px; transition:width .3s; }}
    .bar-count {{ flex:0 0 100px; font-size:.82rem; color:var(--navy); text-align:right; font-variant-numeric:tabular-nums; }}
    .bar-pct {{ color:var(--muted); font-size:.74rem; margin-left:4px; }}
    /* ---- Layout cols ---- */
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }}
    .three-col {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; margin-top:14px; }}
    .col-panel {{ border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:14px 15px; background:#fafcff; }}
    .col-panel h3 {{ margin:0 0 12px; font-size:.86rem; font-weight:750; color:var(--navy); }}
    .subsec-h3 {{ margin:16px 0 10px; font-size:.86rem; font-weight:750; color:var(--navy); }}
    .trend-sm-svg {{ width:100%; height:130px; display:block; }}
    /* ---- Funnel ---- */
    .funnel-bar {{ display:flex; flex-direction:column; gap:8px; }}
    .funnel-step {{ display:flex; align-items:center; gap:12px; }}
    .funnel-step-label {{ flex:0 0 110px; font-size:.82rem; color:var(--navy); font-weight:600; }}
    .funnel-step-track {{ flex:1 1 auto; height:22px; background:var(--line-soft); border-radius:6px; overflow:hidden; }}
    .funnel-step-fill {{ height:100%; background:var(--accent); border-radius:6px; display:flex; align-items:center; padding-left:10px; font-size:.76rem; color:#fff; font-weight:700; min-width:2px; }}
    .funnel-step-count {{ flex:0 0 56px; font-size:.82rem; color:var(--navy); text-align:right; font-weight:700; }}
    /* ---- Explorer tree ---- */
    .indent1 {{ display:inline-block; width:18px; }}
    .indent2 {{ display:inline-block; width:36px; }}
    .tree-row[data-expandable] {{ cursor:pointer; }}
    .tree-row[data-expandable]:hover {{ background:#f3f8ff; }}
    .caret {{ display:inline-block; width:14px; color:var(--muted); font-size:.8rem; }}
    .tree-row[data-expandable] .caret::before {{ content:'\\25B8'; }}
    .tree-row[data-expandable].open .caret::before {{ content:'\\25BE'; }}
    .lvl-campaign .tree-name {{ font-weight:800; color:var(--navy); }}
    .lvl-group .tree-name {{ font-weight:600; }}
    .lvl-ad td.left {{ color:var(--muted); }}
    .pill {{ display:inline-block; padding:1px 7px; border-radius:999px; font-size:.64rem; font-weight:800; letter-spacing:.03em; text-transform:uppercase; vertical-align:middle; margin-right:7px; }}
    .pill-google {{ background:#e8f0fe; color:#1a73e8; }}
    .pill-linkedin {{ background:#e6f0f8; color:#0a66c2; }}
    .pill-meta {{ background:#f0e8fe; color:#7b2ff7; }}
    .ad-cell {{ display:inline-flex; align-items:center; gap:9px; vertical-align:middle; }}
    .ad-thumb {{ width:34px; height:34px; border-radius:5px; object-fit:cover; border:1px solid var(--line); background:#f0f3f8; flex:0 0 auto; }}
    .ad-meta {{ display:flex; flex-direction:column; line-height:1.25; }}
    .ad-label {{ font-weight:600; color:#1f2d40; }}
    .ad-type {{ font-size:.68rem; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }}
    .ad-copy {{ display:flex; flex-direction:column; gap:1px; margin-top:3px; }}
    .ad-copy-line {{ font-size:.78rem; color:var(--muted); white-space:normal; }}
    .ad-copy-tag {{ display:inline-block; min-width:34px; color:#9aa7bd; font-weight:700; font-size:.66rem; text-transform:uppercase; margin-right:5px; }}
    /* ---- Pages search ---- */
    .page-search {{ width:100%; border:1px solid var(--line); border-radius:var(--radius-sm); padding:8px 12px; font:inherit; font-size:.88rem; background:#fff; color:#102033; margin-bottom:10px; }}
    .page-search:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    /* ---- New vs returning ---- */
    .nr-wrap {{ display:flex; align-items:center; gap:20px; padding:12px 14px; border:1px solid var(--line-soft); border-radius:var(--radius-sm); background:#f9fbff; margin-bottom:14px; flex-wrap:wrap; }}
    .nr-stat {{ display:flex; flex-direction:column; gap:2px; min-width:80px; }}
    .nr-stat-label {{ font-size:.67rem; text-transform:uppercase; letter-spacing:.05em; font-weight:800; color:var(--muted); }}
    .nr-stat-value {{ font-size:1.2rem; font-weight:800; color:var(--navy); line-height:1.1; }}
    .nr-stat-pct {{ font-size:.74rem; color:var(--muted); }}
    .nr-bar-wrap {{ flex:1 1 180px; }}
    .nr-bar {{ height:10px; border-radius:5px; overflow:hidden; display:flex; }}
    .nr-bar-new {{ background:#1d6fd0; height:100%; transition:width .3s; }}
    .nr-bar-ret {{ background:#c3d9f5; height:100%; transition:width .3s; }}
    .nr-legend {{ display:flex; gap:14px; margin-top:5px; }}
    .nr-legend-item {{ display:flex; align-items:center; gap:5px; font-size:.74rem; color:var(--muted); }}
    .nr-legend-swatch {{ width:10px; height:10px; border-radius:2px; }}
    @media (max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,minmax(120px,1fr)); }} .two-col,.three-col {{ grid-template-columns:1fr; }} }}
    /* ---- Skeleton loaders ---- */
    @keyframes shimmer {{ 0%{{background-position:-200% 0}} 100%{{background-position:200% 0}} }}
    .skel {{ display:block; background:linear-gradient(90deg,#eef2f7 25%,#e4eaf2 50%,#eef2f7 75%); background-size:200% 100%; animation:shimmer 1.4s ease-in-out infinite; border-radius:5px; }}
    .skel-chart {{ border-radius:10px; }}
  </style>
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    {sidebar_html}
    <div class="dash-main">
  <main>

    <!-- Date / filter bar -->
    <div class="date-bar">
      <div class="date-bar-bottom">
        <div class="filter-group">
          <span class="filter-label">Range</span>
          <div class="chips" id="datePresets">
            <button type="button" class="chip" data-preset="this_month">This month</button>
            <button type="button" class="chip" data-preset="last_month">Last month</button>
            <button type="button" class="chip" data-preset="last_7">Last 7d</button>
            <button type="button" class="chip" data-preset="last_30">Last 30d</button>
            <button type="button" class="chip" data-preset="last_90">Last 90d</button>
          </div>
        </div>
        <div class="filter-group" id="platformFilterGroup">
          <span class="filter-label">Platform</span>
          <div class="chips" id="platformChips"></div>
        </div>
      </div>
    </div>

    <!-- ===== OVERVIEW TAB ===== -->
    <div id="pane-overview">
      <section id="sec-overview">
        <div class="sec-head"><h2>Summary</h2><span class="status" id="summaryStatus"></span></div>
        <div class="cards" id="summaryCards"></div>
      </section>

      <section>
        <div class="sec-head"><h2>Trends</h2><span class="status" id="chartStatus"></span></div>
        <div class="filter-group" style="margin-bottom:12px">
          <span class="filter-label">Metrics</span>
          <div class="chips" id="metricChips"></div>
        </div>
        <div class="chart-wrap" id="trendChartWrap">
          <svg id="trendChart" class="trend-svg" preserveAspectRatio="none"></svg>
          <div id="chartTip" class="chart-tip" hidden></div>
        </div>
        <p class="chart-note">Each line is normalized to its own min–max. Hover for actual values.</p>
      </section>

      <section>
        <div class="sec-head"><h2>Data health</h2><span class="status" id="healthStatus"></span></div>
        <div class="table-wrap"><table id="healthTable"></table></div>
      </section>
    </div>

    <!-- ===== EXPLORER TAB ===== -->
    <div id="pane-explorer" hidden>
      <section id="sec-explorer">
        <div class="sec-head"><h2>Campaign explorer</h2><span class="status" id="explorerStatus"></span></div>
        <div style="display:flex; flex-wrap:wrap; gap:16px; margin-bottom:12px;" id="explorerFilters">
          <div class="filter-group">
            <span class="filter-label">Product</span>
            <div class="chips" id="productChips"></div>
          </div>
          <div class="filter-group">
            <span class="filter-label">Region</span>
            <div class="chips" id="regionChips"></div>
          </div>
        </div>
        <div class="table-wrap"><table id="explorerTable"></table></div>
      </section>
    </div>

    <!-- ===== WEBSITE ANALYTICS TAB ===== -->
    <div id="pane-analytics" hidden>

      <section id="sec-pages">
        <div class="sec-head"><h2>Top pages</h2><span class="status" id="pagesStatus"></span></div>
        <div style="display:flex; flex-wrap:wrap; gap:16px; margin-bottom:10px;" id="pageFilters">
          <div class="filter-group"><span class="filter-label">AI platform</span><div class="chips" id="aiChips"></div></div>
          <div class="filter-group"><span class="filter-label">Paid source</span><div class="chips" id="sourceChips"></div></div>
        </div>
        <input class="page-search" id="pagesSearch" type="search" placeholder="Filter by path…" autocomplete="off">
        <div class="table-wrap"><table id="pagesTable" class="compact"></table></div>
        <div class="pager" id="pagesPager"></div>
      </section>

      <section id="sec-traffic">
        <div class="sec-head"><h2>Traffic</h2><span class="status" id="trafficAcqStatus"></span></div>
        <div class="two-col">
          <div class="col-panel"><h3>Sessions over time</h3><svg id="sessionsTrendChart" class="trend-sm-svg" preserveAspectRatio="none"></svg></div>
          <div class="col-panel"><h3>By channel</h3><div id="channelBars"></div></div>
        </div>
        <h3 class="subsec-h3">Top sources / medium</h3>
        <div class="table-wrap"><table id="sourcesTable" class="compact"></table></div>
      </section>

      <section id="sec-audience">
        <div class="sec-head"><h2>Audience</h2><span class="status" id="deviceStatus"></span></div>
        <div class="col-panel" style="max-width:420px"><h3>Device type</h3><div id="deviceBars"></div></div>
      </section>

      <section id="sec-landing">
        <div class="sec-head"><h2>Landing pages</h2><span class="status" id="landingStatus"></span></div>
        <div class="table-wrap"><table id="landingTable" class="compact"></table></div>
        <div class="pager" id="landingPager"></div>
      </section>

      <section id="sec-conversions">
        <div class="sec-head"><h2>Conversions</h2><span class="status" id="conversionsStatus"></span></div>
        <div class="two-col">
          <div class="col-panel"><h3>Key events</h3><div id="eventBars"></div></div>
          <div class="col-panel"><h3>Form funnel</h3><div id="funnelChart" class="funnel-bar"></div></div>
        </div>
      </section>

      <section id="sec-useracq">
        <div class="sec-head"><h2>New user acquisition</h2><span class="status" id="userAcqStatus"></span></div>
        <div id="newVsReturning"></div>
        <div class="two-col">
          <div class="col-panel"><h3>By first channel</h3><div id="userAcqChannelBars"></div></div>
          <div class="col-panel">
            <h3>By first source / medium</h3>
            <div class="table-wrap"><table id="userAcqSourceTable" class="compact"></table></div>
          </div>
        </div>
      </section>

      <section id="sec-demographics">
        <div class="sec-head"><h2>Demographics</h2><span class="status" id="demoStatus"></span></div>
        <div class="three-col">
          <div class="col-panel">
            <h3>Top cities</h3>
            <div class="table-wrap"><table id="citiesTable" class="compact"></table></div>
          </div>
          <div class="col-panel"><h3>Age bracket</h3><div id="ageBars"></div></div>
          <div class="col-panel"><h3>Gender</h3><div id="genderBars"></div></div>
        </div>
      </section>

    </div><!-- /pane-analytics -->

    <div id="pane-gsc" hidden>
      <section id="sec-gsc-overview">
        <h2>Search Console</h2>
        <p class="src-note"><code>GET /api/clients/nixon/gsc/summary</code><span class="arrow">→</span><code>raw_gsc.fact_gsc_query_daily / fact_gsc_page_daily</code></p>
        <div class="status" id="gscStatus">Waiting…</div>
        <div class="cards" id="gscKpis"></div>
      </section>
      <section>
        <h2>Top queries</h2>
        <div class="table-wrap"><table id="gscQueriesTable" class="compact"></table></div>
      </section>
      <section>
        <h2>Top pages</h2>
        <div class="table-wrap"><table id="gscPagesTable" class="compact"></table></div>
      </section>
    </div><!-- /pane-gsc -->

  </main>
    </div>
  </div>
  <script>{dashboard_topbar_js()}</script>
  <script>
    // ---- Skeleton helpers ----
    const _SW=['50%','76%','38%','90%','62%','44%','70%'];
    function skelCards(n){{return Array.from({{length:n}},()=>`<div class="card" style="display:flex;flex-direction:column;gap:10px"><div class="skel" style="height:10px;width:52%"></div><div class="skel" style="height:24px;width:66%"></div></div>`).join('');}}
    function skelBars(n){{return Array.from({{length:n}},(_,i)=>`<div class="bar-row"><div class="skel" style="height:12px;width:${{_SW[i%_SW.length]}};flex:0 0 auto;min-width:60px"></div><div class="skel bar-track" style="height:7px"></div><div class="skel" style="height:12px;width:40px;flex-shrink:0"></div></div>`).join('');}}
    function skelTable(cols,rows){{const ths=Array.from({{length:cols}},()=>`<th></th>`).join('');const ws=['55%','80%','40%','92%','65%'];const trs=Array.from({{length:rows}},(_,i)=>`<tr>${{Array.from({{length:cols}},(_,j)=>`<td><div class="skel" style="height:12px;width:${{ws[(i*cols+j)%ws.length]}}"></div></td>`).join('')}}</tr>`).join('');return`<thead><tr>${{ths}}</tr></thead><tbody>${{trs}}</tbody>`;}}
    function skelChart(svgId,cssClass){{const svg=document.getElementById(svgId);if(!svg)return;const d=document.createElement('div');d.id='sk_'+svgId;d.className='skel skel-chart '+cssClass;svg.parentElement.insertBefore(d,svg);svg.style.visibility='hidden';}}
    function clearSkelChart(svgId){{const s=document.getElementById('sk_'+svgId);if(s)s.remove();const svg=document.getElementById(svgId);if(svg)svg.style.visibility='';}}

    // ---- API constants ----
    const SUMMARY_API          = "{_aurl('/api/clients/nixon/summary')}";
    const HEALTH_API           = "{_aurl('/api/clients/nixon/marketing/health')}";
    const EXPLORER_API         = "{_aurl('/api/clients/nixon/google-ads/explorer')}";
    const LINKEDIN_EXPLORER_API= "{_aurl('/api/clients/nixon/linkedin/explorer')}";
    const META_EXPLORER_API    = "{_aurl('/api/clients/nixon/meta/explorer')}";
    const BACKFILL_API         = "{_aurl('/api/clients/nixon/backfill-linkedin')}";
    const PAGES_TOP_API        = "{_aurl('/api/clients/nixon/pages/top')}";
    const PAGES_SOURCES_API    = "{_aurl('/api/clients/nixon/pages/sources')}";
    const TRAFFIC_ACQ_API      = "{_aurl('/api/clients/nixon/pages/traffic-acquisition')}";
    const DEVICE_SPLIT_API     = "{_aurl('/api/clients/nixon/pages/device-split')}";
    const LANDING_PAGES_API    = "{_aurl('/api/clients/nixon/pages/landing')}";
    const CONVERSIONS_API      = "{_aurl('/api/clients/nixon/analytics/conversions')}";
    const USER_ACQ_API         = "{_aurl('/api/clients/nixon/analytics/user-acquisition')}";
    const DEMOGRAPHICS_API     = "{_aurl('/api/clients/nixon/analytics/demographics')}";
    const GSC_API              = "{_aurl('/api/clients/nixon/gsc/summary')}";

    // ---- Formatters ----
    const dollars = new Intl.NumberFormat('en-US', {{ style:'currency', currency:'USD', maximumFractionDigits:2 }});
    const nums    = new Intl.NumberFormat('en-US');
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const num  = v => Number(v || 0);
    const money  = v => dollars.format(num(v));
    const count  = v => nums.format(Math.round(num(v)));
    const pct    = v => `${{num(v).toFixed(2)}}%`;
    function fmtDuration(secs) {{
      secs = Math.round(num(secs));
      if (secs < 60) return secs + 's';
      const m = Math.floor(secs / 60), s = secs % 60;
      return m + 'm ' + (s < 10 ? '0' : '') + s + 's';
    }}
    function withDates(base) {{
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + currentStart + '&end_date=' + currentEnd;
    }}
    async function getJson(url) {{
      const resp = await fetch(url, {{ credentials:'same-origin' }});
      const body = await resp.json().catch(() => ({{ detail:resp.statusText }}));
      if (!resp.ok) throw new Error(body.detail || resp.statusText || 'Request failed');
      return body;
    }}
    function setStatus(id, text, isError) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.className = isError ? 'status error' : 'status';
      el.style.display = text ? '' : 'none';
    }}
    function renderTable(id, columns, rows, emptyText) {{
      const el = document.getElementById(id);
      if (!rows || !rows.length) {{ el.innerHTML = `<tbody><tr><td class="empty">${{esc(emptyText)}}</td></tr></tbody>`; return; }}
      el.innerHTML =
        `<thead><tr>${{columns.map(c => `<th class="${{c.left ? 'left' : ''}}">${{esc(c.label)}}</th>`).join('')}}</tr></thead>` +
        `<tbody>${{rows.map(row => `<tr>${{columns.map(c => `<td class="${{c.left ? 'left' : ''}}">${{esc(c.format ? c.format(row[c.key], row) : row[c.key])}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
    }}
    function pctBar(pct) {{
      return `<div class="bar-track"><div class="bar-fill" style="width:${{Math.min(100, pct).toFixed(1)}}%"></div></div>`;
    }}

    // ---- Tab system ----
    const TABS = ['overview', 'explorer', 'analytics', 'gsc'];
    let currentTab = 'overview';
    let analyticsLoaded = false;
    let explorerLoaded = false;
    let gscLoaded = false;

    function switchTab(tab) {{
      TABS.forEach(t => {{ document.getElementById('pane-' + t).hidden = t !== tab; }});
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      const subnav = document.getElementById('analyticsSubnav');
      if (subnav) subnav.hidden = tab !== 'analytics';
      const pf = document.getElementById('platformFilterGroup');
      if (pf) pf.hidden = tab === 'analytics' || tab === 'gsc';
      currentTab = tab;
      if (tab === 'explorer' && !explorerLoaded) {{
        explorerLoaded = true;
        loadExplorer();
      }}
      if (tab === 'analytics' && !analyticsLoaded) {{
        analyticsLoaded = true;
        applyModules();
        loadAllAnalytics();
      }}
      if (tab === 'gsc' && !gscLoaded) {{
        gscLoaded = true;
        loadGsc();
      }}
    }}

    document.querySelectorAll('.tab-btn').forEach(btn =>
      btn.addEventListener('click', () => switchTab(btn.dataset.tab))
    );

    // ---- Module system (localStorage) ----
    const ALL_MODULES = ['top_pages','traffic','audience','landing','conversions','user_acquisition','demographics'];
    const MODULE_SECTIONS = {{
      top_pages:'sec-pages', traffic:'sec-traffic', audience:'sec-audience',
      landing:'sec-landing', conversions:'sec-conversions',
      user_acquisition:'sec-useracq', demographics:'sec-demographics'
    }};

    function getModules() {{
      try {{
        const s = localStorage.getItem('nixon_analytics_modules');
        const saved = s ? JSON.parse(s) : {{}};
        return ALL_MODULES.reduce((o, k) => ({{...o, [k]: k in saved ? saved[k] : true}}), {{}});
      }} catch {{ return ALL_MODULES.reduce((o, k) => ({{...o, [k]: true}}), {{}}); }}
    }}

    function applyModules() {{
      const modules = getModules();
      ALL_MODULES.forEach(key => {{
        const sec = document.getElementById(MODULE_SECTIONS[key]);
        if (sec) sec.hidden = !modules[key];
      }});
      document.querySelectorAll('#analyticsSubnav [data-module]').forEach(a => {{
        a.hidden = !modules[a.dataset.module];
      }});
    }}

    // ---- Paid media: Summary ----
    const SUMMARY_CARDS = [
      ['spend','Spend',money],['impressions','Impressions',count],['clicks','Clicks',count],
      ['conversions','Conversions',count],['cpc','CPC',money],['cpa','CPA',money],['ctr','CTR',pct],
    ];
    const platformFilter = new Set();
    let summaryPayload = null;
    const summaryCards = document.getElementById('summaryCards');

    function selectedSummary() {{
      if (!summaryPayload) return {{}};
      const by = summaryPayload.by_source || null;
      if (!by || platformFilter.size === 0) return summaryPayload.summary || {{}};
      const acc = {{ spend:0, impressions:0, clicks:0, conversions:0 }};
      const needles = [...platformFilter].map(p => p.toLowerCase());
      for (const k of Object.keys(by)) {{
        if (!needles.some(nd => k.toLowerCase().includes(nd))) continue;
        const src = by[k];
        acc.spend += num(src.spend); acc.impressions += num(src.impressions);
        acc.clicks += num(src.clicks); acc.conversions += num(src.conversions);
      }}
      return {{ ...acc, cpc: acc.clicks ? acc.spend/acc.clicks : 0, cpa: acc.conversions ? acc.spend/acc.conversions : 0, ctr: acc.impressions ? acc.clicks/acc.impressions*100 : 0 }};
    }}
    function renderSummary() {{
      const s = selectedSummary();
      summaryCards.innerHTML = SUMMARY_CARDS.map(([key,label,format]) => `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{format(s[key])}}</div></div>`).join('');
    }}

    // ---- Trend chart ----
    const CHART_METRICS = [
      {{ key:'spend', label:'Spend', color:'#1769aa', fmt:money }},
      {{ key:'impressions', label:'Impressions', color:'#7c3aed', fmt:count }},
      {{ key:'clicks', label:'Clicks', color:'#0a7f3f', fmt:count }},
      {{ key:'cpc', label:'CPC', color:'#d97706', fmt:money }},
      {{ key:'cpa', label:'CPA', color:'#dc2626', fmt:money }},
      {{ key:'ctr', label:'CTR', color:'#0891b2', fmt:pct }},
    ];
    const chartMetrics = new Set(['spend','clicks']);
    let chartDaily = [];

    function buildChartDaily() {{
      const daily = (summaryPayload && summaryPayload.daily) ? summaryPayload.daily : [];
      const needles = platformFilter.size ? [...platformFilter].map(p => p.toLowerCase()) : null;
      const byDate = new Map();
      for (const r of daily) {{
        if (needles && !needles.some(nd => String(r.source||'').toLowerCase().includes(nd))) continue;
        let d = byDate.get(r.date);
        if (!d) {{ d = {{ date:r.date, spend:0, impressions:0, clicks:0, conversions:0 }}; byDate.set(r.date, d); }}
        d.spend += num(r.spend); d.impressions += num(r.impressions); d.clicks += num(r.clicks); d.conversions += num(r.conversions);
      }}
      const out = [...byDate.values()].sort((a,b) => a.date < b.date ? -1 : 1);
      for (const d of out) {{ d.cpc = d.clicks ? d.spend/d.clicks : 0; d.cpa = d.conversions ? d.spend/d.conversions : 0; d.ctr = d.impressions ? d.clicks/d.impressions*100 : 0; }}
      return out;
    }}
    function renderChart() {{
      chartDaily = buildChartDaily();
      clearSkelChart('trendChart');
      const svg = document.getElementById('trendChart');
      const W=800, H=260, padL=12, padR=12, padT=14, padB=26, plotW=W-padL-padR, plotH=H-padT-padB, n=chartDaily.length;
      svg.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
      if (!n) {{ svg.innerHTML=''; setStatus('chartStatus','No data for this range.'); return; }}
      const active = CHART_METRICS.filter(m => chartMetrics.has(m.key));
      const xAt = i => padL + (n===1 ? plotW/2 : (i/(n-1))*plotW);
      const parts = [
        `<line x1="${{padL}}" y1="${{padT}}" x2="${{padL}}" y2="${{padT+plotH}}" stroke="#eef2f7"/>`,
        `<line x1="${{padL}}" y1="${{padT+plotH}}" x2="${{padL+plotW}}" y2="${{padT+plotH}}" stroke="#e3e9f1"/>`,
      ];
      for (const m of active) {{
        const vals = chartDaily.map(d => num(d[m.key]));
        const mn=Math.min(...vals), mx=Math.max(...vals), span=(mx-mn)||1;
        const pts = vals.map((v,i) => `${{xAt(i).toFixed(1)}},${{(padT+(1-(v-mn)/span)*plotH).toFixed(1)}}`).join(' ');
        parts.push(`<polyline fill="none" stroke="${{m.color}}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${{pts}}"/>`);
      }}
      const lblIdx = n===1 ? [0] : [0, Math.floor((n-1)/2), n-1];
      for (const i of lblIdx) {{
        const anchor = i===0 ? 'start' : (i===n-1 ? 'end' : 'middle');
        parts.push(`<text x="${{xAt(i).toFixed(1)}}" y="${{H-8}}" font-size="10" fill="#66758f" text-anchor="${{anchor}}">${{esc(String(chartDaily[i].date).slice(5))}}</text>`);
      }}
      svg.innerHTML = parts.join('');
      setStatus('chartStatus', `${{n}} day(s) · ${{active.length}} metric(s)`);
    }}
    function buildMetricChips() {{
      const el = document.getElementById('metricChips');
      el.innerHTML = CHART_METRICS.map(m => `<button type="button" class="chip" data-key="${{m.key}}"><span class="metric-swatch" style="background:${{m.color}}"></span>${{esc(m.label)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        chartMetrics.has(btn.dataset.key) ? chartMetrics.delete(btn.dataset.key) : chartMetrics.add(btn.dataset.key);
        el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', chartMetrics.has(b.dataset.key)));
        renderChart();
      }}));
      el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', chartMetrics.has(b.dataset.key)));
    }}
    function setupChartHover() {{
      const wrap = document.getElementById('trendChartWrap');
      const svg  = document.getElementById('trendChart');
      const tip  = document.getElementById('chartTip');
      svg.addEventListener('mousemove', ev => {{
        if (!chartDaily.length) {{ tip.hidden=true; return; }}
        const rect=svg.getBoundingClientRect(), W=800, padL=12, padR=12;
        const frac=((ev.clientX-rect.left)/rect.width*W-padL)/(W-padL-padR);
        let i=Math.max(0,Math.min(chartDaily.length-1,Math.round(frac*(chartDaily.length-1))));
        const d=chartDaily[i], active=CHART_METRICS.filter(m=>chartMetrics.has(m.key));
        if (!active.length) {{ tip.hidden=true; return; }}
        tip.innerHTML = `<strong>${{esc(d.date)}}</strong>` + active.map(m=>`<br><span class="metric-swatch" style="background:${{m.color}}"></span>${{esc(m.label)}}: ${{m.fmt(d[m.key])}}`).join('');
        tip.hidden=false;
        const wr=wrap.getBoundingClientRect();
        tip.style.left=(ev.clientX-wr.left)+'px'; tip.style.top=(ev.clientY-wr.top)+'px';
      }});
      svg.addEventListener('mouseleave', () => {{ tip.hidden=true; }});
    }}
    async function loadSummary() {{
      setStatus('summaryStatus','Loading…');
      summaryCards.innerHTML = skelCards(7);
      skelChart('trendChart','trend-svg');
      try {{
        summaryPayload = await getJson(withDates(SUMMARY_API));
        renderSummary(); renderChart();
        const note = summaryPayload.by_source ? '' : ' · combined';
        setStatus('summaryStatus', `${{summaryPayload.start_date}} – ${{summaryPayload.end_date}}${{note}}`);
      }} catch(err) {{
        summaryPayload=null;
        setStatus('summaryStatus', err.message||String(err), true);
      }}
    }}
    // ---- Search Console ----
    const gscPos = v => v==null ? '—' : num(v).toFixed(1);
    const gscPct = v => v==null ? '—' : (num(v)).toFixed(2) + '%';
    function renderGscKpis(k) {{
      k = k || {{}};
      const cards = [
        ['Clicks', count(k.clicks)],
        ['Impressions', count(k.impressions)],
        ['CTR', gscPct(k.ctr)],
        ['Avg position', gscPos(k.avg_position)],
      ];
      document.getElementById('gscKpis').innerHTML = cards.map(([label,val]) =>
        `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{val}}</div></div>`).join('');
    }}
    async function loadGsc() {{
      setStatus('gscStatus','Loading…');
      document.getElementById('gscKpis').innerHTML = skelCards(4);
      document.getElementById('gscQueriesTable').innerHTML = skelTable(5,6);
      document.getElementById('gscPagesTable').innerHTML = skelTable(5,6);
      try {{
        const p = await getJson(withDates(GSC_API));
        if (!p || (!p.kpis && !(p.top_queries||[]).length && !(p.top_pages||[]).length)) {{
          renderGscKpis({{}});
          renderTable('gscQueriesTable', [{{key:'query',label:'Query',left:true}}], [], 'No Search Console data yet — it appears after the first sync backfills.');
          renderTable('gscPagesTable', [{{key:'page_url',label:'Page',left:true}}], [], 'No Search Console data yet.');
          setStatus('gscStatus','No data for this range yet.');
          return;
        }}
        renderGscKpis(p.kpis);
        const cols = (labelKey, label) => [
          {{key:labelKey,label:label,left:true}},
          {{key:'clicks',label:'Clicks',format:count}},
          {{key:'impressions',label:'Impr.',format:count}},
          {{key:'ctr',label:'CTR',format:gscPct}},
          {{key:'avg_position',label:'Position',format:gscPos}},
        ];
        renderTable('gscQueriesTable', cols('query','Query'), p.top_queries||[], 'No queries in this range.');
        renderTable('gscPagesTable', cols('page_url','Page'), p.top_pages||[], 'No pages in this range.');
        const k = p.kpis||{{}};
        setStatus('gscStatus', `${{count(k.clicks)}} clicks · ${{count(k.impressions)}} impressions`);
      }} catch(err) {{
        setStatus('gscStatus', err.message||String(err), true);
      }}
    }}
    async function loadHealth() {{
      setStatus('healthStatus','Loading…');
      document.getElementById('healthTable').innerHTML = skelTable(8,5);
      try {{
        const payload = await getJson(withDates(HEALTH_API));
        const rows = payload.rows||[];
        const SRC = {{google:'Google Ads',linkedin:'LinkedIn',google_analytics:'Google Analytics'}};
        const srcLabel = v => SRC[String(v||'').toLowerCase()]||v;
        const moneyD = v => v==null ? '—' : money(v);
        const countD = v => v==null ? '—' : count(v);
        renderTable('healthTable', [
          {{key:'source',label:'Source',left:true,format:srcLabel}},
          {{key:'row_count',label:'Rows',format:count}},
          {{key:'earliest_date',label:'Earliest',left:true}},
          {{key:'latest_date',label:'Latest',left:true}},
          {{key:'spend',label:'Spend',format:moneyD}},
          {{key:'impressions',label:'Impr.',format:countD}},
          {{key:'clicks',label:'Clicks',format:countD}},
          {{key:'conversions',label:'Conv.',format:countD}},
        ], rows, 'No mart health rows found.');
        setStatus('healthStatus', rows.length ? `${{rows.length}} source(s)` : 'No data');
      }} catch(err) {{
        setStatus('healthStatus', err.message||String(err), true);
      }}
    }}

    // ---- Explorer ----
    const METRIC_COLS = [
      {{key:'spend',label:'Spend',format:money}},
      {{key:'impressions',label:'Impr.',format:count}},
      {{key:'clicks',label:'Clicks',format:count}},
      {{key:'conversions',label:'Conv.',format:count}},
      {{key:'ctr',label:'CTR',format:pct}},
    ];
    const PRODUCT_RULES = {{Apparel:/apparel/i,Scrubs:/scrub/i,Linens:/linen/i}};
    const REGION_RULES  = {{TX:/\bTX\b/i,FL:/\bFL\b/i,MA:/\bMA\b/i}};
    const productFilter = new Set(), regionFilter = new Set();
    let explorerRows = [];

    function buildChips(containerId, keys, stateSet, onChange) {{
      const el = document.getElementById(containerId);
      el.innerHTML = ['All',...keys].map(k=>`<button type="button" class="chip" data-key="${{esc(k)}}">${{esc(k)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key=btn.dataset.key;
        if (key==='All') stateSet.clear(); else if (stateSet.has(key)) stateSet.delete(key); else stateSet.add(key);
        el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
        (onChange||renderExplorer)();
      }}));
      el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
    }}
    function explorerRowMatches(row) {{
      const name=String(row.campaign_name||'');
      const prodOk=!productFilter.size||[...productFilter].some(k=>PRODUCT_RULES[k].test(name));
      const regOk=!regionFilter.size||[...regionFilter].some(k=>REGION_RULES[k].test(name));
      const platOk=!platformFilter.size||[...platformFilter].some(k=>k.toLowerCase()===(row.platform||''));
      return prodOk&&regOk&&platOk;
    }}
    function zeroMetrics() {{ return {{spend:0,impressions:0,clicks:0,conversions:0}}; }}
    function addMetrics(acc,r) {{ acc.spend+=num(r.spend);acc.impressions+=num(r.impressions);acc.clicks+=num(r.clicks);acc.conversions+=num(r.conversions); }}
    function withCtr(m) {{ return {{...m,ctr:m.impressions?(num(m.clicks)/num(m.impressions)*100):0}}; }}
    function buildExplorerTree(rows) {{
      const campaigns=new Map();
      for (const r of rows) {{
        const cName=r.campaign_name||'—', platform=(r.platform||'google').toLowerCase(), cKey=platform+'|'+cName;
        if (!campaigns.has(cKey)) campaigns.set(cKey,{{name:cName,platform,metrics:zeroMetrics(),groups:new Map()}});
        const camp=campaigns.get(cKey);
        addMetrics(camp.metrics,r);
        const gName=r.ad_group_name||'—';
        if (!camp.groups.has(gName)) camp.groups.set(gName,{{name:gName,metrics:zeroMetrics(),ads:[]}});
        const grp=camp.groups.get(gName);
        addMetrics(grp.metrics,r);
        grp.ads.push(r);
      }}
      return new Map([...campaigns.entries()].sort((a,b)=>b[1].metrics.spend-a[1].metrics.spend));
    }}
    function metricCells(m) {{ const wc=withCtr(m); return METRIC_COLS.map(c=>`<td>${{c.format(wc[c.key])}}</td>`).join(''); }}
    function platformPill(p) {{
      const k=(p||'google').toLowerCase();
      const key=k==='linkedin'?'linkedin':k==='meta'?'meta':'google';
      const label=key==='linkedin'?'LinkedIn':key==='meta'?'Meta':'Google';
      return `<span class="pill pill-${{key}}">${{label}}</span>`;
    }}
    function parseCopyList(v) {{
      if (Array.isArray(v)) return v.filter(Boolean);
      if (typeof v==='string' && v) {{ try {{ const a=JSON.parse(v); return Array.isArray(a)?a.filter(Boolean):[]; }} catch(e) {{ return []; }} }}
      return [];
    }}
    function adCell(ad) {{
      const label=esc(ad.ad_label||ad.ad_name||'—');
      const type=ad.media_type?`<span class="ad-type">${{esc(ad.media_type)}}</span>`:'';
      const thumb=ad.thumbnail_url?`<img class="ad-thumb" src="${{esc(ad.thumbnail_url)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">` :'';
      // Full RSA copy (up to 15 headlines / 4 descriptions) from the JSON arrays;
      // fall back to the legacy flat columns for rows synced before the repull.
      let hs=parseCopyList(ad.headlines); if(!hs.length) hs=[ad.headline_1,ad.headline_2,ad.headline_3].filter(Boolean);
      let ds=parseCopyList(ad.descriptions); if(!ds.length) ds=[ad.description_1,ad.description_2].filter(Boolean);
      const pairs=[...hs.map((v,i)=>['H'+(i+1),v]),...ds.map((v,i)=>['D'+(i+1),v])];
      const copyLines=pairs.filter(([,v])=>v).map(([tag,v])=>`<span class="ad-copy-line"><span class="ad-copy-tag">${{tag}}</span>${{esc(v)}}</span>`).join('');
      const copy=copyLines?`<div class="ad-copy">${{copyLines}}</div>`:'';
      return `<div class="ad-cell">${{thumb}}<span class="ad-meta"><span class="ad-label">${{label}}</span>${{type}}${{copy}}</span></div>`;
    }}
    function renderExplorer() {{
      const filtered=explorerRows.filter(explorerRowMatches);
      const el=document.getElementById('explorerTable');
      const tree=buildExplorerTree(filtered);
      if (!tree.size) {{ el.innerHTML=`<tbody><tr><td class="empty">No campaigns match these filters.</td></tr></tbody>`; }} else {{
        const head=`<thead><tr><th class="left">Campaign / Ad group / Ad</th>${{METRIC_COLS.map(c=>`<th>${{esc(c.label)}}</th>`).join('')}}</tr></thead>`;
        let body='', cIdx=0;
        for (const camp of tree.values()) {{
          const cId='c'+(cIdx++), gCount=camp.groups.size;
          body+=`<tr class="tree-row lvl-campaign" data-id="${{cId}}" data-expandable="1"><td class="left"><span class="caret"></span>${{platformPill(camp.platform)}}<span class="tree-name">${{esc(camp.name)}}</span> <span class="muted">(${{gCount}} ad group${{gCount===1?'':'s'}})</span></td>${{metricCells(camp.metrics)}}</tr>`;
          let gIdx=0;
          for (const grp of camp.groups.values()) {{
            const gId=cId+'g'+(gIdx++), aCount=grp.ads.length;
            body+=`<tr class="tree-row lvl-group" data-id="${{gId}}" data-parent="${{cId}}" data-expandable="1" hidden><td class="left"><span class="indent1"></span><span class="caret"></span><span class="tree-name">${{esc(grp.name)}}</span> <span class="muted">(${{aCount}} ad${{aCount===1?'':'s'}})</span></td>${{metricCells(grp.metrics)}}</tr>`;
            for (const ad of grp.ads) {{ body+=`<tr class="tree-row lvl-ad" data-parent="${{gId}}" hidden><td class="left"><span class="indent2"></span>${{adCell(ad)}}</td>${{metricCells(ad)}}</tr>`; }}
          }}
        }}
        el.innerHTML=head+`<tbody>${{body}}</tbody>`;
      }}
      const filterActive=productFilter.size||regionFilter.size;
      const totalCampaigns=new Set(explorerRows.map(r=>r.campaign_name||'—')).size;
      setStatus('explorerStatus', explorerRows.length
        ? (filterActive ? `${{tree.size}} of ${{totalCampaigns}} campaign(s)` : `${{tree.size}} campaign(s) · ${{explorerRows.length}} ads`)
        : 'No campaigns found');
    }}
    function toggleExplorerRow(row) {{
      const id=row.dataset.id, table=row.closest('table'), expanded=row.classList.toggle('open');
      if (expanded) {{ table.querySelectorAll(`tr[data-parent="${{id}}"]`).forEach(c=>{{c.hidden=false;}}); }}
      else {{
        const stack=[id];
        while (stack.length) {{ const pid=stack.pop(); table.querySelectorAll(`tr[data-parent="${{pid}}"]`).forEach(c=>{{c.hidden=true;c.classList.remove('open');if(c.dataset.id)stack.push(c.dataset.id);}}); }}
      }}
    }}
    function normalizeExplorerRows(google, linkedin, meta) {{
      const out=[];
      for (const r of (google&&google.rows?google.rows:[])) {{
        out.push({{platform:'google',campaign_name:r.campaign_name,ad_group_name:r.ad_group_name,ad_label:r.ad_label,headlines:r.headlines,descriptions:r.descriptions,headline_1:r.headline_1,headline_2:r.headline_2,headline_3:r.headline_3,description_1:r.description_1,description_2:r.description_2,ad_name:r.ad_name,final_url:r.final_url,ad_type:r.ad_type,thumbnail_url:'',media_type:r.ad_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)}});
      }}
      for (const r of (linkedin&&linkedin.rows?linkedin.rows:[])) {{
        out.push({{platform:'linkedin',campaign_name:r.campaign_group_name||r.campaign_name,ad_group_name:r.campaign_name,ad_label:r.creative_name,thumbnail_url:r.thumbnail_url||r.image_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)}});
      }}
      for (const r of (meta&&meta.rows?meta.rows:[])) {{
        out.push({{platform:'meta',campaign_name:r.campaign_name,ad_group_name:r.adset_name,ad_label:r.ad_name,thumbnail_url:r.thumbnail_url||r.image_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)}});
      }}
      return out;
    }}
    async function loadExplorer() {{
      setStatus('explorerStatus','Loading…');
      document.getElementById('explorerTable').innerHTML = skelTable(6,8);
      const [g,l,m]=await Promise.all([
        getJson(withDates(EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(LINKEDIN_EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(META_EXPLORER_API)).catch(()=>({{rows:[]}})),
      ]);
      explorerRows=normalizeExplorerRows(g,l,m);
      renderExplorer();
    }}

    // ---- GA4: Top pages ----
    let pagesTopRows=[], pagesSourceRows=[], pagesSearchQuery='';
    const paidSourceFilter=new Set(), aiPlatformFilter=new Set();
    const PAGES_PER_PAGE=10; let pagesPageNum=1;
    const PAID_SOURCE_LABELS={{paid_google:'Google',paid_bing:'Bing',paid_linkedin:'LinkedIn',paid_meta:'Meta',paid_facebook:'Facebook'}};
    function paidLabel(src) {{ return PAID_SOURCE_LABELS[src]||String(src).replace(/^paid_/,'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()); }}
    function pageFiltersActive() {{ return paidSourceFilter.size>0||aiPlatformFilter.size>0; }}
    function pageSourceRowMatches(r) {{
      if (aiPlatformFilter.size&&!aiPlatformFilter.has(r.ai_platform)) return false;
      if (paidSourceFilter.size&&!paidSourceFilter.has(r.source_platform)) return false;
      return true;
    }}
    function aggregatePages(rows) {{
      const map=new Map();
      for (const r of rows) {{
        let g=map.get(r.page_path);
        if (!g) {{ g={{page_path:r.page_path,page_group:r.page_group,page_topic:r.page_topic,page_views:0,users:0,sessions:0,engagement_seconds:0,key_events:0}}; map.set(r.page_path,g); }}
        g.page_views+=num(r.page_views);g.users+=num(r.users);g.sessions+=num(r.sessions);g.engagement_seconds+=num(r.engagement_seconds);g.key_events+=num(r.key_events);
      }}
      return [...map.values()].sort((a,b)=>b.page_views-a.page_views);
    }}
    function renderPages() {{
      let base=pageFiltersActive()?aggregatePages(pagesSourceRows.filter(pageSourceRowMatches)):pagesTopRows;
      if (pagesSearchQuery) {{ const q=pagesSearchQuery.toLowerCase(); base=base.filter(p=>p.page_path.toLowerCase().includes(q)); }}
      const el=document.getElementById('pagesTable');
      if (!base.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No pages match${{pagesSearchQuery?' "'+esc(pagesSearchQuery)+'"':''}}.</td></tr></tbody>`; setStatus('pagesStatus','No results'); document.getElementById('pagesPager').innerHTML=''; return; }}
      const totalPages=Math.max(1,Math.ceil(base.length/PAGES_PER_PAGE));
      if (pagesPageNum>totalPages) pagesPageNum=totalPages;
      const startIdx=(pagesPageNum-1)*PAGES_PER_PAGE;
      const pageRows=base.slice(startIdx,startIdx+PAGES_PER_PAGE);
      el.innerHTML=`<thead><tr><th class="left">Page</th><th>Views</th><th>Users</th><th>Key events</th><th>Avg engt</th></tr></thead>`+
        `<tbody>${{pageRows.map(p=>{{const sub=p.page_group?` <span class="muted">${{esc(p.page_group)}}</span>`:'';const engt=p.users?p.engagement_seconds/p.users:0;return`<tr><td class="left"><span class="page-path">${{esc(p.page_path)}}</span>${{sub}}</td><td>${{count(p.page_views)}}</td><td>${{count(p.users)}}</td><td>${{count(p.key_events)}}</td><td>${{fmtDuration(engt)}}</td></tr>`;}}). join('')}}</tbody>`;
      const tag=pageFiltersActive()||pagesSearchQuery?' (filtered)':'';
      setStatus('pagesStatus', `${{startIdx+1}}–${{startIdx+pageRows.length}} of ${{base.length}}${{tag}}`);
      renderPagesPager(totalPages);
    }}
    function renderPagesPager(totalPages) {{
      const pager=document.getElementById('pagesPager');
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" id="pagesPrev"${{pagesPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{pagesPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" id="pagesNext"${{pagesPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      const prev=document.getElementById('pagesPrev'), next=document.getElementById('pagesNext');
      if (prev) prev.onclick=()=>{{if(pagesPageNum>1){{pagesPageNum--;renderPages();}}}};
      if (next) next.onclick=()=>{{if(pagesPageNum<totalPages){{pagesPageNum++;renderPages();}}}};
    }}
    function buildMultiChips(containerId, entries, stateSet) {{
      const el=document.getElementById(containerId);
      el.innerHTML=[['__all__','All'],...entries].map(([v,l])=>`<button type="button" class="chip" data-key="${{esc(v)}}">${{esc(l)}}</button>`).join('');
      const sync=()=>el.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b.dataset.key==='__all__'?stateSet.size===0:stateSet.has(b.dataset.key)));
      el.querySelectorAll('.chip').forEach(btn=>btn.addEventListener('click',()=>{{
        const key=btn.dataset.key;
        if(key==='__all__')stateSet.clear();else if(stateSet.has(key))stateSet.delete(key);else stateSet.add(key);
        sync();pagesPageNum=1;renderPages();
      }}));
      sync();
    }}
    function buildPageFilters() {{
      const aiPlatforms=[...new Set(pagesSourceRows.map(r=>r.ai_platform).filter(Boolean))].sort();
      buildMultiChips('aiChips',aiPlatforms.map(p=>[p,p]),aiPlatformFilter);
      const paidSources=[...new Set(pagesSourceRows.map(r=>r.source_platform).filter(s=>s&&s.startsWith('paid_')))].sort();
      buildMultiChips('sourceChips',paidSources.map(s=>[s,paidLabel(s)]),paidSourceFilter);
    }}
    async function loadPages() {{
      setStatus('pagesStatus','Loading…');
      document.getElementById('pagesTable').innerHTML = skelTable(5,8);
      const [top,src]=await Promise.all([
        getJson(withDates(PAGES_TOP_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(PAGES_SOURCES_API)).catch(()=>({{rows:[]}})),
      ]);
      pagesTopRows=top.rows||[]; pagesSourceRows=src.rows||[]; pagesPageNum=1;
      buildPageFilters(); renderPages();
    }}
    (function(){{
      const inp=document.getElementById('pagesSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{{ clearTimeout(debounce); debounce=setTimeout(()=>{{pagesSearchQuery=inp.value.trim();pagesPageNum=1;renderPages();}},180); }});
    }})();

    // ---- GA4: Traffic acquisition ----
    function drawSessionsTrend(daily) {{
      clearSkelChart('sessionsTrendChart');
      const svg=document.getElementById('sessionsTrendChart');
      const W=800,H=130,padL=10,padR=10,padT=8,padB=24,plotW=W-padL-padR,plotH=H-padT-padB,n=daily.length;
      svg.setAttribute('viewBox',`0 0 ${{W}} ${{H}}`);
      if (!n) {{ svg.innerHTML=''; return; }}
      const vals=daily.map(d=>num(d.sessions));
      const mn=Math.min(...vals),mx=Math.max(...vals),span=(mx-mn)||1;
      const xAt=i=>padL+(n===1?plotW/2:(i/(n-1))*plotW);
      const yAt=v=>padT+(1-(v-mn)/span)*plotH;
      const pts=vals.map((v,i)=>`${{xAt(i).toFixed(1)}},${{yAt(v).toFixed(1)}}`).join(' ');
      const fillPts=`${{padL}},${{padT+plotH}} ${{pts}} ${{(padL+plotW).toFixed(1)}},${{padT+plotH}}`;
      const lblIdx=n===1?[0]:[0,Math.floor((n-1)/2),n-1];
      svg.innerHTML=[
        `<line x1="${{padL}}" y1="${{padT}}" x2="${{padL}}" y2="${{padT+plotH}}" stroke="#eef2f7"/>`,
        `<line x1="${{padL}}" y1="${{padT+plotH}}" x2="${{padL+plotW}}" y2="${{padT+plotH}}" stroke="#e3e9f1"/>`,
        `<polygon fill="rgba(29,111,208,.1)" points="${{fillPts}}"/>`,
        `<polyline fill="none" stroke="#1d6fd0" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${{pts}}"/>`,
        ...lblIdx.map(i=>{{const anchor=i===0?'start':(i===n-1?'end':'middle');return`<text x="${{xAt(i).toFixed(1)}}" y="${{H-5}}" font-size="10" fill="#66758f" text-anchor="${{anchor}}">${{esc(String(daily[i].date).slice(5))}}</text>`;}}),
      ].join('');
    }}
    function renderBarList(containerId, rows, valueKey, labelKey) {{
      const el=document.getElementById(containerId);
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; return; }}
      const total=rows.reduce((s,r)=>s+num(r[valueKey]),0);
      el.innerHTML=rows.map(r=>{{const p=total?num(r[valueKey])/total*100:0;return`<div class="bar-row"><div class="bar-label">${{esc(r[labelKey])}}</div>${{pctBar(p)}}<div class="bar-count">${{count(r[valueKey])}}<span class="bar-pct">${{p.toFixed(0)}}%</span></div></div>`;}}).join('');
    }}
    async function loadTrafficAcq() {{
      setStatus('trafficAcqStatus','Loading…');
      document.getElementById('channelBars').innerHTML = skelBars(5);
      skelChart('sessionsTrendChart','trend-sm-svg');
      document.getElementById('sourcesTable').innerHTML = skelTable(6,6);
      try {{
        const payload=await getJson(withDates(TRAFFIC_ACQ_API));
        renderBarList('channelBars',payload.by_channel||[],'sessions','channel');
        drawSessionsTrend(payload.daily||[]);
        renderTable('sourcesTable',[
          {{key:'source',label:'Source',left:true}},
          {{key:'medium',label:'Medium',left:true}},
          {{key:'sessions',label:'Sessions',format:count}},
          {{key:'engaged_sessions',label:'Engaged',format:count}},
          {{key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'}},
          {{key:'key_events',label:'Key events',format:count}},
        ], payload.by_source||[], 'No source data.');
        setStatus('trafficAcqStatus','');
      }} catch(err) {{ setStatus('trafficAcqStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Device split ----
    async function loadDeviceSplit() {{
      setStatus('deviceStatus','Loading…');
      document.getElementById('deviceBars').innerHTML = skelBars(3);
      try {{
        const payload=await getJson(withDates(DEVICE_SPLIT_API));
        renderBarList('deviceBars',payload.rows||[],'users','device');
        setStatus('deviceStatus','');
      }} catch(err) {{ setStatus('deviceStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Landing pages ----
    const LANDING_PER_PAGE=15; let landingPageNum=1, landingRows=[];
    function renderLanding() {{
      const totalPages=Math.max(1,Math.ceil(landingRows.length/LANDING_PER_PAGE));
      if (landingPageNum>totalPages) landingPageNum=totalPages;
      const startIdx=(landingPageNum-1)*LANDING_PER_PAGE, rows=landingRows.slice(startIdx,startIdx+LANDING_PER_PAGE);
      const el=document.getElementById('landingTable');
      if (!rows.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No landing page data for this range.</td></tr></tbody>`; document.getElementById('landingPager').innerHTML=''; return; }}
      el.innerHTML=`<thead><tr><th class="left">Landing page</th><th>Sessions</th><th>Users</th><th>New users</th><th>Key events</th><th>KE rate</th><th>Avg engt</th></tr></thead>`+
        `<tbody>${{rows.map(r=>`<tr><td class="left"><span class="page-path">${{esc(r.page_path)}}</span></td><td>${{count(r.sessions)}}</td><td>${{count(r.users)}}</td><td>${{count(r.new_users)}}</td><td>${{count(r.key_events)}}</td><td>${{r.key_event_rate!=null?r.key_event_rate+'%':'—'}}</td><td>${{fmtDuration(r.avg_engagement_seconds)}}</td></tr>`).join('')}}</tbody>`;
      setStatus('landingStatus',`${{startIdx+1}}–${{startIdx+rows.length}} of ${{landingRows.length}}`);
      const pager=document.getElementById('landingPager');
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" id="landingPrev"${{landingPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{landingPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" id="landingNext"${{landingPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      const prev=document.getElementById('landingPrev'), next=document.getElementById('landingNext');
      if (prev) prev.onclick=()=>{{if(landingPageNum>1){{landingPageNum--;renderLanding();}}}};
      if (next) next.onclick=()=>{{if(landingPageNum<totalPages){{landingPageNum++;renderLanding();}}}};
    }}
    async function loadLandingPages() {{
      setStatus('landingStatus','Loading…');
      document.getElementById('landingTable').innerHTML = skelTable(7,7);
      try {{
        const payload=await getJson(withDates(LANDING_PAGES_API));
        landingRows=payload.rows||[]; landingPageNum=1; renderLanding();
      }} catch(err) {{ setStatus('landingStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Conversions ----
    async function loadConversions() {{
      setStatus('conversionsStatus','Loading…');
      document.getElementById('eventBars').innerHTML = skelBars(5);
      document.getElementById('funnelChart').innerHTML = skelBars(4);
      try {{
        const payload=await getJson(withDates(CONVERSIONS_API));
        const rows=payload.rows||[];
        const el=document.getElementById('eventBars');
        if (!rows.length) {{ el.innerHTML='<div class="empty">No conversion events for this range.</div>'; }}
        else {{
          const maxCount=rows[0].event_count||1;
          el.innerHTML=rows.map(r=>{{const p=num(r.event_count)/num(maxCount)*100;return`<div class="bar-row"><div class="bar-label">${{esc(r.event_name)}}</div>${{pctBar(p)}}<div class="bar-count">${{count(r.event_count)}}</div></div>`;}}).join('');
        }}
        const funnel=payload.funnel||[];
        const funnelEl=document.getElementById('funnelChart');
        const maxStep=funnel.reduce((mx,s)=>Math.max(mx,num(s.count)),1);
        funnelEl.innerHTML=funnel.map(s=>{{
          const p=maxStep?num(s.count)/maxStep*100:0;
          return`<div class="funnel-step"><div class="funnel-step-label">${{esc(s.step)}}</div><div class="funnel-step-track"><div class="funnel-step-fill" style="width:${{p.toFixed(1)}}%">${{p>15?count(s.count):''}}</div></div><div class="funnel-step-count">${{count(s.count)}}</div></div>`;
        }}).join('');
        setStatus('conversionsStatus','');
      }} catch(err) {{ setStatus('conversionsStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: User acquisition ----
    function renderNewVsReturning(byChannel) {{
      const el=document.getElementById('newVsReturning');
      if (!el) return;
      const totalNew=byChannel.reduce((s,r)=>s+num(r.new_users),0);
      const totalActive=byChannel.reduce((s,r)=>s+num(r.active_users),0);
      const totalRet=Math.max(0,totalActive-totalNew);
      const total=totalNew+totalRet||1;
      const newPct=totalNew/total*100, retPct=totalRet/total*100;
      el.innerHTML=`<div class="nr-wrap">
        <div class="nr-stat"><span class="nr-stat-label">New users</span><span class="nr-stat-value">${{count(totalNew)}}</span><span class="nr-stat-pct">${{newPct.toFixed(0)}}%</span></div>
        <div class="nr-stat"><span class="nr-stat-label">Returning</span><span class="nr-stat-value">${{count(totalRet)}}</span><span class="nr-stat-pct">${{retPct.toFixed(0)}}%</span></div>
        <div class="nr-bar-wrap">
          <div class="nr-bar"><div class="nr-bar-new" style="width:${{newPct.toFixed(1)}}%"></div><div class="nr-bar-ret" style="width:${{retPct.toFixed(1)}}%"></div></div>
          <div class="nr-legend">
            <span class="nr-legend-item"><span class="nr-legend-swatch" style="background:#1d6fd0"></span>New</span>
            <span class="nr-legend-item"><span class="nr-legend-swatch" style="background:#c3d9f5"></span>Returning</span>
          </div>
        </div>
      </div>`;
    }}
    async function loadUserAcquisition() {{
      setStatus('userAcqStatus','Loading…');
      document.getElementById('newVsReturning').innerHTML=`<div class="nr-wrap"><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="nr-bar-wrap"><div class="skel" style="height:10px;border-radius:5px"></div></div></div>`;
      document.getElementById('userAcqChannelBars').innerHTML = skelBars(5);
      document.getElementById('userAcqSourceTable').innerHTML = skelTable(5,5);
      try {{
        const payload=await getJson(withDates(USER_ACQ_API));
        renderNewVsReturning(payload.by_channel||[]);
        renderBarList('userAcqChannelBars',payload.by_channel||[],'new_users','channel');
        renderTable('userAcqSourceTable',[
          {{key:'source',label:'Source',left:true}},
          {{key:'medium',label:'Medium',left:true}},
          {{key:'new_users',label:'New users',format:count}},
          {{key:'key_events',label:'Key events',format:count}},
          {{key:'key_event_rate',label:'KE rate',format:v=>v!=null?v+'%':'—'}},
        ], payload.by_source||[], 'No source data.');
        setStatus('userAcqStatus','');
      }} catch(err) {{ setStatus('userAcqStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Demographics ----
    async function loadDemographics() {{
      setStatus('demoStatus','Loading…');
      document.getElementById('citiesTable').innerHTML = skelTable(5,5);
      document.getElementById('ageBars').innerHTML = skelBars(5);
      document.getElementById('genderBars').innerHTML = skelBars(2);
      try {{
        const payload=await getJson(withDates(DEMOGRAPHICS_API));
        renderTable('citiesTable',[
          {{key:'city',label:'City',left:true}},
          {{key:'region',label:'Region',left:true}},
          {{key:'users',label:'Users',format:count}},
          {{key:'key_events',label:'Key events',format:count}},
          {{key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'}},
        ], payload.by_city||[], 'No city data.');
        renderBarList('ageBars',payload.by_age||[],'users','age_bracket');
        renderBarList('genderBars',payload.by_gender||[],'users','gender');
        setStatus('demoStatus','');
      }} catch(err) {{ setStatus('demoStatus',err.message||String(err),true); }}
    }}

    // ---- Loaders ----
    function loadAllAnalytics() {{
      const modules=getModules();
      if (modules.top_pages)        loadPages();
      if (modules.traffic)          loadTrafficAcq();
      if (modules.audience)         loadDeviceSplit();
      if (modules.landing)          loadLandingPages();
      if (modules.conversions)      loadConversions();
      if (modules.user_acquisition) loadUserAcquisition();
      if (modules.demographics)     loadDemographics();
    }}
    function loadCurrentTab() {{
      if (currentTab==='overview')   {{ loadSummary(); loadHealth(); }}
      else if (currentTab==='explorer') {{ explorerLoaded=false; loadExplorer(); explorerLoaded=true; }}
      else if (currentTab==='analytics') {{ analyticsLoaded=false; applyModules(); loadAllAnalytics(); analyticsLoaded=true; }}
    }}

    // ---- Date presets ----
    let currentStart='{start.isoformat()}', currentEnd='{end.isoformat()}';
    const fmtDate=d=>`${{d.getFullYear()}}-${{String(d.getMonth()+1).padStart(2,'0')}}-${{String(d.getDate()).padStart(2,'0')}}`;
    function applyPreset(name) {{
      const today=new Date(); let s, e=today;
      const lastN=n=>{{e=new Date(today);e.setDate(today.getDate()-1);s=new Date(today);s.setDate(today.getDate()-n);}};
      if (name==='this_month') s=new Date(today.getFullYear(),today.getMonth(),1);
      else if (name==='last_month') {{s=new Date(today.getFullYear(),today.getMonth()-1,1);e=new Date(today.getFullYear(),today.getMonth(),0);}}
      else if (name==='last_7') lastN(7);
      else if (name==='last_30') lastN(30);
      else if (name==='last_90') lastN(90);
      else return;
      currentStart=fmtDate(s); currentEnd=fmtDate(e);
      document.querySelectorAll('#datePresets .chip').forEach(b=>b.classList.toggle('active',b.dataset.preset===name));
      loadCurrentTab();
    }}
    document.getElementById('datePresets').addEventListener('click',ev=>{{
      const btn=ev.target.closest('[data-preset]'); if (btn) applyPreset(btn.dataset.preset);
    }});

    // ---- Platform chips ----
    buildChips('platformChips',['Google','LinkedIn','Meta'],platformFilter,()=>{{renderSummary();renderChart();renderExplorer();}});

    // ---- Explorer chips ----
    buildChips('productChips',['Apparel','Scrubs','Linens'],productFilter);
    buildChips('regionChips',['TX','FL','MA'],regionFilter);

    // ---- Init ----
    buildMetricChips();
    setupChartHover();
    document.getElementById('explorerTable').addEventListener('click',ev=>{{
      const row=ev.target.closest('tr[data-expandable]');
      if (row) toggleExplorerRow(row);
    }});
    loadSummary();
    loadHealth();

    // ---- Analytics sub-nav scrollspy ----
    (function(){{
      const links=[...document.querySelectorAll('#analyticsSubnav [data-nav]')];
      const map=new Map(links.map(a=>[a.dataset.nav,a]));
      const obs=new IntersectionObserver(entries=>{{
        entries.forEach(en=>{{
          if (en.isIntersecting&&currentTab==='analytics') {{
            links.forEach(l=>l.classList.remove('active'));
            const a=map.get(en.target.id); if (a) a.classList.add('active');
          }}
        }});
      }},{{rootMargin:'-40% 0px -55% 0px'}});
      links.forEach(a=>{{const el=document.getElementById(a.dataset.nav);if(el)obs.observe(el);}});
    }})();
  </script>
  {admin_panel_html}
  <script>
    (function(){{
      const fab=document.getElementById('adminFab');
      const panel=document.getElementById('adminPanel');
      const close=document.getElementById('adminPanelClose');
      if (!fab||!panel) return;
      fab.addEventListener('click',()=>panel.classList.toggle('open'));
      if (close) close.addEventListener('click',()=>panel.classList.remove('open'));
      document.addEventListener('keydown',e=>{{if(e.key==='Escape')panel.classList.remove('open');}});
      const refreshBtn=document.getElementById('adminRefreshBtn');
      const refreshSt=document.getElementById('adminRefreshStatus');
      if (refreshBtn) refreshBtn.addEventListener('click',()=>{{
        panel.classList.remove('open');
        if (refreshSt) {{ refreshSt.className='status'; refreshSt.textContent='Refreshing…'; setTimeout(()=>{{refreshSt.textContent='Re-runs all BQ queries for the current date range.';}},2500); }}
        loadCurrentTab();
      }});
    }})();
  </script>
</body>
</html>"""
