# Vendored axe-core

`axe.min.js` is Deque Systems' [axe-core](https://github.com/dequelabs/axe-core)
accessibility rules engine, vendored so the accessibility scanner
(`a11y_scanner.py` / `scripts/a11y_audit.py`) can inject it into a page at scan
time with **no network fetch** and a **pinned, reproducible ruleset**.

- **Version:** 4.12.1
- **Source:** the `axe.min.js` file from the npm package tarball
  (`https://registry.npmjs.org/axe-core`).
- **License:** Mozilla Public License 2.0 — see `LICENSE`.

## Upgrading

1. Download the desired version from npm and copy its `axe.min.js` here,
   overwriting this one; refresh `LICENSE` alongside it.
2. That's it — `a11y_scanner.axe_version()` reads the version out of the file, so
   every generated report records which ruleset produced it.

See [`docs/ACCESSIBILITY_AUDITS.md`](../../../../docs/ACCESSIBILITY_AUDITS.md).
