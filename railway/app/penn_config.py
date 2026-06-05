"""Penn Community Bank account IDs for dashboard sync."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


def _strip_env(val: str | None) -> str:
    if not val:
        return ""
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1].strip()
    return v


@dataclass(frozen=True)
class PennDashboardConfig:
    client_key: str
    label: str
    google_customer_id: str | None
    linkedin_account_id: str | None
    meta_account_id: str | None
    ga4_client_key: str


# Penn Community Bank — used when PENN_DASHBOARD / PENN_* env vars are unset.
_PENN_DEFAULTS = {
    "google_customer_id": "1549971930",
    "linkedin_account_id": "508590994",
    "meta_account_id": "2581574002135957",
    "ga4_client_key": "penn",
}


def _normalize_google_customer_id(val: str) -> str:
    return val.replace("-", "").strip()


def load_penn_config() -> PennDashboardConfig:
    """
    Load Penn dashboard targets from PENN_DASHBOARD JSON or individual env vars.

    Falls back to built-in Penn account IDs (not GOOGLE_CUSTOMER_ID).

    PENN_DASHBOARD example:
    {
      "label": "Penn Community Bank",
      "google_customer_id": "1549971930",
      "linkedin_account_id": "508590994",
      "meta_account_id": "2581574002135957",
      "ga4_client_key": "penn"
    }
    """
    raw = _strip_env(os.getenv("PENN_DASHBOARD"))
    data: dict = {}
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            data = parsed

    google = _normalize_google_customer_id(
        _strip_env(os.getenv("PENN_GOOGLE_CUSTOMER_ID"))
        or _strip_env(str(data.get("google_customer_id") or ""))
        or _PENN_DEFAULTS["google_customer_id"]
    )

    linkedin = (
        _strip_env(os.getenv("PENN_LINKEDIN_ACCOUNT_ID"))
        or _strip_env(str(data.get("linkedin_account_id") or ""))
        or _PENN_DEFAULTS["linkedin_account_id"]
    )

    meta = (
        _strip_env(os.getenv("PENN_META_ACCOUNT_ID"))
        or _strip_env(str(data.get("meta_account_id") or ""))
        or _PENN_DEFAULTS["meta_account_id"]
    )

    ga4_key = (
        _strip_env(str(data.get("ga4_client_key") or ""))
        or _strip_env(os.getenv("PENN_GA4_CLIENT_KEY"))
        or _PENN_DEFAULTS["ga4_client_key"]
    )

    label = _strip_env(str(data.get("label") or "")) or "Penn Community Bank"

    try:
        import client_dashboard_config as cdc

        row = cdc.get_config("penn")
        if row:
            if row.label:
                label = row.label
            if row.google_customer_id:
                google = _normalize_google_customer_id(row.google_customer_id)
            if row.linkedin_account_id:
                linkedin = row.linkedin_account_id
            if row.meta_account_id:
                meta = row.meta_account_id
            if row.ga4_client_key:
                ga4_key = row.ga4_client_key
    except Exception:
        pass

    if not any((google, linkedin, meta, ga4_key)):
        raise RuntimeError(
            "Penn dashboard is not configured. Set PENN_DASHBOARD JSON or "
            "PENN_GOOGLE_CUSTOMER_ID / PENN_LINKEDIN_ACCOUNT_ID / PENN_META_ACCOUNT_ID."
        )

    return PennDashboardConfig(
        client_key="penn",
        label=label,
        google_customer_id=google or None,
        linkedin_account_id=linkedin or None,
        meta_account_id=meta or None,
        ga4_client_key=ga4_key,
    )
