"""Admin routes, split out of main.py.

main.py had every non-dashboard route in it — 102 of them across 3,700 lines —
so a change to the client-hours page and a change to the OAuth callback landed
in the same file and read as one blob. The dashboard package already showed the
shape that works (`dashboard/routes/`, attached by one register call); this is
the same arrangement for /admin.

Moved so far: client hours, dashboard lifecycle, and the agency-wide reporting
pages. Still in main.py: users, groups, feature requests, view-as and the
advanced settings page — those share render helpers with each other that need a
home here first.
"""

from __future__ import annotations

from fastapi import FastAPI

from admin.client_hours_routes import router as client_hours_router
from admin.dashboards_routes import router as dashboards_router
from admin.reporting_routes import router as reporting_router


def register_admin_routes(app: FastAPI) -> None:
    """Attach the /admin/* routes that live in this package."""
    app.include_router(client_hours_router)
    app.include_router(dashboards_router)
    app.include_router(reporting_router)
