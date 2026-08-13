"""Admin "Benchmarks" page: agency averages, cut by client industry.

Rendered inside the shared admin shell (navy sidebar + client switcher) so it
sits alongside Health and Hours. It fetches /admin/benchmarks/data for the
numbers, so the cross-client rollup never blocks first paint.

One metric is on screen at a time — picked from the registry the service ships
in the payload — and the table is industries × distribution (clients, median,
mean, range). Expanding an industry lists its accounts with each one's own value
and its gap to that industry's median, which is the number anyone actually acts
on ("Nixon's CTR is 0.6pt under its peers").

Two honesty affordances are load-bearing rather than decorative: every row shows
the ``n`` behind it and flags thin samples, and the coverage note names how many
accounts are still untagged — an industry benchmark computed from two clients
should not read like an authority.
"""

from __future__ import annotations

from dashboard.renderers.base_layout import render_admin_shell_page

_BENCH_CSS = """
    :root {
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#0a7f3f; --amber:#b7791f; --danger:#b42318;
    }
    body { color:var(--ink);
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; }
    main { max-width:1180px; width:100%; min-width:0; margin:0 auto; padding:26px 24px 56px; }
    section { background:#fff; border:1px solid var(--line); border-radius:16px;
      padding:22px 24px; margin-bottom:18px; box-shadow:0 6px 22px rgba(10,37,64,.06); }
    .page-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:4px; }
    .page-head h2 { margin:0; font-size:1.15rem; color:var(--navy); }
    .sub { color:var(--muted); font-size:.86rem; margin:2px 0 0; }
    /* Filter bar: window + platform + metric. */
    .filters { display:flex; align-items:center; gap:18px; flex-wrap:wrap; margin:14px 0 4px; }
    .fgroup { display:flex; align-items:center; gap:9px; }
    .flabel { color:var(--muted); font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; }
    .seg { display:inline-flex; background:#eef2f7; border:1px solid var(--border); border-radius:9px; padding:2px; }
    .seg button { appearance:none; border:0; background:transparent; cursor:pointer; color:var(--muted);
      font:inherit; font-size:.8rem; font-weight:700; padding:5px 11px; border-radius:7px;
      transition:background .12s,color .12s; white-space:nowrap; }
    .seg button:hover { color:var(--navy); }
    .seg button.active { background:#fff; color:var(--navy); box-shadow:0 1px 3px rgba(10,37,64,.14); }
    select.fsel { font:inherit; font-size:.84rem; color:var(--navy); background:#fff; cursor:pointer;
      border:1px solid var(--border); border-radius:9px; padding:6px 9px; }
    select.fsel:focus { outline:2px solid rgba(37,99,235,.35); outline-offset:1px; }
    .metric-hint { color:var(--muted); font-size:.8rem; margin:10px 0 0; }
    /* Table */
    .table-wrap { overflow-x:auto; -webkit-overflow-scrolling:touch; margin-top:12px; }
    table { width:100%; border-collapse:collapse; font-size:.9rem; min-width:760px; }
    th, td { text-align:right; padding:11px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
    th:first-child, td:first-child { text-align:left; }
    th { color:var(--muted); font-weight:700; font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; }
    tbody tr.ind-row { cursor:pointer; }
    tbody tr.ind-row:hover { background:#f6f9fd; }
    .ind-name { font-weight:700; color:var(--navy); display:flex; align-items:center; gap:8px; }
    .caret { color:var(--muted); display:inline-flex; transition:transform .18s ease; }
    .caret svg { width:15px; height:15px; }
    tr.open .caret { transform:rotate(180deg); }
    .num { font-variant-numeric:tabular-nums; }
    .median-cell { display:flex; align-items:center; justify-content:flex-end; gap:10px; }
    .median-cell .num { font-weight:800; color:var(--navy); }
    .mbar { flex:0 0 92px; height:7px; border-radius:4px; background:#eef2f7; overflow:hidden; }
    .mbar > span { display:block; height:100%; border-radius:4px; background:var(--accent); }
    .nchip { display:inline-flex; align-items:center; justify-content:center; min-width:22px; padding:1px 7px;
      border-radius:999px; background:#eef2f7; color:var(--muted); font-size:.72rem; font-weight:800;
      font-variant-numeric:tabular-nums; }
    .nchip.thin { background:rgba(183,121,31,.14); color:var(--amber); }
    .muted { color:var(--muted); }
    /* Agency row, pinned above the industries as the comparison baseline. */
    tr.agency-row td { background:#f6f9fd; font-weight:700; }
    tr.agency-row .ind-name { color:var(--accent-d); }
    /* Expanded member list */
    tr.members td { padding:0; background:#fbfcfe; }
    .mem { display:flex; align-items:center; justify-content:space-between; gap:14px;
      padding:9px 14px 9px 34px; border-bottom:1px solid #f0f3f8; }
    .mem:last-child { border-bottom:0; }
    .mem-name { color:var(--navy); font-weight:600; text-decoration:none; font-size:.86rem;
      overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .mem-name:hover { text-decoration:underline; }
    .mem-right { display:flex; align-items:center; gap:12px; flex:0 0 auto; }
    .mem-val { font-variant-numeric:tabular-nums; font-weight:700; }
    .delta { font-size:.76rem; font-weight:800; font-variant-numeric:tabular-nums; }
    .delta.good { color:var(--green); }
    .delta.bad { color:var(--danger); }
    .delta.flat { color:var(--muted); }
    .status-note { color:var(--muted); font-size:.82rem; margin:14px 0 0; }
    .status-note a { color:var(--accent); text-decoration:none; }
    .status-note a:hover { text-decoration:underline; }
    .warn-note { color:var(--amber); }
    .empty { text-align:center; color:var(--muted); padding:26px 8px; }
    @media (max-width: 720px) {
      main { padding:16px 13px 44px; }
      section { padding:16px 15px; border-radius:14px; margin-bottom:14px; }
      .page-head h2 { font-size:1.05rem; }
      th, td { padding:9px 10px; }
      .mbar { flex-basis:56px; }
    }"""


