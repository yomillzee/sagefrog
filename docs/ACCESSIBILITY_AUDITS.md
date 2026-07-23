# Accessibility Audits (ADA / WCAG scoping)

A command-line tool that drives a **fresh, automated Chromium session**, loads a
client's key pages, injects Deque's [**axe-core**](https://github.com/dequelabs/axe-core)
rules engine, and rolls the findings up into a **scoping report** — so you can
answer *"how big is this ADA remediation job?"* in a few minutes, with evidence,
before you quote it.

It reuses the same battle-tested Playwright/Chromium plumbing as the
[Consent & Tracking Health scanner](CONSENT_TRACKING_HEALTH.md): the browser
discovery, container-friendly launch flags, on-demand install, and SSRF URL guard
are shared, so any environment that can run one scanner can run this one with no
extra setup.

---

## Why axe-core + Playwright

- **axe-core** is the industry-standard, open-source accessibility rules engine
  (it powers Lighthouse's a11y audit, the Chrome/Firefox devtools, and most
  commercial scanners). It maps every finding to a specific **WCAG success
  criterion** and an **impact** — `critical` / `serious` / `moderate` / `minor` —
  and reports the exact CSS selector and a plain-English fix for each offending
  element. Crucially, it is tuned for **zero false positives**: everything it
  flags is a real failure, which makes it a safe *floor* for an estimate.
- **Playwright** loads the page in a real, fully-rendered browser — so the scan
  sees the DOM a user actually gets, including JavaScript-rendered content, web
  fonts, and lazy-loaded images that a static HTML fetch would miss. axe then
  runs *inside* that page against the live DOM.

> **What this catches — and what it doesn't.** Automated testing reliably finds
> ~30–50% of WCAG 2.1 AA issues: colour contrast, missing alt text, unlabeled form
> fields, ARIA misuse, heading order, landmark/region structure, document
> language, link text, and more. It **cannot** judge keyboard traps, focus order,
> screen-reader semantics, or whether alt text is *meaningful* — those need a
> human. Use this to **scope and prioritise**, then budget manual testing on top.
> The report says this on its face so a client never mistakes it for a full
> conformance certification.

---

## In the dashboard (per client)

Every client's **Insights** page (`/dashboard/{slug}/settings`) carries an
**Accessibility** card next to *Consent health*; **Open →** goes to the audit
page at `/dashboard/{slug}/accessibility`. There an admin edits the page list —
**seeded from the client's Consent-scan pages** when configured — and clicks **Run
audit**. The scan runs on demand and the report renders straight back:

1. A severity summary.
2. **Likely root causes** — affected elements clustered by the component they
   come from (shared CSS-selector root). A cluster spanning many elements and
   several rules is almost always **one broken template**, so the report leads
   with it: e.g. "~205 of 228 affected elements trace to the navigation — fix the
   shared template once, not 205 separate tasks." This keeps a big raw count from
   reading as hundreds of independent problems (technically accurate but
   commercially alarmist). The grouping is a **heuristic**, labelled "likely".
3. **All issues** — the full element-level list, every violation grouped by rule
   with each element's selector, failure summary, and HTML snippet, for the devs.
4. A per-page breakdown.

It deliberately does **not** put an effort estimate on the page (developers scope
that themselves). The CLI still prints an estimate for proposal use.

It's intentionally **stateless** — no database, no background worker, no cron.
The scan runs synchronously in the request (FastAPI's worker threadpool, which is
where Playwright's sync API is safe), so allow ~5–15s per page and keep the list
to a representative handful (capped at 12). Running a scan is **admin-only** and
recorded in the audit log (`accessibility.scan_ran`); the card is hidden from
non-admins. Nothing is persisted between runs — for a saved report, use the CLI
below, which writes files.

## Quick start (CLI)

From `railway/app/`:

```bash
# A handful of the client's most important templates:
python scripts/a11y_audit.py --client "Penn Community Bank" \
    https://www.penncommunitybank.com/ \
    https://www.penncommunitybank.com/personal/ \
    https://www.penncommunitybank.com/personal/checking/ \
    https://www.penncommunitybank.com/contact/
```

or feed a list from a file (one URL per line, `#` comments allowed):

```bash
python scripts/a11y_audit.py --client acme --urls-file acme_pages.txt
```

You get the Markdown report on stdout, plus two files written to
`a11y-reports/` (git-ignored):

| File | Use |
|---|---|
| `<slug>-<date>.md`   | The human scoping report — paste into a proposal / SOW. |
| `<slug>-<date>.json` | Full machine-readable findings — feed to a ticketing script. |

### Which pages to scan

Accessibility problems live in **templates**, not individual URLs, so pick one
page per distinct template rather than crawling the whole site:

- Home page
- A primary landing / product / service page
- A page with a **form** (contact, application, search results)
- A **content** page (blog post, article) — catches heading/link/contrast issues
- Anything with a **data table**, **carousel**, **modal**, or **video**

5–10 well-chosen pages give a representative estimate for the whole site far
faster (and cheaper) than scanning hundreds of near-identical URLs.

---

## Reading the report

```
**Size band: Medium**

- Pages scanned: 6 / 6 requested
- Distinct rule failures: 14 across 88 page elements
- Needs manual review (axe *incomplete*): 5
- First-pass estimate: ~19.5 dev hours + ~0.5h manual review
```

- **Size band** — a one-word `Small` / `Medium` / `Large` for the top of a
  proposal, from the critical+serious count and total failures.
- **Distinct rule failures vs. affected elements** — one rule (say
  `color-contrast`) failing on 40 elements is usually *one* fix (a palette or CSS
  change) applied everywhere, not 40 separate jobs. The report leads with the
  rule count for exactly this reason.
- **Highest-leverage fixes** — rules sorted by impact then by how many elements
  they hit, each linked to Deque's fix guidance. The top of this table is where a
  developer should start.
- **Per-page breakdown** — severity counts per page; a `⚠️` marks a page that
  failed to scan cleanly (see *Scan warnings*).
- **Incomplete** — items axe couldn't decide automatically; they need a human and
  are counted into the manual-review estimate, never into the failure totals.

### The effort estimate

A deliberately coarse **floor**, not a bid. Each *rule instance* (a rule failing
on a page) is weighted by impact — `critical` 2.0h, `serious` 1.5h, `moderate`
0.75h, `minor` 0.25h — and summed; `incomplete` items add a small manual-review
figure. It weights per **rule**, not per element, because fixes generalise across
the elements a rule hits. Tune the weights in `a11y_scanner.IMPACT_WEIGHTS`.

---

## Options & configuration

| Flag | Purpose |
|---|---|
| `--client NAME`     | Report title and filename slug. |
| `--urls-file PATH`  | Newline-delimited URL list (merged with any positional URLs). |
| `--out-dir DIR`     | Where reports are written (default `a11y-reports/`). |
| `--tags a,b,c`      | axe tag set to run (see below). |
| `--no-write`        | Print the report only; write nothing. |

**WCAG tag set** (default `wcag2a,wcag2aa,wcag21a,wcag21aa,best-practice`).
WCAG 2.0/2.1 level **A + AA** is the conformance target US courts and the DOJ
point to for ADA. `best-practice` adds axe's non-WCAG recommendations (kept in
their own bucket so they never inflate the strict-conformance count). Override per
run with `--tags`, or globally with the `A11Y_WCAG_TAGS` env var. To scope only
strict AA, e.g.: `--tags wcag2a,wcag2aa,wcag21a,wcag21aa`.

**Environment knobs** (all optional; the `CONSENT_SCANNER_*` ones are shared with
the consent scanner):

| Var | Effect |
|---|---|
| `A11Y_WCAG_TAGS`            | Default tag set when `--tags` is omitted. |
| `A11Y_SETTLE_MS`            | ms to wait after load before running axe (default 2500). |
| `CONSENT_SCANNER_TIMEOUT_MS`| Per-navigation timeout (default 30000). |
| `CONSENT_SCANNER_PROXY`     | Egress proxy for the browser. |
| `CONSENT_SCANNER_NO_SANDBOX`| `1` to pass `--no-sandbox` (auto-on as root / in the container). |
| `CONSENT_SCANNER_IGNORE_HTTPS` | `1` to ignore TLS errors (e.g. behind a re-terminating proxy). |
| `CONSENT_ALLOW_PRIVATE_HOSTS`  | `1` to permit scanning localhost / staging hosts (off by default as an SSRF guard). |

---

## How axe-core is bundled

The engine is **vendored** at `railway/app/vendor/axe-core/axe.min.js` (currently
v4.12.1) and injected into each page at scan time. Bundling keeps a scan hermetic
— no CDN fetch at run time — and pins the ruleset so results are reproducible
across deploys. The reported `axe_version` in every report records which ruleset
produced it.

**To upgrade the rules**, replace that file with a newer build from
[npm](https://www.npmjs.com/package/axe-core) (`axe.min.js` from the package
tarball) and refresh the `LICENSE` alongside it; the version in reports updates
automatically.

---

## Running the tests

```bash
cd railway/app
python -m unittest tests.test_a11y_scanner
```

The pure tests (aggregation math, effort estimate, vendored-engine sanity) always
run; the browser tests inject axe into a page with a known violation and are
skipped automatically when Playwright or a Chromium binary isn't present — the
same pattern as `tests/test_consent_scanner_browser.py`.

---

## Programmatic use

`scan_pages` returns structured results you can wire into a dashboard, a cron, or
a ticket-creation script:

```python
import a11y_scanner

result = a11y_scanner.scan_pages([
    "https://www.penncommunitybank.com/",
    "https://www.penncommunitybank.com/contact/",
])
# result["available"] is False (never raises) if the browser can't run.
for page in result["pages"]:
    print(page["url"], page["by_impact"], "->", page["violation_count"], "rules")
```

It degrades gracefully exactly like the consent scanner: a missing browser or an
unreachable page is reported in-band (`available=False`, or per-page `errors`),
so a scheduled audit records a failure instead of crashing.
