"""Google Business tab (Google Business Profile) for the shared dashboard.

Like pagespeed_renderer, these helpers return raw HTML/CSS/JS that
bigquery_dashboard_renderer injects into its page, so the JS depends on helpers
already in that page's script scope (getJson, setStatus, skelCards, esc, num,
lineChart, withDates) plus the GOOGLE_BUSINESS_API const the page supplies.
Data comes from /api/clients/{key}/google-business/summary (see
bq_google_business_service.fetch_summary).

It also reuses three presentation classes the Site Performance pane defines —
``.ps-info`` / ``.ps-tip`` (the "?" help bubbles, including the off-screen
anchoring that pane delegates on document) and ``.ps-target`` (the small line
under a card value). Both panes are always emitted into the same page, so this
is safe, but it is a real dependency: dropping the PageSpeed CSS would flatten
this tab's tooltips.

The tab answers two questions a client with locations actually asks, and which
GA4 cannot answer because the activity never touches their site:

  * How many people saw the listing, and did they do anything about it — call,
    tap directions, click through?
  * What is our rating, and is anything waiting for a reply?

Reviews deliberately ignore the page's date range: "what needs answering" is a
current-state question, and scoping it to the selected window would hide an
unanswered one-star review from last month.
"""

from __future__ import annotations


def pane_html() -> str:
    """The hidden #pane-google_business section."""
    return """
    <div id="pane-google_business" hidden>
      <section id="sec-gb-kpis">
        <div class="sec-head">
          <h2>Google Business Profile</h2>
          <span class="status" id="gbStatus"></span>
        </div>
        <div class="gb-intro">
          What people did after finding this business on Google Search and Maps.
          These actions happen on Google, so they never appear in website analytics.
        </div>
        <div class="cards" id="gbKpis"></div>
      </section>
      <section id="sec-gb-trend">
        <div class="sec-head"><h2>Views and actions over time</h2></div>
        <div class="gb-legend">
          <span class="gb-legend-item"><span class="gb-dot" style="background:#1d6fd0"></span>Listing views</span>
          <span class="gb-legend-item"><span class="gb-dot" style="background:#0c9d61"></span>Actions taken</span>
        </div>
        <div class="chart-wrap">
          <div class="chart-canvas-host" style="height:230px"><canvas id="gbTrendChart"></canvas></div>
        </div>
      </section>
      <section id="sec-gb-discovery">
        <div class="sec-head"><h2>Where people found the listing</h2></div>
        <div id="gbDiscovery"></div>
      </section>
      <section id="sec-gb-locations" hidden>
        <div class="sec-head"><h2>By location</h2></div>
        <div class="table-wrap"><table id="gbLocations" class="compact"></table></div>
      </section>
      <section id="sec-gb-reviews">
        <div class="sec-head">
          <h2>Reviews</h2>
          <span class="status" id="gbReviewStatus"></span>
        </div>
        <div class="cards" id="gbReviewKpis"></div>
        <div id="gbReviewList"></div>
      </section>
    </div><!-- /pane-google_business -->
    """


def pane_css() -> str:
    """Scoped styles for the Google Business tab."""
    return """
    .gb-intro { font-size:.82rem; color:var(--muted); line-height:1.55; margin:-4px 0 14px; max-width:78ch; }
    .gb-legend { display:flex; flex-wrap:wrap; gap:16px; margin:0 0 12px; font-size:.8rem; color:var(--muted); }
    .gb-legend-item { display:inline-flex; align-items:center; gap:6px; }
    .gb-dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    /* Discovery split: Maps vs Search, each bar sized by share of total views.
       A client's first question about their listing is almost always "are we
       being found on Maps or in Search", and this answers it without a chart. */
    .gb-split { display:grid; gap:12px; }
    .gb-split-row { display:grid; grid-template-columns:minmax(120px, 180px) 1fr auto; align-items:center; gap:14px; }
    .gb-split-label { font-size:.85rem; font-weight:600; color:var(--text); }
    .gb-split-track { height:10px; border-radius:5px; background:var(--row-alt, #eef2f7); overflow:hidden; }
    .gb-split-fill { display:block; height:100%; border-radius:5px; background:var(--accent, #1d6fd0); }
    .gb-split-fill--alt { background:#0c9d61; }
    .gb-split-value { font-size:.85rem; font-weight:700; color:var(--navy, #0a2540); font-variant-numeric:tabular-nums; }
    .gb-split-sub { font-size:.72rem; color:var(--muted); font-weight:500; }
    /* Reviews */
    .gb-stars { color:#e8a13a; letter-spacing:1px; }
    .gb-review { padding:14px 0; border-top:1px solid var(--line); }
    .gb-review:first-child { border-top:0; }
    .gb-review-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
    .gb-review-who { font-size:.88rem; font-weight:700; color:var(--navy, #0a2540); }
    .gb-review-meta { font-size:.74rem; color:var(--muted); }
    .gb-review-body { margin-top:6px; font-size:.85rem; line-height:1.55; color:var(--text); }
    .gb-badge { display:inline-block; padding:2px 8px; border-radius:20px; font-size:.68rem; font-weight:700; letter-spacing:.02em; }
    .gb-badge--open { background:#fdecec; color:#c3363b; }
    .gb-badge--done { background:#e8f6ef; color:#0a7a4c; }
    .gb-empty { padding:16px; border:1px dashed var(--line); border-radius:10px; font-size:.84rem; color:var(--muted); line-height:1.55; }
    """


