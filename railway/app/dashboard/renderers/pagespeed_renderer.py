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
        <div class="sec-head"><h2>Core Web Vitals (lab)</h2></div>
        <div class="cards" id="psCwv"></div>
      </section>
      <section id="sec-ps-trend">
        <div class="sec-head"><h2>Scores over time</h2></div>
        <div class="ps-legend">
          <span class="ps-legend-item"><span class="ps-dot" style="background:#1d6fd0"></span>Performance</span>
          <span class="ps-legend-item"><span class="ps-dot" style="background:#0c9d61"></span>Accessibility</span>
          <span class="ps-legend-item"><span class="ps-dot" style="background:#e8a13a"></span>Best Practices</span>
          <span class="ps-legend-item"><span class="ps-dot" style="background:#7c3aed"></span>SEO</span>
        </div>
        <div class="chart-wrap">
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
    function psScoreCard(label, v, band) {
      const shown = v == null ? '—' : v;
      const suffix = v == null ? '' : '<span style="font-size:.55em;color:var(--muted);font-weight:600"> /100</span>';
      const tc = psTrafficColor(v, band);
      const dot = tc.light ? `<span class="ps-light ps-light--${tc.light}"></span>` : '';
      const target = band ? `<div class="ps-target">${dot}Target ${band.min}–${band.max}</div>` : '';
      return `<div class="card"><div class="card-title">${esc(label)}</div>` +
             `<div class="card-value" style="color:${tc.color}">${shown}${suffix}</div>${target}</div>`;
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
      document.getElementById('psScores').innerHTML =
        scores.map(([l, k, v]) => psScoreCard(l, v, PAGESPEED_TARGETS[k])).join('');
      const cls = p.cls == null ? '—' : (Math.round(p.cls * 1000) / 1000);
      const cwv = [
        ['Largest Contentful Paint', psMs(p.lcp_ms)],
        ['Cumulative Layout Shift', cls],
        ['Total Blocking Time', psMs(p.tbt_ms)],
        ['First Contentful Paint', psMs(p.fcp_ms)],
        ['Speed Index', psMs(p.speed_index_ms)],
        ['Time to Interactive', psMs(p.tti_ms)],
      ];
      document.getElementById('psCwv').innerHTML = cwv.map(([l, v]) =>
        `<div class="card"><div class="card-title">${esc(l)}</div><div class="card-value">${v}</div></div>`).join('');
      const hist = p.history || [];
      if (hist.length > 1) {
        const labels = hist.map(r => String(r.metric_date || '').slice(5));
        lineChart('psTrendChart', labels, [
          { label: 'Performance', data: hist.map(r => r.performance), color: '#1d6fd0', fmt: v => v },
          { label: 'Accessibility', data: hist.map(r => r.accessibility), color: '#0c9d61', fmt: v => v },
          { label: 'Best Practices', data: hist.map(r => r.best_practices), color: '#e8a13a', fmt: v => v },
          { label: 'SEO', data: hist.map(r => r.seo), color: '#7c3aed', fmt: v => v },
        ], { points: true, yDisplay: true, beginAtZero: true });
      } else {
        __destroyChart('psTrendChart');
      }
    }
    let sitePerfLoaded = false;
    async function loadSitePerformance(strategy) {
      strategy = strategy || psStrategy;
      setStatus('psStatus', 'Loading…');
      document.getElementById('psScores').innerHTML = skelCards(4);
      document.getElementById('psCwv').innerHTML = skelCards(6);
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
        setStatus('psStatus', `${esc(strategy)} · ${esc(p.url)}${when}`);
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
