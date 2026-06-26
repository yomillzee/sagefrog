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

    @abstractmethod
    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        """Return [{"id": ..., "name": ..., "status": ...}, ...] for the authenticated user."""
        ...

    @abstractmethod
    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        """Trigger a source-specific sync run for this platform."""
        ...


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
    "meta_ads",
    "google_ads",
    "ga4",
    "gsc",
    "gtm",
    "hubspot",
]
