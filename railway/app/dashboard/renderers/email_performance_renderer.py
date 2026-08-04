"""HTML renderer for the standalone Email Performance page (HubSpot).

A searchable multi-select of the client's marketing emails drives a table of
delivery / open-rate / click-rate / unsub-rate metrics — pick any set of emails
and they appear as rows. All interaction is client-side: the full email set is
embedded once as JSON and the picker + table are built from it, so selecting,
searching, and removing never hit the server.
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
.ep-wrap { max-width: 1200px; }
.ep-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:4px; }
.ep-title { font-size:1.6rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.01em; }
.ep-sub { font-size:.9rem; color:var(--muted); margin:6px 0 0; }
.ep-hs-tag { display:inline-flex; align-items:center; gap:7px; padding:7px 13px; border-radius:999px; background:#fff3ed; border:1px solid #ffd9c7; color:#c2410c; font-size:.78rem; font-weight:650; white-space:nowrap; }
.ep-hs-dot { width:8px; height:8px; border-radius:50%; background:#ff7a59; }

.ep-card { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:20px 22px; margin:22px 0 20px; box-shadow:var(--shadow-sm); }
.ep-card-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin:0 0 14px; flex-wrap:wrap; }
.ep-card h2 { font-size:1rem; font-weight:700; color:var(--navy); margin:0; }
.ep-note { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:12px; padding:13px 16px; font-size:.85rem; margin-bottom:20px; }

/* Picker */
.ep-picker { position:relative; }
.ep-search { width:100%; box-sizing:border-box; padding:10px 14px 10px 38px; border:1px solid var(--border); border-radius:10px; background:var(--panel);
  color:var(--text); font-size:.9rem; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M21 21l-4.3-4.3'/%3E%3C/svg%3E");
  background-repeat:no-repeat; background-position:12px center; }
.ep-search:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(11,92,171,.15); }
.ep-picker-bar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:12px 0 8px; flex-wrap:wrap; }
.ep-count { font-size:.78rem; color:var(--muted); font-weight:650; }
.ep-actions { display:flex; gap:8px; }
.ep-btn { border:1px solid var(--border); background:var(--panel); color:var(--accent); font-size:.76rem; font-weight:650; padding:5px 11px; border-radius:8px; cursor:pointer; }
.ep-btn:hover { background:var(--surface); }
.ep-list { max-height:280px; overflow-y:auto; border:1px solid var(--border); border-radius:10px; }
.ep-opt { display:flex; align-items:center; gap:11px; padding:9px 13px; border-bottom:1px solid #f1f4f8; cursor:pointer; font-size:.85rem; }
.ep-opt:last-child { border-bottom:0; }
.ep-opt:hover { background:var(--surface); }
.ep-opt input { width:16px; height:16px; accent-color:var(--accent); flex-shrink:0; cursor:pointer; }
.ep-opt-main { min-width:0; flex:1; }
.ep-opt-name { color:var(--text); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ep-opt-sub { color:var(--muted); font-size:.74rem; margin-top:1px; }
.ep-list-empty { padding:18px; text-align:center; color:var(--muted); font-size:.82rem; }

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

  var listEl   = document.getElementById('ep-list');
  var tbodyEl  = document.getElementById('ep-tbody');
  var searchEl = document.getElementById('ep-search');
  var countEl  = document.getElementById('ep-count');
  var emptyEl  = document.getElementById('ep-empty');
  if (!listEl || !tbodyEl) return;

  // Preserve selection order so the table reads the way the user built it.
  var selected = [];
  emails.slice(0, %DEFAULT%).forEach(function (e) { selected.push(e.id); });

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

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

  function renderTable() {
    tbodyEl.innerHTML = '';
    if (!selected.length) {
      if (emptyEl) emptyEl.style.display = '';
      updateCount();
      return;
    }
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
    updateCount();
  }

  function updateCount() {
    if (countEl) countEl.textContent = selected.length + ' selected';
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


def _email_payload(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display-ready records for the client-side picker + table. Rates are
    computed over deliveries here so the JS just renders strings."""
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
) -> str:
    head = (
        '<div class="ep-head">'
        '<div><h1 class="ep-title">Email Performance</h1>'
        f'<p class="ep-sub">HubSpot marketing email reporting for {_esc(label)}.</p></div>'
        '<span class="ep-hs-tag"><span class="ep-hs-dot"></span>HubSpot</span>'
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

    picker = (
        '<div class="ep-card">'
        '<div class="ep-card-head"><h2>Choose emails</h2></div>'
        '<div class="ep-picker">'
        '<input type="text" id="ep-search" class="ep-search" '
        'placeholder="Search emails by name or subject…" autocomplete="off">'
        '<div class="ep-picker-bar">'
        '<span class="ep-count" id="ep-count"></span>'
        '<span class="ep-actions">'
        '<button type="button" class="ep-btn" id="ep-recent">Select 10 most recent</button>'
        '<button type="button" class="ep-btn" id="ep-clear">Clear</button>'
        '</span></div>'
        '<div class="ep-list" id="ep-list"></div>'
        '<div class="ep-list-empty" id="ep-list-empty" style="display:none">No emails match your search.</div>'
        '</div></div>'
    )

    table = (
        '<div class="ep-card">'
        '<div class="ep-card-head"><h2>Performance</h2>'
        '<span class="ep-count">Rates are calculated over deliveries.</span></div>'
        '<div class="ep-table-wrap"><table class="ep-table"><thead><tr>'
        '<th>Email</th><th class="num">Sent</th><th class="num">Deliveries</th>'
        '<th class="num">Open rate</th><th class="num">Click rate</th>'
        '<th class="num">Unsub rate</th><th class="num"></th>'
        '</tr></thead><tbody id="ep-tbody"></tbody></table></div>'
        '<div class="ep-empty" id="ep-empty" style="display:none">'
        'No emails selected — pick some above to build your table.</div>'
        '</div>'
    )

    data_script = f'<script type="application/json" id="ep-emails">{_json(payload)}</script>'
    ep_js = _EP_JS.replace("%DEFAULT%", str(_DEFAULT_SELECTED))

    content = f'<div class="ep-wrap">{head}{picker}{table}</div>{data_script}{ep_js}'
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
