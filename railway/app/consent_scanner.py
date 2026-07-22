"""Automated browser scanner for the Consent & Tracking Health feature.

Drives a fresh, isolated Chromium session (via Playwright's synchronous API, so
it runs cleanly inside a background worker thread) and visits each configured
page in three independent consent states:

    1. **pre_consent**  — load the page, touch nothing.
    2. **reject_all**   — load, click the CMP's "Reject All", let it settle.
    3. **accept_all**   — load, click the CMP's "Accept All", let it settle.

Each phase uses a brand-new browser context (empty cookie jar and storage) so the
three states never contaminate each other. For every phase we capture the raw
observations the classifier needs: scripts downloaded, network requests
transmitted (with method / resource type / a body snippet), cookies, localStorage
/ sessionStorage, and consent signals (TCF, GPP, Google Consent Mode defaults,
and whether a banner was actually shown).

This module performs **no** classification or judgement — it only records what the
browser did. It degrades gracefully: if Playwright or a browser binary is
unavailable, :func:`scan_pages` returns a result with ``available=False`` and a
clear message instead of raising, so a scan is recorded as failed rather than
crashing the request.

Environment knobs (all optional):
    CONSENT_SCANNER_CHROMIUM_PATH  explicit Chromium executable path.
    CONSENT_SCANNER_PROXY          proxy server for the browser (e.g. an egress proxy).
    CONSENT_SCANNER_NO_SANDBOX     "1" to pass --no-sandbox (needed as root/in containers).
    CONSENT_SCANNER_IGNORE_HTTPS   "1" to ignore TLS errors (e.g. behind a re-terminating proxy).
    CONSENT_SCANNER_SETTLE_MS      ms to wait for late beacons after load (default 3500).
    CONSENT_SCANNER_TIMEOUT_MS     per-navigation timeout (default 30000).
"""

from __future__ import annotations

import glob
import logging
import os
from typing import Any

_log = logging.getLogger(__name__)

# Default CMP interaction targets. Covers the common consent platforms plus a
# generic text fallback. Clients can extend/override these via scan config.
DEFAULT_REJECT_SELECTORS: tuple[str, ...] = (
    "#onetrust-reject-all-handler",
    ".ot-pc-refuse-all-handler",
    "#CybotCookiebotDialogBodyButtonDecline",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll",
    "[data-cky-tag='reject-button']",
    ".cky-btn-reject",
    ".cmplz-deny",
    "#tarteaucitronAllDenied2",
    "[data-consent-action='reject']",
    "#consent-reject-all",
    "button#reject-all",
    ".consent-reject-all",
)
DEFAULT_ACCEPT_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "[data-cky-tag='accept-button']",
    ".cky-btn-accept",
    ".cmplz-accept",
    "#tarteaucitronPersonalize2",
    "[data-consent-action='accept']",
    "#consent-accept-all",
    "button#accept-all",
    ".consent-accept-all",
)
REJECT_TEXTS: tuple[str, ...] = ("reject all", "reject", "decline all", "decline", "refuse", "deny", "necessary only")
ACCEPT_TEXTS: tuple[str, ...] = ("accept all", "accept", "allow all", "agree", "i agree", "got it", "allow cookies")

