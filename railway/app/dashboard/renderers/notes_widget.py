"""Floating presenter-notes widget (client-specific notepads).

Self-contained FAB + slide-up panel injected into the client dashboard pages,
plus a standalone popup page for "open in a new window" (so the notes can live
on a second screen and never cover the dashboard during a live presentation).

Both surfaces share one client-side controller that talks to the JSON notes
API under ``/dashboard/{slug}/notes``. The controller is configured through a
small ``window.__sfNotesCfg`` object emitted per page, so the (large) JS blob
itself carries no server-side interpolation — keeping brace-escaping sane.

Only rendered for agency users (see ``notes_routes``/the dashboard renderers);
CSRF headers on the AJAX writes are attached automatically by the app-wide
fetch wrapper (see ``web_security``), so nothing extra is wired here.
"""

from __future__ import annotations

import json

from dashboard.utils.formatting import esc as _esc

_ICON_NOTE = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>'
    '<path d="M14 2v6h6"/><line x1="8" y1="13" x2="16" y2="13"/>'
    '<line x1="8" y1="17" x2="13" y2="17"/></svg>'
)


def _notes_base(client_slug: str) -> str:
    return f"/dashboard/{client_slug}/notes"


def _config_script(*, client_slug: str, label: str, embedded: bool) -> str:
    cfg = {
        "base": _notes_base(client_slug),
        "slug": client_slug,
        "label": label,
        "embedded": bool(embedded),
    }
    return f'<script>window.__sfNotesCfg={json.dumps(cfg)};</script>'


def _panel_inner_html(*, embedded: bool) -> str:
    """Shared panel body: header, notepad switcher, title, editor, footer.

    ``embedded`` toggles the close/pop-out affordances that only make sense
    inside the dashboard (the standalone window has no FAB to collapse into and
    is already its own window, so it hides them).
    """
    popout_btn = (
        '<button type="button" class="sfnote-icon-btn" id="sfnotePopout" '
        'title="Open in a new window" aria-label="Open notes in a new window">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/>'
        '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'
        '</button>'
        if embedded
        else ""
    )
    close_btn = (
        '<button type="button" class="sfnote-icon-btn" id="sfnoteClose" '
        'title="Close" aria-label="Close notes">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
        '</button>'
        if embedded
        else ""
    )
    return f"""
      <div class="sfnote-head">
        <div class="sfnote-head-titles">
          <span class="sfnote-title">Presenter notes</span>
          <span class="sfnote-sub" id="sfnoteClientLabel"></span>
        </div>
        <div class="sfnote-head-actions">
          {popout_btn}
          {close_btn}
        </div>
      </div>
      <div class="sfnote-toolbar">
        <label class="sfnote-sr-only" for="sfnoteSelect">Notepad</label>
        <select class="sfnote-select" id="sfnoteSelect" aria-label="Choose notepad"></select>
        <button type="button" class="sfnote-btn sfnote-btn--ghost" id="sfnoteNew" title="New note">+ New</button>
        <button type="button" class="sfnote-btn sfnote-btn--ghost sfnote-btn--danger" id="sfnoteDelete" title="Delete this note" aria-label="Delete this note">Delete</button>
      </div>
      <input type="text" class="sfnote-name" id="sfnoteName" placeholder="Note title" aria-label="Note title" autocomplete="off" spellcheck="false" maxlength="200">
      <textarea class="sfnote-body" id="sfnoteBody" placeholder="Type notes to have on hand while you present…" aria-label="Note body" spellcheck="true"></textarea>
      <div class="sfnote-footer">
        <span class="sfnote-status" id="sfnoteStatus" role="status" aria-live="polite"></span>
        <button type="button" class="sfnote-btn sfnote-btn--primary" id="sfnoteSave">Save</button>
      </div>
      <p class="sfnote-empty" id="sfnoteEmpty" hidden>No notes yet for this client. Click <strong>+ New</strong> to start one.</p>"""


