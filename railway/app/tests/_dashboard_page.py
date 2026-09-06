"""Render the dashboard as one document, for tests that read its script.

The page's JavaScript now ships as a cached file behind ``/assets`` instead of
an inline ``<script>`` block (see :mod:`dashboard.assets`). Tests in this suite
assert against the markup *and* against the script that drives it — "this panel
exists" and "this loader is wired to it" are the same behaviour — so this
wrapper puts the script back into the document: the same bytes, at the same
point in the page, which is exactly what the browser ends up running.

Import ``render_bigquery_dashboard_page`` from here instead of from the renderer
and every existing assertion keeps working. Tests that care where the script
actually lives (``tests/test_dashboard_assets.py``) import the renderer directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.assets import dashboard_js, dashboard_js_url  # noqa: E402
from dashboard.renderers.bigquery_dashboard_renderer import (  # noqa: E402
    render_bigquery_dashboard_page as _render_page,
)

__all__ = ["render_bigquery_dashboard_page"]


def render_bigquery_dashboard_page(*args, **kwargs) -> str:
    """The rendered dashboard with its external script inlined."""
    html = _render_page(*args, **kwargs)
    tag = f'<script src="{dashboard_js_url()}"></script>'
    if tag not in html:  # page variant without the script — nothing to inline
        return html
    return html.replace(tag, "<script>\n" + dashboard_js()[1] + "\n  </script>")
