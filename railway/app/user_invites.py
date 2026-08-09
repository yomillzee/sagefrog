"""Single-use invite links ("magic links") for onboarding new users.

An admin creates a user without ever choosing a password. Instead the platform
mints an unguessable token and shows the admin a one-off URL —
``/invite/<token>`` — to pass along however they like (email, Slack, a call).
The invitee opens it, picks their own password, and lands signed in. From then
on they use the ordinary password login; nothing else in the auth model changes.

Security properties:

* **Only a hash is stored.** The raw token exists in exactly one place — the
  response shown to the admin who minted it. A database dump (or an admin with
  read access to the table) cannot reconstruct a working link, and the token
  can't leak later out of the row. The cost is that a lost link can't be
  re-displayed; the admin mints a fresh one instead, which revokes the old.
* **Single use.** Accepting stamps ``accepted_at`` in the same transaction that
  sets the password, so a replayed link is dead even if it is still in someone's
  browser history.
* **Expiring.** Links die after ``AUTH_INVITE_TTL_HOURS`` (default 72h).
* **Revocable.** Minting a new invite for a user revokes that user's outstanding
  ones, so only the most recent link ever works.
* **Bound to a live account.** Resolution joins ``web_users`` and requires the
  account still be active — deactivating a user kills their pending invite too.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import db
import db_migrate
import web_users

log = logging.getLogger(__name__)

# 32 bytes → 43 url-safe chars. Long enough that online guessing is hopeless
# even without the rate limiter in front of it.
_TOKEN_BYTES = 32

# Default lifetime of an invite link. Long enough to survive a weekend, short
# enough that a link forgotten in an inbox stops working.
_DEFAULT_TTL_HOURS = 72

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS user_invites (
      id BIGSERIAL PRIMARY KEY,
      token_hash TEXT NOT NULL UNIQUE,
      user_id BIGINT NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
      email TEXT NOT NULL,
      created_by TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      expires_at TIMESTAMPTZ NOT NULL,
      accepted_at TIMESTAMPTZ,
      revoked_at TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS user_invites_user_idx ON user_invites (user_id)
    """,
    # Partial index over the only rows the redeem path ever looks at.
    """
    CREATE INDEX IF NOT EXISTS user_invites_pending_idx ON user_invites (user_id)
      WHERE accepted_at IS NULL AND revoked_at IS NULL
    """,
]

# NOTE ON THE MIGRATION ID: the runner applies migrations in sorted-id order,
# and this table carries a foreign key to web_users(id) — so it must be applied
# *after* web_users:0001_baseline creates that table. A "user_invites:..." id
# would sort before "web_users:..." ('u' < 'w') and fail outright on a fresh
# database. Naming it in the web_users namespace both fixes the ordering and
# states the dependency: this is an extension of the users schema that happens
# to live in its own module.
db_migrate.register(
    [
        db_migrate.Migration(
            id="web_users:0002_user_invites", statements=tuple(SCHEMA_SQL_STATEMENTS)
        )
    ]
)


@dataclass(frozen=True)
class Invite:
    """A resolved, still-valid invite and the account it unlocks."""

    id: int
    user_id: int
    email: str
    expires_at: datetime


def enabled() -> bool:
    return web_users.enabled()


def ttl_hours() -> int:
    raw = (os.getenv("AUTH_INVITE_TTL_HOURS") or "").strip()
    if not raw:
        return _DEFAULT_TTL_HOURS
    try:
        # Clamp to a sane band: at least an hour to be usable, at most 30 days
        # so a misconfigured value can't mint an effectively permanent link.
        return max(1, min(720, int(raw)))
    except ValueError:
        return _DEFAULT_TTL_HOURS


def hash_token(token: str) -> str:
    """SHA-256 of the raw token. High-entropy input, so a plain hash is right
    here — there is nothing for a slow KDF to protect against."""
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def invite_path(token: str) -> str:
    return f"/invite/{token}"


def invite_url(token: str, *, base_url: str = "") -> str:
    """Absolute invite URL when a base is known, else the bare path."""
    base = (base_url or "").rstrip("/")
    return f"{base}{invite_path(token)}" if base else invite_path(token)