# ── Styles ──────────────────────────────────────────────────────────────────
# Shared editor/panel styling. The FAB + fixed-panel chrome lives in
# ``_embedded_css`` (only needed inside the dashboard); the popup window reuses
# ``_shared_css`` on a plain full-height layout.
_SHARED_CSS = """
  .sfnote-sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  .sfnote-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; padding:14px 16px 12px; border-bottom:1px solid #e5eaf1; }
  .sfnote-head-titles { display:flex; flex-direction:column; min-width:0; }
  .sfnote-title { font-weight:700; font-size:.95rem; color:#0a2540; line-height:1.2; }
  .sfnote-sub { font-size:.76rem; color:#64748b; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sfnote-head-actions { display:flex; align-items:center; gap:4px; flex-shrink:0; }
  .sfnote-icon-btn { appearance:none; border:0; background:none; color:#64748b; cursor:pointer; width:30px; height:30px; border-radius:8px; display:inline-flex; align-items:center; justify-content:center; }
  .sfnote-icon-btn:hover { background:#f1f5f9; color:#0a2540; }
  .sfnote-icon-btn svg { width:17px; height:17px; }
  .sfnote-toolbar { display:flex; align-items:center; gap:6px; padding:12px 16px 8px; }
  .sfnote-select { flex:1 1 auto; min-width:0; appearance:none; border:1px solid #cbd5e1; border-radius:8px; background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E") no-repeat right 10px center; padding:8px 30px 8px 10px; font:inherit; font-size:.85rem; color:#0a2540; cursor:pointer; }
  .sfnote-btn { appearance:none; border:1px solid transparent; border-radius:8px; font:inherit; font-size:.83rem; font-weight:650; padding:8px 12px; cursor:pointer; white-space:nowrap; }
  .sfnote-btn--ghost { background:#fff; border-color:#cbd5e1; color:#334155; }
  .sfnote-btn--ghost:hover { background:#f1f5f9; }
  .sfnote-btn--danger { color:#b91c1c; }
  .sfnote-btn--danger:hover { background:#fef2f2; border-color:#fca5a5; }
  .sfnote-btn--primary { background:#0a2540; color:#fff; }
  .sfnote-btn--primary:hover { background:#123a63; }
  .sfnote-btn:disabled { opacity:.5; cursor:default; }
  .sfnote-name { margin:0 16px 8px; border:1px solid #cbd5e1; border-radius:8px; padding:8px 10px; font:inherit; font-size:.9rem; font-weight:650; color:#0a2540; }
  .sfnote-name:focus, .sfnote-select:focus, .sfnote-body:focus { outline:2px solid #93c5fd; outline-offset:0; border-color:#93c5fd; }
  .sfnote-body { flex:1 1 auto; min-height:120px; margin:0 16px; resize:none; border:1px solid #cbd5e1; border-radius:8px; padding:10px 12px; font:inherit; font-size:.9rem; line-height:1.55; color:#1e293b; }
  .sfnote-footer { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 16px 14px; }
  .sfnote-status { font-size:.76rem; color:#64748b; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sfnote-status.is-saved { color:#15803d; }
  .sfnote-status.is-error { color:#b91c1c; }
  .sfnote-empty { margin:0 16px 14px; font-size:.82rem; color:#64748b; }
"""

_EMBEDDED_CSS = """
  .sfnote-fab { position:fixed; bottom:24px; right:24px; z-index:200; display:inline-flex; align-items:center; gap:8px; height:44px; padding:0 16px 0 14px; border:0; border-radius:999px; background:#0a2540; color:#fff; font:inherit; font-size:.9rem; font-weight:650; cursor:pointer; box-shadow:0 3px 14px rgba(0,0,0,.28); transition:transform .15s, box-shadow .15s; }
  .sfnote-fab:hover { transform:translateY(-1px); box-shadow:0 6px 20px rgba(0,0,0,.3); }
  .sfnote-fab svg { width:18px; height:18px; }
  .sfnote-panel { position:fixed; bottom:24px; right:24px; z-index:201; width:min(380px, calc(100vw - 32px)); max-height:min(70vh, 620px); display:flex; flex-direction:column; background:#fff; border:1px solid #e2e8f0; border-radius:16px; box-shadow:0 18px 48px rgba(10,37,64,.24); opacity:0; transform:translateY(12px) scale(.98); transform-origin:bottom right; pointer-events:none; transition:opacity .18s, transform .18s; }
  body.sfnote-open .sfnote-panel { opacity:1; transform:none; pointer-events:auto; }
  body.sfnote-open .sfnote-fab { opacity:0; pointer-events:none; }
  .sfnote-panel .sfnote-body { min-height:180px; }
  @media (max-width:520px) { .sfnote-panel { left:16px; right:16px; bottom:16px; width:auto; } .sfnote-fab { right:16px; bottom:16px; } }
"""

_WINDOW_CSS = """
  * { box-sizing:border-box; }
  html, body { height:100%; }
  body { margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#eef2f7; color:#0a2540; }
  .sfnote-window { display:flex; flex-direction:column; height:100vh; max-width:760px; margin:0 auto; background:#fff; }
  .sfnote-window .sfnote-body { min-height:0; }
"""


