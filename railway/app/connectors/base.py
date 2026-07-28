"""Abstract connector handler interface and global registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class SyncResult:
    rows_loaded: int
    error: str | None = None
    range_start: date | None = None
    range_end: date | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ConnectorHandler(ABC):
    connector_type: str      # e.g. "linkedin_ads"
    display_name: str        # e.g. "LinkedIn Ads"
    oauth_platform: str      # e.g. "linkedin" — maps to oauth_store platform key
    default_raw_dataset: str # e.g. "raw_linkedin_ads"
    default_mart_dataset: str = "marketing_marts"
    # Set to True for connectors that don't need OAuth (e.g. Circle manual import)
    no_oauth: bool = False
    # Set to True for connectors authorized once at the agency level (a single
    # global OAuth token shared across all clients), with the server-side service
    # account as fallback — e.g. GSC. The wizard surfaces the agency connection
    # instead of a per-client authorize step.
    agency_oauth: bool = False
    # Set to True for connectors with no "list accounts" API — the wizard's step 2
    # renders a plain text field (saved as source_account_id) instead of fetching
    # and rendering a picker. e.g. SEMrush, where the "account" is just a domain
    # the user types in.
    manual_account_entry: bool = False
    # Label for the manual entry field when manual_account_entry is True.
    manual_account_label: str = "Account ID"
    # Minimum days between automated (cron) syncs. 0 = run every daily cron tick
    # (the default for fast, cheap sources). Set higher for slow/expensive
    # sources whose data barely moves day to day — e.g. PageSpeed (live Lighthouse
    # audits) runs ~monthly. First run and manual/onboarding syncs ignore this.
    min_sync_interval_days: int = 0

    @abstractmethod
    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        """Return [{"id": ..., "name": ..., "status": ...}, ...] for the authenticated user."""
        ...

    @abstractmethod
    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        """Trigger a source-specific sync run for this platform."""
        ...

    def test_connection(self, *, client_slug: str) -> str:
        """Verify the connection is live; return a short label (e.g. account name).

        Raises on failure so the wizard's Test connection step can surface the
        error. The default lists the authorised accounts and uses the first
        name. Connectors whose account-listing API fans out over many objects
        (e.g. GTM, which lists every account and its containers and trips
        Google's tight per-minute quota) should override this with a cheaper
        single-target check against the already-configured account.
        """
        accounts = self.list_accounts(client_slug=client_slug)
        return accounts[0]["name"] if accounts else ""


# Global registry
_REGISTRY: dict[str, ConnectorHandler] = {}


def register(handler: ConnectorHandler) -> None:
    _REGISTRY[handler.connector_type] = handler


def get(connector_type: str) -> ConnectorHandler | None:
    return _REGISTRY.get(connector_type)


def all_handlers() -> dict[str, ConnectorHandler]:
    return dict(_REGISTRY)


# Ordered list for the directory page
CONNECTOR_ORDER = [
    "linkedin_ads",
    "linkedin_organic",
    "meta_ads",
    "google_ads",
    "microsoft_ads",
    "ga4",
    "gsc",
    "gtm",
    "hubspot",
    "semrush",
    "pagespeed",
]
