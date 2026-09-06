from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The app catches broadly on purpose: a dead connector should degrade one panel,
# not blank a client's dashboard. The problem was never the catching — it was
# that 103 of those handlers were a bare `except Exception: pass`, so a failing
# cache write, a lost audit event or a config read that silently returned
# nothing left no trace anywhere. Combined with a root logger that was never
# configured, the app could be failing continuously and look merely slow.
#
# The rule this pins is not "never swallow". It is "never swallow in silence":
# a handler that does nothing must either log, or carry a comment saying why
# silence is the correct behaviour there (a speculative parse with a documented
# fallback, teardown after the results are already collected).


def _iter_source_files():
    for path in sorted(APP_DIR.rglob("*.py")):
        parts = path.relative_to(APP_DIR).parts
        if parts[0] in {"tests", "scripts"}:
            continue
        yield path


def _unexplained_handlers(path: Path) -> list[int]:
    """Line numbers of handlers whose whole body is `pass`, with no comment."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
        return []
    lines = source.splitlines()
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not (len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)):
                continue
            pass_line = handler.body[0].lineno
            between = lines[handler.lineno : pass_line - 1]
            has_comment = any(line.strip().startswith("#") for line in between)
            trailing = "#" in lines[pass_line - 1]
            if not (has_comment or trailing):
                found.append(handler.lineno)
    return found


class NoSilentExceptionHandlersTest(unittest.TestCase):
    def test_every_swallowed_exception_is_logged_or_explained(self):
        offenders = []
        for path in _iter_source_files():
            for lineno in _unexplained_handlers(path):
                offenders.append(f"{path.relative_to(APP_DIR)}:{lineno}")
        self.assertEqual(
            offenders,
            [],
            "These handlers discard an exception without logging it or saying "
            "why that is safe. Either log it (log.warning when the failure "
            "changes what someone sees, log.debug when the common cause is an "
            "expected miss), or add a comment explaining why silence is "
            "correct:\n  " + "\n  ".join(offenders),
        )

    def test_the_deliberately_silent_handlers_stay_few(self):
        """A comment makes silence a decision rather than an oversight, but it is
        still silence — so the count is pinned. Raising it should be something
        someone chooses, not something that drifts."""
        total = sum(_count_silent(path) for path in _iter_source_files())
        self.assertLessEqual(
            total,
            12,
            f"{total} handlers do nothing but `pass`. Each is explained, but "
            "prefer a log line over adding more.",
        )


def _count_silent(path: Path) -> int:
    """How many handlers in this file have a body of exactly `pass`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
        return 0
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        for h in node.handlers
        if len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
    )


if __name__ == "__main__":
    unittest.main()
