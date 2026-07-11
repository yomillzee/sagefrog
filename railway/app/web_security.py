"""Response security headers and CSRF protection for browser (cookie) routes.

Two concerns live here, both driven from ``main.py`` middleware:

* **Security headers** — a small, framework-safe set applied to every
  response (HSTS, nosniff, frame options, referrer policy, a conservative CSP,
  permissions policy). The CSP deliberately avoids ``script-src``/``style-src``
  restrictions because the dashboards render inline scripts and styles; it only
  locks down framing, base URI, plugins, and form targets.

* **CSRF** — a session synchronizer-token pattern. A per-session token is
  seeded into the signed session cookie and echoed back two ways: a hidden
  ``csrf_token`` field injected into every server-rendered ``<form method=post>``
  (for native form submits) and a ``<meta name="csrf-token">`` plus a tiny
  ``fetch`` wrapper that attaches the ``X-CSRF-Token`` header to same-origin
  unsafe requests (for the dashboards' AJAX calls). Validation accepts the
  token from either channel.

CSRF only engages for cookie-authenticated browser requests: header-authed API
callers (Bearer / X-API-Key), the cron secret, and the legacy ``?key=`` path
are exempt, and the whole mechanism is inert when no session middleware is
installed (e.g. local dev without Postgres).
"""

from __future__ import annotations

import hmac
import re
import secrets

from starlette.requests import Request

# ── CSRF constants ──────────────────────────────────────────────────────────
CSRF_SESSION_KEY = "csrf_token"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

# Methods that never mutate state and so never need a CSRF token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Header/query credentials that indicate a non-cookie caller (API key, bearer
# token, cron secret, or the legacy dashboard access key). Such requests are not
# CSRF-prone — the browser does not attach these automatically — so they skip
# the token check to avoid breaking ChatGPT actions, cron, and ?key= links.
_API_CREDENTIAL_HEADERS = ("authorization", "x-api-key", "x-cron-secret")


# ── Security headers ────────────────────────────────────────────────────────
def security_headers(*, https: bool) -> dict[str, str]:
    """Return the security headers to apply to a response.

    ``https`` gates HSTS — it is meaningless (and ignored by browsers) over
    plain HTTP, so we only emit it on secure/production requests.
    """
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # No script-src/style-src here on purpose: the dashboards depend on
        # inline <script>/<style>. We still pin framing, base URI, plugin
        # objects, and form targets, which are safe with inline code.
        "Content-Security-Policy": (
            "base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'self'; form-action 'self'"
        ),
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }
    if https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def apply_security_headers(response, *, https: bool) -> None:
    """Set security headers on a response without clobbering explicit values."""
    for name, value in security_headers(https=https).items():
        response.headers.setdefault(name, value)


# ── CSRF token helpers ──────────────────────────────────────────────────────
def ensure_csrf_token(session: dict) -> str:
    """Return the session's CSRF token, minting and storing one if absent."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _submitted_token(request: Request, form_token: str | None) -> str | None:
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if header_token:
        return header_token
    return form_token


def tokens_match(expected: str | None, submitted: str | None) -> bool:
    if not expected or not submitted:
        return False
    return hmac.compare_digest(str(expected), str(submitted))


def requires_csrf(request: Request) -> bool:
    """True when a request must carry a valid CSRF token to be honored.

    Only cookie-authenticated, state-changing browser requests qualify: unsafe
    method, a non-empty session (so a session cookie is in play), and no
    API-style credential that a cross-site attacker could not forge anyway.
    """
    if request.method in SAFE_METHODS:
        return False
    session = request.scope.get("session")
    if not session:
        return False
    for header in _API_CREDENTIAL_HEADERS:
        if request.headers.get(header):
            return False
    if request.query_params.get("key"):
        return False
    return True


async def validate_csrf(request: Request) -> bool:
    """Validate the CSRF token on ``request``. Assumes ``requires_csrf`` is True.

    Reads the token from the ``X-CSRF-Token`` header (AJAX) or the
    ``csrf_token`` form field (native form submit), and compares it to the
    session token in constant time.
    """
    form_token: str | None = None
    if not request.headers.get(CSRF_HEADER_NAME):
        ctype = (request.headers.get("content-type") or "").lower()
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            # Prime the body cache first so BaseHTTPMiddleware can replay it to
            # the downstream route — reading request.form() alone would consume
            # the stream and starve the handler's Form(...) parameters.
            try:
                await request.body()
                form = await request.form()
            except Exception:
                form = None
            if form is not None:
                value = form.get(CSRF_FIELD_NAME)
                form_token = value if isinstance(value, str) else None
    submitted = _submitted_token(request, form_token)
    expected = request.scope.get("session", {}).get(CSRF_SESSION_KEY)
    return tokens_match(expected, submitted)


# ── HTML injection ──────────────────────────────────────────────────────────
def _hidden_input(token: str) -> str:
    return f'<input type="hidden" name="{CSRF_FIELD_NAME}" value="{token}">'


def _head_snippet(token: str) -> str:
    """Meta tag plus a fetch wrapper that attaches the token to AJAX writes.

    The wrapper is defensive: any failure falls through to the original fetch,
    and it only touches same-origin POST/PUT/PATCH/DELETE requests. Native form
    submits are covered separately by the injected hidden field.
    """
    return (
        f'<meta name="csrf-token" content="{token}">'
        "<script>(function(){"
        "var m=document.querySelector('meta[name=\"csrf-token\"]');"
        "var t=m&&m.getAttribute('content');"
        "if(!t||!window.fetch)return;"
        "var of=window.fetch;"
        "window.fetch=function(input,init){try{"
        "init=init||{};"
        "var src=(typeof input==='object'&&input)||{};"
        "var method=(init.method||src.method||'GET').toUpperCase();"
        "var url=(typeof input==='string')?input:(src.url||'');"
        "var same=(url.charAt(0)==='/'&&url.charAt(1)!=='/')"
        "||url.indexOf(location.origin+'/')===0||url===location.origin;"
        "if(same&&['POST','PUT','PATCH','DELETE'].indexOf(method)>=0){"
        "var h=new Headers(init.headers||src.headers||undefined);"
        "if(!h.has('X-CSRF-Token'))h.set('X-CSRF-Token',t);"
        "init=Object.assign({},init,{headers:h});"
        "}}catch(e){}return of(input,init);};"
        "})();</script>"
    )


# Opening <form ...> tags that submit with method=post (any casing/quoting).
_POST_FORM_RE = re.compile(
    r'<form\b[^>]*\bmethod\s*=\s*["\']?post["\']?[^>]*>',
    re.IGNORECASE,
)
_HEAD_OPEN_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def inject_csrf_html(html: str, token: str) -> str:
    """Add the CSRF meta/script to <head> and a hidden field to each POST form."""
    snippet = _head_snippet(token)
    head_match = _HEAD_OPEN_RE.search(html)
    if head_match:
        idx = head_match.end()
        html = html[:idx] + snippet + html[idx:]
    else:
        body_match = _BODY_OPEN_RE.search(html)
        if body_match:
            idx = body_match.end()
            html = html[:idx] + snippet + html[idx:]
        else:
            html = snippet + html

    hidden = _hidden_input(token)
    return _POST_FORM_RE.sub(lambda m: m.group(0) + hidden, html)
