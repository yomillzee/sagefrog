"""The built-in demo client.

A single, always-present client whose dashboard is populated entirely with
**synthetic sample data** — no GCP project, no BigQuery, no connectors, no
OAuth, and no real client data. It exists so the agency can pitch prospects,
walk existing clients through a working portal before they approve a live
sync, and train internal staff, all without standing up any infrastructure.

Why it stays aligned with future site changes
----------------------------------------------
The demo is a normal ``bigquery_nixon`` client: it renders through the one
master dashboard template and reaches every panel through the same
``/api/clients/{slug}/*`` routes as a real client. The *only* thing that
differs is where the data comes from — the shared ``_cached_bq_read`` helper
in ``dashboard/routes/api_routes.py`` short-circuits to ``demo_data`` for a
demo slug instead of querying BigQuery. So any future UI change, new panel,
or nav item applies to the demo automatically; only genuinely new *data
shapes* ever need a matching sample added in ``demo_data`` (and unknown keys
degrade gracefully to an empty-but-valid response, never an error).

This module owns the demo's identity and the idempotent seeding that
registers it (dashboard row + config, plus an optional read-only login).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# The demo client's slug. Kept as a constant (not a per-tenant DB flag) because
# the demo is a fixed, agency-owned entity, not something users create — a code
# constant is the lightest single source of truth. Override with DEMO_CLIENT_SLUG
# only if a real client ever legitimately needs the bare "demo" slug.
DEMO_SLUG = (os.getenv("DEMO_CLIENT_SLUG") or "demo").strip().lower()

# Display name shown in the dashboards picker and topbar. "Northwind Health" is a
# long-standing fictional placeholder brand; the "(Demo)" suffix makes it obvious
# in the picker that this is sample data, not a live account.
DEMO_LABEL = (os.getenv("DEMO_CLIENT_LABEL") or "Northwind Health (Demo)").strip()

# A believable-but-fictional monthly paid-media goal, so the Budget tracking /
# pacing card has a target to pace against out of the box.
DEMO_MONTHLY_BUDGET_USD = 42_000.0


def is_demo(client_slug: str | None) -> bool:
    """True when ``client_slug`` is the demo client (case-insensitive)."""
    if not client_slug:
        return False
    return client_slug.strip().lower() == DEMO_SLUG


def enabled() -> bool:
    """Whether the demo client should be seeded on startup.

    On by default (the whole point is that it's always available for pitches
    and training); set ``DEMO_CLIENT_ENABLED=0`` to keep it out of a
    deployment entirely.
    """
    raw = (os.getenv("DEMO_CLIENT_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def seed_demo_client() -> bool:
    """Idempotently register the demo dashboard + config (and optional login).

    Safe to call on every boot: it creates the dashboard registry row and its
    ``bigquery_nixon`` config only when missing, and re-applies the label,
    monthly budget, and (no) segmentation each time so the demo's presentation
    stays consistent. Returns True when the demo is present/seeded, False when
    disabled or the database isn't available. Never raises — a demo-seed
    failure must not take down app startup.
    """
    if not enabled():
        return False

    try:
        import dashboard_registry
        import client_dashboard_config as cdc
    except Exception:
        logger.warning("demo client: config modules unavailable; skipping seed", exc_info=True)
        return False

    if not dashboard_registry.enabled():
        # No DATABASE_URL — nothing to seed against (dev without Postgres).
        return False

    try:
        if not dashboard_registry.has_slug(DEMO_SLUG):
            dashboard_registry.create_client(
                client_slug=DEMO_SLUG, label=DEMO_LABEL, created_by="demo-seed",
            )
            logger.info("demo client: registered dashboard '%s' (%s)", DEMO_SLUG, DEMO_LABEL)
    except ValueError:
        # Already exists (raced with another worker) — fine, it's idempotent.
        pass
    except Exception:
        logger.warning("demo client: dashboard registration failed", exc_info=True)
        return False

    # Ensure the config row is in bigquery_nixon mode with the demo's label,
    # budget goal, and no segment filtering. create_client already writes a
    # bigquery_nixon row; these calls keep the demo's presentation correct even
    # if the row pre-existed in some other state.
    try:
        cdc.save_config(
            DEMO_SLUG,
            label=DEMO_LABEL,
            google_customer_id=None,
            linkedin_account_id=None,
            meta_account_id=None,
            ga4_client_key=DEMO_SLUG,
            updated_by="demo-seed",
            dashboard_mode="bigquery_nixon",
        )
        cdc.save_monthly_budget(
            DEMO_SLUG, monthly_budget_usd=DEMO_MONTHLY_BUDGET_USD, updated_by="demo-seed",
        )
        # No business-line / region segmentation for the demo (keeps the filter
        # UI simple, like most new clients).
        cdc.backfill_segment_filter_profile(DEMO_SLUG, "")
    except Exception:
        logger.warning("demo client: config save failed", exc_info=True)

    _seed_demo_login()
    return True


def _seed_demo_login() -> None:
    """Create a read-only client login for the demo, only when opted in.

    Credentials are never hardcoded: a login is created only when
    ``DEMO_CLIENT_LOGIN_EMAIL`` and ``DEMO_CLIENT_LOGIN_PASSWORD`` (>= 10 chars)
    are both set. Without them the demo is still fully viewable by any admin via
    the dashboards picker — this only mints a dedicated, shareable client login
    when the agency explicitly wants one to hand to prospects or trainees.
    """
    email = (os.getenv("DEMO_CLIENT_LOGIN_EMAIL") or "").strip()
    password = os.getenv("DEMO_CLIENT_LOGIN_PASSWORD") or ""
    if not email or not password:
        return
    try:
        import web_users
    except Exception:
        return
    if not web_users.enabled():
        return
    try:
        existing = web_users.get_user_by_email(email)
        if existing is not None:
            return
        web_users.create_user(
            email=email, password=password, role="client", client_slug=DEMO_SLUG,
        )
        logger.info("demo client: created read-only login for '%s'", email)
    except ValueError as exc:
        # e.g. password too short — surface as a warning, don't crash startup.
        logger.warning("demo client: login not created: %s", exc)
    except Exception:
        logger.warning("demo client: login creation failed", exc_info=True)
