# sagefrog — working notes for Claude

## One shared dashboard renderer for every client

There is **one** master dashboard template: `railway/app/dashboard/renderers/bigquery_dashboard_renderer.py`
(`render_bigquery_dashboard_page`). **Every** client — Nixon included — is served
by it. Do **not** create or edit per-client renderers.

- The **Website Analytics (GA4)** view is the `pane-analytics` section inside that
  file, reached at `/dashboard/<client>?view=analytics`. The Pages / Landing
  Pages / Traffic / etc. tables live there.
- There is no separate analytics renderer. (`analytics_renderer.py` was a
  Nixon-only duplicate and has been removed; its old
  `/dashboard/nixon-bq-test/analytics` URL now 308-redirects into the shared
  view.)

When a change should apply to "the analytics page" or "the dashboard," it goes in
`bigquery_dashboard_renderer.py`, and it applies to all clients at once.

## Renderers are Python f-strings

Each renderer's HTML is one big `f"""..."""`. **Every literal `{` or `}` in the
embedded CSS/JS must be doubled** (`{{` / `}}`); only real Python interpolations
use single braces. `python -m py_compile <file>` catches most brace mistakes, but
a stray single brace that happens to be valid Python only fails at render time —
so always do a real render (below) after editing a renderer.

## Verifying a renderer change (no stubs needed)

The `.claude` SessionStart hook installs the app's Python deps, so you can import
and render for real. Do **not** hand-stub the import chain.

```bash
cd railway/app
python -c "
from dashboard.renderers.bigquery_dashboard_renderer import render_bigquery_dashboard_page as r
html = r(client_slug='demo', api_client_key='demo', label='Demo',
         use_session=True, session_email='t@e.com')
print('rendered OK, len', len(html))
"
```

Rendering fail-opens (no DB/network needed). If an import still fails for missing
deps, run `bash .claude/hooks/session-setup.sh` once.

Tests live in `railway/app/tests` and import renderers directly — run them from
`railway/app` with `python -m pytest tests/<file>`.

## Deploy

Merges to `main` trigger the Railway deploy. 500s referencing
`web_users.ensure_schema` / `DeadlockDetected` in deploy logs are a pre-existing
Postgres locking issue in the auth path, unrelated to renderer/frontend changes.
