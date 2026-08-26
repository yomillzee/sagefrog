"""The signed-in Sagefrog user's notification inbox.

One page, one person: what has been said on the accounts they are staffed on
(see ``client_team``), newest first, unread called out. It reuses the admin
navy-sidebar shell so it looks like the rest of the portal, but it is *not* an
admin page — every agency user has one, so a 'standard' user gets the same shell
with a nav trimmed to the destinations they can actually open.

Rows link through ``/notifications/{id}/open``, which marks the row read and
then forwards to the page the comment was left on. That way opening a
notification the way anyone would — by clicking it — is what clears it, rather
than a separate "mark read" chore.
"""

from __future__ import annotations

from datetime import UTC, datetime

import notifications as notifications_store
from dashboard.renderers.base_layout import (
    _ADMIN_NAV_ICONS,
    render_admin_shell_page,
)
from dashboard.utils.formatting import esc as _esc

_CSS = """
    .nt-main { width: 100%; max-width: 860px; margin: 0 auto; padding: 28px 32px 56px; }
    .nt-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
    .nt-head h1 { margin: 0 0 4px; font-size: 1.6rem; color: #0a2540; }
    .nt-head p { margin: 0; color: #64748b; font-size: .92rem; }
    .nt-readall { appearance: none; border: 1px solid #cbd5e1; background: #fff; color: #334155;
      font: inherit; font-size: .85rem; font-weight: 650; padding: 8px 14px; border-radius: 9px; cursor: pointer; }
    .nt-readall:hover { background: #f1f5f9; }
    .nt-list { display: flex; flex-direction: column; gap: 10px; list-style: none; margin: 0; padding: 0; }
    .nt-item { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; box-shadow: 0 1px 2px rgba(10,37,64,.04); }
    .nt-item.is-unread { border-color: #bfd4ee; background: #f8fbff; }
    .nt-link { display: flex; gap: 12px; padding: 14px 16px; text-decoration: none; color: inherit; align-items: flex-start; }
    .nt-link:hover { background: rgba(10,37,64,.03); border-radius: 12px; }
    .nt-dot { width: 8px; height: 8px; border-radius: 50%; background: #2563eb; margin-top: 7px; flex-shrink: 0; }
    .nt-dot.is-read { background: transparent; }
    .nt-body { min-width: 0; flex: 1; }
    .nt-title { margin: 0 0 3px; font-weight: 700; font-size: .95rem; color: #0a2540; }
    .nt-quote { margin: 0 0 6px; color: #334155; font-size: .9rem; overflow-wrap: anywhere;
      display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
    .nt-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; color: #64748b; font-size: .78rem; }
    .nt-chip { background: #eef2f7; border-radius: 999px; padding: 2px 9px; font-weight: 650; color: #475569; }
    .nt-empty { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 40px 24px; text-align: center; color: #64748b; }
    .nt-empty strong { display: block; color: #0a2540; font-size: 1rem; margin-bottom: 6px; }
    @media (max-width: 640px) { .nt-main { padding: 20px 16px 40px; } }
"""


def _relative_time(iso: str | None) -> str:
    """"3h ago" — a timestamp reads as recency here, not as a date."""
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = (datetime.now(tz=UTC) - when).total_seconds()
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d ago"
    # Older than a week reads as a date. ``%-d`` is POSIX-only (the app also runs
    # on Windows in development), so zero-strip by hand instead.
    return when.strftime("%b %d").replace(" 0", " ")


def _nav_html(*, is_admin: bool) -> str | None:
    """Admins keep the full admin menu; everyone else gets only what they can open."""
    if is_admin:
        return None
    icon = _ADMIN_NAV_ICONS.get("notifications", "")
    return (
        '<nav class="dash-sidebar-nav" aria-label="Sections">'
        '<a class="dash-view-btn active" aria-current="page" href="/notifications">'
        f'{icon}<span>Notifications</span></a>'
        "</nav>"
    )


def _item_html(note: notifications_store.Notification) -> str:
    target = f"/notifications/{note.id}/open"
    unread_cls = " is-unread" if note.is_unread else ""
    dot_cls = "" if note.is_unread else " is-read"
    chips: list[str] = []
    if note.client_slug:
        chips.append(f'<span class="nt-chip">{_esc(note.client_slug)}</span>')
    if note.page_label:
        chips.append(f"<span>{_esc(note.page_label)}</span>")
    when = _relative_time(note.created_at)
    if when:
        chips.append(f"<span>{_esc(when)}</span>")
    quote = (
        f'<p class="nt-quote">{_esc(note.body)}</p>' if note.body else ""
    )
    return f"""
        <li class="nt-item{unread_cls}">
          <a class="nt-link" href="{_esc(target)}">
            <span class="nt-dot{dot_cls}" aria-hidden="true"></span>
            <span class="nt-body">
              <span class="nt-title">{_esc(note.title)}</span>
              {quote}
              <span class="nt-meta">{"".join(chips)}</span>
            </span>
          </a>
        </li>"""


def render_notifications_page(
    *,
    email: str,
    is_admin: bool,
    items: list[notifications_store.Notification],
    unread: int,
) -> str:
    if items:
        rows = "".join(_item_html(n) for n in items)
        body = f'<ul class="nt-list">{rows}</ul>'
    else:
        body = (
            '<div class="nt-empty"><strong>Nothing yet</strong>'
            "You'll hear here when someone comments on a page for one of the "
            "accounts you're on the team for. Leave a comment yourself from the "
            "pencil button in the corner of any dashboard.</div>"
        )

    read_all = (
        '<form method="post" action="/notifications/read-all">'
        '<button type="submit" class="nt-readall">Mark all read</button></form>'
        if unread
        else ""
    )
    sub = (
        f"{unread} unread"
        if unread
        else "Comments left on the accounts you're on the team for."
    )
    content = f"""
  <main class="nt-main">
    <div class="nt-head">
      <div>
        <h1>Notifications</h1>
        <p>{_esc(sub)}</p>
      </div>
      {read_all}
    </div>
    {body}
  </main>"""
    return render_admin_shell_page(
        active_nav="notifications",
        page_title="Notifications",
        content_html=content,
        session_email=email,
        session_is_admin=is_admin,
        extra_css=_CSS,
        nav_html=_nav_html(is_admin=is_admin),
    )