# JS run in-page to read consent signals + storage without any network.
_SIGNAL_JS = r"""
() => {
  const out = {tcf_present:false, gpp_present:false, uspapi_present:false,
               datalayer_consent:[], gtag_consent_default:null, banner_detected:false,
               banner_text:''};
  const vis = (el) => {
    if (!el) return false;
    try {
      const st = getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity||'1') === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch(e){ return false; }
  };
  try { out.tcf_present = typeof window.__tcfapi === 'function'; } catch(e){}
  try { out.gpp_present = typeof window.__gpp === 'function'; } catch(e){}
  try { out.uspapi_present = typeof window.__uspapi === 'function'; } catch(e){}
  try {
    const dl = window.dataLayer || [];
    for (const item of dl) {
      try {
        const s = JSON.stringify(item);
        if (s && /consent/i.test(s)) out.datalayer_consent.push(s.slice(0, 300));
      } catch(e){}
    }
  } catch(e){}
  try {
    if (window.google_tag_data && window.google_tag_data.ics) {
      out.gtag_consent_default = JSON.stringify(window.google_tag_data.ics.entries || {}).slice(0,500);
    }
  } catch(e){}
  // ---- Banner detection ----------------------------------------------------
  // Many CMPs (CookieYes injected via GTM, Usercentrics, some OneTrust setups)
  // render the banner inside an open shadow root or a same-origin iframe, which a
  // plain document.querySelector never sees. Collect every reachable document /
  // shadow root first, then run detection across all of them so a shadow-DOM or
  // iframed banner is not silently reported as "no banner".
  const roots = [];
  (function collectRoots(root, depth) {
    if (!root || depth > 8) return;
    roots.push(root);
    let all;
    try { all = root.querySelectorAll('*'); } catch(e){ return; }
    for (const node of all) {
      if (node.shadowRoot) collectRoots(node.shadowRoot, depth + 1);
      // Descend into same-origin iframes only; cross-origin access throws and is
      // simply skipped.
      if (node.tagName === 'IFRAME') {
        try {
          const doc = node.contentDocument;
          if (doc) collectRoots(doc, depth + 1);
        } catch(e){}
      }
    }
  })(document, 0);

  const queryAll = (sel) => {
    const hits = [];
    for (const root of roots) {
      try { for (const el of root.querySelectorAll(sel)) hits.push(el); } catch(e){}
    }
    return hits;
  };

  // Known CMP containers (including CookieYes' several banner class names).
  const known = ['#onetrust-banner-sdk','#CybotCookiebotDialog','#usercentrics-root',
                 '#usercentrics-cmp-ui','.cky-consent-container','.cky-consent-bar',
                 '.cky-modal','#cookieyes','#cookie-banner','[aria-label*="cookie" i]',
                 '.cc-window','#cookie-consent','#consent-banner','[id*="cookieyes" i]'];
  for (const sel of known) {
    for (const el of queryAll(sel)) {
      if (vis(el)) { out.banner_detected = true;
        out.banner_text = (el.innerText||'').slice(0,200); break; }
    }
    if (out.banner_detected) break;
  }
  if (!out.banner_detected) {
    try {
      const nodes = queryAll('div,section,aside,dialog');
      for (const el of nodes) {
        if (!vis(el)) continue;
        const t = (el.innerText||'');
        // Raise the length ceiling from 500 to 1500: real consent notices are often
        // long. Still bounded so we don't match the whole page body.
        if (t.length < 1500 && /cookie|consent|privacy/i.test(t)
            && el.querySelector("button,a[role='button'],[data-cky-tag],input[type='button']")) {
          out.banner_detected = true; out.banner_text = t.slice(0,200); break;
        }
      }
    } catch(e){}
  }
  return out;
}
"""

_STORAGE_JS = r"""
() => {
  const grab = (store, type) => {
    const items = [];
    try {
      for (let i=0; i<store.length; i++) {
        const k = store.key(i); let v='';
        try { v = store.getItem(k) || ''; } catch(e){}
        items.push({type, key:k, value_snippet: v.slice(0,80), value_len: v.length});
      }
    } catch(e){}
    return items;
  };
  return [].concat(grab(window.localStorage,'local'), grab(window.sessionStorage,'session'));
}
"""


