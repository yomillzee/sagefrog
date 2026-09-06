"""Site Performance tab (PageSpeed Insights) for the BigQuery/Nixon dashboard.

The dashboard renders client-side: these helpers return raw HTML/CSS/JS strings
that bigquery_dashboard_renderer injects into its page. The JS relies on shared
helpers already defined in that page's script scope (getJson, setStatus,
skelCards, count, esc, lineChart, __destroyChart) plus three consts the page
supplies — PAGESPEED_API, PAGESPEED_TARGETS_API, PAGESPEED_TARGETS — so it must
be emitted inside the same <script>. Data comes from
/api/clients/{key}/pagespeed/summary?strategy=… (see
bq_pagespeed_service.fetch_latest_snapshot).
"""

from __future__ import annotations

from typing import Any

# Per-KPI target bands used for traffic-light coloring when a client hasn't set
# their own (client_dashboard_config.pagespeed_targets overrides these). Defaults
# mirror the client scorecard mockup (Performance 50–60, Accessibility 90–95,
# SEO 65–75); Best Practices has no mockup value so gets a sensible 80–90.
DEFAULT_TARGETS: dict[str, dict[str, int]] = {
    "performance": {"min": 50, "max": 60},
    "accessibility": {"min": 90, "max": 95},
    "best_practices": {"min": 80, "max": 90},
    "seo": {"min": 65, "max": 75},
}

_KPIS = ("performance", "accessibility", "best_practices", "seo")


