"""BigQuery-mart client dashboard — paid media (Overview / Explorer) + Website Analytics tabs."""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urlencode

from dashboard.renderers.base_layout import (
    SIDEBAR_CSS,
    dashboard_topbar_js,
    favicon_head_html,
    dashboard_sidebar_view_nav_html,
    render_sidebar,
    platform_nav_flags,
)
from dashboard.renderers import pagespeed_renderer


def _api_url(path: str, *, access_key: str | None) -> str:
    if not access_key:
        return path
    return f"{path}?{urlencode({'key': access_key})}"


def _docs_enabled() -> bool:
    import client_insight_documents as docs
    return docs.enabled()


# Campaign Explorer filter chips, shown when a client hasn't configured their
# own. These reproduce Nixon's long-standing chips; every other client should
# override them from Settings → Campaign explorer filters. Match semantics are
# case-insensitive substring (see explorerRowMatches in the page JS), so the
# region phrases here are slightly broader than the old \bTX\b word-boundary
# regex — acceptable for an opt-in fallback.
DEFAULT_EXPLORER_FILTERS: list[dict] = [
    {
        "id": "g0",
        "label": "Product",
        "chips": [
            {"label": "Apparel", "phrases": ["apparel"]},
            {"label": "Scrubs", "phrases": ["scrub"]},
            {"label": "Linens", "phrases": ["linen"]},
        ],
    },
    {
        "id": "g1",
        "label": "Region",
        "chips": [
            {"label": "TX", "phrases": ["tx"]},
            {"label": "FL", "phrases": ["fl"]},
            {"label": "MA", "phrases": ["ma"]},
        ],
    },
]


def parse_explorer_filters(text: str | None) -> list[dict]:
    """Parse a client's Campaign Explorer filter config into chip groups.

    Format (one rule per line):
        [Group Name]           optional section header; starts a new chip row
        Chip Label = phrase    a chip that keeps campaigns whose name contains
                               the phrase (case-insensitive substring)
        Chip Label = a, b, c   comma-separated phrases are OR'd together

    Lines before the first ``[Group]`` fall into an unnamed leading group.
    Blank lines and lines starting with ``#`` are ignored. Returns a list of
    ``{"id", "label", "chips": [{"label", "phrases": [...]}]}`` groups, empty
    if nothing valid was defined (caller falls back to DEFAULT_EXPLORER_FILTERS).
    """
    groups: list[dict] = []
    current: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {"id": f"g{len(groups)}", "label": line[1:-1].strip() or f"Group {len(groups) + 1}", "chips": []}
            groups.append(current)
            continue
        if "=" not in line:
            continue
        label, _, rhs = line.partition("=")
        label = label.strip()
        phrases = [p.strip().lower() for p in rhs.split(",") if p.strip()]
        if not label or not phrases:
            continue
        if current is None:
            current = {"id": "g0", "label": "Filter", "chips": []}
            groups.append(current)
        current["chips"].append({"label": label, "phrases": phrases})
    return [g for g in groups if g["chips"]]


def resolve_explorer_filters(text: str | None) -> list[dict]:
    """Client config if present, otherwise the built-in default chips."""
    parsed = parse_explorer_filters(text)
    return parsed or DEFAULT_EXPLORER_FILTERS