_BENCH_CONTENT = """
  <main>
    <section>
      <div class="page-head">
        <div>
          <h2>Agency averages by industry</h2>
          <p class="sub" id="bmSub">Loading…</p>
        </div>
      </div>
      <div class="filters">
        <div class="fgroup">
          <span class="flabel">Window</span>
          <div class="seg" id="bmWindow" role="group" aria-label="Benchmark window">
            <button type="button" data-window="month" class="active">This month</button>
            <button type="button" data-window="last_30d">Last 30 days</button>
          </div>
        </div>
        <div class="fgroup">
          <label class="flabel" for="bmMetric">Metric</label>
          <select class="fsel" id="bmMetric" aria-label="Benchmark metric"></select>
        </div>
        <div class="fgroup">
          <label class="flabel" for="bmPlatform">Platform</label>
          <select class="fsel" id="bmPlatform" aria-label="Paid media platform"></select>
        </div>
      </div>
      <p class="metric-hint" id="bmHint"></p>
      <div class="table-wrap">
        <table id="bmTable">
          <thead><tr>
            <th>Industry</th>
            <th>Clients</th>
            <th>Median</th>
            <th>Average</th>
            <th>Range</th>
          </tr></thead>
          <tbody id="bmBody"></tbody>
        </table>
      </div>
      <p class="status-note" id="bmCoverage"></p>
    </section>
  </main>"""


