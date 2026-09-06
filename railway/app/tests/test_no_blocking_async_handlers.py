from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# FastAPI runs an `async def` endpoint directly on the event loop and a plain
# `def` one in a threadpool. This app's work is synchronous throughout —
# psycopg, the vendor SDKs, the OAuth token exchange — so an `async def` handler
# that never awaits anything holds the loop for its entire body and stalls every
# other in-flight request. `oauth_callback` was the worst of them: an HTTP
# round-trip to the OAuth provider, an account verification call, then database
# writes, all without yielding once.
#
# "Never awaits" is the tell, and it is a reliable one: a coroutine that awaits
# nothing had no reason to be a coroutine. Declaring it `def` costs a threadpool
# hop and buys back the loop.
#
# The exceptions are functions that do no I/O at all. For those the hop is pure
# overhead and staying on the loop is correct, so they are listed here by name
# rather than left to look like oversights.

_INTENTIONALLY_ASYNC = {
    # An env read and a constant-time compare. No I/O.
    ("security.py", "require_api_key"),
    ("cron_security.py", "require_cron_secret"),
    # Builds an error page from strings; not_found_page imports nothing but `html`.
    ("main.py", "custom_http_exception_handler"),
}


def _iter_source_files():
    for path in sorted(APP_DIR.rglob("*.py")):
        parts = path.relative_to(APP_DIR).parts
        if parts[0] in {"tests", "scripts"}:
            continue
        yield path


def _async_defs_without_await(path: Path) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        awaits_something = any(
            isinstance(child, (ast.Await, ast.AsyncFor, ast.AsyncWith))
            for child in ast.walk(node)
        )
        if not awaits_something:
            found.append((node.name, node.lineno))
    return found


class NoBlockingAsyncHandlersTest(unittest.TestCase):
    def test_no_async_function_forgets_to_await(self):
        offenders = []
        for path in _iter_source_files():
            rel = str(path.relative_to(APP_DIR))
            for name, lineno in _async_defs_without_await(path):
                if (rel, name) in _INTENTIONALLY_ASYNC:
                    continue
                offenders.append(f"{rel}:{lineno} {name}")
        self.assertEqual(
            offenders,
            [],
            "These are `async def` but never await, so they occupy the event "
            "loop for their whole body while doing blocking work. Declare them "
            "`def` — FastAPI will run them in a threadpool — or, if they "
            "genuinely do no I/O, add them to _INTENTIONALLY_ASYNC with the "
            "reason:\n  " + "\n  ".join(offenders),
        )

    def test_the_allowlist_has_not_gone_stale(self):
        """Every name listed above should still exist and still be async, or the
        list is quietly excusing something that no longer looks like that."""
        for rel, name in sorted(_INTENTIONALLY_ASYNC):
            path = APP_DIR / rel
            self.assertTrue(path.exists(), f"{rel} is listed but no longer exists")
            names = {n for n, _ in _async_defs_without_await(path)}
            self.assertIn(
                name,
                names,
                f"{rel}:{name} is on the intentionally-async list but is no "
                "longer an async function without awaits — drop it from the list.",
            )

    def test_the_auth_dependency_chain_is_sync(self):
        """These run on every authenticated request and do a database read to
        resolve the signed-in account. As coroutines they blocked the loop on
        each one."""
        import inspect

        import web_auth

        for name in ("require_user", "require_admin", "require_super_admin"):
            fn = getattr(web_auth, name)
            self.assertFalse(
                inspect.iscoroutinefunction(fn),
                f"web_auth.{name} must stay sync so FastAPI runs its database "
                "lookup in a threadpool rather than on the event loop.",
            )


if __name__ == "__main__":
    unittest.main()
