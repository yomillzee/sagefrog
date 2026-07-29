"""Admin "Client Hours" page: a Harvest burn-up chart per client.

Rendered inside the shared admin shell (navy sidebar + client switcher) so it
sits alongside the other admin overviews. It fetches /admin/client-hours/data
(live from the Harvest API) and draws, for the current calendar month, each
client's cumulative hours-logged line against a straight goal-pace line — the
same burn-up read the agency already uses for individual clients, now for every
client at once. Each card's monthly goal is editable inline and saved to
/admin/client-hours/goal.

The same view is also served read-only, without a login, via a share link
(``render_shared_client_hours_page`` → /share/client-hours/{token}). The shared
page reuses this file's CSS and chart JS but drops every mutation affordance
(goal editing, project tagging, the manual Harvest refresh, and the share
manager) and points its data fetch at the public, cache-only feed.
"""

from __future__ import annotations

import html
import json

from dashboard.renderers.base_layout import favicon_head_html, render_admin_shell_page


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


_HOURS_CSS = """
    :root {
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2f6df0; --accent-d:#1d4ed8; --grid:#eef2f7; --goal:#9aa7b8;
    }
    body { color:var(--ink);
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; }
    main { max-width:1180px; width:100%; min-width:0; margin:0 auto; padding:26px 24px 56px; }
    .page-head { margin-bottom:14px; display:flex; align-items:flex-start;
      justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .page-head h2 { margin:0; font-size:1.15rem; color:var(--navy); }
    .sub { color:var(--muted); font-size:.86rem; margin:3px 0 0; }
    .head-controls { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .seg { display:inline-flex; background:#eef2f7; border:1px solid var(--border);
      border-radius:999px; padding:2px; gap:2px; }
    .seg-btn { appearance:none; border:0; background:transparent; color:var(--muted);
      border-radius:999px; padding:6px 13px; font:inherit; font-size:.8rem; font-weight:700;
      cursor:pointer; white-space:nowrap; }
    .seg-btn:hover { color:var(--navy); }
    .seg-btn.is-active { background:#fff; color:var(--navy); box-shadow:0 1px 3px rgba(10,37,64,.12); }
    /* Pace filters: toggle chips that narrow the grid to at-risk / growth clients.
       Colors mirror the per-card status (red=behind, amber=over pace). */
    .pace-filters { display:inline-flex; gap:6px; }
    .pace-chip { appearance:none; display:inline-flex; align-items:center; gap:6px;
      border:1px solid var(--border); background:#fff; color:var(--muted); border-radius:999px;
      padding:7px 12px; font:inherit; font-size:.8rem; font-weight:700; cursor:pointer; white-space:nowrap; }
    .pace-chip .dot { width:8px; height:8px; border-radius:50%; flex:0 0 auto; }
    .pace-chip b { font-variant-numeric:tabular-nums; }
    .pace-chip:hover:not(:disabled) { border-color:#94a3b8; }
    .pace-chip:disabled { opacity:.5; cursor:default; }
    .pace-chip.risk .dot { background:#b42318; }
    .pace-chip.grow .dot { background:#b7791f; }
    .pace-chip.risk.on { border-color:#b42318; color:#b42318; background:#fef2f2; }
    .pace-chip.grow.on { border-color:#b7791f; color:#b7791f; background:#fffbeb; }
    .no-goal-note { align-self:center; color:var(--muted); font-size:.75rem; font-weight:600;
      font-variant-numeric:tabular-nums; cursor:default; }
    .ch-search { position:relative; display:inline-flex; align-items:center; flex:0 0 auto; }
    .ch-search svg { position:absolute; left:12px; width:14px; height:14px; color:var(--muted);
      pointer-events:none; }
    .ch-search input { appearance:none; border:1px solid var(--border); background:#fff;
      border-radius:999px; color:var(--navy); font:inherit; font-size:.82rem; font-weight:600;
      padding:8px 30px 8px 32px; width:190px; transition:border-color .12s, box-shadow .12s; }
    .ch-search input::placeholder { color:var(--muted); font-weight:600; }
    .ch-search input:hover { border-color:#94a3b8; }
    .ch-search input:focus { outline:0; border-color:var(--accent);
      box-shadow:0 0 0 3px rgba(47,109,240,.12); }
    .ch-search-clear { position:absolute; right:8px; appearance:none; border:0; background:#eef2f7;
      color:var(--muted); border-radius:50%; width:18px; height:18px; font-size:.7rem; line-height:1;
      cursor:pointer; display:inline-flex; align-items:center; justify-content:center; }
    .ch-search-clear:hover { background:#e2e8f0; color:var(--navy); }
    .head-select { appearance:none; border:1px solid var(--border); background:#fff
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%230a2540' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")
      no-repeat right 12px center; border-radius:999px; color:var(--navy); font:inherit; font-size:.82rem;
      font-weight:700; padding:8px 34px 8px 15px; cursor:pointer; }
    .head-select:hover { border-color:#94a3b8; }
    .refresh-btn { appearance:none; display:inline-flex; align-items:center; gap:7px;
      border:1px solid var(--border); background:#fff; color:var(--navy); border-radius:999px;
      padding:8px 14px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; flex:0 0 auto; }
    .refresh-btn:hover { border-color:#94a3b8; }
    .refresh-btn:disabled { opacity:.6; cursor:default; }
    .refresh-btn svg { width:14px; height:14px; }
    .refresh-btn.spin svg { animation:rot .8s linear infinite; }
    @keyframes rot { to { transform:rotate(360deg); } }
    .notice { border-radius:12px; padding:14px 16px; margin-bottom:16px; font-size:.9rem; }
    .notice.err { background:#fef2f2; border:1px solid #fecaca; color:#b42318; }
    .notice.warn { background:#fffbeb; border:1px solid #fde68a; color:#92400e; }
    .notice a { color:inherit; font-weight:700; }
    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:16px; }
    .card { position:relative; background:#fff; border:1px solid var(--line); border-radius:16px;
      padding:16px 16px 12px; box-shadow:0 6px 22px rgba(10,37,64,.06); }
    .card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; margin-bottom:2px; }
    /* Low-profile "more details" toggle (kebab) + the popout it reveals. The
       popout tucks away per-card metadata (owner, goal, hard-ceiling) so the card
       face stays quiet: title, hours, and the status-colored burn-up. */
    .card-more { appearance:none; border:1px solid var(--border); background:#f8fafc; color:var(--muted);
      border-radius:8px; width:30px; height:26px; cursor:pointer; flex:0 0 auto; display:inline-flex;
      align-items:center; justify-content:center; }
    .card-more:hover { border-color:#94a3b8; color:var(--navy); background:#fff; }
    .card-more[aria-expanded="true"] { border-color:var(--accent); color:var(--accent); background:#fff; }
    .card-more svg { width:16px; height:16px; }
    .card-details { position:absolute; top:46px; right:14px; z-index:30; width:244px; background:#fff;
      border:1px solid var(--line); border-radius:12px; padding:12px 13px;
      box-shadow:0 14px 34px rgba(10,37,64,.20); display:flex; flex-direction:column; gap:11px; }
    .card-details[hidden] { display:none; }
    .cd-row { display:flex; align-items:center; justify-content:space-between; gap:10px; min-width:0; }
    .cd-row.cd-goal { flex-direction:column; align-items:stretch; gap:6px; }
    .cd-label { font-size:.68rem; font-weight:750; text-transform:uppercase; letter-spacing:.04em;
      color:var(--muted); flex:0 0 auto; }
    .cd-muted { color:var(--muted); font-size:.8rem; }
    .cd-val { font-size:.8rem; font-weight:700; color:var(--navy); font-variant-numeric:tabular-nums; }
    .card-details .goal { justify-content:flex-start; }
    .card-details .goal-edit input { width:92px; }
    .card-details .owner-select { max-width:150px; }
    /* Hard-ceiling toggle: stop-at cap. Red accent mirrors the ceiling line on
       the chart. Dimmed + disabled until a goal ceiling exists to cap against. */
    .cd-cap { padding-top:10px; border-top:1px solid var(--line); }
    .cap-toggle { display:inline-flex; align-items:center; gap:8px; font-size:.8rem; font-weight:700;
      color:var(--navy); cursor:pointer; }
    .cap-toggle.is-dim { color:var(--muted); cursor:default; }
    .cap-toggle input { width:15px; height:15px; accent-color:#b42318; cursor:pointer; flex:0 0 auto; }
    .cap-toggle input:disabled { cursor:default; }
    .cap-note { font-size:.72rem; font-weight:700; color:#b42318; font-variant-numeric:tabular-nums; }
    /* Project tags inside the popout: one row per project (name + hours over a
       full-width Untagged/Retainer/Project segmented control). Reuses the .tag-seg
       colors from the old modal; the list scrolls if a client has many projects. */
    .cd-projects { display:flex; flex-direction:column; gap:9px; padding-top:10px;
      border-top:1px solid var(--line); max-height:230px; overflow-y:auto; }
    .cd-proj { display:flex; flex-direction:column; gap:5px; }
    .cd-proj-name { display:flex; align-items:baseline; justify-content:space-between; gap:8px;
      font-size:.78rem; color:var(--ink); min-width:0; }
    .cd-proj-name .nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .cd-proj-hrs { color:var(--muted); font-size:.7rem; font-weight:700;
      font-variant-numeric:tabular-nums; flex:0 0 auto; }
    .cd-projects .tag-seg { display:flex; width:100%; }
    .cd-projects .tag-seg button { flex:1 1 0; padding:4px 6px; font-size:.7rem; text-align:center; }
    .card-title { font-weight:750; color:var(--navy); font-size:.98rem; line-height:1.25;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .card-total { font-variant-numeric:tabular-nums; color:var(--muted); font-size:.78rem; margin-top:2px; }
    .card-total b { color:var(--navy); font-weight:800; }
    .period { color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; }
    .chart-wrap { position:relative; margin-top:8px; }
    .chart-wrap svg { display:block; width:100%; height:auto; }
    /* Goal editor */
    .goal { display:flex; align-items:center; gap:6px; flex:0 0 auto; }
    .goal-btn { appearance:none; border:1px solid var(--border); background:#f8fafc; color:var(--navy);
      border-radius:999px; padding:4px 11px; font:inherit; font-size:.76rem; font-weight:700; cursor:pointer;
      white-space:nowrap; }
    .goal-btn:hover { border-color:#94a3b8; background:#fff; }
    .goal-btn .g-set { color:var(--accent); }
    /* Account-owner control, now housed in the per-card details popout. Admin: a
       pill-shaped <select> (owner color when set, muted "+ Owner" affordance when
       not). Read-only: a static colored pill. --oc carries the owner's
       deterministic color. */
    /* Card footer: just the burn-up legend now (owner moved into the popout). */
    .card-foot { display:flex; align-items:center; justify-content:space-between;
      gap:10px; margin-top:6px; }
    .card-foot .legend { margin-top:0; min-width:0; }
    .owner-select { appearance:none; border:1px solid var(--border); background:#fff;
      background:linear-gradient(#fff,#fff); color:var(--muted); border-radius:999px;
      padding:4px 26px 4px 12px; font:inherit; font-size:.74rem; font-weight:700; cursor:pointer;
      max-width:170px; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%235a6578' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
      background-repeat:no-repeat; background-position:right 10px center; }
    .owner-select:hover { border-color:#94a3b8; }
    .owner-select.on { border-color:var(--oc); color:var(--oc);
      background-color:color-mix(in srgb, var(--oc) 9%, #fff); }
    .owner-chip { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--oc);
      color:var(--oc); background-color:color-mix(in srgb, var(--oc) 9%, #fff);
      border-radius:999px; padding:4px 11px; font-size:.74rem; font-weight:700; white-space:nowrap; }
    .owner-chip::before { content:""; width:7px; height:7px; border-radius:50%; background:var(--oc); flex:0 0 auto; }
    /* Read-only goal label (shared view has no editor) */
    .goal-ro { color:var(--muted); font-size:.76rem; font-weight:700; white-space:nowrap; }
    .goal-ro b { color:var(--accent); }
    .goal-edit { display:none; align-items:center; gap:5px; }
    .goal-edit.on { display:flex; }
    .goal-edit input { width:118px; border:1px solid var(--border); border-radius:8px; padding:4px 8px;
      font:inherit; font-size:.8rem; text-align:left; }
    .goal-edit button { appearance:none; border:0; border-radius:8px; padding:5px 9px; font:inherit;
      font-size:.76rem; font-weight:700; cursor:pointer; }
    .goal-save { background:var(--accent); color:#fff; }
    .goal-cancel { background:#eef2f7; color:var(--muted); }
    .legend { display:flex; gap:14px; align-items:center; margin-top:6px; font-size:.72rem; color:var(--muted); }
    .legend span { display:inline-flex; align-items:center; gap:5px; }
    .legend i { width:14px; height:0; border-top-width:2px; border-top-style:solid; display:inline-block; }
    .legend i.actual { border-top-color:var(--accent); }
    .legend i.goal { border-top-color:var(--goal); border-top-style:dashed; }
    .empty { text-align:center; color:var(--muted); padding:40px 8px; background:#fff;
      border:1px solid var(--line); border-radius:16px; }
    .skel-card { height:230px; background:#fff; border:1px solid var(--line); border-radius:16px;
      position:relative; overflow:hidden; }
    .skel-card::after { content:""; position:absolute; inset:0;
      background:linear-gradient(90deg,transparent,rgba(148,163,184,.12),transparent);
      animation:sh 1.2s ease-in-out infinite; }
    @keyframes sh { 0%{transform:translateX(-100%);} 100%{transform:translateX(100%);} }
    /* Modals + shared bits. The share-link manager (below) reuses the
       .tag-modal-* header styles and .tag-empty; the .tag-seg segmented control is
       reused by the per-card project-tag rows in the details popout. */
    .modal-backdrop { position:fixed; inset:0; background:rgba(10,37,64,.42); z-index:120; }
    /* display:flex/fixed on .share-modal are author styles that would otherwise
       beat the browser's [hidden]{display:none}, leaving the panel stuck open. */
    .share-modal[hidden], .modal-backdrop[hidden] { display:none; }
    .tag-modal-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px;
      padding:18px 20px 12px; border-bottom:1px solid var(--line); }
    .tag-modal-head h2 { margin:0; font-size:1.05rem; color:var(--navy); }
    .tag-modal-sub { margin:4px 0 0; font-size:.78rem; color:var(--muted); line-height:1.4; }
    .tag-modal-close { appearance:none; border:0; background:#eef2f7; color:var(--muted); border-radius:8px;
      width:30px; height:30px; font-size:.95rem; cursor:pointer; flex:0 0 auto; }
    .tag-modal-close:hover { background:#e2e8f0; color:var(--navy); }
    .tag-seg { display:inline-flex; background:#eef2f7; border:1px solid var(--border); border-radius:999px;
      padding:2px; gap:2px; flex:0 0 auto; }
    .tag-seg button { appearance:none; border:0; background:transparent; color:var(--muted); border-radius:999px;
      padding:4px 10px; font:inherit; font-size:.74rem; font-weight:700; cursor:pointer; }
    .tag-seg button.on[data-tag=""] { background:#fff; color:var(--navy); box-shadow:0 1px 2px rgba(10,37,64,.12); }
    .tag-seg button.on[data-tag="retainer"] { background:#0a7f3f; color:#fff; }
    .tag-seg button.on[data-tag="project"] { background:#2f6df0; color:#fff; }
    .tag-empty { text-align:center; color:var(--muted); padding:30px 8px; }
    /* Share-links modal (centered dialog) */
    .share-modal { position:fixed; top:50%; left:50%; transform:translate(-50%,-50%);
      width:min(560px, 94vw); max-height:86vh; background:#fff; z-index:121; display:flex;
      flex-direction:column; border-radius:16px; box-shadow:0 24px 60px rgba(10,37,64,.32);
      animation:popIn .16s ease; }
    @keyframes popIn { from { transform:translate(-50%,-48%); opacity:.7; } to { transform:translate(-50%,-50%); opacity:1; } }
    .share-modal-body { overflow-y:auto; padding:6px 20px; flex:1; }
    .share-modal-foot { display:flex; gap:8px; padding:14px 20px 18px; border-top:1px solid var(--line); }
    .share-modal-foot input { flex:1; min-width:0; border:1px solid var(--border); border-radius:9px;
      padding:9px 12px; font:inherit; font-size:.85rem; }
    .share-create { background:var(--accent); color:#fff; border:0; border-radius:9px; padding:9px 16px;
      font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; flex:0 0 auto; }
    .share-create:disabled { opacity:.6; cursor:default; }
    .share-row { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 0;
      border-bottom:1px solid #f0f3f8; }
    .share-row:last-child { border-bottom:0; }
    .share-row-main { min-width:0; }
    .share-url { font-size:.8rem; color:var(--navy); font-weight:600; white-space:nowrap; overflow:hidden;
      text-overflow:ellipsis; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
    .share-meta { font-size:.72rem; color:var(--muted); margin-top:3px; }
    .share-actions { display:flex; gap:6px; flex:0 0 auto; }
    /* Read-only shared-page top bar */
    .ro-topbar { background:var(--navy); color:#fff; padding:14px 24px; display:flex; align-items:center;
      justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .ro-brand { display:flex; align-items:center; gap:12px; font-weight:800; font-size:1rem; min-width:0; }
    .ro-brand .ro-title { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ro-badge { font-size:.68rem; font-weight:800; letter-spacing:.04em; text-transform:uppercase;
      background:rgba(255,255,255,.16); color:#fff; border-radius:999px; padding:4px 10px; flex:0 0 auto; }
    .ro-note { color:rgba(255,255,255,.72); font-size:.78rem; font-weight:600; }
    /* ── Agency billing summary: one full-width billables burn-up ──────── */
    .summary { background:#fff; border:1px solid var(--line); border-radius:16px;
      box-shadow:0 6px 22px rgba(10,37,64,.06); padding:16px 18px 14px; margin-bottom:16px; }
    .summary-head { display:flex; align-items:flex-start; justify-content:space-between;
      gap:12px 18px; flex-wrap:wrap; }
    .summary-head h3 { margin:0; font-size:.98rem; color:var(--navy); font-weight:800; }
    .summary-head .s-note { color:var(--muted); font-size:.75rem; margin-top:3px;
      font-variant-numeric:tabular-nums; }
    .summary-head .s-note b { color:var(--ink); font-weight:700; }
    /* Hero: estimated billable dollars for the month (the projected read). */
    .s-hero { text-align:right; min-width:0; }
    .s-hero-label { color:var(--muted); font-size:.7rem; text-transform:uppercase;
      letter-spacing:.05em; font-weight:750; display:inline-flex; align-items:center; gap:7px; }
    .s-pill { font-size:.66rem; font-weight:800; letter-spacing:.02em; text-transform:none;
      border-radius:999px; padding:2px 8px; }
    .s-hero-value { color:var(--navy); font-size:1.85rem; font-weight:800; line-height:1.04;
      margin-top:3px; font-variant-numeric:tabular-nums; }
    .s-hero-sub { color:var(--muted); font-size:.77rem; margin-top:3px; font-variant-numeric:tabular-nums; }
    .s-hero-sub b { color:var(--ink); font-weight:700; }
    .summary-chart { margin-top:14px; }
    .summary-chart svg { display:block; width:100%; height:auto; }
    .summary-legend { display:flex; flex-wrap:wrap; gap:8px 18px; align-items:center; margin-top:8px;
      font-size:.72rem; color:var(--muted); }
    .summary-legend span { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }
    .summary-legend i { width:16px; height:0; border-top-width:2px; display:inline-block; flex:0 0 auto; }
    @media (max-width: 720px) {
      main { padding:16px 13px 44px; }
      .grid { grid-template-columns:1fr; }
      .head-controls { width:100%; }
      .ch-search { flex:1 1 100%; }
      .ch-search input { width:100%; }
      .summary-head { flex-direction:column; }
      .s-hero { text-align:left; }
      .s-hero-label { justify-content:flex-start; }
    }"""


