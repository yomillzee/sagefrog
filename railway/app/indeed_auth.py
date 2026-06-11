"""Indeed API authentication and environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Indeed supports: CLIENT_ID + CLIENT_SECRET (OAuth) or INDEED_API_KEY (legacy)
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "client_id": ("INDEED_CLIENT_ID",),
    "client_secret": ("INDEED_CLIENT_SECRET",),
    "api_key": ("INDEED_API_KEY",),
    "publisher_id": ("INDEED_PUBLISHER_ID",),
}


def _get_env(*keys: str) -> str | None:
    """Get environment variable, stripping quotes if needed."""
    for key in keys:
        raw = os.getenv(key)
        if not raw:
            continue
        val = raw.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1].strip()
        if val:
            return val
    return None


def _get_required_env(*keys: str) -> str:
    """Get required environment variable or raise error."""
    val = _get_env(*keys)
    if not val:
        raise RuntimeError(f"Missing required environment variable (one of): {', '.join(keys)}")
    return val


@dataclass(frozen=True)
class IndeedEnv:
    """Indeed API environment configuration."""
    client_id: str
    client_secret: str
    api_key: str | None = None
    publisher_id: str | None = None


def load_indeed_env() -> IndeedEnv:
    """Load and validate Indeed API credentials from environment."""
    return IndeedEnv(
        client_id=_get_required_env("INDEED_CLIENT_ID"),
        client_secret=_get_required_env("INDEED_CLIENT_SECRET"),
        api_key=_get_env("INDEED_API_KEY"),
        publisher_id=_get_env("INDEED_PUBLISHER_ID"),
    )


def env_summary() -> dict:
    """Return summary of Indeed environment configuration (no secrets)."""
    return {
        "has_client_id": bool(_get_env("INDEED_CLIENT_ID")),
        "has_client_secret": bool(_get_env("INDEED_CLIENT_SECRET")),
        "has_api_key": bool(_get_env("INDEED_API_KEY")),
        "has_publisher_id": bool(_get_env("INDEED_PUBLISHER_ID")),
    }