def validate_scan_url(url: str) -> tuple[bool, str]:
    """Validate & normalise a user-entered scan URL. Returns (ok, normalized_or_error).

    Requires an http(s) URL with a hostname. Blocks obvious private / loopback /
    metadata hosts as a basic SSRF guard, since the scanner drives a real browser
    to whatever URL is configured. Set CONSENT_ALLOW_PRIVATE_HOSTS=1 to permit
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

    if (os.getenv("CONSENT_ALLOW_PRIVATE_HOSTS") or "").strip() not in ("1", "true", "yes"):
        low = host.lower()
        if low == "localhost" or low.endswith(".local") or low.endswith(".internal"):
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
    explicit = (os.getenv("CONSENT_SCANNER_CHROMIUM_PATH") or "").strip()
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
    no_sandbox_env = (os.getenv("CONSENT_SCANNER_NO_SANDBOX") or "").strip() in ("1", "true", "yes")
    if no_sandbox_env or _running_as_root():
        args.append("--no-sandbox")
    kwargs: dict[str, Any] = {"headless": True, "args": args}
    exe = _chromium_executable()
    if exe:
        kwargs["executable_path"] = exe
    proxy = (os.getenv("CONSENT_SCANNER_PROXY") or "").strip()
    if proxy:
        kwargs["proxy"] = {"server": proxy}
    return kwargs


def _auto_install_enabled() -> bool:
    # On by default: if the build never installed the browser, self-heal on the
    # first scan rather than failing forever. Set CONSENT_SCANNER_AUTO_INSTALL=0
    # to disable (e.g. air-gapped hosts).
    return (os.getenv("CONSENT_SCANNER_AUTO_INSTALL") or "1").strip().lower() not in ("0", "false", "no")


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
    _log.info("consent scanner: no Chromium found; installing to %s", target)
    # Best-effort OS libraries first (needs root/apt); ignore failures — the
    # browser download below is what unblocks the "executable missing" error.
    if _running_as_root():
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"],
                           env=env, timeout=240, capture_output=True)
        except Exception as exc:
            _log.warning("consent scanner: install-deps failed (continuing): %s", exc)
    try:
        proc = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                              env=env, timeout=420, capture_output=True)
        if proc.returncode != 0:
            _log.warning("consent scanner: playwright install exited %s: %s",
                         proc.returncode, (proc.stderr or b"")[-400:].decode("utf-8", "replace"))
    except Exception as exc:
        _log.warning("consent scanner: playwright install failed: %s", exc)
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


def _settle_ms() -> int:
    try:
        return max(500, int(os.getenv("CONSENT_SCANNER_SETTLE_MS") or 3500))
    except ValueError:
        return 3500


def _timeout_ms() -> int:
    try:
        return max(5000, int(os.getenv("CONSENT_SCANNER_TIMEOUT_MS") or 30000))
    except ValueError:
        return 30000


def _ignore_https() -> bool:
    return (os.getenv("CONSENT_SCANNER_IGNORE_HTTPS") or "").strip() in ("1", "true", "yes")


def _try_click(page, selectors: list[str], texts: tuple[str, ...]) -> dict[str, Any]:
    """Attempt to click a consent button. Returns {clicked, method, target}."""
    # 1. Known CSS selectors.
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=800):
                loc.click(timeout=1500)
                return {"clicked": True, "method": "selector", "target": sel}
        except Exception:
            continue
    # 2. Visible button/link by accessible text.
    for text in texts:
        try:
            loc = page.get_by_role("button", name=_ci(text)).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                loc.click(timeout=1500)
                return {"clicked": True, "method": "role_text", "target": text}
        except Exception:
            pass
        try:
            loc = page.locator(f"text=/{text}/i").first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                loc.click(timeout=1500)
                return {"clicked": True, "method": "text", "target": text}
        except Exception:
            continue
    return {"clicked": False, "method": "", "target": ""}


def _ci(text: str):
    import re
    return re.compile(re.escape(text), re.I)


def _capture_phase(browser, url: str, phase: str, *, config: dict[str, Any]) -> dict[str, Any]:
    """Load `url` in a fresh context, perform the phase's consent action, capture."""
    timeout = _timeout_ms()
    settle = _settle_ms()
    requests: list[dict[str, Any]] = []
    errors: list[str] = []
    interaction: dict[str, Any] = {"clicked": False}

    context = browser.new_context(
        ignore_https_errors=_ignore_https(),
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SagefrogConsentBot/1.0"),
        viewport={"width": 1366, "height": 900},
    )

    def _on_request(req):
        try:
            post = None
            try:
                post = req.post_data
            except Exception:
                post = None
            requests.append({
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "post_data_snippet": (post[:400] if isinstance(post, str) else None),
            })
        except Exception:
            pass

    context.on("request", _on_request)
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as exc:
        errors.append(f"navigation: {str(exc)[:200]}")

    # Give tags a moment to boot and the banner to render.
    try:
        page.wait_for_timeout(min(settle, 2500))
    except Exception:
        pass

    if phase == "reject_all":
        interaction = _try_click(
            page,
            list(config.get("reject_selectors") or DEFAULT_REJECT_SELECTORS),
            REJECT_TEXTS,
        )
    elif phase == "accept_all":
        interaction = _try_click(
            page,
            list(config.get("accept_selectors") or DEFAULT_ACCEPT_SELECTORS),
            ACCEPT_TEXTS,
        )

    # After the consent action, wait again for consent-gated tags to fire.
    try:
        page.wait_for_timeout(settle)
    except Exception:
        pass

    # ---- Capture state ----
    signals: dict[str, Any] = {}
    storage: list[dict[str, Any]] = []
    cookies: list[dict[str, Any]] = []
    final_url = url
    try:
        final_url = page.url
    except Exception:
        pass
    try:
        signals = page.evaluate(_SIGNAL_JS) or {}
    except Exception as exc:
        errors.append(f"signals: {str(exc)[:120]}")
    try:
        storage = page.evaluate(_STORAGE_JS) or []
    except Exception as exc:
        errors.append(f"storage: {str(exc)[:120]}")
    try:
        cookies = context.cookies() or []
    except Exception as exc:
        errors.append(f"cookies: {str(exc)[:120]}")

    scripts = [r for r in requests if r.get("resource_type") == "script"]

    try:
        context.close()
    except Exception:
        pass

    return {
        "url": url,
        "final_url": final_url,
        "phase": phase,
        "requests": requests,
        "scripts": scripts,
        "cookies": cookies,
        "storage": storage,
        "consent_signals": signals,
        "banner_detected": bool(signals.get("banner_detected")),
        "interaction": interaction,
        "errors": errors,
        "request_count": len(requests),
        "script_count": len(scripts),
        "cookie_count": len(cookies),
    }


