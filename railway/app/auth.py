from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GoogleAdsEnv:
    developer_token: str
    login_customer_id: str | None
    client_id: str
    client_secret: str
    refresh_token: str


def _get_required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def load_google_ads_env() -> GoogleAdsEnv:
    return GoogleAdsEnv(
        developer_token=_get_required("GOOGLE_ADS_DEVELOPER_TOKEN"),
        login_customer_id=os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or None,
        client_id=_get_required("GOOGLE_ADS_CLIENT_ID"),
        client_secret=_get_required("GOOGLE_ADS_CLIENT_SECRET"),
        refresh_token=_get_required("GOOGLE_ADS_REFRESH_TOKEN"),
    )


def env_summary() -> dict:
    keys = [
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
    ]
    return {f"has_{k.lower().replace('google_ads_', '')}": bool(os.getenv(k)) for k in keys}