# The head-controls that only make sense when signed in as an admin: manual
# Harvest refresh, project tagging, and the share-link manager. Omitted from the
# read-only shared view, which is a pure viewer.
_ADMIN_HEAD_BUTTONS = """
        <button type="button" class="refresh-btn" id="chShare" title="Create a read-only link anyone can view">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
            <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
          <span>Share</span>
        </button>
        <button type="button" class="refresh-btn" id="chRefresh" title="Pull fresh hours from Harvest">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          <span>Refresh</span>
        </button>"""


# The admin-only share-link manager modal. Project tagging now lives in each
# card's details popout, so there is no longer a separate tag modal. Left out of
# the read-only shared page entirely.
_ADMIN_MODALS = """
  <div class="modal-backdrop" id="shareBackdrop" hidden></div>
  <div class="share-modal" id="shareModal" role="dialog" aria-modal="true" aria-label="Share Client Hours" hidden>
    <header class="tag-modal-head">
      <div>
        <h2>Share Client Hours</h2>
        <p class="tag-modal-sub">Anyone with an active link can view this page read-only, without signing in.
          Links are unguessable — revoke one to disable it.</p>
      </div>
      <button type="button" class="tag-modal-close" id="shareClose" aria-label="Close">✕</button>
    </header>
    <div class="share-modal-body" id="shareBody"></div>
    <div class="share-modal-foot">
      <input type="text" id="shareLabel" placeholder="Label (optional, e.g. “Q3 leadership”)" maxlength="120"
        autocomplete="off" spellcheck="false">
      <button type="button" class="share-create" id="shareCreate">Create link</button>
    </div>
  </div>"""