# ── Controller (static; reads window.__sfNotesCfg) ──────────────────────────
_CONTROLLER_JS = r"""
(function () {
  var cfg = window.__sfNotesCfg;
  if (!cfg || !cfg.base) return;
  var root = document;

  var els = {
    select: root.getElementById('sfnoteSelect'),
    name: root.getElementById('sfnoteName'),
    body: root.getElementById('sfnoteBody'),
    save: root.getElementById('sfnoteSave'),
    del: root.getElementById('sfnoteDelete'),
    neu: root.getElementById('sfnoteNew'),
    status: root.getElementById('sfnoteStatus'),
    empty: root.getElementById('sfnoteEmpty'),
    label: root.getElementById('sfnoteClientLabel'),
    popout: root.getElementById('sfnotePopout'),
  };
  if (!els.select || !els.body) return;
  if (els.label) els.label.textContent = cfg.label || '';

  var notes = [];         // [{id,title,updated_at,updated_by}]
  var currentId = null;   // id of the note in the editor
  var dirty = false;
  var saveTimer = null;
  var loadedBody = '';     // last-loaded body (to detect real edits)
  var loadedTitle = '';

  function setStatus(msg, kind) {
    if (!els.status) return;
    els.status.textContent = msg || '';
    els.status.className = 'sfnote-status' + (kind ? ' is-' + kind : '');
  }
  function fmtWhen(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      if (isNaN(d)) return '';
      return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (e) { return ''; }
  }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Accept': 'application/json' }, opts.headers || {});
    opts.credentials = 'same-origin';
    return fetch(cfg.base + (path || ''), opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || (j && j.ok === false)) {
          throw new Error((j && j.error) || ('Request failed (' + r.status + ')'));
        }
        return j;
      });
    });
  }

  function renderOptions() {
    var html = '';
    for (var i = 0; i < notes.length; i++) {
      var n = notes[i];
      var t = (n.title || 'Untitled note');
      html += '<option value="' + n.id + '">' + escapeHtml(t) + '</option>';
    }
    els.select.innerHTML = html;
    if (currentId != null) els.select.value = String(currentId);
    var has = notes.length > 0;
    els.select.style.display = has ? '' : 'none';
    if (els.empty) els.empty.hidden = has;
    if (els.del) els.del.disabled = !has;
    els.name.style.display = has ? '' : 'none';
    els.body.style.display = has ? '' : 'none';
    if (els.save) els.save.disabled = !has;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function loadInto(note) {
    currentId = note ? note.id : null;
    loadedBody = note ? (note.body || '') : '';
    loadedTitle = note ? (note.title || '') : '';
    els.name.value = loadedTitle;
    els.body.value = loadedBody;
    dirty = false;
    if (currentId != null) els.select.value = String(currentId);
    if (note) setStatus('Saved ' + fmtWhen(note.updated_at), 'saved');
    else setStatus('');
  }

  function openNote(id) {
    if (id == null) { loadInto(null); return; }
    setStatus('Loading…');
    api('/' + id).then(function (j) { loadInto(j.notepad); }).catch(function (e) {
      setStatus(e.message || 'Could not load note', 'error');
    });
  }

  function refreshList(preferId) {
    return api('').then(function (j) {
      notes = (j && j.notepads) || [];
      var want = preferId != null ? preferId : currentId;
      var found = null;
      for (var i = 0; i < notes.length; i++) { if (notes[i].id === want) { found = notes[i]; break; } }
      renderOptions();
      if (notes.length === 0) { loadInto(null); return; }
      if (found) { currentId = found.id; els.select.value = String(found.id); openNote(found.id); }
      else { currentId = notes[0].id; els.select.value = String(notes[0].id); openNote(notes[0].id); }
    }).catch(function (e) {
      setStatus(e.message || 'Could not load notes', 'error');
    });
  }

  function save(opts) {
    opts = opts || {};
    if (currentId == null && !opts.create) return Promise.resolve();
    var title = els.name.value;
    var body = els.body.value;
    var form = new URLSearchParams();
    form.set('title', title);
    form.set('body', body);
    var path = (currentId != null && !opts.create) ? ('/' + currentId) : '';
    setStatus('Saving…');
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
    }).then(function (j) {
      var n = j.notepad;
      if (n) {
        currentId = n.id;
        loadedBody = n.body || '';
        loadedTitle = n.title || '';
        dirty = false;
        // Keep the list/dropdown label in sync.
        var known = false;
        for (var i = 0; i < notes.length; i++) { if (notes[i].id === n.id) { notes[i].title = n.title; notes[i].updated_at = n.updated_at; known = true; break; } }
        if (!known) notes.unshift({ id: n.id, title: n.title, updated_at: n.updated_at, updated_by: n.updated_by });
        renderOptions();
        els.select.value = String(n.id);
        setStatus('Saved ' + fmtWhen(n.updated_at), 'saved');
      }
      return n;
    }).catch(function (e) {
      setStatus(e.message || 'Save failed', 'error');
    });
  }

  function scheduleSave() {
    if (currentId == null) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { if (dirty) save(); }, 1200);
  }

  function onEdit() {
    dirty = (els.body.value !== loadedBody) || (els.name.value !== loadedTitle);
    if (dirty) setStatus('Unsaved changes…');
    scheduleSave();
  }

  // ── wire up ──
  els.body.addEventListener('input', onEdit);
  els.name.addEventListener('input', onEdit);
  if (els.save) els.save.addEventListener('click', function () { save(); });
  els.select.addEventListener('change', function () {
    var id = parseInt(els.select.value, 10);
    var go = function () { openNote(id); };
    if (dirty) { save().then(go); } else { go(); }
  });
  if (els.neu) els.neu.addEventListener('click', function () {
    var proceed = function () {
      els.name.value = '';
      els.body.value = '';
      currentId = null;
      loadedBody = ''; loadedTitle = '';
      save({ create: true }).then(function () { els.name.focus(); });
    };
    if (dirty) { save().then(proceed); } else { proceed(); }
  });
  if (els.del) els.del.addEventListener('click', function () {
    if (currentId == null) return;
    if (!window.confirm('Delete this note? This cannot be undone.')) return;
    var gone = currentId;
    setStatus('Deleting…');
    api('/' + gone + '/delete', { method: 'POST' }).then(function () {
      notes = notes.filter(function (n) { return n.id !== gone; });
      currentId = null;
      renderOptions();
      if (notes.length) { currentId = notes[0].id; openNote(notes[0].id); }
      else { loadInto(null); setStatus('Note deleted', 'saved'); }
    }).catch(function (e) { setStatus(e.message || 'Delete failed', 'error'); });
  });

  // Flush a pending edit when the surface is hidden/closed so nothing is lost.
  window.addEventListener('beforeunload', function () { if (dirty) save(); });
  root.addEventListener('visibilitychange', function () { if (root.hidden && dirty) save(); });

  // When focus returns (e.g. after editing in the popped-out window), refresh
  // the list + current note if the editor has no unsaved local changes.
  window.addEventListener('focus', function () { if (!dirty) refreshList(); });

  window.__sfNotesRefresh = refreshList;
  refreshList();
})();
"""


