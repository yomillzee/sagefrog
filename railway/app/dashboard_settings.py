"""Admin OAuth platform-connection section (rendered on the Admin page)."""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

import dashboard_service
import oauth_flows
import oauth_store


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else ""), quote=True)


def _status_badge(ok: bool, *, ok_label: str = "Connected", fail_label: str = "Not connected") -> str:
    cls = "badge ok" if ok else "badge err"
    label = ok_label if ok else fail_label
    return f'<span class="{cls}">{_esc(label)}</span>'


def _favicon_head_html() -> str:
    return dashboard_service._favicon_head_html()



def _oauth_platform_card_html(
    *,
    platform: str,
    label: str,
    return_url: str,
    can_manage_oauth: bool = True,
    pub: oauth_store.OAuthCredentialPublic | None = None,
) -> str:
    prereq = oauth_flows.connect_prerequisites(platform)
    pub = pub or oauth_store.public_status(platform)
    return_to = quote(return_url, safe="")
    status_badge = _status_badge(pub.connected, ok_label="Connected", fail_label="Not connected")

    actions = ""
    if can_manage_oauth and oauth_store.enabled():
        connect_url = f"/oauth/{platform}/connect?return_to={return_to}"
        if pub.connected:
            actions += f'<a class="btn secondary btn-sm" href="{connect_url}">Reconnect</a>'
        elif prereq.get("ready"):
            actions += f'<a class="btn primary btn-sm" href="{connect_url}">Connect</a>'
        else:
            missing = ", ".join(prereq.get("missing") or [])
            actions += f'<p class="muted">Set {_esc(missing)} in Railway before connecting.</p>'
        if pub.source == "database":
            actions += f"""
            <form method="post" action="/oauth/{platform}/disconnect" class="inline-form"
              onsubmit="return confirm('Disconnect {label}? The stored token will be removed.');">
              <input type="hidden" name="return_to" value="{_esc(return_url)}">
              <button type="submit" class="btn secondary btn-sm">Disconnect</button>
            </form>
            """
    elif can_manage_oauth and not oauth_store.enabled():
        actions = '<p class="muted">Attach Postgres (DATABASE_URL) to store OAuth tokens.</p>'

    note = _esc(prereq.get("note") or "")
    return f"""
    <div class="oauth-card">
      <div class="oauth-card-head">
        <h3>{_esc(label)}</h3>
        {status_badge}
      </div>
      {f'<p class="muted">{note}</p>' if note else ''}
      <div class="oauth-actions">{actions}</div>
    </div>
    """


def render_admin_oauth_section(
    *,
    return_url: str = "/admin",
    oauth_connected: str | None = None,
    oauth_error: str | None = None,
) -> str:
    notice = ""
    if oauth_connected:
        labels = {
            "google_ads": "Google Ads",
            "linkedin": "LinkedIn",
            "meta": "Meta",
            "gsc": "Google Search Console",
        }
        notice += (
            f'<div class="notice ok">{_esc(labels.get(oauth_connected, oauth_connected))} '
            f"connected successfully.</div>"
        )
    if oauth_error:
        notice += f'<div class="notice err">{_esc(oauth_error)}</div>'

    oauth_status = oauth_store.all_public_status()
    cards = []
    for platform, label in (
        ("google_ads", "Google Ads"),
        ("linkedin", "LinkedIn"),
        ("meta", "Meta"),
        ("gsc", "Google Search Console"),
    ):
        cards.append(
            _oauth_platform_card_html(
                platform=platform,
                label=label,
                return_url=return_url,
                can_manage_oauth=True,
                pub=oauth_status[platform],
            )
        )

    return f"""
    {notice}
    <section class="admin-oauth-section">
      <h2>Platform connections</h2>
      <div class="oauth-grid">{"".join(cards)}</div>
    </section>"""