def render_bigquery_dashboard_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    client_slug: str = "nixon-bq-test",
    api_client_key: str = "nixon",
    label: str = "Nixon Medical",
    session_can_switch_clients: bool = False,
    view_as_users: list[dict] | None = None,
    show_budget_tracker: bool = True,
) -> str:
    """Render this BigQuery-mart dashboard for any client.

    client_slug drives dashboard-facing URLs (sidebar, GTM, lead tracking) and
    label is the display name. api_client_key drives the /api/clients/*
    endpoints -- kept separate from client_slug because Nixon's own routes
    still split these (marketing reads under "nixon", GSC/SEMrush/BQ routing
    under "nixon-bq-test"); any client onboarded through the generic
    /api/clients/{client_key}/* routes uses the same value for both.
    """
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=30)

    # First-run onboarding: a client with no connectors yet would otherwise see
    # a bare "no data" Overview. Show a clear call-to-action instead. Fail open
    # (assume connected) on any error so an established client never sees it.
    has_connectors = True
    # Paid-ad platforms specifically -- distinct from has_connectors, which is
    # true for GA4/GSC-only clients too. Gates the paid Summary/Trends cards
    # below so a client with no google/linkedin/meta connector (e.g. GA4+GSC
    # only) doesn't see a broken paid-media panel that has no BQ mart to read.
    has_paid_ads = True
    try:
        import connector_config_store
        configs = connector_config_store.list_configs(client_slug)
        has_connectors = bool(configs)
        has_paid_ads = any(
            c.connector_type in ("google_ads", "linkedin_ads", "meta_ads")
            and c.status not in ("not_connected", "disconnected")
            for c in configs
        )
    except Exception:
        has_connectors = True
        has_paid_ads = True
    # Search Console branded roots + target keywords (client-configurable), used
    # by the "Branded & Target Keywords" section. Stored one per line.
    gsc_branded_roots = ""
    gsc_target_keywords = ""
    ga4_key_events = ""
    explorer_filters_cfg = ""
    monthly_budget_val: float | None = None
    pagespeed_targets_stored: dict | None = None
    try:
        import client_dashboard_config as _cdc
        _kwcfg = _cdc.get_config(api_client_key) or _cdc.get_config(client_slug)
        if _kwcfg:
            gsc_branded_roots = _kwcfg.gsc_branded_roots or ""
            gsc_target_keywords = _kwcfg.gsc_target_keywords or ""
            ga4_key_events = _kwcfg.ga4_key_events or ""
            explorer_filters_cfg = _kwcfg.explorer_filters or ""
            monthly_budget_val = getattr(_kwcfg, "monthly_budget_usd", None)
        pagespeed_targets_stored = (
            _cdc.get_pagespeed_targets(api_client_key)
            or _cdc.get_pagespeed_targets(client_slug)
        )
    except Exception:
        pass
    # Effective per-KPI PageSpeed target bands (client overrides merged over
    # defaults), injected for the Site Performance tab's traffic-light coloring.
    pagespeed_targets_json = json.dumps(
        pagespeed_renderer.effective_targets(pagespeed_targets_stored)
    ).replace("<", "\\u003c")
    # Which device strategies are synced (PAGESPEED_STRATEGIES, default desktop)
    # — the Site Performance toggle renders exactly these, hiding itself when
    # there's only one.
    try:
        import pagespeed_service as _ps
        pagespeed_strategies_json = json.dumps(_ps.synced_strategies())
    except Exception:
        pagespeed_strategies_json = '["desktop"]'
    # Whether the Overview home shows the Site Performance scorecard — gated on
    # the pagespeed connector being connected, same as the sidebar nav button.
    try:
        _pf = platform_nav_flags(client_slug)
        show_pagespeed = bool(_pf.get("show_pagespeed"))
        show_semrush = bool(_pf.get("show_semrush"))
    except Exception:
        show_pagespeed = False
        show_semrush = False
    # Organic Search Intelligence (SEMrush) — only when the SEMrush connector is
    # connected; otherwise the whole section is dropped from the GSC tab.
    semrush_section_html = """
      <section id="sec-semrush">
        <div class="sec-head"><h2>Organic Search Intelligence</h2><span class="status" id="semrushStatus"></span></div>
        <div class="cards" id="semrushKpis"></div>
      </section>""" if show_semrush else ""
    # Campaign Explorer filter chips: client config if set, else Nixon defaults.
    # Injected as JSON for the page JS to build the chip rows + match campaigns.
    # Escape "<" so a chip label can't break out of the <script> block.
    explorer_filter_groups_json = json.dumps(
        resolve_explorer_filters(explorer_filters_cfg)
    ).replace("<", "\\u003c")

    connectors_url = _api_url(f"/dashboard/{client_slug}/connectors", access_key=access_key)
    onboarding_html = "" if has_connectors else f"""
      <section class="onboarding-card onboarding-steps">
        <div class="onboarding-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 3a3 3 0 00-3 3v1H9V6a3 3 0 10-3 3v1H3v2h3v1a3 3 0 103 3v-1h6v1a3 3 0 103-3v-1h3v-2h-3V9a3 3 0 000-6z"/></svg></div>
        <h2>Get your dashboard connected</h2>
        <p>Three steps to start pulling data in. Steps&nbsp;1–2 happen once in Google&nbsp;Cloud (signed in as the agency account); step&nbsp;3 is here in the portal.</p>
        <ol class="ob-steps">
          <li class="ob-step">
            <div class="ob-step-num">1</div>
            <div class="ob-step-body">
              <div class="ob-step-title">Create a BigQuery project &amp; link billing</div>
              <div class="ob-step-desc">In Google Cloud — signed in as <strong>sagefrogmarketinggroup@gmail.com</strong> — create a new GCP project for this client (or reuse an existing one) and <strong>link a billing account</strong> to it (BigQuery won't run queries without one). Note the <strong>project ID</strong> for step&nbsp;3.</div>
              <a class="ob-step-link" href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener">Create project →</a>
              <a class="ob-step-link" href="https://console.cloud.google.com/billing/linkedaccount" target="_blank" rel="noopener" style="margin-left:14px">Link billing →</a>
            </div>
          </li>
          <li class="ob-step">
            <div class="ob-step-num">2</div>
            <div class="ob-step-body">
              <div class="ob-step-title">Grant access in IAM</div>
              <div class="ob-step-desc">On that project, open <strong>IAM &amp; Admin → Grant access</strong>. Add the principal below and give it <strong>both</strong> the <strong>BigQuery Data Editor</strong> and <strong>BigQuery Job User</strong> roles (both are required — the connector step verifies them).</div>
              <div class="ob-copy">
                <code>marketing-data-reader@sagefrog.iam.gserviceaccount.com</code>
                <button type="button" class="ob-copy-btn" onclick="obCopy('marketing-data-reader@sagefrog.iam.gserviceaccount.com', this)">Copy</button>
              </div>
              <a class="ob-step-link" href="https://console.cloud.google.com/iam-admin/iam" target="_blank" rel="noopener">Open IAM →</a>
            </div>
          </li>
          <li class="ob-step">
            <div class="ob-step-num">3</div>
            <div class="ob-step-body">
              <div class="ob-step-title">Set up connectors</div>
              <div class="ob-step-desc">Back here, connect a marketing platform and enter your new <strong>project ID</strong> as the destination. We'll create the datasets and verify access — data appears after the first sync.</div>
              <a class="onboarding-cta" href="{connectors_url}">Set up connectors →</a>
            </div>
          </li>
        </ol>
        <script>
        function obCopy(t, btn){{
          navigator.clipboard.writeText(t).then(function(){{
            var o = btn.textContent; btn.textContent = 'Copied ✓'; btn.classList.add('ok');
            setTimeout(function(){{ btn.textContent = o; btn.classList.remove('ok'); }}, 1400);
          }});
        }}
        </script>
      </section>
    """

    # Section nav (Overview/Explorer/Website Analytics/Search Console as JS tabs
    # driven by switchTab() below, + connected Lead/Event Tracking as links).
    # Shared with the settings/connectors/files pages via dashboard_sidebar_view_nav_html
    # so the sidebar is identical on every page of a Nixon-style dashboard.
    view_nav_html = dashboard_sidebar_view_nav_html(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
        as_tabs=True,
    )

    sidebar_html = render_sidebar(
        client_slug=client_slug,
        label=label,
        active_nav="overview",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=_docs_enabled(),
        # Always expose the connectors nav on connector-driven dashboards so a
        # brand-new client can reach the setup wizards before any connector is
        # connected (pflags only turns it on once one already exists).
        show_connectors=True,
        view_nav_html=view_nav_html,
        session_can_switch_clients=session_can_switch_clients,
    )

    admin_class = "is-admin" if session_is_admin else ""
    _ICON_ADMIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
    admin_panel_html = ""
    if session_is_admin:
        import html as _html
        _users = view_as_users or []
        if _users:
            _opts = "".join(
                '<option value="{uid}">{email} — {role}</option>'.format(
                    uid=int(u["id"]),
                    email=_html.escape(str(u.get("email") or "")),
                    role=_html.escape(
                        str(u.get("role") or "")
                        + (f" · {u['client_slug']}" if u.get("client_slug") else "")
                    ),
                )
                for u in _users
            )
            view_as_body = f"""
        <form method="post" action="/admin/view-as" class="admin-view-as-form">
          <label for="viewAsSelect" class="admin-panel-label">View as user</label>
          <select id="viewAsSelect" name="user_id" required>
            <option value="" disabled selected>Select a user…</option>
            {_opts}
          </select>
          <button type="submit" class="primary" style="width:100%">View as this user</button>
        </form>
        <span class="status" style="display:block;margin-top:8px">See the platform exactly as this user does. A banner lets you exit.</span>"""
        else:
            view_as_body = '<p class="admin-panel-note">No other users to view as yet.</p>'
        admin_panel_html = f"""
    <button class="admin-fab" id="adminFab" title="Admin tools" aria-label="Admin tools">{_ICON_ADMIN}</button>
    <div class="admin-panel" id="adminPanel">
      <div class="admin-panel-head">
        <span class="admin-panel-title">Admin tools</span>
        <button class="admin-panel-close" id="adminPanelClose" aria-label="Close">&#x2715;</button>
      </div>
      <div class="admin-panel-body">
        <p class="admin-panel-note">{label} · admin only</p>
        {view_as_body}
      </div>
    </div>"""

    def _aurl(path: str) -> str:
        return _api_url(path, access_key=access_key)

    # Site Performance (PageSpeed Insights) tab — HTML/CSS/JS injected below. The
    # pane is always emitted (like Search Console); the sidebar nav button is what
    # gates on the pagespeed connector (see base_layout.platform_nav_flags).
    pagespeed_pane_html = pagespeed_renderer.pane_html()
    pagespeed_pane_css = pagespeed_renderer.pane_css()
    pagespeed_pane_js = pagespeed_renderer.pane_js()

    # No paid-ad connector (google/linkedin/meta) -- the paid Summary/Trends
    # cards have no BQ mart to read and would otherwise render a zeroed-out
    # panel with a table-not-found error (see e.g. Andesa: GA4 + GSC only).
    # Show a lightweight traffic/search snapshot in their place instead.
    platform_filter_group_html = "" if not has_paid_ads else """
        <div class="filter-group" id="platformFilterGroup">
          <span class="filter-label">Platform</span>
          <div class="chips" id="platformChips"></div>
        </div>"""

    # Budget tracking (Campaign Explorer, bottom): shared module, also on the
    # settings page. Only when the client runs paid ads (no spend mart otherwise)
    # AND the per-client "show on explorer" toggle is on. The inline goal editor
    # is admin-only (session_is_admin is the effective user, so a view-as client
    # correctly hides it). Lazy import avoids a circular dependency.
    from dashboard.renderers import budget_tracker as _budget_tracker
    show_budget = bool(has_paid_ads and show_budget_tracker)
    budget_section_html = (
        _budget_tracker.section_html(can_edit=session_is_admin) if show_budget else ""
    )
    budget_css = _budget_tracker.css() if show_budget else ""
    budget_scripts = (
        _budget_tracker.scripts(
            api_client_key=api_client_key,
            access_key=access_key,
            monthly_budget=monthly_budget_val,
            can_edit=session_is_admin,
            client_slug=client_slug,
        )
        if show_budget
        else ""
    )

    # Overview is a "home": the top widget from each section, each with a
    # "See more" that jumps to that tab. Panels below are shared by all clients;
    # the paid panel is prepended only when the client runs paid ads.
    ov_panels = """
      <section class="ov-panel">
        <div class="sec-head"><h2>Website analytics</h2><div class="ov-actions"><div class="chips seg" id="ovSessionsGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><button type="button" class="ov-more" aria-label="See more" data-goto="analytics"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:220px"><canvas id="ovSessionsTrend"></canvas></div></div>
        <div class="cmp-legend" id="ovSessionsLegend"></div>
      </section>

      <section class="ov-panel">
        <div class="sec-head"><h2>AI traffic</h2><div class="ov-actions"><div class="chips seg" id="ovAiGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><button type="button" class="ov-more" aria-label="See more" data-goto="ai_traffic"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:220px"><canvas id="ovAiTrend"></canvas></div></div>
        <div class="cmp-legend" id="ovAiLegend"></div>
      </section>

      <section class="ov-panel">
        <div class="sec-head"><h2>Search Console</h2><div class="ov-actions"><span class="status" id="ovGscStatus"></span><button type="button" class="ov-more" aria-label="See more" data-goto="gsc"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="two-col" style="margin-top:0">
          <div class="col-panel">
            <h3>Branded queries</h3>
            <div class="chart-canvas-host" style="height:180px"><canvas id="ovGscBrandedTrend"></canvas></div>
            <div class="muted" id="ovGscBrandedNote" style="font-size:.74rem;margin-top:6px"></div>
          </div>
          <div class="col-panel">
            <h3>Target keywords</h3>
            <div class="chart-canvas-host" style="height:180px"><canvas id="ovGscTargetTrend"></canvas></div>
            <div class="muted" id="ovGscTargetNote" style="font-size:.74rem;margin-top:6px"></div>
          </div>
        </div>
      </section>"""
    if show_pagespeed:
        ov_panels += """
      <section class="ov-panel">
        <div class="sec-head"><h2>Site performance</h2><div class="ov-actions"><span class="status" id="ovPsStatus"></span><button type="button" class="ov-more" aria-label="See more" data-goto="site_performance"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="cards" id="ovPsScores"></div>
      </section>"""
    if has_paid_ads:
        paid_panel = """
      <section id="sec-overview">
        <div class="sec-head"><h2>Paid summary</h2><div class="ov-actions"><span class="status" id="summaryStatus"></span><button type="button" class="ov-more" aria-label="See more" data-goto="explorer"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="cards" id="summaryCards"></div>
      </section>

      <section>
        <div class="sec-head"><h2>Paid trends</h2><span class="status" id="chartStatus"></span></div>
        <div class="filter-group" style="margin-bottom:12px">
          <span class="filter-label">Metrics</span>
          <div class="chips" id="metricChips"></div>
        </div>
        <div class="chart-wrap" id="trendChartWrap">
          <div class="chart-canvas-host" style="height:260px"><canvas id="trendChart"></canvas></div>
        </div>
        <p class="chart-note">Each line is normalized to its own min–max. Hover for actual values.</p>
      </section>"""
        overview_summary_html = paid_panel + ov_panels
    else:
        overview_summary_html = ov_panels

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{label}</title>
  {favicon_head_html()}
  <!-- Charts: Chart.js, vendored locally (served from /static/vendor) -->
  <script src="/static/vendor/chart.umd.min.js"></script>
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --blue:#1769aa; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    /* SIDEBAR_CSS (shared) covers .dash-sidebar* only — the flex-container
       rules that lay the sidebar and content side by side normally live in
       render_client_shell_page's own style block, which this standalone page
       doesn't use, so they're declared explicitly here instead. */
    .app-shell {{ display: flex; flex-direction: row; min-height: 100vh; }}
    .dash-main {{ flex: 1 1 auto; min-width: 0; }}
    {SIDEBAR_CSS}
    main {{ max-width:1320px; margin:0 auto; padding:20px 28px 56px; }}
    h2 {{ margin:0; color:var(--navy); font-size:1.05rem; font-weight:750; letter-spacing:-.005em; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    /* ---- Date bar (sticky) ---- */
    /* Full-bleed sticky filter header — flush to the very top of the content
       area (no gap above), spanning the full width of the main column, with an
       inner wrapper that keeps the chips aligned to the same 1320px/28px grid
       as the cards below. A hairline border + soft shadow separate it from the
       scrolling content. */
    .date-bar {{ position:sticky; top:0; z-index:50; background:var(--card); border-bottom:1px solid var(--line); box-shadow:0 1px 0 rgba(16,33,67,.04), 0 6px 16px -12px rgba(16,33,67,.28); }}
    .date-bar-inner {{ max-width:1320px; margin:0 auto; padding:13px 28px; display:flex; flex-direction:column; gap:10px; }}
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
    .admin-view-as-form {{ display:flex; flex-direction:column; gap:8px; margin:0; }}
    .admin-panel-label {{ font-size:.72rem; font-weight:700; letter-spacing:.03em; text-transform:uppercase; color:var(--muted); }}
    .admin-view-as-form select {{ width:100%; padding:9px 12px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--text); font:inherit; font-size:.86rem; }}
    .date-bar-top {{ display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }}
    .date-bar-bottom {{ display:flex; flex-wrap:wrap; gap:20px; align-items:center; }}
    label {{ display:grid; gap:5px; color:var(--muted); font-size:.68rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em; }}
    input[type=date] {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:8px 11px; font:inherit; font-size:.88rem; background:#fff; color:#102033; }}
    input[type=date]:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    button.primary {{ border:0; border-radius:var(--radius-sm); padding:9px 16px; background:var(--accent); color:#fff; font-weight:700; font-size:.88rem; cursor:pointer; box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    button.primary:hover {{ background:#1a62b8; }}
    button.primary:disabled {{ opacity:.55; cursor:default; }}
    .filter-group {{ display:flex; align-items:center; gap:8px; }}
    /* `hidden` attr must win over the display:flex above — used to scope the
       Platform filter to Overview/Explorer via switchTab()'s pf.hidden toggle. */
    .filter-group[hidden] {{ display:none; }}
    .filter-label {{ color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; white-space:nowrap; }}
    .range-select {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:8px; padding:5px 28px 5px 11px; font:inherit; font-size:.8rem; font-weight:700; cursor:pointer; appearance:none; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%230a2540' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 9px center; }}
    .range-select:hover {{ border-color:#b9c8dc; background-color:#f4f8fd; }}
    .range-select:focus {{ outline:none; border-color:var(--accent); }}
    .chips {{ display:flex; flex-wrap:wrap; gap:5px; }}
    .chip {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:4px 12px; font:inherit; font-size:.8rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s, color .12s; }}
    .chip:hover {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .chip.active {{ background:var(--navy); color:#fff; border-color:var(--navy); }}
    .chip.active:hover {{ background:#0d2c4d; }}
    /* Compact segmented toggle (e.g. Daily/Weekly) that sits on the card title line. */
    .chips.seg {{ gap:0; flex-wrap:nowrap; padding:2px; background:#f1f4f8; border:1px solid var(--line); border-radius:8px; }}
    .chips.seg .chip {{ border:0; border-radius:6px; background:none; padding:3px 12px; font-size:.76rem; color:var(--muted); }}
    .chips.seg .chip:hover {{ background:#fff; color:var(--navy); }}
    .chips.seg .chip.active {{ background:#fff; color:var(--navy); border-color:transparent; box-shadow:0 1px 2px rgba(16,33,67,.14); }}
    .chips.seg .chip.active:hover {{ background:#fff; }}
    /* ---- Key-event searchable dropdown ---- */
    .ke-dropdown {{ position:relative; display:inline-block; }}
    .ke-dd-toggle {{ display:inline-flex; align-items:center; gap:8px; min-width:190px; justify-content:space-between; border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:var(--radius-sm); padding:6px 12px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:border-color .12s; }}
    .ke-dd-toggle:hover {{ border-color:#b9c8dc; }}
    .ke-dropdown.open .ke-dd-toggle {{ border-color:var(--accent); }}
    .ke-dd-caret {{ color:var(--muted); font-size:.7rem; transition:transform .12s; }}
    .ke-dropdown.open .ke-dd-caret {{ transform:rotate(180deg); }}
    .ke-dd-panel {{ position:absolute; z-index:30; top:calc(100% + 4px); left:0; width:280px; background:#fff; border:1px solid var(--line); border-radius:var(--radius-sm); box-shadow:var(--shadow); padding:8px; }}
    .ke-dd-search {{ width:100%; box-sizing:border-box; border:1px solid var(--line); border-radius:6px; padding:7px 10px; font:inherit; font-size:.82rem; color:var(--navy); margin-bottom:6px; }}
    .ke-dd-search:focus {{ outline:none; border-color:var(--accent); }}
    .ke-dd-list {{ max-height:260px; overflow-y:auto; display:flex; flex-direction:column; gap:1px; }}
    .ke-dd-option {{ display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:6px; cursor:pointer; font-size:.82rem; font-weight:600; color:var(--navy); text-transform:none; letter-spacing:0; }}
    .ke-dd-option:hover {{ background:#f4f8fd; }}
    .ke-dd-option.active {{ background:#eaf2fd; }}
    .ke-dd-option input {{ margin:0; accent-color:var(--accent); cursor:pointer; }}
    .ke-dd-name {{ flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .ke-dd-count {{ color:var(--muted); font-size:.74rem; font-weight:600; }}
    .ke-dd-empty {{ color:var(--muted); font-size:.8rem; padding:14px 8px; text-align:center; }}
    /* ---- Explorer filter dropdowns (sticky bar) ---- */
    #explorerFilterBar {{ gap:8px; flex-wrap:wrap; }}
    .expl-dd .ke-dd-toggle {{ min-width:0; }}
    .ke-dd-toggle.has-active {{ border-color:var(--accent); color:var(--accent); }}
    /* ---- Sections ---- */
    section {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:18px 20px 20px; margin-bottom:16px; box-shadow:var(--shadow); }}
    /* ---- First-run onboarding card ---- */
    .onboarding-card {{ text-align:center; padding:40px 24px; }}
    .onboarding-icon {{ width:52px; height:52px; margin:0 auto 14px; border-radius:14px; background:#eaf2fd; color:var(--accent); display:flex; align-items:center; justify-content:center; }}
    .onboarding-icon svg {{ width:26px; height:26px; }}
    .onboarding-card h2 {{ font-size:1.15rem; margin:0 0 6px; }}
    .onboarding-card p {{ max-width:440px; margin:0 auto 18px; color:var(--muted); font-size:.9rem; line-height:1.5; }}
    .onboarding-cta {{ display:inline-block; background:var(--accent); color:#fff; text-decoration:none; font-weight:700; font-size:.9rem; padding:10px 20px; border-radius:var(--radius-sm); box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    .onboarding-cta:hover {{ background:#1a62b8; }}
    /* ---- Step-by-step onboarding checklist ---- */
    .onboarding-steps {{ text-align:left; padding:28px 26px 26px; }}
    .onboarding-steps .onboarding-icon {{ margin:0 0 14px; }}
    .onboarding-steps h2 {{ text-align:left; }}
    .onboarding-steps > p {{ text-align:left; max-width:600px; margin:0 0 22px; }}
    .ob-steps {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:20px; }}
    .ob-step {{ display:flex; gap:14px; }}
    .ob-step-num {{ flex:0 0 28px; width:28px; height:28px; border-radius:50%; background:#eaf2fd; color:var(--accent); font-weight:700; font-size:.86rem; display:flex; align-items:center; justify-content:center; }}
    .ob-step-body {{ flex:1; min-width:0; }}
    .ob-step-title {{ font-weight:700; font-size:.96rem; margin-bottom:3px; }}
    .ob-step-desc {{ color:var(--muted); font-size:.86rem; line-height:1.5; margin-bottom:9px; }}
    .ob-step-link {{ display:inline-block; color:var(--accent); font-weight:600; font-size:.84rem; text-decoration:none; }}
    .ob-step-link:hover {{ text-decoration:underline; }}
    .ob-copy {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:0 0 10px; }}
    .ob-copy code {{ background:#f6f8fb; border:1px solid var(--line); border-radius:6px; padding:5px 9px; font-size:.8rem; word-break:break-all; }}
    .ob-copy-btn {{ border:1px solid var(--line); background:var(--card); color:var(--accent); font-weight:600; font-size:.78rem; padding:5px 11px; border-radius:6px; cursor:pointer; white-space:nowrap; transition:background .15s; }}
    .ob-copy-btn:hover {{ background:#eaf2fd; }}
    .ob-copy-btn.ok {{ color:#178a4c; border-color:#178a4c; }}
    .sec-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }}
    .sec-head h2 {{ margin:0; }}
    .sec-head .status {{ margin:0; font-size:.76rem; text-align:right; flex-shrink:0; }}
    .sec-head-actions {{ display:flex; align-items:center; gap:12px; flex-shrink:0; }}
    .ov-actions {{ display:flex; align-items:center; gap:12px; flex-shrink:0; }}
    .ov-more {{ display:inline-flex; align-items:center; justify-content:center; width:30px; height:30px; border:1px solid var(--line); border-radius:999px; background:var(--card); color:var(--muted); padding:0; font:inherit; cursor:pointer; transition:color .14s, border-color .14s, background .14s, box-shadow .14s; }}
    .ov-more:hover {{ color:var(--accent); border-color:var(--accent); background:var(--card); box-shadow:0 1px 4px rgba(29,111,208,.18); }}
    .ov-more-arrow {{ width:16px; height:16px; display:block; transition:transform .14s; }}
    .ov-more:hover .ov-more-arrow {{ transform:translateX(2px); }}
    .status {{ color:var(--muted); font-size:.82rem; margin:0 0 12px; }}
    .status.error {{ color:var(--bad); }}
    /* ---- Metric cards ---- */
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(128px,1fr)); gap:10px; }}
    .card {{ border:1px solid var(--line-soft); border-top:3px solid var(--accent); border-radius:var(--radius-sm); padding:13px 14px 14px; background:#fff; }}
    .card-title {{ color:var(--muted); font-size:.65rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }}
    .card-value {{ margin-top:7px; font-size:1.5rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; }}
    .card-foot {{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:9px; min-height:22px; }}
    .card-foot .cmp-delta {{ font-size:.74rem; white-space:nowrap; }}
    .cmp-delta.flat {{ color:var(--muted); }}
    .spark {{ width:66px; height:22px; flex:0 0 auto; opacity:.85; }}
    .spark-empty {{ width:66px; height:22px; flex:0 0 auto; }}
    .card-delta {{ margin-top:4px; font-size:.76rem; font-weight:700; }}
    .card-delta.up {{ color:var(--ok); }}
    .card-delta.down {{ color:var(--bad); }}
    .card-delta.flat {{ color:var(--muted); font-weight:600; }}
    .cmp-warn {{ display:inline-block; margin-left:6px; font-size:.85rem; cursor:help; color:#b78103; }}
    .cmp-warn[hidden] {{ display:none; }}
    /* ---- Tables ---- */
    .table-wrap {{ overflow:auto; border:1px solid var(--line-soft); border-radius:var(--radius-sm); }}
    table {{ border-collapse:collapse; width:100%; min-width:600px; font-size:.86rem; }}
    th,td {{ padding:10px 13px; border-bottom:1px solid var(--line-soft); text-align:right; white-space:nowrap; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    tbody tr:hover td {{ background:#f7faff; }}
    th {{ background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; position:sticky; top:0; }}
    th.left,td.left {{ text-align:left; }}
    th.gsc-sort {{ cursor:pointer; user-select:none; white-space:nowrap; transition:background .12s,color .12s; }}
    th.gsc-sort:hover {{ background:#e9eef5; color:#33455e; }}
    th.gsc-sort.active {{ color:var(--accent); }}
    /* Δ Position movement badges (queries table) */
    .gsc-mv {{ display:inline-block; font-size:.74rem; font-weight:800; padding:2px 7px; border-radius:999px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .gsc-mv-up {{ color:#0a7f3f; background:#e7f6ee; }}
    .gsc-mv-down {{ color:#c02626; background:#fdecec; }}
    .gsc-mv-flat {{ color:#8a97a8; background:transparent; font-weight:600; }}
    .gsc-mv-new {{ color:#1769aa; background:#e9f1fb; }}
    th.expl-sort {{ cursor:pointer; user-select:none; white-space:nowrap; transition:background .12s,color .12s; }}
    th.expl-sort:hover {{ background:#e9eef5; color:#33455e; }}
    th.expl-sort.active {{ color:var(--accent); }}
    /* GSC/keyword tables: truncate the long query/URL label instead of letting
       the table overflow its column and push off the page. */
    #gscQueriesTable td.left, #gscPagesTable td.left,
    #gscBrandedTable td.left, #gscTargetTable td.left {{ max-width:0; }}
    #gscQueriesTable td.left > *, #gscPagesTable td.left > *,
    #gscBrandedTable td.left > *, #gscTargetTable td.left > * {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; word-break:normal; }}
    /* Drag-to-resize handle on the label column of the GSC/keyword tables. */
    th.col-resizable {{ position:relative; }}
    .col-resizer {{ position:absolute; top:0; right:0; width:9px; height:100%; cursor:col-resize; user-select:none; touch-action:none; }}
    .col-resizer::after {{ content:''; position:absolute; top:18%; right:3px; height:64%; width:2px; border-radius:1px; background:transparent; transition:background .12s; }}
    .col-resizer:hover::after {{ background:#b9c8dc; }}
    body.col-resizing {{ cursor:col-resize; user-select:none; }}
    body.col-resizing .col-resizer::after {{ background:var(--accent); }}
    /* Admin-only controls (shown when the app-shell has .is-admin) */
    .debug-only {{ display:none !important; }}
    .is-admin .debug-only {{ display:inline-block !important; }}
    /* Inline branded/target keyword tag editor (admin only) */
    .tag-editor {{ display:none; }}
    .is-admin .tag-editor {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:7px 9px; margin-bottom:10px; border:1px solid var(--line); border-radius:var(--radius-sm); background:#fff; }}
    .tag-editor:focus-within {{ border-color:#9bbfe6; box-shadow:0 0 0 2px #e2eefb; }}
    .tag-editor-label {{ width:100%; font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }}
    .tag-chip {{ display:inline-flex; align-items:center; gap:6px; background:#eef4fb; color:var(--navy); border-radius:999px; padding:3px 5px 3px 11px; font-size:.8rem; font-weight:650; }}
    .tag-chip button {{ border:0; background:rgba(10,37,64,.14); color:var(--navy); width:16px; height:16px; border-radius:50%; cursor:pointer; font-size:.72rem; line-height:1; display:flex; align-items:center; justify-content:center; padding:0; }}
    .tag-chip button:hover {{ background:var(--bad); color:#fff; }}
    .tag-input {{ border:0; outline:none; font:inherit; font-size:.84rem; padding:4px 2px; min-width:110px; flex:1; background:transparent; color:#102033; }}
    .tag-input::placeholder {{ color:#9aa7bd; }}
    .empty {{ color:var(--muted); padding:26px; text-align:center; }}
    code {{ background:#eef4fb; padding:2px 5px; border-radius:4px; font-size:.85em; }}
    .muted {{ color:var(--muted); font-size:.78rem; margin-left:6px; }}
    /* Cap the path label so a long URL can't balloon the column and shove the
       metric columns off the horizontal scroller (worst on mobile). Truncates
       with an ellipsis; the full path stays available via the title tooltip. */
    .page-path {{ display:inline-block; max-width:min(52vw,460px); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:bottom; font-weight:600; color:#1f2d40; }}
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
    {budget_css}
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
    /* Keyword Performance: insight banner, toolbar, match badges and in-cell bars. */
    .kw-insight {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 22px; padding:11px 15px; margin-bottom:14px; border:1px solid var(--line-soft); border-radius:var(--radius-sm); background:#f6f9fd; font-size:.82rem; color:var(--navy); }}
    .kw-insight[hidden] {{ display:none; }}
    .kw-insight .kw-ins {{ display:inline-flex; align-items:center; gap:8px; }}
    .kw-insight strong {{ font-weight:800; }}
    .kw-ins-icon {{ flex:0 0 auto; width:15px; height:15px; }}
    .kw-ins.kw-ins-warn {{ color:#8a5a00; }}
    .kw-toolbar {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px 14px; margin-bottom:14px; }}
    .kw-search {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:7px 12px; font:inherit; font-size:.84rem; color:#102033; min-width:230px; background:#fff; }}
    .kw-search:focus {{ outline:none; border-color:var(--accent); }}
    .kw-search::placeholder {{ color:#9aa7bd; }}
    .kw-name {{ font-weight:650; color:var(--navy); }}
    .kw-sub {{ color:var(--muted); font-size:.73rem; margin-top:1px; }}
    .badge-match {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.66rem; font-weight:800; letter-spacing:.02em; white-space:nowrap; }}
    .badge-exact {{ background:#e4f4ea; color:#0a7f3f; }}
    .badge-phrase {{ background:#e8f0fe; color:#1a73e8; }}
    .badge-broad {{ background:#eef2f7; color:#54637a; }}
    .cell-bar {{ display:flex; align-items:center; justify-content:flex-end; gap:9px; }}
    .cell-bar .bar-track {{ flex:0 0 auto; width:74px; height:6px; }}
    .cell-bar-val {{ font-variant-numeric:tabular-nums; }}
    .num-good {{ color:var(--ok); font-weight:650; }}
    .num-bad {{ color:var(--bad); }}
    .cell-flag {{ color:#d9a400; margin-left:6px; }}
    /* ---- Layout cols ---- */
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }}
    /* Grid items must be allowed to shrink below their content, or a wide table
       (long GSC page URLs) forces the column past the viewport. */
    .two-col > * {{ min-width:0; }}
    .three-col {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; margin-top:14px; }}
    .col-panel {{ border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:14px 15px; background:#fafcff; }}
    .col-panel h3 {{ margin:0 0 12px; font-size:.86rem; font-weight:750; color:var(--navy); }}
    .subsec-h3 {{ margin:16px 0 10px; font-size:.86rem; font-weight:750; color:var(--navy); }}
    .trend-sm-svg {{ width:100%; height:130px; display:block; }}
    .trend-md-svg {{ width:100%; height:200px; display:block; }}
    /* Chart.js canvas host: a relatively-positioned box of fixed height that
       the canvas fills (charts run with maintainAspectRatio:false). */
    .chart-canvas-host {{ position:relative; width:100%; }}
    .chart-canvas-host canvas {{ display:block; }}
    {pagespeed_pane_css}
    /* Sessions-over-time current-vs-previous legend */
    .cmp-legend {{ display:flex; flex-wrap:wrap; gap:18px; margin-top:10px; font-size:.78rem; color:var(--muted); }}
    .cmp-item {{ display:inline-flex; align-items:center; gap:7px; }}
    .cmp-swatch {{ width:16px; height:0; border-top-width:3px; border-top-style:solid; border-radius:2px; }}
    .cmp-swatch.cur {{ border-top-color:#1d6fd0; }}
    .cmp-swatch.prev {{ border-top-color:#9aa7bd; border-top-style:dashed; }}
    .cmp-delta {{ font-weight:800; }}
    .cmp-delta.up {{ color:var(--ok); }}
    .cmp-delta.down {{ color:var(--bad); }}
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
    /* Platform brand icons (campaign rows) */
    .plat-ico {{ display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; margin-right:9px; vertical-align:middle; flex:0 0 auto; }}
    .plat-ico svg {{ width:18px; height:18px; display:block; }}
    .ad-cell {{ display:inline-flex; align-items:center; gap:9px; vertical-align:middle; }}
    .ad-thumb {{ width:34px; height:34px; border-radius:5px; object-fit:cover; border:1px solid var(--line); background:#f0f3f8; flex:0 0 auto; cursor:zoom-in; }}
    .creative-preview {{ position:fixed; inset:0; z-index:1000; display:flex; align-items:center; justify-content:center; }}
    .creative-preview[hidden] {{ display:none; }}
    .creative-preview-backdrop {{ position:absolute; inset:0; background:rgba(10,20,35,.72); }}
    .creative-preview-dialog {{ position:relative; max-width:min(90vw,760px); max-height:88vh; background:#fff; border-radius:12px; padding:14px; box-shadow:0 20px 60px rgba(0,0,0,.35); display:flex; }}
    .creative-preview-body {{ display:flex; align-items:center; justify-content:center; max-width:100%; max-height:calc(88vh - 28px); }}
    .creative-preview-body img, .creative-preview-body video {{ max-width:100%; max-height:calc(88vh - 28px); border-radius:6px; display:block; }}
    .creative-preview-close {{ position:absolute; top:-14px; right:-14px; width:32px; height:32px; border-radius:50%; border:0; background:#0a2540; color:#fff; font-size:1.1rem; line-height:1; cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,.25); }}
    .ad-meta {{ display:flex; flex-direction:column; line-height:1.25; }}
    .ad-label {{ font-weight:600; color:#1f2d40; }}
    .ad-id {{ margin-left:6px; font-family:monospace; font-size:.68rem; font-weight:400; color:var(--muted); }}
    .ad-type {{ font-size:.68rem; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }}
    .ad-copy {{ display:flex; flex-direction:column; gap:1px; margin-top:3px; }}
    .ad-copy-line {{ font-size:.78rem; color:var(--muted); white-space:normal; }}
    .ad-copy-tag {{ display:inline-block; min-width:34px; color:#9aa7bd; font-weight:700; font-size:.66rem; text-transform:uppercase; margin-right:5px; }}
    .ad-copy-more {{ align-self:flex-start; margin:2px 0 1px 39px; padding:0; border:0; background:none; color:var(--accent, #2563eb); font-size:.72rem; font-weight:600; cursor:pointer; }}
    .ad-copy-more:hover {{ text-decoration:underline; }}
    .ad-copy-extra {{ display:flex; flex-direction:column; gap:1px; }}
    .ad-copy-extra[hidden] {{ display:none; }}
    /* Google search-ad preview — mimics a real SERP ad so the row reads like the
       live creative. White card + Google colours regardless of dashboard theme. */
    .ad-cell.gads {{ align-items:flex-start; }}
    .gads-preview {{ max-width:540px; padding:9px 12px; border:1px solid #e6e9ef; border-radius:9px; background:#fff; box-shadow:0 1px 2px rgba(16,33,67,.05); }}
    .gads-top {{ display:flex; align-items:center; gap:8px; margin-bottom:3px; }}
    .gads-badge {{ font-size:.62rem; font-weight:800; color:#202124; border:1px solid #202124; border-radius:4px; padding:0 4px; line-height:1.35; }}
    .gads-url {{ font-size:.74rem; color:#202124; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .gads-shuffle {{ margin-left:auto; display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; border:1px solid #dadce0; border-radius:999px; background:#fff; color:#1a73e8; cursor:pointer; flex:0 0 auto; transition:background .12s, transform .12s; }}
    .gads-shuffle svg {{ width:13px; height:13px; }}
    .gads-shuffle:hover {{ background:#f1f6ff; border-color:#a9c7f5; }}
    .gads-shuffle:active {{ transform:rotate(-90deg); }}
    /* white-space:normal overrides the table's `th,td{{white-space:nowrap}}` — without
       it the headline/description text runs on one line and overflows the card
       across the metric columns. */
    .gads-title {{ color:#1a0dab; font-size:1rem; font-weight:400; line-height:1.3; white-space:normal; overflow-wrap:anywhere; }}
    .gads-desc {{ color:#4d5156; font-size:.8rem; line-height:1.35; margin-top:2px; white-space:normal; overflow-wrap:anywhere; }}
    .ad-label-sub {{ margin-top:4px; }}
    .ad-cell.gads .ad-copy-more {{ margin-left:2px; margin-top:5px; }}
    .ad-cell.gads .ad-copy-extra {{ margin-left:2px; margin-top:3px; }}
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
    /* ---- Traffic: single 100% channel bar (hover for per-segment label) ---- */
    .stack-wrap {{ position:relative; }}
    .stack-bar {{ display:flex; width:100%; height:26px; border-radius:6px; overflow:hidden; background:var(--line-soft); }}
    .stack-seg {{ height:100%; min-width:2px; cursor:default; transition:filter .12s; }}
    .stack-seg:hover {{ filter:brightness(1.08); }}
    .stack-tip {{ position:absolute; bottom:calc(100% + 7px); transform:translateX(-50%); background:var(--navy); color:#fff; font-size:.74rem; font-weight:700; padding:5px 9px; border-radius:6px; white-space:nowrap; pointer-events:none; box-shadow:var(--shadow); z-index:5; }}
    .stack-tip[hidden] {{ display:none; }}
    /* ---- Demographics: age toggle, gender split, state tile map ---- */
    .col-panel-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin-bottom:12px; }}
    .col-panel-head h3 {{ margin:0; }}
    .age-toggle {{ display:inline-flex; align-items:center; gap:5px; font-size:.74rem; font-weight:600; color:var(--muted); cursor:pointer; text-transform:none; letter-spacing:0; }}
    .age-toggle input {{ accent-color:var(--accent); cursor:pointer; }}
    .gender-wrap {{ display:flex; flex-direction:column; gap:14px; }}
    .gender-bar {{ display:flex; width:100%; height:30px; border-radius:8px; overflow:hidden; background:var(--line-soft); }}
    .gender-seg {{ height:100%; display:flex; align-items:center; justify-content:center; color:#fff; font-size:.8rem; font-weight:800; min-width:2px; }}
    .gender-stats {{ display:flex; gap:12px; }}
    .gender-stat {{ flex:1 1 0; border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:10px 12px; display:flex; align-items:center; gap:10px; }}
    .gender-dot {{ width:12px; height:12px; border-radius:50%; flex:0 0 auto; }}
    .gender-stat-label {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; font-weight:800; color:var(--muted); }}
    .gender-stat-value {{ font-size:1.1rem; font-weight:800; color:var(--navy); line-height:1.1; }}
    .gender-stat-pct {{ font-size:.74rem; color:var(--muted); }}
    .state-map {{ width:100%; }}
    .state-map svg {{ width:100%; height:auto; display:block; }}
    .state-tile {{ transition:filter .12s; }}
    .state-tile:hover {{ filter:brightness(.92); }}
    .state-tile-label {{ font-size:8px; font-weight:700; fill:#5a6b82; pointer-events:none; }}
    .state-map-scale {{ display:flex; align-items:center; gap:8px; margin-top:10px; font-size:.72rem; color:var(--muted); }}
    .state-map-scale-bar {{ flex:1 1 auto; height:8px; border-radius:4px; background:linear-gradient(90deg,#eaf1fb,#1d6fd0); }}
    /* Sticky date bar clears the fixed 52px mobile top bar (see SIDEBAR_CSS). */
    @media (max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,minmax(120px,1fr)); }} .two-col,.three-col {{ grid-template-columns:1fr; }} .date-bar {{ top:52px; }} }}
    /* On phones give the metric columns more room: tighten the path label cap. */
    @media (max-width:640px) {{ .page-path {{ max-width:44vw; }} }}
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

    <!-- Sticky filter header (full-bleed, flush to top) -->
    <div class="date-bar">
      <div class="date-bar-inner">
      <div class="date-bar-bottom">
        <div class="filter-group">
          <span class="filter-label">Range</span>
          <select class="range-select" id="datePresets" aria-label="Date range">
            <option value="last_7">Last 7 days</option>
            <option value="last_30" selected>Last 30 days</option>
            <option value="last_90">Last 90 days</option>
            <option value="this_week">This week</option>
            <option value="last_week">Last week</option>
            <option value="this_month">This month</option>
            <option value="last_month">Last month</option>
          </select>
        </div>
        <div class="filter-group" id="keyEventFilterGroup" hidden>
          <span class="filter-label">Events</span>
          <div class="ke-dropdown" id="keyEventDropdown">
            <button type="button" class="ke-dd-toggle" id="keyEventToggle" aria-haspopup="listbox" aria-expanded="false">
              <span id="keyEventToggleLabel">All key events</span>
              <span class="ke-dd-caret">▾</span>
            </button>
            <div class="ke-dd-panel" id="keyEventPanel" hidden>
              <input type="text" class="ke-dd-search" id="keyEventSearch" placeholder="Search events…" autocomplete="off">
              <div class="ke-dd-list" id="keyEventList"></div>
            </div>
          </div>
        </div>
        {platform_filter_group_html}
        <div class="filter-group" id="explorerFilterBar" hidden></div>
      </div>
      </div>
    </div>

  <main>

    <!-- ===== OVERVIEW TAB ===== -->
    <div id="pane-overview">
      {onboarding_html}
      {overview_summary_html}
    </div>

    <!-- ===== EXPLORER TAB ===== -->
    <div id="pane-explorer" hidden>
      <section id="sec-explorer">
        <div class="sec-head"><h2>Campaign explorer</h2><span class="status" id="explorerStatus"></span></div>
        <div class="cards" id="explorerSummaryCards" style="margin-bottom:14px"></div>
        <!-- Filter groups (Product / Region / Business line …) live in the sticky
             top bar (#explorerFilterBar) as dropdowns; built by buildExplorerFilters()
             from the client-configured chip rules; see EXPLORER_FILTER_GROUPS. -->
        <div class="table-wrap"><table id="explorerTable"></table></div>
      </section>
      <section id="sec-keywords" style="display:none">
        <div class="sec-head"><h2>Keyword Performance</h2><span class="status" id="keywordStatus"></span></div>
        <div class="kw-insight" id="keywordInsight" hidden></div>
        <div class="kw-toolbar">
          <input type="search" id="keywordSearch" class="kw-search" placeholder="Search keywords…" autocomplete="off">
          <div class="chips" id="keywordMatchChips"></div>
        </div>
        <div class="table-wrap"><table id="keywordTable" class="compact"></table></div>
        <div class="pager" id="keywordPager"></div>
      </section>
      <section id="sec-paid-sources" style="display:none">
        <div class="sec-head"><h2>Paid campaign traffic</h2><span class="status" id="paidSourceStatus"></span></div>
        <p class="chart-note" style="margin-top:0">Website sessions attributed to paid channels (GA4). Expand a channel to see its campaigns, then the pages each campaign drove. Campaigns come from the <code>utm_campaign</code> on the landing session — untagged traffic shows as “(not set).”</p>
        <div class="table-wrap"><table id="paidSourceTable" class="compact"></table></div>
      </section>
      {budget_section_html}
    </div>

    <!-- ===== WEBSITE ANALYTICS TAB ===== -->
    <div id="pane-analytics" hidden>

      <section id="sec-sessions">
        <div class="sec-head"><h2>Sessions over time <span class="cmp-warn" id="sessionsCmpWarn" title="" hidden>&#9888;</span></h2><div class="sec-head-actions"><div class="chips seg" id="sessionsGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><span class="status" id="sessionsTrendStatus"></span></div></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:200px"><canvas id="sessionsTrendChart"></canvas></div></div>
        <div class="cmp-legend" id="sessionsTrendLegend"></div>
      </section>

      <div class="two-col" style="align-items:start">
      <section id="sec-pages">
        <div class="sec-head"><h2>Pages</h2><span class="status" id="pagesStatus"></span></div>
        <input class="page-search" id="pagesSearch" type="search" placeholder="Filter by path…" autocomplete="off">
        <div class="table-wrap"><table id="pagesTable" class="compact"></table></div>
        <div class="pager" id="pagesPager"></div>
      </section>

      <section id="sec-landing">
        <div class="sec-head"><h2>Landing Pages</h2><span class="status" id="landingStatus"></span></div>
        <input class="page-search" id="landingSearch" type="search" placeholder="Filter by path…" autocomplete="off">
        <div class="table-wrap"><table id="landingTable" class="compact"></table></div>
        <div class="pager" id="landingPager"></div>
      </section>
      </div>

      <section id="sec-traffic">
        <div class="sec-head"><h2>Traffic</h2><span class="status" id="trafficAcqStatus"></span></div>
        <div class="col-panel"><h3>By channel</h3><div id="channelBars"></div></div>
        <h3 class="subsec-h3">Top sources / medium</h3>
        <div class="table-wrap"><table id="sourcesTable" class="compact"></table></div>
        <div class="pager" id="sourcesPager"></div>
      </section>

      <section id="sec-audience">
        <div class="sec-head"><h2>Audience</h2><span class="status" id="deviceStatus"></span></div>
        <div class="col-panel" style="max-width:420px"><h3>Device type</h3><div id="deviceBars"></div></div>
      </section>

      <section id="sec-useracq">
        <div class="sec-head"><h2>New user acquisition</h2><span class="status" id="userAcqStatus"></span></div>
        <div id="newVsReturning"></div>
        <div class="two-col" style="align-items:start">
          <div class="col-panel"><h3>By first channel</h3><div id="userAcqChannelBars"></div><div class="pager" id="userAcqChannelPager"></div></div>
          <div class="col-panel">
            <h3>By first source / medium</h3>
            <div class="table-wrap"><table id="userAcqSourceTable" class="compact"></table></div>
            <div class="pager" id="userAcqSourcePager"></div>
          </div>
        </div>
      </section>

      <section id="sec-demographics">
        <div class="sec-head"><h2>Demographics</h2><span class="status" id="demoStatus"></span></div>
        <div class="two-col" style="align-items:start">
          <div class="col-panel">
            <h3>Users by state</h3>
            <div id="stateMap" class="state-map"></div>
          </div>
          <div class="col-panel">
            <h3>Top cities</h3>
            <div class="table-wrap"><table id="citiesTable" class="compact"></table></div>
          </div>
        </div>
        <div class="two-col" style="align-items:start; margin-top:14px">
          <div class="col-panel">
            <div class="col-panel-head"><h3>Age bracket</h3><label class="age-toggle"><input type="checkbox" id="ageUnknownToggle"> Show “unknown”</label></div>
            <div id="ageBars"></div>
          </div>
          <div class="col-panel"><h3>Gender</h3><div id="genderBars"></div></div>
        </div>
      </section>

    </div><!-- /pane-analytics -->

    <!-- ===== AI TRAFFIC TAB ===== -->
    <div id="pane-ai_traffic" hidden>
      <section id="sec-ai-trend">
        <div class="sec-head"><h2>AI traffic over time</h2><div class="sec-head-actions"><div class="chips seg" id="aiTrendGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><span class="status" id="aiTrendStatus"></span></div></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:260px"><canvas id="aiTrendChart"></canvas></div></div>
        <p class="chart-note">Sessions per day, stacked by referring AI assistant.</p>
      </section>
      <section id="sec-ai-sources">
        <div class="sec-head"><h2>AI traffic by source</h2><span class="status" id="aiTrafficStatus"></span></div>
        <p class="chart-note" style="margin-top:0">Website sessions referred by AI assistants (ChatGPT, Perplexity, Gemini, etc.) in this range.</p>
        <div class="table-wrap"><table id="aiSourcesTable" class="compact"></table></div>
      </section>
      <section id="sec-ai-pages">
        <div class="sec-head"><h2>Top landing pages from AI</h2><span class="status" id="aiPagesStatus"></span></div>
        <div class="filter-group" style="margin-bottom:10px"><span class="filter-label">AI source</span><div class="chips" id="aiPageSourceChips"></div></div>
        <input class="page-search" id="aiPagesSearch" type="search" placeholder="Filter by path…" autocomplete="off">
        <div class="table-wrap"><table id="aiPagesTable" class="compact"></table></div>
      </section>
    </div>

    <div id="pane-gsc" hidden>
      {semrush_section_html}
      <section id="sec-gsc-overview">
        <div class="sec-head"><h2>Search Console</h2><span class="status" id="gscStatus"></span></div>
        <div class="cards" id="gscKpis"></div>
      </section>
      <section id="sec-gsc-tables">
        <div class="two-col">
          <div class="col-panel">
            <h3>Top queries</h3>
            <div class="table-wrap"><table id="gscQueriesTable" class="compact"></table></div>
            <div class="pager" id="gscQueriesPager"></div>
          </div>
          <div class="col-panel">
            <h3>Top pages</h3>
            <div class="table-wrap"><table id="gscPagesTable" class="compact"></table></div>
            <div class="pager" id="gscPagesPager"></div>
          </div>
        </div>
      </section>

      <section id="sec-gsc-keywords">
        <div class="sec-head"><h2>Branded &amp; Target Keywords</h2><span class="status" id="gscKwStatus"></span></div>
        <div class="two-col">
          <div class="col-panel">
            <h3>Branded queries <span class="muted" id="gscBrandedCount"></span></h3>
            <div class="tag-editor" id="gscBrandedTags"></div>
            <div class="table-wrap"><table id="gscBrandedTable" class="compact"></table></div>
            <div class="pager" id="gscBrandedPager"></div>
            <div class="chart-wrap" style="margin-top:12px">
              <div class="chart-canvas-host" style="height:130px"><canvas id="gscBrandedTrendChart"></canvas></div>
            </div>
          </div>
          <div class="col-panel">
            <h3>Target queries <span class="muted" id="gscTargetCount"></span></h3>
            <div class="tag-editor" id="gscTargetTags"></div>
            <div class="table-wrap"><table id="gscTargetTable" class="compact"></table></div>
            <div class="pager" id="gscTargetPager"></div>
            <div class="chart-wrap" style="margin-top:12px">
              <div class="chart-canvas-host" style="height:130px"><canvas id="gscTargetTrendChart"></canvas></div>
            </div>
          </div>
        </div>
      </section>
    </div><!-- /pane-gsc -->
    {pagespeed_pane_html}
  </main>
    </div>
  </div>
  <div id="creativePreview" class="creative-preview" hidden>
    <div class="creative-preview-backdrop" data-close-preview></div>
    <div class="creative-preview-dialog" role="dialog" aria-modal="true" aria-label="Creative preview">
      <button type="button" class="creative-preview-close" data-close-preview aria-label="Close">&times;</button>
      <div class="creative-preview-body" id="creativePreviewBody"></div>
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

    const HAS_PAID_ADS = {'true' if has_paid_ads else 'false'};

    // ---- API constants ----
    const SUMMARY_API          = "{_aurl(f'/api/clients/{api_client_key}/summary')}";
    const HEALTH_API           = "{_aurl(f'/api/clients/{api_client_key}/marketing/health')}";
    const EXPLORER_API         = "{_aurl(f'/api/clients/{api_client_key}/google-ads/explorer')}";
    const GOOGLE_ADS_KEYWORDS_API = "{_aurl(f'/api/clients/{api_client_key}/google-ads/keywords')}";
    const LINKEDIN_EXPLORER_API= "{_aurl(f'/api/clients/{api_client_key}/linkedin/explorer')}";
    const META_EXPLORER_API    = "{_aurl(f'/api/clients/{api_client_key}/meta/explorer')}";
    const BACKFILL_API         = "{_aurl(f'/api/clients/{api_client_key}/backfill-linkedin')}";
    const PAGES_TOP_API        = "{_aurl(f'/api/clients/{api_client_key}/pages/top')}";
    const PAGES_SOURCES_API    = "{_aurl(f'/api/clients/{api_client_key}/pages/sources')}";
    const AI_TRAFFIC_DAILY_API = "{_aurl(f'/api/clients/{api_client_key}/ai-traffic/daily')}";
    const TOP_PAGES_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/top-key-events')}";
    const TRAFFIC_ACQ_API      = "{_aurl(f'/api/clients/{api_client_key}/pages/traffic-acquisition')}";
    const DEVICE_SPLIT_API     = "{_aurl(f'/api/clients/{api_client_key}/pages/device-split')}";
    const LANDING_PAGES_API    = "{_aurl(f'/api/clients/{api_client_key}/pages/landing')}";
    const USER_ACQ_API         = "{_aurl(f'/api/clients/{api_client_key}/analytics/user-acquisition')}";
    const DEMOGRAPHICS_API     = "{_aurl(f'/api/clients/{api_client_key}/analytics/demographics')}";
    const GSC_API              = "{_aurl(f'/api/clients/{api_client_key}/gsc/summary')}";
    const SEMRUSH_API          = "{_aurl(f'/api/clients/{api_client_key}/semrush/summary')}";
    const PAGESPEED_API        = "{_aurl(f'/api/clients/{api_client_key}/pagespeed/summary')}";
    const PAGESPEED_TARGETS_API= "{_aurl(f'/api/clients/{api_client_key}/pagespeed/targets')}";
    const PAGESPEED_TARGETS    = {pagespeed_targets_json};
    const PAGESPEED_STRATEGIES = {pagespeed_strategies_json};
    const GSC_KEYWORD_CONFIG_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/keyword-config')}";
    const GSC_KEYWORD_MATCHES_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/keyword-matches')}";
    const GSC_BRANDED_ROOTS = {json.dumps([s.strip() for s in gsc_branded_roots.splitlines() if s.strip()])};
    const GSC_TARGET_KEYWORDS = {json.dumps([s.strip() for s in gsc_target_keywords.splitlines() if s.strip()])};
    const GSC_BRANDED_RAW = {json.dumps(gsc_branded_roots)};
    const GSC_TARGET_RAW = {json.dumps(gsc_target_keywords)};
    const LANDING_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/landing-events')}";
    const TRAFFIC_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/traffic-key-events')}";
    const USER_ACQ_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/analytics/user-acq-key-events')}";
    const GA4_KEY_EVENTS_SAVED = {json.dumps([s.strip() for s in ga4_key_events.splitlines() if s.strip()])};

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

    // ---- Chart.js foundation (lib vendored under /static/vendor) ----
    // Shared theme + small factories keep every chart consistent. Instances are
    // tracked per canvas id and destroyed before re-creation (Chart.js will not
    // reuse a canvas that still has a live chart on it).
    const __charts = {{}};
    function __chart(id, config) {{
      const el = document.getElementById(id);
      if (!el || !window.Chart) return null;
      if (__charts[id]) __charts[id].destroy();
      __charts[id] = new Chart(el.getContext('2d'), config);
      return __charts[id];
    }}
    function __destroyChart(id) {{ if (__charts[id]) {{ __charts[id].destroy(); delete __charts[id]; }} }}
    if (window.Chart) {{
      Chart.defaults.font.family = 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
      Chart.defaults.font.size = 11;
      Chart.defaults.color = '#6b7a90';
      Chart.defaults.maintainAspectRatio = false;
      Chart.defaults.animation.duration = 300;
      Chart.defaults.plugins.legend.display = false;
      const _tt = Chart.defaults.plugins.tooltip;
      _tt.backgroundColor = '#0b1020'; _tt.titleColor = '#e8eefc'; _tt.bodyColor = '#e8eefc';
      _tt.padding = 9; _tt.cornerRadius = 8; _tt.boxPadding = 4; _tt.usePointStyle = true;
    }}
    // Vertical gradient fill under an area line, built from the plot geometry.
    function __areaFill(context, color) {{
      const c = context.chart.ctx, a = context.chart.chartArea;
      if (!a) return color + '00';
      const g = c.createLinearGradient(0, a.top, 0, a.bottom);
      g.addColorStop(0, color + '33'); g.addColorStop(1, color + '00');
      return g;
    }}
    // Line chart. series: [{{label, data, color, fill?, dashed?, raw?, fmt?}}].
    // `data` is what's plotted (may be normalized); `raw`/`fmt` drive tooltips.
    function lineChart(id, labels, series, opts) {{
      opts = opts || {{}};
      const datasets = series.map(s => ({{
        label: s.label, data: s.data, borderColor: s.color,
        backgroundColor: s.fill ? (ctx => __areaFill(ctx, s.color)) : 'transparent',
        fill: !!s.fill, borderWidth: 2.25, tension: 0.35,
        borderDash: s.dashed ? [5, 4] : [],
        pointRadius: opts.points ? 2.5 : 0, pointHoverRadius: 4, pointBackgroundColor: s.color,
        _raw: s.raw || s.data, _fmt: s.fmt || count,
      }}));
      return __chart(id, {{
        type: 'line',
        data: {{ labels, datasets }},
        options: {{
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            x: {{ grid: {{ display: false }}, border: {{ display: false }}, ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: opts.xTicks || 6 }} }},
            y: {{ display: opts.yDisplay !== false, reverse: !!opts.yReverse, beginAtZero: opts.beginAtZero !== false,
                 grid: {{ color: '#f1f4f9' }}, border: {{ display: false }},
                 ticks: {{ maxTicksLimit: 4, callback: opts.yFmt || (v => v) }} }},
          }},
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ callbacks: opts.tooltip || {{
              label: c => `${{c.dataset.label}}: ${{c.dataset._fmt(c.dataset._raw[c.dataIndex])}}`,
            }} }},
          }},
        }},
      }});
    }}
    function withDates(base) {{
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + currentStart + '&end_date=' + currentEnd;
    }}
    function withDatesRange(base, s, e) {{
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + s + '&end_date=' + e;
    }}
    // ---- Period-over-period comparison helpers ----
    function deltaHtml(curr, prev) {{
      curr = num(curr);
      if (prev == null) return '';
      prev = num(prev);
      if (!prev) {{
        if (!curr) return `<div class="card-delta flat">No change vs prior period</div>`;
        return `<div class="card-delta up">New vs prior period</div>`;
      }}
      const change = ((curr - prev) / Math.abs(prev)) * 100;
      const dir = change > 0.05 ? 'up' : (change < -0.05 ? 'down' : 'flat');
      const arrow = dir==='up' ? '\\u25B2' : (dir==='down' ? '\\u25BC' : '\\u2014');
      return `<div class="card-delta ${{dir}}">${{arrow}} ${{Math.abs(change).toFixed(1)}}% vs prior period</div>`;
    }}
    // True if the comparison window (compareStart/compareEnd) reaches back
    // before any of the given sources' synced history -- earliestDates is
    // populated from the /marketing/health payload by loadHealth().
    function cmpBlockedBy(sourceKeys) {{
      for (const k of sourceKeys) {{
        const earliest = earliestDates[k];
        if (earliest && compareStart < earliest) return earliest;
      }}
      return null;
    }}
    function setCmpWarn(elId, sourceKeys) {{
      const el = document.getElementById(elId);
      if (!el) return;
      const blockedSince = cmpBlockedBy(sourceKeys);
      if (blockedSince) {{
        el.hidden = false;
        el.title = `Comparison period (${{compareStart}} to ${{compareEnd}}) starts before synced data begins (${{blockedSince}}). The "vs prior period" figures above may be incomplete.`;
      }} else {{
        el.hidden = true;
        el.title = '';
      }}
    }}
    async function getJson(url, _attempt) {{
      _attempt = _attempt || 0;
      const MAX_RETRIES = 2;
      // Growing backoff with jitter: ~0.4-0.8s, then ~0.8-1.2s.
      const backoff = () => new Promise(r => setTimeout(r, 400 * (_attempt + 1) + Math.random()*400));
      let resp;
      try {{
        resp = await fetch(url, {{ credentials:'same-origin' }});
      }} catch (netErr) {{
        // fetch() rejects (no HTTP status) on network drops, cold-start
        // connection resets and platform proxy blips -- treat like a 5xx and
        // retry before surfacing the failure.
        if (_attempt < MAX_RETRIES) {{ await backoff(); return getJson(url, _attempt + 1); }}
        throw netErr;
      }}
      if (!resp.ok && resp.status >= 500 && _attempt < MAX_RETRIES) {{
        // 5xx here has mostly been transient BQ concurrency pressure / cold
        // starts, not a real failure -- retry with growing backoff.
        await backoff();
        return getJson(url, _attempt + 1);
      }}
      const body = await resp.json().catch(() => ({{ detail:resp.statusText }}));
      if (!resp.ok) {{
        // FastAPI error bodies here are {{detail: {{error, type}}}} -- surface the
        // actual message instead of stringifying the whole object.
        const d = body && body.detail;
        const msg = (d && typeof d === 'object') ? (d.error || JSON.stringify(d)) : d;
        throw new Error(msg || resp.statusText || 'Request failed');
      }}
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
    const TABS = ['overview', 'explorer', 'analytics', 'ai_traffic', 'gsc', 'site_performance'];
    let currentTab = 'overview';
    let analyticsLoaded = false;
    let explorerLoaded = false;
    let gscLoaded = false;
    let aiTrafficLoaded = false;

    function switchTab(tab) {{
      TABS.forEach(t => {{ document.getElementById('pane-' + t).hidden = t !== tab; }});
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      const pf = document.getElementById('platformFilterGroup');
      if (pf) pf.hidden = tab === 'analytics' || tab === 'gsc' || tab === 'ai_traffic';
      const efb = document.getElementById('explorerFilterBar');
      if (efb) efb.hidden = !(tab === 'explorer' && EXPLORER_FILTER_GROUPS.length);
      currentTab = tab;
      // Keep the tab in the URL so a refresh reopens the same page (not
      // Overview), and so switching clients can carry the tab across. Overview
      // is the canonical home, so it drops ?view= to keep that URL clean.
      try {{
        const u = new URL(location.href);
        if (tab === 'overview') u.searchParams.delete('view');
        else u.searchParams.set('view', tab);
        history.replaceState(null, '', u);
      }} catch (e) {{ /* ignore */ }}
      updateKeyEventBar();
      if (tab === 'explorer' && !explorerLoaded) {{
        explorerLoaded = true;
        loadExplorer();
      }}
      if (tab === 'ai_traffic' && !aiTrafficLoaded) {{
        aiTrafficLoaded = true;
        loadAiTraffic();
      }}
      if (tab === 'analytics' && !analyticsLoaded) {{
        analyticsLoaded = true;
        applyModules();
        loadAllAnalytics();
      }}
      if (tab === 'gsc' && !gscLoaded) {{
        gscLoaded = true;
        loadGsc();
        loadSemrush();
      }}
      if (tab === 'site_performance' && !sitePerfLoaded) {{
        sitePerfLoaded = true;
        loadSitePerformance();
      }}
    }}

    document.querySelectorAll('.tab-btn').forEach(btn =>
      btn.addEventListener('click', () => switchTab(btn.dataset.tab))
    );

    // ---- Module system (localStorage) ----
    const ALL_MODULES = ['sessions','top_pages','traffic','audience','landing','user_acquisition','demographics'];
    const MODULE_SECTIONS = {{
      sessions:'sec-sessions', top_pages:'sec-pages', traffic:'sec-traffic', audience:'sec-audience',
      landing:'sec-landing',
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
      updateKeyEventBar();
    }}
    // The Events dropdown now lives in the shared sticky bar, so it must only
    // show on the Website Analytics tab — and only when a panel it drives
    // (Top pages / Traffic / Landing / User acquisition) is enabled.
    function updateKeyEventBar() {{
      const keBar = document.getElementById('keyEventFilterGroup');
      if (!keBar) return;
      const m = getModules();
      const anyPanel = m.top_pages || m.traffic || m.landing || m.user_acquisition;
      keBar.hidden = !(currentTab === 'analytics' && anyPanel);
    }}

    // ---- Paid media: Summary ----
    // 4th field = which direction is "good" for coloring the vs-previous delta:
    // 'up' (more is better), 'down' (less is better), 'neutral' (just report it).
    const SUMMARY_CARDS = [
      ['spend','Spend',money,'neutral'],['impressions','Impressions',count,'up'],['clicks','Clicks',count,'up'],
      ['conversions','Conversions',count,'up'],['cpc','CPC',money,'down'],['cpa','CPA',money,'down'],['ctr','CTR',pct,'up'],
    ];
    const SPARK_COLORS = {{ spend:'#1769aa', impressions:'#7c3aed', clicks:'#0a7f3f', conversions:'#0891b2', cpc:'#d97706', cpa:'#dc2626', ctr:'#0891b2' }};
    const platformFilter = new Set();
    let summaryPayload = null;
    let compareSummaryPayload = null;
    const summaryCards = document.getElementById('summaryCards');

    function selectedSummaryFrom(payload) {{
      if (!payload) return {{}};
      const by = payload.by_source || null;
      if (!by || platformFilter.size === 0) return payload.summary || {{}};
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
    function selectedSummary() {{ return selectedSummaryFrom(summaryPayload); }}
    // Tiny inline-SVG sparkline of the metric's current-period daily trend.
    function sparkSvg(vals, color) {{
      const clean=vals.filter(v=>v!=null&&isFinite(v));
      if (clean.length<2) return '<span class="spark-empty"></span>';
      const n=vals.length, w=66, h=22, mn=Math.min(...clean), mx=Math.max(...clean), span=(mx-mn)||1;
      const pts=vals.map((v,i)=>`${{(n===1?w/2:i/(n-1)*w).toFixed(1)}},${{(h-1-((num(v)-mn)/span)*(h-2)).toFixed(1)}}`).join(' ');
      return `<svg class="spark" viewBox="0 0 ${{w}} ${{h}}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${{pts}}" fill="none" stroke="${{color}}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
    }}
    // % change vs the comparison period, colored by whether the move is good for
    // that metric (dir from SUMMARY_CARDS). Arrow always shows the raw direction.
    // (Distinct from the snapshot-card deltaHtml above — this one is a compact
    // inline chip for the paid summary cards.)
    function summaryDeltaHtml(cur, prev, dir) {{
      if (prev==null || num(prev)===0 || cur==null) return '<span class="cmp-delta flat">—</span>';
      const ch=(num(cur)-num(prev))/num(prev)*100;
      if (Math.abs(ch)<0.5) return '<span class="cmp-delta flat" title="vs previous period">0%</span>';
      const up=ch>0, arrow=up?'▲':'▼';
      let cls='flat';
      if (dir==='up') cls=up?'up':'down'; else if (dir==='down') cls=up?'down':'up';
      return `<span class="cmp-delta ${{cls}}" title="vs previous period">${{arrow}} ${{Math.abs(ch).toFixed(0)}}%</span>`;
    }}
    function renderSummary() {{
      const s = selectedSummary();
      const prev = selectedSummaryFrom(compareSummaryPayload);
      const daily = buildChartDaily();
      summaryCards.innerHTML = SUMMARY_CARDS.map(([key,label,format,dir]) => {{
        const delta = summaryDeltaHtml(s[key], (prev && prev[key]!=null) ? prev[key] : null, dir);
        const spark = sparkSvg(daily.map(d=>num(d[key])), SPARK_COLORS[key]||'#1769aa');
        return `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{format(s[key])}}</div><div class="card-foot">${{delta}}${{spark}}</div></div>`;
      }}).join('');
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
      const n = chartDaily.length;
      if (!n) {{ __destroyChart('trendChart'); setStatus('chartStatus','No data for this range.'); return; }}
      const active = CHART_METRICS.filter(m => chartMetrics.has(m.key));
      const labels = chartDaily.map(d => String(d.date).slice(5));
      const single = active.length === 1;
      // Each metric keeps its own min–max scale, so normalize per-metric to 0–1
      // for plotting and carry the real values (raw) + formatter for tooltips.
      const series = active.map(m => {{
        const raw = chartDaily.map(d => num(d[m.key]));
        const mn = Math.min(...raw), mx = Math.max(...raw), span = (mx - mn) || 1;
        return {{ label: m.label, data: raw.map(v => (v - mn) / span), raw, fmt: m.fmt,
                 color: m.color, fill: single }};
      }});
      lineChart('trendChart', labels, series, {{
        yDisplay: false, xTicks: 6,
        tooltip: {{
          title: items => items.length ? String(chartDaily[items[0].dataIndex].date) : '',
          label: c => `${{c.dataset.label}}: ${{c.dataset._fmt(c.dataset._raw[c.dataIndex])}}`,
        }},
      }});
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
    async function loadSummary() {{
      setStatus('summaryStatus','Loading…');
      summaryCards.innerHTML = skelCards(7);
      skelChart('trendChart','trend-svg');
      try {{
        const [curr, prev] = await Promise.all([
          getJson(withDates(SUMMARY_API)),
          getJson(withDatesRange(SUMMARY_API, compareStart, compareEnd)).catch(()=>null),
        ]);
        summaryPayload = curr;
        compareSummaryPayload = prev;
        renderSummary(); renderChart();
        // Date range is already shown by the Range dropdown, so keep this to
        // just the source note (blank unless the data is combined across sources).
        setStatus('summaryStatus', summaryPayload.by_source ? '' : 'combined');
      }} catch(err) {{
        summaryPayload=null;
        compareSummaryPayload=null;
        summaryCards.innerHTML = '';
        setStatus('summaryStatus', err.message||String(err), true);
      }}
    }}

    // ---- No-paid-ads Overview snapshot (GA4 traffic + GSC search) ----
    // cards: [label, currentRaw, prevRawOrNull, formatFn, ok]. ok===false means
    // the underlying request failed -- render "--" rather than a misleading 0,
    // which is indistinguishable from genuinely-zero activity.
    function renderSnapshotCards(containerId, cards) {{
      document.getElementById(containerId).innerHTML = cards.map(([label,curr,prev,fmt,ok]) => {{
        const valueHtml = ok===false
          ? `<span title="This metric failed to load -- try switching ranges or refreshing.">—</span>`
          : fmt(curr);
        const deltaOrNote = ok===false ? '' : deltaHtml(curr,prev);
        return `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{valueHtml}}</div>${{deltaOrNote}}</div>`;
      }}).join('');
    }}
    async function ga4TrafficTotals(s, e) {{
      // Sequential (not parallel) to keep concurrent BQ query load down --
      // and each sub-request tracks its own success so a failure renders as
      // "unavailable" rather than a misleading zero.
      let trafficOk = true, pagesOk = true;
      const traffic = await getJson(withDatesRange(TRAFFIC_ACQ_API, s, e)).catch(()=>{{ trafficOk=false; return {{by_channel:[]}}; }});
      const pages = await getJson(withDatesRange(PAGES_TOP_API, s, e)).catch(()=>{{ pagesOk=false; return {{rows:[]}}; }});
      const sessions = (traffic.by_channel||[]).reduce((sum,r)=>sum+num(r.sessions),0);
      const engaged = (traffic.by_channel||[]).reduce((sum,r)=>sum+num(r.engaged_sessions),0);
      const rows = pages.rows||[];
      const pageViews = rows.reduce((sum,r)=>sum+num(r.page_views),0);
      const keyEvents = rows.reduce((sum,r)=>sum+num(r.key_events),0);
      return {{sessions, engaged, pageViews, keyEvents, trafficOk, pagesOk}};
    }}
    async function loadOverviewSnapshot() {{
      setStatus('ga4SnapshotStatus','Loading…');
      document.getElementById('ga4SnapshotCards').innerHTML = skelCards(4);
      try {{
        const [curr, prev] = await Promise.all([
          ga4TrafficTotals(currentStart, currentEnd),
          ga4TrafficTotals(compareStart, compareEnd).catch(()=>null),
        ]);
        renderSnapshotCards('ga4SnapshotCards', [
          ['Sessions', curr.sessions, prev&&prev.sessions, count, curr.trafficOk],
          ['Engaged sessions', curr.engaged, prev&&prev.engaged, count, curr.trafficOk],
          ['Page views', curr.pageViews, prev&&prev.pageViews, count, curr.pagesOk],
          ['Key events', curr.keyEvents, prev&&prev.keyEvents, count, curr.pagesOk],
        ]);
        setCmpWarn('ga4SnapshotCmpWarn', ['google_analytics']);
        if (!curr.trafficOk || !curr.pagesOk) {{
          setStatus('ga4SnapshotStatus', 'Some metrics failed to load — try switching ranges or refreshing.', true);
        }} else {{
          setStatus('ga4SnapshotStatus', curr.sessions || curr.pageViews ? '' : 'No data for this range yet.');
        }}
      }} catch(err) {{
        document.getElementById('ga4SnapshotCards').innerHTML = '';
        setStatus('ga4SnapshotStatus', err.message||String(err), true);
      }}
      setStatus('gscSnapshotStatus','Loading…');
      document.getElementById('gscSnapshotCards').innerHTML = skelCards(4);
      try {{
        const [p, prevP] = await Promise.all([
          getJson(withDatesRange(GSC_API, currentStart, currentEnd)),
          getJson(withDatesRange(GSC_API, compareStart, compareEnd)).catch(()=>null),
        ]);
        const k = (p&&p.kpis)||{{}};
        const pk = (prevP&&prevP.kpis)||null;
        renderSnapshotCards('gscSnapshotCards', [
          ['Clicks', k.clicks, pk&&pk.clicks, count],
          ['Impressions', k.impressions, pk&&pk.impressions, count],
          ['CTR', k.ctr, pk&&pk.ctr, v=>v==null?'—':num(v).toFixed(2)+'%'],
          ['Avg position', k.avg_position, pk&&pk.avg_position, v=>v==null?'—':num(v).toFixed(1)],
        ]);
        setCmpWarn('gscSnapshotCmpWarn', ['gsc']);
        const empty = !p || (!p.kpis && !(p.top_queries||[]).length && !(p.top_pages||[]).length);
        setStatus('gscSnapshotStatus', empty ? 'No data for this range yet.' : '');
      }} catch(err) {{
        document.getElementById('gscSnapshotCards').innerHTML = '';
        setStatus('gscSnapshotStatus', err.message||String(err), true);
      }}
    }}
    // ---- Search Console ----
    const gscPos = v => v==null ? '—' : num(v).toFixed(1);
    const gscPct = v => v==null ? '—' : (num(v)).toFixed(2) + '%';
    // Position movement vs. the previous period: value is prior - current, so a
    // positive number means the keyword improved (moved toward rank 1). null =
    // the query didn't rank in the prior period ("New").
    const gscDelta = v => {{
      if (v==null) return '<span class="gsc-mv gsc-mv-new">New</span>';
      const n=num(v);
      if (Math.abs(n)<0.05) return '<span class="gsc-mv gsc-mv-flat">\\u2013</span>';
      if (n>0) return '<span class="gsc-mv gsc-mv-up">\\u25B4 '+n.toFixed(1)+'</span>';
      return '<span class="gsc-mv gsc-mv-down">\\u25BE '+Math.abs(n).toFixed(1)+'</span>';
    }};
    function renderGscKpis(k, daily) {{
      k = k || {{}}; daily = daily || [];
      // [label, formatted value, kpi key, prior value, good-direction, spark color].
      // Avg position is "lower is better" so its delta direction is 'down'.
      const cards = [
        ['Clicks', count(k.clicks), 'clicks', k.prior_clicks, 'up', '#1769aa'],
        ['Impressions', count(k.impressions), 'impressions', k.prior_impressions, 'up', '#7c3aed'],
        ['CTR', gscPct(k.ctr), 'ctr', k.prior_ctr, 'up', '#0a7f3f'],
        ['Avg position', gscPos(k.avg_position), 'avg_position', k.prior_avg_position, 'down', '#d97706'],
      ];
      document.getElementById('gscKpis').innerHTML = cards.map(([label,val,key,prior,dir,color]) => {{
        const delta = summaryDeltaHtml(k[key], (prior!=null?prior:null), dir);
        const spark = sparkSvg(daily.map(d=>num(d[key])), color);
        return `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{val}}</div><div class="card-foot">${{delta}}${{spark}}</div></div>`;
      }}).join('');
    }}
    // ---- GSC queries/pages: sortable + paginated (top 10/page) ----
    const GSC_PER_PAGE = 10;
    const GSC_SORT_COLS = [
      {{key:'clicks', label:'Clicks', format:count, defDir:'desc'}},
      {{key:'impressions', label:'Impr.', format:count, defDir:'desc'}},
      {{key:'ctr', label:'CTR', format:gscPct, defDir:'desc'}},
      {{key:'avg_position', label:'Position', format:gscPos, defDir:'asc'}},
    ];
    // Δ Position is queries-only (pages/branded/target carry no prior-position
    // field). Default asc so the first click surfaces the biggest sinkers.
    const GSC_DELTA_COL = {{key:'delta_position', label:'\\u0394 Pos', format:gscDelta, defDir:'asc'}};
    const gscColsFor = which => which==='queries' ? GSC_SORT_COLS.concat([GSC_DELTA_COL]) : GSC_SORT_COLS;
    // page_url rows come back as full URLs (https://host/path) -- show just the
    // path in the table (full URL stays in the title tooltip on hover).
    function pathOnly(url) {{
      try {{ const u = new URL(url); return (u.pathname || '/') + (u.search || ''); }}
      catch {{ return url; }}
    }}
    const GSC_LABEL_COL_WIDTH = 240;
    const gscTables = {{
      queries: {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscQueriesTable', pagerId:'gscQueriesPager', labelWidth:GSC_LABEL_COL_WIDTH}},
      pages:   {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'page_url', labelText:'Page', tableId:'gscPagesTable', pagerId:'gscPagesPager', labelWidth:GSC_LABEL_COL_WIDTH, labelFormat:pathOnly}},
      branded: {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscBrandedTable', pagerId:'gscBrandedPager', labelWidth:GSC_LABEL_COL_WIDTH}},
      target:  {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscTargetTable', pagerId:'gscTargetPager', labelWidth:GSC_LABEL_COL_WIDTH}},
    }};
    // Reapply a user-chosen label-column width after each (re)render, since the
    // table is rebuilt on sort/paginate. Overrides the default max-width:0 clamp.
    function applyGscColWidth(el, st) {{
      if (!el || !st.labelWidth) return;
      const w=st.labelWidth+'px';
      const th=el.querySelector('th.left'); if (th) th.style.width=w;
      el.querySelectorAll('td.left').forEach(td=>{{ td.style.maxWidth=w; td.style.width=w; }});
    }}
    function renderGscTable(which) {{
      const st = gscTables[which];
      const cols = gscColsFor(which);
      const el = document.getElementById(st.tableId);
      const pager = document.getElementById(st.pagerId);
      if (!st.rows.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No data for this range.</td></tr></tbody>`; pager.innerHTML=''; return; }}
      const sorted=[...st.rows].sort((a,b)=>{{const va=num(a[st.sortKey]),vb=num(b[st.sortKey]);return st.sortDir==='asc'?va-vb:vb-va;}});
      const totalPages=Math.max(1,Math.ceil(sorted.length/GSC_PER_PAGE));
      if (st.page>totalPages) st.page=totalPages;
      const start=(st.page-1)*GSC_PER_PAGE, pageRows=sorted.slice(start,start+GSC_PER_PAGE);
      const arrow=k=>st.sortKey===k?(st.sortDir==='asc'?' \\u25B4':' \\u25BE'):'';
      const head=`<thead><tr><th class="left col-resizable">${{esc(st.labelText)}}<span class="col-resizer" data-which="${{which}}"></span></th>`+cols.map(c=>`<th class="gsc-sort${{st.sortKey===c.key?' active':''}}" data-which="${{which}}" data-key="${{c.key}}">${{c.label}}${{arrow(c.key)}}</th>`).join('')+`</tr></thead>`;
      const body=`<tbody>`+pageRows.map(r=>{{const raw=r[st.labelKey];const label=st.labelFormat?st.labelFormat(raw):raw;return`<tr><td class="left"><span class="page-path" title="${{esc(raw)}}">${{esc(label)}}</span></td>`+cols.map(c=>`<td>${{c.format(r[c.key])}}</td>`).join('')+`</tr>`;}}).join('')+`</tbody>`;
      el.innerHTML=head+body;
      applyGscColWidth(el, st);
      if (totalPages<=1) {{ pager.innerHTML=''; }}
      else {{ pager.innerHTML=`<button type="button" class="pager-btn" data-which="${{which}}" data-dir="prev"${{st.page<=1?' disabled':''}}>\\u2039 Prev</button><span class="pager-info">Page ${{st.page}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-which="${{which}}" data-dir="next"${{st.page>=totalPages?' disabled':''}}>Next \\u203A</button>`; }}
    }}
    document.getElementById('pane-gsc').addEventListener('click', ev => {{
      const th=ev.target.closest('th.gsc-sort');
      if (th) {{ const st=gscTables[th.dataset.which], key=th.dataset.key;
        if (st.sortKey===key) st.sortDir=st.sortDir==='asc'?'desc':'asc';
        else {{ st.sortKey=key; st.sortDir=(gscColsFor(th.dataset.which).find(c=>c.key===key)||{{}}).defDir||'desc'; }}
        st.page=1; renderGscTable(th.dataset.which); return;
      }}
      const pb=ev.target.closest('.pager-btn[data-which]');
      if (pb && !pb.disabled) {{ const st=gscTables[pb.dataset.which]; st.page+=(pb.dataset.dir==='next'?1:-1); renderGscTable(pb.dataset.which); }}
    }});
    // Drag the label column edge to widen/narrow it (persists across sort/paginate).
    (function initGscColResize(){{
      const pane=document.getElementById('pane-gsc'); if (!pane) return;
      let active=null;
      pane.addEventListener('mousedown', ev => {{
        const h=ev.target.closest('.col-resizer'); if (!h) return;
        ev.preventDefault(); ev.stopPropagation();
        const st=gscTables[h.dataset.which], table=h.closest('table');
        const th=table.querySelector('th.left');
        active={{st, table, startX:ev.clientX, startW:th.getBoundingClientRect().width}};
        document.body.classList.add('col-resizing');
      }});
      document.addEventListener('mousemove', ev => {{
        if (!active) return;
        active.st.labelWidth=Math.max(90, Math.round(active.startW+(ev.clientX-active.startX)));
        applyGscColWidth(active.table, active.st);
      }});
      document.addEventListener('mouseup', () => {{
        if (!active) return; active=null; document.body.classList.remove('col-resizing');
      }});
    }})();
    async function loadGsc() {{
      setStatus('gscStatus','Loading…');
      document.getElementById('gscKpis').innerHTML = skelCards(4);
      document.getElementById('gscQueriesTable').innerHTML = skelTable(5,6);
      document.getElementById('gscPagesTable').innerHTML = skelTable(5,6);
      try {{
        const p = await getJson(withDates(GSC_API));
        renderGscKpis((p&&p.kpis)||{{}}, (p&&p.daily)||[]);
        for (const which of ['queries','pages']) {{
          const st=gscTables[which]; st.rows = (p && (which==='queries'?p.top_queries:p.top_pages)) || [];
          st.page=1; st.sortKey='clicks'; st.sortDir='desc'; renderGscTable(which);
        }}
        renderGscKeywordTables();
        const k=(p&&p.kpis)||{{}};
        const empty = !p || (!p.kpis && !(p.top_queries||[]).length && !(p.top_pages||[]).length);
        setStatus('gscStatus', empty ? 'No data for this range yet.' : `${{count(k.clicks)}} clicks · ${{count(k.impressions)}} impressions`);
      }} catch(err) {{
        setStatus('gscStatus', err.message||String(err), true);
      }}
    }}

    // ---- Branded / target keyword filtering (GSC queries only) ----
    let gscBrandedRoots = GSC_BRANDED_ROOTS.slice();
    let gscTargetKeywords = GSC_TARGET_KEYWORDS.slice();
    // Branded/target queries are matched against the FULL date-range dataset
    // via a dedicated backend scan (gsc/keyword-matches), not filtered from
    // the top_queries subset (top_queries is LIMIT 25 by clicks in
    // gsc/summary, so a real match outside the top 25 would otherwise be
    // silently missed).
    let _gscKwReqId = 0;
    async function fetchKeywordMatches(terms) {{
      if (!terms.length) return {{rows:[], weekly:[]}};
      const url = withDates(GSC_KEYWORD_MATCHES_API) + '&terms=' + encodeURIComponent(terms.join(','));
      try {{
        const r = await getJson(url);
        return {{rows: r.rows || [], weekly: r.weekly || []}};
      }} catch (err) {{
        return {{rows:[], weekly:[]}};
      }}
    }}
    // Small single-line weekly trend chart, same visual style as
    // drawSessionsTrend but generic over any {{week_start, <valueKey>}} rows.
    // Weekly avg-position trend. For position lower is better, so with invert the
    // y-axis is reversed (best position at the top), like a rank chart.
    function drawKeywordTrend(canvasId, rows, valueKey, color, invert) {{
      clearSkelChart(canvasId);
      if (!document.getElementById(canvasId)) return;
      const n=rows.length;
      if (!n) {{ __destroyChart(canvasId); return; }}
      const labels=rows.map(r=>String(r.week_start||'').slice(5));
      const data=rows.map(r=>num(r[valueKey]));
      const fmtPos=v=>(Math.round(v*10)/10).toFixed(1);
      lineChart(canvasId, labels, [{{ label:'Avg position', data, color, fill:true }}], {{
        points:true, yReverse: !!invert, yDisplay:true, beginAtZero:false,
        yFmt: v => fmtPos(v),
        tooltip: {{ label: c => `Avg position: ${{fmtPos(c.raw)}}` }},
      }});
    }}
    async function renderGscKeywordTables() {{
      const reqId = ++_gscKwReqId;
      // Skeleton only the tables that will actually resolve to data, so we
      // never flash a skeleton in front of a "nothing configured" empty state.
      if (gscBrandedRoots.length) document.getElementById('gscBrandedTable').innerHTML = skelTable(4,4);
      if (gscTargetKeywords.length) document.getElementById('gscTargetTable').innerHTML = skelTable(4,4);
      const [branded, target] = await Promise.all([
        fetchKeywordMatches(gscBrandedRoots),
        fetchKeywordMatches(gscTargetKeywords),
      ]);
      if (reqId !== _gscKwReqId) return; // a newer call (date range/terms changed) superseded this one
      gscTables.branded.rows = branded.rows;
      gscTables.target.rows  = target.rows;
      gscTables.branded.page = 1; gscTables.target.page = 1;
      renderGscTable('branded'); renderGscTable('target');
      drawKeywordTrend('gscBrandedTrendChart', branded.weekly, 'avg_position', '#1d6fd0', true);
      drawKeywordTrend('gscTargetTrendChart', target.weekly, 'avg_position', '#7c3aed', true);
      const setCount=(id,n,configured)=>{{const el=document.getElementById(id); if(el) el.textContent = configured ? `(${{n}})` : '';}};
      setCount('gscBrandedCount', gscTables.branded.rows.length, gscBrandedRoots.length);
      setCount('gscTargetCount', gscTables.target.rows.length, gscTargetKeywords.length);
      const none = !gscBrandedRoots.length && !gscTargetKeywords.length;
      setStatus('gscKwStatus', none ? 'Set branded roots and target keywords to see matching queries.' : '');
      // Empty-state hint when configured but nothing matched anywhere in range.
      if (gscBrandedRoots.length && !gscTables.branded.rows.length) document.getElementById('gscBrandedTable').innerHTML=`<tbody><tr><td class="empty">No queries match these branded roots.</td></tr></tbody>`;
      if (gscTargetKeywords.length && !gscTables.target.rows.length) document.getElementById('gscTargetTable').innerHTML=`<tbody><tr><td class="empty">No queries match these target keywords.</td></tr></tbody>`;
      if (!gscBrandedRoots.length) document.getElementById('gscBrandedTable').innerHTML=`<tbody><tr><td class="empty">No branded roots set.</td></tr></tbody>`;
      if (!gscTargetKeywords.length) document.getElementById('gscTargetTable').innerHTML=`<tbody><tr><td class="empty">No target keywords set.</td></tr></tbody>`;
    }}
    // Inline tag editors for branded roots + target keywords. Add with Enter or
    // comma, remove with the chip ×; changes re-filter live and auto-save.
    let _kwSaveTimer=null;
    function saveKwConfig() {{
      clearTimeout(_kwSaveTimer);
      _kwSaveTimer=setTimeout(async () => {{
        setStatus('gscKwStatus','Saving…');
        try {{
          const r=await fetch(GSC_KEYWORD_CONFIG_API, {{method:'POST', headers:{{'Content-Type':'application/json'}}, credentials:'same-origin', body:JSON.stringify({{branded_roots:gscBrandedRoots.join('\\n'), target_keywords:gscTargetKeywords.join('\\n')}})}});
          const body=await r.json().catch(()=>({{}}));
          if (!r.ok||!body.ok) throw new Error((body&&body.detail&&(body.detail.error||body.detail))||r.statusText);
          setStatus('gscKwStatus','Saved.'); setTimeout(()=>{{const el=document.getElementById('gscKwStatus'); if(el&&el.textContent==='Saved.') el.textContent='';}}, 2000);
        }} catch(err) {{ setStatus('gscKwStatus','Save failed: '+(err.message||err), true); }}
      }}, 700);
    }}
    function makeTagEditor(containerId, label, getTerms, setTerms) {{
      const el=document.getElementById(containerId); if(!el) return;
      function commit(v) {{ v=v.trim(); if(!v) return false; const t=getTerms().slice(); if(t.some(x=>x.toLowerCase()===v.toLowerCase())) return false; t.push(v); setTerms(t); return true; }}
      function render() {{
        const terms=getTerms();
        el.innerHTML=`<span class="tag-editor-label">${{esc(label)}}</span>`
          + terms.map((t,i)=>`<span class="tag-chip">${{esc(t)}}<button type="button" data-i="${{i}}" aria-label="Remove ${{esc(t)}}">×</button></span>`).join('')
          + `<input class="tag-input" type="text" placeholder="${{terms.length?'Add…':'Add a '+label.toLowerCase().replace(/s$/,'')+'…'}}">`;
        el.querySelectorAll('.tag-chip button').forEach(btn=>btn.addEventListener('click',()=>{{
          const t=getTerms().slice(); t.splice(+btn.dataset.i,1); setTerms(t); render(); renderGscKeywordTables(); saveKwConfig();
        }}));
        const inp=el.querySelector('.tag-input');
        const add=(refocus)=>{{ if(commit(inp.value)){{ render(); renderGscKeywordTables(); saveKwConfig(); if(refocus){{const ni=el.querySelector('.tag-input'); if(ni) ni.focus();}} }} else {{ inp.value=''; }} }};
        inp.addEventListener('keydown',e=>{{ if(e.key==='Enter'||e.key===','){{ e.preventDefault(); add(true); }} else if(e.key==='Backspace'&&!inp.value){{ const t=getTerms().slice(); if(t.length){{ t.pop(); setTerms(t); render(); renderGscKeywordTables(); saveKwConfig(); const ni=el.querySelector('.tag-input'); if(ni) ni.focus(); }} }} }});
        inp.addEventListener('blur',()=>add(false));
      }}
      render();
    }}
    makeTagEditor('gscBrandedTags','Branded roots', ()=>gscBrandedRoots, t=>{{gscBrandedRoots=t;}});
    makeTagEditor('gscTargetTags','Target keywords', ()=>gscTargetKeywords, t=>{{gscTargetKeywords=t;}});

    // ---- SEMrush (domain-level snapshot — not date-range scoped) ----
    function renderSemrushKpis(ov, bl) {{
      ov = ov || {{}}; bl = bl || {{}};
      const cards = [
        ['Organic Traffic (est.)', count(ov.organic_traffic)],
        ['Organic Keywords', count(ov.organic_keywords)],
        ['Authority Score', bl.authority_score != null ? bl.authority_score + '/100' : '—'],
        ['Referring Domains', count(bl.referring_domains)],
      ];
      document.getElementById('semrushKpis').innerHTML = cards.map(([label,val]) =>
        `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{val}}</div></div>`).join('');
    }}
    async function loadSemrush() {{
      const host=document.getElementById('semrushKpis');
      if (!host) return;   // section omitted when SEMrush isn't connected
      setStatus('semrushStatus','Loading…');
      host.innerHTML = skelCards(4);
      try {{
        const p = await getJson(SEMRUSH_API);
        if (!p || !p.domain) {{
          renderSemrushKpis({{}}, {{}});
          setStatus('semrushStatus','No SEMrush data yet.');
          return;
        }}
        renderSemrushKpis(p.overview, p.backlinks);
        setStatus('semrushStatus', esc(p.domain));
      }} catch(err) {{
        setStatus('semrushStatus', err.message||String(err), true);
      }}
    }}
    {pagespeed_pane_js}
    // Earliest synced date per source (google/linkedin/meta/google_analytics/gsc),
    // populated by loadHealth() below. Used to warn when a comparison period
    // (see compareStart/compareEnd) falls before the data actually starts.
    let earliestDates = {{}};
    // The mart-health TABLE now lives on the Settings page; on the dashboard we
    // still fetch it (quietly) only to populate earliestDates, which drives the
    // "comparison period predates synced data" warnings on the summary cards.
    async function loadHealth() {{
      try {{
        const payload = await getJson(withDates(HEALTH_API));
        const rows = payload.rows||[];
        earliestDates = {{}};
        for (const r of rows) {{
          const k = String(r.source||'').toLowerCase();
          if (k && r.earliest_date) earliestDates[k] = r.earliest_date;
        }}
      }} catch(err) {{
        // Non-fatal: no earliest-date info just means no comparison warnings.
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
    // Explorer table sort — click a column header to sort every tree level (campaigns,
    // ad groups, ads) by it. 'name' sorts the label column alphabetically.
    let explorerSort = {{ key:'spend', dir:'desc' }};
    function explorerMetricVal(m, key) {{
      if (key==='ctr') return num(m.impressions) ? num(m.clicks)/num(m.impressions)*100 : 0;
      return num(m[key]);
    }}
    function explorerAdName(a) {{ return String(a.ad_name||a.ad_label||a.ad_id||''); }}
    // Client-configured filter chip groups: [{{id,label,chips:[{{label,phrases}}]}}].
    // phrases are pre-lowercased server-side; a chip matches a campaign whose
    // (lowercased) name contains any of its phrases.
    const EXPLORER_FILTER_GROUPS = {explorer_filter_groups_json};
    const explorerFilterState = new Map(); // groupId -> Set of active chip labels
    let explorerRows = [];

    function buildChips(container, keys, stateSet, onChange) {{
      const el = typeof container==='string' ? document.getElementById(container) : container;
      el.innerHTML = ['All',...keys].map(k=>`<button type="button" class="chip" data-key="${{esc(k)}}">${{esc(k)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key=btn.dataset.key;
        if (key==='All') stateSet.clear(); else if (stateSet.has(key)) stateSet.delete(key); else stateSet.add(key);
        el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
        (onChange||renderExplorer)();
      }}));
      el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
    }}
    // Explorer filter groups (Business line / Product / Region …) render as
    // space-saving dropdowns in the sticky top bar, each holding a multi-select
    // checkbox list. Reuses the .ke-dd-* dropdown styling.
    function closeAllExplDropdowns(except) {{
      document.querySelectorAll('#explorerFilterBar .expl-dd').forEach(dd => {{
        if (dd===except) return;
        const p=dd.querySelector('.ke-dd-panel'); if (p) p.hidden=true;
        dd.classList.remove('open');
        const t=dd.querySelector('.ke-dd-toggle'); if (t) t.setAttribute('aria-expanded','false');
      }});
    }}
    function wireExplorerDropdown(dd, g, set) {{
      const toggle=dd.querySelector('.ke-dd-toggle');
      const panel=dd.querySelector('.ke-dd-panel');
      const list=dd.querySelector('.ke-dd-list');
      const label=dd.querySelector('.expl-dd-label');
      const updateLabel=()=>{{ label.textContent = set.size ? `${{label.dataset.base}} · ${{set.size}}` : label.dataset.base; toggle.classList.toggle('has-active', set.size>0); }};
      list.innerHTML = g.chips.map(c=>`<label class="ke-dd-option${{set.has(c.label)?' active':''}}"><input type="checkbox"${{set.has(c.label)?' checked':''}} data-val="${{esc(c.label)}}"><span class="ke-dd-name">${{esc(c.label)}}</span></label>`).join('');
      list.querySelectorAll('input[data-val]').forEach(cb=>cb.addEventListener('change',()=>{{
        const v=cb.dataset.val;
        if (set.has(v)) set.delete(v); else set.add(v);
        const opt=cb.closest('.ke-dd-option'); if (opt) opt.classList.toggle('active', set.has(v));
        updateLabel();
        renderExplorer();
      }}));
      toggle.addEventListener('click', e=>{{
        e.stopPropagation();
        if (panel.hidden) {{ closeAllExplDropdowns(dd); panel.hidden=false; dd.classList.add('open'); toggle.setAttribute('aria-expanded','true'); }}
        else {{ panel.hidden=true; dd.classList.remove('open'); toggle.setAttribute('aria-expanded','false'); }}
      }});
      updateLabel();
    }}
    function buildExplorerFilters() {{
      const host = document.getElementById('explorerFilterBar');
      if (!host) return;
      explorerFilterState.clear();
      if (!EXPLORER_FILTER_GROUPS.length) {{ host.innerHTML=''; host.hidden=true; return; }}
      host.innerHTML = EXPLORER_FILTER_GROUPS.map((g,i) =>
        `<div class="ke-dropdown expl-dd" data-group="${{i}}">`+
          `<button type="button" class="ke-dd-toggle" aria-haspopup="listbox" aria-expanded="false">`+
            `<span class="expl-dd-label" data-base="${{esc(g.label)}}">${{esc(g.label)}}</span>`+
            `<span class="ke-dd-caret">▾</span>`+
          `</button>`+
          `<div class="ke-dd-panel" hidden><div class="ke-dd-list"></div></div>`+
        `</div>`
      ).join('');
      EXPLORER_FILTER_GROUPS.forEach((g,i) => {{
        const set = new Set();
        explorerFilterState.set(g.id, set);
        wireExplorerDropdown(host.querySelector(`.expl-dd[data-group="${{i}}"]`), g, set);
      }});
    }}
    document.addEventListener('click', e=>{{ if (!e.target.closest('#explorerFilterBar .expl-dd')) closeAllExplDropdowns(); }});
    document.addEventListener('keydown', e=>{{ if (e.key==='Escape') closeAllExplDropdowns(); }});
    function explorerRowMatches(row) {{
      const name=String(row.campaign_name||'').toLowerCase();
      for (const g of EXPLORER_FILTER_GROUPS) {{
        const set=explorerFilterState.get(g.id);
        if (!set||!set.size) continue;
        const ok=g.chips.some(c=>set.has(c.label)&&c.phrases.some(p=>p&&name.includes(p)));
        if (!ok) return false;
      }}
      const platOk=!platformFilter.size||[...platformFilter].some(k=>k.toLowerCase()===(row.platform||''));
      return platOk;
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
      // Sort every level by the active column / direction.
      const {{key,dir}}=explorerSort, mul=dir==='asc'?1:-1;
      const cmpName=(x,y)=>mul*String(x).localeCompare(String(y),undefined,{{numeric:true}});
      const cmpMetric=(x,y)=>mul*(explorerMetricVal(x,key)-explorerMetricVal(y,key));
      const cmpNode=(a,b)=> key==='name' ? cmpName(a[1].name,b[1].name) : cmpMetric(a[1].metrics,b[1].metrics);
      for (const camp of campaigns.values()) {{
        for (const grp of camp.groups.values()) {{
          grp.ads.sort((a,b)=> key==='name' ? cmpName(explorerAdName(a),explorerAdName(b)) : cmpMetric(a,b));
        }}
        camp.groups=new Map([...camp.groups.entries()].sort(cmpNode));
      }}
      return new Map([...campaigns.entries()].sort(cmpNode));
    }}
    function metricCells(m) {{ const wc=withCtr(m); return METRIC_COLS.map(c=>`<td>${{c.format(wc[c.key])}}</td>`).join(''); }}
    // Brand marks for the platform column — inline SVG so the tree reads as a
    // sleek icon rail instead of text pills. Google = 4-colour G, LinkedIn +
    // Meta = their single-path glyphs in brand colours.
    const PLATFORM_SVG = {{
      google: '<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>',
      linkedin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#0A66C2" d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.55V9h3.57v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.72V1.72C24 .77 23.2 0 22.22 0z"/></svg>',
      meta: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#0668E1" d="M6.915 4.03c-1.968 0-3.683 1.28-4.871 3.113C.704 9.208 0 11.883 0 14.449c0 .706.07 1.369.21 1.973a6.624 6.624 0 0 0 .265.86 5.297 5.297 0 0 0 .371.761c.696 1.159 1.818 1.927 3.593 1.927 1.497 0 2.633-.671 3.965-2.444.76-1.012 1.144-1.626 2.663-4.32l.756-1.339.186-.325c.061.1.121.196.183.294l2.152 3.595c.724 1.21 1.665 2.556 2.47 3.314 1.046.987 1.992 1.22 3.06 1.22 1.075 0 1.876-.355 2.455-.843a3.743 3.743 0 0 0 .81-.973c.542-.939.861-2.127.861-3.745 0-2.72-.681-5.357-2.084-7.45-1.282-1.912-2.957-2.93-4.716-2.93-1.047 0-2.088.467-3.053 1.308-.652.57-1.257 1.29-1.82 2.05-.69-.875-1.335-1.547-1.958-2.056-1.182-.966-2.315-1.303-3.454-1.303zm10.16 2.053c1.147 0 2.188.758 2.992 1.999 1.132 1.748 1.647 4.195 1.647 6.4 0 1.548-.368 2.9-1.839 2.9-.58 0-1.027-.235-1.664-1.001-.496-.601-1.343-1.878-2.832-4.358l-.617-1.028a44.908 44.908 0 0 0-1.255-1.98c.07-.109.141-.224.211-.327 1.12-1.667 2.118-2.605 3.325-2.605zm-10.201.553c1.265 0 2.058.791 2.675 1.446.307.327.737.871 1.234 1.579l-1.02 1.566c-.757 1.163-1.882 3.017-2.837 4.338-1.191 1.649-1.81 1.817-2.486 1.817-.524 0-1.038-.237-1.383-.794-.263-.426-.464-1.13-.464-2.046 0-2.221.63-4.535 1.66-6.088.454-.687.964-1.226 1.533-1.533a2.264 2.264 0 0 1 1.088-.282z"/></svg>',
    }};
    function platformIcon(p) {{
      const k=(p||'google').toLowerCase();
      const key=k==='linkedin'?'linkedin':k==='meta'?'meta':'google';
      const label=key==='linkedin'?'LinkedIn':key==='meta'?'Meta':'Google';
      return `<span class="plat-ico plat-ico-${{key}}" title="${{label}}" aria-label="${{label}}">${{PLATFORM_SVG[key]}}</span>`;
    }}
    function parseCopyList(v) {{
      if (Array.isArray(v)) return v.filter(Boolean);
      if (typeof v==='string' && v) {{ try {{ const a=JSON.parse(v); return Array.isArray(a)?a.filter(Boolean):[]; }} catch(e) {{ return []; }} }}
      return [];
    }}
    const HEADLINES_VISIBLE = 5;
    const ICON_SHUFFLE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';
    // Turn a final URL into a Google-style display path (host + first segment).
    function gadsDisplayUrl(ad) {{
      const raw=String(ad.final_url||'').trim();
      try {{
        const u=new URL(raw.match(/^https?:\\/\\//)?raw:('https://'+raw));
        const seg=u.pathname.split('/').filter(Boolean)[0];
        return u.hostname.replace(/^www\\./,'')+(seg?('/'+seg):'');
      }} catch(e) {{
        return (raw||'example.com').replace(/^https?:\\/\\//,'').replace(/^www\\./,'').split('/').slice(0,2).join('/');
      }}
    }}
    // Fisher–Yates pick of n items — how the shuffle randomizes the RSA mix.
    function gadsPick(arr,n) {{
      const a=arr.slice();
      for (let i=a.length-1;i>0;i--) {{ const j=Math.floor(Math.random()*(i+1)); const t=a[i]; a[i]=a[j]; a[j]=t; }}
      return a.slice(0,n);
    }}
    // Re-render a Google preview's shown assets (up to 3 headlines, 2 descriptions).
    // shuffle=true randomizes the mix like Google's RSA serving would.
    function gadsUpdatePreview(cell, shuffle) {{
      let hs=[], ds=[];
      try {{ hs=JSON.parse(cell.dataset.hs||'[]'); }} catch(e) {{}}
      try {{ ds=JSON.parse(cell.dataset.ds||'[]'); }} catch(e) {{}}
      const h=shuffle?gadsPick(hs,3):hs.slice(0,3);
      const d=shuffle?gadsPick(ds,2):ds.slice(0,2);
      const t=cell.querySelector('.gads-title'); if (t) t.textContent=h.join(' | ');
      const de=cell.querySelector('.gads-desc'); if (de) de.textContent=d.join(' ');
    }}
    // Google search RSA → a realistic ad preview (3 headlines / 2 descriptions),
    // a shuffle button that re-rolls the mix, and the full asset list in an
    // accordion. Non-search / image ads fall through to the thumbnail layout.
    function googleAdCell(ad, hs, ds) {{
      const sub=ad.ad_id?`<span class="ad-label-sub"><span class="ad-id">#${{esc(ad.ad_id)}}</span></span>`:'';
      const disp=esc(gadsDisplayUrl(ad));
      const h0=hs.slice(0,3).map(esc).join(' | ');
      const d0=ds.slice(0,2).map(esc).join(' ');
      const allH=hs.map((v,i)=>`<span class="ad-copy-line"><span class="ad-copy-tag">H${{i+1}}</span>${{esc(v)}}</span>`).join('');
      const allD=ds.map((v,i)=>`<span class="ad-copy-line"><span class="ad-copy-tag">D${{i+1}}</span>${{esc(v)}}</span>`).join('');
      const cnt=`${{hs.length}} headline${{hs.length===1?'':'s'}} · ${{ds.length}} description${{ds.length===1?'':'s'}}`;
      const acc=(hs.length>3||ds.length>2)
        ? `<button type="button" class="ad-copy-more" data-more-label="All assets (${{cnt}})">All assets (${{cnt}})</button><div class="ad-copy-extra" hidden>${{allH}}${{allD}}</div>`
        : '';
      return `<div class="ad-cell gads" data-hs="${{esc(JSON.stringify(hs))}}" data-ds="${{esc(JSON.stringify(ds))}}"><span class="ad-meta">
        <div class="gads-preview">
          <div class="gads-top"><span class="gads-badge">Ad</span><span class="gads-url">${{disp}}</span><button type="button" class="gads-shuffle" title="Shuffle asset mix" aria-label="Shuffle asset mix">${{ICON_SHUFFLE}}</button></div>
          <div class="gads-title">${{h0}}</div>
          <div class="gads-desc">${{d0}}</div>
        </div>
        ${{sub}}
        ${{acc}}
      </span></div>`;
    }}
    function adCell(ad) {{
      const platform=(ad.platform||'google').toLowerCase();
      // Full RSA copy (up to 15 headlines / 4 descriptions) from the JSON arrays;
      // fall back to the legacy flat columns for rows synced before the repull.
      let hs=parseCopyList(ad.headlines); if(!hs.length) hs=[ad.headline_1,ad.headline_2,ad.headline_3].filter(Boolean);
      let ds=parseCopyList(ad.descriptions); if(!ds.length) ds=[ad.description_1,ad.description_2].filter(Boolean);
      // Google search ads (text/RSA) get the true ad preview; image/display and
      // LinkedIn/Meta creatives keep the thumbnail-based layout below.
      if (platform==='google' && (hs.length||ds.length) && !ad.thumbnail_url) {{
        return googleAdCell(ad, hs, ds);
      }}
      const type=ad.media_type?`<span class="ad-type">${{esc(ad.media_type)}}</span>`:'';
      // Full-size preview prefers image_url/video_url over the (often smaller/
      // cropped) thumbnail_url; click opens it in the modal (see creativePreview).
      const fullImg=ad.image_url||ad.thumbnail_url||'';
      const thumb=ad.thumbnail_url?`<img class="ad-thumb" src="${{esc(ad.thumbnail_url)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'" data-preview-image="${{esc(fullImg)}}" data-preview-video="${{esc(ad.video_url||'')}}">` :'';
      // ad_name is often blank for RSAs; prefer it, then the first headline, then
      // the raw ad ID as a last resort (shown small/muted, not as the headline label).
      const label=esc(ad.ad_name || hs[0] || ad.ad_label || '—');
      const idTag=ad.ad_id?`<span class="ad-id">#${{esc(ad.ad_id)}}</span>`:'';
      const visible=hs.slice(0, HEADLINES_VISIBLE), extra=hs.slice(HEADLINES_VISIBLE);
      const visibleLines=visible.map((v,i)=>['H'+(i+1),v]);
      const extraLines=extra.map((v,i)=>['H'+(i+1+HEADLINES_VISIBLE),v]);
      const descLines=ds.map((v,i)=>['D'+(i+1),v]);
      const line=([tag,v])=>`<span class="ad-copy-line"><span class="ad-copy-tag">${{tag}}</span>${{esc(v)}}</span>`;
      const visibleHtml=visibleLines.filter(([,v])=>v).map(line).join('');
      const extraHtml=extraLines.filter(([,v])=>v).map(line).join('');
      const descHtml=descLines.filter(([,v])=>v).map(line).join('');
      const more=extra.length
        ? `<button type="button" class="ad-copy-more" data-more-label="+${{extra.length}} more">+${{extra.length}} more</button><div class="ad-copy-extra" hidden>${{extraHtml}}</div>`
        : '';
      const copyLines=visibleHtml+more+descHtml;
      const copy=copyLines?`<div class="ad-copy">${{copyLines}}</div>`:'';
      return `<div class="ad-cell">${{thumb}}<span class="ad-meta"><span class="ad-label">${{label}}${{idTag}}</span>${{type}}${{copy}}</span></div>`;
    }}
    function renderExplorer() {{
      const filtered=explorerRows.filter(explorerRowMatches);
      // Aggregate summary cards — slice with the same filters as the table
      // (date range, Platform chips, and the explorer filter chips).
      const agg=withCtr(filtered.reduce((a,r)=>{{addMetrics(a,r);return a;}}, zeroMetrics()));
      const scards=document.getElementById('explorerSummaryCards');
      if (scards) scards.innerHTML=[
        ['Spend',money(agg.spend)],['Impressions',count(agg.impressions)],['Clicks',count(agg.clicks)],
        ['Conversions',count(agg.conversions)],['CTR',num(agg.ctr).toFixed(2)+'%'],
      ].map(([l,v])=>`<div class="card"><div class="card-title">${{l}}</div><div class="card-value">${{v}}</div></div>`).join('');
      const el=document.getElementById('explorerTable');
      const tree=buildExplorerTree(filtered);
      if (!tree.size) {{ el.innerHTML=`<tbody><tr><td class="empty">No campaigns match these filters.</td></tr></tbody>`; }} else {{
        const sArrow=k=>explorerSort.key===k?(explorerSort.dir==='asc'?' ▲':' ▼'):'';
        const head=`<thead><tr><th class="left expl-sort${{explorerSort.key==='name'?' active':''}}" data-key="name">Campaign / Ad group / Ad${{sArrow('name')}}</th>${{METRIC_COLS.map(c=>`<th class="expl-sort${{explorerSort.key===c.key?' active':''}}" data-key="${{c.key}}">${{esc(c.label)}}${{sArrow(c.key)}}</th>`).join('')}}</tr></thead>`;
        let body='', cIdx=0;
        for (const camp of tree.values()) {{
          const cId='c'+(cIdx++), gCount=camp.groups.size;
          body+=`<tr class="tree-row lvl-campaign" data-id="${{cId}}" data-expandable="1"><td class="left"><span class="caret"></span>${{platformIcon(camp.platform)}}<span class="tree-name">${{esc(camp.name)}}</span> <span class="muted">(${{gCount}} ad group${{gCount===1?'':'s'}})</span></td>${{metricCells(camp.metrics)}}</tr>`;
          let gIdx=0;
          for (const grp of camp.groups.values()) {{
            const gId=cId+'g'+(gIdx++), aCount=grp.ads.length;
            body+=`<tr class="tree-row lvl-group" data-id="${{gId}}" data-parent="${{cId}}" data-expandable="1" hidden><td class="left"><span class="indent1"></span><span class="caret"></span><span class="tree-name">${{esc(grp.name)}}</span> <span class="muted">(${{aCount}} ad${{aCount===1?'':'s'}})</span></td>${{metricCells(grp.metrics)}}</tr>`;
            for (const ad of grp.ads) {{ body+=`<tr class="tree-row lvl-ad" data-parent="${{gId}}" hidden><td class="left"><span class="indent2"></span>${{adCell(ad)}}</td>${{metricCells(ad)}}</tr>`; }}
          }}
        }}
        el.innerHTML=head+`<tbody>${{body}}</tbody>`;
      }}
      const filterActive=[...explorerFilterState.values()].some(s=>s.size);
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
        out.push({{platform:'google',campaign_name:r.campaign_name,ad_group_name:r.ad_group_name,ad_label:r.ad_label,ad_id:r.ad_id,headlines:r.headlines,descriptions:r.descriptions,headline_1:r.headline_1,headline_2:r.headline_2,headline_3:r.headline_3,description_1:r.description_1,description_2:r.description_2,ad_name:r.ad_name,final_url:r.final_url,ad_type:r.ad_type,thumbnail_url:'',media_type:r.ad_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)}});
      }}
      for (const r of (linkedin&&linkedin.rows?linkedin.rows:[])) {{
        out.push({{platform:'linkedin',campaign_name:r.campaign_group_name||r.campaign_name,ad_group_name:r.campaign_name,ad_label:r.creative_name,thumbnail_url:r.thumbnail_url||r.image_url||'',image_url:r.image_url||'',video_url:r.video_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)}});
      }}
      for (const r of (meta&&meta.rows?meta.rows:[])) {{
        out.push({{platform:'meta',campaign_name:r.campaign_name,ad_group_name:r.adset_name,ad_label:r.ad_name,thumbnail_url:r.thumbnail_url||r.image_url||'',image_url:r.image_url||'',video_url:r.video_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)}});
      }}
      return out;
    }}
    // "Keyword Performance" — Google Ads search keywords only; the section stays
    // hidden for clients with no keyword data. Summary cards + an insight
    // banner + a sortable/searchable table with match badges and in-cell bars,
    // all derived from the same keyword rows (no extra fetch).
    let kwAllRows=[];
    let kwSort={{ key:'spend', dir:'desc' }};
    let kwSearch='';
    let kwMatchFilter=new Set();  // uppercased match types; empty = all
    const KW_PER_PAGE=10; let kwPageNum=1;
    const KW_COLS=[
      {{key:'keyword_text',label:'Keyword',left:true}},
      {{key:'match_type',label:'Match',left:true}},
      {{key:'spend',label:'Spend'}},
      {{key:'impressions',label:'Impr.'}},
      {{key:'clicks',label:'Clicks'}},
      {{key:'ctr',label:'CTR'}},
      {{key:'conversions',label:'Conv.'}},
      {{key:'cvr',label:'CVR'}},
      {{key:'cpa',label:'CPA'}},
      {{key:'conversion_value',label:'Conv. value'}},
    ];
    const kwTitle=t=>t?t.charAt(0)+t.slice(1).toLowerCase():'';
    function kwMatchBadge(mt) {{
      const t=(mt||'').toUpperCase();
      if (!t) return '<span class="kw-sub">—</span>';
      const cls=t==='EXACT'?'badge-exact':t==='PHRASE'?'badge-phrase':'badge-broad';
      return `<span class="badge-match ${{cls}}">${{esc(kwTitle(t))}}</span>`;
    }}
    // Values without a defined metric (e.g. CPA with no conversions) return null
    // so the sort comparator can always sink them to the bottom.
    function kwSortVal(r,key) {{
      if (key==='keyword_text') return (r.keyword_text||'').toLowerCase();
      if (key==='match_type')   return (r.match_type||'').toLowerCase();
      if (key==='cvr')  return num(r.clicks) ? num(r.conversions)/num(r.clicks) : null;
      if (key==='cpa')  return num(r.conversions) ? num(r.spend)/num(r.conversions) : null;
      return num(r[key]);
    }}
    function kwFiltered() {{
      let rows=kwAllRows.slice();
      if (kwMatchFilter.size) rows=rows.filter(r=>kwMatchFilter.has((r.match_type||'').toUpperCase()));
      const q=kwSearch.trim().toLowerCase();
      if (q) rows=rows.filter(r=>((r.keyword_text||'')+' '+(r.ad_group_name||'')+' '+(r.campaign_name||'')).toLowerCase().includes(q));
      const {{key,dir}}=kwSort, mul=dir==='asc'?1:-1;
      rows.sort((a,b)=>{{
        const x=kwSortVal(a,key), y=kwSortVal(b,key);
        const xb=(x==null), yb=(y==null);
        if (xb&&yb) return 0; if (xb) return 1; if (yb) return -1;
        if (typeof x==='string') return mul*x.localeCompare(y,undefined,{{numeric:true}});
        return mul*(x-y);
      }});
      return rows;
    }}
    function renderKeywordInsight() {{
      const el=document.getElementById('keywordInsight');
      if (!el) return;
      const items=[];
      const bySpend=kwAllRows.slice().sort((a,b)=>num(b.spend)-num(a.spend));
      const total=bySpend.reduce((s,r)=>s+num(r.spend),0);
      const topN=Math.min(3, bySpend.length);
      if (total>0 && topN) {{
        const topSpend=bySpend.slice(0,topN).reduce((s,r)=>s+num(r.spend),0);
        const pct=Math.round(topSpend/total*100);
        const info='<svg class="kw-ins-icon" viewBox="0 0 24 24" fill="none" stroke="#1d6fd0" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M12 11v5" stroke-linecap="round"/><circle cx="12" cy="7.6" r="1.1" fill="#1d6fd0" stroke="none"/></svg>';
        items.push(`<span class="kw-ins">${{info}}<span><strong>${{pct}}% of spend</strong> came from the top ${{topN}} keyword${{topN===1?'':'s'}}.</span></span>`);
      }}
      const wasteful=kwAllRows.filter(r=>num(r.spend)>100 && !num(r.conversions)).length;
      if (wasteful) {{
        const warn='<svg class="kw-ins-icon" viewBox="0 0 24 24" fill="none" stroke="#c98a00" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 1.8 20.5h20.4z"/><path d="M12 10v4"/><circle cx="12" cy="17.4" r="0.6" fill="#c98a00" stroke="none"/></svg>';
        items.push(`<span class="kw-ins kw-ins-warn">${{warn}}<span><strong>${{wasteful}} keyword${{wasteful===1?'':'s'}}</strong> spent over $100 without a conversion.</span></span>`);
      }}
      el.innerHTML=items.join('');
      el.hidden=!items.length;
    }}
    function buildKeywordControls() {{
      const host=document.getElementById('keywordMatchChips');
      if (!host) return;
      const order=['EXACT','PHRASE','BROAD'];
      const types=[...new Set(kwAllRows.map(r=>(r.match_type||'').toUpperCase()).filter(Boolean))]
        .sort((a,b)=>(order.indexOf(a)<0?9:order.indexOf(a))-(order.indexOf(b)<0?9:order.indexOf(b)));
      // A single match type isn't worth a filter row.
      host.innerHTML = types.length<2 ? '' : ['All',...types].map(t=>
        `<button type="button" class="chip" data-match="${{esc(t)}}">${{t==='All'?'All':esc(kwTitle(t))}}</button>`).join('');
      syncKeywordChips();
    }}
    function syncKeywordChips() {{
      document.querySelectorAll('#keywordMatchChips .chip').forEach(b=>{{
        const m=b.dataset.match;
        b.classList.toggle('active', m==='All' ? !kwMatchFilter.size : kwMatchFilter.has(m));
      }});
    }}
    function renderKeywordTable() {{
      const rows=kwFiltered();
      const total=kwAllRows.length;
      const el=document.getElementById('keywordTable');
      const arrow=k=>kwSort.key===k?(kwSort.dir==='asc'?' ▲':' ▼'):'';
      const head=`<thead><tr>${{KW_COLS.map(c=>`<th class="expl-sort${{c.left?' left':''}}${{kwSort.key===c.key?' active':''}}" data-key="${{c.key}}">${{esc(c.label)}}${{arrow(c.key)}}</th>`).join('')}}</tr></thead>`;
      if (!rows.length) {{
        el.innerHTML=head+`<tbody><tr><td class="empty" colspan="${{KW_COLS.length}}">No keywords match these filters.</td></tr></tbody>`;
        setStatus('keywordStatus', total?`0 of ${{total}} keyword(s)`:'No keyword data');
        document.getElementById('keywordPager').innerHTML='';
        return;
      }}
      // Bars scale to the whole filtered set (not just the page) so they stay
      // comparable as you move between pages.
      const maxSpend=Math.max(...rows.map(r=>num(r.spend)), 0);
      const maxCpa=Math.max(...rows.map(r=>num(r.conversions)?num(r.spend)/num(r.conversions):0), 0);
      const totalPages=Math.max(1, Math.ceil(rows.length/KW_PER_PAGE));
      if (kwPageNum>totalPages) kwPageNum=totalPages;
      const startIdx=(kwPageNum-1)*KW_PER_PAGE;
      const pageRows=rows.slice(startIdx, startIdx+KW_PER_PAGE);
      const body=pageRows.map(r=>{{
        const clk=num(r.clicks), conv=num(r.conversions), spend=num(r.spend);
        const spendCell=`<div class="cell-bar"><span class="cell-bar-val">${{money(spend)}}</span>${{pctBar(maxSpend?spend/maxSpend*100:0)}}</div>`;
        const ctr=r.ctr==null?'—':num(r.ctr).toFixed(2)+'%';
        const cvrCell=clk?`<span class="${{conv?'num-good':'num-bad'}}">${{(conv/clk*100).toFixed(2)}}%</span>`:'—';
        let cpaCell;
        if (conv) {{
          const cpa=spend/conv;
          cpaCell=`<div class="cell-bar"><span class="cell-bar-val">${{money(cpa)}}</span>${{pctBar(maxCpa?cpa/maxCpa*100:0)}}</div>`;
        }} else {{
          const flag=spend>0?`<span class="cell-flag" title="Spent without a conversion">&#9888;</span>`:'';
          cpaCell=`<span class="cell-bar-val">—</span>${{flag}}`;
        }}
        return `<tr>`+
          `<td class="left"><div class="kw-name">${{esc(r.keyword_text||'—')}}</div>${{r.ad_group_name?`<div class="kw-sub">${{esc(r.ad_group_name)}}</div>`:''}}</td>`+
          `<td class="left">${{kwMatchBadge(r.match_type)}}</td>`+
          `<td>${{spendCell}}</td>`+
          `<td>${{count(r.impressions)}}</td>`+
          `<td>${{count(clk)}}</td>`+
          `<td>${{ctr}}</td>`+
          `<td>${{count(conv)}}</td>`+
          `<td>${{cvrCell}}</td>`+
          `<td>${{cpaCell}}</td>`+
          `<td>${{r.conversion_value==null?'—':money(r.conversion_value)}}</td>`+
        `</tr>`;
      }}).join('');
      el.innerHTML=head+`<tbody>${{body}}</tbody>`;
      const filtered=rows.length!==total;
      const suffix=filtered?` (of ${{total}})`:'';
      const status=totalPages>1
        ? `${{startIdx+1}}–${{startIdx+pageRows.length}} of ${{rows.length}} keyword(s)${{suffix}}`
        : `${{rows.length}} keyword(s)${{suffix}}`;
      setStatus('keywordStatus', status);
      renderKeywordPager(totalPages);
    }}
    function renderKeywordPager(totalPages) {{
      const pager=document.getElementById('keywordPager');
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" id="kwPrev"${{kwPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{kwPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" id="kwNext"${{kwPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      const prev=document.getElementById('kwPrev'), next=document.getElementById('kwNext');
      if (prev) prev.onclick=()=>{{ if(kwPageNum>1){{ kwPageNum--; renderKeywordTable(); }} }};
      if (next) next.onclick=()=>{{ if(kwPageNum<totalPages){{ kwPageNum++; renderKeywordTable(); }} }};
    }}
    function renderKeywords() {{
      renderKeywordInsight();
      renderKeywordTable();
    }}
    async function loadExplorer() {{
      setStatus('explorerStatus','Loading…');
      document.getElementById('explorerSummaryCards').innerHTML = skelCards(5);
      document.getElementById('explorerTable').innerHTML = skelTable(6,8);
      const [g,l,m,kw]=await Promise.all([
        getJson(withDates(EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(LINKEDIN_EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(META_EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(GOOGLE_ADS_KEYWORDS_API)).catch(()=>({{rows:[]}})),
      ]);
      explorerRows=normalizeExplorerRows(g,l,m);
      renderExplorer();
      // Keyword table: only show the section when this client actually has
      // Google Ads search-keyword data (empty for LinkedIn/Meta-only clients).
      kwAllRows=(kw&&kw.rows)||[];
      const kwSec=document.getElementById('sec-keywords');
      if (kwAllRows.length) {{
        kwSec.style.display='';
        kwPageNum=1;
        buildKeywordControls();
        renderKeywords();
      }} else {{
        kwSec.style.display='none';
      }}
      loadPaidSources();
    }}

    // ---- GA4: Top pages ----
    let pagesTopRows=[], pagesSourceRows=[], pagesSearchQuery='';
    let pagesEventMap={{}};   // page_path -> {{ event_name: count }}, from TOP_PAGES_KEY_EVENTS_API
    // Recompute key_events per row from the global key-event selection, same
    // fallback rule as Traffic/User acquisition: only override once our own
    // event map actually has data, otherwise keep the base report's real value.
    function applyPageEvents(rows) {{
      if (!Object.keys(pagesEventMap).length) return rows;
      return rows.map(r=>({{...r, key_events:keSum(pagesEventMap, r.page_path)}}));
    }}
    const PAGES_PER_PAGE=10; let pagesPageNum=1;
    // Cap Top pages to the top N by views — the long tail past this is almost
    // always checkout steps and one-off paths that just add noise.
    const PAGES_TOP_LIMIT=50;
    // Keys match fn_ga4_source_platform's paid_* outputs (bq_ga4_mart_service).
    const PAID_SOURCE_LABELS={{paid_google:'Google Ads',paid_microsoft:'Microsoft Ads',paid_social:'Paid social',paid_linkedin:'LinkedIn',paid_meta:'Meta'}};
    function paidLabel(src) {{ return PAID_SOURCE_LABELS[src]||String(src).replace(/^paid_/,'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()); }}
    // Per-page source / AI-referral rows (vw_page_path_source_daily), shared by
    // the AI Traffic tab and the Campaign Explorer paid-source module. Fetched
    // once per date range and memoized so switching between them is instant.
    let pagesSourceLoadedFor=null;
    async function ensurePagesSources() {{
      const k=currentStart+'|'+currentEnd;
      if (pagesSourceLoadedFor===k) return pagesSourceRows;
      const src=await getJson(withDates(PAGES_SOURCES_API)).catch(()=>({{rows:[]}}));
      pagesSourceRows=src.rows||[]; pagesSourceLoadedFor=k;
      return pagesSourceRows;
    }}
    function renderPages() {{
      let base=applyPageEvents(pagesTopRows);
      // Rows are already sorted by views desc (server ORDER BY), so slicing
      // keeps the top N. Search then filters within that top set.
      base=base.slice(0, PAGES_TOP_LIMIT);
      if (pagesSearchQuery) {{ const q=pagesSearchQuery.toLowerCase(); base=base.filter(p=>p.page_path.toLowerCase().includes(q)); }}
      const el=document.getElementById('pagesTable');
      if (!base.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No pages match${{pagesSearchQuery?' "'+esc(pagesSearchQuery)+'"':''}}.</td></tr></tbody>`; setStatus('pagesStatus','No results'); document.getElementById('pagesPager').innerHTML=''; return; }}
      const totalPages=Math.max(1,Math.ceil(base.length/PAGES_PER_PAGE));
      if (pagesPageNum>totalPages) pagesPageNum=totalPages;
      const startIdx=(pagesPageNum-1)*PAGES_PER_PAGE;
      const pageRows=base.slice(startIdx,startIdx+PAGES_PER_PAGE);
      el.innerHTML=`<thead><tr><th class="left">Page</th><th>Views</th><th>Users</th><th>Key events</th><th>Avg engt</th></tr></thead>`+
        `<tbody>${{pageRows.map(p=>{{const engt=p.users?p.engagement_seconds/p.users:0;return`<tr><td class="left"><span class="page-path" title="${{esc(p.page_path)}}">${{esc(p.page_path)}}</span></td><td>${{count(p.page_views)}}</td><td>${{count(p.users)}}</td><td>${{count(p.key_events)}}</td><td>${{fmtDuration(engt)}}</td></tr>`;}}). join('')}}</tbody>`;
      const tag=pagesSearchQuery?' (filtered)':'';
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
    async function loadPages() {{
      setStatus('pagesStatus','Loading…');
      document.getElementById('pagesTable').innerHTML = skelTable(5,8);
      const [top,ev]=await Promise.all([
        getJson(withDates(PAGES_TOP_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(TOP_PAGES_KEY_EVENTS_API)).catch(()=>({{rows:[],events:[]}})),
      ]);
      pagesTopRows=top.rows||[]; pagesPageNum=1;
      pagesEventMap={{}};
      for (const r of (ev.rows||[])) {{
        (pagesEventMap[r.page_path]=pagesEventMap[r.page_path]||{{}})[r.event_name]=num(r.event_count);
      }}
      mergeEvents(ev.events);
      renderPages();
    }}
    (function(){{
      const inp=document.getElementById('pagesSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{{ clearTimeout(debounce); debounce=setTimeout(()=>{{pagesSearchQuery=inp.value.trim();pagesPageNum=1;renderPages();}},180); }});
    }})();

    // ---- AI Traffic tab (from vw_page_path_source_daily, is_ai_referral) ----
    let aiRows=[], aiPagesSearchQuery='', aiSourceFilter=new Set();
    const AI_PALETTE=['#1d6fd0','#7c3aed','#0a7f3f','#dc2626','#d97706','#0891b2','#be185d','#4b5563'];
    // Stacked-area trend: sessions/day, one band per AI platform. Data comes from
    // the daily endpoint (the range-aggregated pages/sources has no time axis).
    // AI-traffic trend granularity (Daily/Weekly chips), mirroring the sessions
    // trend. Daily rows are re-bucketed to Monday-start weeks client-side: each
    // row's date is remapped to its week start, and renderAiTrend's per-date sum
    // collapses the days into weeks. Only drives the main tab chart (default
    // chartId), not the overview mini-chart.
    let aiTrendGran = 'daily';
    let aiTrendDailyCache = [];
    function aggregateAiWeekly(rows) {{
      return (rows || []).map(function(r) {{
        const dt = new Date(String(r.date) + 'T00:00:00');
        const dow = (dt.getDay() + 6) % 7;            // 0 = Monday
        const mon = new Date(dt); mon.setDate(dt.getDate() - dow);
        const key = `${{mon.getFullYear()}}-${{String(mon.getMonth()+1).padStart(2,'0')}}-${{String(mon.getDate()).padStart(2,'0')}}`;
        return Object.assign({{}}, r, {{ date: key }});
      }});
    }}
    function renderAiTrendGran() {{
      renderAiTrend(aiTrendGran === 'weekly' ? aggregateAiWeekly(aiTrendDailyCache) : aiTrendDailyCache);
    }}
    document.querySelectorAll('#aiTrendGranChips .chip').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        if (btn.dataset.gran === aiTrendGran) return;
        aiTrendGran = btn.dataset.gran;
        document.querySelectorAll('#aiTrendGranChips .chip').forEach(b => b.classList.toggle('active', b === btn));
        renderAiTrendGran();
      }});
    }});
    function renderAiTrend(daily, chartId, statusId) {{
      chartId=chartId||'aiTrendChart'; statusId=statusId||'aiTrendStatus';
      const rows=daily||[];
      const dates=[...new Set(rows.map(r=>String(r.date)))].sort();
      const pivot=new Map();   // platform -> Map(date -> sessions)
      for (const r of rows) {{
        const pl=r.ai_platform||'Unknown';
        let m=pivot.get(pl); if(!m){{m=new Map();pivot.set(pl,m);}}
        m.set(String(r.date),(m.get(String(r.date))||0)+num(r.sessions));
      }}
      // Biggest platform first so it sits at the bottom of the stack.
      const ordered=[...pivot.keys()].map(pl=>[pl,[...pivot.get(pl).values()].reduce((a,b)=>a+b,0)]).sort((a,b)=>b[1]-a[1]);
      if (!dates.length || !ordered.length) {{ __destroyChart(chartId); setStatus(statusId,'No AI traffic in this range.'); return; }}
      // Stack the data ourselves (scales.y.stacked doesn't stack lines in this
      // Chart.js build): each series plots the running cumulative total and fills
      // down to the series below it. _raw keeps the per-platform value for tooltips.
      const cum=dates.map(()=>0);
      const datasets=ordered.map(([pl],i)=>{{
        const color=AI_PALETTE[i%AI_PALETTE.length], m=pivot.get(pl);
        const raw=dates.map(d=>m.get(d)||0);
        const data=raw.map((v,idx)=>(cum[idx]+=v));
        return {{ label:pl, data, _raw:raw, borderColor:color, backgroundColor:color+'59',
                  fill:i===0?'origin':'-1', borderWidth:2, tension:0.3, pointRadius:0, pointHoverRadius:4 }};
      }});
      __chart(chartId, {{
        type:'line',
        data:{{ labels:dates.map(d=>d.slice(5)), datasets }},
        options:{{
          interaction:{{mode:'index',intersect:false}},
          scales:{{
            x:{{ grid:{{display:false}}, border:{{display:false}}, ticks:{{maxRotation:0,autoSkip:true,maxTicksLimit:8}} }},
            y:{{ beginAtZero:true, grid:{{color:'#f1f4f9'}}, border:{{display:false}}, ticks:{{maxTicksLimit:5}} }},
          }},
          plugins:{{
            legend:{{ display:true, position:'bottom', labels:{{usePointStyle:true, boxWidth:8, padding:12}} }},
            tooltip:{{ callbacks:{{ label:c=>`${{c.dataset.label}}: ${{count(c.dataset._raw[c.dataIndex])}}` }} }},
          }},
        }},
      }});
      setStatus(statusId,'');
    }}
    async function loadAiTraffic() {{
      setStatus('aiTrafficStatus','Loading…');
      setStatus('aiTrendStatus','Loading…');
      document.getElementById('aiSourcesTable').innerHTML=skelTable(5,5);
      document.getElementById('aiPagesTable').innerHTML=skelTable(4,8);
      const [rows, daily]=await Promise.all([
        ensurePagesSources().then(rs=>rs.filter(r=>r.is_ai_referral)),
        getJson(withDates(AI_TRAFFIC_DAILY_API)).then(d=>d.rows||[]).catch(()=>[]),
      ]);
      aiTrendDailyCache = daily;
      renderAiTrendGran();
      const bySrc=new Map();
      for (const r of rows) {{
        const key=r.ai_platform||'Unknown';
        let g=bySrc.get(key); if(!g){{g={{source:key,sessions:0,users:0,page_views:0,engagement_seconds:0}};bySrc.set(key,g);}}
        g.sessions+=num(r.sessions);g.users+=num(r.users);g.page_views+=num(r.page_views);g.engagement_seconds+=num(r.engagement_seconds);
      }}
      const srcRows=[...bySrc.values()].sort((a,b)=>b.sessions-a.sessions);
      renderTable('aiSourcesTable',[
        {{key:'source',label:'AI source',left:true}},
        {{key:'sessions',label:'Sessions',format:count}},
        {{key:'users',label:'Users',format:count}},
        {{key:'page_views',label:'Page views',format:count}},
        {{key:'engt',label:'Avg engt',format:(_,r)=>fmtDuration(r.users?r.engagement_seconds/r.users:0)}},
      ],srcRows,'No AI-referred traffic in this range.');
      setStatus('aiTrafficStatus', srcRows.length?`${{srcRows.length}} source(s)`:'No AI traffic');
      aiRows=rows;
      buildAiSourceChips();
      renderAiPages();
    }}
    function buildAiSourceChips() {{
      const el=document.getElementById('aiPageSourceChips');
      if (!el) return;
      const sources=[...new Set(aiRows.map(r=>r.ai_platform).filter(Boolean))].sort();
      // Drop any selected source no longer present in this range.
      for (const s of [...aiSourceFilter]) if (!sources.includes(s)) aiSourceFilter.delete(s);
      el.innerHTML=[['__all__','All'],...sources.map(s=>[s,s])].map(([v,l])=>`<button type="button" class="chip" data-key="${{esc(v)}}">${{esc(l)}}</button>`).join('');
      const sync=()=>el.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active', b.dataset.key==='__all__'?aiSourceFilter.size===0:aiSourceFilter.has(b.dataset.key)));
      el.querySelectorAll('.chip').forEach(btn=>btn.addEventListener('click',()=>{{
        const key=btn.dataset.key;
        if(key==='__all__')aiSourceFilter.clear();else if(aiSourceFilter.has(key))aiSourceFilter.delete(key);else aiSourceFilter.add(key);
        sync();renderAiPages();
      }}));
      sync();
    }}
    function renderAiPages() {{
      const src=aiSourceFilter.size?aiRows.filter(r=>aiSourceFilter.has(r.ai_platform)):aiRows;
      const byPage=new Map();
      for (const r of src) {{
        let g=byPage.get(r.page_path); if(!g){{g={{page_path:r.page_path,sessions:0,users:0,page_views:0}};byPage.set(r.page_path,g);}}
        g.sessions+=num(r.sessions);g.users+=num(r.users);g.page_views+=num(r.page_views);
      }}
      let base=[...byPage.values()].sort((a,b)=>b.sessions-a.sessions);
      if (aiPagesSearchQuery) {{ const q=aiPagesSearchQuery.toLowerCase(); base=base.filter(p=>p.page_path.toLowerCase().includes(q)); }}
      base=base.slice(0,PAGES_TOP_LIMIT);
      renderTable('aiPagesTable',[
        {{key:'page_path',label:'Page',left:true}},
        {{key:'sessions',label:'Sessions',format:count}},
        {{key:'users',label:'Users',format:count}},
        {{key:'page_views',label:'Page views',format:count}},
      ],base,(aiPagesSearchQuery||aiSourceFilter.size)?'No pages match.':'No AI-referred pages in this range.');
      const tag=aiSourceFilter.size?` · ${{[...aiSourceFilter].join(', ')}}`:'';
      setStatus('aiPagesStatus', base.length?`${{base.length}} page(s)${{tag}}`:'');
    }}
    (function(){{
      const inp=document.getElementById('aiPagesSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{{ clearTimeout(debounce); debounce=setTimeout(()=>{{aiPagesSearchQuery=inp.value.trim();renderAiPages();}},180); }});
    }})();

    // ---- Campaign Explorer: paid-source module (paid_* from source_platform) ----
    async function loadPaidSources() {{
      const sec=document.getElementById('sec-paid-sources');
      setStatus('paidSourceStatus','Loading…');
      const rows=(await ensurePagesSources()).filter(r=>r.source_platform&&r.source_platform.startsWith('paid_'));
      if (!rows.length) {{ if(sec) sec.style.display='none'; setStatus('paidSourceStatus',''); return; }}
      if (sec) sec.style.display='';
      // Channel -> Campaign (utm_campaign) -> Page, each level summing sessions/users/views.
      const addMetrics=(g,r)=>{{g.sessions+=num(r.sessions);g.users+=num(r.users);g.page_views+=num(r.page_views);}};
      const byChan=new Map();
      for (const r of rows) {{
        let c=byChan.get(r.source_platform);
        if(!c){{c={{source:r.source_platform,sessions:0,users:0,page_views:0,camps:new Map()}};byChan.set(r.source_platform,c);}}
        addMetrics(c,r);
        const campName=(r.utm_campaign&&String(r.utm_campaign).trim())||'(not set)';
        let cm=c.camps.get(campName);
        if(!cm){{cm={{campaign:campName,sessions:0,users:0,page_views:0,pages:new Map()}};c.camps.set(campName,cm);}}
        addMetrics(cm,r);
        let p=cm.pages.get(r.page_path);
        if(!p){{p={{page_path:r.page_path,sessions:0,users:0,page_views:0}};cm.pages.set(r.page_path,p);}}
        addMetrics(p,r);
      }}
      const chanRows=[...byChan.values()].sort((a,b)=>b.sessions-a.sessions);
      const cells=g=>`<td>${{count(g.sessions)}}</td><td>${{count(g.users)}}</td><td>${{count(g.page_views)}}</td>`;
      const head=`<thead><tr><th class="left">Paid channel / Campaign / Page</th><th>Sessions</th><th>Users</th><th>Page views</th></tr></thead>`;
      let body='', ci=0;
      for (const c of chanRows) {{
        const cId='pc'+(ci++), camps=[...c.camps.values()].sort((a,b)=>b.sessions-a.sessions);
        body+=`<tr class="tree-row lvl-campaign" data-id="${{cId}}" data-expandable="1"><td class="left"><span class="caret"></span><span class="tree-name">${{esc(paidLabel(c.source))}}</span> <span class="muted">(${{camps.length}} campaign${{camps.length===1?'':'s'}})</span></td>${{cells(c)}}</tr>`;
        let mi=0;
        for (const cm of camps) {{
          const mId=cId+'m'+(mi++), pages=[...cm.pages.values()].sort((a,b)=>b.sessions-a.sessions).slice(0,PAGES_TOP_LIMIT);
          body+=`<tr class="tree-row lvl-group" data-id="${{mId}}" data-parent="${{cId}}" data-expandable="1" hidden><td class="left"><span class="indent1"></span><span class="caret"></span><span class="tree-name">${{esc(cm.campaign)}}</span> <span class="muted">(${{cm.pages.size}} page${{cm.pages.size===1?'':'s'}})</span></td>${{cells(cm)}}</tr>`;
          for (const p of pages) {{
            body+=`<tr class="tree-row lvl-ad" data-parent="${{mId}}" hidden><td class="left"><span class="indent2"></span><span class="page-path" title="${{esc(p.page_path)}}">${{esc(p.page_path)}}</span></td>${{cells(p)}}</tr>`;
          }}
        }}
      }}
      document.getElementById('paidSourceTable').innerHTML=head+`<tbody>${{body}}</tbody>`;
      setStatus('paidSourceStatus', `${{chanRows.length}} channel(s)`);
    }}
    (function(){{
      const t=document.getElementById('paidSourceTable');
      if (!t) return;
      t.addEventListener('click',ev=>{{ const row=ev.target.closest('tr[data-expandable]'); if (row) toggleExplorerRow(row); }});
    }})();

    // ---- GA4: Traffic acquisition ----
    // Sessions/day for the current period (solid blue, filled) with the prior
    // period overlaid (dashed grey), aligned day-for-day by index so the shapes
    // line up regardless of the actual calendar dates.
    // Sessions-over-time granularity (Daily/Weekly chips). Daily rows are fetched
    // once and re-bucketed to weeks client-side, so switching is instant and needs
    // no extra query. Weeks start Monday; the week's label is its start date.
    let sessionsGran = 'daily';
    let sessionsTrendCache = {{ cur: [], prev: [] }};
    function aggregateWeekly(daily) {{
      if (!daily || !daily.length) return [];
      const out = []; let cur = null;
      for (const d of daily) {{
        const dt = new Date(String(d.date) + 'T00:00:00');
        const dow = (dt.getDay() + 6) % 7;            // 0 = Monday
        const mon = new Date(dt); mon.setDate(dt.getDate() - dow);
        const key = `${{mon.getFullYear()}}-${{String(mon.getMonth()+1).padStart(2,'0')}}-${{String(mon.getDate()).padStart(2,'0')}}`;
        if (!cur || cur.date !== key) {{ cur = {{ date: key, sessions: 0 }}; out.push(cur); }}
        cur.sessions += num(d.sessions);
      }}
      return out;
    }}
    function renderSessionsTrend() {{
      const c = sessionsTrendCache;
      if (sessionsGran === 'weekly') drawSessionsTrend(aggregateWeekly(c.cur), aggregateWeekly(c.prev));
      else drawSessionsTrend(c.cur, c.prev);
    }}
    function drawSessionsTrend(daily, prevDaily) {{
      clearSkelChart('sessionsTrendChart');
      const legend=document.getElementById('sessionsTrendLegend');
      const n=daily.length;
      if (!n) {{ __destroyChart('sessionsTrendChart'); if(legend) legend.innerHTML=''; return; }}
      const vals=daily.map(d=>num(d.sessions));
      const prevVals=(prevDaily||[]).map(d=>num(d.sessions));
      const hasPrev=prevVals.length>0;
      const labels=daily.map(d=>String(d.date).slice(5));
      const series=[];
      // Previous first so the current line renders on top of it.
      if (hasPrev) series.push({{ label:'Previous', data: prevVals.slice(0,n), color:'#9aa7bd', dashed:true }});
      series.push({{ label:'Current', data: vals, color:'#1d6fd0', fill:true }});
      lineChart('sessionsTrendChart', labels, series, {{
        yFmt: v => count(v),
        tooltip: {{ label: c => `${{c.dataset.label}}: ${{count(c.raw)}} sessions` }},
      }});
      if (legend) {{
        const curTot=vals.reduce((a,b)=>a+b,0), prevTot=prevVals.reduce((a,b)=>a+b,0);
        const delta=(hasPrev&&prevTot)?((curTot-prevTot)/prevTot*100):null;
        const deltaTxt=delta==null?'':` <span class="cmp-delta ${{delta>=0?'up':'down'}}">${{delta>=0?'+':''}}${{delta.toFixed(0)}}%</span>`;
        legend.innerHTML=`<span class="cmp-item"><span class="cmp-swatch cur"></span>Current (${{esc(currentStart.slice(5))}} – ${{esc(currentEnd.slice(5))}}) · ${{count(curTot)}} sessions${{deltaTxt}}</span>`
          + (hasPrev?`<span class="cmp-item"><span class="cmp-swatch prev"></span>Previous (${{esc(compareStart.slice(5))}} – ${{esc(compareEnd.slice(5))}}) · ${{count(prevTot)}}</span>`:'');
      }}
    }}
    function renderBarList(containerId, rows, valueKey, labelKey) {{
      const el=document.getElementById(containerId);
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; return; }}
      const total=rows.reduce((s,r)=>s+num(r[valueKey]),0);
      el.innerHTML=rows.map(r=>{{const p=total?num(r[valueKey])/total*100:0;return`<div class="bar-row"><div class="bar-label">${{esc(r[labelKey])}}</div>${{pctBar(p)}}<div class="bar-count">${{count(r[valueKey])}}<span class="bar-pct">${{p.toFixed(0)}}%</span></div></div>`;}}).join('');
    }}
    // Categorical palette for stacked/segmented charts (channels, gender, etc.).
    const CHART_PALETTE=['#1d6fd0','#7c3aed','#0a7f3f','#e08a1e','#d6336c','#0d9488','#5661b3','#b4530a','#3b7ddd','#8a4fbe'];
    // Traffic → single 100% bar, one segment per channel; hover shows channel,
    // sessions and share. A legend under the bar lists each channel + %.
    // Single 100% bar, one segment per channel. No legend — hovering a segment
    // shows its channel, sessions and share in a floating tooltip.
    function renderChannelStacked(rows) {{
      const el=document.getElementById('channelBars');
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; return; }}
      const ordered=[...rows].sort((a,b)=>num(b.sessions)-num(a.sessions));
      const total=ordered.reduce((s,r)=>s+num(r.sessions),0)||1;
      const seg=ordered.map((r,i)=>{{
        const p=num(r.sessions)/total*100, color=CHART_PALETTE[i%CHART_PALETTE.length];
        return`<div class="stack-seg" style="width:${{p.toFixed(2)}}%;background:${{color}}" data-label="${{esc(r.channel)}}" data-detail="${{count(r.sessions)}} sessions · ${{p.toFixed(1)}}%"></div>`;
      }}).join('');
      el.innerHTML=`<div class="stack-wrap"><div class="stack-bar">${{seg}}</div><div class="stack-tip" hidden></div></div>`;
      const wrap=el.querySelector('.stack-wrap'), tip=el.querySelector('.stack-tip');
      wrap.querySelectorAll('.stack-seg').forEach(s=>{{
        s.addEventListener('mousemove', e=>{{
          tip.textContent=s.dataset.label+' — '+s.dataset.detail;
          tip.hidden=false;
          const rect=wrap.getBoundingClientRect();
          tip.style.left=Math.max(0,Math.min(e.clientX-rect.left, rect.width))+'px';
        }});
        s.addEventListener('mouseleave', ()=>{{ tip.hidden=true; }});
      }});
    }}
    // Bar list capped to the top N with a Prev/Next pager for the tail. Shares
    // are computed against the full total so pagination doesn't skew percentages.
    function renderBarListPaged(containerId, pagerId, rows, valueKey, labelKey, state) {{
      const el=document.getElementById(containerId), pager=document.getElementById(pagerId);
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; if(pager) pager.innerHTML=''; return; }}
      const perPage=10, total=rows.reduce((s,r)=>s+num(r[valueKey]),0)||1;
      const totalPages=Math.max(1,Math.ceil(rows.length/perPage));
      if (state.page>totalPages) state.page=totalPages;
      const startIdx=(state.page-1)*perPage, pageRows=rows.slice(startIdx,startIdx+perPage);
      el.innerHTML=pageRows.map(r=>{{const p=num(r[valueKey])/total*100;return`<div class="bar-row"><div class="bar-label">${{esc(r[labelKey])}}</div>${{pctBar(p)}}<div class="bar-count">${{count(r[valueKey])}}<span class="bar-pct">${{p.toFixed(0)}}%</span></div></div>`;}}).join('');
      if (!pager) return;
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${{state.page<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{state.page}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-dir="next"${{state.page>=totalPages?' disabled':''}}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{{ state.page+=b.dataset.dir==='next'?1:-1; renderBarListPaged(containerId,pagerId,rows,valueKey,labelKey,state); }});
    }}
    // Top sources/medium: 10 per page, rest behind a pager. Rows arrive already
    // sorted by sessions desc, so page 1 is the true top 10.
    const SOURCES_PER_PAGE=10; let sourcesPageNum=1;
    function renderTrafficSources() {{
      const rows=trafficSources||[];
      const totalPages=Math.max(1,Math.ceil(rows.length/SOURCES_PER_PAGE));
      if (sourcesPageNum>totalPages) sourcesPageNum=totalPages;
      const startIdx=(sourcesPageNum-1)*SOURCES_PER_PAGE;
      const pageRows=rows.slice(startIdx,startIdx+SOURCES_PER_PAGE);
      renderTable('sourcesTable',[
        {{key:'source',label:'Source',left:true}},
        {{key:'medium',label:'Medium',left:true}},
        {{key:'sessions',label:'Sessions',format:count}},
        {{key:'engaged_sessions',label:'Engaged',format:count}},
        {{key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'}},
        {{key:'key_events',label:'Key events',format:count}},
      ], pageRows, 'No source data.');
      const pager=document.getElementById('sourcesPager');
      if (!pager) return;
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${{sourcesPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{sourcesPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-dir="next"${{sourcesPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{{ sourcesPageNum+=b.dataset.dir==='next'?1:-1; renderTrafficSources(); }});
    }}
    // Sessions-over-time hero chart (top of the analytics tab). Fetches the
    // current period and the equivalent prior period (compareStart/compareEnd,
    // always set by applyPreset) and overlays them for an at-a-glance trend.
    async function loadSessionsTrend() {{
      setStatus('sessionsTrendStatus','Loading…');
      skelChart('sessionsTrendChart','trend-md-svg');
      try {{
        const [cur, prev] = await Promise.all([
          getJson(withDatesRange(TRAFFIC_ACQ_API, currentStart, currentEnd)),
          compareStart ? getJson(withDatesRange(TRAFFIC_ACQ_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
        ]);
        sessionsTrendCache = {{ cur: cur.daily || [], prev: (prev && prev.daily) || [] }};
        renderSessionsTrend();
        setCmpWarn('sessionsCmpWarn', ['google_analytics']);
        setStatus('sessionsTrendStatus','');
      }} catch(err) {{ setStatus('sessionsTrendStatus',err.message||String(err),true); }}
    }}
    // Daily/Weekly chips for the sessions trend — re-render from cache, no refetch.
    document.querySelectorAll('#sessionsGranChips .chip').forEach(btn =>
      btn.addEventListener('click', () => {{
        if (btn.dataset.gran === sessionsGran) return;
        sessionsGran = btn.dataset.gran;
        document.querySelectorAll('#sessionsGranChips .chip').forEach(b => b.classList.toggle('active', b === btn));
        renderSessionsTrend();
      }})
    );
    async function loadTrafficAcq() {{
      setStatus('trafficAcqStatus','Loading…');
      document.getElementById('channelBars').innerHTML = skelBars(5);
      document.getElementById('sourcesTable').innerHTML = skelTable(6,6);
      try {{
        const [payload, ev] = await Promise.all([
          getJson(withDates(TRAFFIC_ACQ_API)),
          getJson(withDates(TRAFFIC_KEY_EVENTS_API)).catch(()=>({{by_source_events:[],events:[]}})),
        ]);
        renderChannelStacked(payload.by_channel||[]);
        sourcesPageNum=1;
        trafficBaseSources = payload.by_source||[];
        trafficSourceEventMap={{}};
        for (const r of (ev.by_source_events||[])) {{
          const k=srcKey(r.source,r.medium);
          (trafficSourceEventMap[k]=trafficSourceEventMap[k]||{{}})[r.event_name]=num(r.event_count);
        }}
        mergeEvents(ev.events);
        applyTrafficSources(); renderTrafficSources();
        setStatus('trafficAcqStatus','');
      }} catch(err) {{ setStatus('trafficAcqStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Device split ----
    async function loadDeviceSplit() {{
      const sec=document.getElementById('sec-audience');
      setStatus('deviceStatus','Loading…');
      document.getElementById('deviceBars').innerHTML = skelBars(3);
      try {{
        const payload=await getJson(withDates(DEVICE_SPLIT_API));
        const rows=payload.rows||[];
        // Audience only holds the device split — when GA4 returns nothing for the
        // range, drop the whole section rather than showing an empty panel.
        if (sec) sec.hidden = !rows.length;
        renderBarList('deviceBars',rows,'users','device');
        setStatus('deviceStatus','');
      }} catch(err) {{ setStatus('deviceStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Global key-event selector (Traffic + Landing pages + User acquisition) ----
    // One control at the top of Website Analytics chooses which GA4 events count as
    // "key events." Default = GA4's own key events; the selection persists per client
    // (admin "Save as default"). Each panel keeps a base row set + a per-row event map
    // so the key-events column recomputes instantly when the selection changes.
    const LANDING_PER_PAGE=10; let landingPageNum=1, landingRows=[], landingSearchQuery='';
    let landingBaseRows=[];            // from LANDING_PAGES_API
    let landingEventMap={{}};            // page_path -> {{ event_name: count }}
    let trafficSources=[], trafficBaseSources=[], trafficSourceEventMap={{}};   // srcKey -> {{ event_name: count }}
    let userAcqSources=[], userAcqBaseSources=[], userAcqSourceEventMap={{}};
    let keyEventCatalog=[];            // [{{event_name,event_count,is_key}}] unioned across panels
    let keyEventCounts={{}};             // event_name -> total count (union)
    let keyEventKeys=new Set();        // GA4-flagged key events (union)
    let selectedKeyEvents=new Set(GA4_KEY_EVENTS_SAVED);
    let keyEventUserTouched=false;     // once true, stop auto-tracking GA4's key set
    let keyEventSearchTerm='';
    const srcKey=(s,m)=>(s||'')+'\\u0000'+(m||'');

    // Fold one panel's event catalog into the shared union + refresh the dropdown.
    function mergeEvents(events) {{
      for (const e of (events||[])) {{
        const n=e.event_name; if (!n) continue;
        keyEventCounts[n]=(keyEventCounts[n]||0)+num(e.event_count);
        if (num(e.key_events)>0) keyEventKeys.add(n);
      }}
      keyEventCatalog=Object.keys(keyEventCounts)
        .map(n=>({{event_name:n, event_count:keyEventCounts[n], is_key:keyEventKeys.has(n)}}))
        .sort((a,b)=>b.event_count-a.event_count);
      // Until the client saved a set or the user edits it, mirror GA4's own key events.
      if (!keyEventUserTouched && !GA4_KEY_EVENTS_SAVED.length && keyEventKeys.size) {{
        selectedKeyEvents=new Set(keyEventKeys);
      }}
      renderKeyEventDropdown();
    }}
    function keSum(map, key) {{
      const evs=map[key]||{{}};
      let s=0; for (const ev of selectedKeyEvents) s+=(evs[ev]||0); return s;
    }}
    function applyLanding() {{
      if (!Object.keys(landingEventMap).length) {{ landingRows=landingBaseRows.slice(); return; }}
      landingRows=landingBaseRows.map(r=>{{
        const ke=keSum(landingEventMap, r.page_path);
        const rate=r.sessions?Math.round(ke/r.sessions*1000)/10:0;
        return {{...r, key_events:ke, key_event_rate:rate}};
      }});
    }}
    function applyTrafficSources() {{
      // Only trust the override map once its own fetch actually returned rows —
      // a missing/unprovisioned events view must fall back to the base report's
      // key_events, not silently zero everything out.
      const hasOverride=Object.keys(trafficSourceEventMap).length>0;
      trafficSources=trafficBaseSources.map(r=>{{
        const ke=hasOverride?keSum(trafficSourceEventMap, srcKey(r.source,r.medium)):num(r.key_events);
        return {{...r, key_events:ke}};
      }});
    }}
    function applyUserAcqSources() {{
      const hasOverride=Object.keys(userAcqSourceEventMap).length>0;
      userAcqSources=userAcqBaseSources.map(r=>{{
        const ke=hasOverride?keSum(userAcqSourceEventMap, srcKey(r.source,r.medium)):num(r.key_events);
        const rate=r.new_users?Math.round(ke/r.new_users*1000)/10:0;
        return {{...r, key_events:ke, key_event_rate:rate}};
      }});
    }}
    // Re-run every loaded panel against the current selection.
    function applyKeyEventsAll() {{
      if (pagesTopRows.length || pagesSourceRows.length) {{ pagesPageNum=1; renderPages(); }}
      if (landingBaseRows.length) {{ applyLanding(); landingPageNum=1; renderLanding(); }}
      if (trafficBaseSources.length) {{ applyTrafficSources(); renderTrafficSources(); }}
      if (userAcqBaseSources.length) {{ applyUserAcqSources(); renderUserAcqSources(); }}
    }}
    function keyEventToggleLabel() {{
      const n=selectedKeyEvents.size;
      if (!n) return 'All key events';
      if (n===1) return [...selectedKeyEvents][0];
      return n+' events selected';
    }}
    function renderKeyEventDropdown() {{
      const label=document.getElementById('keyEventToggleLabel');
      const list=document.getElementById('keyEventList');
      if (label) label.textContent=keyEventToggleLabel();
      if (!list) return;
      if (!keyEventCatalog.length) {{ list.innerHTML='<div class="ke-dd-empty">Per-event data appears after the next GA4 sync.</div>'; return; }}
      const term=keyEventSearchTerm.trim().toLowerCase();
      const matches=keyEventCatalog.filter(e=>!term||e.event_name.toLowerCase().includes(term));
      if (!matches.length) {{ list.innerHTML='<div class="ke-dd-empty">No events match your search.</div>'; return; }}
      list.innerHTML=matches.map(e=>`<label class="ke-dd-option${{selectedKeyEvents.has(e.event_name)?' active':''}}"><input type="checkbox"${{selectedKeyEvents.has(e.event_name)?' checked':''}} data-ev="${{esc(e.event_name)}}"><span class="ke-dd-name">${{esc(e.event_name)}}</span><span class="ke-dd-count">${{count(e.event_count)}}</span></label>`).join('');
      list.querySelectorAll('input[data-ev]').forEach(cb=>cb.addEventListener('change',()=>{{
        const ev=cb.dataset.ev;
        keyEventUserTouched=true;
        if (selectedKeyEvents.has(ev)) selectedKeyEvents.delete(ev); else selectedKeyEvents.add(ev);
        if (label) label.textContent=keyEventToggleLabel();
        const opt=cb.closest('.ke-dd-option'); if (opt) opt.classList.toggle('active', selectedKeyEvents.has(ev));
        applyKeyEventsAll();
      }}));
    }}
    (function initKeyEventDropdown(){{
      const dd=document.getElementById('keyEventDropdown');
      const toggle=document.getElementById('keyEventToggle');
      const panel=document.getElementById('keyEventPanel');
      const search=document.getElementById('keyEventSearch');
      if (!dd||!toggle||!panel) return;
      const open=()=>{{ panel.hidden=false; dd.classList.add('open'); toggle.setAttribute('aria-expanded','true'); if (search) {{ search.value=keyEventSearchTerm; search.focus(); }} }};
      const close=()=>{{ panel.hidden=true; dd.classList.remove('open'); toggle.setAttribute('aria-expanded','false'); }};
      toggle.addEventListener('click', e=>{{ e.stopPropagation(); if (panel.hidden) open(); else close(); }});
      if (search) search.addEventListener('input', ()=>{{ keyEventSearchTerm=search.value; renderKeyEventDropdown(); }});
      document.addEventListener('click', e=>{{ if (!dd.contains(e.target)) close(); }});
      document.addEventListener('keydown', e=>{{ if (e.key==='Escape' && !panel.hidden) {{ close(); toggle.focus(); }} }});
    }})();
    function renderLanding() {{
      let base=landingRows;
      if (landingSearchQuery) {{ const q=landingSearchQuery.toLowerCase(); base=base.filter(r=>String(r.page_path).toLowerCase().includes(q)); }}
      const el=document.getElementById('landingTable');
      if (!base.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No landing pages match${{landingSearchQuery?' "'+esc(landingSearchQuery)+'"':' this range'}}.</td></tr></tbody>`; setStatus('landingStatus', landingSearchQuery?'No results':''); document.getElementById('landingPager').innerHTML=''; return; }}
      const totalPages=Math.max(1,Math.ceil(base.length/LANDING_PER_PAGE));
      if (landingPageNum>totalPages) landingPageNum=totalPages;
      const startIdx=(landingPageNum-1)*LANDING_PER_PAGE, rows=base.slice(startIdx,startIdx+LANDING_PER_PAGE);
      el.innerHTML=`<thead><tr><th class="left">Landing page</th><th>Sessions</th><th>Users</th><th>New users</th><th>Key events</th><th>KE rate</th><th>Avg engt</th></tr></thead>`+
        `<tbody>${{rows.map(r=>`<tr><td class="left"><span class="page-path" title="${{esc(r.page_path)}}">${{esc(r.page_path)}}</span></td><td>${{count(r.sessions)}}</td><td>${{count(r.users)}}</td><td>${{count(r.new_users)}}</td><td>${{count(r.key_events)}}</td><td>${{r.key_event_rate!=null?r.key_event_rate+'%':'—'}}</td><td>${{fmtDuration(r.avg_engagement_seconds)}}</td></tr>`).join('')}}</tbody>`;
      setStatus('landingStatus',`${{startIdx+1}}–${{startIdx+rows.length}} of ${{base.length}}`+(landingSearchQuery?' (filtered)':''));
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
        const [pages, ev] = await Promise.all([
          getJson(withDates(LANDING_PAGES_API)),
          getJson(withDates(LANDING_EVENTS_API)).catch(()=>({{rows:[],events:[]}})),
        ]);
        landingBaseRows = pages.rows||[];
        // Build per-page event map, then fold this panel's events into the shared catalog.
        landingEventMap={{}};
        for (const r of (ev.rows||[])) {{
          (landingEventMap[r.page_path]=landingEventMap[r.page_path]||{{}})[r.event_name]=num(r.event_count);
        }}
        mergeEvents(ev.events);
        applyLanding(); landingPageNum=1; renderLanding();
      }} catch(err) {{ setStatus('landingStatus',err.message||String(err),true); }}
    }}
    (function(){{
      const inp=document.getElementById('landingSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{{ clearTimeout(debounce); debounce=setTimeout(()=>{{landingSearchQuery=inp.value.trim();landingPageNum=1;renderLanding();}},180); }});
    }})();
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
    const USERACQ_SRC_PER_PAGE=10; let userAcqSrcPage=1;
    const userAcqChanState={{page:1}};
    function renderUserAcqSources() {{
      const rows=userAcqSources||[];
      const totalPages=Math.max(1,Math.ceil(rows.length/USERACQ_SRC_PER_PAGE));
      if (userAcqSrcPage>totalPages) userAcqSrcPage=totalPages;
      const startIdx=(userAcqSrcPage-1)*USERACQ_SRC_PER_PAGE;
      renderTable('userAcqSourceTable',[
        {{key:'source',label:'Source',left:true}},
        {{key:'medium',label:'Medium',left:true}},
        {{key:'new_users',label:'New users',format:count}},
        {{key:'key_events',label:'Key events',format:count}},
        {{key:'key_event_rate',label:'KE rate',format:v=>v!=null?v+'%':'—'}},
      ], rows.slice(startIdx,startIdx+USERACQ_SRC_PER_PAGE), 'No source data.');
      const pager=document.getElementById('userAcqSourcePager');
      if (!pager) return;
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${{userAcqSrcPage<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{userAcqSrcPage}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-dir="next"${{userAcqSrcPage>=totalPages?' disabled':''}}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{{ userAcqSrcPage+=b.dataset.dir==='next'?1:-1; renderUserAcqSources(); }});
    }}
    async function loadUserAcquisition() {{
      setStatus('userAcqStatus','Loading…');
      document.getElementById('newVsReturning').innerHTML=`<div class="nr-wrap"><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="nr-bar-wrap"><div class="skel" style="height:10px;border-radius:5px"></div></div></div>`;
      document.getElementById('userAcqChannelBars').innerHTML = skelBars(5);
      document.getElementById('userAcqSourceTable').innerHTML = skelTable(5,5);
      try {{
        const [payload, ev] = await Promise.all([
          getJson(withDates(USER_ACQ_API)),
          getJson(withDates(USER_ACQ_KEY_EVENTS_API)).catch(()=>({{by_source_events:[],events:[]}})),
        ]);
        renderNewVsReturning(payload.by_channel||[]);
        userAcqChanState.page=1;
        renderBarListPaged('userAcqChannelBars','userAcqChannelPager',payload.by_channel||[],'new_users','channel',userAcqChanState);
        userAcqSrcPage=1;
        userAcqBaseSources = payload.by_source||[];
        userAcqSourceEventMap={{}};
        for (const r of (ev.by_source_events||[])) {{
          const k=srcKey(r.source,r.medium);
          (userAcqSourceEventMap[k]=userAcqSourceEventMap[k]||{{}})[r.event_name]=num(r.event_count);
        }}
        mergeEvents(ev.events);
        applyUserAcqSources(); renderUserAcqSources();
        setStatus('userAcqStatus','');
      }} catch(err) {{ setStatus('userAcqStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Demographics ----
    // Age bracket, with a toggle for the "unknown" bucket (hidden by default so
    // the real brackets read clearly). Rows are cached so toggling is instant.
    let demoAgeRows=[];
    function renderAge() {{
      const showUnknown=!!(document.getElementById('ageUnknownToggle')||{{}}).checked;
      const rows=demoAgeRows.filter(r=>showUnknown||String(r.age_bracket).toLowerCase()!=='unknown');
      renderBarList('ageBars',rows,'users','age_bracket');
    }}
    {{ const t=document.getElementById('ageUnknownToggle'); if (t) t.addEventListener('change', renderAge); }}
    // Gender → 100% split bar + per-gender stat cards.
    const GENDER_COLORS={{male:'#1d6fd0',female:'#d6336c'}};
    function genderColor(g,i) {{ return GENDER_COLORS[String(g).toLowerCase()]||CHART_PALETTE[i%CHART_PALETTE.length]; }}
    function renderGender(rows) {{
      const el=document.getElementById('genderBars');
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; return; }}
      const ordered=[...rows].sort((a,b)=>num(b.users)-num(a.users));
      const total=ordered.reduce((s,r)=>s+num(r.users),0)||1;
      const cap=g=>String(g).replace(/\b\w/g,c=>c.toUpperCase());
      const seg=ordered.map((r,i)=>{{const p=num(r.users)/total*100;return`<div class="gender-seg" style="width:${{p.toFixed(2)}}%;background:${{genderColor(r.gender,i)}}" title="${{esc(cap(r.gender))}} — ${{count(r.users)}} (${{p.toFixed(1)}}%)">${{p>=10?p.toFixed(0)+'%':''}}</div>`;}}).join('');
      const stats=ordered.map((r,i)=>{{const p=num(r.users)/total*100;return`<div class="gender-stat"><span class="gender-dot" style="background:${{genderColor(r.gender,i)}}"></span><div><div class="gender-stat-label">${{esc(cap(r.gender))}}</div><div class="gender-stat-value">${{count(r.users)}}</div><div class="gender-stat-pct">${{p.toFixed(0)}}% of known</div></div></div>`;}}).join('');
      el.innerHTML=`<div class="gender-wrap"><div class="gender-bar">${{seg}}</div><div class="gender-stats">${{stats}}</div></div>`;
    }}
    // Users-by-state tile-grid heat map. Each state is a labeled square on an
    // approximate US grid, shaded by its share of users; hover for exact counts.
    // Unmapped regions (non-US, territories) simply don't appear — they remain
    // visible in the cities table beside it.
    const STATE_TILES={{
      'Alabama':['AL',6,6],'Alaska':['AK',0,0],'Arizona':['AZ',5,1],'Arkansas':['AR',5,4],
      'California':['CA',4,0],'Colorado':['CO',4,2],'Connecticut':['CT',3,9],'Delaware':['DE',4,9],
      'Florida':['FL',7,8],'Georgia':['GA',6,7],'Hawaii':['HI',7,0],'Idaho':['ID',2,1],
      'Illinois':['IL',2,5],'Indiana':['IN',3,5],'Iowa':['IA',3,4],'Kansas':['KS',5,3],
      'Kentucky':['KY',4,5],'Louisiana':['LA',6,4],'Maine':['ME',0,10],'Maryland':['MD',4,8],
      'Massachusetts':['MA',2,9],'Michigan':['MI',2,7],'Minnesota':['MN',2,4],'Mississippi':['MS',6,5],
      'Missouri':['MO',4,4],'Montana':['MT',2,2],'Nebraska':['NE',4,3],'Nevada':['NV',3,1],
      'New Hampshire':['NH',1,10],'New Jersey':['NJ',3,8],'New Mexico':['NM',5,2],'New York':['NY',2,8],
      'North Carolina':['NC',5,6],'North Dakota':['ND',2,3],'Ohio':['OH',3,6],'Oklahoma':['OK',6,3],
      'Oregon':['OR',3,0],'Pennsylvania':['PA',3,7],'Rhode Island':['RI',2,10],'South Carolina':['SC',5,7],
      'South Dakota':['SD',3,3],'Tennessee':['TN',5,5],'Texas':['TX',7,3],'Utah':['UT',4,1],
      'Vermont':['VT',1,9],'Virginia':['VA',4,7],'Washington':['WA',2,0],'West Virginia':['WV',4,6],
      'Wisconsin':['WI',2,6],'Wyoming':['WY',3,2],'District of Columbia':['DC',5,8]
    }};
    function lerpColor(t) {{
      // #eaf1fb (light) → #1d6fd0 (accent), gamma-eased so mid values read.
      const a=[234,241,251], b=[29,111,208], e=Math.sqrt(Math.max(0,Math.min(1,t)));
      return 'rgb('+a.map((v,i)=>Math.round(v+(b[i]-v)*e)).join(',')+')';
    }}
    function renderStateMap(regionRows) {{
      const host=document.getElementById('stateMap');
      if (!host) return;
      const byState={{}};
      for (const r of (regionRows||[])) byState[r.region]=(byState[r.region]||0)+num(r.users);
      const max=Math.max(1,...Object.values(byState));
      const TS=26, GAP=3, CELL=TS+GAP, COLS=11, ROWS=8;
      const W=COLS*CELL-GAP, H=ROWS*CELL-GAP;
      let tiles='';
      for (const [name,[ab,r,c]] of Object.entries(STATE_TILES)) {{
        const v=byState[name]||0, x=c*CELL, y=r*CELL;
        const fill=v>0?lerpColor(v/max):'#eef2f7';
        const txt=v/max>0.55?'#fff':'#5a6b82';
        tiles+=`<g class="state-tile"><rect x="${{x}}" y="${{y}}" width="${{TS}}" height="${{TS}}" rx="4" fill="${{fill}}"><title>${{esc(name)}} — ${{count(v)}} users</title></rect>`+
          `<text class="state-tile-label" x="${{x+TS/2}}" y="${{y+TS/2+3}}" text-anchor="middle" style="fill:${{txt}}">${{ab}}</text></g>`;
      }}
      const hasData=Object.keys(byState).length>0;
      host.innerHTML=`<svg viewBox="0 0 ${{W}} ${{H}}" role="img" aria-label="Users by US state">${{tiles}}</svg>`+
        (hasData?`<div class="state-map-scale"><span>Fewer</span><span class="state-map-scale-bar"></span><span>More</span></div>`:`<div class="empty" style="padding:12px">No state-level data for this range.</div>`);
    }}
    async function loadDemographics() {{
      setStatus('demoStatus','Loading…');
      document.getElementById('stateMap').innerHTML = `<div class="skel" style="height:200px;border-radius:8px"></div>`;
      document.getElementById('citiesTable').innerHTML = skelTable(5,5);
      document.getElementById('ageBars').innerHTML = skelBars(5);
      document.getElementById('genderBars').innerHTML = skelBars(2);
      try {{
        const payload=await getJson(withDates(DEMOGRAPHICS_API));
        // Prefer the accurate state rollup; fall back to summing the top cities.
        let regionRows=payload.by_region;
        if (!regionRows||!regionRows.length) {{
          const agg={{}};
          for (const r of (payload.by_city||[])) if (r.region) agg[r.region]=(agg[r.region]||0)+num(r.users);
          regionRows=Object.entries(agg).map(([region,users])=>({{region,users}}));
        }}
        renderStateMap(regionRows);
        renderTable('citiesTable',[
          {{key:'city',label:'City',left:true}},
          {{key:'region',label:'Region',left:true}},
          {{key:'users',label:'Users',format:count}},
          {{key:'key_events',label:'Key events',format:count}},
          {{key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'}},
        ], payload.by_city||[], 'No city data.');
        demoAgeRows=payload.by_age||[];
        renderAge();
        renderGender(payload.by_gender||[]);
        setStatus('demoStatus','');
      }} catch(err) {{ setStatus('demoStatus',err.message||String(err),true); }}
    }}

    // ---- Loaders ----
    function loadAllAnalytics() {{
      // Staggered, not simultaneous: 7 modules x 1-3 sub-fetches each means
      // ~12-14 concurrent BigQuery queries if fired all at once, which was
      // intermittently tripping transient 500s under load. Spreading module
      // starts out keeps peak concurrency down without a noticeable delay.
      const modules=getModules();
      const loaders=[];
      if (modules.sessions)         loaders.push(loadSessionsTrend);
      if (modules.top_pages)        loaders.push(loadPages);
      if (modules.traffic)          loaders.push(loadTrafficAcq);
      if (modules.audience)         loaders.push(loadDeviceSplit);
      if (modules.landing)          loaders.push(loadLandingPages);
      if (modules.user_acquisition) loaders.push(loadUserAcquisition);
      if (modules.demographics)     loaders.push(loadDemographics);
      loaders.forEach((fn,i)=>setTimeout(fn, i*250));
    }}
    // ---- Overview home: a widget per section, each with a "See more" jump ----
    // The Website analytics + AI traffic panels overlay the equivalent prior
    // period (compareStart/compareEnd) and support a Daily/Weekly interval
    // toggle; both re-render from cache without refetching. Search Console shows
    // branded vs. target keyword weekly avg-position trends side by side.
    let ovSessionsGran='daily', ovAiGran='daily';
    let ovSessionsCache={{cur:[],prev:[]}}, ovAiCache={{cur:[],prev:[]}};
    // Collapse daily rows to one {{date,value}} per date (AI rows are per-platform,
    // so this also sums sessions across assistants for the total-traffic line).
    function ovSumByDate(rows, key) {{
      const m=new Map();
      for (const r of (rows||[])) {{ const d=String(r.date); m.set(d,(m.get(d)||0)+num(r[key])); }}
      return [...m.keys()].sort().map(d=>({{date:d, value:m.get(d)}}));
    }}
    // Re-bucket {{date,value}} rows into Monday-start weeks (client-side, no refetch).
    function ovAggregateWeekly(rows) {{
      if (!rows||!rows.length) return [];
      const out=[]; let cur=null;
      for (const r of rows) {{
        const dt=new Date(String(r.date)+'T00:00:00');
        const dow=(dt.getDay()+6)%7;                 // 0 = Monday
        const mon=new Date(dt); mon.setDate(dt.getDate()-dow);
        const key=`${{mon.getFullYear()}}-${{String(mon.getMonth()+1).padStart(2,'0')}}-${{String(mon.getDate()).padStart(2,'0')}}`;
        if (!cur||cur.date!==key) {{ cur={{date:key, value:0}}; out.push(cur); }}
        cur.value+=num(r.value);
      }}
      return out;
    }}
    // Current-vs-previous line, aligned by index (previous mapped onto the current
    // labels), mirroring the Website Analytics sessions hero chart.
    function ovDrawCompareTrend(chartId, legendId, curRows, prevRows, color, unit) {{
      const n=curRows.length;
      const lg=document.getElementById(legendId);
      if (!n) {{ __destroyChart(chartId); if(lg) lg.innerHTML=''; return; }}
      const vals=curRows.map(d=>num(d.value));
      const prevVals=(prevRows||[]).map(d=>num(d.value));
      const hasPrev=prevVals.length>0;
      const labels=curRows.map(d=>String(d.date).slice(5));
      const series=[];
      if (hasPrev) series.push({{label:'Previous', data:prevVals.slice(0,n), color:'#9aa7bd', dashed:true}});
      series.push({{label:'Current', data:vals, color, fill:true}});
      lineChart(chartId, labels, series, {{
        yFmt: v=>count(v),
        tooltip: {{ label: c=>`${{c.dataset.label}}: ${{count(c.raw)}} ${{unit}}` }},
      }});
      if (lg) {{
        const curTot=vals.reduce((a,b)=>a+b,0), prevTot=prevVals.reduce((a,b)=>a+b,0);
        const delta=(hasPrev&&prevTot)?((curTot-prevTot)/prevTot*100):null;
        const deltaTxt=delta==null?'':` <span class="cmp-delta ${{delta>=0?'up':'down'}}">${{delta>=0?'+':''}}${{delta.toFixed(0)}}%</span>`;
        lg.innerHTML=`<span class="cmp-item"><span class="cmp-swatch cur"></span>Current · ${{count(curTot)}} ${{unit}}${{deltaTxt}}</span>`
          + (hasPrev?`<span class="cmp-item"><span class="cmp-swatch prev"></span>Previous · ${{count(prevTot)}}</span>`:'');
      }}
    }}
    function ovRenderSessions() {{
      const c=ovSessionsCache, wk=ovSessionsGran==='weekly';
      ovDrawCompareTrend('ovSessionsTrend','ovSessionsLegend',
        wk?ovAggregateWeekly(c.cur):c.cur, wk?ovAggregateWeekly(c.prev):c.prev, '#1769aa', 'sessions');
    }}
    function ovRenderAi() {{
      const c=ovAiCache, wk=ovAiGran==='weekly';
      ovDrawCompareTrend('ovAiTrend','ovAiLegend',
        wk?ovAggregateWeekly(c.cur):c.cur, wk?ovAggregateWeekly(c.prev):c.prev, '#7c3aed', 'sessions');
    }}
    // Site Performance scorecard (the four Lighthouse scores) — reuses psScoreCard
    // + PAGESPEED_TARGETS from the Site Performance pane's JS. Emitted only when
    // the pagespeed connector is present (Python gates the #ovPsScores element).
    async function loadOverviewPagespeed() {{
      const host=document.getElementById('ovPsScores');
      if (!host) return;
      setStatus('ovPsStatus','Loading…');
      host.innerHTML=skelCards(4);
      const strat=(typeof PS_STRATEGIES!=='undefined'&&PS_STRATEGIES.length)?PS_STRATEGIES[0]:'desktop';
      const url=PAGESPEED_API+(PAGESPEED_API.includes('?')?'&':'?')+'strategy='+strat;
      try {{
        const p=await getJson(url);
        if (!p||!p.url) {{ host.innerHTML=''; setStatus('ovPsStatus','No PageSpeed data yet'); return; }}
        const scores=[['Performance','performance',p.performance],['Accessibility','accessibility',p.accessibility],
          ['Best Practices','best_practices',p.best_practices],['SEO','seo',p.seo]];
        host.innerHTML=scores.map(([l,k,v])=>psScoreCard(l,v,PAGESPEED_TARGETS[k])).join('');
        setStatus('ovPsStatus', p.metric_date?('measured '+p.metric_date):'');
      }} catch(err) {{ host.innerHTML=''; setStatus('ovPsStatus', err.message||String(err), true); }}
    }}
    async function loadOverviewHome() {{
      setStatus('ovSessionsStatus','Loading…'); setStatus('ovAiStatus','Loading…'); setStatus('ovGscStatus','Loading…');
      const hasPrev=!!compareStart;
      const [traffic, trafficPrev, aiCur, aiPrev, branded, target] = await Promise.all([
        getJson(withDatesRange(TRAFFIC_ACQ_API, currentStart, currentEnd)).catch(()=>({{daily:[]}})),
        hasPrev ? getJson(withDatesRange(TRAFFIC_ACQ_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
        getJson(withDatesRange(AI_TRAFFIC_DAILY_API, currentStart, currentEnd)).then(d=>d.rows||[]).catch(()=>[]),
        hasPrev ? getJson(withDatesRange(AI_TRAFFIC_DAILY_API, compareStart, compareEnd)).then(d=>d.rows||[]).catch(()=>[]) : Promise.resolve([]),
        fetchKeywordMatches(gscBrandedRoots),
        fetchKeywordMatches(gscTargetKeywords),
      ]);
      // Website analytics — sessions, current vs previous.
      ovSessionsCache={{ cur: ovSumByDate(traffic.daily||[],'sessions'), prev: ovSumByDate((trafficPrev&&trafficPrev.daily)||[],'sessions') }};
      ovRenderSessions();
      const sTot=ovSessionsCache.cur.reduce((s,d)=>s+num(d.value),0);
      setStatus('ovSessionsStatus', sTot?count(sTot)+' sessions':'No data');
      // AI traffic — total AI sessions, current vs previous.
      ovAiCache={{ cur: ovSumByDate(aiCur,'sessions'), prev: ovSumByDate(aiPrev,'sessions') }};
      ovRenderAi();
      const aTot=ovAiCache.cur.reduce((s,d)=>s+num(d.value),0);
      setStatus('ovAiStatus', aTot?count(aTot)+' sessions':'No AI traffic');
      // Search Console — branded & target keyword weekly avg-position trends.
      drawKeywordTrend('ovGscBrandedTrend', branded.weekly, 'avg_position', '#1d6fd0', true);
      drawKeywordTrend('ovGscTargetTrend', target.weekly, 'avg_position', '#7c3aed', true);
      const noteFor=(roots, weekly)=> !roots.length ? 'Set keywords on the Search Console tab.'
        : (!(weekly||[]).length ? 'No matching queries in this range.' : '');
      const bn=document.getElementById('ovGscBrandedNote'); if(bn) bn.textContent=noteFor(gscBrandedRoots, branded.weekly);
      const tn=document.getElementById('ovGscTargetNote'); if(tn) tn.textContent=noteFor(gscTargetKeywords, target.weekly);
      setStatus('ovGscStatus', (!gscBrandedRoots.length && !gscTargetKeywords.length) ? ''
        : `${{gscBrandedRoots.length}} branded · ${{gscTargetKeywords.length}} target`);
      // Site performance scorecard (only present when the connector is on).
      loadOverviewPagespeed();
    }}
    // Daily/Weekly interval toggles for the two overview trend panels.
    document.querySelectorAll('#ovSessionsGranChips .chip').forEach(btn=>
      btn.addEventListener('click',()=>{{
        if (btn.dataset.gran===ovSessionsGran) return;
        ovSessionsGran=btn.dataset.gran;
        document.querySelectorAll('#ovSessionsGranChips .chip').forEach(b=>b.classList.toggle('active', b===btn));
        ovRenderSessions();
      }})
    );
    document.querySelectorAll('#ovAiGranChips .chip').forEach(btn=>
      btn.addEventListener('click',()=>{{
        if (btn.dataset.gran===ovAiGran) return;
        ovAiGran=btn.dataset.gran;
        document.querySelectorAll('#ovAiGranChips .chip').forEach(b=>b.classList.toggle('active', b===btn));
        ovRenderAi();
      }})
    );
    document.querySelectorAll('.ov-more[data-goto]').forEach(btn=>
      btn.addEventListener('click', ()=>switchTab(btn.dataset.goto))
    );
    function loadCurrentTab() {{
      if (currentTab==='overview')   {{ loadHealth().then(()=>{{ if (HAS_PAID_ADS) loadSummary(); loadOverviewHome(); }}); }}
      else if (currentTab==='explorer') {{ explorerLoaded=false; loadExplorer(); explorerLoaded=true; }}
      else if (currentTab==='analytics') {{ analyticsLoaded=false; applyModules(); loadAllAnalytics(); analyticsLoaded=true; }}
      else if (currentTab==='ai_traffic') {{ aiTrafficLoaded=false; loadAiTraffic(); aiTrafficLoaded=true; }}
    }}

    // ---- Date presets ----
    let currentStart='{start.isoformat()}', currentEnd='{end.isoformat()}';
    // The equivalent prior period for the currently selected preset (e.g.
    // "This month" July 1-6 -> compareStart/compareEnd June 1-6), computed
    // alongside currentStart/currentEnd in applyPreset() below.
    let compareStart='', compareEnd='';
    const fmtDate=d=>`${{d.getFullYear()}}-${{String(d.getMonth()+1).padStart(2,'0')}}-${{String(d.getDate()).padStart(2,'0')}}`;
    function mondayOf(d) {{
      const day=d.getDay(); const diff=(day===0?-6:1)-day;
      const m=new Date(d); m.setDate(d.getDate()+diff); return m;
    }}
    function applyPreset(name) {{
      const today=new Date(); let s, e=today, cs, ce;
      const lastN=n=>{{
        e=new Date(today);e.setDate(today.getDate()-1);
        s=new Date(today);s.setDate(today.getDate()-n);
        ce=new Date(s); ce.setDate(s.getDate()-1);
        cs=new Date(ce); cs.setDate(ce.getDate()-(n-1));
      }};
      // GA4/GSC syncs typically lag a day or more, so "today" itself is
      // usually incomplete or entirely unsynced -- these presets end at
      // yesterday like the trailing (last_N) ones do, falling back to the
      // period start when yesterday would fall outside it (e.g. Monday for
      // "this_week", the 1st for "this_month").
      const yesterday=new Date(today); yesterday.setDate(today.getDate()-1);
      if (name==='this_week') {{
        s=mondayOf(today); e=(yesterday>=s)?yesterday:s;
        cs=new Date(s); cs.setDate(s.getDate()-7);
        ce=new Date(e); ce.setDate(e.getDate()-7);
      }} else if (name==='last_week') {{
        const lw=new Date(today); lw.setDate(today.getDate()-7);
        s=mondayOf(lw); e=new Date(s); e.setDate(s.getDate()+6);
        cs=new Date(s); cs.setDate(s.getDate()-7);
        ce=new Date(e); ce.setDate(e.getDate()-7);
      }} else if (name==='this_month') {{
        s=new Date(today.getFullYear(),today.getMonth(),1);
        e=(yesterday>=s)?yesterday:s;
        const dom=e.getDate();
        const daysInPrevMonth=new Date(today.getFullYear(),today.getMonth(),0).getDate();
        cs=new Date(today.getFullYear(),today.getMonth()-1,1);
        ce=new Date(today.getFullYear(),today.getMonth()-1,Math.min(dom,daysInPrevMonth));
      }} else if (name==='last_month') {{
        s=new Date(today.getFullYear(),today.getMonth()-1,1); e=new Date(today.getFullYear(),today.getMonth(),0);
        cs=new Date(today.getFullYear(),today.getMonth()-2,1); ce=new Date(today.getFullYear(),today.getMonth()-1,0);
      }} else if (name==='last_7') lastN(7);
      else if (name==='last_30') lastN(30);
      else if (name==='last_90') lastN(90);
      else return;
      currentStart=fmtDate(s); currentEnd=fmtDate(e);
      compareStart=fmtDate(cs); compareEnd=fmtDate(ce);
      const sel=document.getElementById('datePresets'); if (sel && sel.value!==name) sel.value=name;
      loadCurrentTab();
    }}
    document.getElementById('datePresets').addEventListener('change',ev=>{{
      applyPreset(ev.target.value);
    }});

    // ---- Platform chips ----
    if (HAS_PAID_ADS) {{
      buildChips('platformChips',['Google','LinkedIn','Meta'],platformFilter,()=>{{renderSummary();renderChart();renderExplorer();}});
    }}

    // ---- Explorer chips ----
    buildExplorerFilters();

    // ---- Init ----
    if (HAS_PAID_ADS) {{
      buildMetricChips();
    }}
    document.getElementById('explorerTable').addEventListener('click',ev=>{{
      const shuf=ev.target.closest('.gads-shuffle');
      if (shuf) {{
        ev.stopPropagation();
        const cell=shuf.closest('.ad-cell');
        if (cell) gadsUpdatePreview(cell, true);
        return;
      }}
      const thumb=ev.target.closest('.ad-thumb');
      if (thumb) {{
        openCreativePreview(thumb.dataset.previewImage||'', thumb.dataset.previewVideo||'');
        return;
      }}
      const moreBtn=ev.target.closest('.ad-copy-more');
      if (moreBtn) {{
        const extra=moreBtn.nextElementSibling;
        extra.hidden=!extra.hidden;
        moreBtn.textContent = extra.hidden ? moreBtn.dataset.moreLabel : 'Show less';
        return;
      }}
      const sortTh=ev.target.closest('th.expl-sort');
      if (sortTh) {{
        const key=sortTh.dataset.key;
        if (explorerSort.key===key) explorerSort.dir = explorerSort.dir==='asc'?'desc':'asc';
        else explorerSort = {{ key, dir: key==='name'?'asc':'desc' }};
        renderExplorer();
        return;
      }}
      const row=ev.target.closest('tr[data-expandable]');
      if (row) toggleExplorerRow(row);
    }});
    // ---- Keyword Performance: search, match filter and column sorting ----
    document.getElementById('keywordSearch').addEventListener('input',ev=>{{
      kwSearch=ev.target.value; kwPageNum=1; renderKeywordTable();
    }});
    document.getElementById('keywordMatchChips').addEventListener('click',ev=>{{
      const btn=ev.target.closest('.chip'); if (!btn) return;
      const m=btn.dataset.match;
      if (m==='All') kwMatchFilter.clear();
      else kwMatchFilter.has(m) ? kwMatchFilter.delete(m) : kwMatchFilter.add(m);
      kwPageNum=1; syncKeywordChips(); renderKeywordTable();
    }});
    document.getElementById('keywordTable').addEventListener('click',ev=>{{
      const th=ev.target.closest('th.expl-sort'); if (!th) return;
      const key=th.dataset.key;
      if (kwSort.key===key) kwSort.dir = kwSort.dir==='asc'?'desc':'asc';
      else kwSort = {{ key, dir: (key==='keyword_text'||key==='match_type')?'asc':'desc' }};
      kwPageNum=1; renderKeywordTable();
    }});
    // ---- Creative preview modal (click a thumbnail to see it full size) ----
    function openCreativePreview(imageUrl, videoUrl) {{
      const body=document.getElementById('creativePreviewBody');
      const modal=document.getElementById('creativePreview');
      if (!body || !modal) return;
      if (videoUrl) {{
        body.innerHTML = `<video src="${{esc(videoUrl)}}" controls autoplay playsinline poster="${{esc(imageUrl)}}"></video>`;
      }} else if (imageUrl) {{
        body.innerHTML = `<img src="${{esc(imageUrl)}}" alt="" referrerpolicy="no-referrer">`;
      }} else {{
        return;
      }}
      modal.hidden = false;
    }}
    function closeCreativePreview() {{
      const modal=document.getElementById('creativePreview');
      const body=document.getElementById('creativePreviewBody');
      if (!modal) return;
      modal.hidden = true;
      if (body) body.innerHTML = ''; // stop any playing video
    }}
    document.querySelectorAll('[data-close-preview]').forEach(el=>el.addEventListener('click', closeCreativePreview));
    document.addEventListener('keydown', ev=>{{ if (ev.key==='Escape') closeCreativePreview(); }});
    applyPreset('last_30');

    // Deep-link + page-visibility prefs: land on the tab named in ?view= (set
    // by the sidebar links on Settings/Files/Connectors), unless that page was
    // turned off in Settings > "Sidebar pages" -- then fall back to the first
    // enabled page. Runs last, after all loaders are initialized.
    (function(){{
      let prefs = {{}};
      try {{ prefs = JSON.parse(localStorage.getItem('nixon_sidebar_pages:{client_slug}') || '{{}}'); }} catch(e) {{}}
      const v = new URLSearchParams(location.search).get('view');
      let target = (v && TABS.includes(v)) ? v : 'overview';
      if (prefs[target] === false) target = TABS.find(t => prefs[t] !== false) || 'overview';
      if (target !== currentTab) switchTab(target);
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
    }})();
  </script>
  {budget_scripts}
</body>
</html>"""
