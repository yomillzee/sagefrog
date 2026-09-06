"""Shared Chromium plumbing for the browser-driving scanners.

Locating (and, if missing, installing) a Playwright-managed Chromium is fiddly
and deploy-specific, and the SSRF guard on a user-entered scan URL must be
identical everywhere we point a real browser at a customer's site. Both live
here so :mod:`a11y_scanner` — and any future scanner — drive the same browser the
same way instead of re-deriving it.

This module performs no page work of its own; it only resolves a launchable
browser and validates URLs.

Environment knobs (all optional; the ``CONSENT_SCANNER_*`` spellings are still
honoured so an existing deploy's variables keep working):
    BROWSER_SCANNER_CHROMIUM_PATH  explicit Chromium executable path.
    BROWSER_SCANNER_PROXY          proxy server for the browser (e.g. an egress proxy).
    BROWSER_SCANNER_NO_SANDBOX     "1" to pass --no-sandbox (needed as root/in containers).
    BROWSER_SCANNER_IGNORE_HTTPS   "1" to ignore TLS errors (e.g. behind a re-terminating proxy).
    BROWSER_SCANNER_TIMEOUT_MS     per-navigation timeout (default 30000).
    BROWSER_SCANNER_AUTO_INSTALL   "0" to never download a missing browser.
    SCANNER_ALLOW_PRIVATE_HOSTS    "1" to permit private/loopback scan targets.
"""

from __future__ import annotations

import glob
import logging
import os
from typing import Any

_log = logging.getLogger(__name__)

_TRUE = ("1", "true", "yes")


def _env(*names: str) -> str:
    """First non-empty value among ``names``, so legacy spellings still work."""
    for name in names:
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    return ""


def validate_scan_url(url: str) -> tuple[bool, str]:
    """Validate & normalise a user-entered scan URL. Returns (ok, normalized_or_error).

    Requires an http(s) URL with a hostname. Blocks obvious private / loopback /
    metadata hosts as a basic SSRF guard, since the scanner drives a real browser
    to whatever URL is configured. Set SCANNER_ALLOW_PRIVATE_HOSTS=1 to permit
    them (e.g. scanning a staging host, or local test fixtures).
    """
    import ipaddress
    from urllib.parse import urlparse

    raw = (url or "").strip()
    if not raw:
        return False, "Empty URL."
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return False, f"Could not parse URL: {url}"
    if parsed.scheme not in ("http", "https"):
        return False, f"Only http/https URLs are supported: {url}"
    host = (parsed.hostname or "").strip()
    if not host:
        return False, f"URL has no hostname: {url}"

    if _env("SCANNER_ALLOW_PRIVATE_HOSTS", "CONSENT_ALLOW_PRIVATE_HOSTS") not in _TRUE:
        low = host.lower()
        if low == "localhost" or low.endswith((".local", ".internal")):
            return False, f"Internal hosts are not allowed: {host}"
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"Private/loopback addresses are not allowed: {host}"
        except ValueError:
            pass  # hostname, not a literal IP — fine
    return True, raw


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


# Playwright browser layouts we can launch via executable_path, most-preferred
# first. The full Chromium runs fine headless, so we prefer it; the headless
# shell (whose directory/binary name has changed across Playwright versions) is
# a fallback so a shell-only install still resolves.
_CHROMIUM_GLOBS = (
    "chromium-*/chrome-linux/chrome",
    "chromium_headless_shell-*/chrome-linux/headless_shell",
    "chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell",
    "chromium-*/chrome-linux/headless_shell",
)


# Directories the Railway build may install Chromium into. We search these
# unconditionally — the runtime container does not reliably inherit
# PLAYWRIGHT_BROWSERS_PATH from the build, so we must not depend on that env var
# being set at run time to locate a browser the build placed at a known path.
_BROWSER_SEARCH_DIRS = (
    "/opt/pw-browsers",
    os.path.expanduser("~/.cache/ms-playwright"),
    "/root/.cache/ms-playwright",
)


