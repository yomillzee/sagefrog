#!/usr/bin/env python3
"""Per-client ADA / WCAG scoping audit — a thin CLI over :mod:`a11y_scanner`.

Point it at a client's key templates and it runs axe-core against each, then rolls
the findings up into a *scoping* report: totals by severity, the rules that recur
most (your highest-leverage fixes), a per-page breakdown, and an order-of-magnitude
remediation estimate. The goal is to answer "how big is this ADA job?" in a few
minutes, with evidence, before you quote it.

Usage
-----
    # A handful of pages, straight on the command line:
    python scripts/a11y_audit.py --client "Penn Community Bank" \
        https://www.penncommunitybank.com/ \
        https://www.penncommunitybank.com/personal/ \
        https://www.penncommunitybank.com/contact/

    # Or a newline-delimited file of URLs (one per line, '#' comments allowed):
    python scripts/a11y_audit.py --client acme --urls-file acme_pages.txt

    # Control where reports land and which WCAG tags run:
    python scripts/a11y_audit.py --client acme --out-dir ./audits \
        --tags wcag2a,wcag2aa,wcag21aa  https://acme.com/

Output
------
Writes two files into --out-dir (default ./a11y-reports/):
    <slug>-<date>.json   full machine-readable findings (feed to a ticketing script)
    <slug>-<date>.md     the human scoping report (paste into a proposal / SOW)
and prints the Markdown summary to stdout.

Exit status is 0 on a completed scan (even with violations found), 2 if the
browser was unavailable, 1 on a usage error — so it slots into CI or a cron.

Local dev: internal/loopback hosts are blocked by the shared SSRF guard; set
CONSENT_ALLOW_PRIVATE_HOSTS=1 to scan a staging or localhost URL.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

# Run from anywhere: put the app dir (this file's parent's parent) on sys.path so
# `import a11y_scanner` resolves the same way the app imports it.
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import a11y_scanner  # noqa: E402

_IMPACT_ORDER = a11y_scanner.IMPACT_ORDER


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "client").strip().lower()).strip("-")
    return s or "client"


def _read_urls_file(path: str) -> list[str]:
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# The scoping roll-up and size band live in a11y_scanner so the CLI and the
# in-dashboard Accessibility page count and estimate identically. Thin wrappers
# keep the CLI's call sites (and tests) stable.

def _aggregate(scan: dict) -> dict:
    """Roll per-page axe results up into client-level scoping numbers."""
    return a11y_scanner.aggregate(scan)


def _band(agg: dict) -> str:
    """A one-word size band for the top of a proposal (CLI phrasing)."""
    band = a11y_scanner.size_band(agg)
    return "Clean (no automated violations)" if band == "Clean" else band


def _render_markdown(scan: dict, agg: dict, client: str) -> str:
    today = _dt.date.today().isoformat()
    t = agg["totals_by_impact"]
    lines: list[str] = []
    lines.append(f"# Accessibility scoping audit — {client}")
    lines.append("")
    lines.append(f"_Generated {today} · engine axe-core {scan.get('axe_version') or '?'} · "
                 f"tags: {', '.join(scan.get('tags') or [])}_")
    lines.append("")
    lines.append(f"**Size band: {_band(agg)}**")
    lines.append("")
    lines.append(f"- Pages scanned: **{agg['pages_scanned']}** / {agg['pages_requested']} requested")
    lines.append(f"- Distinct rule failures: **{agg['total_violations']}** "
                 f"across **{agg['total_nodes']}** page elements")
    lines.append(f"- Needs manual review (axe *incomplete*): **{agg['incomplete_total']}**")
    lines.append(f"- First-pass estimate: **~{agg['est_dev_hours']} dev hours** "
                 f"+ ~{agg['est_manual_review_hours']}h manual review")
    lines.append("")
    lines.append("> Automated testing (axe-core) catches roughly a third to a half of WCAG 2.1 AA "
                 "issues — every item below is a real failure, but a full ADA conformance pass still "
                 "needs manual keyboard and screen-reader testing. Treat these numbers as a floor.")
    lines.append("")

    lines.append("## Severity summary")
    lines.append("")
    lines.append("| Impact | Rule failures | Affected elements |")
    lines.append("| --- | ---: | ---: |")
    for impact in _IMPACT_ORDER:
        lines.append(f"| {impact.capitalize()} | {t.get(impact, 0)} | "
                     f"{agg['nodes_by_impact'].get(impact, 0)} |")
    lines.append("")

    if agg["rules"]:
        lines.append("## Highest-leverage fixes (most-affected rules first)")
        lines.append("")
        lines.append("| Rule | Impact | Pages | Elements | Guidance |")
        lines.append("| --- | --- | ---: | ---: | --- |")
        for r in agg["rules"][:25]:
            help_link = f"[{r['help']}]({r['helpUrl']})" if r.get("helpUrl") else r.get("help", "")
            lines.append(f"| `{r['id']}` | {r['impact']} | {r['page_count']} | "
                         f"{r['nodes']} | {help_link} |")
        lines.append("")

    lines.append("## Per-page breakdown")
    lines.append("")
    lines.append("| Page | Critical | Serious | Moderate | Minor | Incomplete |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for pg in scan.get("pages") or []:
        bi = pg.get("by_impact") or {}
        label = pg.get("title") or pg.get("url")
        if pg.get("errors") and not pg.get("scanned"):
            label = f"{label} ⚠️"
        lines.append(f"| {label} | {bi.get('critical', 0)} | {bi.get('serious', 0)} | "
                     f"{bi.get('moderate', 0)} | {bi.get('minor', 0)} | {pg.get('incomplete_count', 0)} |")
    lines.append("")

    problem_pages = [p for p in (scan.get("pages") or []) if p.get("errors")]
    if problem_pages:
        lines.append("## Scan warnings")
        lines.append("")
        for pg in problem_pages:
            lines.append(f"- `{pg.get('url')}`: {'; '.join(pg.get('errors') or [])}")
        lines.append("")
    if scan.get("rejected"):
        lines.append("## Rejected URLs")
        lines.append("")
        for r in scan["rejected"]:
            lines.append(f"- `{r['url']}`: {r['reason']}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run an axe-core accessibility scoping audit for a client.",
    )
    ap.add_argument("urls", nargs="*", help="Page URLs to scan.")
    ap.add_argument("--client", default="client", help="Client name/slug for the report title & filenames.")
    ap.add_argument("--urls-file", help="Path to a newline-delimited file of URLs ('#' comments allowed).")
    ap.add_argument("--out-dir", default="a11y-reports", help="Directory for the .json and .md reports.")
    ap.add_argument("--tags", help="Comma-separated axe tag set (overrides A11Y_WCAG_TAGS / defaults).")
    ap.add_argument("--no-write", action="store_true", help="Print the report but don't write files.")
    args = ap.parse_args(argv)

    urls = list(args.urls)
    if args.urls_file:
        try:
            urls.extend(_read_urls_file(args.urls_file))
        except OSError as exc:
            print(f"error: could not read --urls-file: {exc}", file=sys.stderr)
            return 1
    if not urls:
        print("error: no URLs given (pass them as arguments or via --urls-file).", file=sys.stderr)
        return 1

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None

    print(f"Scanning {len(urls)} page(s) for {args.client} …", file=sys.stderr)
    scan = a11y_scanner.scan_pages(urls, tags=tags)

    if not scan.get("available"):
        print(f"error: scan could not run: {scan.get('error')}", file=sys.stderr)
        return 2

    agg = _aggregate(scan)
    report_md = _render_markdown(scan, agg, args.client)
    print(report_md)

    if not args.no_write:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{_slugify(args.client)}-{_dt.date.today().isoformat()}"
        (out_dir / f"{stem}.md").write_text(report_md, encoding="utf-8")
        (out_dir / f"{stem}.json").write_text(
            json.dumps({"client": args.client, "scan": scan, "summary": agg}, indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote {out_dir / (stem + '.md')} and {out_dir / (stem + '.json')}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
