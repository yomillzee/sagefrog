"""Admin "Docs" page: how to set up a new client dashboard, in the portal.

Rendered inside the shared admin shell (navy sidebar + client switcher) so Docs
sits alongside the other admin sections rather than as a standalone page. The
content is the operational runbook for standing up a new client dashboard — the
same material as docs/DASHBOARD_SETUP_SIMPLE.md, rewritten as a friendly,
plain-language walkthrough. The essentials sit up front as five plain steps; the
infrastructure detail (marts, agency OAuth, Dataform, troubleshooting) is tucked
into collapsible "Good to know" panels so the page reads simply for a layman
without losing the reference material.
"""

from __future__ import annotations

import html

from dashboard.renderers.base_layout import render_admin_shell_page


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


_DOCS_CSS = """
    :root {
      --navy:#0a2540; --ink:#16233a; --muted:#5b6678; --line:#e6ebf3;
      --accent:#2563eb; --accent-d:#1d4ed8; --soft:#f5f8fe;
      --green:#0a7f3f; --amber:#b7791f; --danger:#b42318;
    }
    body { color:var(--ink);
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; }
    main { max-width:820px; margin:0 auto; padding:26px 24px 60px; }
    section { background:#fff; border:1px solid var(--line); border-radius:18px;
      padding:24px 28px; margin-bottom:16px; box-shadow:0 6px 22px rgba(10,37,64,.05); }"""


