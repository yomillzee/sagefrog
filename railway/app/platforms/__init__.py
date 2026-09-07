"""Platform API routes, split out of main.py.

These are the public, API-key-authenticated endpoints — the surface the Custom
GPT action and any other external caller talk to. They lived in main.py
alongside the admin pages and the OAuth flow, which meant one file held both the
portal's HTML and its public API.

One module per platform, because that is how they change: a Google Ads schema
bump has nothing to do with LinkedIn's, and the person editing one should not
have to scroll past the other.

Unlike the admin routers these stay in the OpenAPI schema — they are documented
endpoints, and the generated spec is what the Custom GPT is configured against.
"""

from __future__ import annotations

from fastapi import FastAPI

from platforms.ga4_routes import router as ga4_router
from platforms.google_ads_routes import router as google_ads_router
from platforms.indeed_routes import router as indeed_router
from platforms.linkedin_routes import router as linkedin_router
from platforms.meta_routes import router as meta_router
from platforms.warehouse_routes import router as warehouse_router


def register_platform_routes(app: FastAPI) -> None:
    """Attach the platform API routes.

    Order matters only for the OpenAPI spec's path ordering, which the Custom
    GPT does not depend on; it follows main.py's original definition order so
    the generated document stays comparable.
    """
    app.include_router(google_ads_router)
    app.include_router(linkedin_router)
    app.include_router(meta_router)
    app.include_router(ga4_router)
    app.include_router(indeed_router)
    app.include_router(warehouse_router)