def pane_js() -> str:
    """loadGoogleBusiness() and its render helpers. Emitted in the page <script>."""
    return """
    // ---- Google Business Profile ----
    // [label, response key, plain-language tooltip]. "Listing views" is the sum
    // of the four impression columns Google splits by surface and device; the
    // split itself is shown in the discovery section below.
    const GB_ACTION_CARDS = [
      ['Calls', 'call_clicks', 'People who tapped the call button on the Google listing.'],
      ['Website clicks', 'website_clicks', 'People who clicked through to the website from Google.'],
      ['Direction requests', 'direction_requests', 'People who asked Google for directions to this location.'],
      ['Messages', 'conversations', 'People who started a chat with the business through Google.'],
    ];
    const GB_IMPRESSION_KEYS = [
      'impressions_desktop_maps', 'impressions_desktop_search',
      'impressions_mobile_maps', 'impressions_mobile_search',
    ];
    const GB_ACTION_KEYS = [
      'call_clicks', 'website_clicks', 'direction_requests',
      'conversations', 'bookings', 'food_orders',
    ];
    function gbSum(row, keys) {
      return keys.reduce((t, k) => t + (Number(row && row[k]) || 0), 0);
    }
    function gbNum(v) {
      const n = Number(v) || 0;
      return n.toLocaleString();
    }
    function gbInfo(tip) {
      if (!tip) return '';
      const safe = esc(tip);
      return `<button type="button" class="ps-info ps-tip ps-tip--wide" data-tip="${safe}" aria-label="${safe}">?</button>`;
    }
    function gbCard(label, value, tip, sub) {
      return `<div class="card"><div class="card-title">${esc(label)}${gbInfo(tip)}</div>` +
        `<div class="card-value">${gbNum(value)}</div>` +
        (sub ? `<div class="ps-target">${esc(sub)}</div>` : '') + `</div>`;
    }
    function gbStars(rating) {
      const r = Number(rating);
      if (!isFinite(r) || r <= 0) return '';
      const full = Math.round(r);
      return `<span class="gb-stars" aria-hidden="true">${'★'.repeat(full)}${'☆'.repeat(Math.max(0, 5 - full))}</span>`;
    }
    // Views split by surface (Maps vs Search) and device. Percentages are of
    // total views, so the two surface rows add to 100%.
    function gbRenderDiscovery(totals) {
      const host = document.getElementById('gbDiscovery');
      if (!host) return;
      const maps = (Number(totals.impressions_desktop_maps) || 0) + (Number(totals.impressions_mobile_maps) || 0);
      const search = (Number(totals.impressions_desktop_search) || 0) + (Number(totals.impressions_mobile_search) || 0);
      const mobile = (Number(totals.impressions_mobile_maps) || 0) + (Number(totals.impressions_mobile_search) || 0);
      const total = maps + search;
      if (!total) {
        host.innerHTML = '<div class="gb-empty">No listing views recorded in this date range.</div>';
        return;
      }
      const pct = v => Math.round((v / total) * 100);
      const rows = [
        ['Google Maps', maps, ''],
        ['Google Search', search, 'gb-split-fill--alt'],
      ];
      host.innerHTML = '<div class="gb-split">' + rows.map(([label, value, cls]) =>
        `<div class="gb-split-row"><span class="gb-split-label">${esc(label)}</span>` +
        `<span class="gb-split-track"><span class="gb-split-fill ${cls}" style="width:${pct(value)}%"></span></span>` +
        `<span class="gb-split-value">${gbNum(value)} <span class="gb-split-sub">${pct(value)}%</span></span></div>`
      ).join('') +
        `<div class="gb-split-row"><span class="gb-split-label">On a phone</span>` +
        `<span class="gb-split-track"><span class="gb-split-fill" style="width:${pct(mobile)}%;opacity:.55"></span></span>` +
        `<span class="gb-split-value">${gbNum(mobile)} <span class="gb-split-sub">${pct(mobile)}%</span></span></div>` +
        '</div>';
    }
    function gbRenderLocations(locations) {
      const section = document.getElementById('sec-gb-locations');
      const table = document.getElementById('gbLocations');
      if (!section || !table) return;
      // One location needs no breakdown — the cards above already are it.
      if (!locations || locations.length < 2) { section.hidden = true; return; }
      section.hidden = false;
      const head = '<thead><tr><th class="left">Location</th><th>Views</th><th>Calls</th>' +
        '<th>Website</th><th>Directions</th></tr></thead>';
      const body = locations.map(l =>
        `<tr><td class="left">${esc(l.location_name || l.location_id || '')}</td>` +
        `<td>${gbNum(gbSum(l, GB_IMPRESSION_KEYS))}</td>` +
        `<td>${gbNum(l.call_clicks)}</td><td>${gbNum(l.website_clicks)}</td>` +
        `<td>${gbNum(l.direction_requests)}</td></tr>`
      ).join('');
      table.innerHTML = head + '<tbody>' + body + '</tbody>';
    }
    function gbRenderReviews(reviews) {
      const kpis = document.getElementById('gbReviewKpis');
      const list = document.getElementById('gbReviewList');
      if (!kpis || !list) return;
      reviews = reviews || {};
      const total = Number(reviews.total) || 0;
      if (!total) {
        kpis.innerHTML = '';
        list.innerHTML = '<div class="gb-empty">No reviews synced yet. Reviews come from Google\\'s ' +
          'legacy Business Profile API, which needs the same access approval as the rest of ' +
          'this connector.</div>';
        setStatus('gbReviewStatus', '');
        return;
      }
      const rating = reviews.average_rating;
      const unanswered = Number(reviews.unanswered) || 0;
      kpis.innerHTML =
        `<div class="card"><div class="card-title">Average rating</div>` +
        `<div class="card-value">${rating == null ? '—' : rating.toFixed(1)}` +
        `<span style="font-size:.55em;color:var(--muted);font-weight:600"> /5</span></div>` +
        `<div class="ps-target">${gbStars(rating)}</div></div>` +
        gbCard('Total reviews', total, 'Every review on the connected locations, all time.') +
        `<div class="card"><div class="card-title">Awaiting a reply</div>` +
        `<div class="card-value" style="color:${unanswered ? '#e5484d' : '#0c9d61'}">${gbNum(unanswered)}</div>` +
        `<div class="ps-target">${unanswered ? 'Replying publicly is a ranking signal.' : 'Every review has been answered.'}</div></div>`;

      const recent = reviews.recent || [];
      list.innerHTML = recent.length ? recent.map(r => {
        const when = String(r.create_time || '').slice(0, 10);
        const badge = r.answered
          ? '<span class="gb-badge gb-badge--done">Replied</span>'
          : '<span class="gb-badge gb-badge--open">Needs reply</span>';
        return `<div class="gb-review"><div class="gb-review-head">` +
          `<span class="gb-review-who">${esc(r.reviewer_name || 'Anonymous')}</span>` +
          gbStars(r.star_rating) +
          `<span class="gb-review-meta">${esc(when)}${r.location_name ? ' · ' + esc(r.location_name) : ''}</span>` +
          badge + `</div>` +
          (r.comment ? `<div class="gb-review-body">${esc(r.comment)}</div>` : '') + `</div>`;
      }).join('') : '<div class="gb-empty">No review text to show.</div>';
      setStatus('gbReviewStatus', `${total} review${total === 1 ? '' : 's'} · all time`);
    }
    function renderGoogleBusiness(p) {
      p = p || {};
      const totals = p.totals || {};
      const views = gbSum(totals, GB_IMPRESSION_KEYS);
      const actions = gbSum(totals, GB_ACTION_KEYS);
      const rate = views ? Math.round((actions / views) * 100) : null;
      document.getElementById('gbKpis').innerHTML =
        gbCard('Listing views', views,
          'How many times the business showed up on Google Search or Maps in this period.') +
        gbCard('Actions taken', actions,
          'Calls, website clicks, direction requests and messages added together.',
          rate == null ? '' : `${rate}% of views led to an action`) +
        GB_ACTION_CARDS.map(([label, key, tip]) => gbCard(label, totals[key], tip)).join('');

      gbRenderDiscovery(totals);
      gbRenderLocations(p.locations);
      gbRenderReviews(p.reviews);

      const daily = p.daily || [];
      const labels = daily.map(r => String(r.metric_date || '').slice(5));
      lineChart('gbTrendChart', labels, [
        { label: 'Listing views', data: daily.map(r => gbSum(r, GB_IMPRESSION_KEYS)), color: '#1d6fd0', fmt: v => v },
        { label: 'Actions taken', data: daily.map(r => gbSum(r, GB_ACTION_KEYS)), color: '#0c9d61', fmt: v => v },
      ], { points: false, yDisplay: true, beginAtZero: true });
    }
    let googleBusinessLoaded = false;
    async function loadGoogleBusiness() {
      setStatus('gbStatus', 'Loading…');
      document.getElementById('gbKpis').innerHTML = skelCards(6);
      try {
        const p = await getJson(withDates(GOOGLE_BUSINESS_API));
        renderGoogleBusiness(p);
        const range = (p && p.start_date && p.end_date)
          ? shortRangeLabel(p.start_date, p.end_date) : '';
        setStatus('gbStatus', range);
      } catch (err) {
        setStatus('gbStatus', err.message || String(err), true);
      }
    }
    """
