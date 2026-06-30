"""OAuth authorize URLs, code exchange, and session state helpers."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

import auth as google_auth
import linkedin_auth
import meta_auth

PLATFORMS = frozenset({"google_ads", "linkedin", "meta", "gsc", "google_analytics", "google_tag_manager", "hubspot"})

HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_SCOPES = "oauth crm.objects.contacts.read"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GTM_SCOPE = "https://www.googleapis.com/auth/tagmanager.readonly"

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_SCOPES = "r_ads r_ads_reporting r_organization_social"

META_SCOPES = "ads_read,business_management"


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
    return f"{public_base_url()}/oauth/{slug}/callback"


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
    s = (os.getenv("AUTH_SESSION_SECRET") or os.getenv("CRON_SECRET") or "").strip()
    if not s:
        raise RuntimeError("AUTH_SESSION_SECRET or CRON_SECRET required for connect links.")
    return s.encode()


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
    if slug == "hubspot":
        return _exchange_hubspot_code(code, redirect_uri=redirect_uri)
    return _exchange_meta_code(code, redirect_uri=redirect_uri)


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
    return {
        "refresh_token": refresh,
        "access_token": data.get("access_token"),
        "token_expires_at": expires_at,
        "scopes": HUBSPOT_SCOPES,
    }


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


def _exchange_linkedin_code(code: str, *, redirect_uri: str) -> dict[str, Any]:
    client_id = linkedin_auth._get_required_env(*linkedin_auth._ENV_ALIASES["client_id"])
    client_secret = linkedin_auth._get_required_env(*linkedin_auth._ENV_ALIASES["client_secret"])
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
        "scopes": LINKEDIN_SCOPES,
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
