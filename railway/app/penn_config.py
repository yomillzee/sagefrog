"""Dashboard client config dataclass.

Named PennDashboardConfig for historical reasons (it originated with the Penn
dashboard) but it is the generic per-client config shape used by every client.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PennDashboardConfig:
    client_key: str
    label: str
    google_customer_id: str | None
    linkedin_account_id: str | None
    meta_account_id: str | None
    ga4_client_key: str
    monthly_budget_usd: float | None = None
    platform_sources: dict[str, str] | None = None