def _owner_filter_options() -> str:
    """<option>s for the top-of-page owner filter: All owners · each roster name
    · Unassigned. The roster is shared with the backend (validation) and the
    per-card chip (via CH_CONFIG)."""
    import harvest_service

    opts = ['<option value="">All owners</option>']
    for name in harvest_service.CLIENT_OWNERS:
        opts.append(f'<option value="{_esc(name)}">{_esc(name)}</option>')
    opts.append('<option value="__none__">Unassigned</option>')
    return "".join(opts)


def _content_html(*, read_only: bool) -> str:
    """The page body (`<main>` + any admin modals). ``read_only`` drops every
    mutation control for the public shared view."""
    head_buttons = "" if read_only else _ADMIN_HEAD_BUTTONS
    modals = "" if read_only else _ADMIN_MODALS
    owner_options = _owner_filter_options()
    return f"""
  <main>
    <div class="page-head">
      <div>
        <h2>Hours</h2>
        <p class="sub" id="chSub">Loading Harvest hours…</p>
      </div>
      <div class="head-controls">
        <div class="ch-search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input type="text" id="chSearch" placeholder="Search clients…" autocomplete="off"
            spellcheck="false" aria-label="Search clients by name">
          <button type="button" class="ch-search-clear" id="chSearchClear" aria-label="Clear search" hidden>✕</button>
        </div>
        <select id="chScope" class="head-select" aria-label="Work type">
          <option value="retainer" selected>Retainer</option>
          <option value="project">Project</option>
          <option value="all">All work</option>
        </select>
        <select id="chOwner" class="head-select" aria-label="Filter by owner">
          {owner_options}
        </select>
        <div class="seg" id="chMetric" role="group" aria-label="Hours type">
          <button type="button" class="seg-btn is-active" data-metric="billable">Billable</button>
          <button type="button" class="seg-btn" data-metric="nonbillable">Non-billable</button>
        </div>
        <div class="seg" id="chSort" role="group" aria-label="Sort order">
          <button type="button" class="seg-btn is-active" data-sort="hours">Highest</button>
          <button type="button" class="seg-btn" data-sort="alpha">A–Z</button>
        </div>
        <div class="pace-filters" id="chStatus" role="group" aria-label="Pace filter">
          <button type="button" class="pace-chip risk" data-status="behind" id="chRisk"
            aria-pressed="false" title="Clients projected to finish under their contracted minimum">
            <span class="dot"></span>At risk <b id="chRiskCount">0</b></button>
          <button type="button" class="pace-chip grow" data-status="over" id="chGrow"
            aria-pressed="false" title="Clients projected to finish over their ceiling — growth opportunities">
            <span class="dot"></span>Growth <b id="chGrowCount">0</b></button>
        </div>
        <span class="no-goal-note" id="chNoGoal" hidden></span>{head_buttons}
      </div>
    </div>
    <div id="chNotice"></div>
    <div id="chSummary"></div>
    <div class="grid" id="chGrid"></div>
  </main>{modals}"""


