"""HTML renderer for the standalone Email Performance page (HubSpot).

Styled to match Overview / Campaign Explorer rather than inventing its own
look: the same metric cards, section card, and table chrome (sticky uppercase
header, right-aligned numbers, click-a-heading-to-sort), so the page reads as
part of one dashboard. A row of metric cards summarises the current selection,
a Trends chart plots it over time, then a single Performance card lists send
date / delivery / bounce / open / click / click-to-open / unsub per email,
sorted by send date (newest first) until a column heading says otherwise.

Two things make the numbers mean something rather than just sit there:

* **Every rate is compared to the client's own average** across every synced
  email — on the tiles ("+1.2 pts vs all emails") and under the open and click
  figures in each row — so an email that over- or under-performed is visible
  without anyone doing the arithmetic.
* **Deliverability and content engagement are on the page**, not just opens:
  bounce rate (over sends) says whether the list is healthy, and click-to-open
  (clicks over opens) separates "the subject line worked" from "the email
  worked".

Choosing what to look at works two ways: quick send-date ranges in the page
header ("30 days", "90 days", "12 months", "All time") for the common case, and
a compact "Choose emails" popover checklist for picking exact sends. The table
can be exported as a CSV of whatever is on screen.

All interaction is client-side: the full email set is embedded once as JSON and
the tiles, chart, picker, and table are built from it, so selecting, searching,
sorting, and removing never hit the server. An admin's chosen set can be saved
portal-wide from the popover (see save_email_performance_selection); it then
seeds the view for every viewer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc
from dashboard.utils.formatting import json_for_html_script as _json
from hubspot_reports_service import EmailPerformanceReport

# Default number of most-recent emails pre-selected into the table on load.
_DEFAULT_SELECTED = 5

# Send-date ranges offered in the page header. `days` of 0 means "everything".
_RANGES: tuple[tuple[str, int], ...] = (
    ("30 days", 30),
    ("90 days", 90),
    ("12 months", 365),
    ("All time", 0),
)

_EXTRA_CSS = """
/* This page borrows Overview / Campaign Explorer's visual language wholesale --
   the same section card, metric cards, and table chrome (sticky uppercase
   header, right-aligned numbers, clickable sort headers) -- so it reads as one
   dashboard rather than a page of its own. The few tones the shared dashboard
   hardcodes (and the shell theme has no variable for) are declared once here. */
.ep-wrap { max-width:1320px; margin:0 auto; --line-soft:#eff3f8; --th-bg:#f4f7fb; --th-hover:#e9eef5; --row-hover:#f7faff; }
.ep-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px 20px; margin-bottom:4px; }
.ep-title { font-size:1.6rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.01em; }
.ep-sub { font-size:.9rem; color:var(--muted); margin:6px 0 0; }

/* Send-date range pills. These drive both the chart and the table, which is why
   they sit in the page header rather than inside one card. A range with nothing
   in it is disabled rather than hidden, so an empty window reads as "nothing was
   sent then" instead of a control that silently did nothing. */
