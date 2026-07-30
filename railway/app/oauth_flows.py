"""OAuth authorize URLs, code exchange, and session state helpers."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

import auth as google_auth
import linkedin_auth
import meta_auth

_log = logging.getLogger(__name__)

PLATFORMS = frozenset({"google_ads", "linkedin", "linkedin_organic", "meta", "gsc", "google_analytics", "google_tag_manager", "hubspot", "harvest", "microsoft_ads"})

HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_SCOPES = "oauth crm.objects.contacts.read crm.objects.deals.read"

# Harvest OAuth2 lives on the shared id.getharvest.com identity host; the data
# API (time entries, reports) is on api.harvestapp.com and needs the
# Harvest-Account-Id header alongside the bearer token. We capture that account
# id during the token exchange (see fetch_harvest_accounts) and stash it in the
# stored token's metadata so the hours page can call the API without extra env.
HARVEST_AUTH_URL = "https://id.getharvest.com/oauth2/authorize"
HARVEST_TOKEN_URL = "https://id.getharvest.com/api/v2/oauth2/token"
HARVEST_ACCOUNTS_URL = "https://id.getharvest.com/api/v2/accounts"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GTM_SCOPE = "https://www.googleapis.com/auth/tagmanager.readonly"

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_SCOPES = "r_ads r_ads_reporting r_organization_social r_organization_admin"

# Organic (company-page) connector runs against its own Community-Management
# app. These are the real, current scope names LinkedIn exposes for that product
# (note rw_organization_admin — there is no r_organization_admin scope), and it
# deliberately omits the r_ads* scopes the organic app isn't granted.
LINKEDIN_ORGANIC_SCOPES = "r_organization_social rw_organization_admin r_organization_followers"

META_SCOPES = "ads_read,business_management"

# Microsoft Advertising via Google OAuth. Microsoft Advertising now supports
# Google as an identity provider: the user authorizes through Google's normal
# OAuth flow (Google is used ONLY to authenticate the user — the resulting
# Google access token is presented to the Microsoft Advertising API with an
# `IdentityProvider: Google` header alongside the Microsoft developer token).
# Google's identity scopes are all that's needed; Microsoft enforces its own
# advertiser permission checks. See microsoft_ads_service for the API side.
#   https://learn.microsoft.com/advertising/guides/authentication-oauth-consent
MICROSOFT_ADS_SCOPES = "profile email"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def public_base_url() -> str:
    explicit = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    if domain:
        return f"https://{domain}"
    return "https://sagefrog-production.up.railway.app"


def callback_url(platform: str) -> str:
    slug = _normalize_platform(platform)
    if slug == "microsoft_ads":
        # Google validates that the redirect_uri on the authorize request, the
        # token exchange, and the value registered in the Google Cloud console
        # all match byte-for-byte. Microsoft Advertising's connect flow is
        # pinned to whatever is registered as MICROSOFT_ADS_REDIRECT_URI, so use
        # that verbatim rather than deriving it — the callback route is mounted
        # at this exact path (see main._register_microsoft_ads_callback).
        explicit = (os.getenv("MICROSOFT_ADS_REDIRECT_URI") or "").strip()
        if explicit:
            return explicit
    return f"{public_base_url()}/oauth/{slug}/callback"


def microsoft_ads_callback_path() -> str:
    """Path component of MICROSOFT_ADS_REDIRECT_URI (e.g. '/oauth/microsoft_ads/callback').

    Used to mount the callback route at the exact path Google will redirect to,
    so the callback never 404s regardless of how the redirect URI is configured.
    Falls back to the conventional per-platform path when the env var is unset.
    """
    from urllib.parse import urlsplit

    explicit = (os.getenv("MICROSOFT_ADS_REDIRECT_URI") or "").strip()
    if explicit:
        path = urlsplit(explicit).path.strip()
        if path.startswith("/"):
            return path
    return "/oauth/microsoft_ads/callback"


def _microsoft_ads_env() -> dict[str, str]:
    return {
        "client_id": (os.getenv("MICROSOFT_ADS_CLIENT_ID") or "").strip(),
        "client_secret": (os.getenv("MICROSOFT_ADS_CLIENT_SECRET") or "").strip(),
        "redirect_uri": (os.getenv("MICROSOFT_ADS_REDIRECT_URI") or "").strip(),
        "developer_token": (os.getenv("MICROSOFT_ADS_DEVELOPER_TOKEN") or "").strip(),
        "provider": (os.getenv("MICROSOFT_ADS_OAUTH_PROVIDER") or "google").strip().lower(),
    }


def _normalize_platform(platform: str) -> str:
    slug = (platform or "").strip().lower()
    if slug not in PLATFORMS:
        raise ValueError(f"Unknown OAuth platform '{platform}'.")
    return slug


def make_state() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Signed connect links — let a client/specialist authorize one client's
# connector (e.g. HubSpot, which has no agency-wide business manager) without a
# login. The signed token carries the client_slug + platform so the OAuth state
# round-trips cryptographically instead of via session.
# ---------------------------------------------------------------------------

import base64 as _base64
import hashlib as _hashlib
import hmac as _hmac
import json as _json
import time as _time


def _connect_secret() -> bytes:
    # Same signing secret as session cookies (AUTH_SESSION_SECRET): dedicated,
    # fail-closed in production, dev-only fallback. See security.session_signing_secret.
    from security import session_signing_secret

    return session_signing_secret().encode()


def sign_connect_state(client_slug: str, platform: str, *, ttl_seconds: int = 7 * 86400) -> str:
    """Signed, expiring token carrying (client_slug, platform). Serves as both the
    connect-link token and the OAuth `state` param."""
    payload = {
        "c": (client_slug or "").strip().lower(),
        "p": _normalize_platform(platform),
        "exp": int(_time.time()) + int(ttl_seconds),
    }
    raw = _base64.urlsafe_b64encode(_json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = _hmac.new(_connect_secret(), raw.encode(), _hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def verify_connect_state(token: str) -> tuple[str, str] | None:
    """Return (client_slug, platform) for a valid, unexpired token, else None."""
    try:
        raw, sig = (token or "").rsplit(".", 1)
        expect = _hmac.new(_connect_secret(), raw.encode(), _hashlib.sha256).hexdigest()[:32]
        if not _hmac.compare_digest(sig, expect):
            return None
        payload = _json.loads(_base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if int(payload.get("exp", 0)) < int(_time.time()):
            return None
        slug = str(payload.get("c") or "").strip().lower()
        plat = str(payload.get("p") or "").strip().lower()
        if not slug or plat not in PLATFORMS:
            return None
        return slug, plat
    except Exception:
        return None


def store_oauth_state(request, *, platform: str, state: str, return_to: str, client_slug: str = "") -> None:
    slug = _normalize_platform(platform)
    request.session[f"oauth_state_{slug}"] = state
    request.session["oauth_return_to"] = (return_to or "/admin").strip() or "/admin"
    request.session["oauth_client_slug"] = (client_slug or "").strip()


def pop_oauth_state(request, *, platform: str) -> tuple[str | None, str, str]:
    """Returns (state, return_to, client_slug). client_slug='' means agency-wide global token."""
    slug = _normalize_platform(platform)
    state = request.session.pop(f"oauth_state_{slug}", None)
    return_to = request.session.pop("oauth_return_to", "/admin") or "/admin"
    client_slug = request.session.pop("oauth_client_slug", "") or ""
    return state, return_to, client_slug


def validate_return_to(path: str) -> str:
    text = (path or "/admin").strip()
    if not text.startswith("/") or text.startswith("//"):
        return "/admin"
    return text


def connect_prerequisites(platform: str) -> dict[str, Any]:
    """Check whether OAuth app credentials exist in env to start a connect flow."""
    slug = _normalize_platform(platform)
    if slug == "google_ads":
        summary = google_auth.env_summary()
        return {
            "ready": bool(summary.get("has_client_id") and summary.get("has_client_secret")),
            "missing": [
                label
                for key, label in (
                    ("has_client_id", "GOOGLE_ADS_CLIENT_ID"),
                    ("has_client_secret", "GOOGLE_ADS_CLIENT_SECRET"),
                    ("has_developer_token", "GOOGLE_ADS_DEVELOPER_TOKEN"),
                )
                if not summary.get(key)
            ],
            "note": "Developer token stays in Railway; Connect mints the refresh token.",
        }
    if slug == "gsc":
        summary = google_auth.env_summary()
        return {
            "ready": bool(summary.get("has_client_id") and summary.get("has_client_secret")),
            "missing": [
                label
                for key, label in (
                    ("has_client_id", "GOOGLE_ADS_CLIENT_ID"),
                    ("has_client_secret", "GOOGLE_ADS_CLIENT_SECRET"),
                )
                if not summary.get(key)
            ],
            "note": (
                "Reuses the Google Ads OAuth client. Connect with the agency Google "
                "account that has access to every client's Search Console property."
            ),
        }
    if slug == "google_analytics":
        summary = google_auth.env_summary()
        return {
            "ready": bool(summary.get("has_client_id") and summary.get("has_client_secret")),
            "missing": [
                label
                for key, label in (
                    ("has_client_id", "GOOGLE_ADS_CLIENT_ID"),
                    ("has_client_secret", "GOOGLE_ADS_CLIENT_SECRET"),
                )
                if not summary.get(key)
            ],
            "note": (
                "Reuses the Google Ads OAuth client. Connect with the Google account "
                "that has Viewer access to the GA4 property."
            ),
        }
    if slug == "google_tag_manager":
        summary = google_auth.env_summary()
        return {
            "ready": bool(summary.get("has_client_id") and summary.get("has_client_secret")),
            "missing": [
                label
                for key, label in (
                    ("has_client_id", "GOOGLE_ADS_CLIENT_ID"),
                    ("has_client_secret", "GOOGLE_ADS_CLIENT_SECRET"),
                )
                if not summary.get(key)
            ],
            "note": (
                "Reuses the Google Ads OAuth client. Connect with the Google account "
                "that has Read access to the GTM container."
            ),
        }
    if slug == "linkedin":
        summary = linkedin_auth.env_summary()
        return {
            "ready": bool(summary.get("has_client_id") and summary.get("has_client_secret")),
            "missing": [
                label
                for key, label in (
                    ("has_client_id", "LINKEDIN_CLIENT_ID"),
                    ("has_client_secret", "LINKEDIN_CLIENT_SECRET"),
                )
                if not summary.get(key)
            ],
            "note": "Connect stores the refresh token in Postgres.",
        }
    if slug == "linkedin_organic":
        summary = linkedin_auth.organic_env_summary()
        return {
            "ready": bool(summary.get("has_client_id") and summary.get("has_client_secret")),
            "missing": [
                label
                for key, label in (
                    ("has_client_id", "LINKEDIN_ORGANIC_CLIENT_ID"),
                    ("has_client_secret", "LINKEDIN_ORGANIC_CLIENT_SECRET"),
                )
                if not summary.get(key)
            ],
            "note": (
                "Uses the Community-Management LinkedIn app. Add "
                "/oauth/linkedin_organic/callback to that app's authorized "
                "redirect URLs, and connect as an admin of the company page."
            ),
        }
    if slug == "hubspot":
        cid = (os.getenv("HUBSPOT_CLIENT_ID") or "").strip()
        secret = (os.getenv("HUBSPOT_CLIENT_SECRET") or "").strip()
        return {
            "ready": bool(cid and secret),
            "missing": [
                label for val, label in (
                    (cid, "HUBSPOT_CLIENT_ID"),
                    (secret, "HUBSPOT_CLIENT_SECRET"),
                ) if not val
            ],
            "note": "Connect each client's own HubSpot portal; stores the refresh token in Postgres.",
        }
    if slug == "harvest":
        cid = (os.getenv("HARVEST_CLIENT_ID") or "").strip()
        secret = (os.getenv("HARVEST_CLIENT_SECRET") or "").strip()
        return {
            "ready": bool(cid and secret),
            "missing": [
                label for val, label in (
                    (cid, "HARVEST_CLIENT_ID"),
                    (secret, "HARVEST_CLIENT_SECRET"),
                ) if not val
            ],
            "note": (
                "Connect the agency Harvest account once (all clients live under it). "
                "Add this app's /oauth/harvest/callback as the Redirect URL in your "
                "Harvest OAuth2 app. Stores the refresh token in Postgres."
            ),
        }
    if slug == "microsoft_ads":
        env = _microsoft_ads_env()
        return {
            "ready": bool(env["client_id"] and env["client_secret"] and env["redirect_uri"] and env["developer_token"]),
            "missing": [
                label
                for val, label in (
                    (env["client_id"], "MICROSOFT_ADS_CLIENT_ID"),
                    (env["client_secret"], "MICROSOFT_ADS_CLIENT_SECRET"),
                    (env["redirect_uri"], "MICROSOFT_ADS_REDIRECT_URI"),
                    (env["developer_token"], "MICROSOFT_ADS_DEVELOPER_TOKEN"),
                )
                if not val
            ],
            "note": (
                "Authorizes with Google (MICROSOFT_ADS_OAUTH_PROVIDER=google) and calls the "
                "Microsoft Advertising API with the developer token. Connect with the Google "
                "account that has access to the Microsoft Advertising accounts."
            ),
        }
    summary = meta_auth.env_summary()
    return {
        "ready": bool(summary.get("has_app_id") and summary.get("has_app_secret")),
        "missing": [
            label
            for key, label in (
                ("has_app_id", "META_APP_ID"),
                ("has_app_secret", "META_APP_SECRET"),
            )
            if not summary.get(key)
        ],
        "note": "Connect stores a long-lived access token. META_BUSINESS_ID can stay in Railway.",
    }


def build_authorize_url(platform: str, *, state: str) -> str:
    slug = _normalize_platform(platform)
    redirect_uri = callback_url(slug)
    if slug == "google_ads":
        client_id = google_auth._get_env(*google_auth._ENV_ALIASES["client_id"])
        if not client_id:
            raise RuntimeError("Set GOOGLE_ADS_CLIENT_ID before connecting Google Ads.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_ADS_SCOPE,
            "access_type": "offline",
            "prompt": "select_account consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    if slug == "gsc":
        client_id = google_auth._get_env(*google_auth._ENV_ALIASES["client_id"])
        if not client_id:
            raise RuntimeError("Set GOOGLE_ADS_CLIENT_ID before connecting Search Console.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GSC_SCOPE,
            "access_type": "offline",
            "prompt": "select_account consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    if slug == "google_analytics":
        client_id = google_auth._get_env(*google_auth._ENV_ALIASES["client_id"])
        if not client_id:
            raise RuntimeError("Set GOOGLE_ADS_CLIENT_ID before connecting Google Analytics.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GA4_SCOPE,
            "access_type": "offline",
            "prompt": "select_account consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    if slug == "google_tag_manager":
        client_id = google_auth._get_env(*google_auth._ENV_ALIASES["client_id"])
        if not client_id:
            raise RuntimeError("Set GOOGLE_ADS_CLIENT_ID before connecting Google Tag Manager.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GTM_SCOPE,
            "access_type": "offline",
            "prompt": "select_account consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    if slug == "linkedin":
        client_id = linkedin_auth._get_env(*linkedin_auth._ENV_ALIASES["client_id"])
        if not client_id:
            raise RuntimeError("Set LINKEDIN_CLIENT_ID before connecting LinkedIn.")
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": LINKEDIN_SCOPES,
        }
        return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"
    if slug == "linkedin_organic":
        client_id = linkedin_auth._get_env(*linkedin_auth._ORGANIC_ENV_ALIASES["client_id"])
        if not client_id:
            raise RuntimeError(
                "Set LINKEDIN_ORGANIC_CLIENT_ID before connecting LinkedIn Organic."
            )
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": LINKEDIN_ORGANIC_SCOPES,
        }
        return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"
    if slug == "hubspot":
        client_id = (os.getenv("HUBSPOT_CLIENT_ID") or "").strip()
        if not client_id:
            raise RuntimeError("Set HUBSPOT_CLIENT_ID before connecting HubSpot.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": HUBSPOT_SCOPES,
            "state": state,
        }
        return f"{HUBSPOT_AUTH_URL}?{urlencode(params)}"
    if slug == "harvest":
        client_id = (os.getenv("HARVEST_CLIENT_ID") or "").strip()
        if not client_id:
            raise RuntimeError("Set HARVEST_CLIENT_ID before connecting Harvest.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        return f"{HARVEST_AUTH_URL}?{urlencode(params)}"
    if slug == "microsoft_ads":
        ms = _microsoft_ads_env()
        if not ms["client_id"]:
            raise RuntimeError("Set MICROSOFT_ADS_CLIENT_ID before connecting Microsoft Ads.")
        if not ms["redirect_uri"]:
            raise RuntimeError("Set MICROSOFT_ADS_REDIRECT_URI before connecting Microsoft Ads.")
        params = {
            "client_id": ms["client_id"],
            "redirect_uri": ms["redirect_uri"],
            "response_type": "code",
            "scope": MICROSOFT_ADS_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    env = meta_auth._get_env(*meta_auth._ENV_ALIASES["app_id"])
    api_version = meta_auth._get_env(*meta_auth._ENV_ALIASES["api_version"]) or "v21.0"
    if not env:
        raise RuntimeError("Set META_APP_ID before connecting Meta.")
    params = {
        "client_id": env,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": META_SCOPES,
        "response_type": "code",
    }
    return f"https://www.facebook.com/{api_version}/dialog/oauth?{urlencode(params)}"


def exchange_code(platform: str, *, code: str) -> dict[str, Any]:
    slug = _normalize_platform(platform)
    redirect_uri = callback_url(slug)
    if slug == "google_ads":
        return _exchange_google_code(code, redirect_uri=redirect_uri, scope_label=GOOGLE_ADS_SCOPE)
    if slug == "gsc":
        return _exchange_google_code(code, redirect_uri=redirect_uri, scope_label=GSC_SCOPE)
    if slug == "google_analytics":
        return _exchange_google_code(code, redirect_uri=redirect_uri, scope_label=GA4_SCOPE)
    if slug == "google_tag_manager":
        return _exchange_google_code(code, redirect_uri=redirect_uri, scope_label=GTM_SCOPE)
    if slug == "linkedin":
        return _exchange_linkedin_code(code, redirect_uri=redirect_uri)
    if slug == "linkedin_organic":
        return _exchange_linkedin_code(code, redirect_uri=redirect_uri, organic=True)
    if slug == "hubspot":
        return _exchange_hubspot_code(code, redirect_uri=redirect_uri)
    if slug == "harvest":
        return _exchange_harvest_code(code, redirect_uri=redirect_uri)
    if slug == "microsoft_ads":
        return _exchange_microsoft_ads_code(code, redirect_uri=redirect_uri)
    return _exchange_meta_code(code, redirect_uri=redirect_uri)


def _exchange_microsoft_ads_code(code: str, *, redirect_uri: str) -> dict[str, Any]:
    """Exchange a Google authorization code for tokens (Microsoft Ads connector).

    Microsoft Advertising delegates user authentication to Google, so the code
    is redeemed at Google's token endpoint using the Microsoft-Ads-specific
    Google OAuth client. The authenticated email is captured from Google's
    userinfo endpoint and stored in token metadata so the connector UI can show
    who authorized. Tokens themselves are never logged.
    """
    ms = _microsoft_ads_env()
    if not ms["client_id"] or not ms["client_secret"]:
        raise RuntimeError("Set MICROSOFT_ADS_CLIENT_ID and MICROSOFT_ADS_CLIENT_SECRET before connecting Microsoft Ads.")
    body = {
        "code": code,
        "client_id": ms["client_id"],
        "client_secret": ms["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(GOOGLE_TOKEN_URL, data=body)
    if response.status_code >= 400:
        raise RuntimeError(f"Google token exchange failed ({response.status_code}): {response.text[:500]}")
    data = response.json()
    refresh = (data.get("refresh_token") or "").strip()
    if not refresh:
        raise RuntimeError(
            "Google did not return a refresh token. Revoke this app's access at "
            "https://myaccount.google.com/permissions and connect again (the flow requests "
            "access_type=offline and prompt=consent)."
        )
    access = (data.get("access_token") or "").strip()
    expires_in = int(data.get("expires_in") or 0)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in) if expires_in else None
    email = _fetch_google_userinfo_email(access) if access else ""
    return {
        "refresh_token": refresh,
        "access_token": access,
        "token_expires_at": expires_at,
        "scopes": MICROSOFT_ADS_SCOPES,
        "metadata": {"email": email} if email else None,
    }


def _fetch_google_userinfo_email(access_token: str) -> str:
    """Return the authenticated Google account's email, or '' on any failure.

    Best-effort: never blocks a token exchange. Used only to label the connection.
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        return str(resp.json().get("email") or "").strip()
    except Exception as exc:
        _log.warning("Google userinfo fetch failed for Microsoft Ads connect: %s", exc)
        return ""