def _chromium_executable() -> str | None:
    explicit = _env("BROWSER_SCANNER_CHROMIUM_PATH", "CONSENT_SCANNER_CHROMIUM_PATH")
    if explicit and os.path.exists(explicit):
        return explicit
    # Auto-detect a Playwright-managed Chromium across the paths our build may use
    # and the default per-user cache, tolerating the several on-disk layouts
    # Playwright has used for the browser binary across versions.
    search_dirs: list[str] = []
    env_base = (os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_base:
        search_dirs.append(env_base)
    search_dirs.extend(_BROWSER_SEARCH_DIRS)
    seen: set[str] = set()
    for directory in search_dirs:
        if not directory or directory in seen or not os.path.isdir(directory):
            continue
        seen.add(directory)
        for pat in _CHROMIUM_GLOBS:
            hits = sorted(glob.glob(os.path.join(directory, pat)))
            if hits:
                return hits[-1]
    return None


def _running_as_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:  # non-POSIX
        return False


def _launch_kwargs() -> dict[str, Any]:
    args: list[str] = ["--disable-dev-shm-usage"]
    # Chromium refuses to start its sandbox as root, which is how the deploy
    # container runs. Pass --no-sandbox when explicitly asked OR when we detect
    # we're root, so a scan isn't silently blocked if the env var didn't
    # propagate to the runtime container.
    no_sandbox_env = _env("BROWSER_SCANNER_NO_SANDBOX", "CONSENT_SCANNER_NO_SANDBOX") in _TRUE
    if no_sandbox_env or _running_as_root():
        args.append("--no-sandbox")
    kwargs: dict[str, Any] = {"headless": True, "args": args}
    exe = _chromium_executable()
    if exe:
        kwargs["executable_path"] = exe
    proxy = _env("BROWSER_SCANNER_PROXY", "CONSENT_SCANNER_PROXY")
    if proxy:
        kwargs["proxy"] = {"server": proxy}
    return kwargs


def _auto_install_enabled() -> bool:
    # On by default: if the build never installed the browser, self-heal on the
    # first scan rather than failing forever. Set BROWSER_SCANNER_AUTO_INSTALL=0
    # to disable (e.g. air-gapped hosts).
    raw = _env("BROWSER_SCANNER_AUTO_INSTALL", "CONSENT_SCANNER_AUTO_INSTALL") or "1"
    return raw.lower() not in ("0", "false", "no")


def _install_target_dir() -> str:
    """A writable directory to install the browser into if one is missing."""
    for candidate in ("/opt/pw-browsers", os.path.expanduser("~/.cache/ms-playwright")):
        parent = candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
        if os.access(parent or "/", os.W_OK):
            return candidate
    return os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")


def ensure_chromium_installed() -> str | None:
    """Return a launchable Chromium path, installing it on demand if missing.

    Downloads the browser (and best-effort its OS libraries, when root) into a
    writable path the scanner searches. Returns the executable path, or None if it
    still couldn't be resolved. Never raises.
    """
    exe = _chromium_executable()
    if exe:
        return exe
    if not _auto_install_enabled():
        return None

    import subprocess
    import sys

    target = _install_target_dir()
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=target)
    _log.info("browser scanner: no Chromium found; installing to %s", target)
    # Best-effort OS libraries first (needs root/apt); ignore failures — the
    # browser download below is what unblocks the "executable missing" error.
    if _running_as_root():
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"],
                           env=env, timeout=240, capture_output=True)
        except Exception as exc:
            _log.warning("browser scanner: install-deps failed (continuing): %s", exc)
    try:
        proc = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                              env=env, timeout=420, capture_output=True)
        if proc.returncode != 0:
            _log.warning("browser scanner: playwright install exited %s: %s",
                         proc.returncode, (proc.stderr or b"")[-400:].decode("utf-8", "replace"))
    except Exception as exc:
        _log.warning("browser scanner: playwright install failed: %s", exc)
        return None
    # Re-resolve, now including the freshly-populated target dir.
    return _chromium_executable() or _find_in_dir(target)


def _find_in_dir(directory: str) -> str | None:
    if not directory or not os.path.isdir(directory):
        return None
    for pat in _CHROMIUM_GLOBS:
        hits = sorted(glob.glob(os.path.join(directory, pat)))
        if hits:
            return hits[-1]
    return None


def _timeout_ms() -> int:
    try:
        return max(5000, int(_env("BROWSER_SCANNER_TIMEOUT_MS", "CONSENT_SCANNER_TIMEOUT_MS") or 30000))
    except ValueError:
        return 30000


def _ignore_https() -> bool:
    return _env("BROWSER_SCANNER_IGNORE_HTTPS", "CONSENT_SCANNER_IGNORE_HTTPS") in _TRUE
