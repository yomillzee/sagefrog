from __future__ import annotations

import os
from dataclasses import dataclass

# Railway / local: supports GOOGLE_ADS_* (preferred) or GOOGLE_* (linkedin-ads-dashboard style)
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "developer_token": ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_DEVELOPER_TOKEN"),
    "login_customer_id": ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "GOOGLE_LOGIN_CUSTOMER_ID"),
    "client_id": ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_CLIENT_ID"),
    "client_secret": ("GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"),
    "refresh_token": ("GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_REFRESH_TOKEN"),
}


def _strip_env_value(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        val = val[1:-1].strip()
    return val


def _get_env(*keys: str) -> str | None:
    for key in keys:
        raw = os.getenv(key)
        if not raw:
            continue
        val = _strip_env_value(raw)
        if val:
            return val
    return None


def _get_required_env(*keys: str) -> str:
    val = _get_env(*keys)
    if not val:
        raise RuntimeError(f"Missing required environment variable (one of): {', '.join(keys)}")
    return val


@dataclass(frozen=True)
class GoogleAdsEnv:
    developer_token: str
    login_customer_id: str | None
    client_id: str
    client_secret: str
    refresh_token: str


def load_google_ads_env() -> GoogleAdsEnv:
    return GoogleAdsEnv(
        developer_token=_get_required_env(*_ENV_ALIASES["developer_token"]),
        login_customer_id=_get_env(*_ENV_ALIASES["login_customer_id"]),
        client_id=_get_required_env(*_ENV_ALIASES["client_id"]),
        client_secret=_get_required_env(*_ENV_ALIASES["client_secret"]),
        refresh_token=_get_required_env(*_ENV_ALIASES["refresh_token"]),
    )


def env_summary() -> dict:
    return {
        "has_developer_token": bool(_get_env(*_ENV_ALIASES["developer_token"])),
        "has_login_customer_id": bool(_get_env(*_ENV_ALIASES["login_customer_id"])),
        "has_client_id": bool(_get_env(*_ENV_ALIASES["client_id"])),
        "has_client_secret": bool(_get_env(*_ENV_ALIASES["client_secret"])),
        "has_refresh_token": bool(_get_env(*_ENV_ALIASES["refresh_token"])),
    }


def creds_fingerprint() -> dict:
    """Safe hints to verify Railway values match what you minted (no full secrets)."""
    cid = _get_env(*_ENV_ALIASES["client_id"])
    secret = _get_env(*_ENV_ALIASES["client_secret"])
    refresh = _get_env(*_ENV_ALIASES["refresh_token"])

    def token_hint(value: str | None, head: int = 10) -> dict | None:
        if not value:
            return None
        return {
            "length": len(value),
            "starts_with": value[:head],
            "ends_with": value[-4:],
        }

    return {
        "client_id": token_hint(cid, 12),
        "client_id_looks_valid": bool(cid and cid.endswith(".apps.googleusercontent.com")),
        "client_secret": {
            "length": len(secret) if secret else 0,
            "looks_like_gocspx": bool(secret and secret.startswith("GOCSPX-")),
        },
        "refresh_token": token_hint(refresh, 12),
        "refresh_token_looks_valid": bool(refresh and refresh.startswith("1//")),
    }