# The page's client-side logic. Kept identical between the admin and shared
# views; behaviour that only applies to one is gated on ``CH_CONFIG.readOnly``.
# ``CH_CONFIG`` (data URL + read-only flag) is injected ahead of this block by
# ``_page_script``.
_PAGE_JS = r"""
    const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const hrs = v => new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(Number(v||0));
    // Standard blended billing rate — the summary card models revenue at this
    // rate ("@ $165/hr standard"), so every dollar figure there is a modeled
    // figure, not billed reality.
    const STANDARD_RATE = 165;
    const usd = v => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',
      maximumFractionDigits:0}).format(Number(v||0));
    // Compact "$1.2k / $89k" money for tight sub-lines; full for the hero values.
    const usdK = v => { const n = Number(v||0);
      return Math.abs(n) >= 1000
        ? '$' + new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(n/1000) + 'k'
        : usd(n); };
    let chData = null;
    // View state (client-side only — each client carries its projects with both
    // total + billable series, so scope/metric/sort toggles never re-hit
    // Harvest). scope: all|retainer|project · metric: billable|nonbillable ·
    // sort: hours|alpha. Defaults: retainer work, billable hours.
    // status: null | 'behind' | 'over' — the pace filter chips narrow the grid to
    // just the at-risk (behind their minimum) or growth (over their ceiling) book.
    // owner: '' = all · '__none__' = unassigned only · else a specific owner name.
    const view = { scope: 'retainer', metric: 'billable', sort: 'hours', search: '', status: null, owner: '' };

    // Deterministic owner → color, so each owner's chip reads the same everywhere.
    const OWNER_COLORS = ['#2f6df0','#0a7f3f','#b7791f','#b42318','#7c3aed','#0e7490',
      '#be185d','#4d7c0f','#c2410c','#0369a1','#9333ea','#15803d'];
    function ownerColor(name) {
      const s = String(name || ''); let h = 0;
      for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
      return OWNER_COLORS[h % OWNER_COLORS.length];
    }
    // The account-owner control on each card: an editable pill-select for admins,
    // a static colored chip in the read-only view (hidden when unassigned).
    function ownerChip(c) {
      const owner = c.owner || '';
      if (CH_CONFIG.readOnly) {
        return owner
          ? `<span class="owner-chip" style="--oc:${ownerColor(owner)}">${esc(owner)}</span>`
          : '';
      }
      const opts = ['<option value="">+ Owner</option>'].concat(
        (CH_CONFIG.owners || []).map(o =>
          `<option value="${esc(o)}"${o === owner ? ' selected' : ''}>${esc(o)}</option>`));
      return `<select class="owner-select${owner ? ' on' : ''}" data-cid="${esc(c.harvest_client_id)}"`
        + ` data-name="${esc(c.name)}" style="--oc:${owner ? ownerColor(owner) : '#cbd5e1'}"`
        + ` aria-label="Owner for ${esc(c.name)}">${opts.join('')}</select>`;
    }
    // Projects the current scope selects: everything under "all"; only projects
    // carrying the matching tag under "retainer"/"project" (untagged excluded).
    function projectsInScope(c) {
      const ps = c.projects || [];
      return view.scope === 'all' ? ps : ps.filter(p => p.tag === view.scope);
    }
    // Cumulative series are additive across disjoint projects, so summing them
    // elementwise gives the scope's cumulative series.
    function sumSeries(ps, key) {
      const out = [];
      ps.forEach(p => { const s = p[key] || []; for (let i = 0; i < s.length; i++) out[i] = (out[i] || 0) + s[i]; });
      return out;
    }
    // The selected metric's cumulative series. Non-billable is derived as the
    // total minus the billable series (both carried per project).
    function metricSeries(ps) {
      if (view.metric === 'nonbillable') {
        const all = sumSeries(ps, 'series'), bil = sumSeries(ps, 'series_billable');
        const out = [];
        for (let i = 0; i < all.length; i++) out[i] = Math.max(0, (all[i] || 0) - (bil[i] || 0));
        return out;
      }
      return sumSeries(ps, 'series_billable');
    }
    const seriesOf = c => metricSeries(projectsInScope(c));
    const totalOf  = c => { const s = seriesOf(c); return s.length ? s[s.length - 1] : 0; };
    const metricLabel = () => (view.metric === 'nonbillable' ? 'non-billable' : 'billable');

    // On/off-track status by projecting the current run-rate to month end and
    // comparing it to the goal (range, floor, or single value):
    //   behind = projected under the floor · over = projected past the ceiling ·
    //   on = landing inside the goal · none = no goal set (neutral blue).
    const STATUS = {
      on:     { color:'#0a7f3f', fill:'rgba(10,127,63,.10)',  label:'On track' },
      over:   { color:'#b7791f', fill:'rgba(183,121,31,.11)', label:'Over pace' },
      behind: { color:'#b42318', fill:'rgba(180,35,24,.09)',  label:'Behind' },
      none:   { color:'#2f6df0', fill:'rgba(47,109,240,.10)', label:'' },
    };
    function trackStatus(c, meta) {
      const lo = c.goal_min, hi = c.goal_max;
      if (lo == null && hi == null) return 'none';
      const total = totalOf(c);
      const frac = meta.days_in_month ? meta.days_elapsed / meta.days_in_month : 0;
      const projected = frac > 0 ? total / frac : total;
      if (lo != null && projected < lo * 0.95) return 'behind';
      if (hi != null && projected > hi * 1.03) return 'over';
      return 'on';
    }

    // Burn-up SVG: cumulative actual hours (solid, colored by track status) vs the
    // goal pace — a straight line for a single goal, a dashed floor for an open
    // "N+" goal, or a shaded corridor between the min and max pace for a range.
    function chart(c, meta) {
      const W = 320, H = 180, padL = 34, padR = 12, padT = 12, padB = 22;
      const days = meta.days_in_month, elapsed = meta.days_elapsed;
      const lo = (c.goal_min != null && c.goal_min > 0) ? Number(c.goal_min) : null;
      const hi = (c.goal_max != null && c.goal_max > 0) ? Number(c.goal_max) : null;
      const gTop = (hi != null ? hi : lo);
      const series = seriesOf(c);
      const st = STATUS[trackStatus(c, meta)] || STATUS.none;
      const actualMax = series.length ? Math.max.apply(null, series) : 0;
      const yMax = Math.max(actualMax, gTop || 0, 1) * 1.08;
      const x = d => padL + (W - padL - padR) * ((d - 1) / Math.max(1, days - 1));
      const y = v => H - padB - (H - padT - padB) * (v / yMax);

      // Horizontal gridlines + y labels (0, mid, max-ish).
      const ticks = [0, yMax/2, yMax];
      let grid = '';
      ticks.forEach(t => {
        const yy = y(t).toFixed(1);
        grid += `<line x1="${padL}" y1="${yy}" x2="${W-padR}" y2="${yy}" stroke="var(--grid)" stroke-width="1"/>`;
        grid += `<text x="${padL-6}" y="${(y(t)+3).toFixed(1)}" text-anchor="end" font-size="9" fill="#94a3b8">${Math.round(t)}</text>`;
      });
      // X axis day labels (1, mid, last).
      let xlab = '';
      [1, Math.round(days/2), days].forEach(d => {
        xlab += `<text x="${x(d).toFixed(1)}" y="${H-6}" text-anchor="middle" font-size="9" fill="#94a3b8">${d}</text>`;
      });

      // Goal pace element(s): all pace lines start at (day 1, 0).
      let goalEl = '';
      const gx1 = x(1).toFixed(1), gy0 = y(0).toFixed(1), gxN = x(days).toFixed(1);
      if (lo != null && hi != null && hi !== lo) {
        // Shaded corridor between the min-pace and max-pace lines + dashed edges.
        const yLo = y(lo).toFixed(1), yHi = y(hi).toFixed(1);
        goalEl = `<polygon points="${gx1},${gy0} ${gxN},${yLo} ${gxN},${yHi}" fill="rgba(154,167,184,.16)"/>`
          + `<line x1="${gx1}" y1="${gy0}" x2="${gxN}" y2="${yLo}" stroke="var(--goal)" stroke-width="1.3" stroke-dasharray="4 3"/>`
          + `<line x1="${gx1}" y1="${gy0}" x2="${gxN}" y2="${yHi}" stroke="var(--goal)" stroke-width="1.3" stroke-dasharray="4 3"/>`;
      } else if (gTop != null) {
        // Single goal or open-ended floor: one dashed pace line.
        goalEl = `<line x1="${gx1}" y1="${gy0}" x2="${gxN}" y2="${y(gTop).toFixed(1)}" stroke="var(--goal)" stroke-width="1.5" stroke-dasharray="4 3"/>`;
      }

      // Hard ceiling: a solid red cap line at the ceiling (goal_max) with a faint
      // "no-bill zone" tint above it — the team must stop here every month.
      let capEl = '';
      if (c.goal_hard && hi != null) {
        const yc = y(hi), plotW = (W - padL - padR).toFixed(1);
        const zoneH = Math.max(0, yc - padT).toFixed(1);
        capEl = `<rect x="${padL}" y="${padT}" width="${plotW}" height="${zoneH}" fill="rgba(180,35,24,.055)"/>`
          + `<line x1="${padL}" y1="${yc.toFixed(1)}" x2="${W-padR}" y2="${yc.toFixed(1)}" stroke="#b42318" stroke-width="1.5"/>`
          + `<text x="${W-padR}" y="${(yc-4).toFixed(1)}" text-anchor="end" font-size="8.5" font-weight="800" fill="#b42318">Ceiling</text>`;
      }

      // Actual cumulative line + soft fill under it, colored by track status.
      let actualEl = '';
      if (series.length) {
        const pts = series.map((v, i) => `${x(i+1).toFixed(1)},${y(v).toFixed(1)}`);
        const line = pts.join(' ');
        const area = `${x(1).toFixed(1)},${gy0} ${line} ${x(elapsed).toFixed(1)},${gy0}`;
        actualEl = `<polygon points="${area}" fill="${st.fill}"/>`
          + `<polyline points="${line}" fill="none" stroke="${st.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;
        const last = series[series.length-1];
        actualEl += `<circle cx="${x(elapsed).toFixed(1)}" cy="${y(last).toFixed(1)}" r="2.6" fill="${st.color}"/>`;
      }
      return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(c.name)} hours burn-up">`
        + grid + xlab + goalEl + capEl + actualEl + `</svg>`;
    }

    // Goal control: an inline editor for admins, a static label in the read-only
    // shared view (no mutation affordance).
    function goalEditor(c) {
      if (CH_CONFIG.readOnly) {
        return c.goal_label
          ? `<div class="goal"><span class="goal-ro"><b>${esc(c.goal_label)}</b> goal</span></div>`
          : `<div class="goal"><span class="goal-ro">No goal</span></div>`;
      }
      const label = c.goal_label ? `<span class="g-set">${esc(c.goal_label)} goal</span>` : 'Set goal';
      const cur = c.goal_label ? esc(c.goal_label.replace(/h/g, '').replace('–', '-')) : '';
      return `<div class="goal" data-cid="${esc(c.harvest_client_id)}" data-name="${esc(c.name)}">`
        + `<button type="button" class="goal-btn">${label}</button>`
        + `<span class="goal-edit">`
          + `<input type="text" value="${cur}" placeholder="80 or 80-100 or 80+" aria-label="Monthly hours goal" title="A number (80), a range (80-100), or an open floor (80+)">`
          + `<button type="button" class="goal-save">Save</button>`
          + `<button type="button" class="goal-cancel">✕</button>`
        + `</span></div>`;
    }

    // Reconstruct the goal-text form of a client's stored bounds ("80" / "80-100"
    // / "80+"), so the hard-ceiling toggle can re-POST to /goal without a separate
    // endpoint. Mirrors parse_goal_text server-side.
    function goalText(c) {
      const lo = c.goal_min, hi = c.goal_max;
      if (lo == null && hi == null) return '';
      if (hi == null) return `${lo}+`;
      if (lo == null) return `${hi}`;
      return lo === hi ? `${lo}` : `${lo}-${hi}`;
    }

    // Hard-ceiling control inside the details popout. Admin: a checkbox that draws
    // the cap line (disabled until a goal ceiling exists). Read-only: a static
    // "Hard cap / Flexible" read, shown only when the client has a ceiling.
    function hardCeilingControl(c) {
      const hasCeil = c.goal_max != null;
      if (CH_CONFIG.readOnly) {
        if (!hasCeil) return '';
        return `<div class="cd-row cd-cap"><span class="cd-label">Ceiling</span>`
          + (c.goal_hard
              ? `<span class="cap-note">Hard cap · ${hrs(c.goal_max)}h</span>`
              : `<span class="cd-muted">Flexible</span>`)
          + `</div>`;
      }
      const dis = hasCeil ? '' : ' disabled';
      const title = hasCeil
        ? 'Draw a hard cap at the ceiling — the team stops here, no billing over'
        : 'Set a goal with a ceiling (e.g. 40 or 80-100) to enable a hard cap';
      return `<div class="cd-row cd-cap" data-cid="${esc(c.harvest_client_id)}" data-name="${esc(c.name)}">`
        + `<label class="cap-toggle${hasCeil ? '' : ' is-dim'}" title="${esc(title)}">`
          + `<input type="checkbox" class="cap-check"${c.goal_hard ? ' checked' : ''}${dis}>`
          + `<span>Hard ceiling${hasCeil ? ` at ${hrs(c.goal_max)}h` : ''}</span>`
        + `</label></div>`;
    }

    // Project tags for this client, listed in the popout: each Harvest project
    // with an Untagged / Retainer / Project segmented control. Admin-only (the
    // read-only view has no tagging), and only when the client has projects.
    // Replaces the old slide-out "Tag projects" modal.
    function projectTagsSection(c) {
      if (CH_CONFIG.readOnly) return '';
      const ps = (c.projects || []).slice()
        .sort((a, b) => (b.total_hours || 0) - (a.total_hours || 0));
      if (!ps.length) return '';
      const opts = [['', 'Untagged'], ['retainer', 'Retainer'], ['project', 'Project']];
      const rows = ps.map(p => {
        const cur = p.tag || '';
        const seg = `<div class="tag-seg" data-pid="${esc(p.project_id)}" data-cid="${esc(c.harvest_client_id)}"`
          + ` data-cname="${esc(c.name)}" data-pname="${esc(p.name)}">`
          + opts.map(([v, l]) =>
              `<button type="button" data-tag="${v}" class="${cur === v ? 'on' : ''}">${l}</button>`).join('')
          + `</div>`;
        return `<div class="cd-proj"><div class="cd-proj-name">`
          + `<span class="nm" title="${esc(p.name)}">${esc(p.name)}</span>`
          + `<span class="cd-proj-hrs">${hrs(p.total_hours || 0)}h</span></div>${seg}</div>`;
      }).join('');
      return `<div class="cd-projects"><span class="cd-label">Projects</span>${rows}</div>`;
    }

    // The per-card details popout: owner, goal, the hard-ceiling toggle, and the
    // project tags — tucked behind the kebab so the card face stays quiet.
    function detailsPanel(c) {
      const ownerHtml = ownerChip(c);
      const ownerCell = ownerHtml || '<span class="cd-muted">Unassigned</span>';
      return `<div class="card-details" hidden>`
        + `<div class="cd-row"><span class="cd-label">Owner</span>${ownerCell}</div>`
        + `<div class="cd-row cd-goal"><span class="cd-label">Monthly goal</span>${goalEditor(c)}</div>`
        + hardCeilingControl(c)
        + projectTagsSection(c)
      + `</div>`;
    }

    function card(c, meta) {
      const stKey = trackStatus(c, meta);
      const st = STATUS[stKey] || STATUS.none;
      const kind = metricLabel();
      const goalSwatch = c.goal_hard && c.goal_max != null
        ? `<span><i class="goal"></i>Goal pace</span><span><i style="border-top-color:#b42318"></i>Hard ceiling</span>`
        : `<span><i class="goal"></i>${(c.goal_min != null && c.goal_max != null && c.goal_max !== c.goal_min) ? 'Goal range' : 'Goal pace'}</span>`;
      return `<div class="card" data-cid="${esc(c.harvest_client_id)}">`
        + `<div class="card-head">`
          + `<div style="min-width:0"><div class="card-title" title="${esc(c.name)}">${esc(c.name)}</div>`
          + `<div class="card-total"><b>${hrs(totalOf(c))}h</b> ${kind}</div></div>`
          + `<button type="button" class="card-more" aria-label="Details for ${esc(c.name)}" aria-expanded="false" title="Details">`
            + `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">`
            + `<circle cx="5" cy="12" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="19" cy="12" r="1.9"/></svg>`
          + `</button>`
        + `</div>`
        + `<div class="chart-wrap">${chart(c, meta)}</div>`
        + `<div class="card-foot">`
          + `<div class="legend"><span><i class="actual" style="border-top-color:${st.color}"></i>Hours logged</span>`
            + goalSwatch + `</div>`
        + `</div>`
        + detailsPanel(c)
      + `</div>`;
    }

    // "updated 3h ago" from an ISO timestamp — the burn-up data is cached
    // server-side (default 6h) so refreshes don't hammer Harvest; this shows how
    // fresh the currently-displayed pull is.
    function agoTxt(iso) {
      if (!iso) return '';
      const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
      if (secs < 60) return 'just now';
      const m = Math.round(secs / 60);
      if (m < 60) return `${m}m ago`;
      const h = Math.round(m / 60);
      return `${h}h ago`;
    }

    // The book the current scope selects (before search / pace filters) — the
    // universe both the grid and the pace-chip counts are drawn from.
    function clientsInScope(meta) {
      let clients = (meta.clients || []).slice();
      // In a split scope, drop clients with no hours of that type this month.
      if (view.scope !== 'all') clients = clients.filter(c => totalOf(c) > 0);
      return clients;
    }
    // Tally the scoped book by pace: behind (won't hit minimum) / over (past
    // ceiling) / no goal set. Drives the At risk · Growth chip counts + the
    // "N no goal" flag, so blind spots (clients that can't be paced) stay visible.
    function paceCounts(meta) {
      let risk = 0, grow = 0, noGoal = 0;
      clientsInScope(meta).forEach(c => {
        const s = trackStatus(c, meta);
        if (s === 'behind') risk++;
        else if (s === 'over') grow++;
        else if (s === 'none') noGoal++;
      });
      return { risk, grow, noGoal };
    }

    function sortedClients(meta) {
      let clients = clientsInScope(meta);
      // Pace filter (At risk / Growth chips): keep only clients at that status.
      if (view.status) clients = clients.filter(c => trackStatus(c, meta) === view.status);
      // Owner filter: a specific owner, or unassigned-only.
      if (view.owner === '__none__') clients = clients.filter(c => !c.owner);
      else if (view.owner) clients = clients.filter(c => c.owner === view.owner);
      // Free-text name filter (case-insensitive substring) — the search bar
      // narrows the visible cards without re-hitting Harvest.
      const q = view.search.trim().toLowerCase();
      if (q) clients = clients.filter(c => String(c.name||'').toLowerCase().includes(q));
      if (view.sort === 'alpha') {
        clients.sort((a, b) => String(a.name||'').localeCompare(String(b.name||''),
          undefined, { sensitivity: 'base' }));
      } else {
        clients.sort((a, b) => totalOf(b) - totalOf(a));
      }
      return clients;
    }

    // ── Agency billing summary ────────────────────────────────────────────
    // The strip above the grid: a always-billable, scope-aware billing read of
    // the whole book. Deliberately independent of the Billable/Non-billable
    // metric toggle (a billing summary is billable by definition); it does honor
    // the scope dropdown so it always describes the same set as the cards below.
    // Agency-wide cumulative billable series for the current scope: sum each
    // client's scoped billable series elementwise (all share the same day index),
    // so out[i] is total billable hours logged agency-wide through day i+1.
    function scopeBillableSeries(meta) {
      const out = [];
      (meta.clients || []).forEach(c => {
        const s = sumSeries(projectsInScope(c), 'series_billable');
        for (let i = 0; i < s.length; i++) out[i] = (out[i] || 0) + s[i];
      });
      return out;
    }
    // Agency on/off-track from the pace projection vs the contracted floor/ceiling
    // — the same run-rate method the per-client cards use, one level up.
    function agencyStatus(projected, floor, ceil) {
      if (!floor && !ceil) return 'none';
      if (floor && projected < floor * 0.98) return 'behind';
      if (ceil && projected > ceil * 1.03) return 'over';
      return 'on';
    }
    // One full-width burn-up: cumulative billable hours (solid, status-colored) vs
    // the total contracted-minimum pace (dashed grey), with a dotted projection
    // from today's run-rate to month-end. Dual axis — hours left, modeled $ right —
    // so the same line reads as both hours logged and billable dollars.
    function summaryChart(ctx) {
      const { floor, series, actual, projected, elapsed, days, st, showFloor } = ctx;
      const W = 960, H = 280, padL = 46, padR = 92, padT = 18, padB = 30;
      const yMax = Math.max(actual, projected, showFloor ? floor : 0, 1) * 1.12;
      const x = d => padL + (W - padL - padR) * ((d - 1) / Math.max(1, days - 1));
      const y = v => H - padB - (H - padT - padB) * (v / yMax);

      // Horizontal gridlines + hours labels on the left. Dollars are carried by the
      // hero figure and the projection endpoint, so the axis stays a single scale.
      let grid = '';
      [0, 0.25, 0.5, 0.75, 1].forEach(f => {
        const t = yMax * f, yy = y(t).toFixed(1);
        grid += `<line x1="${padL}" y1="${yy}" x2="${W-padR}" y2="${yy}" stroke="var(--grid)" stroke-width="1"/>`;
        grid += `<text x="${padL-8}" y="${(y(t)+3.5).toFixed(1)}" text-anchor="end" font-size="11" fill="#94a3b8">${Math.round(t)}h</text>`;
      });
      // X axis day labels (1 · quarters · last).
      let xlab = '';
      Array.from(new Set([1, Math.round(days/4), Math.round(days/2), Math.round(days*3/4), days]))
        .forEach(d => {
          xlab += `<text x="${x(d).toFixed(1)}" y="${H-9}" text-anchor="middle" font-size="11" fill="#94a3b8">${d}</text>`;
        });

      // "Today" divider — where booked hours end and the projection takes over.
      const tx = x(elapsed).toFixed(1);
      const todayEl = elapsed < days
        ? `<line x1="${tx}" y1="${y(0).toFixed(1)}" x2="${tx}" y2="${padT}" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="2 3"/>`
        : '';

      // Contracted-minimum pace: dashed grey line from (day 1, 0) to (month end, floor).
      let goalEl = '';
      if (showFloor && floor > 0) {
        goalEl = `<line x1="${x(1).toFixed(1)}" y1="${y(0).toFixed(1)}" x2="${x(days).toFixed(1)}" y2="${y(floor).toFixed(1)}"`
          + ` stroke="var(--goal)" stroke-width="1.6" stroke-dasharray="5 4"/>`
          + `<text x="${(x(days)-2).toFixed(1)}" y="${(y(floor)-6).toFixed(1)}" text-anchor="end" font-size="10.5"`
          + ` font-weight="700" fill="#8794a6">Contracted min ${hrs(floor)}h</text>`;
      }

      // Booked billable line (solid + soft fill) up to today, colored by track status.
      let actualEl = '';
      if (series.length) {
        const pts = series.map((v, i) => `${x(i+1).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
        const area = `${x(1).toFixed(1)},${y(0).toFixed(1)} ${pts} ${tx},${y(0).toFixed(1)}`;
        actualEl = `<polygon points="${area}" fill="${st.fill}"/>`
          + `<polyline points="${pts}" fill="none" stroke="${st.color}" stroke-width="2.6"`
          + ` stroke-linejoin="round" stroke-linecap="round"/>`
          + `<circle cx="${tx}" cy="${y(actual).toFixed(1)}" r="3.4" fill="${st.color}"/>`;
      }

      // Projection: dotted continuation of the run-rate from today to month-end,
      // ending at an annotated marker (projected hours + modeled dollars).
      let projEl = '';
      if (elapsed < days && projected > 0) {
        const ex = x(days).toFixed(1), ey = y(projected).toFixed(1);
        projEl = `<line x1="${tx}" y1="${y(actual).toFixed(1)}" x2="${ex}" y2="${ey}"`
          + ` stroke="${st.color}" stroke-width="2" stroke-dasharray="2 4" stroke-linecap="round" opacity="0.7"/>`
          + `<circle cx="${ex}" cy="${ey}" r="3.4" fill="#fff" stroke="${st.color}" stroke-width="2"/>`
          + `<text x="${(x(days)+8).toFixed(1)}" y="${(y(projected)-2).toFixed(1)}" text-anchor="start"`
          + ` font-size="11" font-weight="800" fill="${st.color}">${hrs(projected)}h</text>`
          + `<text x="${(x(days)+8).toFixed(1)}" y="${(y(projected)+11).toFixed(1)}" text-anchor="start"`
          + ` font-size="10" fill="#94a3b8">${usdK(projected*STANDARD_RATE)}</text>`;
      }

      return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Agency billable hours burn-up">`
        + grid + xlab + todayEl + goalEl + actualEl + projEl + `</svg>`;
    }

    function summaryCard(meta) {
      const box = document.getElementById('chSummary');
      const t = meta.totals || {};
      const floor = Number(t.goal_min || 0);   // Σ contracted minimums (agency-wide).
      const ceil  = Number(t.goal_max || 0);   // Σ ceilings (open "N+" floors contribute their floor).
      const series = scopeBillableSeries(meta);
      const actual = series.length ? series[series.length - 1] : 0;
      const elapsed = meta.days_elapsed, days = meta.days_in_month;
      const frac = days ? elapsed / days : 0;
      const projected = frac > 0 ? actual / frac : actual;
      const scopeLbl = view.scope === 'retainer' ? 'Retainer'
        : (view.scope === 'project' ? 'Project' : 'All work');
      // A retainer floor only means something against retainer/all billables — under
      // a pure "Project" scope there's no contracted minimum to pace against. And
      // the on/off-track colouring only reads right in retainer scope (under "All"
      // the floor is beaten simply because project work is folded in), so there we
      // still show the line but stay neutral.
      const showFloor = floor > 0 && view.scope !== 'project';
      const stKey = (showFloor && view.scope === 'retainer')
        ? agencyStatus(projected, floor, ceil) : 'none';
      const st = STATUS[stKey] || STATUS.none;

      const projRev = projected * STANDARD_RATE, actualRev = actual * STANDARD_RATE;
      const pill = st.label
        ? `<span class="s-pill" style="color:${st.color};background:${st.fill}">${st.label}</span>` : '';
      const floorNote = showFloor
        ? ` · ${projRev >= floor*STANDARD_RATE ? '+' : '−'}${usdK(Math.abs(projRev - floor*STANDARD_RATE))} vs floor`
        : '';

      box.className = 'summary';
      box.innerHTML = `<div class="summary-head">`
        + `<div><h3>Agency billing — ${esc(meta.month_label)}</h3>`
          + `<div class="s-note">Billable · ${scopeLbl} · day <b>${elapsed}</b> of ${days}`
            + ` · modeled @ $${STANDARD_RATE}/hr</div></div>`
        + `<div class="s-hero"><div class="s-hero-label">Est. billable this month${pill}</div>`
          + `<div class="s-hero-value">${usd(projRev)}</div>`
          + `<div class="s-hero-sub"><b>${usd(actualRev)}</b> booked · ${hrs(actual)}h logged${floorNote}</div></div>`
        + `</div>`
        + `<div class="summary-chart">`
          + summaryChart({ floor, series, actual, projected, elapsed, days, st, showFloor })
        + `</div>`
        + `<div class="summary-legend">`
          + `<span><i style="border-top-color:${st.color};border-top-style:solid"></i>Billable logged</span>`
          + (showFloor ? `<span><i style="border-top-color:var(--goal);border-top-style:dashed"></i>Contracted minimum</span>` : '')
          + (elapsed < days ? `<span><i style="border-top-color:${st.color};border-top-style:dotted"></i>Projected month-end</span>` : '')
        + `</div>`;
    }

    // Refresh the At risk / Growth chip counts + the "N no goal" flag from the
    // current scope, and reflect which chip (if any) is the active filter.
    function updatePaceChips(meta) {
      const { risk, grow, noGoal } = paceCounts(meta);
      const riskBtn = document.getElementById('chRisk');
      const growBtn = document.getElementById('chGrow');
      document.getElementById('chRiskCount').textContent = risk;
      document.getElementById('chGrowCount').textContent = grow;
      // Keep an active chip clickable (so it can toggle off) even if its count is 0.
      riskBtn.disabled = risk === 0 && view.status !== 'behind';
      growBtn.disabled = grow === 0 && view.status !== 'over';
      riskBtn.classList.toggle('on', view.status === 'behind');
      growBtn.classList.toggle('on', view.status === 'over');
      riskBtn.setAttribute('aria-pressed', String(view.status === 'behind'));
      growBtn.setAttribute('aria-pressed', String(view.status === 'over'));
      const note = document.getElementById('chNoGoal');
      if (noGoal > 0) {
        note.hidden = false;
        note.textContent = `${noGoal} no goal`;
        note.title = `${noGoal} client${noGoal===1?'':'s'} in view ${noGoal===1?'has':'have'} no goal set`
          + ` — set a goal to include ${noGoal===1?'it':'them'} in the at-risk read`;
      } else note.hidden = true;
    }

    function render() {
      const meta = chData;
      const sub = document.getElementById('chSub');
      const fresh = meta.refreshed_at ? ` · updated ${agoTxt(meta.refreshed_at)}` : '';
      updatePaceChips(meta);
      const shown = sortedClients(meta);
      const totalShown = shown.reduce((s, c) => s + totalOf(c), 0);
      const scopeLabel = view.scope === 'retainer' ? 'retainer ' : (view.scope === 'project' ? 'project ' : '');
      const kind = metricLabel();
      sub.textContent = `${meta.month_label} · day ${meta.days_elapsed} of ${meta.days_in_month}`
        + ` · ${shown.length} clients · ${hrs(totalShown)}h ${scopeLabel}${kind}`
        + (meta.account_name ? ` · ${meta.account_name}` : '') + fresh;

      const notice = document.getElementById('chNotice');
      if (meta.error) {
        // The "Connect Harvest →" affordance is admin-only; a public viewer can't
        // act on it, so the read-only page just shows the message.
        const connect = (!meta.connected && !CH_CONFIG.readOnly)
          ? ' <a href="/admin">Connect Harvest →</a>' : '';
        notice.innerHTML = `<div class="notice ${meta.connected?'warn':'err'}">${esc(meta.error)}${connect}</div>`;
      } else notice.innerHTML = '';

      const summary = document.getElementById('chSummary');
      if (!meta.error && (meta.clients || []).length) summaryCard(meta);
      else { summary.className = ''; summary.innerHTML = ''; }

      const grid = document.getElementById('chGrid');
      if (!shown.length) {
        const q = view.search.trim();
        let msg;
        if (meta.error) msg = '';
        else if (view.status) {
          // Pace filter active but nothing matches (possibly narrowed further by search).
          const lbl = view.status === 'behind' ? 'at-risk' : 'growth-opportunity';
          msg = q
            ? `<div class="empty">No ${lbl} clients match “${esc(q)}”.</div>`
            : `<div class="empty">No ${lbl} clients in this view.</div>`;
        } else if (view.owner) {
          const who = view.owner === '__none__'
            ? 'unassigned clients' : `clients owned by ${esc(view.owner)}`;
          msg = q
            ? `<div class="empty">No ${who} match “${esc(q)}”.</div>`
            : `<div class="empty">No ${who} in this view.</div>`;
        } else if (q) {
          msg = `<div class="empty">No clients match “${esc(q)}”.</div>`;
        } else if (view.scope === 'all') {
          msg = `<div class="empty">No hours logged yet this month.</div>`;
        } else {
          msg = `<div class="empty">No ${view.scope} hours this month.${CH_CONFIG.readOnly ? '' : ' Switch to “All work” and open a card’s details (⋯) to tag its projects.'}</div>`;
        }
        grid.innerHTML = msg;
        return;
      }
      grid.innerHTML = shown.map(c => card(c, meta)).join('');
    }

    async function load(force) {
      const btn = document.getElementById('chRefresh');
      if (force && btn) { btn.disabled = true; btn.classList.add('spin'); }
      else document.getElementById('chGrid').innerHTML =
        Array.from({length:6}, () => `<div class="skel-card"></div>`).join('');
      try {
        const r = await fetch(CH_CONFIG.dataUrl + (force ? '?refresh=1' : ''),
          { credentials:'same-origin' });
        if (!r.ok) throw new Error('HTTP '+r.status);
        chData = await r.json();
        render();
      } catch (e) {
        document.getElementById('chSub').textContent = 'Failed to load Harvest hours.';
        if (!force) document.getElementById('chGrid').innerHTML =
          `<div class="empty">Could not load hours (${esc(e.message||'error')}). Try refreshing.</div>`;
      } finally {
        if (btn) { btn.disabled = false; btn.classList.remove('spin'); }
      }
    }

    // Segmented controls (All/Billable hours, Highest/A–Z sort): update view
    // state and re-render off the cached data — no Harvest round-trip.
    function wireSeg(id, key, attr) {
      const seg = document.getElementById(id);
      seg.addEventListener('click', (ev) => {
        const b = ev.target.closest('.seg-btn'); if (!b) return;
        view[key] = b.dataset[attr];
        seg.querySelectorAll('.seg-btn').forEach(x => x.classList.toggle('is-active', x === b));
        if (chData) render();
      });
    }
    document.getElementById('chScope').addEventListener('change', (ev) => {
      view.scope = ev.target.value;
      if (chData) render();
    });
    document.getElementById('chOwner').addEventListener('change', (ev) => {
      view.owner = ev.target.value;
      if (chData) render();
    });
    wireSeg('chMetric', 'metric', 'metric');
    wireSeg('chSort', 'sort', 'sort');
    // Pace filter chips: toggle the At risk / Growth filter (re-click clears it).
    document.getElementById('chStatus').addEventListener('click', (ev) => {
      const b = ev.target.closest('.pace-chip'); if (!b || b.disabled) return;
      const s = b.dataset.status;
      view.status = (view.status === s) ? null : s;
      if (chData) render();
    });

    // Client name search: filter the cards live off the cached data as the user
    // types (no Harvest round-trip). The clear button resets the field.
    const chSearch = document.getElementById('chSearch');
    const chSearchClear = document.getElementById('chSearchClear');
    chSearch.addEventListener('input', () => {
      view.search = chSearch.value;
      chSearchClear.hidden = !chSearch.value;
      if (chData) render();
    });
    chSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && chSearch.value) { e.stopPropagation();
        chSearch.value = ''; view.search = ''; chSearchClear.hidden = true;
        if (chData) render(); }
    });
    chSearchClear.addEventListener('click', () => {
      chSearch.value = ''; view.search = ''; chSearchClear.hidden = true;
      chSearch.focus(); if (chData) render();
    });

    // Per-card details popout (kebab): open one at a time, close others. Shared by
    // the admin and read-only views. Clicks inside the panel (goal editor, owner,
    // ceiling toggle) are handled by their own delegated listeners.
    function closeDetails(except) {
      document.querySelectorAll('.card-details').forEach(p => {
        if (p !== except) p.setAttribute('hidden', ''); });
      document.querySelectorAll('.card-more').forEach(b => {
        const panel = b.closest('.card').querySelector('.card-details');
        b.setAttribute('aria-expanded', String(!!panel && !panel.hasAttribute('hidden')));
      });
    }
    document.getElementById('chGrid').addEventListener('click', (ev) => {
      const btn = ev.target.closest('.card-more'); if (!btn) return;
      const panel = btn.closest('.card').querySelector('.card-details');
      const willOpen = panel.hasAttribute('hidden');
      if (willOpen) panel.removeAttribute('hidden'); else panel.setAttribute('hidden', '');
      closeDetails(willOpen ? panel : null);
    });
    // Click outside any popout (and not on a kebab) closes them all.
    document.addEventListener('click', (ev) => {
      if (ev.target.closest('.card-details') || ev.target.closest('.card-more')) return;
      closeDetails(null);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeDetails(null);
    });

"""


