"""Values shared by more than one admin route module.

Small on purpose: this is not a home for anything that has a better one. It
exists so that a constant used by two different admin routes does not have to
live in main.py and be imported back out of it, which would close an import
cycle once those routes moved into this package.
"""

from __future__ import annotations

# Ceiling on a base64 data URI stored as a user avatar or a client logo. Both
# are written straight into Postgres and rendered inline in the page, so the cap
# is about the size of every response that carries one, not just the upload.
AVATAR_MAX_CHARS = 400_000
