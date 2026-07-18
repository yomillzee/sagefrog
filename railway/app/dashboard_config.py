"""Dashboard client config dataclass — the per-client config shape (account IDs,
GA4 key, budget) that config loading returns for every client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardConfig:
    client_key: str
    label: str
    google_customer_id: str | None
    linkedin_account_id: str | None
    meta_account_id: str | None
    ga4_client_key: str
    monthly_budget_usd: float | None = None
    platform_sources: dict[str, str] | None = None