.ep-ranges { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding-top:6px; }
.ep-ranges-label { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; font-weight:800; color:var(--muted); }
.ep-range-group { display:inline-flex; border:1px solid var(--border); border-radius:9px; overflow:hidden; background:var(--panel); }
.ep-range { border:0; border-right:1px solid var(--border); background:transparent; color:var(--text); font-size:.78rem; font-weight:650; padding:7px 12px; cursor:pointer; }
.ep-range:last-child { border-right:0; }
.ep-range:hover:not(:disabled):not(.active) { background:var(--surface); }
.ep-range.active { background:var(--accent); color:#fff; }
.ep-range:disabled { color:#b3bfcd; cursor:default; }

/* Metric cards -- Overview's .cards / .card, down to the 3px accent top rule. */
.ep-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin:20px 0 16px; }
.ep-tile { background:var(--panel); border:1px solid var(--line-soft); border-top:3px solid var(--accent); border-radius:9px; padding:13px 14px 14px; }
.ep-tile-label { color:var(--muted); font-size:.65rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }
.ep-tile-value { margin-top:7px; font-size:1.5rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.ep-tile-sub { margin-top:6px; font-size:.72rem; color:var(--muted); font-weight:600; }
/* Better/worse than this client's own average. Green is always "good for you",
   which for unsubscribes and bounces means the number went down. */
.ep-tile-sub.up { color:var(--ok); }
.ep-tile-sub.down { color:var(--err); }

/* Section card -- the dashboard's <section>: same radius, padding and shadow. */
.ep-card { background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:18px 20px 20px; margin-bottom:16px; box-shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }
.ep-card-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 16px; flex-wrap:wrap; }
.ep-card-head-titles { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.ep-card h2 { font-size:1.05rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.005em; }
.ep-card-note { font-size:.78rem; color:var(--muted); max-width:64ch; }
.ep-note { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:12px; padding:13px 16px; font-size:.85rem; margin-bottom:20px; }

/* Low-profile picker: a compact button in the card header that opens a popover checklist. */
.ep-tools { display:flex; align-items:center; gap:8px; }
.ep-picker { position:relative; }
.ep-pick-btn { display:inline-flex; align-items:center; gap:7px; border:1px solid var(--border); background:var(--panel); color:var(--text); font-size:.8rem; font-weight:650; padding:7px 12px; border-radius:9px; cursor:pointer; }
.ep-pick-btn:hover { background:var(--surface); border-color:#cbd5e1; }
.ep-pick-btn svg { width:15px; height:15px; opacity:.7; }
.ep-pick-badge { background:var(--accent); color:#fff; font-size:.7rem; font-weight:700; border-radius:999px; padding:1px 7px; line-height:1.5; }
/* Wide enough to read a full email name without hovering, and tall enough to
   tick a run of emails without scrolling on a normal laptop screen. */
.ep-pop { position:absolute; z-index:40; top:calc(100% + 6px); right:0; width:620px; max-width:92vw; background:var(--panel); border:1px solid var(--border); border-radius:12px; box-shadow:var(--shadow); padding:14px; }
.ep-pop[hidden] { display:none; }
.ep-pop-head { display:flex; align-items:center; justify-content:space-between; margin:0 0 9px; }
.ep-pop-head span { font-size:.82rem; font-weight:700; color:var(--navy); }
.ep-pop-x { border:0; background:transparent; color:var(--muted); font-size:1.15rem; line-height:1; cursor:pointer; padding:0 2px; }
.ep-pop-x:hover { color:var(--text); }
.ep-search { width:100%; box-sizing:border-box; padding:8px 12px 8px 34px; border:1px solid var(--border); border-radius:9px; background-color:var(--surface);
  color:var(--text); font-size:.85rem; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M21 21l-4.3-4.3'/%3E%3C/svg%3E");
  background-repeat:no-repeat; background-position:11px center; }
.ep-search:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(11,92,171,.15); background-color:var(--panel); }
.ep-pop-bar { display:flex; align-items:center; justify-content:space-between; gap:10px; margin:9px 2px 8px; }
.ep-count { font-size:.75rem; color:var(--muted); font-weight:650; }
.ep-actions { display:flex; gap:6px; flex-wrap:wrap; }
.ep-btn { border:1px solid var(--border); background:var(--panel); color:var(--accent); font-size:.72rem; font-weight:650; padding:4px 9px; border-radius:7px; cursor:pointer; }
.ep-btn:hover { background:var(--surface); }
.ep-list { max-height:min(58vh,520px); overflow-y:auto; border:1px solid var(--border); border-radius:10px; }
.ep-opt { display:flex; align-items:flex-start; gap:10px; padding:9px 12px; border-bottom:1px solid #f1f4f8; cursor:pointer; font-size:.83rem; }
.ep-opt:last-child { border-bottom:0; }
.ep-opt:hover { background:var(--surface); }
.ep-opt input { width:15px; height:15px; margin-top:2px; accent-color:var(--accent); flex-shrink:0; cursor:pointer; }
.ep-opt-main { min-width:0; flex:1; }
/* Names wrap instead of truncating -- the popover is wide enough that a long
   name costs at most a second line, and nothing has to be hovered to be read. */
.ep-opt-name { color:var(--text); font-weight:600; line-height:1.35; overflow-wrap:anywhere; }
.ep-opt-sub { color:var(--muted); font-size:.72rem; margin-top:2px; }
.ep-list-empty { padding:16px; text-align:center; color:var(--muted); font-size:.8rem; }
.ep-pop-foot { display:flex; align-items:center; gap:10px; margin-top:10px; }
.ep-save-btn { border:1px solid var(--accent); background:var(--accent); color:#fff; font-size:.76rem; font-weight:650; padding:6px 12px; border-radius:8px; cursor:pointer; }
.ep-save-btn:hover { filter:brightness(1.05); }
.ep-save-btn:disabled { opacity:.6; cursor:default; }
.ep-save-status { font-size:.74rem; font-weight:600; color:var(--muted); }
.ep-save-status.ok { color:var(--ok); }
.ep-save-status.err { color:var(--err); }

/* Trend chart -- Chart.js on the shared card, one line per metric. Deliveries
   are a count and the rates are percentages, so they ride separate axes
   (counts left, rates right); the legend is clickable, which is how you isolate
   the unsub line from the much larger open-rate line -- and how you bring in the
   click-to-open and bounce lines, which start hidden so the chart opens readable. */
.ep-chart-host { position:relative; height:300px; }
.ep-chart-empty { color:var(--muted); font-size:.86rem; padding:26px 4px; text-align:center; }

/* Table -- the dashboard's table chrome: bordered scroller, sticky uppercase
   header, numbers right-aligned by default, .left for the label column. */
.ep-table-wrap { overflow:auto; border:1px solid var(--line-soft); border-radius:9px; }
.ep-table { border-collapse:separate; border-spacing:0; width:100%; min-width:980px; font-size:.86rem; }
.ep-table th, .ep-table td { padding:10px 13px; border-bottom:1px solid var(--line-soft); text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.ep-table th { background:var(--th-bg); color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; position:sticky; top:0; z-index:2; }
.ep-table th.left, .ep-table td.left { text-align:left; font-variant-numeric:normal; }
.ep-table td.left { white-space:normal; }
.ep-table th.left, .ep-table td.left { min-width:250px; max-width:340px; }
/* The email name stays put while the numbers scroll sideways -- with ten columns
   a row is otherwise unreadable once you scroll past the label. */
.ep-table th.left, .ep-table td.left { position:sticky; left:0; background:var(--panel); box-shadow:1px 0 0 var(--line-soft); }
.ep-table th.left { z-index:3; background:var(--th-bg); }
.ep-table td.left { z-index:1; }
.ep-table tbody tr:last-child td { border-bottom:0; }
.ep-table tbody tr:hover td, .ep-table tbody tr:hover td.left { background:var(--row-hover); }
/* Sortable headers, styled like the Explorer's th.expl-sort. */
.ep-table th.ep-sort { cursor:pointer; user-select:none; transition:background .12s,color .12s; }
.ep-table th.ep-sort:hover { background:var(--th-hover); color:#33455e; }
.ep-table th.ep-sort.active { color:var(--accent); }
.ep-email-name { font-weight:700; color:var(--navy); }
.ep-email-meta { color:var(--muted); font-size:.74rem; margin-top:2px; font-weight:600; }
.ep-type { display:inline-block; margin-left:7px; padding:1px 7px; border-radius:999px; background:var(--surface); border:1px solid var(--border);
  color:var(--muted); font-size:.63rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; vertical-align:middle; }
/* Per-row comparison to this client's own average, in percentage points. */
.ep-delta { display:block; margin-top:2px; font-size:.68rem; font-weight:700; color:var(--muted); }
.ep-delta.up { color:var(--ok); }
.ep-delta.down { color:var(--err); }
.ep-remove { border:0; background:transparent; color:var(--muted); cursor:pointer; font-size:1.1rem; line-height:1; padding:0 2px; }
.ep-remove:hover { color:var(--err); }
.ep-empty { color:var(--muted); font-size:.86rem; padding:26px 4px; text-align:center; }
.ep-sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
.ep-empty .ep-btn { margin-left:6px; }
.ep-backdrop { display:none; }
@media (max-width: 720px) {
  .ep-head { flex-direction:column; }
  .ep-card-head { align-items:flex-start; }
  /* The popover hangs off a button that sits near the middle of a phone screen,
     so anchoring 620px to its right edge pushed most of the list off the left of
     the viewport. On a phone it becomes a sheet pinned to the bottom of the
     screen instead, which is where a thumb already is. */
  .ep-pop { position:fixed; top:auto; right:10px; bottom:10px; left:10px; width:auto; max-width:none;
    z-index:60; box-shadow:0 -2px 12px rgba(10,37,64,.10), 0 12px 40px rgba(10,37,64,.22); }
  /* The sheet itself must not scroll -- the list inside it does -- or the search
     box and the buttons scroll away from under the thumb. */
  .ep-list { max-height:44vh; }
  .ep-backdrop { display:block; position:fixed; inset:0; z-index:59; background:rgba(10,37,64,.34); }
  .ep-backdrop[hidden] { display:none; }
}
"""

# All interaction is client-side; the email set is read from the JSON script tag.
# Plain (non-f) string, so literal braces need no doubling.
_EP_JS = """
<script>
(function () {
  var dataEl = document.getElementById('ep-emails');
  if (!dataEl) return;
  var emails;
  try { emails = JSON.parse(dataEl.textContent || '[]'); }
  catch (e) { return; }
  var byId = {};
  emails.forEach(function (e) { byId[e.id] = e; });

  var listEl     = document.getElementById('ep-list');
  var tbodyEl    = document.getElementById('ep-tbody');
  var searchEl   = document.getElementById('ep-search');
  var popCountEl = document.getElementById('ep-pop-count');
  var badgeEl    = document.getElementById('ep-pick-badge');
  var emptyEl    = document.getElementById('ep-empty');
  if (!listEl || !tbodyEl) return;

  // An admin's saved selection (email IDs) wins over the built-in default of the
  // N most-recent emails; IDs no longer in the synced set are dropped. Empty
  // saved set falls back to the default so a fresh client still sees a table.
  var savedEl = document.getElementById('ep-saved');
  var savedIds = [];
  if (savedEl) { try { savedIds = JSON.parse(savedEl.textContent || '[]'); } catch (e) { savedIds = []; } }
  var validSaved = savedIds.filter(function (id) { return byId[id]; });

  // `selected` is the *set* of emails in the table; the order they appear in
  // is the sort below, so ticking an email drops it into the sorted position
  // rather than at the bottom.
  var selected = validSaved.length
    ? validSaved.slice()
    : emails.slice(0, %DEFAULT%).map(function (e) { return e.id; });

  // Click a column header to sort the table. Send date descending is the
  // default, which is the order the emails arrive in and the order the default
  // selection (the N most recent) implies.
  var sort = { key: 'date', dir: 'desc' };
  var SORT_DIRS = { name: 'asc', date: 'desc', sent: 'desc', deliveries: 'desc',
                    bounce: 'desc', open: 'desc', click: 'desc', ctor: 'desc',
                    unsub: 'desc' };

  // Every rate on this page is a ratio of two raw counters, declared once here
  // so the tiles, the table, the sorting and the chart can never disagree about
  // what "open rate" means. Bounces are over *sends* (a bounce is precisely a
  // send that never became a delivery); click-to-open is over opens, which is
  // what separates "the subject line worked" from "the email worked".
  var RATES = {
    bounce: { num: '_bounces', den: '_sent',      dp: 2, better: 'low'  },
    open:   { num: '_opens',   den: '_delivered', dp: 1, better: 'high' },
    click:  { num: '_clicks',  den: '_delivered', dp: 1, better: 'high' },
    ctor:   { num: '_clicks',  den: '_opens',     dp: 1, better: 'high' },
    unsub:  { num: '_unsub',   den: '_delivered', dp: 2, better: 'low'  }
  };

  function rate(num, den) {
    var d = Number(den || 0);
    return d > 0 ? Number(num || 0) / d : -1;   // no denominator sorts below 0%
  }
  function emailRate(e, key) {
    var r = RATES[key];
    return rate(e[r.num], e[r.den]);
  }
  // Weighted rate across a set of emails: sum the numerators, sum the
  // denominators, divide -- so a 20k send counts for more than a 200 send,
  // which is what "our average open rate" actually means.
  function aggRate(ids, key) {
    var r = RATES[key], num = 0, den = 0;
    ids.forEach(function (id) {
      var e = byId[id]; if (!e) return;
      num += Number(e[r.num] || 0); den += Number(e[r.den] || 0);
    });
    return den > 0 ? 100 * num / den : null;    // percent, or null when unknown
  }

  var allIds = emails.map(function (e) { return e.id; });
  // This client's own baseline: every synced email, weighted. Tiles and rows are
  // measured against it, which is the whole point -- an open rate means nothing
  // until you know what this list usually does.
  var baseline = {};
  Object.keys(RATES).forEach(function (k) { baseline[k] = aggRate(allIds, k); });
  var baselineCount = allIds.length;

  // Sort value per column: strings for the two text columns, numbers for the
  // rest. Rates are recomputed from the raw counts so they sort by their true
  // value rather than by the rounded string in the cell.
  function sortVal(e, key) {
    switch (key) {
      case 'name':       return String(e.name || '').toLowerCase();
      case 'date':       return String(e._ts || '');
      case 'sent':       return Number(e._sent || 0);
      case 'deliveries': return Number(e._delivered || 0);
      default:           return RATES[key] ? emailRate(e, key) : 0;
    }
  }
  function sortedSelection() {
    var ids = selected.filter(function (id) { return byId[id]; });
    var sign = sort.dir === 'asc' ? 1 : -1;
    return ids.sort(function (a, b) {
      var va = sortVal(byId[a], sort.key), vb = sortVal(byId[b], sort.key);
      if (va < vb) return -sign;
      if (va > vb) return sign;
      // Stable tie-break so equal values never shuffle between renders.
      return String(byId[a].name || '').localeCompare(String(byId[b].name || ''));
    });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function fmtInt(n) { try { return Number(n || 0).toLocaleString('en-US'); } catch (e) { return String(n || 0); } }
  function setText(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }

  // "+1.2 pts" / "-0.30 pts" against the all-email baseline, coloured by whether
  // that direction is good news for the metric (fewer unsubscribes is good).
  function delta(value, key) {
    var base = baseline[key];
    if (value == null || base == null) return { text: '', cls: '' };
    var r = RATES[key], diff = value - base;
    var eps = r.dp >= 2 ? 0.005 : 0.05;
    if (Math.abs(diff) < eps) return { text: 'in line with all ' + baselineCount, cls: '' };
    var sign = diff > 0 ? '+' : '−';
    return {
      text: sign + Math.abs(diff).toFixed(r.dp) + ' pts',
      cls: (diff > 0) === (r.better === 'high') ? 'up' : 'down'
    };
  }
  function fmtRate(value, key) {
    return value == null ? '—' : value.toFixed(RATES[key].dp) + '%';
  }

  // Hero tiles summarise the current selection (rates weighted by their
  // denominators, so they mean the same thing as the per-row rates) and say how
  // that selection compares with every email this client has sent.
  function renderTiles() {
    var del = 0;
    selected.forEach(function (id) { var e = byId[id]; if (e) del += Number(e._delivered || 0); });
    setText('ep-kpi-count', String(selected.length));
    setText('ep-kpi-delivered', selected.length ? fmtInt(del) : '—');
    Object.keys(RATES).forEach(function (key) {
      var v = selected.length ? aggRate(selected, key) : null;
      setText('ep-kpi-' + key, fmtRate(v, key));
      var sub = document.getElementById('ep-kpi-' + key + '-sub');
      if (!sub) return;
      var d = delta(v, key);
      sub.className = 'ep-tile-sub' + (d.cls ? ' ' + d.cls : '');
      sub.textContent = !selected.length ? 'nothing selected'
        : selected.length === baselineCount ? 'across all ' + baselineCount + ' emails'
        : d.text ? (d.cls ? d.text + ' vs all emails' : d.text + ' emails')
        : 'no data yet';
    });
  }

  // Trend chart: one point per selected email, always in send-date order
  // (independent of the table's sort), so the x axis reads left-to-right as
  // time. Deliveries are a count and the rest are percentages, so deliveries
  // get the left axis and the rates share the right one.
  var chartHost  = document.getElementById('ep-chart-host');
  var chartEl    = document.getElementById('ep-chart');
  var chartEmpty = document.getElementById('ep-chart-empty');
  var chart = null;

  function chartOrder() {
    return selected.filter(function (id) { return byId[id]; }).sort(function (a, b) {
      var ta = String(byId[a]._ts || ''), tb = String(byId[b]._ts || '');
      if (ta < tb) return -1;
      if (ta > tb) return 1;
      return String(byId[a].name || '').localeCompare(String(byId[b].name || ''));
    });
  }

  function ratePct(e, key) {
    var v = emailRate(e, key);
    return v < 0 ? null : 100 * v;   // no denominator = gap in the line
  }

  function lineFor(label, color, data, axis, dp, suffix, hidden) {
    return {
      label: label, data: data, borderColor: color, backgroundColor: color,
      yAxisID: axis, borderWidth: 2, tension: 0.3, pointRadius: 3,
      pointHoverRadius: 5, spanGaps: true, hidden: !!hidden, _dp: dp, _suffix: suffix,
    };
  }

  function renderChart() {
    if (!chartEl || !window.Chart) { if (chartHost) chartHost.style.display = 'none'; return; }
    var ids = chartOrder();
    if (chartEmpty) chartEmpty.style.display = ids.length ? 'none' : '';
    if (chartHost) chartHost.style.display = ids.length ? '' : 'none';
    if (chart) { chart.destroy(); chart = null; }
    if (!ids.length) return;

    var rows = ids.map(function (id) { return byId[id]; });
    function series(key) { return rows.map(function (e) { return ratePct(e, key); }); }
    var cfg = {
      type: 'line',
      data: {
        labels: rows.map(function (e) { return e.date; }),
        datasets: [
          lineFor('Deliveries', '#1d6fd0', rows.map(function (e) { return Number(e._delivered || 0); }), 'yCount', 0, ''),
          lineFor('Open rate', '#0a7f3f', series('open'), 'yRate', 1, '%'),
          lineFor('Click rate', '#7c3aed', series('click'), 'yRate', 2, '%'),
          lineFor('Unsub rate', '#d6336c', series('unsub'), 'yRate', 2, '%'),
          // Off by default: six lines at once is unreadable, and these two are
          // the ones you go looking for rather than glance at.
          lineFor('Click-to-open', '#0891b2', series('ctor'), 'yRate', 1, '%', true),
          lineFor('Bounce rate', '#b45309', series('bounce'), 'yRate', 2, '%', true),
        ],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false }, border: { display: false },
               ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
          yCount: { position: 'left', beginAtZero: true, grid: { color: '#f1f4f9' },
                    border: { display: false }, title: { display: true, text: 'Deliveries' },
                    ticks: { maxTicksLimit: 5, callback: function (v) { return fmtInt(v); } } },
          yRate: { position: 'right', beginAtZero: true, grid: { display: false },
                   border: { display: false }, title: { display: true, text: 'Rate' },
                   ticks: { maxTicksLimit: 5, callback: function (v) { return v + '%'; } } },
        },
        plugins: {
          legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8, padding: 14 } },
          tooltip: {
            backgroundColor: '#0b1020', titleColor: '#e8eefc', bodyColor: '#e8eefc',
            padding: 9, cornerRadius: 8, boxPadding: 4, usePointStyle: true,
            callbacks: {
              // The date label alone doesn't say which email a point is, so the
              // tooltip leads with the email's name.
              title: function (items) {
                var e = rows[items[0].dataIndex];
                return e ? [e.name, e.date] : '';
              },
              label: function (c) {
                var ds = c.dataset;
                if (c.parsed.y == null) return ds.label + ': —';
                var v = ds._suffix === '%' ? c.parsed.y.toFixed(ds._dp) : fmtInt(c.parsed.y);
                return ds.label + ': ' + v + ds._suffix;
              },
            },
          },
        },
      },
    };
    chart = new Chart(chartEl.getContext('2d'), cfg);
  }

  // A rate cell, with its distance from the client's own average underneath.
  function rateCell(e, key, withDelta) {
    var v = emailRate(e, key);
    var pctText = v < 0 ? '—' : (100 * v).toFixed(RATES[key].dp) + '%';
    var html = pctText;
    if (withDelta && v >= 0) {
      var d = delta(100 * v, key);
      if (d.text && d.cls) {
        html += '<span class="ep-delta ' + d.cls + '" title="Compared with the ' +
                'average across all ' + baselineCount + ' emails">' + esc(d.text) + '</span>';
      }
    }
    return '<td>' + html + '</td>';
  }

  function renderTable() {
    tbodyEl.innerHTML = '';
    if (!selected.length) {
      if (emptyEl) emptyEl.style.display = '';
    } else {
      if (emptyEl) emptyEl.style.display = 'none';
      sortedSelection().forEach(function (id) {
        var e = byId[id];
        var tr = document.createElement('tr');
        tr.innerHTML =
          '<td class="left"><div class="ep-email-name">' + esc(e.name) +
            (e.type ? '<span class="ep-type">' + esc(e.type) + '</span>' : '') + '</div>' +
            (e.subject ? '<div class="ep-email-meta">' + esc(e.subject) + '</div>' : '') +
          '</td>' +
          '<td>' + esc(e.date) + '</td>' +
          '<td>' + esc(e.sent) + '</td>' +
          '<td>' + esc(e.deliveries) + '</td>' +
          rateCell(e, 'bounce', false) +
          rateCell(e, 'open', true) +
          rateCell(e, 'click', true) +
          rateCell(e, 'ctor', false) +
          rateCell(e, 'unsub', false) +
          '<td><button type="button" class="ep-remove" data-remove="' + esc(e.id) +
            '" title="Remove from this table" aria-label="Remove ' + esc(e.name) +
            ' from this table">&times;</button></td>';
        tbodyEl.appendChild(tr);
      });
    }
    renderTiles();
    renderChart();
    updateCount();
    renderSortHeaders();
    renderRanges();
  }

  // The header cells are server-rendered; this only paints the active column
  // and its direction arrow.
  var theadEl = document.getElementById('ep-thead');
  function renderSortHeaders() {
    if (!theadEl) return;
    theadEl.querySelectorAll('th.ep-sort').forEach(function (th) {
      var active = th.getAttribute('data-key') === sort.key;
      th.classList.toggle('active', active);
      th.setAttribute('aria-sort', active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      var arrow = th.querySelector('.ep-arrow');
      if (arrow) arrow.textContent = active ? (sort.dir === 'asc' ? ' ▴' : ' ▾') : '';
    });
  }
  if (theadEl) theadEl.addEventListener('click', function (ev) {
    var th = ev.target.closest('th.ep-sort');
    if (!th) return;
    var key = th.getAttribute('data-key');
    // Re-clicking the active column flips it; a new column starts in the
    // direction that surfaces what you clicked it to find.
    if (sort.key === key) sort.dir = sort.dir === 'asc' ? 'desc' : 'asc';
    else { sort.key = key; sort.dir = SORT_DIRS[key] || 'desc'; }
    renderTable();
  });

  function updateCount() {
    if (popCountEl) popCountEl.textContent = selected.length + ' selected';
    if (badgeEl) {
      if (selected.length) { badgeEl.hidden = false; badgeEl.textContent = String(selected.length); }
      else { badgeEl.hidden = true; }
    }
  }

  function setSelected(id, on) {
    var i = selected.indexOf(id);
    if (on && i < 0) selected.push(id);
    else if (!on && i >= 0) selected.splice(i, 1);
    renderTable();
  }

  listEl.addEventListener('change', function (ev) {
    var cb = ev.target;
    if (cb && cb.matches('input[type=checkbox][data-id]')) {
      setSelected(cb.getAttribute('data-id'), cb.checked);
    }
  });

  tbodyEl.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-remove]');
    if (!btn) return;
    var id = btn.getAttribute('data-remove');
    setSelected(id, false);
    var cb = listEl.querySelector('input[data-id="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
    if (cb) cb.checked = false;
  });

  // ---- Send-date ranges -------------------------------------------------
  // The common question is "how did the last quarter go", not "show me these
  // five sends", so the ranges are the primary control and the checklist is
  // there for when you want exact emails. A range whose window is empty is
  // disabled, so you never click one and watch the page go blank.
  var rangeEls = Array.prototype.slice.call(document.querySelectorAll('.ep-range'));

  function idsInRange(days) {
    if (!days) return allIds.slice();
    var cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    var iso = cutoff.toISOString().slice(0, 10);
    return allIds.filter(function (id) {
      var ts = String(byId[id]._ts || '');
      return ts && ts.slice(0, 10) >= iso;
    });
  }

  var rangeIds = {};
  rangeEls.forEach(function (btn) {
    var days = Number(btn.getAttribute('data-days') || 0);
    var ids = idsInRange(days);
    rangeIds[days] = ids;
    btn.disabled = !ids.length;
    btn.title = ids.length
      ? ids.length + (ids.length === 1 ? ' email' : ' emails') + ' sent in this window'
      : 'No emails sent in this window';
    btn.addEventListener('click', function () {
      if (btn.disabled) return;
      selected = rangeIds[days].slice();
      syncChecks(); renderTable();
    });
  });

  // A range pill lights up whenever the selection happens to *be* that window --
  // whether you clicked the pill or arrived at the same set by hand.
  function sameSet(a, b) {
    if (a.length !== b.length) return false;
    var seen = {};
    a.forEach(function (id) { seen[id] = true; });
    return b.every(function (id) { return seen[id]; });
  }
  function renderRanges() {
    rangeEls.forEach(function (btn) {
      var days = Number(btn.getAttribute('data-days') || 0);
      var on = !btn.disabled && selected.length && sameSet(selected, rangeIds[days] || []);
      btn.classList.toggle('active', !!on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  // ---- Picker ------------------------------------------------------------
  function buildPicker() {
    listEl.innerHTML = '';
    emails.forEach(function (e) {
      var row = document.createElement('label');
      row.className = 'ep-opt';
      row.setAttribute('data-search',
        (String(e.name) + ' ' + String(e.subject || '') + ' ' + String(e.type || '')).toLowerCase());
      row.innerHTML =
        '<input type="checkbox" data-id="' + esc(e.id) + '"' +
          (selected.indexOf(e.id) >= 0 ? ' checked' : '') + '>' +
        '<span class="ep-opt-main"><div class="ep-opt-name">' + esc(e.name) + '</div>' +
        // Enough to choose on without opening the email: when it went, how big
        // the send was, and how it did.
        '<div class="ep-opt-sub">' + esc(e.date) + ' &middot; ' + esc(e.deliveries) +
        ' delivered &middot; ' + esc(e.open) + ' open</div></span>';
      listEl.appendChild(row);
    });
  }

  function shownOptions() {
    return Array.prototype.slice.call(listEl.querySelectorAll('.ep-opt'))
      .filter(function (row) { return row.style.display !== 'none'; });
  }

  if (searchEl) {
    searchEl.addEventListener('input', function () {
      var q = searchEl.value.trim().toLowerCase();
      var shown = 0;
      listEl.querySelectorAll('.ep-opt').forEach(function (row) {
        var hit = !q || row.getAttribute('data-search').indexOf(q) >= 0;
        row.style.display = hit ? '' : 'none';
        if (hit) shown++;
      });
      var noRes = document.getElementById('ep-list-empty');
      if (noRes) noRes.style.display = shown ? 'none' : '';
      var addBtn = document.getElementById('ep-add-shown');
      if (addBtn) {
        addBtn.hidden = !q || !shown;
        addBtn.textContent = 'Add these ' + shown;
      }
    });
  }

  // "Add these N" turns a search into a selection in one click -- the fastest
  // way to build a table of, say, every "Newsletter" send.
  var addShownBtn = document.getElementById('ep-add-shown');
  if (addShownBtn) addShownBtn.addEventListener('click', function () {
    shownOptions().forEach(function (row) {
      var cb = row.querySelector('input[data-id]');
      if (cb && selected.indexOf(cb.getAttribute('data-id')) < 0) {
        selected.push(cb.getAttribute('data-id'));
      }
    });
    syncChecks(); renderTable();
  });

  var recentBtn = document.getElementById('ep-recent');
  if (recentBtn) recentBtn.addEventListener('click', function () {
    selected = emails.slice(0, 10).map(function (e) { return e.id; });
    syncChecks(); renderTable();
  });
  var clearBtn = document.getElementById('ep-clear');
  if (clearBtn) clearBtn.addEventListener('click', function () {
    selected = []; syncChecks(); renderTable();
  });
  function syncChecks() {
    listEl.querySelectorAll('input[type=checkbox][data-id]').forEach(function (cb) {
      cb.checked = selected.indexOf(cb.getAttribute('data-id')) >= 0;
    });
  }

  // Low-profile picker: a header button toggles a popover checklist. Clicking
  // outside or Escape closes it; clicks inside don't bubble to the doc handler.
  // Opening moves focus into the search box and closing hands it back to the
  // button, so the whole control works from the keyboard.
  var pickBtn  = document.getElementById('ep-pick-btn');
  var pop      = document.getElementById('ep-pop');
  var backdrop = document.getElementById('ep-backdrop');
  var setPickerOpen = function () {};
  if (pickBtn && pop) {
    // One path in and out, so the phone sheet's scrim can never be left behind
    // over a page whose picker has already closed.
    setPickerOpen = function (o, keepFocus) {
      pop.hidden = !o;
      if (backdrop) backdrop.hidden = !o;
      pickBtn.setAttribute('aria-expanded', o ? 'true' : 'false');
      if (o && searchEl) {
        searchEl.value = '';
        searchEl.dispatchEvent(new Event('input'));
        // Focusing the search box raises the phone keyboard over the sheet, so
        // only reach for it when the pointer isn't the input device.
        if (window.matchMedia && window.matchMedia('(hover: hover)').matches) searchEl.focus();
      } else if (!o && !keepFocus) {
        pickBtn.focus();
      }
    };
    pickBtn.addEventListener('click', function (e) { e.stopPropagation(); setPickerOpen(pop.hidden); });
    pop.addEventListener('click', function (e) { e.stopPropagation(); });
    var xBtn = pop.querySelector('.ep-pop-x');
    if (xBtn) xBtn.addEventListener('click', function () { setPickerOpen(false); });
    // A tap anywhere outside closes, the scrim included -- but it must not yank
    // focus back to the button, which would scroll a phone back up the page.
    document.addEventListener('click', function () { if (!pop.hidden) setPickerOpen(false, true); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !pop.hidden) setPickerOpen(false); });
  }
  var emptyOpen = document.getElementById('ep-empty-open');
  if (emptyOpen) emptyOpen.addEventListener('click', function (e) {
    e.stopPropagation(); setPickerOpen(true);
  });

  // ---- CSV export --------------------------------------------------------
  // Whatever is on screen, in the order it is on screen -- the table is where
  // people build the slide, and retyping ten numbers into a deck is how numbers
  // get typed wrong.
  function csvCell(v) {
    var s = String(v == null ? '' : v);
    return /[",\\r\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  var CSV_HEAD = ['Email', 'Subject', 'Type', 'Send date', 'Sent', 'Delivered',
                  'Bounce rate', 'Open rate', 'Click rate', 'Click-to-open', 'Unsub rate'];
  var exportBtn = document.getElementById('ep-export');
  if (exportBtn) exportBtn.addEventListener('click', function () {
    var ids = sortedSelection();
    if (!ids.length) return;
    var rows = [CSV_HEAD].concat(ids.map(function (id) {
      var e = byId[id];
      return [e.name, e.subject || '', e.type || '', e.date, e.sent, e.deliveries,
              e.bounce, e.open, e.click, e.ctor, e.unsub];
    }));
    // The BOM is what makes Excel open UTF-8 names correctly.
    var body = '\\ufeff' + rows.map(function (r) { return r.map(csvCell).join(','); }).join('\\r\\n');
    var url = URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' }));
    var a = document.createElement('a');
    a.href = url;
    a.download = (exportBtn.getAttribute('data-slug') || 'email') + '-performance.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });

  // Admin-only: persist the current selection portal-wide. The Save button only
  // exists in the DOM for admins, so its absence is the guard.
  var saveBtn = document.getElementById('ep-save');
  var saveStatus = document.getElementById('ep-save-status');
  if (saveBtn) saveBtn.addEventListener('click', function () {
    var api = saveBtn.getAttribute('data-api');
    if (!api) return;
    saveBtn.disabled = true;
    if (saveStatus) { saveStatus.className = 'ep-save-status'; saveStatus.textContent = 'Saving…'; }
    fetch(api, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emails: selected })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        if (!r.ok) throw new Error((b && b.detail && (b.detail.error || b.detail)) || r.statusText);
        if (saveStatus) { saveStatus.className = 'ep-save-status ok'; saveStatus.textContent = 'Saved.'; }
      });
    }).catch(function (err) {
      if (saveStatus) { saveStatus.className = 'ep-save-status err'; saveStatus.textContent = 'Save failed: ' + (err.message || err); }
    }).then(function () { saveBtn.disabled = false; });
  });

  buildPicker();
  renderTable();
})();
</script>
"""


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def _rate_str(num: Any, den: Any, decimals: int = 1) -> str:
    """Percentage of num over den, em dash when den is 0."""
    try:
        n, d = float(num or 0), float(den or 0)
    except (TypeError, ValueError):
        return "—"
    if d <= 0:
        return "—"
    return f"{100.0 * n / d:.{decimals}f}%"


def _fmt_dt(v: Any) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%b %d, %Y")
    return _esc(str(v)) if v else "—"


def _iso(v: Any) -> str:
    """Send date as a sortable string; unknown dates sort last under a desc
    sort (the default) because the empty string compares lowest."""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v) if v else ""


# HubSpot spells the email kind several ways depending on which endpoint the row
# came from ("BATCH_EMAIL", "batch", "AUTOMATED_AB_EMAIL", …); this maps the ones
# we see onto something a person would say out loud.
_TYPE_LABELS: dict[str, str] = {
    "batch": "Batch",
    "batch_email": "Batch",
    "ab": "A/B",
    "ab_email": "A/B",
    "automated_ab_email": "A/B",
    "automated": "Automated",
    "automated_email": "Automated",
    "blog": "Blog",
    "blog_email": "Blog",
    "rss": "RSS",
    "rss_email": "RSS",
    "followup": "Follow-up",
    "followup_email": "Follow-up",
    "local_time_email": "Local time",
    "single_send_api": "Single send",
}


def _type_label(v: Any) -> str:
    key = str(v or "").strip().lower()
    if not key:
        return ""
    if key in _TYPE_LABELS:
        return _TYPE_LABELS[key]
    return key.replace("_email", "").replace("_", " ").strip().title()


def _sort_th(key: str, label: str, *, left: bool = False, tip: str = "") -> str:
    """A clickable column heading. The arrow span is filled in by the JS, which
    owns which column is active."""
    cls = "ep-sort left" if left else "ep-sort"
    title = f' title="{_esc(tip)}"' if tip else ""
    return (
        f'<th class="{cls}" data-key="{key}" scope="col" aria-sort="none"{title}>'
        f'{_esc(label)}<span class="ep-arrow"></span></th>'
    )


def _num(v: Any) -> float:
    """Raw numeric coercion for client-side tile aggregation (0.0 on junk)."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _email_payload(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display-ready records for the client-side picker + table.

    Rates are precomputed here so the JS just renders strings, and the leading-
    underscore keys carry the raw counts the tiles, deltas and sorting aggregate
    live. Note the denominators: opens/clicks/unsubscribes are over deliveries,
    bounces are over sends (a bounce is a send that never got delivered), and
    click-to-open is clicks over opens."""
    out = []
    for e in emails:
        delivered = e.get("delivered") or 0
        sent = e.get("sent") or 0
        opens = e.get("opens") or 0
        out.append({
            "id":         str(e.get("email_id")),
            "name":       e.get("name") or e.get("subject") or "Untitled email",
            "subject":    e.get("subject") or "",
            "type":       _type_label(e.get("email_type")),
            "date":       _fmt_dt(e.get("publish_date")),
            # Sortable form of the send date (ISO, so string order = date order).
            "_ts":        _iso(e.get("publish_date")),
            "sent":       _fmt_int(sent),
            "deliveries": _fmt_int(delivered),
            "bounce":     _rate_str(e.get("bounces"), sent, decimals=2),
            "open":       _rate_str(opens, delivered),
            "click":      _rate_str(e.get("clicks"), delivered),
            "ctor":       _rate_str(e.get("clicks"), opens),
            "unsub":      _rate_str(e.get("unsubscribed"), delivered, decimals=2),
            # Raw counts for the KPI tiles (weighted rates = Σnum / Σden), for the
            # vs-average deltas, and for column sorting, which must not sort the
            # rounded strings.
            "_sent":      _num(sent),
            "_delivered": _num(delivered),
            "_opens":     _num(opens),
            "_clicks":    _num(e.get("clicks")),
            "_unsub":     _num(e.get("unsubscribed")),
            "_bounces":   _num(e.get("bounces")),
        })
    return out


def _tile(key: str, label: str, sub: str) -> str:
    """One metric card. Rate tiles fill their sub-line from the JS with how the
    selection compares to every email this client has sent; `sub` is the static
    fallback shown before the first render."""
    return (
        f'<div class="ep-tile"><div class="ep-tile-label">{_esc(label)}</div>'
        f'<div class="ep-tile-value" id="ep-kpi-{key}">—</div>'
        f'<div class="ep-tile-sub" id="ep-kpi-{key}-sub">{_esc(sub)}</div></div>'
    )


def render_email_performance(
    *,
    client_slug: str,
    label: str,
    report: EmailPerformanceReport,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    saved_selection: list[str] | tuple[str, ...] | None = None,
) -> str:
    title_block = (
        '<div><h1 class="ep-title">Email Performance</h1>'
        f'<p class="ep-sub">HubSpot marketing email reporting for {_esc(label)}.</p></div>'
    )

    if not report.configured:
        body = (
            f'<div class="ep-note">{_esc(report.error or "HubSpot is not configured for this client.")} '
            'Connect HubSpot from the Connectors page to enable email reporting.</div>'
        )
        head = f'<div class="ep-head">{title_block}</div>'
        return _shell(client_slug, label, f'<div class="ep-wrap">{head}{body}</div>',
                      access_key, use_session, session_email, session_is_admin)

    payload = _email_payload(report.emails or [])

    if not payload:
        note = (
            'No marketing email data has synced yet. This appears once HubSpot '
            'syncs marketing emails for a Marketing Hub tier that exposes email '
            'statistics — run a HubSpot sync from the Connectors page, then refresh.'
        )
        body = f'<div class="ep-note">{_esc(note)}</div>'
        head = f'<div class="ep-head">{title_block}</div>'
        return _shell(client_slug, label, f'<div class="ep-wrap">{head}{body}</div>',
                      access_key, use_session, session_email, session_is_admin)

    total = len(payload)

    # Send-date ranges govern the chart *and* the table, so they sit with the
    # page title rather than inside one card. The JS disables a window nothing
    # was sent in, and lights up whichever one matches the current selection.
    range_btns = "".join(
        f'<button type="button" class="ep-range" data-days="{days}" aria-pressed="false">'
        f'{_esc(text)}</button>'
        for text, days in _RANGES
    )
    ranges = (
        '<div class="ep-ranges">'
        '<span class="ep-ranges-label">Sent in</span>'
        f'<span class="ep-range-group" role="group" aria-label="Send date range">{range_btns}</span>'
        '</div>'
    )
    head = f'<div class="ep-head">{title_block}{ranges}</div>'

    # Hero KPI tiles — values are filled live by the JS from the current
    # selection, so they read as a real dashboard summary (like Lead Tracking /
    # Overview) rather than a static header. Every rate tile carries how the
    # selection compares with this client's own all-email average.
    tiles = (
        '<div class="ep-tiles">'
        '<div class="ep-tile"><div class="ep-tile-label">Emails selected</div>'
        '<div class="ep-tile-value" id="ep-kpi-count">0</div>'
        f'<div class="ep-tile-sub">of {total} available</div></div>'
        '<div class="ep-tile"><div class="ep-tile-label">Delivered</div>'
        '<div class="ep-tile-value" id="ep-kpi-delivered">—</div>'
        '<div class="ep-tile-sub">across selected</div></div>'
        + _tile("open", "Open rate", "of deliveries")
        + _tile("click", "Click rate", "of deliveries")
        + _tile("ctor", "Click-to-open", "clicks per open")
        + _tile("unsub", "Unsub rate", "of deliveries")
        + _tile("bounce", "Bounce rate", "of sends")
        + '</div>'
    )

    # Admins get a Save control (in the popover footer) that persists the ticked
    # set portal-wide via the email-performance/selection API; clients see the
    # same picker but can only tweak their own in-session view. The Save button
    # carries the API URL so the JS needs no extra plumbing, and its absence is
    # the client/admin guard.
    save_api = f'/api/clients/{_esc(client_slug)}/email-performance/selection'
    save_foot = (
        '<div class="ep-pop-foot">'
        f'<button type="button" class="ep-save-btn" id="ep-save" data-api="{save_api}" '
        f'title="Save this selection for everyone who views this client\'s portal">'
        'Save selection</button>'
        '<span class="ep-save-status" id="ep-save-status"></span>'
        '</div>'
        if session_is_admin else ''
    )

    # Filter/list icon echoes the Campaign Explorer's "Campaigns" picker.
    pick_icon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M4 6h16"/><path d="M7 12h10"/><path d="M10 18h4"/></svg>'
    )
    picker_control = (
        '<div class="ep-picker">'
        '<button type="button" class="ep-pick-btn" id="ep-pick-btn" '
        'aria-haspopup="dialog" aria-expanded="false">'
        f'{pick_icon}<span>Choose emails</span>'
        '<span class="ep-pick-badge" id="ep-pick-badge" hidden></span></button>'
        '<div class="ep-backdrop" id="ep-backdrop" hidden></div>'
        '<div class="ep-pop" id="ep-pop" role="dialog" aria-label="Choose emails" hidden>'
        '<div class="ep-pop-head"><span>Choose emails</span>'
        '<button type="button" class="ep-pop-x" aria-label="Close">&times;</button></div>'
        '<input type="text" id="ep-search" class="ep-search" '
        'placeholder="Search by name, subject or type…" autocomplete="off">'
        '<div class="ep-pop-bar">'
        '<span class="ep-count" id="ep-pop-count" aria-live="polite"></span>'
        '<span class="ep-actions">'
        '<button type="button" class="ep-btn" id="ep-add-shown" hidden>Add these</button>'
        '<button type="button" class="ep-btn" id="ep-recent">10 most recent</button>'
        '<button type="button" class="ep-btn" id="ep-clear">Clear</button>'
        '</span></div>'
        '<div class="ep-list" id="ep-list"></div>'
        '<div class="ep-list-empty" id="ep-list-empty" style="display:none">No emails match your search.</div>'
        f'{save_foot}'
        '</div></div>'
    )

    export_control = (
        f'<button type="button" class="ep-pick-btn" id="ep-export" data-slug="{_esc(client_slug)}" '
        'title="Download the table exactly as it is on screen">Export CSV</button>'
    )

    # Trend card -- the selected emails plotted over their send dates, so the
    # table's rows can be read as a shape (a run of weak opens, a spiking
    # unsub) before anyone scans the numbers. Same selection as the table.
    chart_card = (
        '<div class="ep-card">'
        '<div class="ep-card-head">'
        '<div class="ep-card-head-titles"><h2>Trends</h2>'
        '<span class="ep-card-note">Selected emails by send date. '
        'Click a legend key to hide a line, or to bring in click-to-open and '
        'bounce rate.</span></div>'
        '</div>'
        '<div class="ep-chart-host" id="ep-chart-host"><canvas id="ep-chart"></canvas></div>'
        '<div class="ep-chart-empty" id="ep-chart-empty" style="display:none">'
        'No emails selected — pick a range above, or use “Choose emails”.</div>'
        '</div>'
    )

    card = (
        '<div class="ep-card">'
        '<div class="ep-card-head">'
        '<div class="ep-card-head-titles"><h2>Performance</h2>'
        f'<span class="ep-card-note">Open, click and unsub rates are over deliveries; '
        f'bounce rate is over sends. The small figure under an open or click rate is '
        f'its distance from the average across all {total} emails. Click a column '
        'heading to sort.</span></div>'
        f'<div class="ep-tools">{picker_control}{export_control}</div>'
        '</div>'
        '<div class="ep-table-wrap"><table class="ep-table">'
        f'<thead id="ep-thead"><tr>{_sort_th("name", "Email", left=True)}'
        f'{_sort_th("date", "Send date")}{_sort_th("sent", "Sent")}'
        f'{_sort_th("deliveries", "Deliveries")}'
        f'{_sort_th("bounce", "Bounce rate", tip="Bounces as a share of sends")}'
        f'{_sort_th("open", "Open rate", tip="Opens as a share of deliveries")}'
        f'{_sort_th("click", "Click rate", tip="Clicks as a share of deliveries")}'
        f'{_sort_th("ctor", "Click-to-open", tip="Clicks as a share of opens — how the email itself did, once it was opened")}'
        f'{_sort_th("unsub", "Unsub rate", tip="Unsubscribes as a share of deliveries")}'
        '<th scope="col"><span class="ep-sr-only">Remove</span></th></tr></thead>'
        '<tbody id="ep-tbody"></tbody></table></div>'
        '<div class="ep-empty" id="ep-empty" style="display:none">'
        'No emails selected — pick a range above, or'
        '<button type="button" class="ep-btn" id="ep-empty-open">choose emails</button>'
        '</div>'
        '</div>'
    )

    data_script = f'<script type="application/json" id="ep-emails">{_json(payload)}</script>'
    saved_ids = [str(i) for i in (saved_selection or [])]
    saved_script = f'<script type="application/json" id="ep-saved">{_json(saved_ids)}</script>'
    ep_js = _EP_JS.replace("%DEFAULT%", str(_DEFAULT_SELECTED))

    content = f'<div class="ep-wrap">{head}{tiles}{chart_card}{card}</div>{data_script}{saved_script}{ep_js}'
    return _shell(client_slug, label, content, access_key, use_session, session_email,
                  session_is_admin, include_chartjs=True)


def _shell(client_slug, label, content, access_key, use_session, session_email,
           session_is_admin, include_chartjs: bool = False) -> str:
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="email-performance",
        page_title="Email Performance",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
        include_chartjs=include_chartjs,
    )