# Admin-only client-side wiring — the manual Harvest refresh, inline goal editing,
# the project-tag modal, and the share-link manager. Appended to the page JS only
# for the signed-in admin view, so the read-only shared page ships none of it (not
# even as dead source). The ``if (!CH_CONFIG.readOnly)`` guard is belt-and-braces.
_PAGE_JS_ADMIN = r"""
    // ── Admin-only wiring ─────────────────────────────────────────────────
    if (!CH_CONFIG.readOnly) {
      const chRefreshBtn = document.getElementById('chRefresh');
      if (chRefreshBtn) chRefreshBtn.addEventListener('click', () => load(true));

      // Inline goal editing: toggle the input, POST on save, then re-render just
      // that client's number (cheap full re-render off the cached data).
      function wireGoals() {
        const grid = document.getElementById('chGrid');
        grid.addEventListener('click', async (ev) => {
          const g = ev.target.closest('.goal'); if (!g) return;
          const editor = g.querySelector('.goal-edit');
          const btn = g.querySelector('.goal-btn');
          if (ev.target.closest('.goal-btn')) {
            editor.classList.add('on'); btn.style.display='none';
            const inp = editor.querySelector('input'); inp.focus(); inp.select(); return;
          }
          if (ev.target.closest('.goal-cancel')) {
            editor.classList.remove('on'); btn.style.display=''; return;
          }
          if (ev.target.closest('.goal-save')) {
            const cid = g.dataset.cid;
            const raw = editor.querySelector('input').value.trim();
            // Preserve the current hard-ceiling choice from the same popout.
            const panel = g.closest('.card-details');
            const capChk = panel ? panel.querySelector('.cap-check') : null;
            const saveBtn = g.querySelector('.goal-save'); saveBtn.disabled = true;
            try {
              // The server parses "80" / "80-100" / "80+" and returns the stored
              // min/max + label; apply those so the chart re-colors immediately
              // (no Harvest round-trip — the hours series is unchanged).
              const body = new URLSearchParams({ harvest_client_id: cid, client_name: g.dataset.name,
                goal: raw, hard_ceiling: (capChk && capChk.checked) ? '1' : '' });
              const r = await fetch('/admin/client-hours/goal', { method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
              const b = await r.json().catch(()=>({}));
              if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
              const client = (chData.clients||[]).find(c => String(c.harvest_client_id) === String(cid));
              if (client) {
                client.goal_min = (b.goal_min == null ? null : Number(b.goal_min));
                client.goal_max = (b.goal_max == null ? null : Number(b.goal_max));
                client.goal_hard = !!b.goal_hard;
                client.goal_label = b.goal_label || '';
              }
              render();
            } catch (e) {
              saveBtn.disabled = false;
              alert('Could not save goal: ' + (e.message||e));
            }
          }
        });
      }
      wireGoals();

      // Hard-ceiling toggle: on check/uncheck, re-POST the client's existing goal
      // to /goal with the new hard flag. Re-render so the cap line draws/clears
      // immediately (no Harvest round-trip). Delegated on the grid.
      function wireCeilings() {
        const grid = document.getElementById('chGrid');
        grid.addEventListener('change', async (ev) => {
          const chk = ev.target.closest('.cap-check'); if (!chk) return;
          const row = chk.closest('.cd-cap'); const cid = row.dataset.cid;
          const client = (chData.clients || []).find(c => String(c.harvest_client_id) === String(cid));
          if (!client) return;
          const want = chk.checked;
          chk.disabled = true;
          try {
            const body = new URLSearchParams({ harvest_client_id: cid, client_name: row.dataset.name,
              goal: goalText(client), hard_ceiling: want ? '1' : '' });
            const r = await fetch('/admin/client-hours/goal', { method:'POST', credentials:'same-origin',
              headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
            const b = await r.json().catch(()=>({}));
            if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
            client.goal_hard = !!b.goal_hard;
            client.goal_min = (b.goal_min == null ? null : Number(b.goal_min));
            client.goal_max = (b.goal_max == null ? null : Number(b.goal_max));
            client.goal_label = b.goal_label || '';
            render();
          } catch (e) {
            chk.disabled = false; chk.checked = !want;
            alert('Could not update ceiling: ' + (e.message||e));
          }
        });
      }
      wireCeilings();

      // Owner chip: on select change, persist via /owner and update the in-memory
      // client so the chip re-colors and the owner filter stays consistent (no
      // Harvest round-trip). Delegated on the grid, which re-renders in place.
      function wireOwners() {
        const grid = document.getElementById('chGrid');
        grid.addEventListener('change', async (ev) => {
          const sel = ev.target.closest('.owner-select'); if (!sel) return;
          const cid = sel.dataset.cid;
          const owner = sel.value;
          const client = (chData.clients || []).find(c => String(c.harvest_client_id) === String(cid));
          const prev = client ? (client.owner || '') : '';
          sel.disabled = true;
          try {
            const body = new URLSearchParams({ harvest_client_id: cid, client_name: sel.dataset.name, owner });
            const r = await fetch('/admin/client-hours/owner', { method:'POST', credentials:'same-origin',
              headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
            const b = await r.json().catch(()=>({}));
            if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
            if (client) client.owner = b.owner || null;
            render();
          } catch (e) {
            sel.disabled = false;
            sel.value = prev;
            alert('Could not save owner: ' + (e.message||e));
          }
        });
      }
      wireOwners();

      // ── Project tags (in the per-card details popout) ───────────────────
      // Each card's popout lists the client's projects with an Untagged /
      // Retainer / Project selector. Changing one persists via /project-tag and
      // updates the in-memory data so the charts + scope views re-color
      // immediately (no Harvest round-trip). After the re-render we reopen the
      // same card's popout, so several of a client's projects can be tagged in
      // one sitting. Delegated on the grid.
      function openDetailsFor(cid) {
        if (cid == null) return;
        const sel = (window.CSS && CSS.escape) ? CSS.escape(String(cid)) : String(cid);
        const card = document.querySelector(`.card[data-cid="${sel}"]`);
        if (!card) return;
        const panel = card.querySelector('.card-details');
        const kebab = card.querySelector('.card-more');
        if (panel) panel.removeAttribute('hidden');
        if (kebab) kebab.setAttribute('aria-expanded', 'true');
      }
      document.getElementById('chGrid').addEventListener('click', async (ev) => {
        const btn = ev.target.closest('.cd-projects .tag-seg button'); if (!btn) return;
        const seg = btn.closest('.tag-seg');
        const pid = seg.dataset.pid, cid = seg.dataset.cid, newTag = btn.dataset.tag;
        const prev = seg.querySelector('button.on');
        // Optimistic: reflect the choice immediately.
        seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
        try {
          const body = new URLSearchParams({ harvest_project_id: pid, tag: newTag,
            project_name: seg.dataset.pname, client_name: seg.dataset.cname });
          const r = await fetch('/admin/client-hours/project-tag', { method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
          const b = await r.json().catch(()=>({}));
          if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
          // Update in-memory project tag across the dataset, then re-render charts.
          (chData.clients || []).forEach(c => (c.projects || []).forEach(p => {
            if (String(p.project_id) === String(pid)) p.tag = (b.tag || null);
          }));
          render();
          openDetailsFor(cid);
        } catch (e) {
          if (prev) seg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === prev));
          alert('Could not save tag: ' + (e.message||e));
        }
      });

      // ── Share-links modal ───────────────────────────────────────────────
      // Mint / list / revoke read-only share links. A link points at
      // /share/client-hours/{token}; anyone with an active one can view this
      // page without logging in.
      const shareModal = document.getElementById('shareModal');
      const shareBackdrop = document.getElementById('shareBackdrop');
      const shareBody = document.getElementById('shareBody');
      const shareLabel = document.getElementById('shareLabel');
      const shareCreate = document.getElementById('shareCreate');
      const shareUrl = token => location.origin + '/share/client-hours/' + token;

      function renderShareBody(links) {
        if (!links || !links.length) {
          shareBody.innerHTML = `<div class="tag-empty">No active links yet. Create one below — it works until you revoke it.</div>`;
          return;
        }
        shareBody.innerHTML = links.map(l => {
          const url = shareUrl(l.token);
          const bits = [];
          if (l.label) bits.push(esc(l.label));
          bits.push(`${l.view_count||0} view${l.view_count===1?'':'s'}`);
          return `<div class="share-row" data-token="${esc(l.token)}">`
            + `<div class="share-row-main"><div class="share-url" title="${esc(url)}">${esc(url)}</div>`
            + `<div class="share-meta">${bits.join(' · ')}</div></div>`
            + `<div class="share-actions">`
              + `<button type="button" class="goal-btn share-copy" data-url="${esc(url)}">Copy</button>`
              + `<button type="button" class="goal-btn share-revoke">Revoke</button>`
            + `</div></div>`;
        }).join('');
      }
      async function loadShares() {
        shareBody.innerHTML = `<div class="tag-empty">Loading…</div>`;
        try {
          const r = await fetch('/admin/client-hours/shares', { credentials:'same-origin' });
          const b = await r.json().catch(()=>({}));
          if (!r.ok) throw new Error(b.error || ('HTTP '+r.status));
          renderShareBody(b.links || []);
        } catch (e) {
          shareBody.innerHTML = `<div class="tag-empty">Could not load links (${esc(e.message||'error')}).</div>`;
        }
      }
      function openShare() { shareBackdrop.hidden = false; shareModal.hidden = false; loadShares(); }
      function closeShare() { shareBackdrop.hidden = true; shareModal.hidden = true; }
      document.getElementById('chShare').addEventListener('click', openShare);
      document.getElementById('shareClose').addEventListener('click', closeShare);
      shareBackdrop.addEventListener('click', closeShare);
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !shareModal.hidden) closeShare(); });

      shareCreate.addEventListener('click', async () => {
        shareCreate.disabled = true;
        try {
          const body = new URLSearchParams({ label: shareLabel.value || '' });
          const r = await fetch('/admin/client-hours/share', { method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
          const b = await r.json().catch(()=>({}));
          if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
          shareLabel.value = '';
          loadShares();
        } catch (e) {
          alert('Could not create link: ' + (e.message||e));
        } finally { shareCreate.disabled = false; }
      });

      shareBody.addEventListener('click', async (ev) => {
        const copyBtn = ev.target.closest('.share-copy');
        if (copyBtn) {
          const url = copyBtn.dataset.url;
          try {
            await navigator.clipboard.writeText(url);
            const orig = copyBtn.textContent; copyBtn.textContent = 'Copied';
            setTimeout(() => { copyBtn.textContent = orig; }, 1200);
          } catch (_) { window.prompt('Copy this link:', url); }
          return;
        }
        const revokeBtn = ev.target.closest('.share-revoke');
        if (revokeBtn) {
          const row = revokeBtn.closest('.share-row'); const token = row.dataset.token;
          if (!window.confirm('Revoke this link? Anyone using it will lose access immediately.')) return;
          revokeBtn.disabled = true;
          try {
            const body = new URLSearchParams({ token });
            const r = await fetch('/admin/client-hours/share/revoke', { method:'POST', credentials:'same-origin',
              headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
            const b = await r.json().catch(()=>({}));
            if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
            loadShares();
          } catch (e) { revokeBtn.disabled = false; alert('Could not revoke: ' + (e.message||e)); }
        }
      });
    }
"""