def scan_pages(urls: list[str], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Scan every URL across all three consent phases.

    Returns a dict:
        {
          "available": bool,          # False if no browser could run
          "error": str | None,
          "engine": "chromium",
          "pages": [ { "url": ..., "phases": {phase: capture, ...} }, ... ]
        }
    Never raises for per-page/per-phase failures — those are recorded inline.
    """
    cfg = dict(config or {})
    clean_urls = [u.strip() for u in urls if u and u.strip()]
    if not clean_urls:
        return {"available": True, "error": "No pages configured to scan.", "engine": "chromium", "pages": []}

    if not playwright_available():
        return {
            "available": False,
            "error": ("The automated browser (Playwright) is not installed in this "
                      "environment. Add `playwright` to requirements and install a "
                      "Chromium build to enable live scanning."),
            "engine": "chromium",
            "pages": [],
        }

    from playwright.sync_api import sync_playwright

    # Resolve (and, if missing, install) a Chromium the build may not have placed
    # where Playwright looks by default. When found we pass its path explicitly so
    # we never depend on Playwright's default per-revision resolution.
    resolved_exe = ensure_chromium_installed()

    phases = ("pre_consent", "reject_all", "accept_all")
    results: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            launch_kwargs = _launch_kwargs()
            if resolved_exe and "executable_path" not in launch_kwargs:
                launch_kwargs["executable_path"] = resolved_exe
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as exc:
                hint = ""
                if "executable doesn't exist" in str(exc).lower():
                    hint = (" The Chromium binary is missing — the deploy build did not run "
                            "`playwright install chromium`. See docs/CONSENT_TRACKING_HEALTH.md.")
                elif "error while loading shared libraries" in str(exc).lower() or ".so" in str(exc):
                    hint = (" Chromium's OS libraries are missing — run "
                            "`playwright install-deps chromium` in the build.")
                return {
                    "available": False,
                    "error": f"Could not launch Chromium: {str(exc)[:220]}{hint}",
                    "engine": "chromium",
                    "pages": [],
                }
            try:
                for url in clean_urls:
                    page_result: dict[str, Any] = {"url": url, "phases": {}}
                    for phase in phases:
                        try:
                            page_result["phases"][phase] = _capture_phase(
                                browser, url, phase, config=cfg
                            )
                        except Exception as exc:
                            _log.warning("consent scan phase failed [%s/%s]: %s", url, phase, exc)
                            page_result["phases"][phase] = {
                                "url": url, "phase": phase, "requests": [], "scripts": [],
                                "cookies": [], "storage": [], "consent_signals": {},
                                "banner_detected": False, "interaction": {"clicked": False},
                                "errors": [f"phase failed: {str(exc)[:200]}"],
                            }
                    results.append(page_result)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as exc:
        return {
            "available": False,
            "error": f"Scanner failed to start: {str(exc)[:240]}",
            "engine": "chromium",
            "pages": results,
        }

    return {"available": True, "error": None, "engine": "chromium", "pages": results}
