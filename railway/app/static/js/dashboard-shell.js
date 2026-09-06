
    // ---- Panel edit mode: hide / show / reorder (admin only) ----
    // Same editor on Overview and Campaign Explorer. Admins enter edit mode for
    // a pane from that tab's sidebar kebab, then hide a panel (it greys out but
    // stays, so it can be shown back), show a hidden one, or drag panels to
    // reorder. Every change persists the full {order, hidden} for that tab to
    // its card-layout endpoint; the server applies it on the next render (and
    // omits hidden panels from clients' HTML entirely). Optimistic: the DOM is
    // already correct after the edit, so we don't reload on success.
    //
    // The one exception is the Explorer's Budget tracking panel: its visibility
    // has its own per-client setting (the same one the settings page's "Show on
    // Explorer" toggle writes), so its Hide/Show posts there instead and the
    // layout's hidden set stays quiet about it.
    (function () {
      const shell = document.getElementById('appShell');
      if (!shell || !shell.classList.contains('is-admin')) return;

      // One editor per editable pane, keyed by its tab so the sidebar kebab can
      // find it. A pane with no banner isn't editable (nothing to persist to).
      const editors = {};

      function makeEditor(pane) {
        const tab = pane.getAttribute('data-edit-pane') || '';
        const banner = pane.querySelector('.ov-editing-banner');
        if (!tab || !banner) return null;
        const LAYOUT_API = banner.getAttribute('data-ov-layout-api') || '';
        const statusEl = banner.querySelector('.ov-edit-status');
        const doneBtn = banner.querySelector('.ov-edit-done');

        function setStatus(msg, isError) {
          if (!statusEl) return;
          statusEl.textContent = msg || '';
          statusEl.classList.toggle('is-error', !!isError);
        }

        function setEditing(on) {
          pane.classList.toggle('is-editing', !!on);
          if (!on) setStatus('');
          else {
            // Bring the pane into view when entering from the sidebar.
            try { pane.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) {}
          }
        }

        // Exit via the banner's Done button.
        if (doneBtn) doneBtn.addEventListener('click', function () { setEditing(false); });

        // Collect the current layout from the live DOM: order = every panel in
        // document order; hidden = the ones flagged with the hidden class. The
        // budget panel is left out of `hidden` on purpose — see the note above.
        function currentLayout() {
          const units = Array.prototype.slice.call(pane.querySelectorAll('.ov-unit'));
          const order = [], hidden = [];
          units.forEach(function (u) {
            const key = u.getAttribute('data-ov-card');
            if (!key) return;
            order.push(key);
            if (key !== 'budget' && u.classList.contains('ov-unit--hidden')) hidden.push(key);
          });
          return { order: order, hidden: hidden };
        }

        let saveSeq = 0;
        function persist() {
          const payload = currentLayout();
          const seq = ++saveSeq;
          setStatus('Saving…', false);
          fetch(LAYOUT_API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
          }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (b) {
              if (!r.ok || !b.ok) throw new Error((b && b.detail && (b.detail.error || b.detail)) || r.statusText);
              if (seq === saveSeq) setStatus('Saved', false);
            });
          }).catch(function (err) {
            if (seq === saveSeq) setStatus('Could not save: ' + (err.message || err), true);
          });
        }

        // The budget panel's visibility is a portal-wide client setting, not part
        // of the layout: post it to its own endpoint and reflect the result in
        // the same status line.
        function persistBudget(nowHidden, unit, btn) {
          setStatus('Saving…', false);
          fetch(BUDGET_VISIBILITY_API, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'show=' + (nowHidden ? '0' : '1'),
          }).then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (b) {
              if (b && b.ok) { setStatus('Saved', false); return; }
              throw new Error((b && b.error) || 'unknown error');
            }).catch(function (err) {
              // Put the panel back the way it was so the page keeps telling the truth.
              unit.classList.toggle('ov-unit--hidden', !nowHidden);
              btn.setAttribute('aria-pressed', nowHidden ? 'false' : 'true');
              btn.textContent = nowHidden ? 'Hide' : 'Show';
              setStatus('Could not save: ' + (err.message || err), true);
            });
        }

        // Hide / show a panel.
        pane.addEventListener('click', function (ev) {
          const btn = ev.target.closest && ev.target.closest('.ov-hide-toggle');
          if (!btn || !pane.contains(btn)) return;
          ev.preventDefault();
          const unit = btn.closest('.ov-unit');
          if (!unit) return;
          const nowHidden = unit.classList.toggle('ov-unit--hidden');
          btn.setAttribute('aria-pressed', nowHidden ? 'true' : 'false');
          btn.textContent = nowHidden ? 'Show' : 'Hide';
          if (unit.getAttribute('data-ov-card') === 'budget') persistBudget(nowHidden, unit, btn);
          else persist();
        });

        // Drag to reorder. The handle is the drag source; we move its parent unit.
        let dragUnit = null;
        pane.addEventListener('dragstart', function (ev) {
          const handle = ev.target.closest && ev.target.closest('.ov-drag');
          if (!handle || !pane.classList.contains('is-editing')) return;
          dragUnit = handle.closest('.ov-unit');
          if (!dragUnit) return;
          dragUnit.classList.add('is-dragging');
          if (ev.dataTransfer) {
            ev.dataTransfer.effectAllowed = 'move';
            try { ev.dataTransfer.setData('text/plain', dragUnit.getAttribute('data-ov-card') || ''); } catch (e) {}
          }
        });
        pane.addEventListener('dragover', function (ev) {
          if (!dragUnit) return;
          const over = ev.target.closest && ev.target.closest('.ov-unit');
          if (!over || over === dragUnit || !pane.contains(over)) return;
          ev.preventDefault();
          if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
          const rect = over.getBoundingClientRect();
          const after = (ev.clientY - rect.top) > rect.height / 2;
          pane.querySelectorAll('.ov-unit.is-drag-over').forEach(function (u) { u.classList.remove('is-drag-over'); });
          over.classList.add('is-drag-over');
          if (after) over.parentNode.insertBefore(dragUnit, over.nextSibling);
          else over.parentNode.insertBefore(dragUnit, over);
        });
        function endDrag(persistIt) {
          pane.querySelectorAll('.ov-unit.is-drag-over').forEach(function (u) { u.classList.remove('is-drag-over'); });
          if (dragUnit) {
            dragUnit.classList.remove('is-dragging');
            dragUnit = null;
            if (persistIt) persist();
          }
        }
        pane.addEventListener('drop', function (ev) { if (dragUnit) { ev.preventDefault(); endDrag(true); } });
        pane.addEventListener('dragend', function () { endDrag(true); });

        return { tab: tab, pane: pane, setEditing: setEditing };
      }

      document.querySelectorAll('.ov-editable').forEach(function (pane) {
        const ed = makeEditor(pane);
        if (ed) editors[ed.tab] = ed;
      });
      if (!Object.keys(editors).length) return;

      // Only one pane edits at a time — entering edit mode on one leaves the other.
      function editOnly(tab) {
        Object.keys(editors).forEach(function (k) { editors[k].setEditing(k === tab); });
      }
      function exitAll() {
        Object.keys(editors).forEach(function (k) { editors[k].setEditing(false); });
      }
      function anyEditing() {
        return Object.keys(editors).some(function (k) {
          return editors[k].pane.classList.contains('is-editing');
        });
      }

      // ---- Entry point: the sidebar kebab (⋮) on an editable nav item ----
      // A small popover menu whose "Edit layout" item switches to that tab and
      // drops into edit mode. Menu open/close is handled here so the sidebar
      // renderer stays presentational.
      function closeMenus() {
        document.querySelectorAll('.dash-view-item.menu-open').forEach(function (it) {
          it.classList.remove('menu-open');
          const k = it.querySelector('.dash-view-kebab');
          const m = it.querySelector('.dash-view-menu');
          if (k) k.setAttribute('aria-expanded', 'false');
          if (m) m.hidden = true;
        });
      }
      document.addEventListener('click', function (ev) {
        const kebab = ev.target.closest && ev.target.closest('.dash-view-kebab');
        if (kebab) {
          ev.preventDefault();
          ev.stopPropagation();
          const item = kebab.closest('.dash-view-item');
          const menu = item && item.querySelector('.dash-view-menu');
          const willOpen = !(item && item.classList.contains('menu-open'));
          closeMenus();
          if (willOpen && item) {
            item.classList.add('menu-open');
            kebab.setAttribute('aria-expanded', 'true');
            if (menu) menu.hidden = false;
          }
          return;
        }
        const action = ev.target.closest && ev.target.closest('.dash-view-menu-item');
        if (action) {
          ev.preventDefault();
          ev.stopPropagation();
          closeMenus();
          if (action.getAttribute('data-action') === 'edit-layout') {
            const item = action.closest('.dash-view-item');
            const tab = item && item.getAttribute('data-view-item');
            if (!tab || !editors[tab]) return;
            // Make sure that tab is the active one, then enter edit mode.
            const navBtn = document.querySelector('.dash-view-btn[data-tab="' + tab + '"]');
            if (navBtn && !navBtn.classList.contains('active')) navBtn.click();
            editOnly(tab);
          }
          return;
        }
        // A click anywhere else dismisses any open kebab menu.
        closeMenus();
      });
      document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape') {
          if (anyEditing()) exitAll();
          closeMenus();
        }
      });
    })();

    // ---- Formatters ----
    const dollars = new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:2 });
    const nums    = new Intl.NumberFormat('en-US');
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    // Shorten long URL paths for display; the full value stays in the cell
    // tooltip. Middle ellipsis keeps the leading segment and the distinguishing
    // tail both visible (paths often share a long common prefix).
    const PATH_MAX = 24;
    function truncPath(path, max) {
      max = max || PATH_MAX;
      const s = String(path == null ? '' : path);
      if (s.length <= max) return s;
      const keep = max - 1;
      const head = Math.ceil(keep / 2);
      const tail = keep - head;
      return s.slice(0, head) + '\u2026' + s.slice(s.length - tail);
    }
    // Drag-to-resize table columns. Widths persist per table across re-renders
    // and pagination. On first render we freeze the auto-computed widths, then
    // switch to a fixed layout so columns can be widened *or* narrowed by
    // dragging their right edge.
    const colWidths = {};
    function enableColResize(tableId) {
      const table = document.getElementById(tableId);
      if (!table) return;
      const ths = [...table.querySelectorAll('thead th')];
      if (!ths.length) return;
      const store = colWidths[tableId] || (colWidths[tableId] = {});
      // Safe to call again on the same thead (a tab switch re-runs it) — drop any
      // grips from a previous pass instead of stacking a second set.
      ths.forEach(th => th.querySelectorAll(':scope > .col-resizer').forEach(g => g.remove()));
      table.style.tableLayout = 'auto';
      // A table rendered inside a hidden tab has no layout to freeze — every
      // column would measure 0 and collapse. Leave it auto; showPanelTab calls
      // back once the pane is on screen.
      if (!table.getBoundingClientRect().width) return;
      ths.forEach((th, idx) => { if (store[idx] == null) store[idx] = Math.round(th.getBoundingClientRect().width); });
      ths.forEach((th, idx) => { th.style.width = store[idx] + 'px'; });
      table.style.tableLayout = 'fixed';
      ths.forEach((th, idx) => {
        const grip = document.createElement('span');
        grip.className = 'col-resizer';
        grip.title = 'Drag to resize';
        // Keep the grip from triggering the header's (delegated) sort handler.
        grip.addEventListener('click', e => e.stopPropagation());
        grip.addEventListener('mousedown', e => {
          e.preventDefault();
          e.stopPropagation();
          const startX = e.pageX;
          const startW = th.getBoundingClientRect().width;
          document.body.classList.add('col-resizing');
          const onMove = ev => {
            const w = Math.max(48, Math.round(startW + (ev.pageX - startX)));
            th.style.width = w + 'px';
            store[idx] = w;
          };
          const onUp = () => {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.body.classList.remove('col-resizing');
          };
          document.addEventListener('mousemove', onMove);
          document.addEventListener('mouseup', onUp);
        });
        th.appendChild(grip);
      });
    }
    const num  = v => Number(v || 0);
    const money  = v => dollars.format(num(v));
    const count  = v => nums.format(Math.round(num(v)));
    const pct    = v => `${num(v).toFixed(2)}%`;
    // Card-sized numbers. A headline value is read at a glance, so it gets three
    // significant figures and a magnitude suffix ($1.10K, 4.14K, $219); the exact
    // figure is never lost -- every card that abbreviates carries the full number
    // in its title attribute.
    const sigFix = x => { const a=Math.abs(x); return x.toFixed(a<10 ? 2 : (a<100 ? 1 : 0)); };
    const compactNum = v => {
      const n=num(v), a=Math.abs(n);
      if (a>=1e9) return sigFix(n/1e9)+'B';
      if (a>=1e6) return sigFix(n/1e6)+'M';
      if (a>=1e3) return sigFix(n/1e3)+'K';
      return null;
    };
    // Cents only where they carry information: $2.35 for a CPC, $219 for a CPA.
    const moneyCompact = v => {
      const c=compactNum(v);
      if (c!=null) return '$'+c;
      const n=num(v);
      return '$'+(Math.abs(n)<10 ? n.toFixed(2) : Math.round(n).toLocaleString('en-US'));
    };
    const countCompact = v => { const c=compactNum(v); return c!=null ? c : nums.format(Math.round(num(v))); };
    function fmtDuration(secs) {
      secs = Math.round(num(secs));
      if (secs < 60) return secs + 's';
      const m = Math.floor(secs / 60), s = secs % 60;
      return m + 'm ' + (s < 10 ? '0' : '') + s + 's';
    }

    // ---- Chart.js foundation (lib vendored under /static/vendor) ----
    // Shared theme + small factories keep every chart consistent. Instances are
    // tracked per canvas id and destroyed before re-creation (Chart.js will not
    // reuse a canvas that still has a live chart on it).
    const __charts = {};
    function __chart(id, config) {
      const el = document.getElementById(id);
      if (!el || !window.Chart) return null;
      if (__charts[id]) __charts[id].destroy();
      __charts[id] = new Chart(el.getContext('2d'), config);
      return __charts[id];
    }
    function __destroyChart(id) { if (__charts[id]) { __charts[id].destroy(); delete __charts[id]; } }
    if (window.Chart) {
      Chart.defaults.font.family = 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
      Chart.defaults.font.size = 11;
      Chart.defaults.color = '#6b7a90';
      Chart.defaults.maintainAspectRatio = false;
      Chart.defaults.animation.duration = 300;
      Chart.defaults.plugins.legend.display = false;
      const _tt = Chart.defaults.plugins.tooltip;
      _tt.backgroundColor = '#0b1020'; _tt.titleColor = '#e8eefc'; _tt.bodyColor = '#e8eefc';
      _tt.padding = 9; _tt.cornerRadius = 8; _tt.boxPadding = 4; _tt.usePointStyle = true;
    }
    // Vertical gradient fill under an area line, built from the plot geometry.
    function __areaFill(context, color) {
      const c = context.chart.ctx, a = context.chart.chartArea;
      if (!a) return color + '00';
      const g = c.createLinearGradient(0, a.top, 0, a.bottom);
      g.addColorStop(0, color + '33'); g.addColorStop(1, color + '00');
      return g;
    }
    // Line chart. series: [{label, data, color, fill?, dashed?, raw?, fmt?}].
    // `data` is what's plotted (may be normalized); `raw`/`fmt` drive tooltips.
    function lineChart(id, labels, series, opts) {
      opts = opts || {};
      const datasets = series.map(s => ({
        label: s.label, data: s.data, borderColor: s.color,
        backgroundColor: s.fill ? (ctx => __areaFill(ctx, s.color)) : 'transparent',
        fill: !!s.fill, borderWidth: 2.25, tension: 0.35,
        borderDash: s.dashed ? [5, 4] : [],
        pointRadius: opts.points ? 2.5 : 0, pointHoverRadius: 4, pointBackgroundColor: s.color,
        // A series can ride its own axis (`axisId`, declared in opts.extraScales)
        // when one chart carries metrics of different magnitudes -- the paid
        // trends multi-select does this rather than flattening the smaller line.
        yAxisID: s.axisId || 'y',
        _raw: s.raw || s.data, _fmt: s.fmt || count,
      }));
      const chart = __chart(id, {
        type: 'line',
        data: { labels, datasets },
        options: {
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: opts.xTicks || 6 } },
            y: { display: opts.yDisplay !== false, reverse: !!opts.yReverse, beginAtZero: opts.beginAtZero !== false,
                 grid: { color: '#f1f4f9' }, border: { display: false },
                 ticks: { maxTicksLimit: 4, callback: opts.yFmt || (v => v) } },
            ...(opts.extraScales || {}),
          },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: opts.tooltip || {
              label: c => `${c.dataset.label}: ${c.dataset._fmt(c.dataset._raw[c.dataIndex])}`,
            } },
          },
        },
        // `dates` is the ISO date behind each label. Charts that pass it get
        // timeline annotations drawn over them; charts that don't are untouched.
        // `annoScope` says which family of events belongs here ('ads' for the
        // paid trend chart, 'analytics' — the default — for the GA4 ones).
        plugins: opts.dates ? [annoLinePlugin(opts.dates, opts.annoScope)] : [],
      });
      if (opts.dates) syncAnnoPins(id, opts.dates, opts.annoScope);
      return chart;
    }

    // Bar chart, same option surface as lineChart (yFmt / tooltip / dates), for
    // series where each point is its own reading rather than a flow -- a daily
    // average reads as a row of bars, not a slope. A series tagged
    // kind:'line' rides the same axes, which is how a comparison period is
    // overlaid without doubling the bars.
    function barChart(id, labels, series, opts) {
      opts = opts || {};
      const datasets = series.map(s => s.kind === 'line'
        ? ({
            type: 'line', label: s.label, data: s.data, borderColor: s.color,
            backgroundColor: 'transparent', fill: false, borderWidth: 2,
            borderDash: s.dashed ? [5, 4] : [], tension: 0.35,
            pointRadius: 0, pointHoverRadius: 4, pointBackgroundColor: s.color,
            order: 0, _raw: s.raw || s.data, _fmt: s.fmt || count,
          })
        : ({
            label: s.label, data: s.data, backgroundColor: s.color,
            hoverBackgroundColor: s.hoverColor || s.color,
            borderRadius: 3, borderSkipped: false,
            maxBarThickness: opts.maxBarThickness || 22,
            order: 1, _raw: s.raw || s.data, _fmt: s.fmt || count,
          }));
      const chart = __chart(id, {
        type: 'bar',
        data: { labels, datasets },
        options: {
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: opts.xTicks || 6 } },
            y: { display: opts.yDisplay !== false, beginAtZero: opts.beginAtZero !== false,
                 grid: { color: '#f1f4f9' }, border: { display: false },
                 ticks: { maxTicksLimit: 4, callback: opts.yFmt || (v => v) } },
          },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: opts.tooltip || {
              label: c => `${c.dataset.label}: ${c.dataset._fmt(c.dataset._raw[c.dataIndex])}`,
            } },
          },
        },
        plugins: opts.dates ? [annoLinePlugin(opts.dates, opts.annoScope)] : [],
      });
      if (opts.dates) syncAnnoPins(id, opts.dates, opts.annoScope);
      return chart;
    }

    // ---- Timeline annotations ----
    // A chart can show that traffic fell; only an annotation can say the site was
    // migrated that week. Markers are drawn in two halves for a reason: the
    // vertical rule belongs on the canvas (it has to sit inside the plot area and
    // survive resizes), while the flag at the top is a real DOM element so it
    // gets native hover text and keyboard focus. Nothing here runs for a
    // client-role user beyond drawing the shared markers they are allowed to see.
    // Adding/editing events happens on the Settings → Insights page, not here.
    let annoCache = [];
    let annoCanEdit = false;
    let annoCategories = [];
    let annoLoaded = false;

    // The index of the plotted point an annotation belongs to: the last date at
    // or before it. Weekly-aggregated charts label each bucket with its Monday,
    // so a Thursday event correctly lands on that week's point.
    function annoIndexFor(dates, iso) {
      let found = -1;
      for (let i = 0; i < dates.length; i++) {
        if (String(dates[i]) <= iso) found = i; else break;
      }
      // An annotation before the first plotted day still marks the chart's start
      // when its range overlaps the window; one after the last is off-chart.
      if (found < 0) return (annoOverlapsStart(iso, dates)) ? 0 : -1;
      return found;
    }
    function annoOverlapsStart(iso, dates) { return dates.length > 0 && iso < String(dates[0]); }

    // Whether an event belongs on this chart. Each event carries the family of
    // charts it explains ("ads", "analytics", or "both" — set per event under
    // Settings → Insights); anything stored before that field existed reads as
    // "both", which is how it used to behave.
    function annoInScope(a, scope) {
      const on = String(a.charts || 'both');
      return on === 'both' || on === scope;
    }
    // Annotations that fall inside (or overlap) the dates a chart is showing,
    // and that belong on this family of charts.
    function annoVisibleFor(dates, scope) {
      if (!annoCache.length || !dates || !dates.length) return [];
      const first = String(dates[0]), last = String(dates[dates.length - 1]);
      scope = scope || 'analytics';
      return annoCache
        .filter(a => annoInScope(a, scope))
        .filter(a => {
          const start = String(a.event_date || '');
          const end = String(a.end_date || a.event_date || '');
          return start <= last && end >= first;   // any overlap with the window
        })
        .map(a => ({ anno: a, idx: annoIndexFor(dates, String(a.event_date)) }))
        .filter(m => m.idx >= 0);
    }

    function annoLinePlugin(dates, scope) {
      return {
        id: 'sfAnnotations',
        afterDatasetsDraw(chart) {
          const markers = annoVisibleFor(dates, scope);
          if (!markers.length) return;
          const area = chart.chartArea, xs = chart.scales.x;
          if (!area || !xs) return;
          const ctx = chart.ctx;
          ctx.save();
          for (const m of markers) {
            const x = xs.getPixelForValue(m.idx);
            if (x < area.left - 1 || x > area.right + 1) continue;
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = m.anno.color || '#64748b';
            ctx.globalAlpha = 0.75;
            ctx.moveTo(x, area.top);
            ctx.lineTo(x, area.bottom);
            ctx.stroke();
          }
          ctx.restore();
        },
      };
    }

    // The DOM half: one focusable pin per marker, positioned over the canvas.
    function syncAnnoPins(chartId, dates, scope) {
      const canvas = document.getElementById(chartId);
      if (!canvas) return;
      const host = canvas.parentElement;
      if (!host) return;
      host.classList.add('anno-host');
      let layer = host.querySelector('.anno-layer');
      if (!layer) {
        layer = document.createElement('div');
        layer.className = 'anno-layer';
        host.appendChild(layer);
      }
      const chart = __charts[chartId];
      const markers = annoVisibleFor(dates, scope);
      // chartArea is only populated once the chart has laid out; bail quietly
      // rather than positioning pins against undefined geometry.
      if (!chart || !chart.scales || !chart.scales.x || !chart.chartArea || !markers.length) {
        layer.innerHTML = ''; return;
      }
      const xs = chart.scales.x, area = chart.chartArea;
      layer.innerHTML = markers.map(m => {
        const x = xs.getPixelForValue(m.idx);
        if (x < area.left - 1 || x > area.right + 1) return '';
        const a = m.anno;
        const when = a.end_date ? `${a.event_date} → ${a.end_date}` : a.event_date;
        const shared = a.visibility === 'shared' ? 'Shared with the client' : 'Internal — the client does not see this';
        const tip = `${when} · ${a.category_label}\n${a.title}`
          + (a.body ? `\n\n${a.body}` : '')
          + `\n\n${shared}${annoCanEdit ? ' · edit under Settings → Insights' : ''}`;
        return `<button type="button" class="anno-pin${a.visibility === 'internal' ? ' internal' : ''}"`
          + ` style="left:${x.toFixed(1)}px;--anno-color:${esc(a.color)}" data-anno-id="${a.id}"`
          + ` title="${esc(tip)}" aria-label="${esc(when + ': ' + a.title)}"></button>`;
      }).join('');
    }

    // Redraw every chart that carries annotations — used after an edit so the
    // markers update without a page reload.
    const ANNOTATED_CHARTS = [];
    function registerAnnotatedChart(fn) { if (ANNOTATED_CHARTS.indexOf(fn) < 0) ANNOTATED_CHARTS.push(fn); }
    function refreshAnnotatedCharts() { for (const fn of ANNOTATED_CHARTS) { try { fn(); } catch (e) {} } }

    async function loadAnnotations(force) {
      if (annoLoaded && !force) return;
      try {
        const payload = await getJson(ANNOTATIONS_API);
        annoCache = payload.annotations || [];
        annoCanEdit = !!payload.can_edit;
        annoCategories = payload.categories || [];
        annoLoaded = true;
        refreshAnnotatedCharts();
      } catch (err) {
        // Markers are additive context; a chart without them is still correct.
        annoCache = []; annoCanEdit = false;
      }
    }

    function withDates(base) {
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + currentStart + '&end_date=' + currentEnd;
    }
    // "Jul 1 – Jul 31, 2026" for a pair of ISO dates — used by panel badges that
    // have to name the window they are showing.
    const _MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function shortRangeLabel(s, e) {
      const a=String(s||'').split('-'), b=String(e||'').split('-');
      if (a.length!==3 || b.length!==3) return `${s} – ${e}`;
      const mon=p=>_MONTHS_SHORT[Number(p[1])-1]||p[1];
      const day=p=>String(Number(p[2]));
      const left=`${mon(a)} ${day(a)}`+(a[0]===b[0]?'':`, ${a[0]}`);
      return `${left} – ${mon(b)} ${day(b)}, ${b[0]}`;
    }
    function withDatesRange(base, s, e) {
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + s + '&end_date=' + e;
    }
    // Endpoints that compute their own vs-previous figures server-side (the
    // Search Console ones) need the comparison window spelled out, or they fall
    // back to the preceding period and contradict the Compare picker.
    function withCompare(url) {
      if (!compareStart || !compareEnd) return url;
      const sep = url.includes('?') ? '&' : '?';
      return url + sep + 'compare_start_date=' + compareStart + '&compare_end_date=' + compareEnd;
    }
    // ---- Period-over-period comparison helpers ----
    function deltaHtml(curr, prev) {
      curr = num(curr);
      if (prev == null) return '';
      prev = num(prev);
      const vs = cmpNoun();
      if (!prev) {
        if (!curr) return `<div class="card-delta flat">No change vs ${vs}</div>`;
        return `<div class="card-delta up">New vs ${vs}</div>`;
      }
      const change = ((curr - prev) / Math.abs(prev)) * 100;
      const dir = change > 0.05 ? 'up' : (change < -0.05 ? 'down' : 'flat');
      const arrow = dir==='up' ? '\u25B2' : (dir==='down' ? '\u25BC' : '\u2014');
      return `<div class="card-delta ${dir}">${arrow} ${Math.abs(change).toFixed(1)}% vs ${vs}</div>`;
    }
    // True if the comparison window (compareStart/compareEnd) reaches back
    // before any of the given sources' synced history -- earliestDates is
    // populated from the /marketing/health payload by loadHealth().
    function cmpBlockedBy(sourceKeys) {
      for (const k of sourceKeys) {
        const earliest = earliestDates[k];
        if (earliest && compareStart && compareStart < earliest) return earliest;
      }
      return null;
    }
    function setCmpWarn(elId, sourceKeys) {
      const el = document.getElementById(elId);
      if (!el) return;
      const blockedSince = cmpBlockedBy(sourceKeys);
      if (blockedSince) {
        el.hidden = false;
        el.title = `Comparison period (${compareStart} to ${compareEnd}) starts before synced data begins (${blockedSince}). The "vs ${cmpNoun()}" figures above may be incomplete.`;
      } else {
        el.hidden = true;
        el.title = '';
      }
    }
    // Every per-card warning icon and the source(s) whose synced history it
    // speaks for. Each card sets its own icon as it renders, but the health
    // payload that decides them arrives independently -- so whichever lands
    // second has to repaint the other's. setCmpWarn is null-guarded, so listing
    // a card that isn't on this page (or hasn't rendered yet) is a no-op.
    const CMP_WARN_TARGETS = [
      ['ga4SnapshotCmpWarn', ['google_analytics']],
      ['gscSnapshotCmpWarn', ['gsc']],
      ['avgDurCmpWarn', ['google_analytics']],
    ];
    function refreshCmpWarns() {
      for (const [elId, sources] of CMP_WARN_TARGETS) setCmpWarn(elId, sources);
    }
    // Human names for the /marketing/health source keys, for the notice below.
    const CMP_SOURCE_LABELS = {
      google:'Google Ads', linkedin:'LinkedIn Ads', meta:'Meta Ads',
      microsoft:'Microsoft Ads', google_analytics:'GA4', gsc:'Search Console',
    };
    // Page-level version of the per-section warning icons: name every source
    // whose synced history starts after the comparison window does. Usually
    // silent on a previous-period comparison and loud on a previous-year one,
    // which is the signal that the warehouse needs a deeper backfill.
    function syncCompareNotice() {
      const el = document.getElementById('compareNotice');
      const tip = document.getElementById('compareNoticeTip');
      if (!el || !tip) return;
      const gaps = [];
      if (compareStart) {
        for (const k of Object.keys(earliestDates)) {
          const earliest = earliestDates[k];
          if (earliest && compareStart < earliest) {
            gaps.push(`${CMP_SOURCE_LABELS[k] || k} (from ${earliest})`);
          }
        }
      }
      if (!gaps.length) { el.hidden = true; tip.innerHTML = ''; return; }
      gaps.sort();
      const mode = COMPARE_MODE_LABELS[compareMode] || 'Previous period';
      el.hidden = false;
      tip.innerHTML = `No synced data covers all of the <strong>${esc(mode.toLowerCase())}</strong> comparison window `
        + `(${esc(compareStart)} – ${esc(compareEnd)}): ${esc(gaps.join(', '))}. `
        + 'Every "vs ' + esc(cmpNoun()) + '" figure below is measured against a partial window until those sources are backfilled.';
    }
    async function getJson(url, _attempt) {
      _attempt = _attempt || 0;
      const MAX_RETRIES = 2;
      // Growing backoff with jitter: ~0.4-0.8s, then ~0.8-1.2s.
      const backoff = () => new Promise(r => setTimeout(r, 400 * (_attempt + 1) + Math.random()*400));
      let resp;
      try {
        resp = await fetch(url, { credentials:'same-origin' });
      } catch (netErr) {
        // fetch() rejects (no HTTP status) on network drops, cold-start
        // connection resets and platform proxy blips -- treat like a 5xx and
        // retry before surfacing the failure.
        if (_attempt < MAX_RETRIES) { await backoff(); return getJson(url, _attempt + 1); }
        throw netErr;
      }
      if (!resp.ok && resp.status >= 500 && _attempt < MAX_RETRIES) {
        // 5xx here has mostly been transient BQ concurrency pressure / cold
        // starts, not a real failure -- retry with growing backoff.
        await backoff();
        return getJson(url, _attempt + 1);
      }
      const body = await resp.json().catch(() => ({ detail:resp.statusText }));
      if (!resp.ok) {
        // FastAPI error bodies here are {detail: {error, type}} -- surface the
        // actual message instead of stringifying the whole object.
        const d = body && body.detail;
        const msg = (d && typeof d === 'object') ? (d.error || JSON.stringify(d)) : d;
        throw new Error(msg || resp.statusText || 'Request failed');
      }
      return body;
    }
    function setStatus(id, text, isError) {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.className = isError ? 'status error' : 'status';
      el.style.display = text ? '' : 'none';
    }
    function renderTable(id, columns, rows, emptyText) {
      const el = document.getElementById(id);
      if (!rows || !rows.length) { el.innerHTML = `<tbody><tr><td class="empty">${esc(emptyText)}</td></tr></tbody>`; return; }
      el.innerHTML =
        `<thead><tr>${columns.map(c => `<th class="${c.left ? 'left' : ''}">${esc(c.label)}</th>`).join('')}</tr></thead>` +
        `<tbody>${rows.map(row => `<tr>${columns.map(c => `<td class="${c.left ? 'left' : ''}">${esc(c.format ? c.format(row[c.key], row) : row[c.key])}</td>`).join('')}</tr>`).join('')}</tbody>`;
    }
    function pctBar(pct) {
      return `<div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, pct).toFixed(1)}%"></div></div>`;
    }

    // ---- Tab system ----
    const TABS = ['overview', 'explorer', 'analytics', 'ai_traffic', 'gsc', 'site_performance', 'google_business'];
    let currentTab = 'overview';
    let analyticsLoaded = false;
    let explorerLoaded = false;
    let gscLoaded = false;
    let aiTrafficLoaded = false;

    function switchTab(tab) {
      TABS.forEach(t => { document.getElementById('pane-' + t).hidden = t !== tab; });
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      const efb = document.getElementById('explorerFilterBar');
      if (efb) efb.hidden = !(tab === 'explorer' && EXPLORER_FILTER_GROUPS.length);
      // Site Performance shows one latest PageSpeed snapshot plus its own trend,
      // so the sticky Range/Compare filters have nothing to act on — hide the
      // whole filter bar on that tab rather than leaving dead controls.
      const dateBar = document.querySelector('.date-bar');
      if (dateBar) dateBar.hidden = tab === 'site_performance';
      currentTab = tab;
      // Keep the tab in the URL so a refresh reopens the same page (not
      // Overview), and so switching clients can carry the tab across. Overview
      // is the canonical home, so it drops ?view= to keep that URL clean.
      try {
        const u = new URL(location.href);
        if (tab === 'overview') u.searchParams.delete('view');
        else u.searchParams.set('view', tab);
        history.replaceState(null, '', u);
      } catch (e) { /* ignore */ }
      updateKeyEventBar();
      if (tab === 'explorer' && !explorerLoaded) {
        explorerLoaded = true;
        loadExplorer();
      }
      // The budget module lazy-loads via IntersectionObserver, which is
      // unreliable when the pane is revealed below the fold (blank until a
      // scroll/refresh/toggle). Trigger it directly on tab open; it's a no-op
      // once already loaded. (Absent when the client has no paid-ads budget.)
      if (tab === 'explorer' && typeof window.ensureBudgetLoaded === 'function') {
        window.ensureBudgetLoaded();
      }
      if (tab === 'ai_traffic' && !aiTrafficLoaded) {
        aiTrafficLoaded = true;
        loadAiTraffic();
      }
      if (tab === 'analytics' && !analyticsLoaded) {
        analyticsLoaded = true;
        applyModules();
        loadAllAnalytics();
      }
      if (tab === 'gsc' && !gscLoaded) {
        gscLoaded = true;
        loadGsc();
        loadSemrush();
      }
      if (tab === 'site_performance' && !sitePerfLoaded) {
        sitePerfLoaded = true;
        loadSitePerformance();
      }
      if (tab === 'google_business' && !googleBusinessLoaded) {
        googleBusinessLoaded = true;
        loadGoogleBusiness();
      }
    }

    document.querySelectorAll('.tab-btn').forEach(btn =>
      btn.addEventListener('click', () => switchTab(btn.dataset.tab))
    );

    // ---- Module system (localStorage) ----
    const ALL_MODULES = ['top_pages','traffic','audience','landing','user_acquisition','avg_duration','demographics'];
    // Element a module owns. For the four modules that share a tabbed card
    // (top_pages/landing, traffic/user_acquisition) this is the tab's pane, and
    // applyPanelCards — not applyModules — decides when it shows.
    const MODULE_SECTIONS = {
      top_pages:'sec-pages', traffic:'sec-traffic', audience:'sec-audience',
      landing:'sec-landing',
      user_acquisition:'sec-useracq', avg_duration:'sec-avgduration', demographics:'sec-demographics'
    };

    // Modules hidden while a page-path scope is active: they aggregate whole-site
    // GA4 sessions/users and have no page_path to scope by, so showing them next
    // to careers-only Pages/Landing would be misleading. Top pages and Landing
    // pages are not in this list — their daily series is served page-scoped from
    // vw_page_path_daily (see fetch_traffic_acquisition).
    // Demographics is likewise not hidden: its geography half reads the page-scoped
    // vw_ga4_geo_page_daily under a scope (see fetch_demographics), and only its
    // user-scoped age/gender panels hide — that's demoUserPanels below.
    const PATH_FILTER_HIDDEN_MODULES = ['traffic','audience','user_acquisition'];
    function pathFilterActive() { return Array.isArray(ANALYTICS_PATH_FILTER) && ANALYTICS_PATH_FILTER.length > 0; }
    function getModules() {
      let modules;
      try {
        const s = localStorage.getItem('nixon_analytics_modules');
        const saved = s ? JSON.parse(s) : {};
        modules = ALL_MODULES.reduce((o, k) => ({...o, [k]: k in saved ? saved[k] : true}), {});
      } catch { modules = ALL_MODULES.reduce((o, k) => ({...o, [k]: true}), {}); }
      if (pathFilterActive()) PATH_FILTER_HIDDEN_MODULES.forEach(k => { modules[k] = false; });
      return modules;
    }

    // ---- Tabbed panel cards ----
    // Two modules share one full-width card and swap on a tab. The module system
    // still owns whether each half exists at all, so a card follows its tabs:
    // one module off hides that tab (and falls back to the other if it was the
    // one on screen), both off hides the card.
    const PANEL_CARDS = {
      pages: { card:'card-pages', tabs:[
        {module:'top_pages', pane:'sec-pages',    status:'pagesStatus'},
        {module:'landing',   pane:'sec-landing',  status:'landingStatus'},
      ]},
      acq: { card:'card-acq', tabs:[
        {module:'traffic',          pane:'sec-traffic', status:'trafficAcqStatus'},
        {module:'user_acquisition', pane:'sec-useracq', status:'userAcqStatus'},
      ]},
      // Search Console's Branded / Target queries. No module gates these two —
      // they are the same Search Console data cut two ways — so they carry no
      // `module` and are always live.
      gsckw: { card:'card-gsc-kw', tabs:[
        {pane:'sec-gsc-branded', status:null},
        {pane:'sec-gsc-target',  status:null},
      ]},
    };
    // Pane on screen per card. Defaults match the markup.
    const panelOpen = { pages:'sec-pages', acq:'sec-traffic', gsckw:'sec-gsc-branded' };

    function showPanelTab(grp, pane) {
      const cfg = PANEL_CARDS[grp];
      if (!cfg || !cfg.tabs.some(t => t.pane === pane)) return;
      panelOpen[grp] = pane;
      cfg.tabs.forEach(t => {
        const on = t.pane === pane;
        const el = document.getElementById(t.pane);
        const btn = document.getElementById('tab-' + t.pane);
        const st = document.getElementById(t.status);
        if (el) el.hidden = !on;
        if (btn) btn.setAttribute('aria-selected', on ? 'true' : 'false');
        if (st) st.hidden = !on;
      });
      // Controls that live in the card head but belong to one pane (the
      // Branded / Target keyword editors' Edit button) follow the tab too.
      const cardEl = document.getElementById(cfg.card);
      if (cardEl) cardEl.querySelectorAll('[data-pnl-for]').forEach(el => {
        el.hidden = el.getAttribute('data-pnl-for') !== pane;
      });
      // Column widths are frozen from a laid-out table, and a table first
      // rendered inside a hidden pane measures nothing — so re-run the freeze
      // now that the pane is on screen (enableColResize is idempotent).
      const shown = document.getElementById(pane);
      if (shown) shown.querySelectorAll('table.resizable').forEach(t => enableColResize(t.id));
      // Same story for a chart drawn behind a closed tab: it measured a 0-high
      // host. Chart.js re-measures on its own resize observer, but nudge it so
      // the chart is right on the first frame rather than the second.
      if (shown) shown.querySelectorAll('canvas[id]').forEach(c => {
        const ch = __charts[c.id];
        if (ch) { try { ch.resize(); } catch (e) {} }
      });
    }

    document.querySelectorAll('.pnl-tab').forEach(btn =>
      btn.addEventListener('click', () => showPanelTab(btn.dataset.pnl, btn.dataset.pane))
    );

    function applyPanelCards(modules) {
      Object.entries(PANEL_CARDS).forEach(([grp, cfg]) => {
        const card = document.getElementById(cfg.card);
        const live = cfg.tabs.filter(t => !t.module || modules[t.module]);
        cfg.tabs.forEach(t => {
          const btn = document.getElementById('tab-' + t.pane);
          if (btn) btn.hidden = !!t.module && !modules[t.module];
        });
        if (card) card.hidden = !live.length;
        if (!live.length) return;
        const tablist = card && card.querySelector('.pnl-tabs');
        if (tablist) tablist.classList.toggle('one-tab', live.length === 1);
        // If the pane on screen just lost its module, fall back to what's left.
        showPanelTab(grp, live.some(t => t.pane === panelOpen[grp]) ? panelOpen[grp] : live[0].pane);
      });
    }

    function applyModules() {
      const modules = getModules();
      const tabbed = new Set(Object.values(PANEL_CARDS).flatMap(c => c.tabs.map(t => t.module)).filter(Boolean));
      ALL_MODULES.forEach(key => {
        if (tabbed.has(key)) return;              // handled by applyPanelCards
        const sec = document.getElementById(MODULE_SECTIONS[key]);
        if (sec) sec.hidden = !modules[key];
      });
      applyPanelCards(modules);
      updateKeyEventBar();
    }
    // The Events dropdown now lives in the shared sticky bar, so it must only
    // show on the Website Analytics tab — and only when a panel it drives
    // (Top pages / Traffic / Landing / User acquisition) is enabled.
    function updateKeyEventBar() {
      const keBar = document.getElementById('keyEventFilterGroup');
      if (!keBar) return;
      const m = getModules();
      const anyPanel = m.top_pages || m.traffic || m.landing || m.user_acquisition;
      keBar.hidden = !(currentTab === 'analytics' && anyPanel);
    }

    // ---- Paid media: Summary ----
    // 4th field = which direction is "good" for coloring the vs-previous delta:
    // 'up' (more is better), 'down' (less is better), 'neutral' (just report it).
    // CTR is deliberately 'neutral': it is a ratio, and it falls whenever
    // impressions grow faster than clicks -- which is what broadening reach
    // looks like, and is good news if conversions came with it. Colouring a CTR
    // dip red tells people to fix something that may not be broken, so the
    // number and its arrow are reported and the judgement is left to the reader.
    // Compact form per metric; the `format` in SUMMARY_CARDS stays the exact one
    // and becomes the hover title. CTR is already short, so it is its own.
    const CARD_COMPACT = { spend:moneyCompact, impressions:countCompact, clicks:countCompact,
      conversions:countCompact, cpc:moneyCompact, cpa:moneyCompact, ctr:pct };
    const SUMMARY_CARDS = [
      ['spend','Spend',money,'neutral'],['impressions','Impressions',count,'up'],['clicks','Clicks',count,'up'],
      ['conversions','Conversions',count,'up'],['cpc','CPC',money,'down'],['cpa','CPA',money,'down'],['ctr','CTR',pct,'neutral'],
    ];
    // One colour for every summary spark. A per-metric palette implied that the
    // colours meant something -- they never did, and seven of them across one row
    // read as decoration.
    const SPARK_COLOR = '#1d6fd0';
    const platformFilter = new Set();
    let summaryPayload = null;
    let compareSummaryPayload = null;
    const summaryCards = document.getElementById('summaryCards');

    function selectedSummaryFrom(payload) {
      if (!payload) return {};
      const by = payload.by_source || null;
      if (!by || platformFilter.size === 0) return payload.summary || {};
      const acc = { spend:0, impressions:0, clicks:0, conversions:0 };
      const needles = [...platformFilter].map(p => p.toLowerCase());
      for (const k of Object.keys(by)) {
        if (!needles.some(nd => k.toLowerCase().includes(nd))) continue;
        const src = by[k];
        acc.spend += num(src.spend); acc.impressions += num(src.impressions);
        acc.clicks += num(src.clicks); acc.conversions += num(src.conversions);
      }
      return { ...acc, cpc: acc.clicks ? acc.spend/acc.clicks : 0, cpa: acc.conversions ? acc.spend/acc.conversions : 0, ctr: acc.impressions ? acc.clicks/acc.impressions*100 : 0 };
    }
    function selectedSummary() { return selectedSummaryFrom(summaryPayload); }
    // Tiny inline-SVG sparkline of the metric's current-period daily trend.
    function sparkSvg(vals, color) {
      const clean=vals.filter(v=>v!=null&&isFinite(v));
      if (clean.length<2) return '<span class="spark-empty"></span>';
      const n=vals.length, w=66, h=22, mn=Math.min(...clean), mx=Math.max(...clean), span=(mx-mn)||1;
      const xy=vals.map((v,i)=>[(n===1?w/2:i/(n-1)*w), (h-2-((num(v)-mn)/span)*(h-4))]);
      const pts=xy.map(([x,y])=>`${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
      // A faint fill down to a baseline rule. A bare stroke floats in the middle
      // of the card and reads as a scribble; given a floor to sit on and an area
      // under it, the same line reads as a chart.
      const area=`${xy[0][0].toFixed(1)},${h} ${pts} ${xy[xy.length-1][0].toFixed(1)},${h}`;
      // Strokes are non-scaling: these boxes stretch horizontally (the spark
      // goes full-width on a card with no comparison), and a scaled stroke
      // would thin out as it did.
      return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">`
        + `<polygon class="spark-fill" points="${area}" fill="${color}"/>`
        + `<line class="spark-base" x1="0" y1="${h-0.5}" x2="${w}" y2="${h-0.5}" stroke="${color}" vector-effect="non-scaling-stroke"/>`
        + `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
    }
    // % change vs the comparison period, colored by whether the move is good for
    // that metric (dir from SUMMARY_CARDS). Arrow always shows the raw direction.
    // (Distinct from the snapshot-card deltaHtml above — this one is a compact
    // inline chip for the paid summary cards.)
    function summaryDeltaHtml(cur, prev, dir) {
      // Nothing to compare against: say nothing. An em dash under every value is
      // a placeholder for a number that is never coming, and the card's footer
      // gives the room back to the spark line instead (see .card-foot CSS).
      if (prev==null || num(prev)===0 || cur==null) return '';
      const ch=(num(cur)-num(prev))/num(prev)*100;
      const tip=`vs ${cmpNoun()} (${compareStart} – ${compareEnd})`;
      if (Math.abs(ch)<0.5) return `<span class="cmp-delta flat" title="${tip}">0%</span>`;
      const up=ch>0, arrow=up?'▲':'▼';
      // Two separate questions, and only the second one earns colour:
      //   1. Which way did it move? -- the arrow, always shown.
      //   2. Is that worth reacting to? -- red or green, and only for a move of
      //      at least MEANINGFUL_DELTA_PCT on a metric that has a good
      //      direction. Everything smaller keeps its arrow in muted grey, so a
      //      table of ordinary week-to-week wobble reads as calm instead of as
      //      a wall of alarms.
      const meaningful=Math.abs(ch)>=MEANINGFUL_DELTA_PCT;
      let cls='flat';
      if (meaningful) {
        if (dir==='up') cls=up?'up':'down'; else if (dir==='down') cls=up?'down':'up';
      }
      const note=meaningful ? '' : ` · under ${MEANINGFUL_DELTA_PCT}%, reads as steady`;
      return `<span class="cmp-delta ${cls}" title="${tip}${note}">${arrow} ${Math.abs(ch).toFixed(0)}%</span>`;
    }
    // ---- Goals + peer benchmarks (admin preview) ----
    // Both hang off the summary cards and both are additive: if either payload
    // is missing, failed, or has nothing for a metric, the card renders exactly
    // as it always did. Neither is ever fetched for a non-admin.
    let goalsPayload = null;
    let benchPayload = null;

    // Format a number the way its card does, so a target reads like the value it
    // sits under (a $ target under a $ value) rather than as a bare number.
    const GOAL_FORMATTERS = { currency:money, currency2:v=>'$'+num(v).toFixed(2), percent:pct, number:count };
    function goalFmt(fmt, v) { return (GOAL_FORMATTERS[fmt] || count)(v); }

    // Percent-of-target → good|warn|bad, using the thresholds the server sent.
    // Mirrors metric_goals.grade() in Python; the server owns the numbers so the
    // two cannot drift apart without the payload changing.
    function goalStatus(pct, direction, thresholds) {
      const t = (thresholds||{})[direction];
      if (!t || pct==null || !isFinite(pct)) return '';
      if (direction === 'up')   return pct >= t.good ? 'good' : (pct >= t.warn ? 'warn' : 'bad');
      if (direction === 'down') return pct <= t.good ? 'good' : (pct <= t.warn ? 'warn' : 'bad');
      if (pct >= t.good_low && pct <= t.good_high) return 'good';
      if (pct >= t.warn_low && pct <= t.warn_high) return 'warn';
      return 'bad';
    }

    function goalRowHtml(key, value) {
      if (!goalsPayload) return '';
      const g = (goalsPayload.goals||{})[key];
      if (!g || !g.target || value==null) return '';
      const pct = num(value) / num(g.target) * 100;
      if (!isFinite(pct)) return '';
      const status = goalStatus(pct, g.direction, goalsPayload.thresholds);
      // The bar caps at 100% so an overshoot doesn't render off the card; the
      // caption still states the true percentage.
      const width = Math.max(0, Math.min(100, pct));
      const verb = g.direction === 'down' ? 'of' : 'of';
      const tip = `Target ${goalFmt(g.format, g.target)} for this range · stored goal ${goalFmt(g.format, g.goal)}${g.cumulative ? '/mo' : ''}. ${g.note}`;
      return `<div class="card-goal ${status}" title="${esc(tip)}">`
        + `<div class="cg-bar"><span style="width:${width.toFixed(1)}%"></span></div>`
        + `<div class="cg-text">${pct.toFixed(0)}% ${verb} ${goalFmt(g.format, g.target)} target</div></div>`;
    }

    function benchRowHtml(key, value) {
      if (!benchPayload || !benchPayload.available) return '';
      const b = (benchPayload.metrics||{})[key];
      if (!b || !b.peer || !b.peer.n) return '';
      const median = num(b.peer.median);
      // "Ahead"/"behind" follows the metric's meaning, not its arithmetic: a CPA
      // below the peer median is ahead, a CTR below it is behind.
      let verdict = 'level', word = 'in line with';
      if (value != null && median) {
        const higher = num(value) > median * 1.05;
        const lower  = num(value) < median * 0.95;
        if (b.direction === 'up' && higher)   { verdict='ahead';  word='ahead of'; }
        else if (b.direction === 'up' && lower) { verdict='behind'; word='behind'; }
        else if (b.direction === 'down' && lower) { verdict='ahead';  word='better than'; }
        else if (b.direction === 'down' && higher) { verdict='behind'; word='worse than'; }
        else if (b.direction === 'none' && (higher||lower)) { word = higher ? 'above' : 'below'; }
      }
      const thin = b.peer.thin ? ` <span class="cb-thin" title="Only ${b.peer.n} comparable client${b.peer.n===1?'':'s'} — directional, not a benchmark.">thin</span>` : '';
      const scope = b.scope === 'industry' ? b.peer_label : 'all clients';
      const tip = `${b.label} across ${b.peer.n} other ${esc(String(scope))} account${b.peer.n===1?'':'s'}: `
        + `median ${goalFmt(b.format, b.peer.median)}, mean ${goalFmt(b.format, b.peer.mean)}, `
        + `range ${goalFmt(b.format, b.peer.min)}–${goalFmt(b.format, b.peer.max)}. `
        + `The client's own value is excluded from its peer group.`;
      return `<div class="card-bench" title="${esc(tip)}">`
        + `<span class="cb-verdict ${verdict}">${esc(word)}</span>`
        + `<span>${esc(String(scope))} · <b>${goalFmt(b.format, b.peer.median)}</b> median (n=${b.peer.n})</span>${thin}</div>`;
    }

    function renderSummary() {
      const s = selectedSummary();
      const prev = selectedSummaryFrom(compareSummaryPayload);
      const daily = buildChartDaily();
      summaryCards.innerHTML = SUMMARY_CARDS.map(([key,label,format,dir]) => {
        const delta = summaryDeltaHtml(s[key], (prev && prev[key]!=null) ? prev[key] : null, dir);
        const spark = sparkSvg(daily.map(d=>num(d[key])), SPARK_COLOR);
        const extra = goalRowHtml(key, s[key]) + benchRowHtml(key, s[key]);
        const exact = format(s[key]), shown = (CARD_COMPACT[key]||format)(s[key]);
        const vTip = shown===exact ? '' : ` title="${esc(exact)}"`;
        return `<div class="card"><div class="card-title">${label}</div><div class="card-value"${vTip}>${shown}</div><div class="card-foot">${delta}${spark}</div>${extra}</div>`;
      }).join('');
    }

    // Both loaders are fire-and-forget: they re-render the cards when they land
    // and stay silent when they don't, so a slow benchmark pass (it walks every
    // client's cached marts) never holds up the numbers the page exists to show.
    async function loadGoals() {
      if (!IS_ADMIN) return;
      try {
        goalsPayload = await getJson(withDates(GOALS_API));
        if (summaryPayload) renderSummary();
      } catch (err) { goalsPayload = null; }
    }
    // Not window-scoped — the benchmark always describes the agency's own two
    // warm windows — so one successful fetch serves the whole page session and
    // changing the date range does not re-request it.
    let benchLoaded = false;
    async function loadBenchmarks() {
      if (!IS_ADMIN || !SHOW_BENCHMARKS || benchLoaded) return;
      try {
        benchPayload = await getJson(BENCHMARKS_API);
        benchLoaded = true;
        if (summaryPayload) renderSummary();
      } catch (err) { benchPayload = null; }
    }

    // ---- Summary sparkline data (feeds the Paid summary cards) ----
    function buildChartDaily() { return buildPaidDaily(summaryPayload); }
    // One row per date for whatever the Platform chips allow, with cpc/cpa/ctr
    // derived from the summed parts (never averaged from per-source ratios,
    // which is a different and wrong number).
    function buildPaidDaily(payload) {
      const daily = (payload && payload.daily) ? payload.daily : [];
      const needles = platformFilter.size ? [...platformFilter].map(p => p.toLowerCase()) : null;
      const byDate = new Map();
      for (const r of daily) {
        if (needles && !needles.some(nd => String(r.source||'').toLowerCase().includes(nd))) continue;
        let d = byDate.get(r.date);
        if (!d) { d = { date:r.date, spend:0, impressions:0, clicks:0, conversions:0 }; byDate.set(r.date, d); }
        d.spend += num(r.spend); d.impressions += num(r.impressions); d.clicks += num(r.clicks); d.conversions += num(r.conversions);
      }
      const out = [...byDate.values()].sort((a,b) => a.date < b.date ? -1 : 1);
      for (const d of out) { d.cpc = d.clicks ? d.spend/d.clicks : 0; d.cpa = d.conversions ? d.spend/d.conversions : 0; d.ctr = d.impressions ? d.clicks/d.impressions*100 : 0; }
      return out;
    }
    async function loadSummary() {
      setStatus('summaryStatus','Loading…');
      summaryCards.innerHTML = skelCards(7);
      try {
        const [curr, prev] = await Promise.all([
          getJson(withDates(SUMMARY_API)),
          compareStart ? getJson(withDatesRange(SUMMARY_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
        ]);
        summaryPayload = curr;
        compareSummaryPayload = prev;
        renderSummary();
        // Date range is already shown by the Range dropdown, so keep this to
        // just the source note (blank unless the data is combined across sources).
        setStatus('summaryStatus', summaryPayload.by_source ? '' : 'combined');
      } catch(err) {
        summaryPayload=null;
        compareSummaryPayload=null;
        summaryCards.innerHTML = '';
        setStatus('summaryStatus', err.message||String(err), true);
      }
    }

    // ---- No-paid-ads Overview snapshot (GA4 traffic + GSC search) ----
    // cards: [label, currentRaw, prevRawOrNull, formatFn, ok]. ok===false means
    // the underlying request failed -- render "--" rather than a misleading 0,
    // which is indistinguishable from genuinely-zero activity.
    function renderSnapshotCards(containerId, cards) {
      document.getElementById(containerId).innerHTML = cards.map(([label,curr,prev,fmt,ok]) => {
        const valueHtml = ok===false
          ? `<span title="This metric failed to load -- try switching ranges or refreshing.">—</span>`
          : fmt(curr);
        const deltaOrNote = ok===false ? '' : deltaHtml(curr,prev);
        return `<div class="card"><div class="card-title">${label}</div><div class="card-value">${valueHtml}</div>${deltaOrNote}</div>`;
      }).join('');
    }
    async function ga4TrafficTotals(s, e) {
      // Sequential (not parallel) to keep concurrent BQ query load down --
      // and each sub-request tracks its own success so a failure renders as
      // "unavailable" rather than a misleading zero.
      let trafficOk = true, pagesOk = true;
      const traffic = await getJson(withDatesRange(TRAFFIC_ACQ_API, s, e)).catch(()=>{ trafficOk=false; return {by_channel:[]}; });
      const pages = await getJson(withDatesRange(PAGES_TOP_API, s, e)).catch(()=>{ pagesOk=false; return {rows:[]}; });
      const sessions = (traffic.by_channel||[]).reduce((sum,r)=>sum+num(r.sessions),0);
      const engaged = (traffic.by_channel||[]).reduce((sum,r)=>sum+num(r.engaged_sessions),0);
      const rows = pages.rows||[];
      const pageViews = rows.reduce((sum,r)=>sum+num(r.page_views),0);
      const keyEvents = rows.reduce((sum,r)=>sum+num(r.key_events),0);
      return {sessions, engaged, pageViews, keyEvents, trafficOk, pagesOk};
    }
    async function loadOverviewSnapshot() {
      setStatus('ga4SnapshotStatus','Loading…');
      document.getElementById('ga4SnapshotCards').innerHTML = skelCards(4);
      try {
        const [curr, prev] = await Promise.all([
          ga4TrafficTotals(currentStart, currentEnd),
          compareStart ? ga4TrafficTotals(compareStart, compareEnd).catch(()=>null) : Promise.resolve(null),
        ]);
        renderSnapshotCards('ga4SnapshotCards', [
          ['Sessions', curr.sessions, prev&&prev.sessions, count, curr.trafficOk],
          ['Engaged sessions', curr.engaged, prev&&prev.engaged, count, curr.trafficOk],
          ['Page views', curr.pageViews, prev&&prev.pageViews, count, curr.pagesOk],
          ['Key events', curr.keyEvents, prev&&prev.keyEvents, count, curr.pagesOk],
        ]);
        setCmpWarn('ga4SnapshotCmpWarn', ['google_analytics']);
        if (!curr.trafficOk || !curr.pagesOk) {
          setStatus('ga4SnapshotStatus', 'Some metrics failed to load — try switching ranges or refreshing.', true);
        } else {
          setStatus('ga4SnapshotStatus', curr.sessions || curr.pageViews ? '' : 'No data for this range yet.');
        }
      } catch(err) {
        document.getElementById('ga4SnapshotCards').innerHTML = '';
        setStatus('ga4SnapshotStatus', err.message||String(err), true);
      }
      setStatus('gscSnapshotStatus','Loading…');
      document.getElementById('gscSnapshotCards').innerHTML = skelCards(4);
      try {
        const [p, prevP] = await Promise.all([
          getJson(withDatesRange(GSC_API, currentStart, currentEnd)),
          compareStart ? getJson(withDatesRange(GSC_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
        ]);
        const k = (p&&p.kpis)||{};
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
      } catch(err) {
        document.getElementById('gscSnapshotCards').innerHTML = '';
        setStatus('gscSnapshotStatus', err.message||String(err), true);
      }
    }
    // ---- Search Console ----
    const gscPos = v => v==null ? '—' : num(v).toFixed(1);
    const gscPct = v => v==null ? '—' : (num(v)).toFixed(2) + '%';
    // How a query's CTR compares with what its average rank normally earns.
    // The bands are the standard organic click-curve ranges (position 1 pulls
    // 20-40%, position 11+ almost nothing), so the dot answers "is this CTR
    // good?" in the only way that means anything -- good *for that rank*.
    const GSC_CTR_BANDS = [
      {max:1.5,  lo:20, hi:40, label:'20–40%'},
      {max:2.5,  lo:10, hi:20, label:'10–20%'},
      {max:3.5,  lo:6,  hi:12, label:'6–12%'},
      {max:5.5,  lo:3,  hi:8,  label:'3–8%'},
      {max:10.5, lo:1,  hi:5,  label:'1–5%'},
      {max:Infinity, lo:0, hi:2, label:'under 2%'},
    ];
    const gscCtrBand = pos => GSC_CTR_BANDS.find(b => pos < b.max) || GSC_CTR_BANDS[GSC_CTR_BANDS.length-1];
    function gscCtrCell(v, row) {
      const txt = gscPct(v);
      const pos = row ? row.avg_position : null;
      if (v==null || pos==null) return txt;
      const b = gscCtrBand(num(pos)), c = num(v);
      const state = c > b.hi ? 'above' : (c < b.lo ? 'below' : 'typical');
      const word = state==='above' ? 'Ahead of' : state==='below' ? 'Behind' : 'In line with';
      const title = `${word} the ${b.label} a query at position ${num(pos).toFixed(1)} typically earns`;
      return `<span class="gsc-ctr gsc-ctr-${state}" title="${esc(title)}"><span class="gsc-ctr-dot"></span>${txt}</span>`;
    }
    // Position movement vs. the comparison period (whichever the Compare picker
    // selected -- the backend is told which): value is prior - current, so a
    // positive number means the keyword improved (moved toward rank 1). null =
    // the query didn't rank in that period ("New").
    const gscDelta = v => {
      if (v==null) return '<span class="gsc-mv gsc-mv-new">New</span>';
      const n=num(v);
      if (Math.abs(n)<0.05) return '<span class="gsc-mv gsc-mv-flat">\u2013</span>';
      if (n>0) return '<span class="gsc-mv gsc-mv-up">\u25B4 '+n.toFixed(1)+'</span>';
      return '<span class="gsc-mv gsc-mv-down">\u25BE '+Math.abs(n).toFixed(1)+'</span>';
    };
    function renderGscKpis(k, daily) {
      k = k || {}; daily = daily || [];
      // [label, formatted value, kpi key, prior value, good-direction, spark color].
      // Avg position is "lower is better" so its delta direction is 'down'.
      const cards = [
        ['Clicks', count(k.clicks), 'clicks', k.prior_clicks, 'up', '#1769aa'],
        ['Impressions', count(k.impressions), 'impressions', k.prior_impressions, 'up', '#7c3aed'],
        ['CTR', gscPct(k.ctr), 'ctr', k.prior_ctr, 'up', '#0a7f3f'],
        ['Avg position', gscPos(k.avg_position), 'avg_position', k.prior_avg_position, 'down', '#d97706'],
      ];
      document.getElementById('gscKpis').innerHTML = cards.map(([label,val,key,prior,dir,color]) => {
        const delta = summaryDeltaHtml(k[key], (prior!=null?prior:null), dir);
        const spark = sparkSvg(daily.map(d=>num(d[key])), color);
        return `<div class="card"><div class="card-title">${label}</div><div class="card-value">${val}</div><div class="card-foot">${delta}${spark}</div></div>`;
      }).join('');
    }
    // ---- GSC queries/pages: sortable + paginated (top 10/page) ----
    const GSC_PER_PAGE = 10;
    const GSC_SORT_COLS = [
      {key:'clicks', label:'Clicks', format:count, defDir:'desc'},
      {key:'impressions', label:'Impr.', format:count, defDir:'desc'},
      {key:'ctr', label:'CTR', format:gscCtrCell, defDir:'desc'},
      {key:'avg_position', label:'Position', format:gscPos, defDir:'asc'},
    ];
    // Δ Position vs. the comparison period. Every query-keyed table (top queries,
    // branded, target) carries prior_avg_position/delta_position from the
    // backend; only the pages table doesn't. Default asc so the first click
    // surfaces the biggest sinkers.
    const GSC_DELTA_COL = {key:'delta_position', label:'\u0394 Pos', format:gscDelta, defDir:'asc'};
    // With comparison switched off there is nothing to measure movement against,
    // and a column of dashes is worse than no column -- so it only appears while
    // the Compare switch is on.
    const gscColsFor = which => (which==='pages' || !compareStart) ? GSC_SORT_COLS : GSC_SORT_COLS.concat([GSC_DELTA_COL]);
    // page_url rows come back as full URLs (https://host/path) -- show just the
    // path in the table (full URL stays in the title tooltip on hover).
    function pathOnly(url) {
      try { const u = new URL(url); return (u.pathname || '/') + (u.search || ''); }
      catch { return url; }
    }
    const GSC_LABEL_COL_WIDTH = 185;
    const gscTables = {
      queries: {rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscQueriesTable', pagerId:'gscQueriesPager', labelWidth:GSC_LABEL_COL_WIDTH},
      pages:   {rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'page_url', labelText:'Page', tableId:'gscPagesTable', pagerId:'gscPagesPager', labelWidth:GSC_LABEL_COL_WIDTH, labelFormat:pathOnly},
      branded: {rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscBrandedTable', pagerId:'gscBrandedPager', labelWidth:GSC_LABEL_COL_WIDTH},
      target:  {rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscTargetTable', pagerId:'gscTargetPager', labelWidth:GSC_LABEL_COL_WIDTH},
    };
    // Reapply a user-chosen label-column width after each (re)render, since the
    // table is rebuilt on sort/paginate. Overrides the default max-width:0 clamp.
    function applyGscColWidth(el, st) {
      if (!el || !st.labelWidth) return;
      const w=st.labelWidth+'px';
      const th=el.querySelector('th.left'); if (th) th.style.width=w;
      el.querySelectorAll('td.left').forEach(td=>{ td.style.maxWidth=w; td.style.width=w; });
    }
    function renderGscTable(which) {
      const st = gscTables[which];
      const cols = gscColsFor(which);
      if (!cols.some(c => c.key === st.sortKey)) { st.sortKey='clicks'; st.sortDir='desc'; }
      const el = document.getElementById(st.tableId);
      const pager = document.getElementById(st.pagerId);
      if (!st.rows.length) { el.innerHTML=`<tbody><tr><td class="empty">No data for this range.</td></tr></tbody>`; pager.innerHTML=''; return; }
      const sorted=[...st.rows].sort((a,b)=>{const va=num(a[st.sortKey]),vb=num(b[st.sortKey]);return st.sortDir==='asc'?va-vb:vb-va;});
      const totalPages=Math.max(1,Math.ceil(sorted.length/GSC_PER_PAGE));
      if (st.page>totalPages) st.page=totalPages;
      const start=(st.page-1)*GSC_PER_PAGE, pageRows=sorted.slice(start,start+GSC_PER_PAGE);
      const arrow=k=>st.sortKey===k?(st.sortDir==='asc'?' \u25B4':' \u25BE'):'';
      const head=`<thead><tr><th class="left col-resizable">${esc(st.labelText)}<span class="col-resizer" data-which="${which}"></span></th>`+cols.map(c=>`<th class="gsc-sort${st.sortKey===c.key?' active':''}" data-which="${which}" data-key="${c.key}">${c.label}${arrow(c.key)}</th>`).join('')+`</tr></thead>`;
      const body=`<tbody>`+pageRows.map(r=>{const raw=r[st.labelKey];const label=st.labelFormat?st.labelFormat(raw):raw;return`<tr><td class="left"><span class="page-path" title="${esc(raw)}">${esc(label)}</span></td>`+cols.map(c=>`<td>${c.format(r[c.key], r)}</td>`).join('')+`</tr>`;}).join('')+`</tbody>`;
      el.innerHTML=head+body;
      applyGscColWidth(el, st);
      if (totalPages<=1) { pager.innerHTML=''; }
      else { pager.innerHTML=`<button type="button" class="pager-btn" data-which="${which}" data-dir="prev"${st.page<=1?' disabled':''}>\u2039 Prev</button><span class="pager-info">Page ${st.page} of ${totalPages}</span><button type="button" class="pager-btn" data-which="${which}" data-dir="next"${st.page>=totalPages?' disabled':''}>Next \u203A</button>`; }
    }
    document.getElementById('pane-gsc').addEventListener('click', ev => {
      const th=ev.target.closest('th.gsc-sort');
      if (th) { const st=gscTables[th.dataset.which], key=th.dataset.key;
        if (st.sortKey===key) st.sortDir=st.sortDir==='asc'?'desc':'asc';
        else { st.sortKey=key; st.sortDir=(gscColsFor(th.dataset.which).find(c=>c.key===key)||{}).defDir||'desc'; }
        st.page=1; renderGscTable(th.dataset.which); return;
      }
      const pb=ev.target.closest('.pager-btn[data-which]');
      if (pb && !pb.disabled) { const st=gscTables[pb.dataset.which]; st.page+=(pb.dataset.dir==='next'?1:-1); renderGscTable(pb.dataset.which); }
    });
    // Drag the label column edge to widen/narrow it (persists across sort/paginate).
    (function initGscColResize(){
      const pane=document.getElementById('pane-gsc'); if (!pane) return;
      let active=null;
      pane.addEventListener('mousedown', ev => {
        const h=ev.target.closest('.col-resizer'); if (!h) return;
        ev.preventDefault(); ev.stopPropagation();
        const st=gscTables[h.dataset.which], table=h.closest('table');
        const th=table.querySelector('th.left');
        active={st, table, startX:ev.clientX, startW:th.getBoundingClientRect().width};
        document.body.classList.add('col-resizing');
      });
      document.addEventListener('mousemove', ev => {
        if (!active) return;
        active.st.labelWidth=Math.max(90, Math.round(active.startW+(ev.clientX-active.startX)));
        applyGscColWidth(active.table, active.st);
      });
      document.addEventListener('mouseup', () => {
        if (!active) return; active=null; document.body.classList.remove('col-resizing');
      });
    })();
    async function loadGsc() {
      setStatus('gscStatus','Loading…');
      document.getElementById('gscKpis').innerHTML = skelCards(4);
      document.getElementById('gscQueriesTable').innerHTML = skelTable(5,6);
      document.getElementById('gscPagesTable').innerHTML = skelTable(5,6);
      try {
        const p = await getJson(withCompare(withDates(GSC_API)));
        renderGscKpis((p&&p.kpis)||{}, (p&&p.daily)||[]);
        for (const which of ['queries','pages']) {
          const st=gscTables[which]; st.rows = (p && (which==='queries'?p.top_queries:p.top_pages)) || [];
          st.page=1; st.sortKey='clicks'; st.sortDir='desc'; renderGscTable(which);
        }
        renderGscKeywordTables();
        loadGscWatchlist();
        const k=(p&&p.kpis)||{};
        const empty = !p || (!p.kpis && !(p.top_queries||[]).length && !(p.top_pages||[]).length);
        setStatus('gscStatus', empty ? 'No data for this range yet.' : `${count(k.clicks)} clicks · ${count(k.impressions)} impressions`);
      } catch(err) {
        setStatus('gscStatus', err.message||String(err), true);
      }
    }

    // ---- Branded / target keyword filtering (GSC queries only) ----
    // Each group has include roots plus optional exclude roots: a query counts
    // when it contains any include root AND none of the exclude roots (so you
    // can include "benjamin" but exclude "dr").
    let gscBrandedRoots = GSC_BRANDED_ROOTS.slice();
    let gscTargetKeywords = GSC_TARGET_KEYWORDS.slice();
    let gscBrandedExclude = GSC_BRANDED_EXCLUDE.slice();
    let gscTargetExclude = GSC_TARGET_EXCLUDE.slice();
    // Branded/target queries are matched against the FULL date-range dataset
    // via a dedicated backend scan (gsc/keyword-matches), not filtered from
    // the top_queries subset (top_queries is LIMIT 25 by clicks in
    // gsc/summary, so a real match outside the top 25 would otherwise be
    // silently missed).
    let _gscKwReqId = 0;
    async function fetchKeywordMatches(terms, excludeTerms) {
      if (!terms.length) return {rows:[], weekly:[]};
      let url = withCompare(withDates(GSC_KEYWORD_MATCHES_API)) + '&terms=' + encodeURIComponent(terms.join(','));
      if (excludeTerms && excludeTerms.length) url += '&exclude=' + encodeURIComponent(excludeTerms.join(','));
      try {
        const r = await getJson(url);
        return {rows: r.rows || [], weekly: r.weekly || []};
      } catch (err) {
        return {rows:[], weekly:[]};
      }
    }
    // Weekly avg-position trend: a single impression-weighted average-position
    // line per week over the matched keyword basket. For position lower is
    // better, so the y-axis is reversed (best rank at the top) like a rank
    // chart. The include/exclude roots keep the basket on-target, so the mean
    // tracks real rank movement rather than basket churn.
    function drawKeywordTrend(canvasId, rows, valueKey, color, invert) {
      clearSkelChart(canvasId);
      if (!document.getElementById(canvasId)) return;
      const n=rows.length;
      if (!n) { __destroyChart(canvasId); return; }
      const labels=rows.map(r=>String(r.week_start||'').slice(5));
      const data=rows.map(r=>num(r[valueKey]));
      const fmtPos=v=>(Math.round(v*10)/10).toFixed(1);
      lineChart(canvasId, labels, [{ label:'Avg position', data, color, fill:true }], {
        points:true, yReverse: !!invert, yDisplay:true, beginAtZero:false,
        yFmt: v => fmtPos(v),
        tooltip: { label: c => `Avg position: ${fmtPos(c.raw)}` },
      });
    }
    async function renderGscKeywordTables() {
      const reqId = ++_gscKwReqId;
      // Skeleton only the tables that will actually resolve to data, so we
      // never flash a skeleton in front of a "nothing configured" empty state.
      if (gscBrandedRoots.length) document.getElementById('gscBrandedTable').innerHTML = skelTable(4,6);
      if (gscTargetKeywords.length) document.getElementById('gscTargetTable').innerHTML = skelTable(4,6);
      const [branded, target] = await Promise.all([
        fetchKeywordMatches(gscBrandedRoots, gscBrandedExclude),
        fetchKeywordMatches(gscTargetKeywords, gscTargetExclude),
      ]);
      if (reqId !== _gscKwReqId) return; // a newer call (date range/terms changed) superseded this one
      gscTables.branded.rows = branded.rows;
      gscTables.target.rows  = target.rows;
      gscTables.branded.page = 1; gscTables.target.page = 1;
      renderGscTable('branded'); renderGscTable('target');
      drawKeywordTrend('gscBrandedTrendChart', branded.weekly, 'avg_position', '#1d6fd0', true);
      drawKeywordTrend('gscTargetTrendChart', target.weekly, 'avg_position', '#7c3aed', true);
      const setCount=(id,n,configured)=>{const el=document.getElementById(id); if(el) el.textContent = configured ? `(${n})` : '';};
      setCount('gscBrandedCount', gscTables.branded.rows.length, gscBrandedRoots.length);
      setCount('gscTargetCount', gscTables.target.rows.length, gscTargetKeywords.length);
      const none = !gscBrandedRoots.length && !gscTargetKeywords.length;
      setStatus('gscKwStatus', none ? 'Set branded roots and target keywords to see matching queries.' : '');
      // Empty-state hint when configured but nothing matched anywhere in range.
      if (gscBrandedRoots.length && !gscTables.branded.rows.length) document.getElementById('gscBrandedTable').innerHTML=`<tbody><tr><td class="empty">No queries match these branded roots.</td></tr></tbody>`;
      if (gscTargetKeywords.length && !gscTables.target.rows.length) document.getElementById('gscTargetTable').innerHTML=`<tbody><tr><td class="empty">No queries match these target keywords.</td></tr></tbody>`;
      if (!gscBrandedRoots.length) document.getElementById('gscBrandedTable').innerHTML=`<tbody><tr><td class="empty">No branded roots set.</td></tr></tbody>`;
      if (!gscTargetKeywords.length) document.getElementById('gscTargetTable').innerHTML=`<tbody><tr><td class="empty">No target keywords set.</td></tr></tbody>`;
    }
    // ---- Keyword watchlist -------------------------------------------------
    // The branded/target panels above pour every query containing a root into
    // one bucket. Here each ROW is one watched keyword an admin entered -- "this
    // page was written for this keyword" -- with its own rank spark line, so a
    // commitment made when the blog was briefed has somewhere to be checked.
    let gscWatchItems = (GSC_WATCH_ITEMS || []).map(it => ({kw:String(it.kw||''), page:String(it.page||'')}));
    // Default sort is 'manual' -- the order the admin arranged, which is the
    // order the list is stored in. A curated list should open the way it was
    // curated; the metric columns are still one click away.
    const gscWatch = {rows:{}, weekly:{}, sortKey:'manual', sortDir:'asc', dragFrom:null};
    const watchKey = kw => String(kw||'').trim().toLowerCase();
    const watchTerms = () => gscWatchItems.map(i=>i.kw.trim()).filter(Boolean);
    // Position: lower is better, so the spark plots the NEGATED series -- the
    // line then rises exactly when the rank improves, which is how a trend line
    // reads. Weeks with no impressions are absent from the series rather than
    // zero (a zero would draw as rank 1), so the line spans the weeks that
    // actually ranked. Green when the last week beats the first.
    function watchSpark(series) {
      const vals=(series||[]).map(r=>num(r.avg_position)).filter(v=>isFinite(v)&&v>0);
      if (vals.length<2) return '<span class="spark-empty"></span>';
      const improving=vals[vals.length-1]<=vals[0];
      return sparkSvg(vals.map(v=>-v), improving ? '#0a7f3f' : '#c02626');
    }
    const WATCH_COLS = [
      {key:'impressions', label:'Impr.', format:count, defDir:'desc'},
      {key:'clicks', label:'Clicks', format:count, defDir:'desc'},
      {key:'ctr', label:'CTR', format:gscCtrCell, defDir:'desc'},
    ];
    // One row per configured keyword, whether or not it ranked -- a keyword with
    // no impressions yet is the most interesting row on a watchlist, so it stays
    // visible with empty metrics instead of dropping out.
    function watchRows() {
      // Every configured item becomes a row, including one just added and not
      // yet named (`draft`) -- adding a row and having nothing appear is the
      // one thing an editable list must not do.
      return gscWatchItems.map((i,idx)=>{
        const k=watchKey(i.kw);
        const d=gscWatch.rows[k]||{};
        return Object.assign({}, d, {
          kw:i.kw, page:i.page, idx, draft:!i.kw.trim(),
          series:gscWatch.weekly[k]||[],
        });
      });
    }
    function renderGscWatchTable() {
      const el=document.getElementById('gscWatchTable'); if(!el) return;
      const rows=watchRows();
      if (!rows.length) {
        const hint=IS_ADMIN ? ' Use “+ Add keyword” or “Bulk add” to start the list.' : '';
        el.innerHTML=`<tbody><tr><td class="empty">No keywords on the watchlist yet.${hint}</td></tr></tbody>`;
        return;
      }
      // Missing metrics always sort last, in both directions: an unranked
      // keyword reads as 0 impressions but as no position at all, and sorting it
      // to the top of "best position" would be a lie.
      const manual = gscWatch.sortKey==='manual';
      const sorted=[...rows].sort((a,b)=>{
        // List order is the stored order, and a dragged row means nothing if the
        // table then re-sorts it away.
        if (manual) return a.idx-b.idx;
        // A just-added row has no keyword yet and no metrics to sort on, so it
        // pins to the top rather than falling in with the unranked rows.
        if (a.draft!==b.draft) return a.draft ? -1 : 1;
        const va=a[gscWatch.sortKey], vb=b[gscWatch.sortKey];
        const ma=(va==null||!isFinite(num(va))), mb=(vb==null||!isFinite(num(vb)));
        if (ma&&mb) return 0;
        if (ma) return 1;
        if (mb) return -1;
        return gscWatch.sortDir==='asc' ? num(va)-num(vb) : num(vb)-num(va);
      });
      const arrow=k=>gscWatch.sortKey===k?(gscWatch.sortDir==='asc'?' ▴':' ▾'):'';
      const th=(k,label,cls,tip)=>`<th class="watch-sort${cls?' '+cls:''}${gscWatch.sortKey===k?' active':''}" data-watch-key="${k}"${tip?` title="${esc(tip)}"`:''}>${label}${arrow(k)}</th>`;
      // The handle column's header is the way back to list order, so a metric
      // sort is never a dead end for someone who wants to rearrange.
      const gripHead = IS_ADMIN
        ? `<th class="watch-sort watch-grip-cell${manual?' active':''}" data-watch-key="manual" title="List order — drag rows to reorder">⇅</th>`
        : '';
      const head=`<thead><tr>${gripHead}<th class="left">Keyword</th>`
        + th('avg_position','Position · 13 wks', '', 'Impression-weighted average rank over the selected range. The spark line covers the last 13 weeks and rises as the rank improves.')
        + (compareStart ? th('delta_position','Δ Pos', '', 'Change in average rank against the comparison period. Positive means the keyword moved toward rank 1.') : '')
        + WATCH_COLS.map(c=>th(c.key,c.label)).join('')
        + (IS_ADMIN ? `<th></th>` : '') + `</tr></thead>`;
      const body=`<tbody>`+sorted.map(r=>{
        const kw=r.kw.trim(), variants=kw.endsWith('*');
        const kwTxt=kw.replace(/[*]$/,'').trim();
        const vTip=variants
          ? `Counts this keyword and its variants${r.query_count?' (' + count(r.query_count) + ' queries in this range)':''}`
          : '';
        const vTag=variants ? `<span class="watch-variants" title="${esc(vTip)}">+ variants</span>` : '';
        const posTxt=(r.avg_position==null)
          ? '<span class="watch-pos watch-pos-none">—</span>'
          : `<span class="watch-pos">${num(r.avg_position).toFixed(1)}</span>`;
        // Admin cells are the same read-only cells plus a click target, so the
        // list looks identical to what a client sees until you click into it.
        const editable=(field, inner) => IS_ADMIN
          ? `<span class="watch-editable" data-watch-edit="${field}" data-watch-i="${r.idx}" title="Click to edit">${inner}</span>`
          : inner;
        const kwCell = (IS_ADMIN && r.draft)
          ? watchCellInput('kw', r.idx, '')
          : editable('kw', `<span class="watch-kw" title="${esc(kw)}">${esc(kwTxt)}${vTag}</span>`);
        // The grip is focusable so the order can be changed from the keyboard
        // (Arrow keys) as well as dragged.
        const grip = !IS_ADMIN ? '' : (manual
          ? `<td class="watch-grip-cell"><span class="watch-grip" draggable="true" data-watch-grip="${r.idx}" tabindex="0" role="button" aria-label="Reorder ${esc(kwTxt||'this row')} — drag, or use the arrow keys">⠿</span></td>`
          : `<td class="watch-grip-cell"></td>`);
        return `<tr${r.draft ? ' class="watch-draft"' : ''} data-watch-row="${r.idx}">`
          + grip
          + `<td class="left">${kwCell}</td>`
          + `<td><span class="watch-spark">${watchSpark(r.series)}${posTxt}</span></td>`
          // "New" (what a null delta means elsewhere) would be wrong for a
          // keyword that has no rank at all yet -- that row gets a plain dash.
          + (compareStart ? `<td>${r.avg_position==null ? '—' : gscDelta(r.delta_position, r)}</td>` : '')
          + WATCH_COLS.map(c=>`<td>${r[c.key]==null ? '—' : c.format(r[c.key], r)}</td>`).join('')
          + (IS_ADMIN ? `<td class="watch-rm-cell"><button type="button" class="watch-rm" data-watch-rm="${r.idx}" aria-label="Remove ${esc(kwTxt||'this row')}">×</button></td>` : '')
          + `</tr>`;
      }).join('')+`</tbody>`;
      el.innerHTML=head+body;
    }
    let _gscWatchReqId=0;
    async function loadGscWatchlist() {
      const terms=watchTerms();
      gscWatch.rows={}; gscWatch.weekly={};
      // Nothing on the list and nobody who could add one: the whole section is
      // noise on a client's tab, so it stays out of the page rather than
      // explaining an empty table to them.
      const sec=document.getElementById('sec-gsc-watchlist');
      // Hide the editable wrapper, not just the section, so an empty watchlist
      // doesn't leave a stray edit bar behind on an admin's tab.
      const secUnit=sec&&(sec.closest('.ov-unit')||sec);
      if (secUnit) secUnit.hidden = !terms.length && !IS_ADMIN;
      if (!terms.length) {
        renderGscWatchTable();
        setStatus('gscWatchStatus', IS_ADMIN ? 'Add the keywords this site is being written for.' : '');
        return;
      }
      const reqId=++_gscWatchReqId;
      const el=document.getElementById('gscWatchTable');
      if (el) el.innerHTML=skelTable(IS_ADMIN ? 8 : 6, Math.min(6, terms.length));
      setStatus('gscWatchStatus','Loading…');
      const url=withCompare(withDates(GSC_WATCHLIST_API))+'&terms='+encodeURIComponent(terms.join(','));
      try {
        const p=await getJson(url);
        if (reqId!==_gscWatchReqId) return; // superseded by a newer range/keyword change
        (p.rows||[]).forEach(r=>{ gscWatch.rows[watchKey(r.term)]=r; });
        (p.weekly||[]).forEach(r=>{ const k=watchKey(r.term); (gscWatch.weekly[k]=gscWatch.weekly[k]||[]).push(r); });
        renderGscWatchTable();
        const ranked=Object.keys(gscWatch.rows).length;
        setStatus('gscWatchStatus', `${ranked} of ${terms.length} watched keyword${terms.length===1?'':'s'} earned impressions in this range.`);
      } catch(err) {
        if (reqId!==_gscWatchReqId) return;
        renderGscWatchTable();
        setStatus('gscWatchStatus', err.message||String(err), true);
      }
    }
    (function initWatchSort(){
      const el=document.getElementById('gscWatchTable'); if(!el) return;
      el.addEventListener('click', ev => {
        const th=ev.target.closest('th.watch-sort'); if(!th) return;
        const key=th.dataset.watchKey;
        // List order has one direction -- the stored one -- so clicking it is a
        // switch, not a toggle.
        if (key==='manual') { gscWatch.sortKey='manual'; gscWatch.sortDir='asc'; renderGscWatchTable(); return; }
        if (gscWatch.sortKey===key) gscWatch.sortDir = gscWatch.sortDir==='asc' ? 'desc' : 'asc';
        else {
          gscWatch.sortKey=key;
          const col=WATCH_COLS.find(c=>c.key===key);
          // Position and Δ Pos default ascending, so the first click surfaces
          // the best rank / the biggest sinker rather than the largest number.
          gscWatch.sortDir = col ? col.defDir : 'asc';
        }
        renderGscWatchTable();
      });
    })();
    // Editing is the list. An admin clicks a keyword or page cell, it becomes an
    // input in place, and blur/Enter commits -- there is no second copy of the
    // watchlist to keep in sync with what the table shows.
    let _watchSaveTimer=null;
    function saveWatchConfig() {
      clearTimeout(_watchSaveTimer);
      _watchSaveTimer=setTimeout(async () => {
        const text=gscWatchItems.filter(i=>i.kw.trim())
          .map(i=>i.page.trim() ? `${i.kw.trim()}|${i.page.trim()}` : i.kw.trim()).join('\n');
        try {
          const r=await fetch(GSC_WATCHLIST_CONFIG_API, {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body:JSON.stringify({watch_keywords:text})});
          const body=await r.json().catch(()=>({}));
          if (!r.ok||!body.ok) throw new Error((body&&body.detail&&(body.detail.error||body.detail))||r.statusText);
        } catch(err) { setStatus('gscWatchStatus','Save failed: '+(err.message||err), true); }
      }, 700);
    }
    // The input a cell turns into. Rendered by the table for a draft row, and
    // swapped in on click for an existing one.
    function watchCellInput(field, idx, value) {
      const ph = field==='kw' ? 'keyword' : 'page this keyword is for (optional)';
      return `<input class="watch-cell-in" type="text" data-watch-in="${field}" data-watch-i="${idx}" value="${esc(value||'')}" placeholder="${ph}" spellcheck="false">`;
    }
    function focusWatchInput(idx, field) {
      const el=document.getElementById('gscWatchTable'); if(!el) return;
      const inp=el.querySelector(`input[data-watch-in="${field}"][data-watch-i="${idx}"]`);
      if (inp) { inp.focus(); inp.select(); }
    }
    // Commit one cell. An existing row whose keyword is cleared is a removal --
    // a nameless row would just sit there unrankable.
    function commitWatchCell(idx, field, raw) {
      const item=gscWatchItems[idx]; if(!item) return;
      const val=String(raw||'').trim();
      const changed = item[field]!==val;
      item[field]=val;
      if (field==='kw' && !val) gscWatchItems.splice(idx,1);
      if (changed) { saveWatchConfig(); loadGscWatchlist(); }
      else renderGscWatchTable();
    }
    // New rows go to the TOP of the list: it is the keyword you are working on
    // now, and on a long list an appended row would land below the fold. In list
    // order that is also where it stays until it is dragged.
    function addWatchRow() {
      gscWatchItems.unshift({kw:'', page:''});
      renderGscWatchTable();
      focusWatchInput(0, 'kw');
    }
    // Move one row and persist -- no refetch, because the set of watched terms
    // is unchanged and the data behind each row is already in hand.
    function moveWatchItem(from, to) {
      if (from===to || from==null || to==null) return;
      if (from<0 || from>=gscWatchItems.length) return;
      to=Math.max(0, Math.min(gscWatchItems.length-1, to));
      const [item]=gscWatchItems.splice(from,1);
      gscWatchItems.splice(to,0,item);
      // Reordering only means something in list order, so a drag under a metric
      // sort switches the view to where the change is visible.
      gscWatch.sortKey='manual'; gscWatch.sortDir='asc';
      saveWatchConfig();
      renderGscWatchTable();
      const grip=document.querySelector(`[data-watch-grip="${to}"]`);
      if (grip) grip.focus();
    }
    // Bulk add. Two shapes people actually paste, told apart by whether the
    // second field looks like a page: "keyword, /page" is one row, and a line of
    // plain comma-separated keywords is one row each. "keyword|page" (the stored
    // format) always means one row, so a saved list can be pasted straight back.
    function parseWatchBulk(text) {
      const looksPage=v=>/^(https?:[/][/]|[/])/i.test(v);
      const out=[];
      String(text||'').split(/[\r\n]+/).forEach(line=>{
        const t=line.trim(); if(!t) return;
        if (t.includes('|')) {
          const bar=t.indexOf('|');
          const kw=t.slice(0,bar).trim();
          if (kw) out.push({kw, page:t.slice(bar+1).trim()});
          return;
        }
        const parts=t.split(',').map(v=>v.trim()).filter(Boolean);
        if (parts.length===2 && looksPage(parts[1])) { out.push({kw:parts[0], page:parts[1]}); return; }
        parts.forEach(v=>{ if (!looksPage(v)) out.push({kw:v, page:''}); });
      });
      return out;
    }
    // Adds what is new and says what it skipped -- pasting a list that overlaps
    // the current one is normal, and silently dropping half of it is not.
    function applyWatchBulk(text) {
      const parsed=parseWatchBulk(text);
      if (!parsed.length) { setStatus('gscWatchStatus','Nothing to add — paste one keyword per line, or comma-separated.', true); return false; }
      const seen=new Set(gscWatchItems.map(i=>watchKey(i.kw)).filter(Boolean));
      let added=0, skipped=0;
      parsed.forEach(it=>{
        const k=watchKey(it.kw);
        if (!k || seen.has(k)) { skipped++; return; }
        seen.add(k); gscWatchItems.push(it); added++;
      });
      if (!added) { setStatus('gscWatchStatus','Already on the watchlist — nothing added.'); return false; }
      saveWatchConfig(); loadGscWatchlist();
      const tail = skipped ? ` ${skipped} already on the list.` : '';
      setStatus('gscWatchStatus', `Added ${added} keyword${added===1?'':'s'}.${tail}`);
      return true;
    }
    (function initWatchEditing(){
      const table=document.getElementById('gscWatchTable'); if(!table) return;
      if (IS_ADMIN) {
        // Swap a cell for an input on click. Ignore clicks on the page link so
        // an admin can still follow it.
        table.addEventListener('click', ev => {
          if (ev.target.closest('a')) return;
          const rm=ev.target.closest('button[data-watch-rm]');
          if (rm) {
            gscWatchItems.splice(+rm.dataset.watchRm,1);
            saveWatchConfig(); loadGscWatchlist(); return;
          }
          const cell=ev.target.closest('[data-watch-edit]'); if(!cell) return;
          const field=cell.dataset.watchEdit, idx=+cell.dataset.watchI;
          const item=gscWatchItems[idx]; if(!item) return;
          const td=cell.closest('td'); if(!td) return;
          td.innerHTML=watchCellInput(field, idx, item[field]);
          focusWatchInput(idx, field);
        });
        table.addEventListener('keydown', ev => {
          const inp=ev.target.closest('input[data-watch-in]'); if(!inp) return;
          if (ev.key==='Enter') { ev.preventDefault(); inp.blur(); }
          else if (ev.key==='Escape') { ev.preventDefault(); inp.dataset.cancelled='1'; renderGscWatchTable(); }

        });
        table.addEventListener('focusout', ev => {
          const inp=ev.target.closest('input[data-watch-in]'); if(!inp) return;
          if (inp.dataset.cancelled) return;
          commitWatchCell(+inp.dataset.watchI, inp.dataset.watchIn, inp.value);
        });
      }
      if (IS_ADMIN) {
        // Drag to reorder. The row under the pointer shows where the dragged row
        // will land -- above it or below it, by which half you are over.
        const clearMarks=()=>table.querySelectorAll('tr.watch-drop-above,tr.watch-drop-below')
          .forEach(tr=>tr.classList.remove('watch-drop-above','watch-drop-below'));
        table.addEventListener('dragstart', ev => {
          const grip=ev.target.closest('[data-watch-grip]'); if(!grip) return;
          gscWatch.dragFrom=+grip.dataset.watchGrip;
          const tr=grip.closest('tr'); if (tr) tr.classList.add('watch-dragging');
          if (ev.dataTransfer) { ev.dataTransfer.effectAllowed='move'; ev.dataTransfer.setData('text/plain', String(gscWatch.dragFrom)); }
        });
        table.addEventListener('dragover', ev => {
          if (gscWatch.dragFrom==null) return;
          const tr=ev.target.closest('tr[data-watch-row]'); if(!tr) return;
          ev.preventDefault();
          if (ev.dataTransfer) ev.dataTransfer.dropEffect='move';
          const box=tr.getBoundingClientRect();
          const above=(ev.clientY-box.top) < box.height/2;
          clearMarks();
          tr.classList.add(above ? 'watch-drop-above' : 'watch-drop-below');
        });
        table.addEventListener('drop', ev => {
          if (gscWatch.dragFrom==null) return;
          const tr=ev.target.closest('tr[data-watch-row]');
          ev.preventDefault();
          const from=gscWatch.dragFrom;
          gscWatch.dragFrom=null;
          clearMarks();
          if (!tr) { renderGscWatchTable(); return; }
          const over=+tr.dataset.watchRow;
          const box=tr.getBoundingClientRect();
          const above=(ev.clientY-box.top) < box.height/2;
          // Dropping below a row that sits above the dragged one lands ON that
          // row's index; the splice removing the source first shifts the rest.
          let to=above ? over : over+1;
          if (from<to) to--;
          moveWatchItem(from, to);
        });
        table.addEventListener('dragend', () => {
          gscWatch.dragFrom=null;
          clearMarks();
          table.querySelectorAll('tr.watch-dragging').forEach(tr=>tr.classList.remove('watch-dragging'));
        });
        // Same move from the keyboard, for anyone not dragging with a mouse.
        table.addEventListener('keydown', ev => {
          const grip=ev.target.closest('[data-watch-grip]'); if(!grip) return;
          const from=+grip.dataset.watchGrip;
          if (ev.key==='ArrowUp') { ev.preventDefault(); moveWatchItem(from, from-1); }
          else if (ev.key==='ArrowDown') { ev.preventDefault(); moveWatchItem(from, from+1); }
          else if (ev.key==='Home') { ev.preventDefault(); moveWatchItem(from, 0); }
          else if (ev.key==='End') { ev.preventDefault(); moveWatchItem(from, gscWatchItems.length-1); }
        });
      }
      const addBtn=document.getElementById('gscWatchAdd');
      if (addBtn) addBtn.addEventListener('click', addWatchRow);
      const bulkBtn=document.getElementById('gscWatchBulkBtn');
      const bulk=document.getElementById('gscWatchBulk');
      const bulkText=document.getElementById('gscWatchBulkText');
      const closeBulk=(refocus)=>{
        if (!bulk || bulk.hidden) return;
        bulk.hidden=true;
        if (bulkBtn) { bulkBtn.setAttribute('aria-expanded','false'); if (refocus) bulkBtn.focus(); }
      };
      if (bulkBtn && bulk) bulkBtn.addEventListener('click', () => {
        const open=bulk.hidden;
        bulk.hidden=!open;
        bulkBtn.setAttribute('aria-expanded', open?'true':'false');
        if (open && bulkText) bulkText.focus();
      });
      // Click anywhere else closes it, the way a popover should. mousedown, not
      // click, so it closes as soon as you press outside it.
      document.addEventListener('mousedown', ev => {
        if (!bulk || bulk.hidden) return;
        if (ev.target.closest('#gscWatchBulk') || ev.target.closest('#gscWatchBulkBtn')) return;
        closeBulk();
      });
      const bulkAdd=document.getElementById('gscWatchBulkAdd');
      if (bulkAdd && bulkText) bulkAdd.addEventListener('click', () => {
        if (applyWatchBulk(bulkText.value)) { bulkText.value=''; closeBulk(true); }
      });
      const bulkCancel=document.getElementById('gscWatchBulkCancel');
      if (bulkCancel) bulkCancel.addEventListener('click', () => { if (bulkText) bulkText.value=''; closeBulk(true); });
      // Ctrl/Cmd+Enter submits, so a long paste doesn't need a trip to the mouse.
      if (bulkText) bulkText.addEventListener('keydown', ev => {
        if (ev.key==='Enter' && (ev.metaKey||ev.ctrlKey)) { ev.preventDefault(); if (applyWatchBulk(bulkText.value)) { bulkText.value=''; closeBulk(true); } }
        else if (ev.key==='Escape') { ev.preventDefault(); closeBulk(true); }
      });
    })();
    // Inline tag editors for branded roots + target keywords. Add with Enter or
    // comma, remove with the chip ×; changes re-filter live and auto-save.
    let _kwSaveTimer=null;
    function saveKwConfig() {
      clearTimeout(_kwSaveTimer);
      _kwSaveTimer=setTimeout(async () => {
        setStatus('gscKwStatus','Saving…');
        try {
          const r=await fetch(GSC_KEYWORD_CONFIG_API, {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body:JSON.stringify({branded_roots:gscBrandedRoots.join('\n'), target_keywords:gscTargetKeywords.join('\n'), branded_exclude:gscBrandedExclude.join('\n'), target_exclude:gscTargetExclude.join('\n')})});
          const body=await r.json().catch(()=>({}));
          if (!r.ok||!body.ok) throw new Error((body&&body.detail&&(body.detail.error||body.detail))||r.statusText);
          setStatus('gscKwStatus','Saved.'); setTimeout(()=>{const el=document.getElementById('gscKwStatus'); if(el&&el.textContent==='Saved.') el.textContent='';}, 2000);
        } catch(err) { setStatus('gscKwStatus','Save failed: '+(err.message||err), true); }
      }, 700);
    }
    function makeTagEditor(containerId, label, getTerms, setTerms) {
      const el=document.getElementById(containerId); if(!el) return;
      function commit(v) { v=v.trim(); if(!v) return false; const t=getTerms().slice(); if(t.some(x=>x.toLowerCase()===v.toLowerCase())) return false; t.push(v); setTerms(t); return true; }
      function render() {
        const terms=getTerms();
        el.innerHTML=`<span class="tag-editor-label">${esc(label)}</span>`
          + terms.map((t,i)=>`<span class="tag-chip">${esc(t)}<button type="button" data-i="${i}" aria-label="Remove ${esc(t)}">×</button></span>`).join('')
          + `<input class="tag-input" type="text" placeholder="${terms.length?'Add…':'Add a '+label.toLowerCase().replace(/s$/,'')+'…'}">`;
        el.querySelectorAll('.tag-chip button').forEach(btn=>btn.addEventListener('click',()=>{
          const t=getTerms().slice(); t.splice(+btn.dataset.i,1); setTerms(t); render(); renderGscKeywordTables(); saveKwConfig();
        }));
        const inp=el.querySelector('.tag-input');
        const add=(refocus)=>{ if(commit(inp.value)){ render(); renderGscKeywordTables(); saveKwConfig(); if(refocus){const ni=el.querySelector('.tag-input'); if(ni) ni.focus();} } else { inp.value=''; } };
        inp.addEventListener('keydown',e=>{ if(e.key==='Enter'||e.key===','){ e.preventDefault(); add(true); } else if(e.key==='Backspace'&&!inp.value){ const t=getTerms().slice(); if(t.length){ t.pop(); setTerms(t); render(); renderGscKeywordTables(); saveKwConfig(); const ni=el.querySelector('.tag-input'); if(ni) ni.focus(); } } });
        inp.addEventListener('blur',()=>add(false));
      }
      render();
    }
    makeTagEditor('gscBrandedTags','Include roots', ()=>gscBrandedRoots, t=>{gscBrandedRoots=t;});
    makeTagEditor('gscBrandedExcludeTags','Exclude terms', ()=>gscBrandedExclude, t=>{gscBrandedExclude=t;});
    makeTagEditor('gscTargetTags','Include keywords', ()=>gscTargetKeywords, t=>{gscTargetKeywords=t;});
    makeTagEditor('gscTargetExcludeTags','Exclude terms', ()=>gscTargetExclude, t=>{gscTargetExclude=t;});
    // Toggle the include/exclude tag editors from the "Edit" button in each
    // keyword panel's headline row (keeps the editors tucked away by default).
    document.querySelectorAll('.kw-edit-btn[data-kw-edit]').forEach(btn=>{
      btn.addEventListener('click',()=>{
        const box=document.getElementById(btn.dataset.kwEdit); if(!box) return;
        const open=box.hidden; box.hidden=!open; btn.setAttribute('aria-expanded', open?'true':'false');
        if(open){const inp=box.querySelector('.tag-input'); if(inp) inp.focus();}
      });
    });

    // ---- SEMrush (domain-level snapshot — not date-range scoped) ----
    function renderSemrushKpis(ov, bl, series, aio) {
      ov = ov || {}; bl = bl || {}; series = series || []; aio = aio || {};
      const first = series.length ? series[0] : null;
      const last  = series.length ? series[series.length-1] : null;
      // Delta is first-vs-last over the ~90-day daily series (SEMrush snapshots
      // are domain-level, not date-range scoped). Spark draws the same series.
      const sdelta = (key,dir) => (series.length>=2 && first && last) ? summaryDeltaHtml(last[key], first[key], dir) : '';
      const sspark = (key,color) => sparkSvg(series.map(d=>num(d[key])), color);
      const aioFoot = (aio.keywords_cited!=null)
        ? `<span class="cmp-delta flat" title="ranked keywords citing this domain in an AI Overview">${count(aio.keywords_cited)} cited</span>`
        : '';
      // [label, value, foot-delta html, spark html]. AI Overview keyword count
      // has no "good" direction (more AIOs can cannibalize clicks), so it shows
      // the cited count instead of a colored delta.
      const cards = [
        ['Organic Traffic (est.)', count(ov.organic_traffic), sdelta('organic_traffic','up'), sspark('organic_traffic','#1769aa')],
        ['Organic Keywords', count(ov.organic_keywords), sdelta('organic_keywords','up'), sspark('organic_keywords','#7c3aed')],
        ['Authority Score', bl.authority_score != null ? bl.authority_score + '/100' : '—', sdelta('authority_score','up'), sspark('authority_score','#0a7f3f')],
        ['Referring Domains', count(bl.referring_domains), sdelta('referring_domains','up'), sspark('referring_domains','#d97706')],
        ['Total Backlinks', count(bl.total_backlinks), sdelta('total_backlinks','up'), sspark('total_backlinks','#0891b2')],
        ['AI Overview Keywords', count(aio.keywords_with_aio), aioFoot, sspark('ai_overview_keywords','#a855f7')],
      ];
      document.getElementById('semrushKpis').innerHTML = cards.map(([label,val,delta,spark]) =>
        `<div class="card"><div class="card-title">${label}</div><div class="card-value">${val}</div><div class="card-foot">${delta}${spark}</div></div>`).join('');
    }
    async function loadSemrush() {
      const host=document.getElementById('semrushKpis');
      if (!host) return;   // section omitted when SEMrush isn't connected
      setStatus('semrushStatus','Loading…');
      host.innerHTML = skelCards(4);
      try {
        const p = await getJson(SEMRUSH_API);
        if (!p || !p.domain) {
          renderSemrushKpis({}, {}, [], {});
          setStatus('semrushStatus','No SEMrush data yet.');
          return;
        }
        renderSemrushKpis(p.overview, p.backlinks, p.series, p.ai_overview);
        setStatus('semrushStatus', esc(p.domain));
      } catch(err) {
        setStatus('semrushStatus', err.message||String(err), true);
      }
    }
    