def render_agency_benchmarks_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/benchmarks. Data loads from …/data."""
    body_end = """<script>
    const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const money = v => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(Number(v||0));
    const money2 = v => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(v||0));
    const num = v => new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Number(v||0));
    const CARET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

    let bmData = null;
    let win = 'month';          // month | last_30d
    let platform = 'all';
    let metricKey = 'ctr';
    const openRows = new Set();

    function metricDef(key) {
      return (bmData && bmData.metric_catalog || []).find(m => m.key === key) || null;
    }
    // Each metric carries its own format, so one column renders currency,
    // percentages, and counts without the table knowing which is which.
    function fmt(v, format) {
      if (v == null) return '—';
      v = Number(v);
      if (format === 'percent') return v.toFixed(2) + '%';
      if (format === 'currency2') return money2(v);
      if (format === 'currency') return money(v);
      return num(v);
    }
    // A client's gap to its industry median, signed and coloured by whether the
    // metric is one where up is good (CTR) or down is good (CPC). Size metrics
    // (direction 'none') get a neutral delta — bigger spend is not "better".
    function deltaHtml(value, base, def) {
      if (value == null || base == null || !base) return '';
      const diff = value - base;
      const pct = (diff / Math.abs(base)) * 100;
      if (Math.abs(pct) < 0.5) return '<span class="delta flat">on par</span>';
      const dir = def && def.direction;
      let cls = 'flat';
      if (dir === 'up') cls = diff > 0 ? 'good' : 'bad';
      else if (dir === 'down') cls = diff < 0 ? 'good' : 'bad';
      const sign = diff > 0 ? '+' : '';
      return `<span class="delta ${cls}">${sign}${pct.toFixed(0)}% vs peers</span>`;
    }
    function nChip(stat) {
      if (!stat) return '<span class="nchip">0</span>';
      const cls = stat.thin ? ' thin' : '';
      const tip = stat.thin ? `Only ${stat.n} account${stat.n===1?'':'s'} — directional, not a benchmark` : `${stat.n} accounts with data`;
      return `<span class="nchip${cls}" title="${esc(tip)}">${stat.n}</span>`;
    }
    function medianCell(stat, def, maxMedian) {
      if (!stat) return '<span class="muted">—</span>';
      const w = maxMedian > 0 ? Math.max(3, Math.min(100, (stat.median / maxMedian) * 100)) : 0;
      return `<div class="median-cell"><span class="num">${fmt(stat.median, def.format)}</span>`
        + `<span class="mbar"><span style="width:${w}%"></span></span></div>`;
    }

    function memberRows(bucket, def) {
      const base = bucket.metrics[metricKey] ? bucket.metrics[metricKey].median : null;
      const slugs = new Set(bucket.clients || []);
      const rows = (bmData.clients || []).filter(c => slugs.has(c.client_slug));
      if (!rows.length) return '<div class="mem"><span class="muted">No accounts.</span></div>';
      // Clients with a value first (biggest first), then the ones with no data.
      rows.sort((a, b) => {
        const x = a.metrics[metricKey], y = b.metrics[metricKey];
        if (x == null && y == null) return a.label.localeCompare(b.label);
        if (x == null) return 1;
        if (y == null) return -1;
        return y - x;
      });
      return rows.map(c => {
        const v = c.metrics[metricKey];
        const valTxt = v == null ? '<span class="muted">no data</span>' : fmt(v, def.format);
        return `<div class="mem">`
          + `<a class="mem-name" href="/dashboard/${encodeURIComponent(c.client_slug)}">${esc(c.label)}</a>`
          + `<span class="mem-right"><span class="mem-val">${valTxt}</span>${deltaHtml(v, base, def)}</span></div>`;
      }).join('');
    }

    function render() {
      const body = document.getElementById('bmBody');
      const def = metricDef(metricKey);
      if (!bmData || !def) { body.innerHTML = ''; return; }

      document.getElementById('bmHint').textContent = def.hint || '';
      const w = bmData.window || {};
      const p = bmData.platform || {};
      const scope = def.platform_scoped ? ` · ${p.label || 'All paid media'}` : '';
      document.getElementById('bmSub').textContent =
        `${def.label} — ${w.label || ''} (${(w.start||'').slice(5)} → ${(w.end||'').slice(5)})${scope}`;

      const buckets = (bmData.industries || []).filter(b => b.metrics[metricKey]);
      buckets.sort((a, b) => b.metrics[metricKey].median - a.metrics[metricKey].median);
      const agency = bmData.agency;
      const agencyStat = agency && agency.metrics[metricKey];
      const maxMedian = Math.max(0, ...buckets.map(b => b.metrics[metricKey].median),
                                 agencyStat ? agencyStat.median : 0);

      const rowHtml = (bucket, cls) => {
        const stat = bucket.metrics[metricKey];
        const open = openRows.has(bucket.key);
        const range = stat ? `${fmt(stat.min, def.format)} – ${fmt(stat.max, def.format)}` : '—';
        let html = `<tr class="ind-row ${cls}${open ? ' open' : ''}" data-key="${esc(bucket.key)}">`
          + `<td><span class="ind-name"><span class="caret">${CARET}</span>${esc(bucket.label)}</span></td>`
          + `<td>${nChip(stat)}</td>`
          + `<td>${medianCell(stat, def, maxMedian)}</td>`
          + `<td class="num">${stat ? fmt(stat.mean, def.format) : '—'}</td>`
          + `<td class="num muted">${range}</td></tr>`;
        if (open) {
          html += `<tr class="members"><td colspan="5">${memberRows(bucket, def)}</td></tr>`;
        }
        return html;
      };

      let html = agencyStat ? rowHtml(agency, 'agency-row') : '';
      html += buckets.map(b => rowHtml(b, '')).join('');
      if (!html) {
        html = '<tr><td colspan="5" class="empty">No account has data for this metric in this window.</td></tr>';
      }
      body.innerHTML = html;

      body.querySelectorAll('tr.ind-row').forEach(tr => {
        tr.addEventListener('click', () => {
          const key = tr.dataset.key;
          if (openRows.has(key)) openRows.delete(key); else openRows.add(key);
          render();
        });
      });

      const cov = bmData.coverage || {};
      const covEl = document.getElementById('bmCoverage');
      if (cov.untagged) {
        covEl.className = 'status-note warn-note';
        covEl.innerHTML = `${cov.tagged} of ${cov.total} accounts are tagged with an industry. `
          + `<a href="/admin/clients">Tag the remaining ${cov.untagged}</a> to fill out these buckets — `
          + `untagged accounts still count toward "All clients".`;
      } else {
        covEl.className = 'status-note';
        covEl.textContent = `All ${cov.total || 0} accounts are tagged. Medians are shown as the benchmark; `
          + `averages sit alongside so a skewed bucket is visible.`;
      }
    }

    function fillSelect(el, options, selected) {
      el.innerHTML = options.map(o =>
        `<option value="${esc(o.key)}"${o.key === selected ? ' selected' : ''}>${esc(o.label)}</option>`
      ).join('');
    }

    function syncControls() {
      const metrics = bmData.metric_catalog || [];
      if (!metrics.some(m => m.key === metricKey) && metrics.length) metricKey = metrics[0].key;
      fillSelect(document.getElementById('bmMetric'), metrics, metricKey);
      const opts = (bmData.platform && bmData.platform.options) || [{key:'all', label:'All paid media'}];
      if (!opts.some(o => o.key === platform)) platform = 'all';
      fillSelect(document.getElementById('bmPlatform'), opts, platform);
      // The platform filter only means something for paid metrics.
      const def = metricDef(metricKey);
      document.getElementById('bmPlatform').disabled = !(def && def.platform_scoped);
    }

    async function load() {
      document.getElementById('bmSub').textContent = 'Loading…';
      const qs = new URLSearchParams({ window: win, platform: platform });
      try {
        const r = await fetch('/admin/benchmarks/data?' + qs.toString(), { credentials:'same-origin' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        bmData = await r.json();
      } catch (e) {
        document.getElementById('bmSub').textContent = 'Could not load benchmarks.';
        return;
      }
      syncControls();
      render();
    }

    document.getElementById('bmWindow').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-window]');
      if (!btn) return;
      win = btn.dataset.window;
      document.querySelectorAll('#bmWindow button').forEach(b => b.classList.toggle('active', b === btn));
      load();
    });
    document.getElementById('bmMetric').addEventListener('change', (e) => {
      metricKey = e.target.value;
      syncControls();
      render();
    });
    // Platform re-scopes the paid aggregation server-side, so it refetches.
    document.getElementById('bmPlatform').addEventListener('change', (e) => {
      platform = e.target.value;
      load();
    });
    load();
    </script>"""

    return render_admin_shell_page(
        active_nav="benchmarks",
        page_title="Admin · Benchmarks",
        content_html=_BENCH_CONTENT,
        session_email=user_email,
        session_is_admin=True,
        extra_css=_BENCH_CSS,
        body_end_html=body_end,
    )
