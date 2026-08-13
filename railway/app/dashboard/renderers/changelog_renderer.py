"""Admin "What's New" page: the portal's release notes, newest first.

Rendered inside the shared admin shell (navy sidebar + client switcher) so it
sits alongside Docs and the rollups. Static content — the entries come from
``changelog.ENTRIES``, so shipping a note is a one-item edit there and no change
to this file.

The layout is a dated timeline rather than a table: the question someone brings
to this page is "what changed since I last looked", which is a scan down dates,
not a lookup. Entries that shipped the same day group under one date marker so a
busy release reads as one event.
"""

from __future__ import annotations

import html
from datetime import date

import changelog
from dashboard.renderers.base_layout import render_admin_shell_page


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _pretty_date(iso: str) -> str:
    """"August 13, 2026" — falls back to the raw string if it isn't a real date.

    Built by hand rather than with ``%-d``, which is not portable off glibc.
    """
    try:
        when = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{when:%B} {when.day}, {when.year}"


_LOG_CSS = """
    :root {
      --navy:#0a2540; --ink:#16233a; --muted:#5b6678; --line:#e6ebf3; --border:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --soft:#f5f8fe;
      --green:#0a7f3f; --amber:#b7791f;
    }
    body { color:var(--ink);
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; }
    main { max-width:820px; margin:0 auto; padding:26px 24px 60px; }
    section { background:#fff; border:1px solid var(--line); border-radius:18px;
      padding:24px 28px; margin-bottom:16px; box-shadow:0 6px 22px rgba(10,37,64,.05); }
    a { color:var(--accent); }

    /* Hero */
    .hero { display:flex; gap:18px; align-items:flex-start; }
    .hero .badge-ic { flex-shrink:0; width:52px; height:52px; border-radius:15px; display:grid; place-items:center;
      background:linear-gradient(135deg,#2563eb,#1d4ed8); color:#fff; box-shadow:0 8px 20px rgba(37,99,235,.28); }
    .hero .badge-ic svg { width:26px; height:26px; }
    .hero h1 { margin:2px 0 6px; font-size:1.5rem; color:var(--navy); letter-spacing:-.01em; }
    .hero p { margin:0; color:var(--muted); font-size:.96rem; line-height:1.55; }

    /* Dated timeline. The rail is the date column; entries hang off it. */
    .day { display:grid; grid-template-columns:132px 1fr; gap:22px; align-items:start; }
    .day + .day { margin-top:8px; }
    .day-date { position:sticky; top:16px; padding-top:14px; text-align:right; }
    .day-date .d { font-weight:800; font-size:.86rem; color:var(--navy); letter-spacing:-.01em; }
    .day-date .rel { display:block; margin-top:2px; font-size:.74rem; color:var(--muted); font-weight:600; }
    .day-entries { border-left:2px solid var(--line); padding:0 0 4px 22px; position:relative; }
    .day-entries::before { content:""; position:absolute; left:-7px; top:20px; width:12px; height:12px;
      border-radius:50%; background:var(--accent); border:2px solid #fff; box-shadow:0 0 0 1px var(--border); }

    .entry { padding:14px 0 4px; }
    .entry + .entry { border-top:1px solid var(--line); margin-top:6px; }
    .entry-head { display:flex; align-items:center; gap:9px; flex-wrap:wrap; margin-bottom:6px; }
    .kind { display:inline-flex; align-items:center; font-size:.68rem; font-weight:800; letter-spacing:.05em;
      text-transform:uppercase; padding:3px 9px; border-radius:999px; }
    .kind.new { background:#e7f3ec; color:var(--green); }
    .kind.improved { background:#eef4fd; color:var(--accent-d); }
    .kind.fixed { background:rgba(183,121,31,.14); color:var(--amber); }
    .area { font-size:.74rem; font-weight:700; color:var(--muted); }
    .entry h3 { margin:0 0 6px; font-size:1.02rem; color:var(--navy); line-height:1.35; }
    .entry p { margin:0; color:var(--ink); font-size:.93rem; line-height:1.6; }
    .entry ul { margin:10px 0 0; padding-left:19px; }
    .entry li { margin-bottom:5px; color:var(--muted); font-size:.88rem; line-height:1.55; }
    .entry li:last-child { margin-bottom:0; }

    .empty { color:var(--muted); text-align:center; padding:26px 8px; margin:0; }
    .foot { color:var(--muted); font-size:.84rem; margin:0; }
    .foot code { background:#eef2fa; border:1px solid var(--line); border-radius:5px; padding:1px 6px;
      font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace; font-size:.85em; color:var(--navy); }

    @media (max-width: 720px) {
      main { padding:16px 13px 44px; }
      section { padding:18px 17px; border-radius:14px; }
      /* The date stops being a column and becomes a heading above its entries. */
      .day { grid-template-columns:1fr; gap:0; }
      .day-date { position:static; text-align:left; padding-top:16px; }
      .day-date .rel { display:inline; margin-left:7px; }
      .day-entries { border-left:0; padding-left:0; }
      .day-entries::before { display:none; }
    }"""

