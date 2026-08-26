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

# The collapsed FAB glyph — a pencil-in-a-spark mark that reads as "jot / act".
_ICON_FAB = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>'
)

_ICON_COMMENT = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8z"/></svg>'
)

_ICON_SPARK = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 3v4"/><path d="M12 17v4"/><path d="M3 12h4"/><path d="M17 12h4"/>'
    '<path d="M12 8a4 4 0 0 0 4 4 4 4 0 0 0-4 4 4 4 0 0 0-4-4 4 4 0 0 0 4-4z"/></svg>'
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


def _fr_panel_inner_html() -> str:
    """Feature-request composer: the page it was raised from + a request body.

    Submissions land in the super-admin inbox on ``/admin`` and light up the
    notification badge there. The current page (URL + label) is captured
    client-side so the reviewer knows exactly where the ask came from.
    """
    close_btn = (
        '<button type="button" class="sfnote-icon-btn" id="sffrClose" '
        'title="Close" aria-label="Close feature request">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
        '</button>'
    )
    return f"""
      <div class="sfnote-head">
        <div class="sfnote-head-titles">
          <span class="sfnote-title">Feature request</span>
          <span class="sfnote-sub">Sent to the Sagefrog admin inbox</span>
        </div>
        <div class="sfnote-head-actions">{close_btn}</div>
      </div>
      <div class="sffr-context" id="sffrContext" aria-live="polite"></div>
      <textarea class="sfnote-body sffr-body" id="sffrBody" placeholder="What would make this dashboard better? Describe the feature or fix you'd like…" aria-label="Feature request" spellcheck="true"></textarea>
      <label class="sffr-scope" for="sffrScope">
        <input type="checkbox" id="sffrScope">
        <span class="sffr-scope-text">
          <span class="sffr-scope-label">This client only</span>
          <span class="sffr-scope-hint">Leave unchecked if this should apply to every client's dashboard.</span>
        </span>
      </label>
      <div class="sfnote-footer">
        <span class="sfnote-status" id="sffrStatus" role="status" aria-live="polite"></span>
        <button type="button" class="sfnote-btn sfnote-btn--primary" id="sffrSend">Send request</button>
      </div>"""