def effective_targets(stored: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    """Merge a client's stored targets over the defaults, keeping only the four
    known KPIs and clamping each band to 0–100 (min <= max)."""
    out = {k: dict(v) for k, v in DEFAULT_TARGETS.items()}
    for kpi in _KPIS:
        band = (stored or {}).get(kpi)
        if not isinstance(band, dict):
            continue
        try:
            lo = max(0, min(100, int(round(float(band.get("min"))))))
            hi = max(0, min(100, int(round(float(band.get("max"))))))
        except (TypeError, ValueError):
            continue
        if hi < lo:
            lo, hi = hi, lo
        out[kpi] = {"min": lo, "max": hi}
    return out


def pane_html() -> str:
    """The hidden #pane-site_performance section, mounted after #pane-gsc."""
    return """
    <div id="pane-site_performance" hidden>
      <section id="sec-ps-scores">
        <div class="sec-head">
          <h2>Site Performance</h2>
          <div class="ps-controls">
            <div class="ps-toggle" id="psToggle" role="group" aria-label="Device strategy"></div>
            <button type="button" class="ps-edit-btn" id="psEditTargets">Edit targets</button>
            <span class="status" id="psStatus"></span>
          </div>
        </div>
        <div class="cards" id="psScores"></div>
        <div class="ps-editor" id="psEditor" hidden>
          <div class="ps-editor-head">Target bands (0–100). A score at or above the low end shows green; within 10 below, amber; further below, red.</div>
          <div class="ps-editor-fields" id="psEditorFields"></div>
          <div class="ps-editor-actions">
            <button type="button" class="primary" id="psTargetsSave">Save targets</button>
            <button type="button" class="ps-btn-plain" id="psTargetsCancel">Cancel</button>
            <span class="status" id="psTargetsStatus"></span>
          </div>
        </div>
      </section>
      <section id="sec-ps-cwv">
        <div class="sec-head"><h2>Core Web Vitals</h2></div>
        <div class="cards" id="psCwv"></div>
      </section>
      <section id="sec-ps-field">
        <div class="sec-head">
          <h2>Real visitors (last 6 months)</h2>
          <span class="status" id="psFieldStatus"></span>
        </div>
        <div class="ps-field-intro">
          What Chrome users actually experienced on this site, from Google's Chrome UX Report.
          The cards above are a single lab test; these are real visits, and Google uses
          <strong>these</strong> numbers for search. Each point is a 28-day average, stepped weekly.
        </div>
        <div id="psAssessment"></div>
        <div class="cards" id="psFieldCards"></div>
      </section>
      <section id="sec-ps-trend">
        <div class="sec-head"><h2>Score trend</h2></div>
        <div class="ps-legend">
          <span class="ps-legend-item"><span class="ps-dot" style="background:#1d6fd0"></span>Performance</span>
          <span class="ps-legend-item"><span class="ps-dot" style="background:#0c9d61"></span>Accessibility</span>
          <span class="ps-legend-item"><span class="ps-dot" style="background:#e8a13a"></span>Best Practices</span>
          <span class="ps-legend-item"><span class="ps-dot" style="background:#7c3aed"></span>SEO</span>
        </div>
        <div class="chart-wrap" id="psTrendWrap">
          <div class="chart-canvas-host" style="height:220px"><canvas id="psTrendChart"></canvas></div>
        </div>
      </section>
    </div><!-- /pane-site_performance -->
    """


def pane_css() -> str:
    """Scoped styles for the Site Performance tab (injected into the page CSS)."""
    return """
    .ps-controls { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
    .ps-toggle { display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
    .ps-toggle[hidden] { display:none; }
    .ps-toggle-btn { appearance:none; border:0; background:#fff; color:var(--muted); font:inherit; font-size:.82rem; font-weight:600; padding:6px 14px; cursor:pointer; }
    .ps-toggle-btn + .ps-toggle-btn { border-left:1px solid var(--line); }
    .ps-toggle-btn.active { background:var(--navy, #0a2540); color:#fff; }
    .ps-edit-btn, .ps-btn-plain { appearance:none; border:1px solid var(--line); background:#fff; color:var(--text); font:inherit; font-size:.82rem; font-weight:600; padding:6px 12px; border-radius:8px; cursor:pointer; }
    .ps-edit-btn:hover, .ps-btn-plain:hover { background:var(--row-alt, #f4f7fb); }
    .ps-target { margin-top:6px; font-size:.72rem; color:var(--muted); display:flex; align-items:center; gap:6px; }
    .ps-light { width:9px; height:9px; border-radius:50%; display:inline-block; flex-shrink:0; }
    .ps-light--green { background:#0c9d61; }
    .ps-light--yellow { background:#e8a13a; }
    .ps-light--red { background:#e5484d; }
    .ps-editor { margin-top:14px; padding:14px 16px; border:1px solid var(--line); border-radius:10px; background:var(--row-alt, #f8fafc); }
    .ps-editor-head { font-size:.78rem; color:var(--muted); margin-bottom:10px; }
    .ps-editor-fields { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px 20px; }
    .ps-field { display:flex; align-items:center; gap:8px; font-size:.85rem; font-weight:600; }
    .ps-field > span:first-child { flex:1; }
    .ps-field input { width:56px; padding:5px 7px; border:1px solid var(--line); border-radius:6px; font:inherit; font-size:.85rem; }
    .ps-editor-actions { display:flex; align-items:center; gap:10px; margin-top:14px; }
    .ps-legend { display:flex; flex-wrap:wrap; gap:16px; margin:0 0 12px; font-size:.8rem; color:var(--muted); }
    .ps-legend-item { display:inline-flex; align-items:center; gap:6px; }
    .ps-dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    /* ---- "Building history" trend states (≤1 reading) ---- */
    /* Overlay floats a status card over the anchored-reading chart (or the
       shimmer skeleton) without blocking the chart's own tooltips. */
    #psTrendWrap { position:relative; }
    .ps-trend-overlay { position:absolute; inset:10px 12px; display:flex; align-items:center; justify-content:center; pointer-events:none; z-index:2; }
    /* One-reading state: park the card along the bottom so it sits under the
       plotted scores (which cluster high) instead of covering them — matters
       most on narrow/mobile widths where a centered card hides the whole chart. */
    .ps-trend-overlay--bottom { align-items:flex-end; }
    .ps-trend-overlay--skel { border-radius:10px; overflow:hidden; background:linear-gradient(90deg,#eef2f7 25%,#e4eaf2 50%,#eef2f7 75%); background-size:200% 100%; animation:shimmer 1.6s ease-in-out infinite; }
    .ps-trend-badge { pointer-events:auto; display:flex; align-items:center; gap:12px; max-width:86%; padding:12px 16px; background:rgba(255,255,255,.9); backdrop-filter:blur(5px); -webkit-backdrop-filter:blur(5px); border:1px solid var(--line); border-radius:12px; box-shadow:0 10px 30px -14px rgba(16,33,67,.5); }
    .ps-trend-copy { display:flex; flex-direction:column; gap:2px; }
    .ps-trend-copy strong { font-size:.9rem; color:var(--navy, #0a2540); }
    .ps-trend-copy span { font-size:.76rem; color:var(--muted); }
    .ps-trend-pulse { flex-shrink:0; width:11px; height:11px; border-radius:50%; background:var(--accent, #1d6fd0); box-shadow:0 0 0 0 rgba(29,111,208,.5); animation:psTrendPulse 1.9s cubic-bezier(.4,0,.2,1) infinite; }
    @keyframes psTrendPulse { 0% { box-shadow:0 0 0 0 rgba(29,111,208,.45); } 70% { box-shadow:0 0 0 11px rgba(29,111,208,0); } 100% { box-shadow:0 0 0 0 rgba(29,111,208,0); } }
    /* ---- Core Web Vitals sparklines + health ---- */
    #psCwv .card, #psFieldCards .card { display:flex; flex-direction:column; }
    .ps-spark { margin-top:10px; height:26px; }
    .ps-spark svg { display:block; width:100%; height:100%; }
    .ps-spark--empty { position:relative; }
    .ps-spark--empty::after { content:""; position:absolute; left:0; right:0; top:50%; border-top:1.5px dashed var(--line); }
    /* The goal now lives inside the sparkline as a dashed benchmark line, so
       the card's hover tooltip carries the numbers (goal + full bands). */
    .ps-spark { cursor:help; }
    /* ---- Real-user (CrUX) field data ---- */
    .ps-field-intro { font-size:.82rem; color:var(--muted); line-height:1.55; margin:-4px 0 14px; max-width:78ch; }
    .ps-field-intro strong { color:var(--text); font-weight:700; }
    /* Pass/fail strip: Google's own Core Web Vitals assessment (LCP + INP + CLS
       all at or under "good" at the 75th percentile), stated in one line rather
       than left for someone to work out from three cards. */
    .ps-assess { display:flex; align-items:center; gap:12px; flex-wrap:wrap; padding:12px 16px; border:1px solid var(--line); border-left-width:4px; border-radius:10px; margin-bottom:14px; background:var(--row-alt, #f8fafc); }
    .ps-assess--pass { border-left-color:#0c9d61; }
    .ps-assess--fail { border-left-color:#e5484d; }
    .ps-assess--none { border-left-color:var(--line); }
    .ps-assess-verdict { font-size:.92rem; font-weight:700; color:var(--navy, #0a2540); }
    .ps-assess--pass .ps-assess-verdict { color:#0a7a4c; }
    .ps-assess--fail .ps-assess-verdict { color:#c3363b; }
    .ps-assess-detail { font-size:.79rem; color:var(--muted); }
    /* Distribution bar: the good / needs-improvement / poor split of real visits.
       The p75 headline says where the slow tail sits; this says how many people
       are in it. */
    .ps-dist { display:flex; height:6px; border-radius:3px; overflow:hidden; margin-top:10px; background:var(--line); }
    .ps-dist span { display:block; height:100%; }
    .ps-dist-good { background:#0c9d61; }
    .ps-dist-ni { background:#e8a13a; }
    .ps-dist-poor { background:#e5484d; }
    .ps-dist-legend { display:flex; justify-content:space-between; margin-top:5px; font-size:.7rem; color:var(--muted); }
    .ps-field-empty { padding:16px; border:1px dashed var(--line); border-radius:10px; font-size:.84rem; color:var(--muted); line-height:1.55; }
    /* ---- Per-card "what does this mean?" tooltips ---- */
    #psScores .card-title, #psCwv .card-title, #psFieldCards .card-title, #ovPsScores .card-title { display:flex; align-items:center; gap:5px; }
    .ps-info { appearance:none; flex-shrink:0; width:14px; height:14px; padding:0; display:inline-flex; align-items:center;
      justify-content:center; border:1px solid var(--line); border-radius:50%; background:#fff; color:var(--muted);
      font:inherit; font-size:.6rem; font-weight:800; line-height:1; cursor:help; transition:color .12s, border-color .12s; }
    .ps-info:hover, .ps-info:focus-visible { color:var(--accent, #1d6fd0); border-color:#b9c8dc; outline:none; }
    /* ---- Audited-URL chip on the first score card + modern tooltip ---- */
    #psScores .card:first-child { position:relative; }
    /* #psScores prefix beats the generic .ps-tip{position:relative} below so the
       chip stays pinned to the card corner (it carries both classes). */
    #psScores .ps-url-chip { position:absolute; top:10px; right:10px; width:26px; height:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--line); border-radius:7px; background:#fff; color:var(--muted); cursor:pointer; transition:color .12s, border-color .12s, background .12s; }
    .ps-url-chip:hover, .ps-url-chip:focus-visible { color:var(--accent, #1d6fd0); border-color:#b9c8dc; background:var(--row-alt, #f4f7fb); outline:none; }
    .ps-url-chip svg { width:15px; height:15px; }
    /* Modern tooltip: dark rounded bubble with an arrow, revealed on hover or
       keyboard focus; centered under the trigger so it clears the card edges. */
    .ps-tip { position:relative; }
    .ps-tip::after { content:attr(data-tip); position:absolute; top:calc(100% + 9px); left:50%; z-index:70;
      background:#0b1020; color:#e8eefc; font-size:.74rem; font-weight:600; line-height:1.4; letter-spacing:.01em;
      padding:8px 11px; border-radius:9px; width:max-content; max-width:230px; text-align:center; word-break:break-word;
      box-shadow:0 12px 32px -10px rgba(11,16,32,.55); opacity:0; transform:translate(-50%, -4px); pointer-events:none;
      transition:opacity .16s ease, transform .16s ease; }
    .ps-tip::before { content:""; position:absolute; top:calc(100% + 4px); left:50%; z-index:71;
      border:5px solid transparent; border-bottom-color:#0b1020; opacity:0; transform:translateX(-50%); transition:opacity .16s ease; }
    .ps-tip:hover::after, .ps-tip:focus-visible::after { opacity:1; transform:translate(-50%, 0); }
    .ps-tip:hover::before, .ps-tip:focus-visible::before { opacity:1; }
    /* Plain-language card tooltips run longer than the URL chip's and read
       better left-aligned than centered. */
    .ps-tip.ps-tip--wide::after { max-width:270px; text-align:left; font-weight:500; }
    /* Edge cards: a 270px bubble centered on a card in the first or last column
       would hang off the page, so psAnchorTips() flips it to hug the trigger.
       The arrow stays centered on the "?" either way. */
    .ps-tip.ps-tip--start::after { left:-8px; transform:translate(0, -4px); }
    .ps-tip.ps-tip--start:hover::after, .ps-tip.ps-tip--start:focus-visible::after { transform:translate(0, 0); }
    .ps-tip.ps-tip--end::after { left:auto; right:-8px; transform:translate(0, -4px); }
    .ps-tip.ps-tip--end:hover::after, .ps-tip.ps-tip--end:focus-visible::after { transform:translate(0, 0); }
    """


def pane_js() -> str:
    """loadSitePerformance() + toggle/editor/render helpers. Emitted in the page <script>."""
    return """
    // ---- Site Performance (PageSpeed Insights) ----
    // Strategies actually synced (server-driven); toggle renders exactly these.
    const PS_STRATEGIES = (typeof PAGESPEED_STRATEGIES !== 'undefined' && PAGESPEED_STRATEGIES.length)
      ? PAGESPEED_STRATEGIES : ['desktop'];
    let psStrategy = PS_STRATEGIES[0];   // active device
    let psLast = {};                     // last snapshot, so target edits can re-color live

    // Lighthouse-standard fallback color when a KPI has no target band.
    function psScoreColor(v) {
      if (v == null) return 'var(--muted)';
      if (v >= 90) return '#0c9d61';
      if (v >= 50) return '#e8a13a';
      return '#e5484d';
    }
    // Traffic light vs a target band: green at/above the low end, amber within
    // 10 points below it, red beyond that.
    function psTrafficColor(v, band) {
      if (v == null || !band) return { color: psScoreColor(v), light: null };
      if (v >= band.min) return { color: '#0c9d61', light: 'green' };
      if (v >= band.min - 10) return { color: '#e8a13a', light: 'yellow' };
      return { color: '#e5484d', light: 'red' };
    }
    function psMs(v) {
      if (v == null) return '—';
      return v >= 1000 ? (v / 1000).toFixed(1) + ' s' : Math.round(v) + ' ms';
    }
    // Plain-language explanations, shown from the little "?" on each card. Written
    // for a client reading the page, not for whoever tuned the Lighthouse config.
    const PS_SCORE_TIPS = {
      performance: 'How fast the page feels to a real visitor — how quickly it loads and starts responding. 100 is the fastest.',
      accessibility: 'How usable the page is for people on a screen reader, keyboard, or with low vision — readable text, labeled buttons, enough contrast.',
      best_practices: 'Whether the site follows modern web standards — secure connections, no broken images, nothing outdated or unsafe.',
      seo: 'Whether search engines can read and index this page properly — page title, description, headings, links they can follow.',
    };
    const PS_CWV_TIPS = {
      lcp_ms: 'How long until the biggest thing on screen — usually the main image or headline — has finished loading. This is what "the page loaded" feels like to a visitor.',
      cls: 'How much the page jumps around while it loads. High numbers mean buttons move under someone as they go to tap them.',
      tbt_ms: 'How long the page is busy running code and cannot respond to clicks or taps. Lower means it reacts right away.',
      fcp_ms: 'How long before a visitor sees anything at all instead of a blank screen.',
      speed_index_ms: 'How quickly the page fills in visually, from blank to finished. Rewards showing something useful early.',
      tti_ms: 'How long before a visitor can actually use everything on the page — click, type, scroll — without it feeling stuck.',
    };
    function psInfo(tip) {
      if (!tip) return '';
      const safe = esc(tip);
      return `<button type="button" class="ps-info ps-tip ps-tip--wide" data-tip="${safe}" aria-label="${safe}">?</button>`;
    }
    function psScoreCard(label, v, band, tip) {
      const shown = v == null ? '—' : v;
      const suffix = v == null ? '' : '<span style="font-size:.55em;color:var(--muted);font-weight:600"> /100</span>';
      const tc = psTrafficColor(v, band);
      const dot = tc.light ? `<span class="ps-light ps-light--${tc.light}"></span>` : '';
      const target = band ? `<div class="ps-target">${dot}Target ${band.min}–${band.max}</div>` : '';
      return `<div class="card"><div class="card-title">${esc(label)}${psInfo(tip)}</div>` +
             `<div class="card-value" style="color:${tc.color}">${shown}${suffix}</div>${target}</div>`;
    }
    // Core Web Vitals lab thresholds (Lighthouse buckets, ms unless noted).
    // value ≤ good → green, ≤ ni → amber, else red. Lower is always better.
    const CWV_THRESHOLDS = {
      lcp_ms:         { good: 2500, ni: 4000, ms: true },
      cls:            { good: 0.1,  ni: 0.25, ms: false },
      tbt_ms:         { good: 200,  ni: 600,  ms: true },
      fcp_ms:         { good: 1800, ni: 3000, ms: true },
      speed_index_ms: { good: 3400, ni: 5800, ms: true },
      tti_ms:         { good: 3800, ni: 7300, ms: true },
    };
    function psCwvFmt(v, ms) { return ms ? psMs(v) : (Math.round(v * 1000) / 1000); }
    // Classify a metric's current value into a good/needs-improvement/poor band,
    // returning the band color (shared by the value text + sparkline), a status
    // label, the "good" goal, and a full-range string for the hover tooltip.
    function psCwvHealth(key, v) {
      const t = CWV_THRESHOLDS[key];
      if (!t) return {};
      const g = psCwvFmt(t.good, t.ms), n = psCwvFmt(t.ni, t.ms);
      const rangeTitle = `Goal: ${g} or less  ·  Needs improvement: up to ${n}  ·  Poor: over ${n}`
        + `\nThe dotted line on the chart is the ${g} goal; the trend line is green when this page is inside it.`;
      if (v == null || !isFinite(Number(v))) {
        return { targetText: g, statusLabel: 'Target', color: null, lightClass: null, rangeTitle };
      }
      v = Number(v);
      if (v <= t.good) return { color: '#0c9d61', lightClass: 'green',  statusLabel: 'Good', targetText: g, rangeTitle };
      if (v <= t.ni)   return { color: '#e8a13a', lightClass: 'yellow', statusLabel: 'Needs improvement', targetText: g, rangeTitle };
      return                 { color: '#e5484d', lightClass: 'red',    statusLabel: 'Poor', targetText: g, rangeTitle };
    }
    // Tiny inline-SVG sparkline for a Core Web Vitals card. `values` is the
    // metric's history oldest→newest (nulls tolerated); `color` (from the card's
    // current health band) tints the line so it reads good/bad at a glance,
    // falling back to trend direction. Faint baseline placeholder until ≥2 reads.
    function psSparkline(values, color, goal, title) {
      const pts = (values || []).map(v => (v == null ? null : Number(v)));
      const nums = pts.filter(v => v != null && isFinite(v));
      const tip = title ? ` title="${esc(title)}"` : '';
      if (nums.length < 2) return `<div class="ps-spark ps-spark--empty"${tip}></div>`;
      const W = 120, H = 26, pad = 3, n = pts.length;
      // The goal joins the y-domain so its dotted line always lands on-chart,
      // even when every reading sits well above or below it.
      const g = (goal == null || !isFinite(Number(goal))) ? null : Number(goal);
      const domain = g == null ? nums : nums.concat([g]);
      const min = Math.min(...domain), max = Math.max(...domain), span = (max - min) || 1;
      const x = i => pad + (n === 1 ? (W - pad * 2) / 2 : (i / (n - 1)) * (W - pad * 2));
      const y = v => H - pad - ((v - min) / span) * (H - pad * 2);
      let d = '', firstX = null, lastX = 0, lastY = 0, started = false;
      pts.forEach((v, i) => {
        if (v == null || !isFinite(v)) return;
        const px = x(i), py = y(v);
        d += (started ? 'L' : 'M') + px.toFixed(1) + ' ' + py.toFixed(1) + ' ';
        if (!started) firstX = px;
        started = true; lastX = px; lastY = py;
      });
      const first = nums[0], last = nums[nums.length - 1];
      color = color || (last < first ? '#0c9d61' : (last > first ? '#e5484d' : '#8595ab'));
      const area = `${d}L${lastX.toFixed(1)} ${H} L${firstX.toFixed(1)} ${H} Z`;
      // Dotted benchmark, drawn under the trend so the colored line stays legible.
      const goalLine = g == null ? '' :
        `<line x1="0" y1="${y(g).toFixed(1)}" x2="${W}" y2="${y(g).toFixed(1)}" stroke="#8595ab" stroke-width="1" ` +
        `stroke-dasharray="2 3" stroke-linecap="round" vector-effect="non-scaling-stroke" opacity="0.75"/>`;
      return `<div class="ps-spark"${tip}><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">` +
        goalLine +
        `<path d="${area}" fill="${color}" opacity="0.09"/>` +
        `<path d="${d.trim()}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>` +
        `<circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="1.9" fill="${color}"/></svg></div>`;
    }
    // Small globe affordance pinned to the first score card. The audited URL
    // lives in its modern tooltip (data-tip) rather than cluttering the header.
    function psUrlChip(url) {
      const safe = esc(url);
      return `<button type="button" class="ps-url-chip ps-tip" data-tip="${safe}" aria-label="Audited page: ${safe}">` +
        `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
        `<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.5 3.8 5.7 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.7-3.8-9S9.5 5.5 12 3Z"/></svg></button>`;
    }
    function renderPagespeed(p) {
      p = p || {};
      psLast = p;
      const scores = [
        ['Performance', 'performance', p.performance],
        ['Accessibility', 'accessibility', p.accessibility],
        ['Best Practices', 'best_practices', p.best_practices],
        ['SEO', 'seo', p.seo],
      ];
      const psScoresEl = document.getElementById('psScores');
      psScoresEl.innerHTML = scores.map(([l, k, v]) => psScoreCard(l, v, PAGESPEED_TARGETS[k], PS_SCORE_TIPS[k])).join('');
      const firstScoreCard = psScoresEl.querySelector('.card');
      if (firstScoreCard && p.url) firstScoreCard.insertAdjacentHTML('beforeend', psUrlChip(p.url));
      const hist = p.history || [];
      const clsVal = p.cls == null ? '—' : (Math.round(p.cls * 1000) / 1000);
      // [label, history key, formatted current value]. Every lab metric is
      // "lower is better", so the sparkline colors a downward trend green.
      const cwv = [
        ['Largest Contentful Paint', 'lcp_ms', psMs(p.lcp_ms)],
        ['Cumulative Layout Shift', 'cls', clsVal],
        ['Total Blocking Time', 'tbt_ms', psMs(p.tbt_ms)],
        ['First Contentful Paint', 'fcp_ms', psMs(p.fcp_ms)],
        ['Speed Index', 'speed_index_ms', psMs(p.speed_index_ms)],
        ['Time to Interactive', 'tti_ms', psMs(p.tti_ms)],
      ];
      document.getElementById('psCwv').innerHTML = cwv.map(([l, key, v]) => {
        const h = psCwvHealth(key, p[key]);
        const valStyle = h.color ? ` style="color:${h.color}"` : '';
        const goal = (CWV_THRESHOLDS[key] || {}).good;
        return `<div class="card"><div class="card-title">${esc(l)}${psInfo(PS_CWV_TIPS[key])}</div>` +
          `<div class="card-value"${valStyle}>${v}</div>` +
          psSparkline(hist.map(r => r[key]), h.color, goal, h.rangeTitle) + `</div>`;
      }).join('');
      psRenderCrux(p.crux_history);
      const hasCurrent = ['performance', 'accessibility', 'best_practices', 'seo'].some(k => p[k] != null);
      if (hist.length > 1) {
        psClearTrendState();
        const labels = hist.map(r => String(r.metric_date || '').slice(5));
        lineChart('psTrendChart', labels, [
          { label: 'Performance', data: hist.map(r => r.performance), color: '#1d6fd0', fmt: v => v },
          { label: 'Accessibility', data: hist.map(r => r.accessibility), color: '#0c9d61', fmt: v => v },
          { label: 'Best Practices', data: hist.map(r => r.best_practices), color: '#e8a13a', fmt: v => v },
          { label: 'SEO', data: hist.map(r => r.seo), color: '#7c3aed', fmt: v => v },
        ], { points: true, yDisplay: true, beginAtZero: true });
      } else if (hasCurrent) {
        psRenderTrendBuilding(p);   // one reading: anchor it + project "incoming"
      } else {
        psRenderTrendSkeleton();    // no reading yet: shimmer placeholder
      }
    }
    // ---- Real visitors (Chrome UX Report field data) ----
    // [label, column prefix, thresholds, is a Core Web Vital]. The three CWVs
    // decide the pass/fail assessment; FCP and TTFB are supporting context.
    // Thresholds are Google's own, and mirror crux_service.THRESHOLDS server-side.
    const PS_CRUX_METRICS = [
      ['Largest Contentful Paint', 'lcp',  { good: 2500, ni: 4000, ms: true },  true],
      ['Interaction to Next Paint', 'inp', { good: 200,  ni: 500,  ms: true },  true],
      ['Cumulative Layout Shift', 'cls',   { good: 0.1,  ni: 0.25, ms: false }, true],
      ['First Contentful Paint', 'fcp',    { good: 1800, ni: 3000, ms: true },  false],
      ['Time to First Byte', 'ttfb',       { good: 800,  ni: 1800, ms: true },  false],
    ];
    const PS_CRUX_TIPS = {
      lcp: 'How long real visitors waited for the main content to appear. Google counts this as a search ranking signal.',
      inp: 'How quickly the page responded when real visitors tapped or clicked. Replaced First Input Delay as a ranking signal.',
      cls: 'How much the page shifted under real visitors while it loaded. Counts for ranking.',
      fcp: 'How long real visitors stared at a blank screen before anything appeared.',
      ttfb: 'How long the server took to send the first byte. High numbers here make every other metric worse.',
    };
    // A metric "passes" when its 75th percentile is inside the good band —
    // i.e. at least 75% of real visits were good. Same rule Google applies.
    function psCruxBand(v, t) {
      if (v == null || !isFinite(Number(v))) return { color: null, label: 'No data' };
      v = Number(v);
      if (v <= t.good) return { color: '#0c9d61', label: 'Good', pass: true };
      if (v <= t.ni)   return { color: '#e8a13a', label: 'Needs improvement', pass: false };
      return                 { color: '#e5484d', label: 'Poor', pass: false };
    }
    function psCruxFmt(v, ms) {
      if (v == null || !isFinite(Number(v))) return '—';
      return ms ? psMs(Number(v)) : (Math.round(Number(v) * 1000) / 1000);
    }
    function psPct(v) {
      return (v == null || !isFinite(Number(v))) ? null : Math.round(Number(v) * 100);
    }
    // good/needs-improvement/poor split of real visits, as a stacked bar. The
    // headline p75 says how slow the tail is; this says how many people are in it.
    function psDistBar(good, ni, poor) {
      const g = psPct(good), n = psPct(ni), p = psPct(poor);
      if (g == null && n == null && p == null) return '';
      const w = x => (x == null ? 0 : x);
      return `<div class="ps-dist" role="img" aria-label="${w(g)}% good, ${w(n)}% needs improvement, ${w(p)}% poor">` +
        `<span class="ps-dist-good" style="width:${w(g)}%"></span>` +
        `<span class="ps-dist-ni" style="width:${w(n)}%"></span>` +
        `<span class="ps-dist-poor" style="width:${w(p)}%"></span></div>` +
        `<div class="ps-dist-legend"><span>${w(g)}% good</span><span>${w(p)}% poor</span></div>`;
    }
    function psRenderCrux(hist) {
      const cards = document.getElementById('psFieldCards');
      const assess = document.getElementById('psAssessment');
      if (!cards || !assess) return;
      hist = hist || [];
      if (!hist.length) {
        assess.innerHTML = '';
        cards.innerHTML =
          '<div class="ps-field-empty">No real-user data for this site yet. Google only reports Chrome UX ' +
          'Report data once an origin has enough Chrome traffic, so smaller sites can sit below the ' +
          'threshold. The lab scores above still work.</div>';
        setStatus('psFieldStatus', '');
        return;
      }
      const latest = hist[hist.length - 1];
      // Pass/fail on the three Core Web Vitals, Google's own definition: every
      // one of LCP, INP and CLS must be inside its good band at p75.
      const cwv = PS_CRUX_METRICS.filter(m => m[3]);
      const bands = cwv.map(([, key, t]) => psCruxBand(latest[key + '_p75'], t));
      const measured = bands.filter(b => b.pass !== undefined);
      const failing = cwv.filter((m, i) => bands[i].pass === false).map(m => m[0]);
      let cls = 'ps-assess--none', verdict = 'Not enough data to assess', detail = '';
      if (measured.length === cwv.length) {
        if (!failing.length) {
          cls = 'ps-assess--pass';
          verdict = 'Passing Core Web Vitals';
          detail = 'All three of Google\\'s ranking metrics are in the good range for real visitors.';
        } else {
          cls = 'ps-assess--fail';
          verdict = 'Not passing Core Web Vitals';
          detail = 'Failing on ' + failing.join(' and ') + '. Google needs all three in the good range.';
        }
      } else if (measured.length) {
        detail = 'Google reports only some metrics for this site — usually a traffic threshold.';
      }
      const period = latest.period_end
        ? `28 days to ${esc(String(latest.period_end))}` : '';
      assess.innerHTML = `<div class="ps-assess ${cls}">` +
        `<span class="ps-assess-verdict">${esc(verdict)}</span>` +
        `<span class="ps-assess-detail">${esc(detail)}</span></div>`;
      cards.innerHTML = PS_CRUX_METRICS.map(([label, key, t]) => {
        const v = latest[key + '_p75'];
        const band = psCruxBand(v, t);
        const valStyle = band.color ? ` style="color:${band.color}"` : '';
        const series = hist.map(r => r[key + '_p75']);
        const goalTip = `Goal: ${psCruxFmt(t.good, t.ms)} or less  ·  Needs improvement: up to ` +
          `${psCruxFmt(t.ni, t.ms)}  ·  Poor: over ${psCruxFmt(t.ni, t.ms)}` +
          `\\nDotted line is the goal. 75th percentile — 3 in 4 visits were at least this fast.`;
        return `<div class="card"><div class="card-title">${esc(label)}${psInfo(PS_CRUX_TIPS[key])}</div>` +
          `<div class="card-value"${valStyle}>${psCruxFmt(v, t.ms)}</div>` +
          `<div class="ps-target">${band.color ? `<span class="ps-light" style="background:${band.color}"></span>` : ''}${esc(band.label)}</div>` +
          psSparkline(series, band.color, t.good, goalTip) +
          psDistBar(latest[key + '_good'], latest[key + '_ni'], latest[key + '_poor']) +
          `</div>`;
      }).join('');
      setStatus('psFieldStatus', period ? `${hist.length} weeks · ${period}` : `${hist.length} weeks`);
    }

    // ---- "Scores over time" empty states ----
    // A single PSI reading (or none) can't draw a trend, so instead of a blank
    // canvas we show where the trend is heading: the reading anchored on the
    // left, dashed "holds" fading into a shaded incoming band, and a status card
    // naming the first-snapshot date.
    const PS_TREND_KPIS = [
      ['Performance', 'performance', '#1d6fd0'],
      ['Accessibility', 'accessibility', '#0c9d61'],
      ['Best Practices', 'best_practices', '#e8a13a'],
      ['SEO', 'seo', '#7c3aed'],
    ];
    function psAddDays(iso, days) {
      const d = iso ? new Date(iso + 'T00:00:00') : new Date();
      const base = isNaN(d.getTime()) ? new Date() : d;
      base.setDate(base.getDate() + days);
      return base;
    }
    function psFmtMD(d) {
      return String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }
    function psClearTrendState() {
      const wrap = document.getElementById('psTrendWrap');
      if (wrap) wrap.querySelectorAll('.ps-trend-overlay').forEach(el => el.remove());
    }
    function psTrendOverlay(html, extraClass) {
      const wrap = document.getElementById('psTrendWrap');
      if (!wrap) return;
      psClearTrendState();
      const ov = document.createElement('div');
      ov.className = 'ps-trend-overlay' + (extraClass ? ' ' + extraClass : '');
      ov.innerHTML = html;
      wrap.appendChild(ov);
    }
    function psRenderTrendSkeleton() {
      __destroyChart('psTrendChart');
      psTrendOverlay(
        '<div class="ps-trend-badge"><span class="ps-trend-pulse"></span>' +
        '<div class="ps-trend-copy"><strong>Awaiting your first snapshot</strong>' +
        '<span>Run a PageSpeed sync from Connectors — scores plot here once data lands.</span></div></div>',
        'ps-trend-overlay--skel'
      );
    }
    function psRenderTrendBuilding(p) {
      const anchorISO = p.metric_date || new Date().toISOString().slice(0, 10);
      const STEP = 7, FUTURE = 4;                 // project a month of weekly slots
      const labels = [psFmtMD(psAddDays(anchorISO, 0))];
      for (let i = 1; i <= FUTURE; i++) labels.push(psFmtMD(psAddDays(anchorISO, i * STEP)));
      const datasets = PS_TREND_KPIS.map(([label, key, color]) => {
        const v = p[key];
        return {
          label, borderColor: color + 'B3', borderWidth: 2, borderDash: [5, 5],
          tension: 0, fill: false, spanGaps: true,
          data: labels.map(() => (v == null ? null : v)),
          pointRadius: ctx => (ctx.dataIndex === 0 && v != null ? 4 : 0),
          pointHoverRadius: ctx => (ctx.dataIndex === 0 && v != null ? 5 : 0),
          pointBackgroundColor: color, pointBorderColor: '#fff', pointBorderWidth: 1.5,
        };
      });
      // Inline plugin: wash the future region and drop a soft "now" divider so
      // the dashed holds read as pending rather than a flat forecast.
      const incomingBand = {
        id: 'psIncomingBand',
        beforeDatasetsDraw(chart) {
          const a = chart.chartArea, x = chart.scales.x, ctx = chart.ctx;
          if (!a) return;
          const x0 = x.getPixelForValue(0);
          ctx.save();
          const g = ctx.createLinearGradient(x0, 0, a.right, 0);
          g.addColorStop(0, 'rgba(29,111,208,0.07)');
          g.addColorStop(1, 'rgba(29,111,208,0.015)');
          ctx.fillStyle = g;
          ctx.fillRect(x0, a.top, a.right - x0, a.bottom - a.top);
          ctx.beginPath();
          ctx.moveTo(x0, a.top); ctx.lineTo(x0, a.bottom);
          ctx.setLineDash([3, 3]); ctx.lineWidth = 1; ctx.strokeStyle = 'rgba(16,33,67,0.2)';
          ctx.stroke();
          ctx.restore();
        },
      };
      __chart('psTrendChart', {
        type: 'line',
        data: { labels, datasets },
        options: {
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: false } },
            y: { beginAtZero: true, max: 100, grid: { color: '#f1f4f9' }, border: { display: false }, ticks: { maxTicksLimit: 4 } },
          },
          plugins: {
            legend: { display: false },
            tooltip: { filter: item => item.dataIndex === 0, callbacks: { label: c => `${c.dataset.label}: ${c.raw}` } },
          },
        },
        plugins: [incomingBand],
      });
      psTrendOverlay(
        '<div class="ps-trend-badge"><span class="ps-trend-pulse"></span>' +
        '<div class="ps-trend-copy"><strong>Building your performance history</strong>' +
        `<span>First snapshot ${esc(anchorISO)} · the trend fills in as new syncs land.</span></div></div>`,
        'ps-trend-overlay--bottom'   // sit under the plotted reading, not over it
      );
    }
    let sitePerfLoaded = false;
    async function loadSitePerformance(strategy) {
      strategy = strategy || psStrategy;
      setStatus('psStatus', 'Loading…');
      document.getElementById('psScores').innerHTML = skelCards(4);
      document.getElementById('psCwv').innerHTML = skelCards(6);
      document.getElementById('psFieldCards').innerHTML = skelCards(5);
      const url = PAGESPEED_API + (PAGESPEED_API.includes('?') ? '&' : '?') + 'strategy=' + strategy;
      try {
        const p = await getJson(url);
        if (!p || !p.url) {
          renderPagespeed({});
          setStatus('psStatus', `No ${strategy} PageSpeed data yet — run a sync from Connectors.`);
          return;
        }
        renderPagespeed(p);
        const when = p.metric_date ? (' · measured ' + p.metric_date) : '';
        setStatus('psStatus', `${esc(strategy)}${when}`);
      } catch (err) {
        setStatus('psStatus', err.message || String(err), true);
      }
    }
    // Desktop/mobile toggle — built from the synced strategies. Hidden entirely
    // when only one is synced (e.g. desktop-only), so there's no dead half.
    (function buildPsToggle() {
      const host = document.getElementById('psToggle');
      if (!host) return;
      if (PS_STRATEGIES.length <= 1) { host.hidden = true; return; }
      const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
      host.innerHTML = PS_STRATEGIES.map((s, i) =>
        `<button type="button" class="ps-toggle-btn${i === 0 ? ' active' : ''}" data-strategy="${s}">${cap(s)}</button>`
      ).join('');
      host.querySelectorAll('.ps-toggle-btn').forEach(btn =>
        btn.addEventListener('click', () => {
          if (btn.dataset.strategy === psStrategy) return;
          psStrategy = btn.dataset.strategy;
          host.querySelectorAll('.ps-toggle-btn').forEach(b => b.classList.toggle('active', b === btn));
          loadSitePerformance(psStrategy);
        })
      );
    })();
    // Keep the wide card tooltips on-screen: before one shows, pick left/center/
    // right anchoring from where its "?" sits. Delegated, so it survives the
    // re-renders that rebuild the cards.
    const PS_TIP_HALF = 143;   // half the 270px bubble + a little breathing room
    function psAnchorTip(btn) {
      const r = btn.getBoundingClientRect(), mid = r.left + r.width / 2;
      btn.classList.toggle('ps-tip--start', mid - PS_TIP_HALF < 8);
      btn.classList.toggle('ps-tip--end', mid + PS_TIP_HALF > window.innerWidth - 8);
    }
    ['pointerover', 'focusin'].forEach(evt => document.addEventListener(evt, e => {
      const btn = e.target.closest && e.target.closest('.ps-tip--wide');
      if (btn) psAnchorTip(btn);
    }, true));

    // Per-KPI target editor.
    const PS_KPI_LABELS = { performance: 'Performance', accessibility: 'Accessibility', best_practices: 'Best Practices', seo: 'SEO' };
    function psRenderEditorFields() {
      const host = document.getElementById('psEditorFields');
      if (!host) return;
      host.innerHTML = Object.keys(PS_KPI_LABELS).map(k => {
        const b = PAGESPEED_TARGETS[k] || { min: 0, max: 0 };
        return `<div class="ps-field"><span>${esc(PS_KPI_LABELS[k])}</span>` +
          `<input type="number" min="0" max="100" data-kpi="${k}" data-bound="min" value="${b.min}">` +
          `<span>–</span>` +
          `<input type="number" min="0" max="100" data-kpi="${k}" data-bound="max" value="${b.max}"></div>`;
      }).join('');
    }
    document.getElementById('psEditTargets')?.addEventListener('click', () => {
      const ed = document.getElementById('psEditor');
      if (!ed) return;
      if (ed.hidden) psRenderEditorFields();
      ed.hidden = !ed.hidden;
    });
    document.getElementById('psTargetsCancel')?.addEventListener('click', () => {
      const ed = document.getElementById('psEditor'); if (ed) ed.hidden = true;
    });
    document.getElementById('psTargetsSave')?.addEventListener('click', async () => {
      const t = {};
      document.querySelectorAll('#psEditorFields input').forEach(inp => {
        const k = inp.dataset.kpi, b = inp.dataset.bound;
        (t[k] = t[k] || {})[b] = Math.max(0, Math.min(100, parseInt(inp.value, 10) || 0));
      });
      setStatus('psTargetsStatus', 'Saving…');
      try {
        const r = await fetch(PAGESPEED_TARGETS_API, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin', body: JSON.stringify({ targets: t }),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok || !body.ok) throw new Error((body && body.detail && (body.detail.error || body.detail)) || r.statusText);
        Object.keys(PAGESPEED_TARGETS).forEach(k => delete PAGESPEED_TARGETS[k]);
        Object.assign(PAGESPEED_TARGETS, body.targets || t);
        setStatus('psTargetsStatus', 'Saved.');
        const ed = document.getElementById('psEditor'); if (ed) ed.hidden = true;
        renderPagespeed(psLast);   // re-color cards with the new bands
      } catch (err) {
        setStatus('psTargetsStatus', 'Save failed: ' + (err.message || err), true);
      }
    });
    """
