"""Seed sample data for the STAGING environment.

Staging shares the production code path but starts with an empty database, so
there is nobody to log in as and no warehouse metrics for the dashboards to
draw. This script fills those gaps with deterministic, obviously-fake sample
data so the app is usable end-to-end on staging without touching real client
data.

What it seeds (all idempotent — safe to run repeatedly):
  * Login users: an admin, a client user, and a standard user, with known
    credentials (overridable via env, see below).
  * A client group bundling the built-in demo client(s).
  * ~90 days of daily + per-campaign metrics for the built-in Nixon client,
    across the linkedin / google / meta sources.

Safety: this refuses to run unless SEED_STAGING is truthy, and additionally
aborts if it looks like a production environment (RAILWAY_ENVIRONMENT_NAME)
unless SEED_FORCE=1 is set. It never deletes anything.

Run it from railway/app (so the sibling modules import cleanly):

    SEED_STAGING=1 python seed_staging.py

Credentials can be overridden with:
    STAGING_ADMIN_EMAIL / STAGING_ADMIN_PASSWORD
    STAGING_CLIENT_EMAIL / STAGING_CLIENT_PASSWORD
    STAGING_STANDARD_EMAIL / STAGING_STANDARD_PASSWORD
"""

from __future__ import annotations

import math
import os
import random
import sys
from datetime import UTC, date, datetime, timedelta

import dashboard_registry
import warehouse
import web_users

# Built-in demo client the app already registers on boot (see client_config).
# Its warehouse account id is the one the Nixon dashboard queries against.
DEMO_CLIENT_SLUG = "nixon"
DEMO_ACCOUNT_ID = "503285948"

# Sources to seed metrics for. Dashboards query metrics_daily by
# (source, account_id), so seeding all three lets every source view render.
SOURCES = ("linkedin", "google", "meta")

# Per-source baseline daily figures. Real numbers vary; these are plausible
# staging placeholders with a gentle trend + weekly seasonality applied below.
SOURCE_BASELINES = {
    "linkedin": {"spend": 180.0, "cpc": 6.5, "ctr": 0.006, "cvr": 0.04, "aov": 900.0},
    "google": {"spend": 320.0, "cpc": 3.2, "ctr": 0.045, "cvr": 0.05, "aov": 750.0},
    "meta": {"spend": 140.0, "cpc": 1.1, "ctr": 0.012, "cvr": 0.03, "aov": 600.0},
}

CAMPAIGNS = {
    "linkedin": [
        ("li-brand-awareness", "Brand Awareness — Sponsored Content"),
        ("li-lead-gen", "Lead Gen Forms — Decision Makers"),
        ("li-retargeting", "Website Retargeting"),
    ],
    "google": [
        ("g-search-brand", "Search — Brand"),
        ("g-search-nonbrand", "Search — Non-Brand"),
        ("g-pmax", "Performance Max"),
    ],
    "meta": [
        ("m-prospecting", "Prospecting — Broad"),
        ("m-retargeting", "Retargeting — 30d"),
    ],
}

