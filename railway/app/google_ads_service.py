from __future__ import annotations

from typing import Any

from google.ads.googleads.client import GoogleAdsClient

from .auth import GoogleAdsEnv, load_google_ads_env


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