def mint_invite(*, user_id: int, email: str, created_by: str = "") -> tuple[str, datetime]:
    """Create an invite for ``user_id`` and return ``(raw_token, expires_at)``.

    Revokes that user's outstanding invites first, so exactly one link is live
    at a time and re-inviting cleanly invalidates a link that went astray. The
    raw token is returned once and never stored — only its hash is persisted.
    """
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to create invite links.")
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(tz=UTC) + timedelta(hours=ttl_hours())
    creator = (created_by or "").strip() or None
    with db.connection() as conn:
        conn.execute(
            "UPDATE user_invites SET revoked_at = NOW() "
            "WHERE user_id = %s AND accepted_at IS NULL AND revoked_at IS NULL",
            (int(user_id),),
        )
        conn.execute(
            "INSERT INTO user_invites (token_hash, user_id, email, created_by, expires_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (hash_token(token), int(user_id), (email or "").strip().lower(), creator, expires_at),
        )
    return token, expires_at


def resolve_invite(token: str) -> Invite | None:
    """Return the invite for a valid token, else None.

    Valid means: the token matches, and the invite is unaccepted, unrevoked,
    unexpired, and belongs to an account that is still active. Callers get a
    single generic failure — never a reason — so a probe can't distinguish
    "wrong token" from "already used".
    """
    tok = (token or "").strip()
    if not tok or not enabled():
        return None
    try:
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT i.id, i.user_id, i.email, i.expires_at
                FROM user_invites i
                JOIN web_users u ON u.id = i.user_id
                WHERE i.token_hash = %s
                  AND i.accepted_at IS NULL
                  AND i.revoked_at IS NULL
                  AND i.expires_at > NOW()
                  AND u.is_active = TRUE
                """,
                (hash_token(tok),),
            ).fetchone()
    except Exception as exc:
        log.warning("Invite resolve failed: %s", exc)
        return None
    if not row:
        return None
    return Invite(id=int(row[0]), user_id=int(row[1]), email=str(row[2]), expires_at=row[3])


def accept_invite(token: str, new_password: str) -> Invite | None:
    """Set the invitee's password and burn the token. Returns the accepted
    invite, or None if the token was not (or is no longer) valid.

    The claim and the password write share one transaction and the claim goes
    first, guarded by the same ``accepted_at IS NULL`` predicate that
    ``resolve_invite`` checks. Two concurrent submissions of the same link
    therefore cannot both set a password: the second UPDATE matches no row and
    the whole thing rolls back.
    """
    tok = (token or "").strip()
    if not tok or not enabled():
        return None
    if len(new_password or "") < 10:
        raise ValueError("Password must be at least 10 characters.")
    pw_hash = web_users.hash_password(new_password)
    with db.connection() as conn:
        row = conn.execute(
            """
            UPDATE user_invites i
            SET accepted_at = NOW()
            FROM web_users u
            WHERE u.id = i.user_id
              AND i.token_hash = %s
              AND i.accepted_at IS NULL
              AND i.revoked_at IS NULL
              AND i.expires_at > NOW()
              AND u.is_active = TRUE
            RETURNING i.id, i.user_id, i.email, i.expires_at
            """,
            (hash_token(tok),),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE web_users SET password_hash = %s, updated_at = NOW() "
            "WHERE id = %s AND is_active = TRUE",
            (pw_hash, int(row[1])),
        )
    return Invite(id=int(row[0]), user_id=int(row[1]), email=str(row[2]), expires_at=row[3])


def revoke_for_user(user_id: int) -> int:
    """Revoke every outstanding invite for a user. Returns how many died."""
    if not enabled():
        return 0
    with db.connection() as conn:
        cur = conn.execute(
            "UPDATE user_invites SET revoked_at = NOW() "
            "WHERE user_id = %s AND accepted_at IS NULL AND revoked_at IS NULL",
            (int(user_id),),
        )
        return int(cur.rowcount or 0)


def pending_by_user() -> dict[int, dict[str, Any]]:
    """Outstanding (unaccepted, unrevoked, unexpired) invites keyed by user id.

    Used by the admin roster to badge who still hasn't set a password.
    Best-effort: a failure here degrades the badge, never the page.
    """
    if not enabled():
        return {}
    try:
        with db.connection() as conn:
            rows = conn.execute(
                """
                SELECT user_id, MAX(expires_at)
                FROM user_invites
                WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > NOW()
                GROUP BY user_id
                """
            ).fetchall()
    except Exception as exc:
        log.warning("Pending-invite lookup failed: %s", exc)
        return {}
    return {
        int(uid): {"expires_at": exp.isoformat() if isinstance(exp, datetime) else None}
        for uid, exp in rows
    }
