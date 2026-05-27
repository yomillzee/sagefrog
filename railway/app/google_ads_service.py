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


def search(customer_id: str, query: str, *, client: GoogleAdsClient | None = None) -> list[dict]:
    client = client or build_client()
    ga_service = client.get_service("GoogleAdsService")
    # Newer google-ads Python clients don't accept page_size here.
    resp = ga_service.search(customer_id=customer_id, query=query)
    rows: list[dict] = []
    for row in resp:
        # proto-plus objects are not JSON-serializable; convert to dict-ish via str for now
        # (you can replace with MessageToDict if you want richer structured output)
        rows.append({"row": str(row)})
    return rows


def list_accessible_customer_ids(*, client: GoogleAdsClient | None = None) -> list[str]:
    client = client or build_client()
    customer_service = client.get_service("CustomerService")
    resp = customer_service.list_accessible_customers()
    ids: list[str] = []
    for resource_name in resp.resource_names:
        # Format is usually "customers/1234567890"
        cid = resource_name.split("/")[-1].strip()
        if cid:
            ids.append(cid)
    return sorted(set(ids))


def get_account_metadata(customer_id: str, *, client: GoogleAdsClient | None = None) -> dict[str, Any]:
    client = client or build_client()
    ga_service = client.get_service("GoogleAdsService")
    query = (
        "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
        "customer.time_zone, customer.status FROM customer LIMIT 1"
    )
    resp = ga_service.search(customer_id=customer_id, query=query)
    first = next(iter(resp), None)
    if first is None:
        return {
            "customer_id": customer_id,
            "resource_name": f"customers/{customer_id}",
            "descriptive_name": None,
            "currency_code": None,
            "time_zone": None,
            "status": "ok",
            "error": None,
        }
    customer = first.customer
    return {
        "customer_id": str(customer.id),
        "resource_name": customer.resource_name,
        "descriptive_name": getattr(customer, "descriptive_name", None),
        "currency_code": getattr(customer, "currency_code", None),
        "time_zone": getattr(customer, "time_zone", None),
        "status": "ok",
        "error": None,
    }


def account_summary(customer_id: str, date_range: str = "LAST_30_DAYS", *, client: GoogleAdsClient | None = None) -> dict[str, Any]:
    client = client or build_client()
    ga_service = client.get_service("GoogleAdsService")
    query = f"""
        SELECT
          customer.id,
          customer.descriptive_name,
          metrics.impressions,
          metrics.clicks,
          metrics.conversions,
          metrics.cost_micros
        FROM customer
        WHERE segments.date DURING {date_range}
    """
    resp = ga_service.search(customer_id=customer_id, query=query)
    impressions = 0
    clicks = 0
    conversions = 0.0
    cost_micros = 0
    name: str | None = None
    for row in resp:
        name = row.customer.descriptive_name or name
        impressions += int(getattr(row.metrics, "impressions", 0) or 0)
        clicks += int(getattr(row.metrics, "clicks", 0) or 0)
        conversions += float(getattr(row.metrics, "conversions", 0.0) or 0.0)
        cost_micros += int(getattr(row.metrics, "cost_micros", 0) or 0)

    spend = cost_micros / 1_000_000
    ctr = (clicks / impressions) if impressions else 0.0
    return {
        "customer_id": customer_id,
        "descriptive_name": name,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "cost_micros": cost_micros,
        "spend": spend,
        "ctr": ctr,
    }
