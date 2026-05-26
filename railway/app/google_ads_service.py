from __future__ import annotations

from typing import Any

from google.ads.googleads.client import GoogleAdsClient
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from auth import GoogleAdsEnv, load_google_ads_env


def build_client(env: GoogleAdsEnv | None = None) -> GoogleAdsClient:
    """
    Build a GoogleAdsClient using OAuth refresh-token flow.

    Expected env vars (Railway variables):
    - GOOGLE_ADS_DEVELOPER_TOKEN
    - GOOGLE_ADS_CLIENT_ID
    - GOOGLE_ADS_CLIENT_SECRET
    - GOOGLE_ADS_REFRESH_TOKEN
    - GOOGLE_ADS_LOGIN_CUSTOMER_ID (optional)
    """
    env = env or load_google_ads_env()
    cfg: dict[str, Any] = {
        "developer_token": env.developer_token,
        "client_id": env.client_id,
        "client_secret": env.client_secret,
        "refresh_token": env.refresh_token,
        "use_proto_plus": True,
    }
    if env.login_customer_id:
        cfg["login_customer_id"] = env.login_customer_id
    return GoogleAdsClient.load_from_dict(cfg)


def test_refresh_token(env: GoogleAdsEnv | None = None) -> dict[str, Any]:
    """
    Exchange refresh token for a short-lived access token (no GAQL / Ads API call).
    Use this to debug invalid_grant vs developer-token / customer issues.
    """
    env = env or load_google_ads_env()
    try:
        creds = Credentials(
            token=None,
            refresh_token=env.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=env.client_id,
            client_secret=env.client_secret,
        )
        creds.refresh(Request())
        expires = creds.expiry.isoformat() if creds.expiry else None
        return {
            "ok": True,
            "message": "OAuth refresh succeeded. Client ID, secret, and refresh token match.",
            "token_expires_at": expires,
            "error": None,
        }
    except Exception as e:
        err = str(e)
        hint = (
            "Usually: refresh token revoked, wrong client secret, token from a different "
            "OAuth client, or missing https://www.googleapis.com/auth/adwords scope."
        )
        if "invalid_grant" in err.lower():
            hint = (
                "invalid_grant: regenerate refresh token with the same GOOGLE_CLIENT_ID / "
                "GOOGLE_CLIENT_SECRET in Railway, using scope adwords."
            )
        return {
            "ok": False,
            "message": "OAuth refresh failed.",
            "token_expires_at": None,
            "error": f"{err} — {hint}",
        }


def search(customer_id: str, query: str, *, client: GoogleAdsClient | None = None, page_size: int = 100) -> list[dict]:
    client = client or build_client()
    ga_service = client.get_service("GoogleAdsService")
    resp = ga_service.search(customer_id=customer_id, query=query, page_size=page_size)
    rows: list[dict] = []
    for row in resp:
        # proto-plus objects are not JSON-serializable; convert to dict-ish via str for now
        # (you can replace with MessageToDict if you want richer structured output)
        rows.append({"row": str(row)})
    return rows
