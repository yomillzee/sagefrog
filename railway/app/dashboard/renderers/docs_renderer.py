"""Admin "Docs" page: how to set up a new client dashboard, in the portal.

Self-contained HTML that mirrors the /admin, HQ, and Trends chrome (same colour
tokens, header, card styling). The content is the operational runbook for
standing up a new client dashboard — the same material as
docs/CREATING_A_NEW_DASHBOARD.md, rendered inline so admins can read it without
leaving the portal.
"""

from __future__ import annotations

import html


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def render_docs_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/docs. Static content — no data fetch."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Docs · Sagefrog Marketing Group</title>
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <style>
    :root {{
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#0a7f3f; --amber:#b7791f; --danger:#b42318;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; min-height:100vh; }}
    header {{ color:#fff; padding:16px 24px; display:flex; align-items:center; justify-content:space-between;
      gap:16px; flex-wrap:wrap; background:linear-gradient(120deg,#0a2540 0%,#0d2f57 100%);
      box-shadow:0 2px 14px rgba(5,18,31,.28); }}
    .brand-head {{ display:flex; align-items:center; gap:12px; }}
    .brand-mark {{ width:40px; height:40px; border-radius:11px; display:grid; place-items:center; overflow:hidden;
      background:#fff; box-shadow:0 6px 16px rgba(5,18,31,.4); flex-shrink:0; }}
    .brand-mark img {{ width:100%; height:100%; object-fit:cover; }}
    header h1 {{ margin:0; font-size:1.05rem; letter-spacing:.2px; }}
    header .who {{ font-size:.82rem; opacity:.7; }}
    header a, header button.link {{ color:#fff; text-decoration:none; background:none; border:0;
      cursor:pointer; font:inherit; opacity:.9; }}
    header a:hover {{ opacity:1; text-decoration:underline; }}
    .head-actions {{ display:flex; align-items:center; gap:16px; }}
    main {{ max-width:920px; margin:0 auto; padding:26px 20px 56px; }}
    section {{ background:#fff; border:1px solid var(--line); border-radius:16px;
      padding:22px 26px; margin-bottom:18px; box-shadow:0 6px 22px rgba(10,37,64,.06); }}
    .page-head h2 {{ margin:0; font-size:1.2rem; color:var(--navy); }}
    .page-head .sub {{ color:var(--muted); font-size:.86rem; margin:6px 0 0; }}
    h3 {{ margin:0 0 10px; font-size:1.02rem; color:var(--navy); }}
    .step-num {{ display:inline-grid; place-items:center; width:24px; height:24px; border-radius:7px;
      background:var(--accent); color:#fff; font-size:.8rem; font-weight:800; margin-right:9px; vertical-align:middle; }}
    p {{ line-height:1.6; margin:0 0 12px; color:var(--ink); }}
    ol, ul {{ line-height:1.6; margin:0 0 12px; padding-left:22px; }}
    li {{ margin-bottom:7px; }}
    a {{ color:var(--accent); }}
    code {{ background:#f2f5fa; border:1px solid var(--line); border-radius:5px; padding:1px 6px;
      font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace; font-size:.85em; color:var(--navy); }}
    .muted {{ color:var(--muted); }}
    .callout {{ border-left:3px solid var(--accent); background:#f6f9fd; border-radius:0 10px 10px 0;
      padding:12px 16px; margin:0 0 14px; font-size:.9rem; }}
    .callout.warn {{ border-left-color:var(--amber); background:#fdf9f0; }}
    .callout strong {{ color:var(--navy); }}
    .toc {{ margin:0; padding-left:0; list-style:none; columns:2; column-gap:26px; }}
    .toc li {{ margin-bottom:6px; }}
    table {{ width:100%; border-collapse:collapse; font-size:.88rem; margin:4px 0 12px; }}
    th, td {{ text-align:left; padding:9px 11px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ color:var(--muted); font-weight:700; font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; }}
    .badge {{ display:inline-block; font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em;
      padding:2px 8px; border-radius:999px; }}
    .badge.manual {{ background:#fdf0ee; color:var(--danger); }}
    .badge.auto {{ background:#eaf6ee; color:var(--green); }}
    .step {{ padding:16px 0; border-bottom:1px solid var(--line); }}
    .step:last-child {{ border-bottom:0; padding-bottom:0; }}
  </style>
</head>
<body>
  <header>
    <div class="brand-head">
      <div class="brand-mark">
        <img src="/static/apple-touch-icon.png" alt="Sagefrog" width="40" height="40">
      </div>
      <div>
        <h1>Sagefrog Marketing Group · Docs</h1>
        <span class="who">Signed in as {_esc(user_email)}</span>
      </div>
    </div>
    <div class="head-actions">
      <a href="/admin/hq">HQ</a>
      <a href="/admin/agency-trends">Trends</a>
      <a href="/admin">&larr; Admin</a>
      <form method="post" action="/logout" style="display:inline"><button type="submit" class="link">Sign out</button></form>
    </div>
  </header>
  <main>
    <section>
      <div class="page-head">
        <h2>Setting up a new client dashboard</h2>
        <p class="sub">The end-to-end process for standing up a new client dashboard on the Sagefrog portal.</p>
      </div>
      <div class="callout">
        <strong>Scope:</strong> this covers the <code>bigquery_nixon</code> connector-driven dashboard —
        the mode every dashboard created via <a href="/admin">Admin</a> uses automatically. Everything except
        the two GCP steps below is fully automated: the app builds all datasets, marts, and views on sync, and a
        finished connector is picked up by the daily cron on its own.
      </div>
    </section>

    <section>
      <div class="page-head"><h2>Quick checklist (per new client)</h2></div>
      <ol>
        <li><strong>GCP (manual):</strong> create/confirm the client's GCP project; grant
          <code>marketing-data-reader@sagefrog.iam.gserviceaccount.com</code> both <strong>BigQuery Data
          Editor</strong> and <strong>BigQuery Job User</strong> on it.</li>
        <li><strong>Create the dashboard:</strong> <a href="/admin">Admin</a> → Dashboards → <strong>Add</strong>
          (slug + label).</li>
        <li><strong>Connect each platform:</strong> dashboard → <strong>Connectors</strong> → run each wizard to
          the end (Connect → account → destination → backfill → test → <strong>finish</strong>). Finishing enables
          the daily sync.</li>
        <li><strong>First data:</strong> click <strong>Sync now</strong> on a connector (or wait for the 11:30 UTC
          cron). Overview fills once a paid sync runs; other panels as their connectors sync.</li>
        <li><strong>Verify:</strong> run the verification checklist below — Overview, Analytics, and Explorer
          panels should populate.</li>
      </ol>
      <p class="muted">Everything else (datasets, marts, views) is created automatically by the app.</p>
    </section>

    <section>
      <div class="page-head"><h2>One-time agency setup</h2></div>
      <p>Done once, not per client:</p>
      <ol>
        <li><strong>Agency OAuth</strong> — in <a href="/admin">Admin</a> → platform connections, connect the
          agency login for each platform (Google Ads, GA4, LinkedIn, Meta). These are stored as agency-wide global
          tokens, so you do <strong>not</strong> re-authorize Google/LinkedIn/Meta per client. (HubSpot is the
          exception — it's authorized per client.)</li>
        <li><strong>Shared service account</strong> — <code>marketing-data-reader@sagefrog.iam.gserviceaccount.com</code>
          is the single identity the app uses for BigQuery on every client project.</li>
      </ol>
    </section>

    <section>
      <div class="page-head"><h2>Step by step</h2></div>

      <div class="step">
        <h3><span class="step-num">1</span>Per-client GCP prerequisites <span class="badge manual">manual</span></h3>
        <p>The app cannot create a GCP project (that needs org admin + billing). Before touching the portal:</p>
        <ul>
          <li><strong>Create the client's GCP project</strong> (or reuse an existing one).</li>
          <li><strong>Grant the shared service account</strong> two roles on that project — both are required, the
            wizard verifies this and fails without them:
            <ul>
              <li><strong>BigQuery Data Editor</strong> — create datasets/tables + write data</li>
              <li><strong>BigQuery Job User</strong> — run queries (needed by the verification check and every
                dashboard read)</li>
            </ul>
          </li>
          <li><strong>GA4</strong>: the client's GA4 property must be accessible to the agency Google login so it
            appears in the account picker. GA4 data is pulled by the app via the Data API — no native BigQuery
            export linking required.</li>
        </ul>
      </div>

      <div class="step">
        <h3><span class="step-num">2</span>Create the dashboard <span class="badge auto">~1 min</span></h3>
        <ol>
          <li>Go to <a href="/admin">Admin</a> (admin login required).</li>
          <li>Under <strong>Dashboards → Add</strong>, enter a <strong>slug</strong> (e.g. <code>acme</code>) and
            <strong>display label</strong> (e.g. <code>Acme Co</code>), then submit.</li>
          <li>The dashboard is now live at <code>/dashboard/{{slug}}</code> and renders the Nixon-style template
            immediately (an empty "no data yet" state until connectors are set up). Its
            <code>/settings</code> and <code>/connectors</code> pages are also live.</li>
        </ol>
        <p class="muted">There is no separate "register GCP project" step — the project ID is captured during
          connector setup.</p>
      </div>

      <div class="step">
        <h3><span class="step-num">3</span>Connect the connectors <span class="badge auto">per platform</span></h3>
        <p>From the dashboard sidebar → <strong>Connectors</strong>, every connector type has a
          <strong>Connect</strong> button. The wizard runs these steps:</p>
        <ol>
          <li><strong>Connect</strong> — reuses the agency OAuth token (or authorizes, for HubSpot).</li>
          <li><strong>Select account</strong> — pick the client's account from the platform accounts the agency
            login can see.</li>
          <li><strong>Confirm destination</strong> — enter the <strong>GCP project ID</strong> + dataset names. On
            submit the app provisions and <strong>verifies BigQuery immediately</strong> (creates the standard
            datasets and runs a permission check). If the project doesn't exist or the service account lacks the
            two roles, setup fails here with an actionable message.</li>
          <li><strong>Backfill</strong> — choose how many days of history to pull on the first sync.</li>
          <li><strong>Test</strong> — verifies the source connection.</li>
          <li><strong>Finish</strong> — sets the connector to <code>connected</code> and, via the "Sync
            automatically" toggle (<strong>on by default</strong>), enrolls it in the daily cron.</li>
        </ol>
        <div class="callout warn">
          <strong>Always run the wizard to the end.</strong> A connector left mid-wizard stays
          <code>sync_enabled = false</code> and the cron skips it — you'd have to sync it manually every time.
        </div>
      </div>

      <div class="step">
        <h3><span class="step-num">4</span>First sync (populate the data)</h3>
        <p>Data does not appear until a sync runs. Either:</p>
        <ul>
          <li><strong>Per connector</strong>: the connector card's <strong>Sync now</strong> button, or</li>
          <li><strong>Whole client</strong>: the settings page <strong>Refresh — last 30 days</strong> button, or</li>
          <li><strong>Automatically</strong>: the daily cron <code>POST /internal/sync-bq-all</code>
            (<strong>11:30 UTC</strong>), which syncs every client with <code>sync_enabled</code> connectors in
            parallel, guarded by a Postgres lock.</li>
        </ul>
        <p>Each connector's sync writes its <code>raw_*</code> data and rebuilds its own marts.</p>
      </div>

      <div class="step">
        <h3><span class="step-num">5</span>Verification checklist</h3>
        <ul>
          <li><code>/dashboard/{{slug}}</code> loads the Nixon template (not the old snapshot page).</li>
          <li>Sidebar is identical across Overview / Settings / Connectors / Files.</li>
          <li>Each connected connector card shows a successful last sync.</li>
          <li>Overview <strong>Summary</strong> + <strong>Trend</strong> show numbers → confirms a Google Ads,
            LinkedIn, or Meta sync ran.</li>
          <li>Website Analytics panels show data → confirms GA4 sync + app views.</li>
          <li>Explorer tabs (Google / LinkedIn / Meta) show ads → confirms ad connectors.</li>
          <li>Search Console / SEMrush panels populate (if those connectors are used).</li>
        </ul>
        <p class="muted">If Summary is empty but a paid connector shows a successful sync, check that its raw
          <code>campaign_daily</code> table has rows for the selected date range.</p>
      </div>
    </section>

    <section>
      <div class="page-head"><h2>The two steps that can't be automated</h2></div>
      <p>The onboarding path is otherwise fully automated; these are inherent to GCP and must be done by a human,
        once per client:</p>
      <table>
        <thead><tr><th>Step</th><th>Why it's manual</th></tr></thead>
        <tbody>
          <tr>
            <td><strong>Create the GCP project + grant IAM</strong></td>
            <td>The app can't create GCP projects (needs org admin + billing). Grant the service account both
              BigQuery <strong>Data Editor</strong> and <strong>Job User</strong>. The connector wizard and the
              settings-page <strong>Verify BigQuery access</strong> button both check this.</td>
          </tr>
          <tr>
            <td><strong>Stop any old Dataform schedule</strong></td>
            <td>Dataform is retired (marts are all app-built), but if a client project still has a
              <strong>scheduled</strong> Dataform workflow, disable it in GCP Console → Dataform so it can't
              overwrite the app-built marts.</td>
          </tr>
        </tbody>
      </table>
      <p class="muted">Full reference with data-flow diagrams and mart ownership lives in
        <code>docs/CREATING_A_NEW_DASHBOARD.md</code> in the repo.</p>
    </section>
  </main>
</body>
</html>"""
