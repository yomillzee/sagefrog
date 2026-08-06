"""HTML renderer for the standalone Email Performance page (HubSpot).

Laid out like the rest of the dashboard (Overview / Lead Tracking): a row of
hero KPI tiles summarising the current selection, then a single Performance
card whose table lists delivery / open-rate / click-rate / unsub-rate per email.
Choosing which emails to show is a low-profile control — a compact "Choose
emails" button in the card header opens a popover checklist (search + list),
echoing the Campaign Explorer's picker — so the table stays the hero.

All interaction is client-side: the full email set is embedded once as JSON and
the tiles, picker, and table are built from it, so selecting, searching, and
removing never hit the server. An admin's chosen set can be saved portal-wide
from the popover (see save_email_performance_selection); it then seeds the view
for every viewer.
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

_EXTRA_CSS = """
/* Match Overview: a centered content column instead of hugging the sidebar. */
.ep-wrap { max-width:1320px; margin:0 auto; }
.ep-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:4px; }
.ep-title { font-size:1.6rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.01em; }
.ep-sub { font-size:.9rem; color:var(--muted); margin:6px 0 0; }

/* Hero KPI tiles — mirror Lead Tracking / Overview so the page reads as one family. */
.ep-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin:22px 0 20px; }
.ep-tile { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:18px 20px; box-shadow:var(--shadow-sm); position:relative; overflow:hidden; }
.ep-tile::before { content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--accent); opacity:.85; }
.ep-tile.t-count::before { background:var(--navy); }
.ep-tile.t-delivered::before { background:var(--accent); }
.ep-tile.t-open::before { background:#0ea5e9; }
.ep-tile.t-click::before { background:var(--ok); }
.ep-tile.t-unsub::before { background:var(--err); }
.ep-tile-label { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); font-weight:700; margin-bottom:8px; }
.ep-tile-value { font-size:1.9rem; font-weight:750; color:var(--navy); line-height:1.05; letter-spacing:-.01em; font-variant-numeric:tabular-nums; }
.ep-tile-sub { font-size:.78rem; color:var(--muted); margin-top:6px; }