def refresh_microsoft_ads_access_token(refresh_token: str) -> dict[str, Any]:
    """Exchange a stored Google refresh token for a fresh access token.

    Returns {"access_token": str, "token_expires_at": datetime|None}. Raises on
    failure. Never logs the token.
    """
    ms = _microsoft_ads_env()
    if not ms["client_id"] or not ms["client_secret"]:
        raise RuntimeError("MICROSOFT_ADS_CLIENT_ID / MICROSOFT_ADS_CLIENT_SECRET not set.")
    body = {
        "grant_type": "refresh_token",
        "client_id": ms["client_id"],
        "client_secret": ms["client_secret"],
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(GOOGLE_TOKEN_URL, data=body)
    if response.status_code >= 400:
        raise RuntimeError(f"Google token refresh failed ({response.status_code}): {response.text[:400]}")
    data = response.json()
    access = (data.get("access_token") or "").strip()
    if not access:
        raise RuntimeError("Google refresh returned no access token.")
    expires_in = int(data.get("expires_in") or 0)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in) if expires_in else None
    return {"access_token": access, "token_expires_at": expires_at}


def _exchange_harvest_code(code: str, *, redirect_uri: str) -> dict[str, Any]:
    client_id = (os.getenv("HARVEST_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("HARVEST_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Set HARVEST_CLIENT_ID and HARVEST_CLIENT_SECRET before connecting Harvest.")
    body = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            HARVEST_TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Harvest token exchange failed ({response.status_code}): {response.text[:500]}")
    data = response.json()
    refresh = (data.get("refresh_token") or "").strip()
    if not refresh:
        raise RuntimeError("Harvest did not return a refresh token.")
    expires_in = int(data.get("expires_in") or 0)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in) if expires_in else None
    access = (data.get("access_token") or "").strip()
    # Capture the Harvest account id (needed as the Harvest-Account-Id header on
    # every data-API call) so the hours page works without extra env config.
    account = fetch_harvest_accounts(access) if access else {}
    return {
        "refresh_token": refresh,
        "access_token": access,
        "token_expires_at": expires_at,
        "scopes": (data.get("scope") or "").strip() or None,
        "metadata": account or None,
    }


def fetch_harvest_accounts(access_token: str) -> dict[str, Any]:
    """Return the first Harvest-product account the token can reach.

    Best-effort: returns {} on any failure so it never blocks a token exchange.
    An operator can still override with HARVEST_ACCOUNT_ID if a token has access
    to several Harvest accounts and the wrong one is picked.
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                HARVEST_ACCOUNTS_URL,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
        resp.raise_for_status()
        accounts = resp.json().get("accounts") or []
    except Exception as exc:
        _log.warning("Harvest accounts fetch failed: %s", exc)
        return {}
    harvest_accounts = [a for a in accounts if str(a.get("product") or "").lower() == "harvest"]
    chosen = (harvest_accounts or accounts or [None])[0]
    if not chosen:
        return {}
    out: dict[str, Any] = {}
    acct_id = str(chosen.get("id") or "").strip()
    if acct_id:
        out["account_id"] = acct_id
    if chosen.get("name"):
        out["account_name"] = str(chosen["name"])
    return out


def refresh_harvest_access_token(refresh_token: str) -> str:
    """Exchange a stored Harvest refresh token for a fresh access token."""
    client_id = (os.getenv("HARVEST_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("HARVEST_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("HARVEST_CLIENT_ID / HARVEST_CLIENT_SECRET not set.")
    body = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            HARVEST_TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Harvest token refresh failed ({response.status_code}): {response.text[:400]}")
    access = (response.json().get("access_token") or "").strip()
    if not access:
        raise RuntimeError("Harvest refresh returned no access token.")
    return access


def _exchange_hubspot_code(code: str, *, redirect_uri: str) -> dict[str, Any]:
    client_id = (os.getenv("HUBSPOT_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("HUBSPOT_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Set HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET before connecting HubSpot.")
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            HUBSPOT_TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"HubSpot token exchange failed ({response.status_code}): {response.text[:500]}")
    data = response.json()
    refresh = (data.get("refresh_token") or "").strip()
    if not refresh:
        raise RuntimeError("HubSpot did not return a refresh token.")
    expires_in = int(data.get("expires_in") or 0)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in) if expires_in else None
    access = data.get("access_token")
    # Capture the connected portal's identity so the callback can verify it matches
    # the client being configured (and so we have a tamper-evident record of which
    # HubSpot account a client's token belongs to).
    portal = fetch_hubspot_portal_info(access) if access else {}
    return {
        "refresh_token": refresh,
        "access_token": access,
        "token_expires_at": expires_at,
        "scopes": HUBSPOT_SCOPES,
        "metadata": portal or None,
    }


def fetch_hubspot_portal_info(access_token: str) -> dict[str, Any]:
    """Return the connected HubSpot portal's identity (portal_id, hub_domain, company).

    Best-effort: returns {} on any failure so it never blocks a token exchange.
    """
    import json as _json
    import urllib.request

    try:
        req = urllib.request.Request(
            "https://api.hubapi.com/account-info/v3/details",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            info = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _log.warning("HubSpot portal-info fetch failed: %s", exc)
        return {}
    out: dict[str, Any] = {}
    portal_id = str(info.get("portalId") or "").strip()
    if portal_id:
        out["portal_id"] = portal_id
    if info.get("companyName"):
        out["company_name"] = str(info["companyName"])
    domain = info.get("uiDomain") or info.get("domain")
    if domain:
        out["hub_domain"] = str(domain)
    return out


def verify_connected_account(platform: str, tokens: dict[str, Any], *, client_slug: str) -> str | None:
    """Guard against wiring the wrong external account into a client's connection.

    Returns a user-facing error message if the just-authorized account doesn't match
    the account this client is already configured for, else None. Best-effort: if the
    connected account can't be determined, or the client has no configured account yet
    (first connect), it does not block.
    """
    slug = _normalize_platform(platform)
    if slug != "hubspot" or not (client_slug or "").strip():
        return None
    meta = tokens.get("metadata") or {}
    connected_portal = str(meta.get("portal_id") or "").strip()
    if not connected_portal:
        return None  # couldn't determine the portal — don't block the connect
    try:
        import connector_config_store

        cfg = connector_config_store.get_config(client_slug, "hubspot")
    except Exception as exc:
        _log.warning("HubSpot portal verification skipped [%s]: %s", client_slug, exc)
        return None
    expected = str(cfg.source_account_id or "").strip() if cfg else ""
    if expected and expected != connected_portal:
        connected_label = meta.get("hub_domain") or connected_portal
        return (
            f"This HubSpot login is connected to portal {connected_label} "
            f"(id {connected_portal}), but this client is configured for portal "
            f"{expected}. Connect the correct HubSpot account, or update the client's "
            f"portal in connector settings before reconnecting."
        )
    return None


def refresh_hubspot_access_token(refresh_token: str) -> str:
    """Exchange a stored HubSpot refresh token for a fresh access token."""
    client_id = (os.getenv("HUBSPOT_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("HUBSPOT_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("HUBSPOT_CLIENT_ID / HUBSPOT_CLIENT_SECRET not set.")
    body = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            HUBSPOT_TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"HubSpot token refresh failed ({response.status_code}): {response.text[:400]}")
    access = (response.json().get("access_token") or "").strip()
    if not access:
        raise RuntimeError("HubSpot refresh returned no access token.")
    return access


def _exchange_google_code(code: str, *, redirect_uri: str, scope_label: str = GOOGLE_ADS_SCOPE) -> dict[str, Any]:
    client_id = google_auth._get_required_env(*google_auth._ENV_ALIASES["client_id"])
    client_secret = google_auth._get_required_env(*google_auth._ENV_ALIASES["client_secret"])
    body = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(GOOGLE_TOKEN_URL, data=body)
    if response.status_code >= 400:
        raise RuntimeError(f"Google token exchange failed ({response.status_code}): {response.text[:500]}")
    data = response.json()
    refresh = (data.get("refresh_token") or "").strip()
    if not refresh:
        raise RuntimeError(
            "Google did not return a refresh token. Revoke app access in Google Account settings "
            "and connect again with prompt=consent."
        )
    expires_in = int(data.get("expires_in") or 0)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in) if expires_in else None
    return {
        "refresh_token": refresh,
        "access_token": data.get("access_token"),
        "token_expires_at": expires_at,
        "scopes": scope_label,
    }


def _exchange_linkedin_code(
    code: str, *, redirect_uri: str, organic: bool = False
) -> dict[str, Any]:
    aliases = linkedin_auth._ORGANIC_ENV_ALIASES if organic else linkedin_auth._ENV_ALIASES
    scopes = LINKEDIN_ORGANIC_SCOPES if organic else LINKEDIN_SCOPES
    client_id = linkedin_auth._get_required_env(*aliases["client_id"])
    client_secret = linkedin_auth._get_required_env(*aliases["client_secret"])
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            LINKEDIN_TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"LinkedIn token exchange failed ({response.status_code}): {response.text[:500]}")
    data = response.json()
    refresh = (data.get("refresh_token") or "").strip()
    if not refresh:
        raise RuntimeError("LinkedIn did not return a refresh token.")
    expires_in = int(data.get("expires_in") or 0)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in) if expires_in else None
    return {
        "refresh_token": refresh,
        "access_token": data.get("access_token"),
        "token_expires_at": expires_at,
        "scopes": scopes,
    }


def _exchange_meta_code(code: str, *, redirect_uri: str) -> dict[str, Any]:
    app_id = meta_auth._get_required_env(*meta_auth._ENV_ALIASES["app_id"])
    app_secret = meta_auth._get_required_env(*meta_auth._ENV_ALIASES["app_secret"])
    api_version = meta_auth._get_env(*meta_auth._ENV_ALIASES["api_version"]) or "v21.0"
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "client_secret": app_secret,
        "code": code,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.get(f"https://graph.facebook.com/{api_version}/oauth/access_token", params=params)
    if response.status_code >= 400:
        raise RuntimeError(f"Meta token exchange failed ({response.status_code}): {response.text[:500]}")
    data = response.json()
    short_token = (data.get("access_token") or "").strip()
    if not short_token:
        raise RuntimeError("Meta did not return an access token.")
    long_params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }
    with httpx.Client(timeout=60.0) as client:
        long_resp = client.get(
            f"https://graph.facebook.com/{api_version}/oauth/access_token",
            params=long_params,
        )
    if long_resp.status_code >= 400:
        raise RuntimeError(
            f"Meta long-lived token exchange failed ({long_resp.status_code}): {long_resp.text[:500]}"
        )
    long_data = long_resp.json()
    access = (long_data.get("access_token") or short_token).strip()
    expires_in = int(long_data.get("expires_in") or data.get("expires_in") or 0)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in) if expires_in else None
    return {
        "access_token": access,
        "token_expires_at": expires_at,
        "scopes": META_SCOPES,
    }