def _page_script(*, data_url: str, read_only: bool) -> str:
    """Assemble the page JS: a ``CH_CONFIG`` (data feed URL + read-only flag), the
    shared chart/view code, and — for the admin view only — the mutation wiring.
    The read-only shared page never receives the admin block, so its source
    contains no admin endpoints or controls."""
    import harvest_service

    config = "const CH_CONFIG = " + json.dumps({
        "dataUrl": data_url,
        "readOnly": read_only,
        "owners": list(harvest_service.CLIENT_OWNERS),
    }) + ";"
    admin = "" if read_only else _PAGE_JS_ADMIN
    return (
        "<script>\n    " + config + "\n"
        + _PAGE_JS + admin
        + "\n    load(false);\n  </script>"
    )


def render_client_hours_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/client-hours. Data loads from …/data."""
    return render_admin_shell_page(
        active_nav="hours",
        page_title="Hours",
        content_html=_content_html(read_only=False),
        session_email=user_email,
        extra_css=_HOURS_CSS,
        body_end_html=_page_script(data_url="/admin/client-hours/data", read_only=False),
    )


def render_shared_client_hours_page(*, token: str, label: str | None = None) -> str:
    """Full HTML for the public, read-only shared view
    (GET /share/client-hours/{token}). Standalone (no admin sidebar / account
    chip): a navy header with a "read-only" badge over the same burn-up grid,
    fed by the token's cache-only data endpoint."""
    data_url = f"/share/client-hours/{_esc(token)}/data"
    label_html = (
        f'<span class="ro-note">{_esc(label)}</span>' if (label or "").strip() else ""
    )
    body = f"""
  <header class="ro-topbar">
    <div class="ro-brand">
      <span class="ro-title">Sagefrog · Client Hours</span>
      <span class="ro-badge">Read-only</span>
    </div>
    {label_html}
  </header>
  {_content_html(read_only=True)}
  {_page_script(data_url=data_url, read_only=True)}"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Client Hours · Sagefrog Marketing Group</title>
  {favicon_head_html()}
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      color: var(--ink, #0f1c2e); line-height: 1.5; -webkit-font-smoothing: antialiased; }}
    {_HOURS_CSS}
  </style>
</head>
<body>
  {body}
</body>
</html>"""
