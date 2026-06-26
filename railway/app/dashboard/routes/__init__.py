"""Dashboard HTTP routes (Pass 4)."""

from __future__ import annotations

from fastapi import FastAPI

from dashboard.routes.api_routes import router as api_router
from dashboard.routes.connector_routes import router as connector_router
from dashboard.routes.core_routes import router as core_router
from dashboard.routes.files_routes import router as files_router
from dashboard.routes.settings_routes import router as settings_router


def register_dashboard_routes(app: FastAPI) -> None:
    """Attach all /dashboard/* and related internal sync routes to the app."""
    app.include_router(api_router)
    app.include_router(settings_router)
    app.include_router(connector_router)
    app.include_router(core_router)
    app.include_router(files_router)
