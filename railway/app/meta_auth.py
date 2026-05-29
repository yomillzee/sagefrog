from __future__ import annotations

import os
from dataclasses import dataclass

_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "app_id": ("META_APP_ID", "FACEBOOK_APP_ID"),
    "app_secret": ("META_APP_SECRET", "FACEBOOK_APP_SECRET"),
    "access_token": ("META_ACCESS_TOKEN", "FACEBOOK_ACCESS_TOKEN"),
    "business_id": ("META_BUSINESS_ID", "FACEBOOK_BUSINESS_ID"),
    "api_version": ("META_API_VERSION", "FACEBOOK_API_VERSION"),
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
class MetaEnv:
    app_id: str
    app_secret: str
    access_token: str
    business_id: str
    api_version: str


def load_meta_env() -> MetaEnv:
    return MetaEnv(
        app_id=_get_required_env(*_ENV_ALIASES["app_id"]),
        app_secret=_get_required_env(*_ENV_ALIASES["app_secret"]),
        access_token=_get_required_env(*_ENV_ALIASES["access_token"]),
        business_id=_get_required_env(*_ENV_ALIASES["business_id"]),
        api_version=_get_env(*_ENV_ALIASES["api_version"]) or "v21.0",
    )


def env_summary() -> dict:
    token = _get_env(*_ENV_ALIASES["access_token"])
    business_id = _get_env(*_ENV_ALIASES["business_id"])
    return {
        "has_app_id": bool(_get_env(*_ENV_ALIASES["app_id"])),
        "has_app_secret": bool(_get_env(*_ENV_ALIASES["app_secret"])),
        "has_access_token": bool(token),
        "has_business_id": bool(business_id),
        "business_id": business_id,
        "meta_api_version": _get_env(*_ENV_ALIASES["api_version"]) or "v21.0",
        "access_token_looks_valid": bool(token and len(token) > 20),
    }
