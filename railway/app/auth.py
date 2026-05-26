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
    return {
        "has_developer_token": bool(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")),
        "has_login_customer_id": bool(os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID")),
        "has_client_id": bool(os.getenv("GOOGLE_ADS_CLIENT_ID")),
        "has_client_secret": bool(os.getenv("GOOGLE_ADS_CLIENT_SECRET")),
        "has_refresh_token": bool(os.getenv("GOOGLE_ADS_REFRESH_TOKEN")),
    }