_HERO_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 3l2.3 4.9 5.2.7-3.8 3.7.9 5.3-4.6-2.5-4.6 2.5.9-5.3L4.5 8.6l5.2-.7z"/></svg>'
)


def _relative_day(iso: str, today: date) -> str:
    """"today" / "yesterday" / "3 weeks ago" — the scent that says how fresh this is."""
    try:
        when = date.fromisoformat(iso)
    except ValueError:
        return ""
    days = (today - when).days
    if days < 0:
        return "scheduled"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months > 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years > 1 else ''} ago"


def _entry_html(entry: changelog.Entry) -> str:
    details = (
        "<ul>" + "".join(f"<li>{_esc(line)}</li>" for line in entry.details) + "</ul>"
        if entry.details
        else ""
    )
    return f"""
        <article class="entry">
          <div class="entry-head">
            <span class="kind {_esc(changelog.kind_class(entry.kind))}">{_esc(changelog.kind_label(entry.kind))}</span>
            <span class="area">{_esc(entry.area)}</span>
          </div>
          <h3>{_esc(entry.title)}</h3>
          <p>{_esc(entry.summary)}</p>
          {details}
        </article>"""


def render_changelog_page(*, user_email: str, today: date | None = None) -> str:
    """Full HTML for GET /admin/changelog. Static content — no data fetch.

    ``today`` is injectable so the "3 weeks ago" line is testable.
    """
    now = today or date.today()

    # Group by day, preserving the newest-first order entries() already gives us.
    days: list[tuple[str, list[changelog.Entry]]] = []
    for entry in changelog.entries():
        if days and days[-1][0] == entry.date:
            days[-1][1].append(entry)
        else:
            days.append((entry.date, [entry]))

    if days:
        timeline = "".join(
            f"""
      <div class="day">
        <div class="day-date">
          <span class="d">{_esc(_pretty_date(day))}</span>
          <span class="rel">{_esc(_relative_day(day, now))}</span>
        </div>
        <div class="day-entries">{"".join(_entry_html(e) for e in group)}</div>
      </div>"""
            for day, group in days
        )
    else:
        timeline = '<p class="empty">Nothing shipped yet.</p>'

    content = f"""
  <main>
    <section>
      <div class="hero">
        <span class="badge-ic">{_HERO_ICON}</span>
        <div>
          <h1>What's new</h1>
          <p>Changes to how the portal looks and works, newest first — so you hear it
             here rather than from a client. Fixes and internal plumbing that nobody
             would notice are left out on purpose.</p>
        </div>
      </div>
    </section>
    <section>{timeline}</section>
    <section>
      <p class="foot">Shipping something people will notice? Add it to
        <code>railway/app/changelog.py</code> in the same change — one entry, written for
        the person using the page.</p>
    </section>
  </main>"""

    return render_admin_shell_page(
        active_nav="changelog",
        page_title="Admin · What's New",
        content_html=content,
        session_email=user_email,
        session_is_admin=True,
        extra_css=_LOG_CSS,
    )