/* Card */
.ep-card { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:20px 22px; margin-bottom:20px; box-shadow:var(--shadow-sm); }
.ep-card-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 14px; flex-wrap:wrap; }
.ep-card-head-titles { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.ep-card h2 { font-size:1rem; font-weight:700; color:var(--navy); margin:0; }
.ep-card-note { font-size:.78rem; color:var(--muted); }
.ep-note { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:12px; padding:13px 16px; font-size:.85rem; margin-bottom:20px; }

/* Low-profile picker: a compact button in the card header that opens a popover checklist. */
.ep-tools { display:flex; align-items:center; gap:10px; }
.ep-picker { position:relative; }
.ep-pick-btn { display:inline-flex; align-items:center; gap:7px; border:1px solid var(--border); background:var(--panel); color:var(--text); font-size:.8rem; font-weight:650; padding:7px 12px; border-radius:9px; cursor:pointer; }
.ep-pick-btn:hover { background:var(--surface); border-color:#cbd5e1; }
.ep-pick-btn svg { width:15px; height:15px; opacity:.7; }
.ep-pick-badge { background:var(--accent); color:#fff; font-size:.7rem; font-weight:700; border-radius:999px; padding:1px 7px; line-height:1.5; }
.ep-pop { position:absolute; z-index:40; top:calc(100% + 6px); right:0; width:340px; max-width:88vw; background:var(--panel); border:1px solid var(--border); border-radius:12px; box-shadow:var(--shadow); padding:12px; }
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
.ep-actions { display:flex; gap:6px; }
.ep-btn { border:1px solid var(--border); background:var(--panel); color:var(--accent); font-size:.72rem; font-weight:650; padding:4px 9px; border-radius:7px; cursor:pointer; }
.ep-btn:hover { background:var(--surface); }
.ep-list { max-height:260px; overflow-y:auto; border:1px solid var(--border); border-radius:10px; }
.ep-opt { display:flex; align-items:center; gap:10px; padding:8px 12px; border-bottom:1px solid #f1f4f8; cursor:pointer; font-size:.83rem; }
.ep-opt:last-child { border-bottom:0; }
.ep-opt:hover { background:var(--surface); }
.ep-opt input { width:15px; height:15px; accent-color:var(--accent); flex-shrink:0; cursor:pointer; }
.ep-opt-main { min-width:0; flex:1; }
.ep-opt-name { color:var(--text); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ep-opt-sub { color:var(--muted); font-size:.72rem; margin-top:1px; }
.ep-list-empty { padding:16px; text-align:center; color:var(--muted); font-size:.8rem; }
.ep-pop-foot { display:flex; align-items:center; gap:10px; margin-top:10px; }
.ep-save-btn { border:1px solid var(--accent); background:var(--accent); color:#fff; font-size:.76rem; font-weight:650; padding:6px 12px; border-radius:8px; cursor:pointer; }
.ep-save-btn:hover { filter:brightness(1.05); }
.ep-save-btn:disabled { opacity:.6; cursor:default; }
.ep-save-status { font-size:.74rem; font-weight:600; color:var(--muted); }
.ep-save-status.ok { color:var(--ok); }
.ep-save-status.err { color:var(--err); }

/* Table */
.ep-table-wrap { overflow-x:auto; }
.ep-table { width:100%; border-collapse:collapse; font-size:.86rem; }
.ep-table th { text-align:left; color:var(--muted); font-weight:700; font-size:.68rem; text-transform:uppercase; letter-spacing:.05em; padding:10px 12px; border-bottom:1px solid var(--border); white-space:nowrap; }
.ep-table th.num, .ep-table td.num { text-align:right; font-variant-numeric:tabular-nums; }
.ep-table td { padding:12px 12px; border-bottom:1px solid #f1f4f8; color:var(--text); vertical-align:top; }
.ep-table tbody tr:last-child td { border-bottom:0; }
.ep-table tbody tr:hover td { background:var(--surface); }
.ep-email-name { font-weight:650; color:var(--navy); }
.ep-email-meta { color:var(--muted); font-size:.74rem; margin-top:2px; }
.ep-remove { border:0; background:transparent; color:var(--muted); cursor:pointer; font-size:1.1rem; line-height:1; padding:0 2px; }
.ep-remove:hover { color:var(--err); }
.ep-empty { color:var(--muted); font-size:.86rem; padding:26px 4px; text-align:center; }
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

  // Preserve selection order so the table reads the way the user built it.
  var selected = validSaved.length
    ? validSaved.slice()
    : emails.slice(0, %DEFAULT%).map(function (e) { return e.id; });

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function fmtInt(n) { try { return Number(n || 0).toLocaleString('en-US'); } catch (e) { return String(n || 0); } }
  function pct(num, den, dp) {
    var n = Number(num || 0), d = Number(den || 0);
    if (!(d > 0)) return '—';
    return (100 * n / d).toFixed(dp) + '%';
  }
  function setText(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }

  function buildPicker() {
    listEl.innerHTML = '';
    emails.forEach(function (e) {
      var row = document.createElement('label');
      row.className = 'ep-opt';
      row.setAttribute('data-search', (String(e.name) + ' ' + String(e.subject || '')).toLowerCase());
      row.innerHTML =
        '<input type="checkbox" data-id="' + esc(e.id) + '"' +
          (selected.indexOf(e.id) >= 0 ? ' checked' : '') + '>' +
        '<span class="ep-opt-main"><div class="ep-opt-name">' + esc(e.name) + '</div>' +
        '<div class="ep-opt-sub">' + esc(e.date) + ' &middot; ' + esc(e.deliveries) + ' delivered</div></span>';
      listEl.appendChild(row);
    });
  }

  // Hero tiles summarise the current selection (rates weighted by deliveries, so
  // they mean the same thing as the per-row rates).
  function renderTiles() {
    var del = 0, opn = 0, clk = 0, uns = 0;
    selected.forEach(function (id) {
      var e = byId[id]; if (!e) return;
      del += Number(e._delivered || 0); opn += Number(e._opens || 0);
      clk += Number(e._clicks || 0);    uns += Number(e._unsub || 0);
    });
    setText('ep-kpi-count', String(selected.length));
    setText('ep-kpi-delivered', selected.length ? fmtInt(del) : '—');
    setText('ep-kpi-open', pct(opn, del, 1));
    setText('ep-kpi-click', pct(clk, del, 1));
    setText('ep-kpi-unsub', pct(uns, del, 2));
  }

  function renderTable() {
    tbodyEl.innerHTML = '';
    if (!selected.length) {
      if (emptyEl) emptyEl.style.display = '';
    } else {
      if (emptyEl) emptyEl.style.display = 'none';
      selected.forEach(function (id) {
        var e = byId[id];
        if (!e) return;
        var tr = document.createElement('tr');
        tr.innerHTML =
          '<td><div class="ep-email-name">' + esc(e.name) + '</div>' +
            '<div class="ep-email-meta">' + esc(e.date) +
            (e.subject ? ' &middot; ' + esc(e.subject) : '') + '</div></td>' +
          '<td class="num">' + esc(e.sent) + '</td>' +
          '<td class="num">' + esc(e.deliveries) + '</td>' +
          '<td class="num">' + esc(e.open) + '</td>' +
          '<td class="num">' + esc(e.click) + '</td>' +
          '<td class="num">' + esc(e.unsub) + '</td>' +
          '<td class="num"><button type="button" class="ep-remove" data-remove="' + esc(e.id) +
            '" title="Remove" aria-label="Remove">&times;</button></td>';
        tbodyEl.appendChild(tr);
      });
    }
    renderTiles();
    updateCount();
  }

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

  if (searchEl) {
    searchEl.addEventListener('input', function () {
      var q = searchEl.value.trim().toLowerCase();
      var any = false;
      listEl.querySelectorAll('.ep-opt').forEach(function (row) {
        var hit = !q || row.getAttribute('data-search').indexOf(q) >= 0;
        row.style.display = hit ? '' : 'none';
        if (hit) any = true;
      });
      var noRes = document.getElementById('ep-list-empty');
      if (noRes) noRes.style.display = any ? 'none' : '';
    });
  }

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
  var pickBtn = document.getElementById('ep-pick-btn');
  var pop = document.getElementById('ep-pop');
  if (pickBtn && pop) {
    var setOpen = function (o) {
      pop.hidden = !o;
      pickBtn.setAttribute('aria-expanded', o ? 'true' : 'false');
      if (o && searchEl) { searchEl.value = ''; searchEl.dispatchEvent(new Event('input')); }
    };
    pickBtn.addEventListener('click', function (e) { e.stopPropagation(); setOpen(pop.hidden); });
    pop.addEventListener('click', function (e) { e.stopPropagation(); });
    var xBtn = pop.querySelector('.ep-pop-x');
    if (xBtn) xBtn.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });
  }

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
    """Percentage of num over den (over deliveries), em dash when den is 0."""
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


def _num(v: Any) -> float:
    """Raw numeric coercion for client-side tile aggregation (0.0 on junk)."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _email_payload(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display-ready records for the client-side picker + table. Rates are
    computed over deliveries here so the JS just renders strings; the leading-
    underscore keys carry the raw counts the hero tiles aggregate live."""
    out = []
    for e in emails:
        delivered = e.get("delivered") or 0
        out.append({
            "id":         str(e.get("email_id")),
            "name":       e.get("name") or e.get("subject") or "Untitled email",
            "subject":    e.get("subject") or "",
            "date":       _fmt_dt(e.get("publish_date")),
            "sent":       _fmt_int(e.get("sent") or 0),
            "deliveries": _fmt_int(delivered),
            "open":       _rate_str(e.get("opens"), delivered),
            "click":      _rate_str(e.get("clicks"), delivered),
            "unsub":      _rate_str(e.get("unsubscribed"), delivered, decimals=2),
            # Raw counts for the KPI tiles (weighted rates = Σnum / Σdeliveries).
            "_delivered": _num(delivered),
            "_opens":     _num(e.get("opens")),
            "_clicks":    _num(e.get("clicks")),
            "_unsub":     _num(e.get("unsubscribed")),
        })
    return out


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
    head = (
        '<div class="ep-head">'
        '<div><h1 class="ep-title">Email Performance</h1>'
        f'<p class="ep-sub">HubSpot marketing email reporting for {_esc(label)}.</p></div>'
        '</div>'
    )

    if not report.configured:
        body = (
            f'<div class="ep-note">{_esc(report.error or "HubSpot is not configured for this client.")} '
            'Connect HubSpot from the Connectors page to enable email reporting.</div>'
        )
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
        return _shell(client_slug, label, f'<div class="ep-wrap">{head}{body}</div>',
                      access_key, use_session, session_email, session_is_admin)

    # Hero KPI tiles — values are filled live by the JS from the current
    # selection, so they read as a real dashboard summary (like Lead Tracking /
    # Overview) rather than a static header.
    total = len(payload)
    tiles = (
        '<div class="ep-tiles">'
        '<div class="ep-tile t-count"><div class="ep-tile-label">Emails selected</div>'
        '<div class="ep-tile-value" id="ep-kpi-count">0</div>'
        f'<div class="ep-tile-sub">of {total} available</div></div>'
        '<div class="ep-tile t-delivered"><div class="ep-tile-label">Delivered</div>'
        '<div class="ep-tile-value" id="ep-kpi-delivered">—</div>'
        '<div class="ep-tile-sub">across selected</div></div>'
        '<div class="ep-tile t-open"><div class="ep-tile-label">Avg open rate</div>'
        '<div class="ep-tile-value" id="ep-kpi-open">—</div>'
        '<div class="ep-tile-sub">over deliveries</div></div>'
        '<div class="ep-tile t-click"><div class="ep-tile-label">Avg click rate</div>'
        '<div class="ep-tile-value" id="ep-kpi-click">—</div>'
        '<div class="ep-tile-sub">over deliveries</div></div>'
        '<div class="ep-tile t-unsub"><div class="ep-tile-label">Avg unsub rate</div>'
        '<div class="ep-tile-value" id="ep-kpi-unsub">—</div>'
        '<div class="ep-tile-sub">over deliveries</div></div>'
        '</div>'
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
        '<div class="ep-pop" id="ep-pop" role="dialog" aria-label="Choose emails" hidden>'
        '<div class="ep-pop-head"><span>Choose emails</span>'
        '<button type="button" class="ep-pop-x" aria-label="Close">&times;</button></div>'
        '<input type="text" id="ep-search" class="ep-search" '
        'placeholder="Search emails…" autocomplete="off">'
        '<div class="ep-pop-bar"><span class="ep-count" id="ep-pop-count"></span>'
        '<span class="ep-actions">'
        '<button type="button" class="ep-btn" id="ep-recent">10 most recent</button>'
        '<button type="button" class="ep-btn" id="ep-clear">Clear</button>'
        '</span></div>'
        '<div class="ep-list" id="ep-list"></div>'
        '<div class="ep-list-empty" id="ep-list-empty" style="display:none">No emails match your search.</div>'
        f'{save_foot}'
        '</div></div>'
    )

    card = (
        '<div class="ep-card">'
        '<div class="ep-card-head">'
        '<div class="ep-card-head-titles"><h2>Performance</h2>'
        '<span class="ep-card-note">Rates are calculated over deliveries.</span></div>'
        f'<div class="ep-tools">{picker_control}</div>'
        '</div>'
        '<div class="ep-table-wrap"><table class="ep-table"><thead><tr>'
        '<th>Email</th><th class="num">Sent</th><th class="num">Deliveries</th>'
        '<th class="num">Open rate</th><th class="num">Click rate</th>'
        '<th class="num">Unsub rate</th><th class="num"></th>'
        '</tr></thead><tbody id="ep-tbody"></tbody></table></div>'
        '<div class="ep-empty" id="ep-empty" style="display:none">'
        'No emails selected — use “Choose emails” to build your table.</div>'
        '</div>'
    )

    data_script = f'<script type="application/json" id="ep-emails">{_json(payload)}</script>'
    saved_ids = [str(i) for i in (saved_selection or [])]
    saved_script = f'<script type="application/json" id="ep-saved">{_json(saved_ids)}</script>'
    ep_js = _EP_JS.replace("%DEFAULT%", str(_DEFAULT_SELECTED))

    content = f'<div class="ep-wrap">{head}{tiles}{card}</div>{data_script}{saved_script}{ep_js}'
    return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)


def _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin) -> str:
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
    )