SEED_DAYS = int((os.getenv("STAGING_SEED_DAYS") or "90").strip() or "90")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _guard() -> None:
    """Refuse to run outside an explicitly opted-in, non-production context."""
    if not _truthy(os.getenv("SEED_STAGING")):
        sys.exit(
            "Refusing to seed: set SEED_STAGING=1 to confirm you are seeding a "
            "staging database."
        )
    if not (os.getenv("DATABASE_URL") or "").strip():
        sys.exit("Refusing to seed: DATABASE_URL is not set.")
    env_name = (os.getenv("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
    if env_name in ("production", "prod") and not _truthy(os.getenv("SEED_FORCE")):
        sys.exit(
            f"Refusing to seed: RAILWAY_ENVIRONMENT_NAME={env_name!r} looks like "
            "production. Set SEED_FORCE=1 only if you really mean it."
        )


def _seed_users() -> None:
    specs = [
        (
            "admin",
            os.getenv("STAGING_ADMIN_EMAIL", "admin@staging.sagefrog.test"),
            os.getenv("STAGING_ADMIN_PASSWORD", "staging-admin-1234"),
            {"role": "admin"},
        ),
        (
            "client",
            os.getenv("STAGING_CLIENT_EMAIL", "client@staging.sagefrog.test"),
            os.getenv("STAGING_CLIENT_PASSWORD", "staging-client-1234"),
            {"role": "client", "client_slug": DEMO_CLIENT_SLUG},
        ),
        (
            "standard",
            os.getenv("STAGING_STANDARD_EMAIL", "standard@staging.sagefrog.test"),
            os.getenv("STAGING_STANDARD_PASSWORD", "staging-standard-1234"),
            {"role": "standard", "allowed_client_slugs": [DEMO_CLIENT_SLUG]},
        ),
    ]
    print("\nUsers:")
    for _label, email, password, kwargs in specs:
        existing = web_users.get_user_by_email(email)
        if existing is not None:
            print(f"  = {email:<38} exists ({existing.role}) — skipped")
            continue
        user = web_users.create_user(email=email, password=password, **kwargs)
        print(f"  + {email:<38} created ({user.role})  password: {password}")


def _seed_group() -> None:
    name = "Staging Demo Group"
    print("\nClient group:")
    existing = next(
        (g for g in web_users.list_groups() if g.get("name") == name), None
    )
    if existing is not None:
        print(f"  = {name!r} exists — skipped")
        return
    group = web_users.create_group(
        name=name,
        client_slugs=[DEMO_CLIENT_SLUG],
        description="Sample group for staging demos.",
    )
    print(f"  + {name!r} created (id={group.get('id')}) → [{DEMO_CLIENT_SLUG}]")


def _daily_rows(source: str, days: int) -> list[dict]:
    """Deterministic daily metrics with a mild upward trend + weekly rhythm."""
    base = SOURCE_BASELINES[source]
    rng = random.Random(f"{source}:{DEMO_ACCOUNT_ID}")
    today = date.today()
    rows: list[dict] = []
    for i in range(days):
        d = today - timedelta(days=(days - 1 - i))
        trend = 1.0 + (i / max(days, 1)) * 0.25  # ~+25% over the window
        weekly = 1.0 + 0.15 * math.sin((d.weekday() / 7.0) * 2 * math.pi)
        jitter = rng.uniform(0.85, 1.15)
        factor = trend * weekly * jitter
        spend = round(base["spend"] * factor, 2)
        clicks = max(0, int(spend / base["cpc"]))
        impressions = max(clicks, int(clicks / base["ctr"])) if base["ctr"] else clicks
        conversions = round(clicks * base["cvr"], 2)
        conversion_value = round(conversions * base["aov"], 2)
        rows.append(
            {
                "metric_date": d.isoformat(),
                "spend": spend,
                "clicks": clicks,
                "impressions": impressions,
                "conversions": conversions,
                "conversion_value": conversion_value,
            }
        )
    return rows


def _campaign_rows(source: str, days: int) -> list[dict]:
    """Split each day's totals across the source's campaigns with fixed weights."""
    campaigns = CAMPAIGNS.get(source, [])
    if not campaigns:
        return []
    rng = random.Random(f"campaigns:{source}:{DEMO_ACCOUNT_ID}")
    weights = [rng.uniform(0.5, 1.5) for _ in campaigns]
    total_w = sum(weights) or 1.0
    rows: list[dict] = []
    for day in _daily_rows(source, days):
        for (camp_id, camp_name), w in zip(campaigns, weights):
            share = w / total_w
            rows.append(
                {
                    "campaign_id": camp_id,
                    "campaign_name": camp_name,
                    "metric_date": day["metric_date"],
                    "spend": round(day["spend"] * share, 2),
                    "clicks": int(day["clicks"] * share),
                    "impressions": int(day["impressions"] * share),
                    "conversions": round(day["conversions"] * share, 2),
                    "conversion_value": round(day["conversion_value"] * share, 2),
                }
            )
    return rows


def _seed_metrics() -> None:
    print(f"\nWarehouse metrics ({SEED_DAYS} days, account {DEMO_ACCOUNT_ID}):")
    for source in SOURCES:
        daily = warehouse.upsert_metrics_daily_batch(
            source, DEMO_ACCOUNT_ID, _daily_rows(source, SEED_DAYS)
        )
        camp = warehouse.upsert_campaign_daily_batch(
            source, DEMO_ACCOUNT_ID, _campaign_rows(source, SEED_DAYS)
        )
        print(f"  {source:<9} {daily:>4} daily rows, {camp:>4} campaign rows")


def main() -> None:
    _guard()
    print(f"Seeding staging sample data @ {datetime.now(tz=UTC).isoformat()}")

    # Make sure the tables + built-in client registry exist before we write.
    web_users.ensure_schema()
    dashboard_registry.ensure_schema()
    warehouse.ensure_schema()

    slugs = set(dashboard_registry.list_slugs())
    if DEMO_CLIENT_SLUG not in slugs:
        print(
            f"  note: demo client {DEMO_CLIENT_SLUG!r} not in registry "
            f"({sorted(slugs)}); seeding metrics anyway."
        )

    _seed_users()
    _seed_group()
    _seed_metrics()
    print("\nDone. Sample data is ready on staging.")


if __name__ == "__main__":
    main()
