"""Composed dashboard stylesheets, served with far-future caching.

Separate from the ``/static`` mount because the body is stitched together at
runtime from ``.css`` files *and* Python constants shared with other renderers
(see :mod:`dashboard.assets`), so there is no single file on disk to hand to
``StaticFiles``.

The URL embeds a digest of the composed bytes, which is what makes
``immutable`` safe to promise: a change to any input yields a different URL, so
a cached copy is never wrong for the URL it was cached under.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from dashboard.assets import dashboard_css, dashboard_js

router = APIRouter(include_in_schema=False)

# A year, the maximum any cache should honour. Paired with `immutable` so
# browsers skip even the revalidating conditional request.
_CACHE_CONTROL = "public, max-age=31536000, immutable"


@router.get("/assets/dashboard-{digest}.css")
def dashboard_stylesheet(digest: str) -> Response:
    """Serve the composed dashboard CSS.

    The ``digest`` in the path is deliberately *not* validated against the
    current build. A process only ever holds one version of the stylesheet, and
    a request carrying an older digest comes from a page served before the last
    deploy — answering it with the current CSS is both correct for that page and
    kinder than a 404 during the window where both versions are in flight.
    """
    return _asset_response(*dashboard_css(), media_type="text/css; charset=utf-8")


@router.get("/assets/dashboard-{digest}.js")
def dashboard_script(digest: str) -> Response:
    """Serve the composed dashboard JS. Same stale-digest rule as the CSS."""
    return _asset_response(*dashboard_js(), media_type="text/javascript; charset=utf-8")


def _asset_response(current: str, body: str, *, media_type: str) -> Response:
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Cache-Control": _CACHE_CONTROL,
            # Lets a client with a stale URL notice it drifted, without
            # changing what it renders.
            "ETag": f'"{current}"',
        },
    )