def _comment_panel_inner_html() -> str:
    """Comment composer + the thread already on this page.

    Unlike a feature request (which goes to the admin inbox), a comment is
    addressed to the people staffed on *this* client — so the subtitle says who
    will hear about it, and the thread above the box shows what has already been
    said here.
    """
    close_btn = (
        '<button type="button" class="sfnote-icon-btn" id="sfcmClose" '
        'title="Close" aria-label="Close comments">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
        '</button>'
    )
    return f"""
      <div class="sfnote-head">
        <div class="sfnote-head-titles">
          <span class="sfnote-title">Comments</span>
          <span class="sfnote-sub">The team on this account is notified</span>
        </div>
        <div class="sfnote-head-actions">{close_btn}</div>
      </div>
      <div class="sfcm-context" id="sfcmContext" aria-live="polite"></div>
      <div class="sfcm-thread" id="sfcmThread" role="log" aria-label="Comments on this page"></div>
      <div class="sfcm-reply-to" id="sfcmReplyTo" hidden></div>
      <textarea class="sfnote-body sfcm-body" id="sfcmBody" placeholder="Leave a comment about this page…" aria-label="Comment" spellcheck="true"></textarea>
      <div class="sfnote-footer">
        <span class="sfnote-status" id="sfcmStatus" role="status" aria-live="polite"></span>
        <button type="button" class="sfnote-btn sfnote-btn--primary" id="sfcmSend">Comment</button>
      </div>"""


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
  .sffr-scope { display:flex; align-items:flex-start; gap:9px; margin:10px 16px 2px; cursor:pointer; }
  .sffr-scope input { margin:2px 0 0; width:15px; height:15px; flex-shrink:0; accent-color:#7c3aed; cursor:pointer; }
  .sffr-scope-text { display:flex; flex-direction:column; gap:1px; min-width:0; }
  .sffr-scope-label { font-size:.82rem; font-weight:650; color:#0a2540; line-height:1.3; }
  .sffr-scope-hint { font-size:.73rem; color:#64748b; line-height:1.35; }
"""

_EMBEDDED_CSS = """
  /* Sleek round FAB that fans out a labelled menu on hover/focus. The whole
     dock is one hover target so the menu doesn't flicker as the pointer travels
     from the button up to the actions. */
  .sfnote-dock { position:fixed; bottom:24px; right:24px; z-index:200; display:flex; flex-direction:column; align-items:flex-end; gap:10px; }
  .sfnote-fab { display:inline-flex; align-items:center; justify-content:center; width:54px; height:54px; padding:0; border:0; border-radius:999px; background:linear-gradient(135deg,#123a63,#0a2540); color:#fff; cursor:pointer; box-shadow:0 6px 18px rgba(10,37,64,.32); transition:transform .16s, box-shadow .16s; }
  .sfnote-fab:hover, .sfnote-fab:focus-visible { transform:translateY(-2px); box-shadow:0 10px 26px rgba(10,37,64,.38); outline:none; }
  .sfnote-fab svg { width:22px; height:22px; }
  .sfnote-menu { display:flex; flex-direction:column; align-items:flex-end; gap:8px; opacity:0; transform:translateY(8px) scale(.96); transform-origin:bottom right; pointer-events:none; transition:opacity .16s, transform .16s; }
  .sfnote-dock:hover .sfnote-menu, .sfnote-dock:focus-within .sfnote-menu, .sfnote-dock.is-open .sfnote-menu { opacity:1; transform:none; pointer-events:auto; }
  .sfnote-menu-item { display:inline-flex; align-items:center; gap:9px; height:40px; padding:0 15px; border:1px solid #e2e8f0; border-radius:999px; background:#fff; color:#0a2540; font:inherit; font-size:.86rem; font-weight:650; cursor:pointer; white-space:nowrap; box-shadow:0 4px 14px rgba(10,37,64,.16); transition:transform .12s, box-shadow .12s, border-color .12s; }
  .sfnote-menu-item:hover { transform:translateY(-1px); border-color:#c6d5ea; box-shadow:0 6px 18px rgba(10,37,64,.22); }
  .sfnote-menu-item svg { width:16px; height:16px; color:#123a63; }
  .sfnote-menu-item--fr svg { color:#7c3aed; }
  /* Collapse the dock while a panel is open so the FAB doesn't cover it. */
  body.sfnote-open .sfnote-dock, body.sffr-open .sfnote-dock, body.sfcm-open .sfnote-dock { opacity:0; pointer-events:none; }
  .sfnote-panel { position:fixed; bottom:24px; right:24px; z-index:201; width:min(380px, calc(100vw - 32px)); max-height:min(70vh, 620px); display:flex; flex-direction:column; background:#fff; border:1px solid #e2e8f0; border-radius:16px; box-shadow:0 18px 48px rgba(10,37,64,.24); opacity:0; transform:translateY(12px) scale(.98); transform-origin:bottom right; pointer-events:none; transition:opacity .18s, transform .18s; }
  body.sfnote-open .sfnote-panel { opacity:1; transform:none; pointer-events:auto; }
  .sfnote-panel .sfnote-body { min-height:180px; }
  /* Feature-request panel reuses the notes-panel chrome. */
  .sffr-panel { position:fixed; bottom:24px; right:24px; z-index:201; width:min(380px, calc(100vw - 32px)); max-height:min(70vh, 620px); display:flex; flex-direction:column; background:#fff; border:1px solid #e2e8f0; border-radius:16px; box-shadow:0 18px 48px rgba(10,37,64,.24); opacity:0; transform:translateY(12px) scale(.98); transform-origin:bottom right; pointer-events:none; transition:opacity .18s, transform .18s; }
  body.sffr-open .sffr-panel { opacity:1; transform:none; pointer-events:auto; }
  /* Comments panel reuses the same floating-card chrome. */
  .sfcm-panel { position:fixed; bottom:24px; right:24px; z-index:201; width:min(380px, calc(100vw - 32px)); max-height:min(76vh, 660px); display:flex; flex-direction:column; background:#fff; border:1px solid #e2e8f0; border-radius:16px; box-shadow:0 18px 48px rgba(10,37,64,.24); opacity:0; transform:translateY(12px) scale(.98); transform-origin:bottom right; pointer-events:none; transition:opacity .18s, transform .18s; }
  body.sfcm-open .sfcm-panel { opacity:1; transform:none; pointer-events:auto; }
  .sfnote-menu-item--cm svg { color:#2563eb; }
  .sffr-context { display:flex; align-items:center; gap:8px; margin:12px 16px 6px; padding:8px 11px; border-radius:9px; background:#f5f3ff; border:1px solid #e4defb; color:#4c1d95; font-size:.78rem; }
  .sffr-context svg { width:14px; height:14px; flex-shrink:0; }
  .sffr-context .sffr-page { font-weight:650; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sffr-body { min-height:150px; margin-top:6px; }
  /* Comments panel: a scrolling thread above a short composer. */
  .sfcm-context { display:flex; align-items:center; gap:8px; margin:12px 16px 6px; padding:8px 11px; border-radius:9px; background:#eff6ff; border:1px solid #d6e4f7; color:#0a2540; font-size:.78rem; }
  .sfcm-context svg { width:14px; height:14px; flex-shrink:0; }
  .sfcm-context .sffr-page { font-weight:650; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sfcm-thread { flex:1 1 auto; min-height:80px; overflow-y:auto; padding:4px 16px 0; display:flex; flex-direction:column; gap:10px; }
  .sfcm-empty { color:#64748b; font-size:.8rem; padding:10px 0; }
  .sfcm-item { border-left:2px solid #e2e8f0; padding-left:10px; }
  .sfcm-item.is-reply { margin-left:16px; border-left-color:#cbd5e1; }
  .sfcm-item-head { display:flex; align-items:baseline; gap:6px; flex-wrap:wrap; }
  .sfcm-author { font-size:.8rem; font-weight:700; color:#0a2540; }
  .sfcm-when { font-size:.72rem; color:#94a3b8; }
  .sfcm-text { margin:2px 0 0; font-size:.84rem; color:#334155; white-space:pre-wrap; overflow-wrap:anywhere; }
  .sfcm-actions { display:flex; gap:10px; margin-top:3px; }
  .sfcm-action { appearance:none; border:0; background:none; padding:0; font:inherit; font-size:.73rem; font-weight:650; color:#64748b; cursor:pointer; }
  .sfcm-action:hover { color:#0a2540; text-decoration:underline; }
  .sfcm-action.is-danger:hover { color:#b91c1c; }
  .sfcm-reply-to { display:flex; align-items:center; justify-content:space-between; gap:8px; margin:8px 16px 0; padding:6px 10px; border-radius:8px; background:#f1f5f9; color:#334155; font-size:.76rem; }
  .sfcm-body { min-height:70px; margin-top:6px; }
  @media (max-width:520px) { .sfnote-dock { right:16px; bottom:16px; } .sfnote-panel, .sffr-panel, .sfcm-panel { left:16px; right:16px; bottom:16px; width:auto; } }
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
  // Default title for a new note: today's date in the viewer's locale, e.g.
  // "July 18, 2026". Prefilled so the note is dated out of the box but still
  // editable; the server applies the same fallback (in UTC) if left blank.
  function defaultTitle() {
    try {
      return new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
    } catch (e) { return ''; }
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
      els.name.value = defaultTitle();
      els.body.value = '';
      currentId = null;
      loadedBody = ''; loadedTitle = '';
      // Select the prefilled title so the user can overtype it immediately.
      save({ create: true }).then(function () { els.name.focus(); els.name.select(); });
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


# ── Feature-request composer (static; reads window.__sfNotesCfg) ────────────
# Captures the current page + a free-text ask and POSTs it to the client-scoped
# feature-request endpoint. The submission lands in the super-admin inbox on
# /admin and lights the notification badge there.
_FEATURE_REQUEST_JS = r"""
(function () {
  var cfg = window.__sfNotesCfg;
  if (!cfg || !cfg.base) return;
  var body = document.getElementById('sffrBody');
  var send = document.getElementById('sffrSend');
  var status = document.getElementById('sffrStatus');
  var context = document.getElementById('sffrContext');
  var scope = document.getElementById('sffrScope');
  if (!body || !send) return;

  var PIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-6-5.686-6-10a6 6 0 0 1 12 0c0 4.314-6 10-6 10z"/><circle cx="12" cy="11" r="2"/></svg>';

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function currentPath() {
    try { return window.location.pathname + window.location.search; } catch (e) { return ''; }
  }
  function pageLabel() {
    return cfg.label || '';
  }
  function setStatus(msg, kind) {
    if (!status) return;
    status.textContent = msg || '';
    status.className = 'sfnote-status' + (kind ? ' is-' + kind : '');
  }
  // Fill the "raised from" chip when the panel opens (path can change as the
  // presenter navigates within the dashboard SPA-style).
  window.__sfFrPrime = function () {
    if (!context) return;
    var lbl = pageLabel() || currentPath() || 'this page';
    context.innerHTML = PIN + '<span class="sffr-page">' + escapeHtml(lbl) + '</span>';
  };

  send.addEventListener('click', function () {
    var text = (body.value || '').trim();
    if (!text) { setStatus('Add a short description first.', 'error'); body.focus(); return; }
    send.disabled = true;
    setStatus('Sending…');
    var form = new URLSearchParams();
    form.set('body', text);
    form.set('page', currentPath());
    form.set('page_label', pageLabel());
    form.set('scope', (scope && scope.checked) ? 'client' : 'global');
    fetch(cfg.base + '/feature-request', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' },
      body: form.toString(),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || (j && j.ok === false)) {
          throw new Error((j && j.error) || ('Request failed (' + r.status + ')'));
        }
        return j;
      });
    }).then(function () {
      body.value = '';
      if (scope) scope.checked = false;
      setStatus('Sent to the Sagefrog admin inbox. Thank you!', 'saved');
    }).catch(function (e) {
      setStatus(e.message || 'Could not send request', 'error');
    }).then(function () {
      send.disabled = false;
    });
  });
})();
"""


# ── Comments controller (static; reads window.__sfNotesCfg) ────────────────
# Loads the thread for the page the user is standing on, posts new comments and
# replies, and lets an author remove their own. Every write notifies the
# account's assigned team server-side (see page_comments), which is what makes
# the panel worth opening: the reply lands in someone's inbox, not just here.
_COMMENTS_JS = r"""
(function () {
  var cfg = window.__sfNotesCfg;
  if (!cfg || !cfg.base) return;
  var thread = document.getElementById('sfcmThread');
  var body = document.getElementById('sfcmBody');
  var send = document.getElementById('sfcmSend');
  var status = document.getElementById('sfcmStatus');
  var context = document.getElementById('sfcmContext');
  var replyTo = document.getElementById('sfcmReplyTo');
  if (!thread || !body || !send) return;

  var PIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-6-5.686-6-10a6 6 0 0 1 12 0c0 4.314-6 10-6 10z"/><circle cx="12" cy="11" r="2"/></svg>';
  var me = '';
  var parentId = null;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"\']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function currentPath() {
    try { return window.location.pathname + window.location.search; } catch (e) { return ''; }
  }
  function setStatus(msg, kind) {
    if (!status) return;
    status.textContent = msg || '';
    status.className = 'sfnote-status' + (kind ? ' is-' + kind : '');
  }
  // "3h ago" — recency is what matters in a thread you are standing in.
  function ago(iso) {
    if (!iso) return '';
    var then = Date.parse(iso);
    if (isNaN(then)) return '';
    var mins = Math.floor((Date.now() - then) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    var hours = Math.floor(mins / 60);
    if (hours < 24) return hours + 'h ago';
    return Math.floor(hours / 24) + 'd ago';
  }
  function clearReplyTarget() {
    parentId = null;
    if (replyTo) { replyTo.hidden = true; replyTo.innerHTML = ''; }
    body.placeholder = 'Leave a comment about this page…';
    send.textContent = 'Comment';
  }
  function setReplyTarget(id, author) {
    parentId = id;
    if (replyTo) {
      replyTo.hidden = false;
      replyTo.innerHTML = '<span>Replying to ' + escapeHtml(author || 'this thread') + '</span>' +
        '<button type="button" class="sfcm-action" data-cancel-reply>Cancel</button>';
    }
    body.placeholder = 'Write a reply…';
    send.textContent = 'Reply';
    body.focus();
  }
  function itemHtml(c) {
    var mine = me && c.created_by && c.created_by.toLowerCase() === me.toLowerCase();
    var actions = '<button type="button" class="sfcm-action" data-reply="' + c.id + '" ' +
      'data-author="' + escapeHtml(c.author_name || '') + '">Reply</button>';
    if (mine) {
      actions += '<button type="button" class="sfcm-action is-danger" data-delete="' + c.id + '">Delete</button>';
    }
    return '<div class="sfcm-item' + (c.parent_id ? ' is-reply' : '') + '">' +
      '<div class="sfcm-item-head"><span class="sfcm-author">' + escapeHtml(c.author_name || 'Someone') + '</span>' +
      '<span class="sfcm-when">' + escapeHtml(ago(c.created_at)) + '</span></div>' +
      '<p class="sfcm-text">' + escapeHtml(c.body || '') + '</p>' +
      '<div class="sfcm-actions">' + actions + '</div></div>';
  }
  function render(comments) {
    if (!comments || !comments.length) {
      thread.innerHTML = '<p class="sfcm-empty">No comments on this page yet. Start the thread — ' +
        'everyone on this account hears about it.</p>';
      return;
    }
    // Replies follow their root so the thread reads top to bottom.
    var roots = comments.filter(function (c) { return !c.parent_id; });
    var byParent = {};
    comments.forEach(function (c) {
      if (!c.parent_id) return;
      (byParent[c.parent_id] = byParent[c.parent_id] || []).push(c);
    });
    var html = '';
    roots.forEach(function (root) {
      html += itemHtml(root);
      (byParent[root.id] || []).forEach(function (reply) { html += itemHtml(reply); });
    });
    thread.innerHTML = html;
    thread.scrollTop = thread.scrollHeight;
  }
  function load() {
    thread.innerHTML = '<p class="sfcm-empty">Loading…</p>';
    fetch(cfg.base + '/comments?page=' + encodeURIComponent(currentPath()), {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (!j || j.ok === false) throw new Error('Could not load comments');
      me = j.me || '';
      render(j.comments || []);
    }).catch(function () {
      thread.innerHTML = '<p class="sfcm-empty">Could not load comments.</p>';
    });
  }

  // Prime when the panel opens: the path changes as the user moves around the
  // dashboard, so both the chip and the thread are per-open, not per-load.
  window.__sfCmPrime = function () {
    if (context) {
      var lbl = cfg.label || currentPath() || 'this page';
      context.innerHTML = PIN + '<span class="sffr-page">' + escapeHtml(lbl) + '</span>';
    }
    clearReplyTarget();
    setStatus('');
    load();
  };

  thread.addEventListener('click', function (e) {
    var replyBtn = e.target.closest('[data-reply]');
    if (replyBtn) {
      setReplyTarget(replyBtn.getAttribute('data-reply'), replyBtn.getAttribute('data-author'));
      return;
    }
    var delBtn = e.target.closest('[data-delete]');
    if (!delBtn) return;
    if (!window.confirm('Delete this comment?')) return;
    fetch(cfg.base + '/comments/' + delBtn.getAttribute('data-delete') + '/delete', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    }).then(function (r) {
      if (!r.ok) throw new Error('Could not delete');
      load();
    }).catch(function () { setStatus('Could not delete that comment', 'error'); });
  });

  if (replyTo) {
    replyTo.addEventListener('click', function (e) {
      if (e.target.closest('[data-cancel-reply]')) clearReplyTarget();
    });
  }

  send.addEventListener('click', function () {
    var text = (body.value || '').trim();
    if (!text) { setStatus('Write something first.', 'error'); body.focus(); return; }
    send.disabled = true;
    setStatus('Sending…');
    var form = new URLSearchParams();
    form.set('body', text);
    form.set('page', currentPath());
    form.set('page_label', cfg.label || '');
    if (parentId) form.set('parent_id', parentId);
    fetch(cfg.base + '/comments', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' },
      body: form.toString(),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || (j && j.ok === false)) {
          throw new Error((j && j.detail) || ('Request failed (' + r.status + ')'));
        }
        return j;
      });
    }).then(function () {
      body.value = '';
      clearReplyTarget();
      setStatus('Posted — the team on this account has been notified.', 'saved');
      load();
    }).catch(function (e) {
      setStatus(e.message || 'Could not post comment', 'error');
    }).then(function () {
      send.disabled = false;
    });
  });
})();
"""


def widget_html(*, client_slug: str, label: str) -> str:
    """FAB + slide-up notes panel for injection before ``</body>`` on a dashboard.

    The panel is populated client-side from the notes API; caller must only
    render this for agency users (the routes enforce it too).
    """
    dock = (
        '<div class="sfnote-dock" id="sfnoteDock">'
        '<div class="sfnote-menu" id="sfnoteMenu" role="menu" aria-label="Notes actions">'
        '<button type="button" class="sfnote-menu-item" id="sfnoteOpen" role="menuitem">'
        f'{_ICON_NOTE}<span>Notes</span></button>'
        '<button type="button" class="sfnote-menu-item sfnote-menu-item--cm" id="sfcmOpen" role="menuitem">'
        f'{_ICON_COMMENT}<span>Comment</span></button>'
        '<button type="button" class="sfnote-menu-item sfnote-menu-item--fr" id="sffrOpen" role="menuitem">'
        f'{_ICON_SPARK}<span>Feature request</span></button>'
        '</div>'
        '<button type="button" class="sfnote-fab" id="sfnoteFab" '
        'aria-haspopup="true" aria-expanded="false" '
        'aria-label="Notes, comments and feature requests">'
        f'{_ICON_FAB}</button>'
        '</div>'
    )
    panel = (
        '<aside class="sfnote-panel" id="sfnotePanel" role="dialog" '
        'aria-label="Presenter notes" aria-hidden="true">'
        f'{_panel_inner_html(embedded=True)}'
        '</aside>'
    )
    fr_panel = (
        '<aside class="sffr-panel" id="sffrPanel" role="dialog" '
        'aria-label="Feature request" aria-hidden="true">'
        f'{_fr_panel_inner_html()}'
        '</aside>'
    )
    comment_panel = (
        '<aside class="sfcm-panel" id="sfcmPanel" role="dialog" '
        'aria-label="Comments on this page" aria-hidden="true">'
        f'{_comment_panel_inner_html()}'
        '</aside>'
    )
    toggle_js = """
    (function(){
      var dock=document.getElementById('sfnoteDock');
      var fab=document.getElementById('sfnoteFab');
      var notesOpen=document.getElementById('sfnoteOpen');
      var frOpen=document.getElementById('sffrOpen');
      var cmOpen=document.getElementById('sfcmOpen');
      var panel=document.getElementById('sfnotePanel');
      var frPanel=document.getElementById('sffrPanel');
      var cmPanel=document.getElementById('sfcmPanel');
      var close=document.getElementById('sfnoteClose');
      var frClose=document.getElementById('sffrClose');
      var cmClose=document.getElementById('sfcmClose');
      var popout=document.getElementById('sfnotePopout');
      var cfg=window.__sfNotesCfg||{};
      if(!dock||!fab) return;

      // Pointer devices reveal the menu on hover (CSS); this toggle is the
      // keyboard/touch affordance so the FAB works without a hover.
      fab.addEventListener('click',function(){
        var open=dock.classList.toggle('is-open');
        fab.setAttribute('aria-expanded',open?'true':'false');
      });
      function closeMenu(){dock.classList.remove('is-open');fab.setAttribute('aria-expanded','false');}
      document.addEventListener('click',function(e){if(!dock.contains(e.target))closeMenu();});

      function setNotesOpen(open){
        document.body.classList.toggle('sfnote-open',open);
        if(panel)panel.setAttribute('aria-hidden',open?'false':'true');
        if(open){closeMenu();var b=document.getElementById('sfnoteBody');if(b)setTimeout(function(){b.focus();},60);}
      }
      function setFrOpen(open){
        document.body.classList.toggle('sffr-open',open);
        if(frPanel)frPanel.setAttribute('aria-hidden',open?'false':'true');
        if(open){closeMenu();if(window.__sfFrPrime)window.__sfFrPrime();var b=document.getElementById('sffrBody');if(b)setTimeout(function(){b.focus();},60);}
      }

      function setCmOpen(open){
        document.body.classList.toggle('sfcm-open',open);
        if(cmPanel)cmPanel.setAttribute('aria-hidden',open?'false':'true');
        if(open){closeMenu();if(window.__sfCmPrime)window.__sfCmPrime();var b=document.getElementById('sfcmBody');if(b)setTimeout(function(){b.focus();},60);}
      }

      if(notesOpen)notesOpen.addEventListener('click',function(){setNotesOpen(true);});
      if(frOpen)frOpen.addEventListener('click',function(){setFrOpen(true);});
      if(cmOpen)cmOpen.addEventListener('click',function(){setCmOpen(true);});
      if(close)close.addEventListener('click',function(){setNotesOpen(false);});
      if(frClose)frClose.addEventListener('click',function(){setFrOpen(false);});
      if(cmClose)cmClose.addEventListener('click',function(){setCmOpen(false);});
      document.addEventListener('keydown',function(e){
        if(e.key!=='Escape')return;
        if(document.body.classList.contains('sfnote-open'))setNotesOpen(false);
        else if(document.body.classList.contains('sffr-open'))setFrOpen(false);
        else if(document.body.classList.contains('sfcm-open'))setCmOpen(false);
        else closeMenu();
      });
      if(popout)popout.addEventListener('click',function(){
        window.open(cfg.base+'/window','sfnotes_'+(cfg.slug||''),'width=460,height=640,menubar=no,toolbar=no,location=no,status=no');
        setNotesOpen(false);
      });
    })();
    """
    return f"""
    {_config_script(client_slug=client_slug, label=label, embedded=True)}
    <style>{_SHARED_CSS}{_EMBEDDED_CSS}</style>
    {dock}
    {panel}
    {fr_panel}
    {comment_panel}
    <script>{_CONTROLLER_JS}</script>
    <script>{_FEATURE_REQUEST_JS}</script>
    <script>{_COMMENTS_JS}</script>
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