def widget_html(*, client_slug: str, label: str) -> str:
    """FAB + slide-up notes panel for injection before ``</body>`` on a dashboard.

    The panel is populated client-side from the notes API; caller must only
    render this for agency users (the routes enforce it too).
    """
    fab = (
        f'<button type="button" class="sfnote-fab" id="sfnoteFab" '
        f'aria-label="Open presenter notes" aria-expanded="false">'
        f'{_ICON_NOTE}<span>Notes</span></button>'
    )
    panel = (
        '<aside class="sfnote-panel" id="sfnotePanel" role="dialog" '
        'aria-label="Presenter notes" aria-hidden="true">'
        f'{_panel_inner_html(embedded=True)}'
        '</aside>'
    )
    toggle_js = """
    (function(){
      var fab=document.getElementById('sfnoteFab');
      var panel=document.getElementById('sfnotePanel');
      var close=document.getElementById('sfnoteClose');
      var popout=document.getElementById('sfnotePopout');
      var cfg=window.__sfNotesCfg||{};
      if(!fab||!panel) return;
      function setOpen(open){
        document.body.classList.toggle('sfnote-open',open);
        fab.setAttribute('aria-expanded',open?'true':'false');
        panel.setAttribute('aria-hidden',open?'false':'true');
        if(open){var b=document.getElementById('sfnoteBody');if(b)setTimeout(function(){b.focus();},60);}
      }
      fab.addEventListener('click',function(){setOpen(true);});
      if(close)close.addEventListener('click',function(){setOpen(false);});
      document.addEventListener('keydown',function(e){if(e.key==='Escape'&&document.body.classList.contains('sfnote-open'))setOpen(false);});
      if(popout)popout.addEventListener('click',function(){
        window.open(cfg.base+'/window','sfnotes_'+(cfg.slug||''),'width=460,height=640,menubar=no,toolbar=no,location=no,status=no');
        setOpen(false);
      });
    })();
    """
    return f"""
    {_config_script(client_slug=client_slug, label=label, embedded=True)}
    <style>{_SHARED_CSS}{_EMBEDDED_CSS}</style>
    {fab}
    {panel}
    <script>{_CONTROLLER_JS}</script>
    <script>{toggle_js}</script>"""


def window_page_html(*, client_slug: str, label: str) -> str:
    """Standalone popup page for the "open in a new window" presenter pad."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Notes — {_esc(label)}</title>
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <style>{_WINDOW_CSS}{_SHARED_CSS}</style>
</head>
<body>
  {_config_script(client_slug=client_slug, label=label, embedded=False)}
  <div class="sfnote-window">
    {_panel_inner_html(embedded=False)}
  </div>
  <script>{_CONTROLLER_JS}</script>
</body>
</html>"""