def render_docs_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/docs. Static content — no data fetch."""
    extra_css = _DOCS_CSS + """
    p { line-height:1.6; margin:0 0 12px; color:var(--ink); }
    a { color:var(--accent); }
    strong { color:var(--navy); }
    .muted { color:var(--muted); }
    code { background:#eef2fa; border:1px solid var(--line); border-radius:5px; padding:1px 6px;
      font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace; font-size:.85em; color:var(--navy); }

    /* Hero */
    .hero { display:flex; gap:18px; align-items:flex-start; }
    .hero .badge-ic { flex-shrink:0; width:52px; height:52px; border-radius:15px; display:grid; place-items:center;
      background:linear-gradient(135deg,#2563eb,#1d4ed8); color:#fff; box-shadow:0 8px 20px rgba(37,99,235,.28); }
    .hero .badge-ic svg { width:26px; height:26px; }
    .hero h1 { margin:2px 0 6px; font-size:1.5rem; color:var(--navy); letter-spacing:-.01em; }
    .hero p { margin:0; color:var(--muted); font-size:.96rem; line-height:1.55; }
    .pills { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 0; }
    .pill { display:inline-flex; align-items:center; gap:7px; font-size:.82rem; font-weight:600; color:var(--navy);
      background:var(--soft); border:1px solid #dbe6fb; border-radius:999px; padding:6px 13px; }
    .pill .dot { width:7px; height:7px; border-radius:50%; background:var(--accent); }
    .pill.auto .dot { background:var(--green); }
    .pill.manual .dot { background:var(--amber); }

    /* Section heads */
    .head { margin:0 0 16px; }
    .head h2 { margin:0; font-size:1.12rem; color:var(--navy); }
    .head .sub { color:var(--muted); font-size:.88rem; margin:5px 0 0; }

    /* First callout */
    .first { border:1px solid #cfe0fb; background:linear-gradient(180deg,#f7faff,#f2f7ff);
      border-radius:16px; padding:20px 22px; }
    .first .kicker { display:inline-flex; align-items:center; gap:8px; font-size:.74rem; font-weight:800;
      text-transform:uppercase; letter-spacing:.06em; color:var(--accent-d); margin-bottom:10px; }
    .first ul { margin:8px 0 0; padding-left:20px; line-height:1.6; }
    .first li { margin-bottom:7px; }
    .first .foot { margin:12px 0 0; font-size:.86rem; color:var(--muted); }

    /* Step timeline */
    .steps { margin-top:2px; }
    .step { display:grid; grid-template-columns:auto 1fr; gap:18px; }
    .step .rail { display:flex; flex-direction:column; align-items:center; }
    .step .num { width:34px; height:34px; border-radius:50%; background:var(--accent); color:#fff;
      font-weight:800; font-size:.98rem; display:grid; place-items:center;
      box-shadow:0 3px 9px rgba(37,99,235,.35); }
    .step .line { flex:1; width:2px; background:var(--line); margin:6px 0; border-radius:2px; }
    .step:last-child .line { display:none; }
    .step .body { padding:2px 0 22px; min-width:0; }
    .step:last-child .body { padding-bottom:2px; }
    .step h3 { margin:4px 0 6px; font-size:1.02rem; color:var(--navy); display:flex; flex-wrap:wrap;
      align-items:center; gap:10px; }
    .step p { margin:0; font-size:.92rem; line-height:1.6; }
    .step .note { margin-top:9px; font-size:.85rem; padding:9px 13px; border-radius:10px;
      background:#fdf6ea; border:1px solid #f2e4c6; color:#6b4e12; }
    .step .note strong { color:#6b4e12; }
    .tag { font-size:.68rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em;
      padding:3px 9px; border-radius:999px; white-space:nowrap; }
    .tag.portal { background:#eaf1fe; color:var(--accent-d); }
    .tag.auto { background:#eaf6ee; color:var(--green); }

    /* Verify checklist */
    .check { list-style:none; margin:0; padding:0; display:grid; gap:9px; }
    .check li { display:flex; align-items:flex-start; gap:11px; font-size:.92rem; line-height:1.5; }
    .check .tick { flex-shrink:0; width:22px; height:22px; border-radius:50%; background:#eaf6ee;
      color:var(--green); display:grid; place-items:center; margin-top:1px; }
    .check .tick svg { width:13px; height:13px; }

    /* Good-to-know accordions */
    details.gtk { border:1px solid var(--line); border-radius:12px; margin-bottom:10px;
      background:#fbfcfe; overflow:hidden; }
    details.gtk:last-child { margin-bottom:0; }
    details.gtk > summary { list-style:none; cursor:pointer; padding:14px 18px; font-weight:700;
      color:var(--navy); font-size:.94rem; display:flex; align-items:center; gap:11px; }
    details.gtk > summary::-webkit-details-marker { display:none; }
    details.gtk > summary .chev { margin-left:auto; color:var(--muted); transition:transform .15s ease; }
    details.gtk[open] > summary .chev { transform:rotate(90deg); }
    details.gtk > summary .gic { color:var(--accent); display:grid; place-items:center; }
    details.gtk .gtk-body { padding:2px 18px 18px; font-size:.9rem; line-height:1.6; color:var(--ink); }
    details.gtk .gtk-body ul, details.gtk .gtk-body ol { padding-left:20px; margin:0 0 10px; }
    details.gtk .gtk-body li { margin-bottom:7px; }
    details.gtk .gtk-body p:last-child, details.gtk .gtk-body ul:last-child { margin-bottom:0; }
    details.gtk table { width:100%; border-collapse:collapse; font-size:.88rem; margin:2px 0 0; }
    details.gtk th, details.gtk td { text-align:left; padding:9px 11px; border-bottom:1px solid var(--line);
      vertical-align:top; }
    details.gtk th { color:var(--muted); font-weight:700; font-size:.72rem; text-transform:uppercase;
      letter-spacing:.04em; }
    details.gtk tr:last-child td { border-bottom:0; }

    /* Reference links */
    .doc-links { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }
    .doc-link { display:flex; align-items:flex-start; gap:12px; text-decoration:none; color:inherit;
      border:1px solid var(--line); border-radius:14px; padding:15px 16px; background:#fbfcfe;
      transition:border-color .12s ease, box-shadow .12s ease, transform .12s ease; }
    .doc-link:hover { border-color:var(--accent); box-shadow:0 6px 18px rgba(37,99,235,.1); transform:translateY(-1px); }
    .doc-link .ic { flex-shrink:0; width:36px; height:36px; border-radius:10px; display:grid; place-items:center;
      background:#eaf1fe; color:var(--accent); font-size:1.05rem; }
    .doc-link .t { font-weight:700; color:var(--navy); font-size:.92rem; }
    .doc-link .d { color:var(--muted); font-size:.8rem; margin-top:3px; line-height:1.45; }
    .doc-link.primary { background:#f4f8ff; border-color:#cfe0fb; }

    @media (max-width:560px) {
      main { padding:20px 14px 48px; }
      section { padding:20px 18px; }
      .hero { gap:14px; }
    }"""

    # SVG glyphs kept inline so the page has no external asset dependencies.
    _check_tick = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" '
                   'stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>')
    _chev = ('<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>')

    content = f"""
  <main>
    <section>
      <div class="hero">
        <span class="badge-ic">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/>
            <rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/>
            <rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>
        </span>
        <div>
          <h1>Set up a new client dashboard</h1>
          <p>Five short steps to get a new client live on the portal. Almost everything happens automatically —
            the app builds all the data behind the scenes. You just click through the portal, plus one small
            bit of setup in Google Cloud.</p>
        </div>
      </div>
      <div class="pills">
        <span class="pill"><span class="dot"></span>About 15 minutes</span>
        <span class="pill auto"><span class="dot"></span>Mostly automatic</span>
        <span class="pill manual"><span class="dot"></span>1 thing to do first, in Google Cloud</span>
      </div>
    </section>

    <section>
      <div class="first">
        <div class="kicker">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/>
            <circle cx="12" cy="12" r="9"/></svg>
          Do this first
        </div>
        <p style="margin-bottom:6px"><strong>One quick job in Google Cloud</strong> — this is the only part outside
          the portal:</p>
        <ul>
          <li>Create a <strong>Google Cloud project</strong> for the client (or reuse one they already have).</li>
          <li>Give our service account access to it —
            <code>marketing-data-reader@sagefrog.iam.gserviceaccount.com</code> — with <strong>two</strong>
            permissions: <strong>BigQuery Data Editor</strong> and <strong>BigQuery Job User</strong>.</li>
        </ul>
        <p class="foot">Don't worry about getting it exactly right — when you connect a platform later, the portal
          checks these two permissions and tells you clearly if anything's missing.</p>
      </div>
    </section>

    <section>
      <div class="head">
        <h2>The five steps</h2>
        <p class="sub">Do these in order. Everything here happens inside the portal.</p>
      </div>
      <div class="steps">

        <div class="step">
          <div class="rail"><span class="num">1</span><span class="line"></span></div>
          <div class="body">
            <h3>Create the dashboard <span class="tag portal">in the portal</span></h3>
            <p>Go to <a href="/admin">Admin</a> → <strong>Dashboards</strong> → <strong>Add</strong>. Give it a short
              name (the "slug", e.g. <code>acme</code>) and a friendly label (e.g. <code>Acme Co</code>). The
              dashboard goes live right away — empty until you connect some data.</p>
          </div>
        </div>

        <div class="step">
          <div class="rail"><span class="num">2</span><span class="line"></span></div>
          <div class="body">
            <h3>Connect each platform <span class="tag portal">in the portal</span></h3>
            <p>Open the dashboard's <strong>Connectors</strong> page. For every platform the client uses (Google Ads,
              GA4, LinkedIn, Meta, and so on), click <strong>Connect</strong> and follow the wizard all the way
              through to <strong>Finish</strong>. When it asks, enter the client's Google Cloud project — the portal
              sets everything up and checks access on the spot.</p>
            <div class="note"><strong>Always click Finish.</strong> A connector left half-done won't sync on its own,
              so you'd have to run it by hand every time.</div>
          </div>
        </div>

        <div class="step">
          <div class="rail"><span class="num">3</span><span class="line"></span></div>
          <div class="body">
            <h3>Run the first sync <span class="tag portal">in the portal</span></h3>
            <p>Data only shows up after a sync. To see results straight away, click <strong>Sync now</strong> on a
              connector (or <strong>Refresh — last 30 days</strong> on the settings page). If you'd rather wait, the
              nightly sync at <strong>11:30 UTC</strong> picks up every connected client automatically.</p>
          </div>
        </div>

        <div class="step">
          <div class="rail"><span class="num">4</span><span class="line"></span></div>
          <div class="body">
            <h3>Let the data land <span class="tag auto">automatic</span></h3>
            <p>Each connector fills in its own part of the dashboard. The <strong>Overview</strong> starts showing
              numbers once a paid platform (Google Ads, LinkedIn or Meta) has synced; the other panels fill in as
              their connectors finish.</p>
          </div>
        </div>

        <div class="step">
          <div class="rail"><span class="num">5</span><span class="line"></span></div>
          <div class="body">
            <h3>Check it's working <span class="tag portal">in the portal</span></h3>
            <p>Open the dashboard and confirm the panels have filled in. The quick checklist below tells you what to
              look for.</p>
          </div>
        </div>

      </div>
    </section>

    <section>
      <div class="head">
        <h2>Quick check when it's live</h2>
        <p class="sub">Open <code>/dashboard/{{slug}}</code> and confirm these have populated.</p>
      </div>
      <ul class="check">
        <li><span class="tick">{_check_tick}</span><span>Each connected platform's card shows a recent, successful
          sync.</span></li>
        <li><span class="tick">{_check_tick}</span><span><strong>Overview</strong> shows Summary and Trend numbers
          (this confirms a Google Ads, LinkedIn or Meta sync ran).</span></li>
        <li><span class="tick">{_check_tick}</span><span><strong>Website Analytics</strong> panels show data (this
          confirms GA4 is flowing).</span></li>
        <li><span class="tick">{_check_tick}</span><span><strong>Explorer</strong> tabs (Google / LinkedIn / Meta)
          show ads.</span></li>
        <li><span class="tick">{_check_tick}</span><span>Search Console and SEMrush panels show data, if those
          connectors are used.</span></li>
      </ul>
    </section>

    <section>
      <div class="head">
        <h2>Good to know</h2>
        <p class="sub">Extra detail if you need it — not required to get a dashboard live.</p>
      </div>

      <details class="gtk">
        <summary>
          <span class="gic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></span>
          One-time agency setup (already done — not per client)
          {_chev}
        </summary>
        <div class="gtk-body">
          <ul>
            <li><strong>Agency logins</strong> — the agency's Google Ads, GA4, LinkedIn and Meta logins are
              connected once in <a href="/admin">Admin</a> and reused for every client, so you don't re-authorize
              them each time. (HubSpot is the exception — it's connected per client.)</li>
            <li><strong>Shared service account</strong> —
              <code>marketing-data-reader@sagefrog.iam.gserviceaccount.com</code> is the single identity the app
              uses for BigQuery across every client project.</li>
          </ul>
        </div>
      </details>

      <details class="gtk">
        <summary>
          <span class="gic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/>
            <path d="M12 17h.01"/><circle cx="12" cy="12" r="9"/></svg></span>
          The two things that can't be automated
          {_chev}
        </summary>
        <div class="gtk-body">
          <p>Both are inherent to Google Cloud and need a human, once per client:</p>
          <table>
            <thead><tr><th>Step</th><th>Why it's manual</th></tr></thead>
            <tbody>
              <tr>
                <td><strong>Create the project + grant access</strong></td>
                <td>The app can't create Google Cloud projects (that needs org admin and billing). Grant the
                  service account both <strong>BigQuery Data Editor</strong> and <strong>BigQuery Job User</strong> —
                  the connector wizard and the settings-page <strong>Verify BigQuery access</strong> button both
                  check this.</td>
              </tr>
              <tr>
                <td><strong>Stop any old Dataform schedule</strong></td>
                <td>Dataform is retired (the app builds everything now), but if a client project still has a
                  <strong>scheduled</strong> Dataform workflow, turn it off in Google Cloud Console → Dataform so it
                  can't overwrite the app-built data.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>

      <details class="gtk">
        <summary>
          <span class="gic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/>
            <path d="m21 21-4.3-4.3"/></svg></span>
          A panel looks empty — what to check
          {_chev}
        </summary>
        <div class="gtk-body">
          <p>If the <strong>Overview</strong> Summary is blank but a paid connector shows a successful sync, check
            that its raw <code>campaign_daily</code> table actually has rows for the date range you're looking at.
            No rows there means there's simply no spend/activity for that period, not a setup problem.</p>
        </div>
      </details>
    </section>

    <section>
      <div class="head">
        <h2>Reference docs</h2>
        <p class="sub">The full write-ups this page is based on, in the repo.</p>
      </div>
      <div class="doc-links">
        <a class="doc-link primary" href="https://github.com/yomillzee/sagefrog/blob/main/docs/DASHBOARD_SETUP_SIMPLE.md" target="_blank" rel="noopener">
          <span class="ic">&#9873;</span>
          <span>
            <span class="t">Simple setup guide &#8599;</span>
            <span class="d">The plain-language checklist for standing up a new client dashboard — this page in Markdown.</span>
          </span>
        </a>
        <a class="doc-link" href="https://github.com/yomillzee/sagefrog/blob/main/docs/CREATING_A_NEW_DASHBOARD.md" target="_blank" rel="noopener">
          <span class="ic">&#128218;</span>
          <span>
            <span class="t">Full reference &#8599;</span>
            <span class="d">Data-flow diagrams, mart ownership, and scalability notes for the connector-driven dashboard.</span>
          </span>
        </a>
      </div>
    </section>
  </main>"""
    return render_admin_shell_page(
        active_nav="docs",
        page_title="Docs",
        content_html=content,
        session_email=user_email,
        extra_css=extra_css,
    )
