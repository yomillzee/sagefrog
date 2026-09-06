"""Composed, content-hashed stylesheets for the shared dashboard template.

The dashboard's CSS used to live inside ``render_bigquery_dashboard_page``'s
f-string as a ~960-line ``<style>`` block. That cost us three things:

* **It could not be cached.** Interpolated into the HTML, ~140 KB of identical
  CSS was re-sent on every navigation, for every client.
* **It could not be tooled.** No editor highlighting, no stylesheet linting, and
  every literal ``{``/``}`` had to be doubled because it sat in an f-string — a
  mistake that only surfaces at render time.
* **It pinned the CSP open.** ``web_security.py`` cannot set ``style-src``/
  ``script-src`` while the page depends on inline blocks.

So the static parts now live in real ``.css`` files under ``static/css/`` and are
stitched back together here, in the exact cascade order the inline block used:

    1. dashboard-base.css        :root tokens, body, app shell
    2. base_layout.SIDEBAR_CSS               (shared with every shell page)
    3. dashboard-layout.css      date bar, filters, cards, tables
    4. budget_tracker.css()                  (shared with Settings)
    5. dashboard-charts.css      bar lists, keyword performance, layout cols
    6. pagespeed_renderer.pane_css()         (Site Performance tab)
    7. google_business_renderer.pane_css()   (Google Business tab)
    8. dashboard-panels.css      legend, funnel, explorer tree, skeletons

Steps 2, 4, 6 and 7 stay in Python because other renderers import the same
strings; keeping them as the single source of truth is why this module composes
at runtime instead of shipping one pre-built file. **The order above is the
order the browser saw before this change — preserve it.** The four fragments are
namespaced (``.dash-sidebar*``, ``.budget-*``, ``.ps-*``, ``.gb-*``) so moving
them would probably be harmless, but "probably" is not a good enough reason to
change what the cascade resolves to.

One deliberate difference: the budget CSS used to be omitted for clients without
budget tracking. It is always included now so that every client shares one
cacheable URL. Unmatched CSS is inert, and the trade is ~7 KB once against a
per-client cache miss on every page load.

The URL carries a digest of the composed bytes (``/assets/dashboard-<digest>.css``)
so it can be served ``immutable`` with a one-year max-age: any edit to a ``.css``
file *or* to one of the Python fragments changes the digest, and browsers fetch
the new URL on the next deploy without anyone having to remember to bump it.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent / "static" / "css"
JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"

# Interleave plan, in cascade order. Each entry is one of:
#   ("file", stem)  — read static/css/<stem>.css
#   ("frag", i)     — splice in _fragments()[i]
#   ("raw", text)   — literal text that sat between two fragments in the old
#                     inline block; kept so the composed bytes match exactly.
_CASCADE: tuple[tuple[str, object], ...] = (
    ("file", "dashboard-base"),
    ("frag", 0),  # SIDEBAR_CSS
    ("file", "dashboard-layout"),
    ("frag", 1),  # budget_tracker.css()
    ("file", "dashboard-charts"),
    ("frag", 2),  # pagespeed_renderer.pane_css()
    ("raw", "\n    "),
    ("frag", 3),  # google_business_renderer.pane_css()
    ("file", "dashboard-panels"),
)


def _fragments() -> list[str]:
    """The Python-owned CSS, in cascade order.

    Imported lazily: this module is pulled in by the renderers, and importing
    them at module scope would close an import cycle.
    """
    from dashboard.renderers import (
        budget_tracker,
        google_business_renderer,
        pagespeed_renderer,
    )
    from dashboard.renderers.base_layout import SIDEBAR_CSS

    return [
        SIDEBAR_CSS,
        budget_tracker.css(),
        pagespeed_renderer.pane_css(),
        google_business_renderer.pane_css(),
    ]


def _compose() -> str:
    frags = _fragments()
    parts: list[str] = []
    for kind, value in _CASCADE:
        if kind == "file":
            parts.append((CSS_DIR / f"{value}.css").read_text(encoding="utf-8"))
        elif kind == "frag":
            parts.append(frags[value])
        else:
            parts.append(str(value))
    return "".join(parts)


@lru_cache(maxsize=1)
def dashboard_css() -> tuple[str, str]:
    """``(digest, body)`` for the composed dashboard stylesheet.

    Cached for the life of the process — the inputs are files on disk and
    module-level constants, so the result cannot change without a redeploy.
    """
    body = _compose()
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return digest, body


def dashboard_css_url() -> str:
    """Cache-busting URL for the composed stylesheet."""
    return f"/assets/dashboard-{dashboard_css()[0]}.css"


# ── JavaScript ──────────────────────────────────────────────────────────────
# Same story as the CSS, and the bigger half of it: ~5,400 lines of JS lived in
# the renderer's f-string, 66% of every page. It is now two files plus the two
# panes other renderers own, in the order the single <script> had them:
#
#     1. dashboard-shell.js    panel edit mode, formatters, charts, tabs
#     2. pagespeed_renderer.pane_js()          (Site Performance)
#     3. google_business_renderer.pane_js()    (Google Business)
#     4. dashboard-panels.js   explorer, demographics, GA4 pages, AI traffic
#
# The values that genuinely vary per request — API URLs, feature flags, the
# selected date range — stay in a small inline <script> the page emits *before*
# this file, so the composed body is identical for every client and cacheable.
# Order matters here in a way it did not for CSS: the panes read constants from
# that preamble at load time, and everything shares one global scope.

_JS_CASCADE: tuple[tuple[str, object], ...] = (
    ("file", "dashboard-shell"),
    ("frag", 0),  # pagespeed_renderer.pane_js()
    ("raw", "\n    "),
    ("frag", 1),  # google_business_renderer.pane_js()
    ("file", "dashboard-panels"),
)


def _js_fragments() -> list[str]:
    """The pane JS owned by other renderer modules, in cascade order."""
    from dashboard.renderers import google_business_renderer, pagespeed_renderer

    return [pagespeed_renderer.pane_js(), google_business_renderer.pane_js()]


def _compose_js() -> str:
    frags = _js_fragments()
    parts: list[str] = []
    for kind, value in _JS_CASCADE:
        if kind == "file":
            parts.append((JS_DIR / f"{value}.js").read_text(encoding="utf-8"))
        elif kind == "frag":
            parts.append(frags[value])
        else:
            parts.append(str(value))
    return "".join(parts)


@lru_cache(maxsize=1)
def dashboard_js() -> tuple[str, str]:
    """``(digest, body)`` for the composed dashboard script."""
    body = _compose_js()
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return digest, body


def dashboard_js_url() -> str:
    """Cache-busting URL for the composed script."""
    return f"/assets/dashboard-{dashboard_js()[0]}.js"
