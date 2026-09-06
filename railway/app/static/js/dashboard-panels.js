
    // Earliest synced date per source (google/linkedin/meta/google_analytics/gsc),
    // populated by loadHealth() below. Used to warn when a comparison period
    // (see compareStart/compareEnd) falls before the data actually starts.
    let earliestDates = {};
    // The mart-health TABLE now lives on the Settings page; on the dashboard we
    // still fetch it (quietly) only to populate earliestDates, which drives the
    // "comparison period predates synced data" warnings on the summary cards.
    // Deduped: the page kicks this off at startup for the comparison notice
    // while the Overview loader asks for it too -- one fetch serves both.
    let _healthInflight = null;
    function loadHealth() {
      if (_healthInflight) return _healthInflight;
      _healthInflight = (async () => {
        try {
          const payload = await getJson(withDates(HEALTH_API));
          const rows = payload.rows||[];
          earliestDates = {};
          for (const r of rows) {
            const k = String(r.source||'').toLowerCase();
            if (k && r.earliest_date) earliestDates[k] = r.earliest_date;
          }
          syncCompareNotice();
          // Cards no longer wait for this fetch, so any that already rendered
          // drew their warning icon against an empty earliestDates. Repaint.
          refreshCmpWarns();
        } catch(err) {
          // Non-fatal: no earliest-date info just means no comparison warnings.
        } finally {
          _healthInflight = null;
        }
      })();
      return _healthInflight;
    }

    // ---- Explorer ----
    const METRIC_COLS = [
      {key:'spend',label:'Spend',format:money},
      {key:'impressions',label:'Impr.',format:count},
      {key:'clicks',label:'Clicks',format:count},
      {key:'ctr',label:'CTR',format:pct},
      {key:'conversions',label:'Conv.',format:count,convSelect:true,title:'Conversions as the ad platform counts them. Use the selector to isolate a single conversion action; rows whose platform does not report that far down show a dash rather than a guess.'},
      {key:'verified_sel',label:'Verified conv.',format:count,cls:'ga4-col',keSelect:true,title:'GA4-verified conversions, matched to the Meta ad by id (utm_content) and rolled up. Independent of the platform-reported Conv. Use the selector to isolate a single GA4 key event.'},
    ];
    // GA4-verified conversions are a second, differently-sourced answer to "how
    // many conversions", which is exactly what some clients do not want on the
    // page. The kebab's switch drops the column (and the summary card) for this
    // browser only -- it is a reading preference, not portal config, so it lives
    // in localStorage rather than being saved per client. Default on.
    const EXPLORER_VERIFIED_PREF_KEY = 'sf.explorer.verifiedConv';
    let showVerifiedConv = true;
    try { showVerifiedConv = localStorage.getItem(EXPLORER_VERIFIED_PREF_KEY) !== '0'; } catch(e) {}
    function metricCols() {
      return showVerifiedConv ? METRIC_COLS : METRIC_COLS.filter(c=>c.key!=='verified_sel');
    }
    // Explorer table sort — click a column header to sort every tree level (campaigns,
    // ad groups, ads) by it. 'name' sorts the label column alphabetically.
    let explorerSort = { key:'spend', dir:'desc' };
    function explorerMetricVal(m, key) {
      if (key==='ctr') return num(m.impressions) ? num(m.clicks)/num(m.impressions)*100 : 0;
      // Sorting by Conv. has to follow whatever the column is showing, or the
      // arrow orders the table by a number that isn't on screen.
      if (key==='conversions' && convSelectionActive()) return num(m.conversions_sel);
      return num(m[key]);
    }
    function explorerAdName(a) { return String(a.ad_name||a.ad_label||a.ad_id||''); }
    // Client-configured filter chip groups: [{id,label,chips:[{label,phrases}]}].
    // phrases are pre-lowercased server-side; a chip matches a campaign whose
    // (lowercased) name contains any of its phrases.
    // The Campaign explorer's "more options" kebab. It owns the GA4-verified
    // switch and hosts the admin editors; those keep their own popovers, which
    // stop click propagation, so opening one does not close the menu.
    (function(){
      const btn=document.getElementById('explorerAdvBtn'); if (!btn) return;
      const pop=document.getElementById('explorerAdvPop');
      const setOpen=(o)=>{ pop.hidden=!o; btn.setAttribute('aria-expanded', o?'true':'false'); };
      btn.addEventListener('click',(e)=>{ e.stopPropagation(); setOpen(pop.hidden); });
      pop.addEventListener('click',(e)=>e.stopPropagation());
      document.addEventListener('click',()=>setOpen(false));
      document.addEventListener('keydown',(e)=>{ if (e.key==='Escape') setOpen(false); });
      // Both editors live in one narrow column now, so they would overlap if
      // both were open; opening one closes the other.
      pop.querySelectorAll('.ef-edit-btn').forEach(b=>b.addEventListener('click',()=>{
        pop.querySelectorAll('.ef-edit').forEach(w=>{
          if (w.contains(b)) return;
          const sub=w.querySelector('.ef-pop'), other=w.querySelector('.ef-edit-btn');
          if (sub) sub.hidden=true;
          if (other) other.setAttribute('aria-expanded','false');
        });
      }));
      const cb=document.getElementById('explorerVerifiedToggle');
      if (!cb) return;
      cb.checked=showVerifiedConv;
      cb.addEventListener('change',()=>{
        showVerifiedConv=cb.checked;
        try { localStorage.setItem(EXPLORER_VERIFIED_PREF_KEY, showVerifiedConv?'1':'0'); } catch(e) {}
        // Sorting by a column that just left the table would leave the arrow on
        // nothing, so fall back to the default sort.
        if (!showVerifiedConv && explorerSort.key==='verified_sel') explorerSort={key:'spend',dir:'desc'};
        if (explorerLoaded) renderExplorer();
      });
    })();
    // Admin "Edit filters" popover in the Campaign explorer header. Saves the raw
    // chip-rule text, then reloads so the rebuilt chips reflect the new config.
    (function(){
      const btn = document.getElementById('efEditBtn'); if (!btn) return;
      const pop = document.getElementById('efPop');
      const save = document.getElementById('efSave');
      const ta = document.getElementById('efText');
      const status = document.getElementById('efStatus');
      const setOpen = (o) => { pop.hidden = !o; btn.setAttribute('aria-expanded', o ? 'true' : 'false'); };
      btn.addEventListener('click', (e) => { e.stopPropagation(); setOpen(pop.hidden); });
      pop.addEventListener('click', (e) => e.stopPropagation());
      pop.querySelector('.ef-pop-x').addEventListener('click', () => setOpen(false));
      document.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
      save.addEventListener('click', async () => {
        save.disabled = true; status.className = 'ef-status'; status.textContent = 'Saving…';
        try {
          const r = await fetch(EXPLORER_FILTERS_API, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filters: ta.value }),
          });
          const b = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
          status.textContent = 'Saved. Reloading…';
          setTimeout(() => window.location.reload(), 600);
        } catch (err) {
          status.className = 'ef-status err'; status.textContent = 'Save failed: ' + (err.message || err);
          save.disabled = false;
        }
      });
    })();
    // Website Analytics page-path scope: the scope indicator (shown to every
    // viewer when a filter is set) plus the admin "Edit page filter" popover.
    // Saving reloads so the server re-scopes the page-path panels and the
    // site-wide panels re-hide via getModules().
    (function(){
      const bar = document.getElementById('analyticsFilterBar');
      const note = document.getElementById('analyticsScopeNote');
      const btn = document.getElementById('pfEditBtn');
      const active = pathFilterActive();
      if (note && active) {
        note.hidden = false;
        note.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg><span>Limited to pages matching ' + ANALYTICS_PATH_FILTER.map(p => '<code>' + esc(p) + '</code>').join(' ') + '</span>';
      }
      // The bar takes space only when it has content: the admin editor or a scope.
      if (bar && (btn || active)) bar.hidden = false;
      if (active) {
        // Sessions & engagement says nothing about the scope: the filter bar
        // right above it already names the pages, and its card tooltips are
        // deliberately one plain sentence each.
        // Demographics keeps its geography half (served page-scoped) and drops
        // age/gender, which are user-scoped in GA4 with no page to scope by —
        // so the section is geography only, and says so.
        const dtitle = document.getElementById('demoSectionTitle');
        if (dtitle) dtitle.textContent = 'Geography';
        const dpanels = document.getElementById('demoUserPanels');
        if (dpanels) dpanels.hidden = true;
        const dnote = document.getElementById('demoScopeNote');
        if (dnote) {
          dnote.hidden = false;
          dnote.textContent = 'Where the users who viewed a matching page came from. Counts come from GA4’s per-page geography, so a user who viewed more than one matching page is counted once per page — read the map as relative concentration, not a headcount. Age and gender are user-scoped in GA4 with no page to scope them by, so they’re hidden here.';
        }
      }
      if (!btn) return;
      const pop = document.getElementById('pfPop');
      const save = document.getElementById('pfSave');
      const ta = document.getElementById('pfText');
      const status = document.getElementById('pfStatus');
      const setOpen = (o) => { pop.hidden = !o; btn.setAttribute('aria-expanded', o ? 'true' : 'false'); };
      btn.addEventListener('click', (e) => { e.stopPropagation(); setOpen(pop.hidden); });
      pop.addEventListener('click', (e) => e.stopPropagation());
      pop.querySelector('.ef-pop-x').addEventListener('click', () => setOpen(false));
      document.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
      save.addEventListener('click', async () => {
        save.disabled = true; status.className = 'ef-status'; status.textContent = 'Saving…';
        try {
          const r = await fetch(ANALYTICS_PATH_FILTER_API, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filter: ta.value }),
          });
          const b = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
          status.textContent = 'Saved. Reloading…';
          setTimeout(() => window.location.reload(), 600);
        } catch (err) {
          status.className = 'ef-status err'; status.textContent = 'Save failed: ' + (err.message || err);
          save.disabled = false;
        }
      });
    })();
    // Admin "Campaigns" picker: restrict the Explorer to a chosen subset of
    // campaigns. The checklist is built (on open) from the campaigns currently
    // loaded plus any already-saved names; Save POSTs the ticked set and reloads.
    (function(){
      const btn = document.getElementById('ecEditBtn'); if (!btn) return;
      const pop = document.getElementById('ecPop');
      const list = document.getElementById('ecList');
      const search = document.getElementById('ecSearch');
      const selAll = document.getElementById('ecSelectAll');
      const clr = document.getElementById('ecClear');
      const save = document.getElementById('ecSave');
      const status = document.getElementById('ecStatus');
      const count = document.getElementById('ecCount');
      const badge = document.getElementById('ecBadge');
      const chosen = new Set(EXPLORER_CAMPAIGN_ALLOWLIST);
      if (EXPLORER_CAMPAIGN_ALLOWLIST.length) { badge.hidden=false; badge.textContent=String(EXPLORER_CAMPAIGN_ALLOWLIST.length); }
      const updateCount = () => {
        const n = list.querySelectorAll('input:checked').length;
        count.textContent = n ? (n + ' selected') : 'All campaigns';
      };
      function build(){
        // Union of currently-loaded campaign names and any already-saved ones, so
        // a saved campaign with no spend in the current range still shows (checked).
        const names = new Set();
        for (const r of explorerRows) { const n=String(r.campaign_name||''); if (n) names.add(n); }
        for (const n of chosen) names.add(n);
        const sorted = [...names].sort((a,b)=>a.localeCompare(b));
        if (!sorted.length) { list.innerHTML = '<div class="ec-empty">No campaigns loaded yet.</div>'; updateCount(); return; }
        list.innerHTML = sorted.map(n=>`<label class="ec-option"><input type="checkbox" value="${esc(n)}"${chosen.has(n)?' checked':''}><span class="ec-name" title="${esc(n)}">${esc(n)}</span></label>`).join('');
        updateCount();
      }
      const setOpen = (o) => { pop.hidden = !o; btn.setAttribute('aria-expanded', o ? 'true' : 'false'); if (o) { build(); search.value=''; status.className='ef-status'; status.textContent=''; } };
      btn.addEventListener('click', (e) => { e.stopPropagation(); setOpen(pop.hidden); });
      pop.addEventListener('click', (e) => e.stopPropagation());
      pop.querySelector('.ef-pop-x').addEventListener('click', () => setOpen(false));
      document.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
      list.addEventListener('change', updateCount);
      search.addEventListener('input', () => {
        const q = search.value.trim().toLowerCase();
        list.querySelectorAll('.ec-option').forEach(o => {
          const n = o.querySelector('.ec-name').textContent.toLowerCase();
          o.hidden = !!q && !n.includes(q);
        });
      });
      selAll.addEventListener('click', () => { list.querySelectorAll('.ec-option:not([hidden]) input').forEach(cb=>cb.checked=true); updateCount(); });
      clr.addEventListener('click', () => { list.querySelectorAll('input').forEach(cb=>cb.checked=false); updateCount(); });
      save.addEventListener('click', async () => {
        const campaigns = [...list.querySelectorAll('input:checked')].map(cb=>cb.value);
        save.disabled = true; status.className='ef-status'; status.textContent='Saving…';
        try {
          const r = await fetch(EXPLORER_CAMPAIGNS_API, {
            method:'POST', credentials:'same-origin',
            headers:{ 'Content-Type':'application/json' },
            body: JSON.stringify({ campaigns }),
          });
          const b = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
          status.className='ef-status ok'; status.textContent='Saved. Reloading…';
          setTimeout(() => window.location.reload(), 600);
        } catch (err) {
          status.className='ef-status err'; status.textContent='Save failed: ' + (err.message || err);
          save.disabled = false;
        }
      });
    })();
    const explorerFilterState = new Map(); // groupId -> Set of active chip labels
    let explorerRows = [];
    // Same-shape rows for the Compare picker's window (previous period/year),
    // fetched alongside the current period so the summary cards and table
    // total can show a "vs previous" delta like every other panel does.
    let explorerPrevRows = [];
    let verifiedByAdId = {};
    let verifiedByAdIdEvent = {};
    let verifiedByGoogleCampaignId = {};
    let verifiedByGoogleCampaignIdEvent = {};
    let verifiedByLinkedinGroup = {};
    let verifiedByLinkedinGroupEvent = {};
    let verifiedByMicrosoftCampaign = {};
    let verifiedByMicrosoftCampaignEvent = {};
    let keyEventList = [];
    // Platform conversion actions. Google and Meta report the split at ad grain
    // (keyed by ad id); Microsoft's Goals and Funnels report stops at the ad
    // group, so its map is keyed by ad group id and the ad rows underneath show
    // a dash. LinkedIn has no equivalent read at all -- its analytics pivot by
    // one dimension at a time, so a conversion pivot cannot also say which
    // campaign it belongs to -- so LinkedIn rows stay dashed too.
    let convActionList = [];
    let convByGoogleAdId = {};
    let convByMetaAdId = {};
    let convByMicrosoftGroupId = {};
    function normalizeLiName(name) { return String(name||'').replace(/\+/g,' ').replace(/\s+/g,' ').trim().toLowerCase(); }
    let selectedKeyEvent = (function(){ try { return localStorage.getItem(KE_STORAGE_KEY) || '__all__'; } catch(e) { return '__all__'; } })();
    function applyVerifiedSelection() {
      for (const r of explorerRows) {
        const id=String(r.ad_id||'');
        r.verified = num(verifiedByAdId[id]);
        r.verified_sel = (selectedKeyEvent==='__all__') ? r.verified : num((verifiedByAdIdEvent[id]||{})[selectedKeyEvent]);
      }
    }
    let selectedConvAction = (function(){ try { return localStorage.getItem(CONV_STORAGE_KEY) || '__all__'; } catch(e) { return '__all__'; } })();
    function convSelectionActive() { return selectedConvAction!=='__all__'; }
    // Resolve the selected action onto every row. With no selection the column is
    // the platform's own Conv. exactly as before -- the selector is additive, it
    // never changes the default reading of the table.
    function applyConvSelection(rows) {
      const all=!convSelectionActive();
      for (const r of rows) {
        if (all) { r.conversions_sel=num(r.conversions); r._convSelNa=false; continue; }
        const p=(r.platform||'').toLowerCase();
        if (p==='google') { r.conversions_sel=num((convByGoogleAdId[String(r.ad_id||'')]||{})[selectedConvAction]); r._convSelNa=false; }
        else if (p==='meta') { r.conversions_sel=num((convByMetaAdId[String(r.ad_id||'')]||{})[selectedConvAction]); r._convSelNa=false; }
        // Microsoft resolves at the ad-group node in buildExplorerTree; LinkedIn
        // has nothing to resolve. Both leave the ad row itself dashed.
        else { r.conversions_sel=0; r._convSelNa=true; }
      }
    }
    function convSelectHtml() {
      if (!convActionList.length) return '';
      const opts=[['__all__','All conversions'],...convActionList.map(a=>[a,a])]
        .map(([v,l])=>`<option value="${esc(v)}"${selectedConvAction===v?' selected':''}>${esc(l)}</option>`).join('');
      const target='<svg class="ke-select-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="2.4"/></svg>';
      return `<span class="ke-select-wrap cv${convSelectionActive()?' active':''}">${target}<select class="cv-select" title="Show platform-reported conversions for a single conversion action">${opts}</select><span class="ke-select-caret" aria-hidden="true">&#9662;</span></span>`;
    }
    function keSelectHtml() {
      if (!keyEventList.length) return '';
      const opts=[['__all__','All key events'],...keyEventList.map(e=>[e,e])]
        .map(([v,l])=>`<option value="${esc(v)}"${selectedKeyEvent===v?' selected':''}>${esc(l)}</option>`).join('');
      const active = selectedKeyEvent!=='__all__';
      const funnel = '<svg class="ke-select-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 3h12l-4.5 5.5V13L6.5 11.5V8.5z"/></svg>';
      return `<span class="ke-select-wrap${active?' active':''}">${funnel}<select class="ke-select" title="Show GA4-verified conversions for a single key event">${opts}</select><span class="ke-select-caret" aria-hidden="true">▾</span></span>`;
    }

    function buildChips(container, keys, stateSet, onChange) {
      const el = typeof container==='string' ? document.getElementById(container) : container;
      el.innerHTML = ['All',...keys].map(k=>`<button type="button" class="chip" data-key="${esc(k)}">${esc(k)}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {
        const key=btn.dataset.key;
        if (key==='All') stateSet.clear(); else if (stateSet.has(key)) stateSet.delete(key); else stateSet.add(key);
        el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
        (onChange||renderExplorer)();
      }));
      el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
    }
    // Explorer filter groups (Business line / Product / Region …) render as
    // space-saving dropdowns in the sticky top bar, each holding a multi-select
    // checkbox list. Reuses the .ke-dd-* dropdown styling.
    function closeAllExplDropdowns(except) {
      document.querySelectorAll('#explorerFilterBar .expl-dd').forEach(dd => {
        if (dd===except) return;
        const p=dd.querySelector('.ke-dd-panel'); if (p) p.hidden=true;
        dd.classList.remove('open');
        const t=dd.querySelector('.ke-dd-toggle'); if (t) t.setAttribute('aria-expanded','false');
      });
    }
    function wireExplorerDropdown(dd, g, set) {
      const toggle=dd.querySelector('.ke-dd-toggle');
      const panel=dd.querySelector('.ke-dd-panel');
      const list=dd.querySelector('.ke-dd-list');
      const label=dd.querySelector('.expl-dd-label');
      const updateLabel=()=>{ label.textContent = set.size ? `${label.dataset.base} · ${set.size}` : label.dataset.base; toggle.classList.toggle('has-active', set.size>0); };
      list.innerHTML = g.chips.map(c=>`<label class="ke-dd-option${set.has(c.label)?' active':''}"><input type="checkbox"${set.has(c.label)?' checked':''} data-val="${esc(c.label)}"><span class="ke-dd-name">${esc(c.label)}</span></label>`).join('');
      list.querySelectorAll('input[data-val]').forEach(cb=>cb.addEventListener('change',()=>{
        const v=cb.dataset.val;
        if (set.has(v)) set.delete(v); else set.add(v);
        const opt=cb.closest('.ke-dd-option'); if (opt) opt.classList.toggle('active', set.has(v));
        updateLabel();
        renderExplorer();
      }));
      toggle.addEventListener('click', e=>{
        e.stopPropagation();
        if (panel.hidden) { closeAllExplDropdowns(dd); panel.hidden=false; dd.classList.add('open'); toggle.setAttribute('aria-expanded','true'); }
        else { panel.hidden=true; dd.classList.remove('open'); toggle.setAttribute('aria-expanded','false'); }
      });
      updateLabel();
    }
    function buildExplorerFilters() {
      const host = document.getElementById('explorerFilterBar');
      if (!host) return;
      explorerFilterState.clear();
      if (!EXPLORER_FILTER_GROUPS.length) { host.innerHTML=''; host.hidden=true; return; }
      host.innerHTML = EXPLORER_FILTER_GROUPS.map((g,i) =>
        `<div class="ke-dropdown expl-dd" data-group="${i}">`+
          `<button type="button" class="ke-dd-toggle" aria-haspopup="listbox" aria-expanded="false">`+
            `<span class="expl-dd-label" data-base="${esc(g.label)}">${esc(g.label)}</span>`+
            `<span class="ke-dd-caret">▾</span>`+
          `</button>`+
          `<div class="ke-dd-panel" hidden><div class="ke-dd-list"></div></div>`+
        `</div>`
      ).join('');
      EXPLORER_FILTER_GROUPS.forEach((g,i) => {
        const set = new Set();
        explorerFilterState.set(g.id, set);
        wireExplorerDropdown(host.querySelector(`.expl-dd[data-group="${i}"]`), g, set);
      });
    }
    document.addEventListener('click', e=>{ if (!e.target.closest('#explorerFilterBar .expl-dd')) closeAllExplDropdowns(); });
    document.addEventListener('keydown', e=>{ if (e.key==='Escape') closeAllExplDropdowns(); });
    function explorerRowMatches(row) {
      const name=String(row.campaign_name||'').toLowerCase();
      for (const g of EXPLORER_FILTER_GROUPS) {
        const set=explorerFilterState.get(g.id);
        if (!set||!set.size) continue;
        const ok=g.chips.some(c=>set.has(c.label)&&c.phrases.some(p=>p&&name.includes(p)));
        if (!ok) return false;
      }
      const platOk=!platformFilter.size||[...platformFilter].some(k=>k.toLowerCase()===(row.platform||''));
      return platOk;
    }
    function zeroMetrics() { return {spend:0,impressions:0,clicks:0,conversions:0,conversions_sel:0,verified:0,verified_sel:0}; }
    function addMetrics(acc,r) { acc.spend+=num(r.spend);acc.impressions+=num(r.impressions);acc.clicks+=num(r.clicks);acc.conversions+=num(r.conversions);acc.conversions_sel+=num(r.conversions_sel);acc.verified+=num(r.verified);acc.verified_sel+=num(r.verified_sel); }
    function withCtr(m) { return {...m,ctr:m.impressions?(num(m.clicks)/num(m.impressions)*100):0}; }
    function buildExplorerTree(rows) {
      const campaigns=new Map();
      for (const r of rows) {
        const cName=r.campaign_name||'—', platform=(r.platform||'google').toLowerCase(), cKey=platform+'|'+cName;
        if (!campaigns.has(cKey)) campaigns.set(cKey,{name:cName,platform,campaign_id:r.campaign_id||'',metrics:zeroMetrics(),groups:new Map()});
        const camp=campaigns.get(cKey);
        addMetrics(camp.metrics,r);
        const gName=r.ad_group_name||'—';
        if (!camp.groups.has(gName)) camp.groups.set(gName,{name:gName,ad_group_id:'',metrics:zeroMetrics(),ads:[]});
        const grp=camp.groups.get(gName);
        if (!grp.ad_group_id && r.ad_group_id) grp.ad_group_id=String(r.ad_group_id);
        if (r._verifiedNa) grp.metrics._verifiedNa=true;
        addMetrics(grp.metrics,r);
        grp.ads.push(r);
      }
      // Sort every level by the active column / direction.
      const {key,dir}=explorerSort, mul=dir==='asc'?1:-1;
      const cmpName=(x,y)=>mul*String(x).localeCompare(String(y),undefined,{numeric:true});
      const cmpMetric=(x,y)=>mul*(explorerMetricVal(x,key)-explorerMetricVal(y,key));
      const cmpNode=(a,b)=> key==='name' ? cmpName(a[1].name,b[1].name) : cmpMetric(a[1].metrics,b[1].metrics);
      // No verified data yet (table not synced) -> show "—", not a misleading 0.
      const gHasData = Object.keys(verifiedByGoogleCampaignId).length > 0;
      const lHasData = Object.keys(verifiedByLinkedinGroup).length > 0;
      const mHasData = Object.keys(verifiedByMicrosoftCampaign).length > 0;
      for (const camp of campaigns.values()) {
        // Google verified is a campaign-level number (native GA4 link); attach it
        // to the campaign node — sub-levels stay "—" (no reliable per-ad id).
        if (camp.platform==='google') {
          if (gHasData && camp.campaign_id) {
            const cid=String(camp.campaign_id);
            const gv=num(verifiedByGoogleCampaignId[cid]);
            camp.metrics.verified=gv;
            camp.metrics.verified_sel=(selectedKeyEvent==='__all__')
              ? gv
              : num((verifiedByGoogleCampaignIdEvent[cid]||{})[selectedKeyEvent]);
          } else {
            camp.metrics._verifiedNa=true;
          }
        }
        // LinkedIn verified is campaign-group-level, matched by normalized name
        // (no ids in GA4). Sub-levels (ad set / creative) stay "—".
        else if (camp.platform==='linkedin') {
          if (lHasData) {
            const gname=normalizeLiName(camp.name);
            const lv=num(verifiedByLinkedinGroup[gname]);
            camp.metrics.verified=lv;
            camp.metrics.verified_sel=(selectedKeyEvent==='__all__')
              ? lv
              : num((verifiedByLinkedinGroupEvent[gname]||{})[selectedKeyEvent]);
          } else {
            camp.metrics._verifiedNa=true;
          }
        }
        // Microsoft/Bing verified is campaign-level, matched by normalized name
        // (no native GA4 link, like LinkedIn). Sub-levels (ad group / ad) stay
        // "—" via the row-level _verifiedNa set above.
        else if (camp.platform==='microsoft') {
          if (mHasData) {
            const mname=normalizeLiName(camp.name);
            const mv=num(verifiedByMicrosoftCampaign[mname]);
            camp.metrics.verified=mv;
            camp.metrics.verified_sel=(selectedKeyEvent==='__all__')
              ? mv
              : num((verifiedByMicrosoftCampaignEvent[mname]||{})[selectedKeyEvent]);
          } else {
            camp.metrics._verifiedNa=true;
          }
        }
        for (const grp of camp.groups.values()) {
          grp.ads.sort((a,b)=> key==='name' ? cmpName(explorerAdName(a),explorerAdName(b)) : cmpMetric(a,b));
        }
        // Selected conversion action, resolved per level. Microsoft only
        // reports it at ad-group grain, so the value lands on the group node
        // and the ads below keep their dash; every other platform already
        // summed correctly from the ad rows. The campaign is then re-totalled
        // from its groups so it is always exactly the sum of the rows shown
        // under it, whichever grain those rows came from.
        if (convSelectionActive()) {
          if (camp.platform==='microsoft') {
            for (const grp of camp.groups.values()) {
              const gid=String(grp.ad_group_id||'');
              const hit=gid ? convByMicrosoftGroupId[gid] : null;
              if (hit) { grp.metrics.conversions_sel=num(hit[selectedConvAction]); grp.metrics._convSelNa=false; }
              else { grp.metrics._convSelNa=true; }
            }
          } else if (camp.platform==='linkedin') {
            for (const grp of camp.groups.values()) grp.metrics._convSelNa=true;
          }
          let campSel=0, anyGroup=false;
          for (const grp of camp.groups.values()) {
            if (grp.metrics._convSelNa) continue;
            anyGroup=true; campSel+=num(grp.metrics.conversions_sel);
          }
          camp.metrics.conversions_sel=campSel;
          camp.metrics._convSelNa=!anyGroup;
        }
        camp.groups=new Map([...camp.groups.entries()].sort(cmpNode));
      }
      return new Map([...campaigns.entries()].sort(cmpNode));
    }
    // Each metric cell, with an optional "vs previous" delta chip underneath
    // when a matching comparison-period aggregate (prevM) is passed -- used
    // for campaign rows and the tree footer's grand total alike; ad-group and
    // ad rows call this with prevM omitted and get plain cells.
    function metricCells(m, prevM) {
      const wc=withCtr(m), prevWc=prevM?withCtr(prevM):null;
      return metricCols().map(c=>{
        if (c.key==='verified_sel') { const cell=m._verifiedNa?'—':c.format(wc[c.key]); return `<td${c.cls?` class="${c.cls}"`:''}>${cell}</td>`; }
        // A selected conversion action has no comparison-window figure behind
        // it (the breakdown is only fetched for the current window), so the
        // vs-previous chip is dropped rather than compared against the
        // unfiltered total, which would read as a collapse.
        if (c.key==='conversions' && convSelectionActive()) {
          const cell=m._convSelNa?'—':c.format(num(m.conversions_sel));
          return `<td${c.cls?` class="${c.cls}"`:''}>${cell}</td>`;
        }
        const cell=c.format(wc[c.key]);
        const delta=prevWc?summaryDeltaHtml(wc[c.key],prevWc[c.key],EXPLORER_METRIC_DIR[c.key]):'';
        return `<td${c.cls?` class="${c.cls}"`:''}>${cell}${delta?`<div class="expl-row-delta">${delta}</div>`:''}</td>`;
      }).join('');
    }
    // Grand total for the tree footer. Summed from the campaign nodes rather than
    // the raw rows so the footer is always exactly the sum of the campaign rows
    // above it — including verified conversions, which are resolved per campaign
    // (Google by campaign id, LinkedIn by group name) and would otherwise be
    // double counted or missed. CTR is recomputed from total clicks/impressions,
    // never averaged. Verified stays "—" only when no campaign in view has data.
    function explorerTotals(tree) {
      const t=zeroMetrics();
      let anyVerified=false, anyConvSel=false;
      t.conversions_sel=0;
      for (const camp of tree.values()) {
        addMetrics(t, camp.metrics);
        if (!camp.metrics._verifiedNa) anyVerified=true;
        if (!camp.metrics._convSelNa) anyConvSel=true;
      }
      if (!anyVerified) t._verifiedNa=true;
      // The total is the sum of the campaigns that could answer, and says so
      // with a dash only when none of them could. Read only while an action is
      // selected -- with none selected no campaign is ever flagged.
      if (!anyConvSel) t._convSelNa=true;
      return t;
    }
    // Brand marks for the platform column — inline SVG so the tree reads as a
    // sleek icon rail instead of text pills. Google = 4-colour G, LinkedIn +
    // Meta = their single-path glyphs in brand colours.
    const PLATFORM_SVG = {
      google: '<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>',
      linkedin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#0A66C2" d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.55V9h3.57v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.72V1.72C24 .77 23.2 0 22.22 0z"/></svg>',
      meta: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#0668E1" d="M6.915 4.03c-1.968 0-3.683 1.28-4.871 3.113C.704 9.208 0 11.883 0 14.449c0 .706.07 1.369.21 1.973a6.624 6.624 0 0 0 .265.86 5.297 5.297 0 0 0 .371.761c.696 1.159 1.818 1.927 3.593 1.927 1.497 0 2.633-.671 3.965-2.444.76-1.012 1.144-1.626 2.663-4.32l.756-1.339.186-.325c.061.1.121.196.183.294l2.152 3.595c.724 1.21 1.665 2.556 2.47 3.314 1.046.987 1.992 1.22 3.06 1.22 1.075 0 1.876-.355 2.455-.843a3.743 3.743 0 0 0 .81-.973c.542-.939.861-2.127.861-3.745 0-2.72-.681-5.357-2.084-7.45-1.282-1.912-2.957-2.93-4.716-2.93-1.047 0-2.088.467-3.053 1.308-.652.57-1.257 1.29-1.82 2.05-.69-.875-1.335-1.547-1.958-2.056-1.182-.966-2.315-1.303-3.454-1.303zm10.16 2.053c1.147 0 2.188.758 2.992 1.999 1.132 1.748 1.647 4.195 1.647 6.4 0 1.548-.368 2.9-1.839 2.9-.58 0-1.027-.235-1.664-1.001-.496-.601-1.343-1.878-2.832-4.358l-.617-1.028a44.908 44.908 0 0 0-1.255-1.98c.07-.109.141-.224.211-.327 1.12-1.667 2.118-2.605 3.325-2.605zm-10.201.553c1.265 0 2.058.791 2.675 1.446.307.327.737.871 1.234 1.579l-1.02 1.566c-.757 1.163-1.882 3.017-2.837 4.338-1.191 1.649-1.81 1.817-2.486 1.817-.524 0-1.038-.237-1.383-.794-.263-.426-.464-1.13-.464-2.046 0-2.221.63-4.535 1.66-6.088.454-.687.964-1.226 1.533-1.533a2.264 2.264 0 0 1 1.088-.282z"/></svg>',
      microsoft: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#F25022" d="M1 1h10.4v10.4H1z"/><path fill="#7FBA00" d="M12.6 1H23v10.4H12.6z"/><path fill="#00A4EF" d="M1 12.6h10.4V23H1z"/><path fill="#FFB900" d="M12.6 12.6H23V23H12.6z"/></svg>',
    };
    function platformIcon(p) {
      const k=(p||'google').toLowerCase();
      const key=k==='linkedin'?'linkedin':k==='meta'?'meta':k==='microsoft'?'microsoft':'google';
      const label=key==='linkedin'?'LinkedIn':key==='meta'?'Meta':key==='microsoft'?'Microsoft Ads':'Google';
      return `<span class="plat-ico plat-ico-${key}" title="${label}" aria-label="${label}">${PLATFORM_SVG[key]}</span>`;
    }
    function parseCopyList(v) {
      if (Array.isArray(v)) return v.filter(Boolean);
      if (typeof v==='string' && v) { try { const a=JSON.parse(v); return Array.isArray(a)?a.filter(Boolean):[]; } catch(e) { return []; } }
      return [];
    }
    const HEADLINES_VISIBLE = 5;
    const ICON_SHUFFLE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';
    // Turn a final URL into a Google-style display path (host + first segment).
    function gadsDisplayUrl(ad) {
      const raw=String(ad.final_url||'').trim();
      try {
        const u=new URL(raw.match(/^https?:\/\//)?raw:('https://'+raw));
        const seg=u.pathname.split('/').filter(Boolean)[0];
        return u.hostname.replace(/^www\./,'')+(seg?('/'+seg):'');
      } catch(e) {
        return (raw||'example.com').replace(/^https?:\/\//,'').replace(/^www\./,'').split('/').slice(0,2).join('/');
      }
    }
    // Fisher–Yates pick of n items — how the shuffle randomizes the RSA mix.
    function gadsPick(arr,n) {
      const a=arr.slice();
      for (let i=a.length-1;i>0;i--) { const j=Math.floor(Math.random()*(i+1)); const t=a[i]; a[i]=a[j]; a[j]=t; }
      return a.slice(0,n);
    }
    // Re-render a Google preview's shown assets (up to 3 headlines, 2 descriptions).
    // shuffle=true randomizes the mix like Google's RSA serving would.
    function gadsUpdatePreview(cell, shuffle) {
      let hs=[], ds=[];
      try { hs=JSON.parse(cell.dataset.hs||'[]'); } catch(e) {}
      try { ds=JSON.parse(cell.dataset.ds||'[]'); } catch(e) {}
      const h=shuffle?gadsPick(hs,3):hs.slice(0,3);
      const d=shuffle?gadsPick(ds,2):ds.slice(0,2);
      const t=cell.querySelector('.gads-title'); if (t) t.textContent=h.join(' | ');
      const de=cell.querySelector('.gads-desc'); if (de) de.textContent=d.join(' ');
    }
    // Google search RSA → a realistic ad preview (3 headlines / 2 descriptions),
    // a shuffle button that re-rolls the mix, and the full asset list in an
    // accordion. Non-search / image ads fall through to the thumbnail layout.
    function googleAdCell(ad, hs, ds) {
      const sub=ad.ad_id?`<span class="ad-label-sub"><span class="ad-id">#${esc(ad.ad_id)}</span></span>`:'';
      const disp=esc(gadsDisplayUrl(ad));
      const h0=hs.slice(0,3).map(esc).join(' | ');
      const d0=ds.slice(0,2).map(esc).join(' ');
      const allH=hs.map((v,i)=>`<span class="ad-copy-line"><span class="ad-copy-tag ad-copy-tag--h">H${i+1}</span>${esc(v)}</span>`).join('');
      const allD=ds.map((v,i)=>`<span class="ad-copy-line"><span class="ad-copy-tag ad-copy-tag--d">D${i+1}</span>${esc(v)}</span>`).join('');
      const cnt=`${hs.length} headline${hs.length===1?'':'s'} · ${ds.length} description${ds.length===1?'':'s'}`;
      const acc=(hs.length>3||ds.length>2)
        ? `<button type="button" class="ad-copy-more" data-more-label="All assets (${cnt})">All assets (${cnt})</button><div class="ad-copy-extra" hidden>${allH}${allD}</div>`
        : '';
      return `<div class="ad-cell gads" data-hs="${esc(JSON.stringify(hs))}" data-ds="${esc(JSON.stringify(ds))}"><span class="ad-meta">
        <div class="gads-preview">
          <div class="gads-top"><span class="gads-badge">Ad</span><span class="gads-url">${disp}</span><button type="button" class="gads-shuffle" title="Shuffle asset mix" aria-label="Shuffle asset mix">${ICON_SHUFFLE}</button></div>
          <div class="gads-title">${h0}</div>
          <div class="gads-desc">${d0}</div>
        </div>
        ${sub}
        ${acc}
      </span></div>`;
    }
    function adCell(ad) {
      const platform=(ad.platform||'google').toLowerCase();
      // Full RSA copy (up to 15 headlines / 4 descriptions) from the JSON arrays;
      // fall back to the legacy flat columns for rows synced before the repull.
      let hs=parseCopyList(ad.headlines); if(!hs.length) hs=[ad.headline_1,ad.headline_2,ad.headline_3].filter(Boolean);
      let ds=parseCopyList(ad.descriptions); if(!ds.length) ds=[ad.description_1,ad.description_2].filter(Boolean);
      // Google search ads (text/RSA) get the true ad preview; image/display and
      // LinkedIn/Meta creatives keep the thumbnail-based layout below.
      if (platform==='google' && (hs.length||ds.length) && !ad.thumbnail_url) {
        return googleAdCell(ad, hs, ds);
      }
      const type=ad.media_type?`<span class="ad-type">${esc(ad.media_type)}</span>`:'';
      // Full-size preview prefers image_url/video_url over the (often smaller/
      // cropped) thumbnail_url; click opens it in the modal (see creativePreview).
      const fullImg=ad.image_url||ad.thumbnail_url||'';
      const thumb=ad.thumbnail_url?`<img class="ad-thumb" src="${esc(ad.thumbnail_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'" data-preview-image="${esc(fullImg)}" data-preview-video="${esc(ad.video_url||'')}">` :'';
      // ad_name is often blank for RSAs; prefer it, then the first headline, then
      // the raw ad ID as a last resort (shown small/muted, not as the headline label).
      const label=esc(ad.ad_name || hs[0] || ad.ad_label || '—');
      const idTag=ad.ad_id?`<span class="ad-id">#${esc(ad.ad_id)}</span>`:'';
      const visible=hs.slice(0, HEADLINES_VISIBLE), extra=hs.slice(HEADLINES_VISIBLE);
      const visibleLines=visible.map((v,i)=>['H'+(i+1),v]);
      const extraLines=extra.map((v,i)=>['H'+(i+1+HEADLINES_VISIBLE),v]);
      const descLines=ds.map((v,i)=>['D'+(i+1),v]);
      const line=([tag,v])=>`<span class="ad-copy-line"><span class="ad-copy-tag ad-copy-tag--${(tag[0]||'').toLowerCase()}">${tag}</span>${esc(v)}</span>`;
      const visibleHtml=visibleLines.filter(([,v])=>v).map(line).join('');
      const extraHtml=extraLines.filter(([,v])=>v).map(line).join('');
      const descHtml=descLines.filter(([,v])=>v).map(line).join('');
      const more=extra.length
        ? `<button type="button" class="ad-copy-more" data-more-label="+${extra.length} more">+${extra.length} more</button><div class="ad-copy-extra" hidden>${extraHtml}</div>`
        : '';
      const copyLines=visibleHtml+more+descHtml;
      const copy=copyLines?`<div class="ad-copy">${copyLines}</div>`:'';
      return `<div class="ad-cell">${thumb}<span class="ad-meta"><span class="ad-label">${label}${idTag}</span>${type}${copy}</span></div>`;
    }
    // Sleek child-count indicator: up to 5 dots represent the count at a glance
    // (a "+" marks overflow). The exact count + unit ("3 ad groups") stays on
    // the hover title rather than as inline text next to the row name.
    function treeCount(n, unit) {
      const cap=Math.min(n, 5);
      let dots='';
      for (let i=0;i<cap;i++) dots+='<span class="tc-dot"></span>';
      if (n>5) dots+='<span class="tc-dot tc-more">+</span>';
      const label=esc(unit)+(n===1?'':'s');
      return `<span class="tree-count" title="${n} ${label}" aria-label="${n} ${label}">`
        + `<span class="tc-dots" aria-hidden="true">${dots}</span></span>`;
    }
    // Campaign allowlist: when an admin has scoped this portal to specific
    // campaigns, everything the Explorer shows (table + summary cards + counts)
    // is limited to that set. Empty allowlist = no restriction.
    const _campaignAllowSet = new Set(EXPLORER_CAMPAIGN_ALLOWLIST);
    function explorerAllowedRows() {
      if (!_campaignAllowSet.size) return explorerRows;
      return explorerRows.filter(r => _campaignAllowSet.has(String(r.campaign_name||'')));
    }
    function explorerPrevAllowedRows() {
      if (!_campaignAllowSet.size) return explorerPrevRows;
      return explorerPrevRows.filter(r => _campaignAllowSet.has(String(r.campaign_name||'')));
    }
    // Direction each explorer metric moves in that counts as "good," for the
    // vs-previous delta coloring — same convention as SUMMARY_CARDS above.
    // Verified conv. isn't included: it's stitched together from separate
    // per-platform verified-conversions calls that aren't fetched for the
    // comparison window (yet), so it has nothing to diff against.
    // Which direction is "good" per metric, for colouring row deltas. Spend and
    // CTR are both 'neutral' -- see SUMMARY_CARDS for why a CTR dip is not
    // automatically bad -- so neither ever colours red or green on its own.
    const EXPLORER_METRIC_DIR = { spend:'neutral', impressions:'up', clicks:'up', ctr:'neutral', conversions:'up' };
    function renderExplorer() {
      const base=explorerAllowedRows();
      const filtered=base.filter(explorerRowMatches);
      // Aggregate summary cards — slice with the same filters as the table
      // (date range, Platform chips, and the explorer filter chips).
      const agg=withCtr(filtered.reduce((a,r)=>{addMetrics(a,r);return a;}, zeroMetrics()));
      // Same aggregation over the Compare picker's window (previous period/year),
      // sliced with the same filters, so cards and the table total can show a
      // "vs previous" delta consistent with the rest of the dashboard.
      const prevFiltered = compareStart ? explorerPrevAllowedRows().filter(explorerRowMatches) : null;
      const aggPrev = prevFiltered ? withCtr(prevFiltered.reduce((a,r)=>{addMetrics(a,r);return a;}, zeroMetrics())) : null;
      // Same tree, built from the comparison window, so each campaign row can
      // show a "vs previous" delta alongside the grand total's.
      const prevTree = prevFiltered ? buildExplorerTree(prevFiltered) : null;
      // Google verified is campaign-level (not on rows), so add it once per distinct
      // Google campaign in the filtered set for the summary total.
      const gcSeen=new Set(); let googleVerifiedTotal=0;
      for (const r of filtered) { const cid=String(r.campaign_id||''); if (r.platform==='google' && cid && !gcSeen.has(cid)) { gcSeen.add(cid); googleVerifiedTotal+=num(verifiedByGoogleCampaignId[cid]); } }
      // LinkedIn verified is campaign-group-level (name-matched); add once per group.
      const liSeen=new Set(); let linkedinVerifiedTotal=0;
      for (const r of filtered) { if (r.platform==='linkedin') { const gn=normalizeLiName(r.campaign_name||''); if (gn && !liSeen.has(gn)) { liSeen.add(gn); linkedinVerifiedTotal+=num(verifiedByLinkedinGroup[gn]); } } }
      // Microsoft verified is campaign-level (name-matched); add once per campaign.
      const msSeen=new Set(); let microsoftVerifiedTotal=0;
      for (const r of filtered) { if (r.platform==='microsoft') { const mn=normalizeLiName(r.campaign_name||''); if (mn && !msSeen.has(mn)) { msSeen.add(mn); microsoftVerifiedTotal+=num(verifiedByMicrosoftCampaign[mn]); } } }
      const el=document.getElementById('explorerTable');
      const tree=buildExplorerTree(filtered);
      // The Conversions card follows the column's selector, and it is totalled
      // from the tree rather than the raw rows: Microsoft resolves its split at
      // the ad-group node, so summing rows would report zero for it.
      const treeTotals=explorerTotals(tree);
      const convActive=convSelectionActive();
      const scards=document.getElementById('explorerSummaryCards');
      // A card without a matching chart metric (Verified conv. hidden for this
      // client) can't stay selected -- drop it, and fall back to Spend rather
      // than leave the chart with nothing to plot.
      if (!showVerifiedConv) explorerTrendMetrics.delete('verified');
      if (!explorerTrendMetrics.size) explorerTrendMetrics.add('spend');
      if (scards) {
        scards.innerHTML=[
          ['spend','Spend',v=>money(v)],['impressions','Impressions',v=>count(v)],['clicks','Clicks',v=>count(v)],['ctr','CTR',v=>num(v).toFixed(2)+'%'],
          ['conversions','Conversions',v=>count(v)],
        ].map(([k,l,fmt])=>{
          const active=explorerTrendMetrics.has(k);
          if (k==='conversions' && convActive) {
            const val=treeTotals._convSelNa?'—':count(num(treeTotals.conversions_sel));
            return `<button type="button" class="card metric-card${active?' active':''}" data-metric="${k}" aria-pressed="${active?'true':'false'}"><div class="card-title">${esc(selectedConvAction)}</div><div class="card-value">${val}</div><div class="card-foot"><span class="cmp-delta flat">of ${count(agg.conversions)} conversions</span></div></button>`;
          }
          const delta=aggPrev?summaryDeltaHtml(agg[k],aggPrev[k],EXPLORER_METRIC_DIR[k]):'';
          return `<button type="button" class="card metric-card${active?' active':''}" data-metric="${k}" aria-pressed="${active?'true':'false'}"><div class="card-title">${l}</div><div class="card-value">${fmt(agg[k])}</div>${delta?`<div class="card-foot">${delta}</div>`:''}</button>`;
        }).join('') + (showVerifiedConv ? (()=>{
          const active=explorerTrendMetrics.has('verified');
          return `<button type="button" class="card metric-card${active?' active':''}" data-metric="verified" aria-pressed="${active?'true':'false'}"><div class="card-title">Verified conv. (GA4)</div><div class="card-value">${count(num(agg.verified)+googleVerifiedTotal+linkedinVerifiedTotal+microsoftVerifiedTotal)}</div></button>`;
        })() : '');
        // Multi-select, like the paid-trends metric chips: a card toggles its
        // metric on or off instead of replacing the selection, so Spend and
        // Clicks can be read against each other on one chart.
        scards.querySelectorAll('.metric-card').forEach(btn=>btn.addEventListener('click',()=>{
          const k=btn.dataset.metric;
          // Last one standing stays on -- an empty selection has nothing to plot.
          if (explorerTrendMetrics.has(k)) { if (explorerTrendMetrics.size===1) return; explorerTrendMetrics.delete(k); }
          else explorerTrendMetrics.add(k);
          scards.querySelectorAll('.metric-card').forEach(b=>{
            const a=explorerTrendMetrics.has(b.dataset.metric);
            b.classList.toggle('active',a);
            b.setAttribute('aria-pressed', a?'true':'false');
          });
          renderExplorerTrend();
        }));
      }
      if (!tree.size) { el.innerHTML=`<tbody><tr><td class="empty">No campaigns match these filters.</td></tr></tbody>`; } else {
        const sArrow=k=>explorerSort.key===k?(explorerSort.dir==='asc'?' ▲':' ▼'):'';
        const convSel=convSelectHtml();
        const thInner=c=>c.keSelect
          ? `<span class="ga4-head"><span class="ga4-head-top"><span class="ga4-badge">GA4</span><span class="ga4-head-label">${esc(c.label)}</span>${sArrow(c.key)}</span>${keSelectHtml()}</span>`
          : (c.convSelect && convSel)
          ? `<span class="cv-head"><span class="cv-head-top">${esc(c.label)}${sArrow(c.key)}</span>${convSel}</span>`
          : `${esc(c.label)}${sArrow(c.key)}`;
        const head=`<thead><tr><th class="left expl-sort${explorerSort.key==='name'?' active':''}" data-key="name">Campaign / Ad group / Ad${sArrow('name')}</th>${metricCols().map(c=>`<th class="expl-sort${c.cls?' '+c.cls:''}${explorerSort.key===c.key?' active':''}" data-key="${c.key}"${c.title?` title="${esc(c.title)}"`:''}>${thInner(c)}</th>`).join('')}</tr></thead>`;
        let body='', cIdx=0;
        for (const camp of tree.values()) {
          const cId='c'+(cIdx++), gCount=camp.groups.size;
          // Match this campaign into the comparison-window tree by the same
          // platform+name key buildExplorerTree groups on. A campaign with no
          // matching prior-period rows still gets zeroed metrics (rather than
          // no comparison at all) so its delta reads as "—", same as the total.
          const prevCamp = prevTree ? (prevTree.get(camp.platform+'|'+camp.name) || {metrics: zeroMetrics()}) : null;
          body+=`<tr class="tree-row lvl-campaign" data-id="${cId}" data-expandable="1"><td class="left"><span class="caret"></span>${platformIcon(camp.platform)}<span class="tree-name">${esc(camp.name)}</span>${treeCount(gCount,'ad group')}</td>${metricCells(camp.metrics, prevCamp?prevCamp.metrics:null)}</tr>`;
          let gIdx=0;
          for (const grp of camp.groups.values()) {
            const gId=cId+'g'+(gIdx++), aCount=grp.ads.length;
            body+=`<tr class="tree-row lvl-group" data-id="${gId}" data-parent="${cId}" data-expandable="1" hidden><td class="left"><span class="indent1"></span><span class="caret"></span><span class="tree-name">${esc(grp.name)}</span>${treeCount(aCount,'ad')}</td>${metricCells(grp.metrics)}</tr>`;
            for (const ad of grp.ads) { body+=`<tr class="tree-row lvl-ad" data-parent="${gId}" hidden><td class="left"><span class="indent2"></span>${adCell(ad)}</td>${metricCells(ad)}</tr>`; }
          }
        }
        // Grand total pinned under the tree. It sums whatever is currently in
        // view, so a platform chip, a filter dropdown, the campaign allowlist or
        // a new date range all re-total it on the next render.
        const totals=explorerTotals(tree);
        const nCamp=tree.size;
        const foot=`<tfoot><tr class="expl-total"><td class="left"><span class="tree-name">Total</span><span class="tot-sub">${nCamp} campaign${nCamp===1?'':'s'}</span></td>${metricCells(totals,aggPrev)}</tr></tfoot>`;
        el.innerHTML=head+`<tbody>${body}</tbody>`+foot;
      }
      const filterActive=[...explorerFilterState.values()].some(s=>s.size);
      const totalCampaigns=new Set(base.map(r=>r.campaign_name||'—')).size;
      setStatus('explorerStatus', base.length
        ? (filterActive ? `${tree.size} of ${totalCampaigns} campaign(s)` : `${tree.size} campaign(s) · ${base.length} ads`)
        : 'No campaigns found');
      // The keyword table and the paid trend chart are slices of the same
      // window and the same Platform / campaign filters, so every path that
      // re-renders the tree (a chip, a dropdown, a new range) re-renders them
      // too — otherwise they sit there contradicting the table above them.
      if (kwAllRows.length) renderKeywords();
      explorerTrendKeys = explorerTrendFilterKeys(filtered);
      renderExplorerTrend();
    }
    function toggleExplorerRow(row) {
      const id=row.dataset.id, table=row.closest('table'), expanded=row.classList.toggle('open');
      if (expanded) { table.querySelectorAll(`tr[data-parent="${id}"]`).forEach(c=>{c.hidden=false;}); }
      else {
        const stack=[id];
        while (stack.length) { const pid=stack.pop(); table.querySelectorAll(`tr[data-parent="${pid}"]`).forEach(c=>{c.hidden=true;c.classList.remove('open');if(c.dataset.id)stack.push(c.dataset.id);}); }
      }
    }
    function normalizeExplorerRows(google, linkedin, meta, microsoft) {
      const out=[];
      for (const r of (google&&google.rows?google.rows:[])) {
        out.push({platform:'google',campaign_id:r.campaign_id,campaign_name:r.campaign_name,ad_group_name:r.ad_group_name,ad_label:r.ad_label,ad_id:r.ad_id,headlines:r.headlines,descriptions:r.descriptions,headline_1:r.headline_1,headline_2:r.headline_2,headline_3:r.headline_3,description_1:r.description_1,description_2:r.description_2,ad_name:r.ad_name,final_url:r.final_url,ad_type:r.ad_type,thumbnail_url:'',media_type:r.ad_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),_verifiedNa:true});
      }
      for (const r of (linkedin&&linkedin.rows?linkedin.rows:[])) {
        out.push({platform:'linkedin',campaign_name:r.campaign_group_name||r.campaign_name,ad_group_name:r.campaign_name,ad_label:r.creative_name,thumbnail_url:r.thumbnail_url||r.image_url||'',image_url:r.image_url||'',video_url:r.video_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),_verifiedNa:true});
      }
      for (const r of (meta&&meta.rows?meta.rows:[])) {
        out.push({platform:'meta',campaign_name:r.campaign_name,ad_group_name:r.adset_name,ad_label:r.ad_name,ad_id:r.ad_id,thumbnail_url:r.thumbnail_url||r.image_url||'',image_url:r.image_url||'',video_url:r.video_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)});
      }
      // Microsoft: campaign → ad group → ad (with served ad copy), like Google
      // text ads. The served title parts / descriptions map onto the same
      // headline_1..3 / description_1..2 fields adCell renders. Before ad-level
      // data syncs, the rows are campaign-grain (ad fields blank) and collapse to
      // a single campaign node. _verifiedNa here dashes the ad group / ad rows;
      // buildExplorerTree resolves the real campaign-level verified figure by
      // name match, same as LinkedIn.
      for (const r of (microsoft&&microsoft.rows?microsoft.rows:[])) {
        // Prefer the full RSA asset lists (JSON from Campaign Management); fall
        // back to the served title parts when creative copy hasn't synced.
        const heads=r.headlines||[r.title_part_1,r.title_part_2,r.title_part_3].filter(Boolean);
        const descs=r.descriptions||[r.description_1,r.description_2].filter(Boolean);
        out.push({platform:'microsoft',campaign_id:r.campaign_id,campaign_name:r.campaign_name,ad_group_id:r.ad_group_id||'',ad_group_name:r.ad_group_name||'',ad_id:r.ad_id,ad_label:r.ad_title||r.title_part_1||'',ad_name:r.ad_title||'',headlines:heads,descriptions:descs,headline_1:r.title_part_1,headline_2:r.title_part_2,headline_3:r.title_part_3,description_1:r.description_1,description_2:r.description_2,final_url:r.final_url,ad_type:r.ad_type,media_type:r.ad_type||'',thumbnail_url:'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),_verifiedNa:true});
      }
      return out;
    }
    // "Keyword Performance" — Google Ads search keywords only; the section stays
    // hidden for clients with no keyword data. Summary cards + an insight
    // banner + a sortable/searchable table with match badges and in-cell bars,
    // all derived from the same keyword rows (no extra fetch).
    let kwAllRows=[];
    let kwSort={ key:'spend', dir:'desc' };
    let kwSearch='';
    let kwMatchFilter=new Set();  // uppercased match types; empty = all
    const KW_PER_PAGE=10; let kwPageNum=1;
    const KW_COLS=[
      {key:'keyword_text',label:'Keyword',left:true},
      {key:'match_type',label:'Match',left:true},
      {key:'spend',label:'Spend'},
      {key:'impressions',label:'Impr.'},
      {key:'clicks',label:'Clicks'},
      {key:'ctr',label:'CTR'},
      {key:'conversions',label:'Conv.'},
      {key:'cvr',label:'CVR'},
      {key:'cpa',label:'CPA'},
      {key:'conversion_value',label:'Conv. value'},
    ];
    const kwTitle=t=>t?t.charAt(0)+t.slice(1).toLowerCase():'';
    function kwMatchBadge(mt) {
      const t=(mt||'').toUpperCase();
      if (!t) return '<span class="kw-sub">—</span>';
      const cls=t==='EXACT'?'badge-exact':t==='PHRASE'?'badge-phrase':'badge-broad';
      return `<span class="badge-match ${cls}">${esc(kwTitle(t))}</span>`;
    }
    // Values without a defined metric (e.g. CPA with no conversions) return null
    // so the sort comparator can always sink them to the bottom.
    function kwSortVal(r,key) {
      if (key==='keyword_text') return (r.keyword_text||'').toLowerCase();
      if (key==='match_type')   return (r.match_type||'').toLowerCase();
      if (key==='cvr')  return num(r.clicks) ? num(r.conversions)/num(r.clicks) : null;
      if (key==='cpa')  return num(r.conversions) ? num(r.spend)/num(r.conversions) : null;
      return num(r[key]);
    }
    // Keyword rows are Google Ads only, so a Platform chip that excludes Google
    // empties this table rather than leaving it contradicting the tree above.
    function kwPlatformIncluded() {
      return !platformFilter.size || [...platformFilter].some(k=>k.toLowerCase()==='google');
    }
    // The campaign allowlist and the filter-group dropdowns both key off the
    // campaign name, which keyword rows carry — so the same predicate the
    // explorer tree uses applies here unchanged.
    function kwCampaignAllowed(r) {
      if (_campaignAllowSet.size && !_campaignAllowSet.has(String(r.campaign_name||''))) return false;
      const name=String(r.campaign_name||'').toLowerCase();
      for (const g of EXPLORER_FILTER_GROUPS) {
        const set=explorerFilterState.get(g.id);
        if (!set||!set.size) continue;
        if (!g.chips.some(c=>set.has(c.label)&&c.phrases.some(p=>p&&name.includes(p)))) return false;
      }
      return true;
    }
    function kwScoped() {
      if (!kwPlatformIncluded()) return [];
      return kwAllRows.filter(kwCampaignAllowed);
    }
    function kwFiltered() {
      let rows=kwScoped();
      if (kwMatchFilter.size) rows=rows.filter(r=>kwMatchFilter.has((r.match_type||'').toUpperCase()));
      const q=kwSearch.trim().toLowerCase();
      if (q) rows=rows.filter(r=>((r.keyword_text||'')+' '+(r.ad_group_name||'')+' '+(r.campaign_name||'')).toLowerCase().includes(q));
      const {key,dir}=kwSort, mul=dir==='asc'?1:-1;
      rows.sort((a,b)=>{
        const x=kwSortVal(a,key), y=kwSortVal(b,key);
        const xb=(x==null), yb=(y==null);
        if (xb&&yb) return 0; if (xb) return 1; if (yb) return -1;
        if (typeof x==='string') return mul*x.localeCompare(y,undefined,{numeric:true});
        return mul*(x-y);
      });
      return rows;
    }
    function buildKeywordControls() {
      const host=document.getElementById('keywordMatchChips');
      if (!host) return;
      const order=['EXACT','PHRASE','BROAD'];
      const types=[...new Set(kwScoped().map(r=>(r.match_type||'').toUpperCase()).filter(Boolean))]
        .sort((a,b)=>(order.indexOf(a)<0?9:order.indexOf(a))-(order.indexOf(b)<0?9:order.indexOf(b)));
      // A single match type isn't worth a filter row.
      host.innerHTML = types.length<2 ? '' : ['All',...types].map(t=>
        `<button type="button" class="chip" data-match="${esc(t)}">${t==='All'?'All':esc(kwTitle(t))}</button>`).join('');
      syncKeywordChips();
    }
    function syncKeywordChips() {
      document.querySelectorAll('#keywordMatchChips .chip').forEach(b=>{
        const m=b.dataset.match;
        b.classList.toggle('active', m==='All' ? !kwMatchFilter.size : kwMatchFilter.has(m));
      });
    }
    function renderKeywordTable() {
      const rows=kwFiltered();
      const total=kwScoped().length;
      const el=document.getElementById('keywordTable');
      const arrow=k=>kwSort.key===k?(kwSort.dir==='asc'?' ▲':' ▼'):'';
      const head=`<thead><tr>${KW_COLS.map(c=>`<th class="expl-sort${c.left?' left':''}${kwSort.key===c.key?' active':''}" data-key="${c.key}">${esc(c.label)}${arrow(c.key)}</th>`).join('')}</tr></thead>`;
      if (!rows.length) {
        el.innerHTML=head+`<tbody><tr><td class="empty" colspan="${KW_COLS.length}">No keywords match these filters.</td></tr></tbody>`;
        // "no keywords" and "no Google in this platform filter" are different
        // facts, and reading the second as the first sends someone looking for
        // a broken keyword sync.
        setStatus('keywordStatus', !kwPlatformIncluded()
          ? 'Google is filtered out'
          : (total?`0 of ${total} keyword(s)`:'No keyword data'));
        document.getElementById('keywordPager').innerHTML='';
        return;
      }
      // Bars scale to the whole filtered set (not just the page) so they stay
      // comparable as you move between pages.
      const maxSpend=Math.max(...rows.map(r=>num(r.spend)), 0);
      const maxCpa=Math.max(...rows.map(r=>num(r.conversions)?num(r.spend)/num(r.conversions):0), 0);
      const totalPages=Math.max(1, Math.ceil(rows.length/KW_PER_PAGE));
      if (kwPageNum>totalPages) kwPageNum=totalPages;
      const startIdx=(kwPageNum-1)*KW_PER_PAGE;
      const pageRows=rows.slice(startIdx, startIdx+KW_PER_PAGE);
      const body=pageRows.map(r=>{
        const clk=num(r.clicks), conv=num(r.conversions), spend=num(r.spend);
        const spendCell=`<div class="cell-bar"><span class="cell-bar-val">${money(spend)}</span>${pctBar(maxSpend?spend/maxSpend*100:0)}</div>`;
        const ctr=r.ctr==null?'—':num(r.ctr).toFixed(2)+'%';
        const cvrCell=clk?`<span class="${conv?'num-good':'num-bad'}">${(conv/clk*100).toFixed(2)}%</span>`:'—';
        let cpaCell;
        if (conv) {
          const cpa=spend/conv;
          cpaCell=`<div class="cell-bar"><span class="cell-bar-val">${money(cpa)}</span>${pctBar(maxCpa?cpa/maxCpa*100:0)}</div>`;
        } else {
          const flag=spend>0?`<span class="cell-flag" title="Spent without a conversion">&#9888;</span>`:'';
          cpaCell=`<span class="cell-bar-val">—</span>${flag}`;
        }
        return `<tr>`+
          `<td class="left"><div class="kw-name">${esc(r.keyword_text||'—')}</div>${r.ad_group_name?`<div class="kw-sub">${esc(r.ad_group_name)}</div>`:''}</td>`+
          `<td class="left">${kwMatchBadge(r.match_type)}</td>`+
          `<td>${spendCell}</td>`+
          `<td>${count(r.impressions)}</td>`+
          `<td>${count(clk)}</td>`+
          `<td>${ctr}</td>`+
          `<td>${count(conv)}</td>`+
          `<td>${cvrCell}</td>`+
          `<td>${cpaCell}</td>`+
          `<td>${r.conversion_value==null?'—':money(r.conversion_value)}</td>`+
        `</tr>`;
      }).join('');
      el.innerHTML=head+`<tbody>${body}</tbody>`;
      const filtered=rows.length!==total;
      const suffix=filtered?` (of ${total})`:'';
      const status=totalPages>1
        ? `${startIdx+1}–${startIdx+pageRows.length} of ${rows.length} keyword(s)${suffix}`
        : `${rows.length} keyword(s)${suffix}`;
      setStatus('keywordStatus', status);
      renderKeywordPager(totalPages);
    }
    function renderKeywordPager(totalPages) {
      const pager=document.getElementById('keywordPager');
      if (totalPages<=1) { pager.innerHTML=''; return; }
      pager.innerHTML=`<button type="button" class="pager-btn" id="kwPrev"${kwPageNum<=1?' disabled':''}>‹ Prev</button><span class="pager-info">Page ${kwPageNum} of ${totalPages}</span><button type="button" class="pager-btn" id="kwNext"${kwPageNum>=totalPages?' disabled':''}>Next ›</button>`;
      const prev=document.getElementById('kwPrev'), next=document.getElementById('kwNext');
      if (prev) prev.onclick=()=>{ if(kwPageNum>1){ kwPageNum--; renderKeywordTable(); } };
      if (next) next.onclick=()=>{ if(kwPageNum<totalPages){ kwPageNum++; renderKeywordTable(); } };
    }
    function renderKeywordWindow() {
      const badge=document.getElementById('keywordWindow');
      if (!badge) return;
      const parts=[shortRangeLabel(currentStart, currentEnd)];
      if (!kwPlatformIncluded()) parts.push('Google excluded by the Platform filter');
      badge.textContent=parts.join(' · ');
      badge.title='Google Ads search keywords for the selected date range, sliced by '
        +'the same Platform and campaign filters as the explorer above. Two ranges '
        +'can show the same keywords when the account has no synced keyword history '
        +'for the extra days.';
      badge.hidden=false;
    }
    function renderKeywords() {
      renderKeywordWindow();
      renderKeywordTable();
    }
    // ---- CSV download ----
    // Client-side because every table on this page is already in memory: what
    // the file contains is exactly what the panel is showing, with no second
    // query and no server round-trip to drift out of sync with it.
    function csvCell(v) {
      if (v === null || v === undefined) return '';
      const s = String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    }
    function downloadCsv(filename, rows) {
      if (!rows || !rows.length) return;
      // The BOM is what makes Excel read UTF-8 rather than the local codepage,
      // which is the difference between "Zürich" and "ZÃ¼rich" in a client deck.
      const body = '\ufeff' + rows.map(r => r.map(csvCell).join(',')).join('\r\n');
      const url = URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    }

    // ---- "Show all N" accordion for a table that renders its top rows only ----
    // A breakdown with two hundred companies in it is a wall, not a finding, so
    // these tables show the top ten and keep the rest one click away. The rows
    // are already in memory and the CSV export still carries every one of them,
    // so this only ever changes how much of the table is on screen.
    const TABLE_TOP_N=10;
    // Collapsed state per table id — collapsed by default, and reset by the
    // caller whenever the underlying rows change (a new tab, a new date range).
    const tableExpanded={};
    function tableIsExpanded(id) { return !!tableExpanded[id]; }
    function tableVisibleRows(id, rows) {
      return tableIsExpanded(id) ? rows : rows.slice(0, TABLE_TOP_N);
    }
    // `noun` is plural ("companies", "segments"); `rerender` redraws the table.
    function renderTableMore(hostId, tableId, total, noun, rerender) {
      const host=document.getElementById(hostId);
      if (!host) return;
      if (total<=TABLE_TOP_N) { host.innerHTML=''; return; }
      const open=tableIsExpanded(tableId);
      const label=open
        ? `Show top ${TABLE_TOP_N}`
        : `Show all ${count(total)} ${esc(noun)}`;
      host.innerHTML=`<button type="button" class="tbl-more-btn" aria-expanded="${open?'true':'false'}" aria-controls="${esc(tableId)}">`
        +`<span>${label}</span><span class="tbl-more-caret" aria-hidden="true">${open?'▲':'▼'}</span></button>`;
      host.querySelector('.tbl-more-btn').onclick=()=>{
        tableExpanded[tableId]=!open;
        rerender();
      };
    }

    // ---- LinkedIn audience (member demographics) ----
    // These rows are per-window totals, NOT a daily series: LinkedIn's MEMBER_*
    // pivots carry no date dimension and withhold categories below a minimum
    // event count, so they can be neither summed across days nor re-cut to the
    // date range selected above. The endpoint serves one whole synced window and
    // names it; the panel shows that name and spells out the caveat rather than
    // letting the numbers pass for range-scoped ones.
    const LIDEMO_TABS=[
      ['company','Company'],['job_title','Job title'],['job_function','Job function'],
      ['seniority','Seniority'],['industry','Industry'],['company_size','Company size'],
    ];
    const LIDEMO_WINDOW_LABELS={'LAST_30_DAYS':'Last 30 days','LAST_90_DAYS':'Last 90 days'};
    // How the "show all" button names the rows it is about to reveal — which
    // categories LinkedIn reports differs by dimension, so does the noun.
    const LIDEMO_PLURAL={company:'companies',job_title:'job titles',
      job_function:'job functions',seniority:'seniority levels',
      industry:'industries',company_size:'company sizes'};
    let lidemoData=null, lidemoDim='';
    function lidemoWindowLabel(w) {
      return LIDEMO_WINDOW_LABELS[w]||String(w||'').replace(/_/g,' ').toLowerCase();
    }
    function lidemoRows(dim) {
      return ((lidemoData&&lidemoData.by_dimension)||{})[dim]||[];
    }
    function lidemoLiveDims() {
      return LIDEMO_TABS.filter(([k])=>lidemoRows(k).length);
    }
    function renderLidemoTabs() {
      const host=document.getElementById('lidemoTabs');
      if (!host) return;
      const dims=lidemoLiveDims();
      host.innerHTML=dims.map(([k,label])=>
        `<button type="button" class="pnl-tab" role="tab" data-lidim="${k}" aria-selected="${k===lidemoDim?'true':'false'}">${esc(label)}</button>`
      ).join('');
      host.classList.toggle('one-tab',dims.length===1);
    }
    function lidemoCtr(r) {
      const imp=num(r.impressions);
      if (r.ctr===null||r.ctr===undefined) return imp?num(r.clicks)/imp:0;
      return num(r.ctr);
    }
    function lidemoLabel(r) { return r.category||r.category_urn||'—'; }
    // One file for the whole window, every breakdown stacked under a Breakdown
    // column — the panel only shows one tab at a time, but the thing someone
    // wants in a spreadsheet is all of them.
    const LIDEMO_CSV_HEAD=['Window','Window start','Window end','Breakdown','Category',
      'Impressions','Clicks','CTR %','Spend','Conversions'];
    function lidemoCsvRows() {
      if (!lidemoData) return [];
      const out=[LIDEMO_CSV_HEAD];
      const w=lidemoWindowLabel(lidemoData.window);
      const ws=lidemoData.window_start||'', we=lidemoData.window_end||'';
      for (const [dim,label] of LIDEMO_TABS) {
        for (const r of lidemoRows(dim)) {
          out.push([w, ws, we, label, lidemoLabel(r),
            num(r.impressions), num(r.clicks), (lidemoCtr(r)*100).toFixed(2),
            // spend/conversions come back null when LinkedIn refused that
            // projection; a blank cell is honest, a 0 would not be.
            (r.spend===null||r.spend===undefined)?'':num(r.spend),
            (r.conversions===null||r.conversions===undefined)?'':num(r.conversions)]);
        }
      }
      return out.length>1 ? out : [];
    }
    (function wireLidemoExport() {
      const btn=document.getElementById('lidemoExport');
      if (!btn) return;
      btn.addEventListener('click', ()=>{
        const rows=lidemoCsvRows();
        if (!rows.length) { setStatus('lidemoStatus','Nothing to export yet.'); return; }
        const w=String((lidemoData&&lidemoData.window)||'window').toLowerCase();
        downloadCsv(`linkedin-audience-${w}.csv`, rows);
      });
    })();
    function renderLidemo() {
      const rows=lidemoRows(lidemoDim);
      const el=document.getElementById('lidemoTable');
      if (!el) return;
      if (!rows.length) {
        el.innerHTML='';
        renderTableMore('lidemoMore','lidemoTable',0,'categories',renderLidemo);
        return;
      }
      // Share of reach is relative to the top category in this dimension, so the
      // bars compare like with like even though the dimensions don't partition
      // the same total (a member with no listed job title lands in no bucket).
      const top=rows.reduce((mx,r)=>Math.max(mx,num(r.impressions)),0);
      // spend/conversions come back null when LinkedIn refused that projection
      // for the pivot — show "—", never a measured-looking 0.
      const moneyCell=v=>(v===null||v===undefined)?'—':money(v);
      const head='<thead><tr><th class="left">Category</th><th>Impressions</th>'
        +'<th>Share of reach</th><th>Clicks</th><th>CTR</th><th>Spend</th></tr></thead>';
      const body=tableVisibleRows('lidemoTable',rows).map(r=>{
        const imp=num(r.impressions);
        const share=top?(imp/top*100):0;
        const ctrVal=(r.ctr===null||r.ctr===undefined)?(imp?num(r.clicks)/imp:0):num(r.ctr);
        const label=r.category||r.category_urn||'—';
        return `<tr><td class="left lid-cat" title="${esc(label)}">${esc(label)}</td>`
          +`<td>${count(imp)}</td>`
          +`<td class="lid-share"><span class="lid-bar"><span style="width:${share.toFixed(1)}%"></span></span></td>`
          +`<td>${count(r.clicks)}</td><td>${pct(ctrVal*100)}</td><td>${moneyCell(r.spend)}</td></tr>`;
      }).join('');
      el.innerHTML=head+`<tbody>${body}</tbody>`;
      renderTableMore('lidemoMore','lidemoTable',rows.length,
        LIDEMO_PLURAL[lidemoDim]||'categories',renderLidemo);
    }
    async function loadLinkedinDemographics() {
      const sec=document.getElementById('sec-lidemo');
      if (!sec) return;
      // Toggle the editable-panel wrapper too, so a client with no demographics
      // doesn't get an empty panel (and admins no edit bar for one).
      const unit=sec.closest('.ov-unit')||sec;
      setStatus('lidemoStatus','Loading…');
      const d=await getJson(withDates(LINKEDIN_DEMOGRAPHICS_API)).catch(()=>null);
      lidemoData=d;
      // New window of rows: back to the top ten.
      tableExpanded['lidemoTable']=false;
      const dims=lidemoLiveDims();
      if (!dims.length) {
        sec.style.display='none'; unit.style.display='none';
        setStatus('lidemoStatus','');
        return;
      }
      sec.style.display=''; unit.style.display='';
      if (!dims.some(([k])=>k===lidemoDim)) lidemoDim=dims[0][0];
      const wLabel=lidemoWindowLabel(d.window);
      const span=(d.window_start&&d.window_end)?`${d.window_start} to ${d.window_end}`:'';
      const badge=document.getElementById('lidemoWindow');
      badge.textContent=wLabel;
      badge.title=span;
      document.getElementById('lidemoNote').textContent=
        `LinkedIn reports audience demographics over a fixed window${span?` (${span})`:''}, `
        +'not the date range selected above. Categories with very few events are '
        +'withheld to protect member privacy, so these figures are approximate and '
        +'do not add up to campaign totals.';
      setStatus('lidemoStatus','');
      renderLidemoTabs();
      renderLidemo();
    }

    // ---- Google Ads demographics (age / gender segments) ----
    // Unlike the LinkedIn panel this one honours the date picker: Google reports
    // demographics per day, so the rows are re-cut to whatever range is selected.
    const GDEMO_TABS=[['age_range','Age'],['gender','Gender']];
    let gdemoData=null, gdemoDim='';
    function gdemoDimData(dim) {
      return ((gdemoData&&gdemoData.by_dimension)||{})[dim]||null;
    }
    function gdemoRows(dim) {
      const d=gdemoDimData(dim);
      return (d&&d.segments)||[];
    }
    function gdemoLiveDims() {
      return GDEMO_TABS.filter(([k])=>gdemoRows(k).length);
    }
    function renderGdemoTabs() {
      const host=document.getElementById('gdemoTabs');
      if (!host) return;
      const dims=gdemoLiveDims();
      host.innerHTML=dims.map(([k,label])=>
        `<button type="button" class="pnl-tab" role="tab" data-gdim="${k}" aria-selected="${k===gdemoDim?'true':'false'}">${esc(label)}</button>`
      ).join('');
      host.classList.toggle('one-tab',dims.length===1);
    }
    function renderGdemoRecs() {
      const host=document.getElementById('gdemoRecs');
      if (!host) return;
      const recs=gdemoRows(gdemoDim).map(r=>r.recommendation).filter(Boolean);
      if (!recs.length) { host.hidden=true; host.innerHTML=''; return; }
      host.hidden=false;
      host.innerHTML=recs.map(r=>
        `<div class="gd-rec gd-rec-${esc(r.severity||'medium')}"><div class="gd-rec-body">`
        +`<div class="gd-rec-head">${esc(r.headline||'')}</div>`
        +`<div class="gd-rec-detail">${esc(r.detail||'')}</div>`
        +`</div></div>`
      ).join('');
    }
    function renderGdemo() {
      const el=document.getElementById('gdemoTable');
      if (!el) return;
      const rows=gdemoRows(gdemoDim);
      if (!rows.length) {
        el.innerHTML='';
        renderTableMore('gdemoMore','gdemoTable',0,'segments',renderGdemo);
        return;
      }
      // Spend share and conversion share are both percentages of this
      // dimension's total, so they share one scale — that is the whole point of
      // stacking them, and rescaling either one separately would invent a
      // difference. The common max only decides how much width the pair uses.
      const scale=rows.reduce((mx,r)=>Math.max(mx,num(r.spend_share),num(r.conversion_share)),0)||100;
      const head='<thead><tr><th class="left">Segment</th><th>Spend</th>'
        +'<th title="Top bar: share of this dimension\'s spend. Bottom bar: share of its conversions.">Spend vs conversions</th>'
        +'<th>Clicks</th><th>Conversions</th><th>Cost / conv.</th></tr></thead>';
      const body=tableVisibleRows('gdemoTable',rows).map(r=>{
        const spendW=Math.min(100,num(r.spend_share)/scale*100);
        const convW=Math.min(100,num(r.conversion_share)/scale*100);
        const label=r.segment_label||r.segment_value||'—';
        const rec=r.recommendation;
        let flags='';
        if (r.excluded_everywhere) {
          flags+=`<span class="gd-flag gd-flag-excluded" title="Already excluded in every ad group it appears in">Excluded</span>`;
        } else if (r.excluded_ad_groups) {
          flags+=`<span class="gd-flag gd-flag-excluded" title="Excluded in ${count(r.excluded_ad_groups)} of ${count(r.ad_groups)} ad groups">Partly excluded</span>`;
        }
        if (rec&&rec.kind==='no_conversions') flags+=`<span class="gd-flag gd-flag-waste">No conv.</span>`;
        else if (rec&&rec.kind==='high_cpa') flags+=`<span class="gd-flag gd-flag-cpa">High CPA</span>`;
        const cpa=(r.cpa===null||r.cpa===undefined)?'—':money(r.cpa);
        const sharesTitle=`${pct(num(r.spend_share))} of spend, ${pct(num(r.conversion_share))} of conversions`;
        return `<tr><td class="left gd-seg" title="${esc(label)}">${esc(label)}${flags}</td>`
          +`<td>${money(r.spend)}</td>`
          +`<td class="gd-shares" title="${esc(sharesTitle)}"><span class="gd-split">`
          +`<span class="gd-split-row"><span style="width:${spendW.toFixed(1)}%"></span></span>`
          +`<span class="gd-split-row"><span style="width:${convW.toFixed(1)}%"></span></span>`
          +`</span></td>`
          +`<td>${count(r.clicks)}</td><td>${count(r.conversions)}</td><td>${cpa}</td></tr>`;
      }).join('');
      el.innerHTML=head+`<tbody>${body}</tbody>`;
      renderTableMore('gdemoMore','gdemoTable',rows.length,'segments',renderGdemo);

      const dim=gdemoDimData(gdemoDim)||{};
      const badge=document.getElementById('gdemoCoverage');
      const unknown=dim.undetermined_spend_share;
      if (badge) {
        if (unknown===null||unknown===undefined) { badge.hidden=true; }
        else {
          badge.hidden=false;
          badge.textContent=`${pct(unknown)} unattributed`;
          badge.title='Share of this dimension\'s spend Google could not assign to a segment.';
        }
      }
      const note=document.getElementById('gdemoNote');
      if (note) {
        note.textContent=
          'Google only reports demographics for traffic it can classify, and '
          +'Performance Max campaigns report none at all — so these totals are a '
          +'subset of account spend and will not reconcile with the campaign '
          +'numbers above. Unknown is kept in the table because leaving it out '
          +'would make the other shares look like the whole account.';
      }
      renderGdemoRecs();
    }
    async function loadGoogleDemographics() {
      const sec=document.getElementById('sec-gdemo');
      if (!sec) return;
      // Toggle the editable-panel wrapper too, so a client with no demographic
      // data doesn't get an empty panel (and admins no edit bar for one).
      const unit=sec.closest('.ov-unit')||sec;
      setStatus('gdemoStatus','Loading…');
      const d=await getJson(withDates(GOOGLE_ADS_DEMOGRAPHICS_API)).catch(()=>null);
      gdemoData=d;
      tableExpanded['gdemoTable']=false;
      const dims=gdemoLiveDims();
      if (!dims.length) {
        sec.style.display='none'; unit.style.display='none';
        setStatus('gdemoStatus','');
        return;
      }
      sec.style.display=''; unit.style.display='';
      if (!dims.some(([k])=>k===gdemoDim)) gdemoDim=dims[0][0];
      setStatus('gdemoStatus','');
      renderGdemoTabs();
      renderGdemo();
    }

    // ---- Campaign Explorer: metrics over time ----
    // A line chart between the summary cards and the tree table. The cards
    // above are its metric picker, and they multi-select the way the paid-trends
    // chips do: a card toggles its metric onto or off the chart, so Spend and
    // Clicks can be read against each other. It reads the same campaign-name /
    // platform filters explorerRowMatches already applies to the table, so the
    // line never disagrees with what the cards and tree are currently showing.
    // Its own daily-per-campaign fetch (EXPLORER_TREND_API and siblings) is
    // necessary because the table's own explorer rows are ad-grain totals for
    // the whole window with no date on them -- they can't drive a trend by
    // themselves. Those eight payloads are fetched once per date range and
    // re-sliced client-side on every filter change, so only a range change
    // costs a round trip.
    const EXPLORER_TREND_METRICS = [
      {key:'spend',        label:'Spend',                color:'#1769aa', fmt:money, additive:true},
      {key:'impressions',  label:'Impressions',          color:'#7c3aed', fmt:count, additive:true},
      {key:'clicks',       label:'Clicks',               color:'#0a7f3f', fmt:count, additive:true},
      {key:'ctr',          label:'CTR',                  color:'#b8600a', fmt:pct,   additive:false},
      {key:'conversions',  label:'Conversions',          color:'#0891b2', fmt:count, additive:true},
      {key:'verified',     label:'Verified conv. (GA4)', color:'#8a6d1f', fmt:count, additive:true},
    ];
    // Selected metric keys. Read back in EXPLORER_TREND_METRICS order so the
    // series, legend and colours line up however the cards were clicked -- the
    // same rule the paid-trends chips follow. Never empty: clicking the last
    // active card is a no-op rather than an empty chart.
    let explorerTrendMetrics = new Set(['spend']);
    function explorerTrendDefs() {
      const defs=EXPLORER_TREND_METRICS.filter(m=>explorerTrendMetrics.has(m.key));
      return defs.length?defs:[EXPLORER_TREND_METRICS[0]];
    }
    // The eight daily payloads (base metrics x4 platforms, verified x4
    // platforms), fetched once per date range and re-sliced client-side on
    // every filter change -- see buildExplorerTrendDaily().
    let explorerTrendRaw = null;
    let explorerTrendKey = null;   // currentStart+'|'+currentEnd this was fetched for
    let explorerTrendFetching = false;
    // Which campaigns are "in view" right now, keyed the same way each
    // platform's verified-conversions map is keyed (campaign id for Google, ad
    // id for Meta, normalized campaign name for LinkedIn/Microsoft) -- gathered
    // from the already-filtered ad-level explorer rows renderExplorer() just
    // built. A verified-trend row only counts toward the chart when its
    // campaign is in one of these sets, same rule the whole-window "Verified
    // conv. (GA4)" card total already follows.
    let explorerTrendKeys = { googleIds:new Set(), liGroups:new Set(), msNames:new Set(), metaAdIds:new Set() };
    function explorerTrendFilterKeys(filtered) {
      const googleIds=new Set(), liGroups=new Set(), msNames=new Set(), metaAdIds=new Set();
      for (const r of filtered) {
        if (r.platform==='google') { if (r.campaign_id) googleIds.add(String(r.campaign_id)); }
        else if (r.platform==='linkedin') { const gn=normalizeLiName(r.campaign_name||''); if (gn) liGroups.add(gn); }
        else if (r.platform==='microsoft') { const mn=normalizeLiName(r.campaign_name||''); if (mn) msNames.add(mn); }
        else if (r.platform==='meta') { if (r.ad_id) metaAdIds.add(String(r.ad_id)); }
      }
      return { googleIds, liGroups, msNames, metaAdIds };
    }
    // Reduces the eight payloads to one row per date. explorerRowMatches only
    // ever looks at campaign_name and platform, so it applies unchanged here
    // even though these trend rows carry no ad-level fields.
    function buildExplorerTrendDaily() {
      const raw=explorerTrendRaw;
      if (!raw) return [];
      const byDate=new Map();
      const bump=(d,patch)=>{
        let row=byDate.get(d);
        if (!row) { row={date:d,spend:0,impressions:0,clicks:0,conversions:0,verified:0}; byDate.set(d,row); }
        for (const k in patch) row[k]+=patch[k];
      };
      for (const [platform,payload] of [['google',raw.google],['microsoft',raw.microsoft],['meta',raw.meta]]) {
        for (const r of (payload&&payload.rows)||[]) {
          if (!explorerRowMatches({campaign_name:r.campaign_name, platform})) continue;
          bump(String(r.date), {spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),verified:0});
        }
      }
      for (const r of (raw.linkedin&&raw.linkedin.rows)||[]) {
        if (!explorerRowMatches({campaign_name:r.campaign_group_name, platform:'linkedin'})) continue;
        bump(String(r.date), {spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),verified:0});
      }
      const keys=explorerTrendKeys;
      for (const r of (raw.googleVerified&&raw.googleVerified.rows)||[]) {
        if (!keys.googleIds.has(String(r.campaign_id))) continue;
        bump(String(r.date), {spend:0,impressions:0,clicks:0,conversions:0,verified:num(r.key_events)});
      }
      for (const r of (raw.linkedinVerified&&raw.linkedinVerified.rows)||[]) {
        if (!keys.liGroups.has(normalizeLiName(r.campaign_name||''))) continue;
        bump(String(r.date), {spend:0,impressions:0,clicks:0,conversions:0,verified:num(r.key_events)});
      }
      for (const r of (raw.microsoftVerified&&raw.microsoftVerified.rows)||[]) {
        if (!keys.msNames.has(normalizeLiName(r.campaign_name||''))) continue;
        bump(String(r.date), {spend:0,impressions:0,clicks:0,conversions:0,verified:num(r.key_events)});
      }
      for (const r of (raw.metaVerified&&raw.metaVerified.rows)||[]) {
        if (!keys.metaAdIds.has(String(r.ad_id))) continue;
        bump(String(r.date), {spend:0,impressions:0,clicks:0,conversions:0,verified:num(r.key_events)});
      }
      const out=[...byDate.values()].sort((a,b)=>a.date<b.date?-1:1);
      for (const d of out) d.ctr = d.impressions ? d.clicks/d.impressions*100 : 0;
      return out;
    }
    // The panel title names the selection while it is short enough to read;
    // past three metrics the legend below the chart is the readable list.
    function explorerTrendTitleText(defs) {
      if (defs.length===1) return `${defs[0].label} over time`;
      if (defs.length<=3) return `${defs.map(m=>m.label).join(' · ')} over time`;
      return `${defs.length} metrics over time`;
    }
    // Additive metrics total over the window; CTR is re-derived from the
    // window's clicks and impressions, because the mean of daily CTRs is not
    // the window's CTR.
    function explorerTrendRoll(rows, m) {
      if (m.additive) return rows.reduce((a,r)=>a+num(r[m.key]),0);
      const t=rows.reduce((a,r)=>{a.clicks+=num(r.clicks);a.impressions+=num(r.impressions);return a;},{clicks:0,impressions:0});
      return t.impressions?t.clicks/t.impressions*100:0;
    }
    function renderExplorerTrend() {
      const titleEl=document.getElementById('explorerTrendTitle');
      const defs=explorerTrendDefs();
      const multi=defs.length>1;
      const primary=defs[0];
      if (titleEl) titleEl.textContent = explorerTrendTitleText(defs);
      if (!explorerTrendRaw || explorerTrendKey!==(currentStart+'|'+currentEnd)) {
        if (!explorerTrendFetching) fetchExplorerTrend();
        return;
      }
      clearSkelChart('explorerTrendChart');
      const rows=buildExplorerTrendDaily();
      const legend=document.getElementById('explorerTrendLegend');
      if (!rows.length) {
        __destroyChart('explorerTrendChart');
        if (legend) legend.innerHTML='';
        setStatus('explorerTrendStatus', 'No data for this range yet.');
        return;
      }
      const labels=rows.map(r=>String(r.date).slice(5));
      // Same axis rule as the paid-trends multi-select: metrics differ by
      // orders of magnitude (impressions vs CTR), so every extra series rides
      // its own hidden auto-scaled axis and only the single-metric view draws
      // tick labels or an area fill. Shapes are the comparison; the legend
      // carries each metric's number.
      const series=[], extraScales={};
      defs.forEach((m,i)=>{
        const axisId=i===0?'y':('y'+i);
        if (i>0) extraScales[axisId]={display:false, beginAtZero:true};
        series.push({label:m.label, data:rows.map(r=>num(r[m.key])), color:m.color, fill:!multi, fmt:m.fmt, axisId});
      });
      lineChart('explorerTrendChart', labels, series, {
        yDisplay: !multi,
        yFmt: v => primary.fmt(v),
        extraScales,
        tooltip: { label: c => `${c.dataset.label}: ${c.dataset._fmt(c.raw)}` },
        dates: rows.map(r=>String(r.date)),
        annoScope: 'ads',
      });
      if (legend) {
        legend.innerHTML=defs.map(m=>
          `<span class="cmp-item"><span class="cmp-swatch" style="border-top-color:${m.color}"></span>`
          +`${esc(m.label)} ${m.additive?'total':'over the window'} · ${m.fmt(explorerTrendRoll(rows,m))}</span>`
        ).join('');
      }
      const platTag=platformFilter.size?` · ${[...platformFilter].join(', ')}`:'';
      setStatus('explorerTrendStatus', `${rows.length} day${rows.length===1?'':'s'}${platTag}`);
    }
    async function fetchExplorerTrend() {
      explorerTrendFetching=true;
      setStatus('explorerTrendStatus','Loading…');
      skelChart('explorerTrendChart','trend-md-svg');
      try {
        const [g,m,l,me,gv,lv,mv,mev]=await Promise.all([
          getJson(withDates(EXPLORER_TREND_API)).catch(()=>({rows:[]})),
          getJson(withDates(MICROSOFT_EXPLORER_TREND_API)).catch(()=>({rows:[]})),
          getJson(withDates(LINKEDIN_EXPLORER_TREND_API)).catch(()=>({rows:[]})),
          getJson(withDates(META_EXPLORER_TREND_API)).catch(()=>({rows:[]})),
          getJson(withDates(GOOGLE_VERIFIED_TREND_API)).catch(()=>({rows:[]})),
          getJson(withDates(LINKEDIN_VERIFIED_TREND_API)).catch(()=>({rows:[]})),
          getJson(withDates(MICROSOFT_VERIFIED_TREND_API)).catch(()=>({rows:[]})),
          getJson(withDates(META_VERIFIED_TREND_API)).catch(()=>({rows:[]})),
        ]);
        explorerTrendRaw = { google:g, microsoft:m, linkedin:l, meta:me, googleVerified:gv, linkedinVerified:lv, microsoftVerified:mv, metaVerified:mev };
        explorerTrendKey = currentStart+'|'+currentEnd;
        renderExplorerTrend();
      } catch(err) {
        clearSkelChart('explorerTrendChart');
        __destroyChart('explorerTrendChart');
        setStatus('explorerTrendStatus', err.message||String(err), true);
      } finally {
        explorerTrendFetching=false;
      }
    }
    registerAnnotatedChart(()=>{ if (explorerTrendRaw) renderExplorerTrend(); });

    async function loadExplorer() {
      setStatus('explorerStatus','Loading…');
      // Demographics are independent of the explorer tree (different grain,
      // different window), so they load alongside it rather than blocking it.
      const lidemoDone=loadLinkedinDemographics();
      const gdemoDone=loadGoogleDemographics();
      document.getElementById('explorerSummaryCards').innerHTML = skelCards(5);
      document.getElementById('explorerTable').innerHTML = skelTable(6,8);
      // The Compare picker's window (previous period/year) — fetched only when
      // set, so a page load with no comparison configured yet skips these.
      const cmpOn = !!compareStart;
      const EMPTY_CONV={actions:[],by_entity:{}};
      const [g,l,m,ms,kw,ver,gver,lver,mver,gconv,mconv,msconv,gPrev,lPrev,mPrev,msPrev]=await Promise.all([
        getJson(withDates(EXPLORER_API)).catch(()=>({rows:[]})),
        getJson(withDates(LINKEDIN_EXPLORER_API)).catch(()=>({rows:[]})),
        getJson(withDates(META_EXPLORER_API)).catch(()=>({rows:[]})),
        getJson(withDates(MICROSOFT_EXPLORER_API)).catch(()=>({rows:[]})),
        getJson(withDates(GOOGLE_ADS_KEYWORDS_API)).catch(()=>({rows:[]})),
        getJson(withDates(META_VERIFIED_API)).catch(()=>({by_ad_id:{}})),
        getJson(withDates(GOOGLE_VERIFIED_API)).catch(()=>({by_campaign_id:{}})),
        getJson(withDates(LINKEDIN_VERIFIED_API)).catch(()=>({by_group_name:{}})),
        getJson(withDates(MICROSOFT_VERIFIED_API)).catch(()=>({by_campaign_name:{}})),
        // Conversion-action breakdowns. Fetched for the current window only --
        // the selector's job is "which action is this", not "how did this action
        // move", and the Conv. column drops its delta while one is selected.
        getJson(withDates(GOOGLE_CONV_ACTIONS_API)).catch(()=>EMPTY_CONV),
        getJson(withDates(META_CONV_ACTIONS_API)).catch(()=>EMPTY_CONV),
        getJson(withDates(MICROSOFT_CONV_ACTIONS_API)).catch(()=>EMPTY_CONV),
        cmpOn ? getJson(withDatesRange(EXPLORER_API, compareStart, compareEnd)).catch(()=>({rows:[]})) : Promise.resolve({rows:[]}),
        cmpOn ? getJson(withDatesRange(LINKEDIN_EXPLORER_API, compareStart, compareEnd)).catch(()=>({rows:[]})) : Promise.resolve({rows:[]}),
        cmpOn ? getJson(withDatesRange(META_EXPLORER_API, compareStart, compareEnd)).catch(()=>({rows:[]})) : Promise.resolve({rows:[]}),
        cmpOn ? getJson(withDatesRange(MICROSOFT_EXPLORER_API, compareStart, compareEnd)).catch(()=>({rows:[]})) : Promise.resolve({rows:[]}),
      ]);
      verifiedByAdId=(ver&&ver.by_ad_id)?ver.by_ad_id:{};
      verifiedByAdIdEvent=(ver&&ver.by_ad_id_event)?ver.by_ad_id_event:{};
      verifiedByGoogleCampaignId=(gver&&gver.by_campaign_id)?gver.by_campaign_id:{};
      verifiedByGoogleCampaignIdEvent=(gver&&gver.by_campaign_id_event)?gver.by_campaign_id_event:{};
      verifiedByLinkedinGroup=(lver&&lver.by_group_name)?lver.by_group_name:{};
      verifiedByLinkedinGroupEvent=(lver&&lver.by_group_name_event)?lver.by_group_name_event:{};
      verifiedByMicrosoftCampaign=(mver&&mver.by_campaign_name)?mver.by_campaign_name:{};
      verifiedByMicrosoftCampaignEvent=(mver&&mver.by_campaign_name_event)?mver.by_campaign_name_event:{};
      const metaEvents=(ver&&ver.events)?ver.events:[];
      const googleEvents=(gver&&gver.events)?gver.events:[];
      const linkedinEvents=(lver&&lver.events)?lver.events:[];
      const microsoftEvents=(mver&&mver.events)?mver.events:[];
      keyEventList=[...new Set([...metaEvents,...googleEvents,...linkedinEvents,...microsoftEvents])];
      if (selectedKeyEvent!=='__all__' && keyEventList.indexOf(selectedKeyEvent)<0) selectedKeyEvent='__all__';
      convByGoogleAdId=(gconv&&gconv.by_entity)?gconv.by_entity:{};
      convByMetaAdId=(mconv&&mconv.by_entity)?mconv.by_entity:{};
      convByMicrosoftGroupId=(msconv&&msconv.by_entity)?msconv.by_entity:{};
      // One selector across every platform, so the same action name coming from
      // two platforms is one option that sums both. Ordered by total conversions
      // (each payload is already biggest-first) rather than alphabetically.
      convActionList=[...new Set([
        ...((gconv&&gconv.actions)||[]).map(a=>a.name),
        ...((mconv&&mconv.actions)||[]).map(a=>a.name),
        ...((msconv&&msconv.actions)||[]).map(a=>a.name),
      ])].filter(Boolean);
      // An action that no longer exists in this window would otherwise leave the
      // column dashed everywhere with no way back except reloading.
      if (selectedConvAction!=='__all__' && convActionList.indexOf(selectedConvAction)<0) {
        selectedConvAction='__all__';
        try { localStorage.removeItem(CONV_STORAGE_KEY); } catch(e) {}
      }
      explorerRows=normalizeExplorerRows(g,l,m,ms);
      explorerPrevRows=cmpOn ? normalizeExplorerRows(gPrev,lPrev,mPrev,msPrev) : [];
      applyVerifiedSelection();
      applyConvSelection(explorerRows);
      renderExplorer();
      // Keyword table: only show the section when this client actually has
      // Google Ads search-keyword data (empty for LinkedIn/Meta-only clients).
      kwAllRows=(kw&&kw.rows)||[];
      const kwSec=document.getElementById('sec-keywords');
      // Toggle the editable-panel wrapper when there is one, so a client with no
      // keyword data doesn't get an empty panel (and no edit bar for admins).
      const kwUnit=kwSec.closest('.ov-unit')||kwSec;
      if (kwAllRows.length) {
        kwSec.style.display='';
        kwUnit.style.display='';
        kwPageNum=1;
        buildKeywordControls();
        renderKeywords();
      } else {
        kwSec.style.display='none';
        kwUnit.style.display='none';
      }
      await lidemoDone;
      await gdemoDone;
    }

    // ---- GA4: Top pages ----
    let pagesTopRows=[], pagesSourceRows=[], pagesSearchQuery='';
    let pagesEventMap={};   // page_path -> { event_name: count }, from TOP_PAGES_KEY_EVENTS_API
    // Recompute key_events per row from the global key-event selection, same
    // fallback rule as Traffic/User acquisition: only override once our own
    // event map actually has data, otherwise keep the base report's real value.
    function applyPageEvents(rows) {
      if (!Object.keys(pagesEventMap).length) return rows;
      return rows.map(r=>({...r, key_events:keSum(pagesEventMap, r.page_path)}));
    }
    const PAGES_PER_PAGE=10; let pagesPageNum=1;
    // Cap Top pages to the top N by views — the long tail past this is almost
    // always checkout steps and one-off paths that just add noise.
    const PAGES_TOP_LIMIT=50;
    // Per-page source / AI-referral rows (vw_page_path_source_daily), used by
    // the AI Traffic tab. Fetched once per date range and memoized so switching
    // between date ranges is instant.
    let pagesSourceLoadedFor=null;
    async function ensurePagesSources() {
      const k=currentStart+'|'+currentEnd;
      if (pagesSourceLoadedFor===k) return pagesSourceRows;
      const src=await getJson(withDates(PAGES_SOURCES_API)).catch(()=>({rows:[]}));
      pagesSourceRows=src.rows||[]; pagesSourceLoadedFor=k;
      return pagesSourceRows;
    }
    // Click-to-sort for the Top pages and Landing pages tables. Follows the
    // same house pattern as the GSC tables above (th.gsc-sort + ▴/▾ arrow,
    // delegated click on the pane). Both default to Key events, highest first.
    const PAGES_SORT_COLS=[
      {key:'page_path',label:'Page',left:true},
      {key:'page_views',label:'Views',num:true},
      {key:'users',label:'Users',num:true},
      {key:'key_events',label:'Key events',num:true},
      {key:'avg_engt',label:'Avg engt',num:true,get:p=>p.users?p.engagement_seconds/p.users:0},
    ];
    // Both page tables now run the full width of the card, so the path column has
    // room for a real path instead of the 24-char default. Anything past this
    // still ellipsizes in the cell, and the full path stays in the tooltip.
    const PAGE_TABLE_PATH_MAX=48;
    let pagesSort={key:'key_events',dir:'desc'};
    const LANDING_SORT_COLS=[
      {key:'page_path',label:'Landing page',left:true},
      {key:'sessions',label:'Sessions',num:true},
      {key:'users',label:'Users',num:true},
      {key:'new_users',label:'New users',num:true},
      {key:'key_events',label:'Key events',num:true},
      {key:'key_event_rate',label:'KE rate',num:true},
      {key:'avg_engagement_seconds',label:'Avg engt',num:true},
    ];
    let landingSort={key:'key_events',dir:'desc'};
    function analyticsSortHead(cols,sort,tableName) {
      const arrow=k=>sort.key===k?(sort.dir==='asc'?' \u25B4':' \u25BE'):'';
      return `<thead><tr>`+cols.map(c=>`<th class="gsc-sort${c.left?' left':''}${sort.key===c.key?' active':''}" data-table="${tableName}" data-key="${esc(c.key)}" aria-sort="${sort.key===c.key?(sort.dir==='asc'?'ascending':'descending'):'none'}">${esc(c.label)}${arrow(c.key)}</th>`).join('')+`</tr></thead>`;
    }
    function analyticsSort(rows,cols,sort) {
      const col=cols.find(c=>c.key===sort.key)||cols[0];
      const get=col.get||(r=>r[col.key]);
      const mult=sort.dir==='asc'?1:-1;
      return rows.slice().sort((a,b)=>{
        if (col.num) return (num(get(a))-num(get(b)))*mult;
        return String(get(a)??'').localeCompare(String(get(b)??''))*mult;
      });
    }
    (function(){
      const pane=document.getElementById('pane-analytics');
      if (!pane) return;
      pane.addEventListener('click', ev=>{
        const th=ev.target.closest('th.gsc-sort'); if (!th) return;
        const which=th.dataset.table;
        const sort=which==='pages'?pagesSort:landingSort;
        const cols=which==='pages'?PAGES_SORT_COLS:LANDING_SORT_COLS;
        const col=cols.find(c=>c.key===th.dataset.key); if (!col) return;
        if (sort.key===col.key) sort.dir=sort.dir==='asc'?'desc':'asc';
        else { sort.key=col.key; sort.dir=col.num?'desc':'asc'; }
        if (which==='pages') { pagesPageNum=1; renderPages(); }
        else { landingPageNum=1; renderLanding(); }
      });
    })();
    function renderPages() {
      let base=applyPageEvents(pagesTopRows);
      // Rows are already sorted by views desc (server ORDER BY), so slicing
      // keeps the top N. Search then filters within that top set.
      base=base.slice(0, PAGES_TOP_LIMIT);
      if (pagesSearchQuery) { const q=pagesSearchQuery.toLowerCase(); base=base.filter(p=>p.page_path.toLowerCase().includes(q)); }
      base=analyticsSort(base,PAGES_SORT_COLS,pagesSort);
      const el=document.getElementById('pagesTable');
      if (!base.length) { el.innerHTML=`<tbody><tr><td class="empty">No pages match${pagesSearchQuery?' "'+esc(pagesSearchQuery)+'"':''}.</td></tr></tbody>`; setStatus('pagesStatus','No results'); document.getElementById('pagesPager').innerHTML=''; return; }
      const totalPages=Math.max(1,Math.ceil(base.length/PAGES_PER_PAGE));
      if (pagesPageNum>totalPages) pagesPageNum=totalPages;
      const startIdx=(pagesPageNum-1)*PAGES_PER_PAGE;
      const pageRows=base.slice(startIdx,startIdx+PAGES_PER_PAGE);
      el.innerHTML=analyticsSortHead(PAGES_SORT_COLS,pagesSort,'pages')+
        `<tbody>${pageRows.map(p=>{const engt=p.users?p.engagement_seconds/p.users:0;return`<tr><td class="left"><span class="page-path" title="${esc(p.page_path)}">${esc(truncPath(p.page_path,PAGE_TABLE_PATH_MAX))}</span></td><td>${count(p.page_views)}</td><td>${count(p.users)}</td><td>${count(p.key_events)}</td><td>${fmtDuration(engt)}</td></tr>`;}). join('')}</tbody>`;
      enableColResize('pagesTable');
      const tag=pagesSearchQuery?' (filtered)':'';
      setStatus('pagesStatus', `${startIdx+1}–${startIdx+pageRows.length} of ${base.length}${tag}`);
      renderPagesPager(totalPages);
    }
    function renderPagesPager(totalPages) {
      const pager=document.getElementById('pagesPager');
      if (totalPages<=1) { pager.innerHTML=''; return; }
      pager.innerHTML=`<button type="button" class="pager-btn" id="pagesPrev"${pagesPageNum<=1?' disabled':''}>‹ Prev</button><span class="pager-info">Page ${pagesPageNum} of ${totalPages}</span><button type="button" class="pager-btn" id="pagesNext"${pagesPageNum>=totalPages?' disabled':''}>Next ›</button>`;
      const prev=document.getElementById('pagesPrev'), next=document.getElementById('pagesNext');
      if (prev) prev.onclick=()=>{if(pagesPageNum>1){pagesPageNum--;renderPages();}};
      if (next) next.onclick=()=>{if(pagesPageNum<totalPages){pagesPageNum++;renderPages();}};
    }
    async function loadPages() {
      setStatus('pagesStatus','Loading…');
      document.getElementById('pagesTable').innerHTML = skelTable(5,8);
      const [top,ev]=await Promise.all([
        getJson(withDates(PAGES_TOP_API)).catch(()=>({rows:[]})),
        getJson(withDates(TOP_PAGES_KEY_EVENTS_API)).catch(()=>({rows:[],events:[]})),
      ]);
      pagesTopRows=top.rows||[]; pagesPageNum=1;
      pagesEventMap={};
      for (const r of (ev.rows||[])) {
        (pagesEventMap[r.page_path]=pagesEventMap[r.page_path]||{})[r.event_name]=num(r.event_count);
      }
      mergeEvents(ev.events);
      renderPages();
    }
    (function(){
      const inp=document.getElementById('pagesSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{ clearTimeout(debounce); debounce=setTimeout(()=>{pagesSearchQuery=inp.value.trim();pagesPageNum=1;renderPages();},180); });
    })();

    // ---- AI Traffic tab (from vw_page_path_source_daily, is_ai_referral) ----
    let aiRows=[], aiPagesSearchQuery='', aiSourceFilter=new Set();
    const AI_PALETTE=['#1d6fd0','#7c3aed','#0a7f3f','#dc2626','#d97706','#0891b2','#be185d','#4b5563'];
    // Stacked-area trend: sessions/day, one band per AI platform. Data comes from
    // the daily endpoint (the range-aggregated pages/sources has no time axis).
    // AI-traffic trend granularity (Daily/Weekly chips), mirroring the sessions
    // trend. Daily rows are re-bucketed to Monday-start weeks client-side: each
    // row's date is remapped to its week start, and renderAiTrend's per-date sum
    // collapses the days into weeks. Only drives the main tab chart (default
    // chartId), not the overview mini-chart.
    let aiTrendGran = 'daily';
    let aiTrendDailyCache = [];
    function aggregateAiWeekly(rows) {
      return (rows || []).map(function(r) {
        const dt = new Date(String(r.date) + 'T00:00:00');
        const dow = (dt.getDay() + 6) % 7;            // 0 = Monday
        const mon = new Date(dt); mon.setDate(dt.getDate() - dow);
        const key = `${mon.getFullYear()}-${String(mon.getMonth()+1).padStart(2,'0')}-${String(mon.getDate()).padStart(2,'0')}`;
        return Object.assign({}, r, { date: key });
      });
    }
    function renderAiTrendGran() {
      renderAiTrend(aiTrendGran === 'weekly' ? aggregateAiWeekly(aiTrendDailyCache) : aiTrendDailyCache);
    }
    document.querySelectorAll('#aiTrendGranChips .chip').forEach(function(btn) {
      btn.addEventListener('click', function() {
        if (btn.dataset.gran === aiTrendGran) return;
        aiTrendGran = btn.dataset.gran;
        document.querySelectorAll('#aiTrendGranChips .chip').forEach(b => b.classList.toggle('active', b === btn));
        renderAiTrendGran();
      });
    });
    function renderAiTrend(daily, chartId, statusId) {
      chartId=chartId||'aiTrendChart'; statusId=statusId||'aiTrendStatus';
      const rows=daily||[];
      const dates=[...new Set(rows.map(r=>String(r.date)))].sort();
      const pivot=new Map();   // platform -> Map(date -> sessions)
      for (const r of rows) {
        const pl=r.ai_platform||'Unknown';
        let m=pivot.get(pl); if(!m){m=new Map();pivot.set(pl,m);}
        m.set(String(r.date),(m.get(String(r.date))||0)+num(r.sessions));
      }
      // Biggest platform first so it sits at the bottom of the stack.
      const ordered=[...pivot.keys()].map(pl=>[pl,[...pivot.get(pl).values()].reduce((a,b)=>a+b,0)]).sort((a,b)=>b[1]-a[1]);
      if (!dates.length || !ordered.length) { __destroyChart(chartId); setStatus(statusId,'No AI traffic in this range.'); return; }
      // Stack the data ourselves (scales.y.stacked doesn't stack lines in this
      // Chart.js build): each series plots the running cumulative total and fills
      // down to the series below it. _raw keeps the per-platform value for tooltips.
      const cum=dates.map(()=>0);
      const datasets=ordered.map(([pl],i)=>{
        const color=AI_PALETTE[i%AI_PALETTE.length], m=pivot.get(pl);
        const raw=dates.map(d=>m.get(d)||0);
        const data=raw.map((v,idx)=>(cum[idx]+=v));
        return { label:pl, data, _raw:raw, borderColor:color, backgroundColor:color+'59',
                  fill:i===0?'origin':'-1', borderWidth:2, tension:0.3, pointRadius:0, pointHoverRadius:4 };
      });
      __chart(chartId, {
        type:'line',
        data:{ labels:dates.map(d=>d.slice(5)), datasets },
        options:{
          interaction:{mode:'index',intersect:false},
          scales:{
            x:{ grid:{display:false}, border:{display:false}, ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:8} },
            y:{ beginAtZero:true, grid:{color:'#f1f4f9'}, border:{display:false}, ticks:{maxTicksLimit:5} },
          },
          plugins:{
            legend:{ display:true, position:'bottom', labels:{usePointStyle:true, boxWidth:8, padding:12} },
            tooltip:{ callbacks:{ label:c=>`${c.dataset.label}: ${count(c.dataset._raw[c.dataIndex])}` } },
          },
        },
      });
      setStatus(statusId,'');
    }
    async function loadAiTraffic() {
      setStatus('aiTrafficStatus','Loading…');
      setStatus('aiTrendStatus','Loading…');
      document.getElementById('aiSourcesTable').innerHTML=skelTable(5,5);
      document.getElementById('aiPagesTable').innerHTML=skelTable(4,8);
      const [rows, daily]=await Promise.all([
        ensurePagesSources().then(rs=>rs.filter(r=>r.is_ai_referral)),
        getJson(withDates(AI_TRAFFIC_DAILY_API)).then(d=>d.rows||[]).catch(()=>[]),
      ]);
      aiTrendDailyCache = daily;
      renderAiTrendGran();
      const bySrc=new Map();
      for (const r of rows) {
        const key=r.ai_platform||'Unknown';
        let g=bySrc.get(key); if(!g){g={source:key,sessions:0,users:0,page_views:0,engagement_seconds:0};bySrc.set(key,g);}
        g.sessions+=num(r.sessions);g.users+=num(r.users);g.page_views+=num(r.page_views);g.engagement_seconds+=num(r.engagement_seconds);
      }
      const srcRows=[...bySrc.values()].sort((a,b)=>b.sessions-a.sessions);
      renderTable('aiSourcesTable',[
        {key:'source',label:'AI source',left:true},
        {key:'sessions',label:'Sessions',format:count},
        {key:'users',label:'Users',format:count},
        {key:'page_views',label:'Page views',format:count},
        {key:'engt',label:'Avg engt',format:(_,r)=>fmtDuration(r.users?r.engagement_seconds/r.users:0)},
      ],srcRows,'No AI-referred traffic in this range.');
      setStatus('aiTrafficStatus', srcRows.length?`${srcRows.length} source(s)`:'No AI traffic');
      aiRows=rows;
      buildAiSourceChips();
      renderAiPages();
    }
    function buildAiSourceChips() {
      const el=document.getElementById('aiPageSourceChips');
      if (!el) return;
      const sources=[...new Set(aiRows.map(r=>r.ai_platform).filter(Boolean))].sort();
      // Drop any selected source no longer present in this range.
      for (const s of [...aiSourceFilter]) if (!sources.includes(s)) aiSourceFilter.delete(s);
      el.innerHTML=[['__all__','All'],...sources.map(s=>[s,s])].map(([v,l])=>`<button type="button" class="chip" data-key="${esc(v)}">${esc(l)}</button>`).join('');
      const sync=()=>el.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active', b.dataset.key==='__all__'?aiSourceFilter.size===0:aiSourceFilter.has(b.dataset.key)));
      el.querySelectorAll('.chip').forEach(btn=>btn.addEventListener('click',()=>{
        const key=btn.dataset.key;
        if(key==='__all__')aiSourceFilter.clear();else if(aiSourceFilter.has(key))aiSourceFilter.delete(key);else aiSourceFilter.add(key);
        sync();renderAiPages();
      }));
      sync();
    }
    function renderAiPages() {
      const src=aiSourceFilter.size?aiRows.filter(r=>aiSourceFilter.has(r.ai_platform)):aiRows;
      const byPage=new Map();
      for (const r of src) {
        let g=byPage.get(r.page_path); if(!g){g={page_path:r.page_path,sessions:0,users:0,page_views:0};byPage.set(r.page_path,g);}
        g.sessions+=num(r.sessions);g.users+=num(r.users);g.page_views+=num(r.page_views);
      }
      let base=[...byPage.values()].sort((a,b)=>b.sessions-a.sessions);
      if (aiPagesSearchQuery) { const q=aiPagesSearchQuery.toLowerCase(); base=base.filter(p=>p.page_path.toLowerCase().includes(q)); }
      base=base.slice(0,PAGES_TOP_LIMIT);
      renderTable('aiPagesTable',[
        {key:'page_path',label:'Page',left:true},
        {key:'sessions',label:'Sessions',format:count},
        {key:'users',label:'Users',format:count},
        {key:'page_views',label:'Page views',format:count},
      ],base,(aiPagesSearchQuery||aiSourceFilter.size)?'No pages match.':'No AI-referred pages in this range.');
      const tag=aiSourceFilter.size?` · ${[...aiSourceFilter].join(', ')}`:'';
      setStatus('aiPagesStatus', base.length?`${base.length} page(s)${tag}`:'');
    }
    (function(){
      const inp=document.getElementById('aiPagesSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{ clearTimeout(debounce); debounce=setTimeout(()=>{aiPagesSearchQuery=inp.value.trim();renderAiPages();},180); });
    })();

    // ---- Campaign Explorer: paid-source module (paid_* from source_platform) ----
    // ---- GA4: Traffic acquisition ----
    function renderBarList(containerId, rows, valueKey, labelKey) {
      const el=document.getElementById(containerId);
      if (!rows||!rows.length) { el.innerHTML='<div class="empty">No data.</div>'; return; }
      const total=rows.reduce((s,r)=>s+num(r[valueKey]),0);
      el.innerHTML=rows.map(r=>{const p=total?num(r[valueKey])/total*100:0;return`<div class="bar-row"><div class="bar-label">${esc(r[labelKey])}</div>${pctBar(p)}<div class="bar-count">${count(r[valueKey])}<span class="bar-pct">${p.toFixed(0)}%</span></div></div>`;}).join('');
    }
    // Categorical palette for stacked/segmented charts (channels, gender, etc.).
    const CHART_PALETTE=['#1d6fd0','#7c3aed','#0a7f3f','#e08a1e','#d6336c','#0d9488','#5661b3','#b4530a','#3b7ddd','#8a4fbe'];
    // Traffic → single 100% bar, one segment per channel; hover shows channel,
    // sessions and share. A legend under the bar lists each channel + %.
    // Single 100% bar, one segment per channel. No legend — hovering a segment
    // shows its channel, sessions and share in a floating tooltip.
    function renderChannelStacked(rows) {
      const el=document.getElementById('channelBars');
      if (!rows||!rows.length) { el.innerHTML='<div class="empty">No data.</div>'; return; }
      const ordered=[...rows].sort((a,b)=>num(b.sessions)-num(a.sessions));
      const total=ordered.reduce((s,r)=>s+num(r.sessions),0)||1;
      const seg=ordered.map((r,i)=>{
        const p=num(r.sessions)/total*100, color=CHART_PALETTE[i%CHART_PALETTE.length];
        return`<div class="stack-seg" style="width:${p.toFixed(2)}%;background:${color}" data-label="${esc(r.channel)}" data-detail="${count(r.sessions)} sessions · ${p.toFixed(1)}%"></div>`;
      }).join('');
      el.innerHTML=`<div class="stack-wrap"><div class="stack-bar">${seg}</div><div class="stack-tip" hidden></div></div>`;
      const wrap=el.querySelector('.stack-wrap'), tip=el.querySelector('.stack-tip');
      wrap.querySelectorAll('.stack-seg').forEach(s=>{
        s.addEventListener('mousemove', e=>{
          tip.textContent=s.dataset.label+' — '+s.dataset.detail;
          tip.hidden=false;
          const rect=wrap.getBoundingClientRect();
          tip.style.left=Math.max(0,Math.min(e.clientX-rect.left, rect.width))+'px';
        });
        s.addEventListener('mouseleave', ()=>{ tip.hidden=true; });
      });
    }
    // Bar list capped to the top N with a Prev/Next pager for the tail. Shares
    // are computed against the full total so pagination doesn't skew percentages.
    function renderBarListPaged(containerId, pagerId, rows, valueKey, labelKey, state) {
      const el=document.getElementById(containerId), pager=document.getElementById(pagerId);
      if (!rows||!rows.length) { el.innerHTML='<div class="empty">No data.</div>'; if(pager) pager.innerHTML=''; return; }
      const perPage=10, total=rows.reduce((s,r)=>s+num(r[valueKey]),0)||1;
      const totalPages=Math.max(1,Math.ceil(rows.length/perPage));
      if (state.page>totalPages) state.page=totalPages;
      const startIdx=(state.page-1)*perPage, pageRows=rows.slice(startIdx,startIdx+perPage);
      el.innerHTML=pageRows.map(r=>{const p=num(r[valueKey])/total*100;return`<div class="bar-row"><div class="bar-label">${esc(r[labelKey])}</div>${pctBar(p)}<div class="bar-count">${count(r[valueKey])}<span class="bar-pct">${p.toFixed(0)}%</span></div></div>`;}).join('');
      if (!pager) return;
      if (totalPages<=1) { pager.innerHTML=''; return; }
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${state.page<=1?' disabled':''}>‹ Prev</button><span class="pager-info">Page ${state.page} of ${totalPages}</span><button type="button" class="pager-btn" data-dir="next"${state.page>=totalPages?' disabled':''}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{ state.page+=b.dataset.dir==='next'?1:-1; renderBarListPaged(containerId,pagerId,rows,valueKey,labelKey,state); });
    }
    // Top sources/medium: 10 per page, rest behind a pager. Rows arrive already
    // sorted by sessions desc, so page 1 is the true top 10.
    const SOURCES_PER_PAGE=10; let sourcesPageNum=1;
    function renderTrafficSources() {
      const rows=trafficSources||[];
      const totalPages=Math.max(1,Math.ceil(rows.length/SOURCES_PER_PAGE));
      if (sourcesPageNum>totalPages) sourcesPageNum=totalPages;
      const startIdx=(sourcesPageNum-1)*SOURCES_PER_PAGE;
      const pageRows=rows.slice(startIdx,startIdx+SOURCES_PER_PAGE);
      renderTable('sourcesTable',[
        {key:'source',label:'Source',left:true},
        {key:'medium',label:'Medium',left:true},
        {key:'sessions',label:'Sessions',format:count},
        {key:'engaged_sessions',label:'Engaged',format:count},
        {key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'},
        {key:'key_events',label:'Key events',format:count},
      ], pageRows, 'No source data.');
      const pager=document.getElementById('sourcesPager');
      if (!pager) return;
      if (totalPages<=1) { pager.innerHTML=''; return; }
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${sourcesPageNum<=1?' disabled':''}>‹ Prev</button><span class="pager-info">Page ${sourcesPageNum} of ${totalPages}</span><button type="button" class="pager-btn" data-dir="next"${sourcesPageNum>=totalPages?' disabled':''}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{ sourcesPageNum+=b.dataset.dir==='next'?1:-1; renderTrafficSources(); });
    }
    async function loadTrafficAcq() {
      setStatus('trafficAcqStatus','Loading…');
      document.getElementById('channelBars').innerHTML = skelBars(5);
      document.getElementById('sourcesTable').innerHTML = skelTable(6,6);
      try {
        const [payload, ev] = await Promise.all([
          getJson(withDates(TRAFFIC_ACQ_API)),
          getJson(withDates(TRAFFIC_KEY_EVENTS_API)).catch(()=>({by_source_events:[],events:[]})),
        ]);
        renderChannelStacked(payload.by_channel||[]);
        sourcesPageNum=1;
        trafficBaseSources = payload.by_source||[];
        trafficSourceEventMap={};
        for (const r of (ev.by_source_events||[])) {
          const k=srcKey(r.source,r.medium);
          (trafficSourceEventMap[k]=trafficSourceEventMap[k]||{})[r.event_name]=num(r.event_count);
        }
        mergeEvents(ev.events);
        applyTrafficSources(); renderTrafficSources();
        setStatus('trafficAcqStatus','');
      } catch(err) { setStatus('trafficAcqStatus',err.message||String(err),true); }
    }

    // ---- GA4: Device split ----
    async function loadDeviceSplit() {
      const sec=document.getElementById('sec-audience');
      setStatus('deviceStatus','Loading…');
      document.getElementById('deviceBars').innerHTML = skelBars(3);
      try {
        const payload=await getJson(withDates(DEVICE_SPLIT_API));
        const rows=payload.rows||[];
        // Audience only holds the device split — when GA4 returns nothing for the
        // range, drop the whole section rather than showing an empty panel.
        if (sec) sec.hidden = !rows.length;
        renderBarList('deviceBars',rows,'users','device');
        setStatus('deviceStatus','');
      } catch(err) { setStatus('deviceStatus',err.message||String(err),true); }
    }

    // ---- GA4: Global key-event selector (Traffic + Landing pages + User acquisition) ----
    // One control at the top of Website Analytics chooses which GA4 events count as
    // "key events." Default = GA4's own key events; the selection persists per client
    // (admin "Save as default"). Each panel keeps a base row set + a per-row event map
    // so the key-events column recomputes instantly when the selection changes.
    const LANDING_PER_PAGE=10; let landingPageNum=1, landingRows=[], landingSearchQuery='';
    let landingBaseRows=[];            // from LANDING_PAGES_API
    let landingEventMap={};            // page_path -> { event_name: count }
    let trafficSources=[], trafficBaseSources=[], trafficSourceEventMap={};   // srcKey -> { event_name: count }
    let userAcqSources=[], userAcqBaseSources=[], userAcqSourceEventMap={};
    let keyEventCatalog=[];            // [{event_name,event_count,is_key}] unioned across panels
    let keyEventCounts={};             // event_name -> total count (union)
    let keyEventKeys=new Set();        // GA4-flagged key events (union)
    let selectedKeyEvents=new Set(GA4_KEY_EVENTS_SAVED);
    let keyEventUserTouched=false;     // once true, stop auto-tracking GA4's key set
    let keyEventSearchTerm='';
    const srcKey=(s,m)=>(s||'')+'\u0000'+(m||'');

    // Fold one panel's event catalog into the shared union + refresh the dropdown.
    function mergeEvents(events) {
      for (const e of (events||[])) {
        const n=e.event_name; if (!n) continue;
        keyEventCounts[n]=(keyEventCounts[n]||0)+num(e.event_count);
        if (num(e.key_events)>0) keyEventKeys.add(n);
      }
      keyEventCatalog=Object.keys(keyEventCounts)
        .map(n=>({event_name:n, event_count:keyEventCounts[n], is_key:keyEventKeys.has(n)}))
        .sort((a,b)=>b.event_count-a.event_count);
      // Until the client saved a set or the user edits it, mirror GA4's own key events.
      if (!keyEventUserTouched && !GA4_KEY_EVENTS_SAVED.length && keyEventKeys.size) {
        selectedKeyEvents=new Set(keyEventKeys);
      }
      renderKeyEventDropdown();
    }
    function keSum(map, key) {
      const evs=map[key]||{};
      let s=0; for (const ev of selectedKeyEvents) s+=(evs[ev]||0); return s;
    }
    function applyLanding() {
      if (!Object.keys(landingEventMap).length) { landingRows=landingBaseRows.slice(); return; }
      landingRows=landingBaseRows.map(r=>{
        const ke=keSum(landingEventMap, r.page_path);
        const rate=r.sessions?Math.round(ke/r.sessions*1000)/10:0;
        return {...r, key_events:ke, key_event_rate:rate};
      });
    }
    function applyTrafficSources() {
      // Only trust the override map once its own fetch actually returned rows —
      // a missing/unprovisioned events view must fall back to the base report's
      // key_events, not silently zero everything out.
      const hasOverride=Object.keys(trafficSourceEventMap).length>0;
      trafficSources=trafficBaseSources.map(r=>{
        const ke=hasOverride?keSum(trafficSourceEventMap, srcKey(r.source,r.medium)):num(r.key_events);
        return {...r, key_events:ke};
      });
    }
    function applyUserAcqSources() {
      const hasOverride=Object.keys(userAcqSourceEventMap).length>0;
      userAcqSources=userAcqBaseSources.map(r=>{
        const ke=hasOverride?keSum(userAcqSourceEventMap, srcKey(r.source,r.medium)):num(r.key_events);
        const rate=r.new_users?Math.round(ke/r.new_users*1000)/10:0;
        return {...r, key_events:ke, key_event_rate:rate};
      });
    }
    // Re-run every loaded panel against the current selection.
    function applyKeyEventsAll() {
      if (pagesTopRows.length || pagesSourceRows.length) { pagesPageNum=1; renderPages(); }
      if (landingBaseRows.length) { applyLanding(); landingPageNum=1; renderLanding(); }
      if (trafficBaseSources.length) { applyTrafficSources(); renderTrafficSources(); }
      if (userAcqBaseSources.length) { applyUserAcqSources(); renderUserAcqSources(); }
    }
    function keyEventToggleLabel() {
      const n=selectedKeyEvents.size;
      if (!n) return 'All key events';
      if (n===1) return [...selectedKeyEvents][0];
      return n+' events selected';
    }
    function renderKeyEventDropdown() {
      const label=document.getElementById('keyEventToggleLabel');
      const list=document.getElementById('keyEventList');
      if (label) label.textContent=keyEventToggleLabel();
      if (!list) return;
      if (!keyEventCatalog.length) { list.innerHTML='<div class="ke-dd-empty">Per-event data appears after the next GA4 sync.</div>'; return; }
      const term=keyEventSearchTerm.trim().toLowerCase();
      const matches=keyEventCatalog.filter(e=>!term||e.event_name.toLowerCase().includes(term));
      if (!matches.length) { list.innerHTML='<div class="ke-dd-empty">No events match your search.</div>'; return; }
      list.innerHTML=matches.map(e=>`<label class="ke-dd-option${selectedKeyEvents.has(e.event_name)?' active':''}"><input type="checkbox"${selectedKeyEvents.has(e.event_name)?' checked':''} data-ev="${esc(e.event_name)}"><span class="ke-dd-name">${esc(e.event_name)}</span><span class="ke-dd-count">${count(e.event_count)}</span></label>`).join('');
      list.querySelectorAll('input[data-ev]').forEach(cb=>cb.addEventListener('change',()=>{
        const ev=cb.dataset.ev;
        keyEventUserTouched=true;
        if (selectedKeyEvents.has(ev)) selectedKeyEvents.delete(ev); else selectedKeyEvents.add(ev);
        if (label) label.textContent=keyEventToggleLabel();
        const opt=cb.closest('.ke-dd-option'); if (opt) opt.classList.toggle('active', selectedKeyEvents.has(ev));
        applyKeyEventsAll();
      }));
    }
    (function initKeyEventDropdown(){
      const dd=document.getElementById('keyEventDropdown');
      const toggle=document.getElementById('keyEventToggle');
      const panel=document.getElementById('keyEventPanel');
      const search=document.getElementById('keyEventSearch');
      if (!dd||!toggle||!panel) return;
      const open=()=>{ panel.hidden=false; dd.classList.add('open'); toggle.setAttribute('aria-expanded','true'); if (search) { search.value=keyEventSearchTerm; search.focus(); } };
      const close=()=>{ panel.hidden=true; dd.classList.remove('open'); toggle.setAttribute('aria-expanded','false'); };
      toggle.addEventListener('click', e=>{ e.stopPropagation(); if (panel.hidden) open(); else close(); });
      if (search) search.addEventListener('input', ()=>{ keyEventSearchTerm=search.value; renderKeyEventDropdown(); });
      document.addEventListener('click', e=>{ if (!dd.contains(e.target)) close(); });
      document.addEventListener('keydown', e=>{ if (e.key==='Escape' && !panel.hidden) { close(); toggle.focus(); } });
      // Admin footer: store the ticked events as this client's key-event set, so
      // every visitor lands on the same definition instead of GA4's own flags.
      // Saving nothing clears the stored set and falls back to GA4's key events.
      const saveBtn=document.getElementById('keyEventSaveDefault');
      const saveStatus=document.getElementById('keyEventSaveStatus');
      if (saveBtn) saveBtn.addEventListener('click', async ()=>{
        const names=[...selectedKeyEvents];
        saveBtn.disabled=true;
        if (saveStatus) { saveStatus.className='range-default-status'; saveStatus.textContent='Saving…'; }
        try {
          const r=await fetch(GA4_KEY_EVENTS_API, {
            method:'POST', credentials:'same-origin',
            headers:{ 'Content-Type':'application/json' },
            body: JSON.stringify({ event_names: names.join('\n') }),
          });
          if (!r.ok) throw new Error(r.statusText);
          if (saveStatus) { saveStatus.className='range-default-status ok'; saveStatus.textContent=names.length?'Saved as default':'Default cleared'; }
          setTimeout(close, 700);
        } catch (err) {
          if (saveStatus) { saveStatus.className='range-default-status err'; saveStatus.textContent='Save failed'; }
        } finally { saveBtn.disabled=false; }
      });
    })();
    function renderLanding() {
      let base=landingRows;
      if (landingSearchQuery) { const q=landingSearchQuery.toLowerCase(); base=base.filter(r=>String(r.page_path).toLowerCase().includes(q)); }
      base=analyticsSort(base,LANDING_SORT_COLS,landingSort);
      const el=document.getElementById('landingTable');
      if (!base.length) { el.innerHTML=`<tbody><tr><td class="empty">No landing pages match${landingSearchQuery?' "'+esc(landingSearchQuery)+'"':' this range'}.</td></tr></tbody>`; setStatus('landingStatus', landingSearchQuery?'No results':''); document.getElementById('landingPager').innerHTML=''; return; }
      const totalPages=Math.max(1,Math.ceil(base.length/LANDING_PER_PAGE));
      if (landingPageNum>totalPages) landingPageNum=totalPages;
      const startIdx=(landingPageNum-1)*LANDING_PER_PAGE, rows=base.slice(startIdx,startIdx+LANDING_PER_PAGE);
      el.innerHTML=analyticsSortHead(LANDING_SORT_COLS,landingSort,'landing')+
        `<tbody>${rows.map(r=>`<tr><td class="left"><span class="page-path" title="${esc(r.page_path)}">${esc(truncPath(r.page_path,PAGE_TABLE_PATH_MAX))}</span></td><td>${count(r.sessions)}</td><td>${count(r.users)}</td><td>${count(r.new_users)}</td><td>${count(r.key_events)}</td><td>${r.key_event_rate!=null?r.key_event_rate+'%':'—'}</td><td>${fmtDuration(r.avg_engagement_seconds)}</td></tr>`).join('')}</tbody>`;
      enableColResize('landingTable');
      setStatus('landingStatus',`${startIdx+1}–${startIdx+rows.length} of ${base.length}`+(landingSearchQuery?' (filtered)':''));
      const pager=document.getElementById('landingPager');
      if (totalPages<=1) { pager.innerHTML=''; return; }
      pager.innerHTML=`<button type="button" class="pager-btn" id="landingPrev"${landingPageNum<=1?' disabled':''}>‹ Prev</button><span class="pager-info">Page ${landingPageNum} of ${totalPages}</span><button type="button" class="pager-btn" id="landingNext"${landingPageNum>=totalPages?' disabled':''}>Next ›</button>`;
      const prev=document.getElementById('landingPrev'), next=document.getElementById('landingNext');
      if (prev) prev.onclick=()=>{if(landingPageNum>1){landingPageNum--;renderLanding();}};
      if (next) next.onclick=()=>{if(landingPageNum<totalPages){landingPageNum++;renderLanding();}};
    }
    async function loadLandingPages() {
      setStatus('landingStatus','Loading…');
      document.getElementById('landingTable').innerHTML = skelTable(7,7);
      try {
        const [pages, ev] = await Promise.all([
          getJson(withDates(LANDING_PAGES_API)),
          getJson(withDates(LANDING_EVENTS_API)).catch(()=>({rows:[],events:[]})),
        ]);
        landingBaseRows = pages.rows||[];
        // Build per-page event map, then fold this panel's events into the shared catalog.
        landingEventMap={};
        for (const r of (ev.rows||[])) {
          (landingEventMap[r.page_path]=landingEventMap[r.page_path]||{})[r.event_name]=num(r.event_count);
        }
        mergeEvents(ev.events);
        applyLanding(); landingPageNum=1; renderLanding();
      } catch(err) { setStatus('landingStatus',err.message||String(err),true); }
    }
    (function(){
      const inp=document.getElementById('landingSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{ clearTimeout(debounce); debounce=setTimeout(()=>{landingSearchQuery=inp.value.trim();landingPageNum=1;renderLanding();},180); });
    })();
    // ---- GA4: User acquisition ----
    function renderNewVsReturning(byChannel) {
      const el=document.getElementById('newVsReturning');
      if (!el) return;
      const totalNew=byChannel.reduce((s,r)=>s+num(r.new_users),0);
      const totalActive=byChannel.reduce((s,r)=>s+num(r.active_users),0);
      const totalRet=Math.max(0,totalActive-totalNew);
      const total=totalNew+totalRet||1;
      const newPct=totalNew/total*100, retPct=totalRet/total*100;
      el.innerHTML=`<div class="nr-wrap">
        <div class="nr-stat"><span class="nr-stat-label">New users</span><span class="nr-stat-value">${count(totalNew)}</span><span class="nr-stat-pct">${newPct.toFixed(0)}%</span></div>
        <div class="nr-stat"><span class="nr-stat-label">Returning</span><span class="nr-stat-value">${count(totalRet)}</span><span class="nr-stat-pct">${retPct.toFixed(0)}%</span></div>
        <div class="nr-bar-wrap">
          <div class="nr-bar"><div class="nr-bar-new" style="width:${newPct.toFixed(1)}%"></div><div class="nr-bar-ret" style="width:${retPct.toFixed(1)}%"></div></div>
          <div class="nr-legend">
            <span class="nr-legend-item"><span class="nr-legend-swatch" style="background:#1d6fd0"></span>New</span>
            <span class="nr-legend-item"><span class="nr-legend-swatch" style="background:#c3d9f5"></span>Returning</span>
          </div>
        </div>
      </div>`;
    }
    const USERACQ_SRC_PER_PAGE=10; let userAcqSrcPage=1;
    const userAcqChanState={page:1};
    function renderUserAcqSources() {
      const rows=userAcqSources||[];
      const totalPages=Math.max(1,Math.ceil(rows.length/USERACQ_SRC_PER_PAGE));
      if (userAcqSrcPage>totalPages) userAcqSrcPage=totalPages;
      const startIdx=(userAcqSrcPage-1)*USERACQ_SRC_PER_PAGE;
      renderTable('userAcqSourceTable',[
        {key:'source',label:'Source',left:true},
        {key:'medium',label:'Medium',left:true},
        {key:'new_users',label:'New users',format:count},
        {key:'key_events',label:'Key events',format:count},
        {key:'key_event_rate',label:'KE rate',format:v=>v!=null?v+'%':'—'},
      ], rows.slice(startIdx,startIdx+USERACQ_SRC_PER_PAGE), 'No source data.');
      const pager=document.getElementById('userAcqSourcePager');
      if (!pager) return;
      if (totalPages<=1) { pager.innerHTML=''; return; }
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${userAcqSrcPage<=1?' disabled':''}>‹ Prev</button><span class="pager-info">Page ${userAcqSrcPage} of ${totalPages}</span><button type="button" class="pager-btn" data-dir="next"${userAcqSrcPage>=totalPages?' disabled':''}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{ userAcqSrcPage+=b.dataset.dir==='next'?1:-1; renderUserAcqSources(); });
    }
    async function loadUserAcquisition() {
      setStatus('userAcqStatus','Loading…');
      document.getElementById('newVsReturning').innerHTML=`<div class="nr-wrap"><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="nr-bar-wrap"><div class="skel" style="height:10px;border-radius:5px"></div></div></div>`;
      document.getElementById('userAcqChannelBars').innerHTML = skelBars(5);
      document.getElementById('userAcqSourceTable').innerHTML = skelTable(5,5);
      try {
        const [payload, ev] = await Promise.all([
          getJson(withDates(USER_ACQ_API)),
          getJson(withDates(USER_ACQ_KEY_EVENTS_API)).catch(()=>({by_source_events:[],events:[]})),
        ]);
        renderNewVsReturning(payload.by_channel||[]);
        userAcqChanState.page=1;
        renderBarListPaged('userAcqChannelBars','userAcqChannelPager',payload.by_channel||[],'new_users','channel',userAcqChanState);
        userAcqSrcPage=1;
        userAcqBaseSources = payload.by_source||[];
        userAcqSourceEventMap={};
        for (const r of (ev.by_source_events||[])) {
          const k=srcKey(r.source,r.medium);
          (userAcqSourceEventMap[k]=userAcqSourceEventMap[k]||{})[r.event_name]=num(r.event_count);
        }
        mergeEvents(ev.events);
        applyUserAcqSources(); renderUserAcqSources();
        setStatus('userAcqStatus','');
      } catch(err) { setStatus('userAcqStatus',err.message||String(err),true); }
    }

    // ---- GA4: Demographics ----
    // Age bracket, with a toggle for the "unknown" bucket (hidden by default so
    // the real brackets read clearly). Rows are cached so toggling is instant.
    let demoAgeRows=[];
    function renderAge() {
      const showUnknown=!!(document.getElementById('ageUnknownToggle')||{}).checked;
      const rows=demoAgeRows.filter(r=>showUnknown||String(r.age_bracket).toLowerCase()!=='unknown');
      renderBarList('ageBars',rows,'users','age_bracket');
    }
    { const t=document.getElementById('ageUnknownToggle'); if (t) t.addEventListener('change', renderAge); }
    // Gender → 100% split bar + per-gender stat cards.
    const GENDER_COLORS={male:'#1d6fd0',female:'#d6336c'};
    function genderColor(g,i) { return GENDER_COLORS[String(g).toLowerCase()]||CHART_PALETTE[i%CHART_PALETTE.length]; }
    function renderGender(rows) {
      const el=document.getElementById('genderBars');
      if (!rows||!rows.length) { el.innerHTML='<div class="empty">No data.</div>'; return; }
      const ordered=[...rows].sort((a,b)=>num(b.users)-num(a.users));
      const total=ordered.reduce((s,r)=>s+num(r.users),0)||1;
      const cap=g=>String(g).replace(/\w/g,c=>c.toUpperCase());
      const seg=ordered.map((r,i)=>{const p=num(r.users)/total*100;return`<div class="gender-seg" style="width:${p.toFixed(2)}%;background:${genderColor(r.gender,i)}" title="${esc(cap(r.gender))} — ${count(r.users)} (${p.toFixed(1)}%)">${p>=10?p.toFixed(0)+'%':''}</div>`;}).join('');
      const stats=ordered.map((r,i)=>{const p=num(r.users)/total*100;return`<div class="gender-stat"><span class="gender-dot" style="background:${genderColor(r.gender,i)}"></span><div><div class="gender-stat-label">${esc(cap(r.gender))}</div><div class="gender-stat-value">${count(r.users)}</div><div class="gender-stat-pct">${p.toFixed(0)}% of known</div></div></div>`;}).join('');
      el.innerHTML=`<div class="gender-wrap"><div class="gender-bar">${seg}</div><div class="gender-stats">${stats}</div></div>`;
    }
    // Users-by-state tile-grid heat map. Each state is a labeled square on an
    // approximate US grid, shaded by its share of users; hover for exact counts.
    // Unmapped regions (non-US, territories) simply don't appear — they remain
    // visible in the cities table beside it.
    const STATE_TILES={
      'Alabama':['AL',6,6],'Alaska':['AK',0,0],'Arizona':['AZ',5,1],'Arkansas':['AR',5,4],
      'California':['CA',4,0],'Colorado':['CO',4,2],'Connecticut':['CT',3,9],'Delaware':['DE',4,9],
      'Florida':['FL',7,8],'Georgia':['GA',6,7],'Hawaii':['HI',7,0],'Idaho':['ID',2,1],
      'Illinois':['IL',2,5],'Indiana':['IN',3,5],'Iowa':['IA',3,4],'Kansas':['KS',5,3],
      'Kentucky':['KY',4,5],'Louisiana':['LA',6,4],'Maine':['ME',0,10],'Maryland':['MD',4,8],
      'Massachusetts':['MA',2,9],'Michigan':['MI',2,7],'Minnesota':['MN',2,4],'Mississippi':['MS',6,5],
      'Missouri':['MO',4,4],'Montana':['MT',2,2],'Nebraska':['NE',4,3],'Nevada':['NV',3,1],
      'New Hampshire':['NH',1,10],'New Jersey':['NJ',3,8],'New Mexico':['NM',5,2],'New York':['NY',2,8],
      'North Carolina':['NC',5,6],'North Dakota':['ND',2,3],'Ohio':['OH',3,6],'Oklahoma':['OK',6,3],
      'Oregon':['OR',3,0],'Pennsylvania':['PA',3,7],'Rhode Island':['RI',2,10],'South Carolina':['SC',5,7],
      'South Dakota':['SD',3,3],'Tennessee':['TN',5,5],'Texas':['TX',7,3],'Utah':['UT',4,1],
      'Vermont':['VT',1,9],'Virginia':['VA',4,7],'Washington':['WA',2,0],'West Virginia':['WV',4,6],
      'Wisconsin':['WI',2,6],'Wyoming':['WY',3,2],'District of Columbia':['DC',5,8]
    };
    function lerpColor(t) {
      // #eaf1fb (light) → #1d6fd0 (accent), gamma-eased so mid values read.
      const a=[234,241,251], b=[29,111,208], e=Math.sqrt(Math.max(0,Math.min(1,t)));
      return 'rgb('+a.map((v,i)=>Math.round(v+(b[i]-v)*e)).join(',')+')';
    }
    function renderStateMap(regionRows) {
      const host=document.getElementById('stateMap');
      if (!host) return;
      // Users drive the shading; sessions/new users ride along for the tooltip
      // (absent on the city-derived fallback rows, which carry users only).
      const byState={};
      for (const r of (regionRows||[])) {
        const s=byState[r.region]||(byState[r.region]={users:0,sessions:0,new_users:0,has_detail:false});
        s.users+=num(r.users);
        if (r.sessions!=null||r.new_users!=null) {
          s.sessions+=num(r.sessions); s.new_users+=num(r.new_users); s.has_detail=true;
        }
      }
      const max=Math.max(1,...Object.values(byState).map(s=>s.users));
      const TS=26, GAP=3, CELL=TS+GAP, COLS=11, ROWS=8;
      const W=COLS*CELL-GAP, H=ROWS*CELL-GAP;
      let tiles='';
      for (const [name,[ab,r,c]] of Object.entries(STATE_TILES)) {
        const s=byState[name], v=s?s.users:0, x=c*CELL, y=r*CELL;
        const fill=v>0?lerpColor(v/max):'#eef2f7';
        const txt=v/max>0.55?'#fff':'#5a6b82';
        const detail=(s&&s.has_detail&&v>0)?` · ${count(s.sessions)} sessions · ${count(s.new_users)} new`:'';
        tiles+=`<g class="state-tile"><rect x="${x}" y="${y}" width="${TS}" height="${TS}" rx="4" fill="${fill}"><title>${esc(name)} — ${count(v)} users${detail}</title></rect>`+
          `<text class="state-tile-label" x="${x+TS/2}" y="${y+TS/2+3}" text-anchor="middle" style="fill:${txt}">${ab}</text></g>`;
      }
      const hasData=Object.keys(byState).length>0;
      host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Users by US state">${tiles}</svg>`+
        (hasData?`<div class="geo-map-scale"><span>Fewer</span><span class="geo-map-scale-bar"></span><span>More</span></div>`:`<div class="empty" style="padding:12px">No state-level data for this range.</div>`);
    }
    // Users-by-country choropleth: real border paths (vendored from
    // /static/vendor/world-country-outlines.js), shaded the same way as the
    // state tile grid. GA4's country names mostly match the map's own names;
    // COUNTRY_NAME_ALIASES covers the handful that don't. A few city-states
    // and micro-territories (Singapore, Hong Kong, …) have no fillable shape
    // in the underlying map data at this scale — those with real volume get
    // a dot marker anchored to their neighbouring landmass's own path
    // coordinates instead (a lat/lon projection doesn't line up with this
    // map's pixel space); the rest simply don't appear, same as a non-US
    // region on the state map — they're still in the countries table.
    const COUNTRY_NAME_ALIASES={
      'South Korea':'Korea', 'Türkiye':'Turkey', 'Czechia':'Czech Rep.',
      'Dominican Republic':'Dominican Rep.', 'Myanmar (Burma)':'Myanmar',
      'Bosnia & Herzegovina':'Bosnia and Herz.', 'Congo - Brazzaville':'Congo',
      'North Macedonia':'Macedonia', 'Trinidad & Tobago':'Trinidad and Tobago',
      'Laos':'Lao PDR',
    };
    const COUNTRY_MARKERS={ 'Singapore':[681.0,284.5], 'Hong Kong':[706.0,231.5] };
    let worldMapNameToCode=null;
    function countryCode(name) {
      if (!worldMapNameToCode) {
        worldMapNameToCode={};
        const wm=window.SF_WORLD_MAP;
        if (wm&&wm.paths) for (const code in wm.paths) worldMapNameToCode[wm.paths[code].name]=code;
      }
      return worldMapNameToCode[COUNTRY_NAME_ALIASES[name]||name];
    }
    function renderCountryMap(countryRows) {
      const host=document.getElementById('stateMap');
      if (!host) return;
      const wm=window.SF_WORLD_MAP;
      if (!wm||!wm.paths) { host.innerHTML='<div class="empty" style="padding:12px">Map data unavailable.</div>'; return; }
      const byCode={};
      for (const r of (countryRows||[])) {
        const code=countryCode(r.country);
        if (!code) continue;
        const s=byCode[code]||(byCode[code]={users:0,sessions:0,new_users:0,has_detail:false});
        s.users+=num(r.users);
        if (r.sessions!=null||r.new_users!=null) {
          s.sessions+=num(r.sessions); s.new_users+=num(r.new_users); s.has_detail=true;
        }
      }
      const max=Math.max(1,...Object.values(byCode).map(s=>s.users),...(countryRows||[]).filter(r=>COUNTRY_MARKERS[r.country]).map(r=>num(r.users)));
      let paths='';
      for (const code in wm.paths) {
        const meta=wm.paths[code], s=byCode[code], v=s?s.users:0;
        const fill=v>0?lerpColor(v/max):'#eef2f7';
        const detail=(s&&s.has_detail&&v>0)?` · ${count(s.sessions)} sessions · ${count(s.new_users)} new`:'';
        const tip=v>0?`${meta.name} — ${count(v)} users${detail}`:`${meta.name} — no recorded users`;
        paths+=`<path class="country" d="${meta.path}" fill="${fill}"><title>${esc(tip)}</title></path>`;
      }
      let markers='', markerNames=[];
      for (const r of (countryRows||[])) {
        const pos=COUNTRY_MARKERS[r.country], v=num(r.users);
        if (pos&&v>=50) {
          const radius=4+6*Math.sqrt(v/max);
          markers+=`<circle class="territory-dot" cx="${pos[0]}" cy="${pos[1]}" r="${radius.toFixed(1)}"><title>${esc(r.country)} — ${count(v)} users (city-state, not shaded — too small to render as a filled shape at this scale)</title></circle>`;
          markerNames.push(r.country);
        }
      }
      const hasData=Object.keys(byCode).length>0||markerNames.length>0;
      host.innerHTML=`<svg viewBox="0 0 ${wm.width} ${wm.height}" role="img" aria-label="Users by country">${paths}${markers}</svg>`+
        (hasData?`<div class="geo-map-scale"><span>Fewer</span><span class="geo-map-scale-bar"></span><span>More</span></div>`:`<div class="empty" style="padding:12px">No country-level data for this range.</div>`)+
        (markerNames.length?`<div class="geo-map-note">${esc(markerNames.join(' & '))} shown as a marker, not shaded — too small to render as a filled shape at this scale.</div>`:'');
    }
    let geoView='state', tableView='cities', demoGeoPayload=null;
    function renderGeoMap() {
      if (!demoGeoPayload) return;
      document.getElementById('stateMapTitle').textContent=geoView==='country'?'Users by country':'Users by state';
      if (geoView==='country') renderCountryMap(demoGeoPayload.by_country);
      else renderStateMap(demoGeoPayload.regionRows);
    }
    const CITIES_COLUMNS=[
      {key:'city',label:'City',left:true},
      {key:'region',label:'Region',left:true},
      {key:'users',label:'Users',format:count},
      {key:'new_users',label:'New',format:v=>v!=null?count(v):'—'},
      {key:'sessions',label:'Sessions',format:count},
      {key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'},
      {key:'key_events',label:'Key events',format:count},
    ];
    // The base map only has real border shapes for 176 of GA4's ~249 possible
    // country values (it drops micro-states and tiny territories, same as any
    // map at this pixel width would) -- COUNTRY_MARKERS only covers the ones
    // we've seen carry real traffic so far. Rather than guess at the rest, flag
    // any country in the table that won't be shaded, so the gap is visible for
    // every client instead of just the ones we happened to notice.
    function countryHasVisual(name) { return !!(countryCode(name)||COUNTRY_MARKERS[name]); }
    const COUNTRIES_COLUMNS=[
      {key:'country',label:'Country',left:true,format:(v,row)=>countryHasVisual(row.country)?v:v+' †'},
      {key:'users',label:'Users',format:count},
      {key:'new_users',label:'New',format:v=>v!=null?count(v):'—'},
      {key:'sessions',label:'Sessions',format:count},
      {key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'},
      {key:'key_events',label:'Key events',format:count},
    ];
    function renderGeoTable() {
      if (!demoGeoPayload) return;
      document.getElementById('citiesTableTitle').textContent=tableView==='countries'?'Top countries':'Top cities';
      const note=document.getElementById('citiesTableNote');
      if (tableView==='countries') {
        const rows=demoGeoPayload.by_country;
        renderTable('citiesTable',COUNTRIES_COLUMNS,rows,'No country data.');
        const anyUnshaded=(rows||[]).some(r=>!countryHasVisual(r.country));
        note.hidden=!anyUnshaded;
        if (anyUnshaded) note.textContent='† Too small to show as a shaded shape on the map at this scale — the number here is still accurate.';
      } else {
        renderTable('citiesTable',CITIES_COLUMNS,demoGeoPayload.by_city,'No city data.');
        note.hidden=true;
      }
    }
    function bindGeoToggles() {
      const mapHost=document.getElementById('geoMapViewChips');
      if (mapHost) mapHost.querySelectorAll('.chip').forEach(btn=>btn.addEventListener('click',()=>{
        geoView=btn.dataset.view;
        mapHost.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b===btn));
        renderGeoMap();
      }));
      const tableHost=document.getElementById('geoTableViewChips');
      if (tableHost) tableHost.querySelectorAll('.chip').forEach(btn=>btn.addEventListener('click',()=>{
        tableView=btn.dataset.view;
        tableHost.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b===btn));
        renderGeoTable();
      }));
    }
    bindGeoToggles();
    async function loadDemographics() {
      setStatus('demoStatus','Loading…');
      document.getElementById('stateMap').innerHTML = `<div class="skel" style="height:200px;border-radius:8px"></div>`;
      document.getElementById('citiesTable').innerHTML = skelTable(5,5);
      document.getElementById('ageBars').innerHTML = skelBars(5);
      document.getElementById('genderBars').innerHTML = skelBars(2);
      try {
        const payload=await getJson(withDates(DEMOGRAPHICS_API));
        // Under a page-path scope the geography comes from the page-scoped geo
        // table, which only exists once a GA4 sync has written it. Until then
        // say so — falling back to the site-wide table here would present
        // whole-site geography as if it were scoped to the filtered pages.
        if (payload.scoped && payload.geo_scope_available === false) {
          const msg='Page-scoped geography isn’t available yet. It appears after the next GA4 sync writes the per-page geography report.';
          demoGeoPayload=null; // a stale toggle click must not redraw last load's map/table over this message
          document.getElementById('stateMap').innerHTML=`<div class="empty" style="padding:12px">${esc(msg)}</div>`;
          renderTable('citiesTable',[{key:'city',label:'City',left:true}],[],msg);
          setStatus('demoStatus','');
          return;
        }
        // Prefer the accurate state rollup; fall back to summing the top cities.
        let regionRows=payload.by_region;
        if (!regionRows||!regionRows.length) {
          const agg={};
          for (const r of (payload.by_city||[])) if (r.region) agg[r.region]=(agg[r.region]||0)+num(r.users);
          regionRows=Object.entries(agg).map(([region,users])=>({region,users}));
        }
        // Eng. rate is served derived (engaged ÷ sessions); it reads '—' only for
        // dates synced before the geo report carried engaged_sessions.
        demoGeoPayload={ regionRows, by_country:payload.by_country||[], by_city:payload.by_city||[] };
        renderGeoMap();
        renderGeoTable();
        demoAgeRows=payload.by_age||[];
        renderAge();
        renderGender(payload.by_gender||[]);
        setStatus('demoStatus','');
      } catch(err) { setStatus('demoStatus',err.message||String(err),true); }
    }

    // ---- GA4: Sessions & engagement ----
    // Four cards -- Total sessions, New users, Engagement rate and Avg session
    // duration -- read off one endpoint's daily rows, above a trend chart they
    // act as the picker for. They multi-select the way the paid-trends and
    // Campaign Explorer cards do: a click toggles that metric onto or off the
    // chart rather than replacing the selection, so sessions and engagement
    // rate can be read against each other. Every card always shows its own
    // figure, so the row reads as four live stats whatever is plotted.
    //
    // Weekly by default, with a Daily/Weekly toggle (see seBindGranChips): a
    // single day's average session duration is a mean over a handful of visits,
    // so one long session moves it several minutes, but the daily shape is what
    // someone wants when they are looking at a campaign week. All four metrics
    // bucket the same way, for a consistent x-axis.
    //
    // Each tip is one plain sentence -- these cards are the first thing a
    // client sees on the page, and a paragraph about session-weighting told
    // them less than a sentence about visits. Under a page-path scope the
    // filter bar at the top of the pane already says which pages the numbers
    // cover, so the cards don't repeat it.
    const AVG_DUR_METRICS = [
      {
        key:'sessions', label:'Total sessions', color:'#1d6fd0',
        fmt: v => v==null ? '—' : count(v),
        tip:'How many visits the site got.',
      },
      {
        key:'new_users', label:'New users', color:'#0a7f3f',
        fmt: v => v==null ? '—' : count(v),
        tip:'How many of those visitors were on the site for the first time.',
      },
      {
        key:'engagement_rate', label:'Engagement rate', color:'#7c3aed',
        fmt: v => v==null ? '—' : num(v).toFixed(1)+'%',
        tip:'The share of visits where someone actually did something — stayed a while, looked at another page, or completed something.',
      },
      {
        key:'avg_session_duration_seconds', label:'Avg session duration', color:'#b8600a',
        fmt: v => v==null ? '—' : fmtDuration(v),
        tip:'How long the average visit lasted.',
      },
    ];
    // One implementation, two placements: the card at the top of Website
    // Analytics, and the Overview home's "Website analytics" panel, which used
    // to be a sessions-only line and is now the same four cards and chart.
    // Each placement owns its own selection, cache and element ids; everything
    // below is shared between them.
    function seMake(ids) { return { ids, sel:new Set(['sessions']), gran:'weekly', cache:{ cur:[], prev:[] } }; }
    const seAnalytics = seMake({ cards:'avgDurCards', chart:'avgDurTrendChart', legend:'avgDurTrendLegend', status:'avgDurStatus', warn:'avgDurCmpWarn', gran:'avgDurGranChips' });
    const seOverview  = seMake({ cards:'ovSeCards',   chart:'ovSessionsTrend',  legend:'ovSessionsLegend',  status:'ovSessionsStatus', warn:null, gran:'ovSeGranChips' });
    // Daily/Weekly, weekly by default: the endpoint returns days, and a day is
    // worth plotting for sessions and new users, but a single day's average
    // session duration is a mean over a handful of visits, so one long session
    // moves it several minutes. Weekly leads; the toggle is there for anyone
    // who wants to see which day of the week the traffic landed on. Purely a
    // re-render -- both buckets come out of the same cached daily rows.
    function seBindGranChips(inst) {
      const host = document.getElementById(inst.ids.gran);
      if (!host) return;
      host.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {
        inst.gran = btn.dataset.gran;
        host.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b === btn));
        if (inst.cache.cur.length) renderSeTrend(inst);
      }));
    }
    seBindGranChips(seAnalytics);
    seBindGranChips(seOverview);
    // Selected metrics, read back in AVG_DUR_METRICS order so the series,
    // colours and legend line up however the cards were clicked. Never empty:
    // clicking the last active card is a no-op rather than an empty chart.
    function seDefs(inst) {
      const defs = AVG_DUR_METRICS.filter(m => inst.sel.has(m.key));
      return defs.length ? defs : [AVG_DUR_METRICS[0]];
    }
    // Weeks start Monday, and each figure is re-weighted (or summed) from its
    // days' sessions: averaging the daily averages would let a dead Sunday
    // count for as much as a busy Tuesday.
    function avgDurWeekly(daily) {
      if (!daily || !daily.length) return [];
      const out = []; let cur = null;
      for (const d of daily) {
        const dt = new Date(String(d.date) + 'T00:00:00');
        const dow = (dt.getDay() + 6) % 7;            // 0 = Monday
        const mon = new Date(dt); mon.setDate(dt.getDate() - dow);
        const key = `${mon.getFullYear()}-${String(mon.getMonth()+1).padStart(2,'0')}-${String(mon.getDate()).padStart(2,'0')}`;
        if (!cur || cur.date !== key) { cur = { date:key, sessions:0, new_users:0, engaged_sessions:0, engagement_base:0, secs:0 }; out.push(cur); }
        const sess = num(d.sessions);
        cur.sessions += sess;
        cur.new_users += num(d.new_users);
        cur.engaged_sessions += num(d.engaged_sessions);
        cur.engagement_base += num(d.engagement_base_sessions);
        cur.secs += num(d.avg_session_duration_seconds) * sess;
      }
      return out.map(w => ({
        date: w.date,
        sessions: w.sessions,
        new_users: w.new_users,
        avg_session_duration_seconds: w.sessions ? w.secs / w.sessions : 0,
        engagement_rate: w.engagement_base ? w.engaged_sessions / w.engagement_base * 100 : 0,
      }));
    }
    // A metric's session-weighted figure over a whole set of daily rows --
    // what each card quotes for the range, and the basis of its "vs previous"
    // delta and the chart legend.
    function avgDurMetricValue(daily, key) {
      if (!daily || !daily.length) return null;
      let sess = 0, secs = 0, engaged = 0, engBase = 0, newUsers = 0;
      for (const d of daily) {
        const s = num(d.sessions);
        sess += s; secs += num(d.avg_session_duration_seconds) * s;
        engaged += num(d.engaged_sessions); engBase += num(d.engagement_base_sessions);
        newUsers += num(d.new_users);
      }
      if (key === 'sessions') return sess || null;
      if (key === 'new_users') return newUsers || null;
      // Engagement rate divides the session count that came back with
      // engaged_sessions, not the landing-page one -- see fetch_session_duration.
      if (key === 'engagement_rate') return engBase ? engaged / engBase * 100 : null;
      if (!sess) return null;
      if (key === 'avg_session_duration_seconds') return secs / sess;
      return null;
    }
    function renderSeCards(inst) {
      const host = document.getElementById(inst.ids.cards);
      if (!host) return;
      host.innerHTML = AVG_DUR_METRICS.map((m, i) => {
        const v = avgDurMetricValue(inst.cache.cur, m.key);
        const pv = avgDurMetricValue(inst.cache.prev, m.key);
        const tip = esc(m.tip || '');
        const on = inst.sel.has(m.key);
        // The first and last bubbles align to their own card edge so they stay
        // inside the panel instead of hanging off it.
        const edge = i === 0 ? ' ps-tip--start' : (i === AVG_DUR_METRICS.length - 1 ? ' ps-tip--end' : '');
        return `<button type="button" class="card metric-card ps-tip ps-tip--wide${edge}${on?' active':''}" `
          + `data-metric="${esc(m.key)}" data-tip="${tip}" aria-pressed="${on?'true':'false'}">`
          + `<div class="card-title">${esc(m.label)}</div>`
          + `<div class="card-value">${m.fmt(v)}</div>${deltaHtml(v,pv)}`
          + `<span class="sr-only">${tip}</span></button>`;
      }).join('');
      host.querySelectorAll('.metric-card').forEach(btn => btn.addEventListener('click', () => {
        const k = btn.dataset.metric;
        // Last one standing stays on -- an empty selection has nothing to plot.
        if (inst.sel.has(k)) { if (inst.sel.size === 1) return; inst.sel.delete(k); }
        else inst.sel.add(k);
        host.querySelectorAll('.metric-card').forEach(b => {
          const on = inst.sel.has(b.dataset.metric);
          b.classList.toggle('active', on);
          b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        renderSeTrend(inst);
      }));
    }
    // Daily rows come back without a rate on them -- the same engaged/base
    // division the weekly bucket does, one day at a time.
    function avgDurDaily(daily) {
      return (daily || []).map(d => ({
        date: String(d.date),
        sessions: num(d.sessions),
        new_users: num(d.new_users),
        avg_session_duration_seconds: num(d.avg_session_duration_seconds),
        engagement_rate: num(d.engagement_base_sessions) ? num(d.engaged_sessions) / num(d.engagement_base_sessions) * 100 : 0,
      }));
    }
    function seBucket(inst, daily) {
      return inst.gran === 'daily' ? avgDurDaily(daily) : avgDurWeekly(daily);
    }
    function renderSeTrend(inst) {
      drawSeTrend(inst, seBucket(inst, inst.cache.cur), seBucket(inst, inst.cache.prev));
    }
    // Re-drawn (not just re-pinned) when the timeline changes, so the canvas
    // rules and the DOM pins are rebuilt from one pass.
    registerAnnotatedChart(() => {
      if (seAnalytics.cache.cur.length) renderSeTrend(seAnalytics);
      if (seOverview.cache.cur.length) renderSeTrend(seOverview);
    });
    function drawSeTrend(inst, rows, prevRows) {
      clearSkelChart(inst.ids.chart);
      const legend = document.getElementById(inst.ids.legend);
      const defs = seDefs(inst);
      const multi = defs.length > 1;
      const primary = defs[0];
      const n = rows.length;
      if (!n) { __destroyChart(inst.ids.chart); if (legend) legend.innerHTML = ''; return; }
      const sess = rows.map(r => num(r.sessions));
      const labels = rows.map(r => String(r.date).slice(5));
      const hasPrev = (prevRows || []).length > 0;
      // One metric: the prior period rides behind it, dashed, and the axis
      // carries tick labels. Two or more: the metrics differ by orders of
      // magnitude (sessions against a percentage), so -- the same rule the
      // paid-trends multi-select follows -- every extra series gets its own
      // hidden auto-scaled axis, the area fill and the ticks come off, and the
      // shapes are the comparison. The legend carries each metric's figure and
      // its vs-previous delta either way, so nothing is lost by dropping the
      // dashed overlay instead of drawing two lines per metric.
      const series = [], extraScales = {};
      if (!multi) {
        const prevVals = (prevRows || []).map(r => num(r[primary.key]));
        // Previous first so the current line draws over it.
        if (hasPrev) series.push({ label:cmpSeriesLabel(), data:prevVals.slice(0,n), color:'#9aa7bd', dashed:true, fmt:primary.fmt });
        series.push({ label:primary.label, data:rows.map(r => num(r[primary.key])), color:primary.color, fill:true, fmt:primary.fmt });
      } else defs.forEach((m, i) => {
        const axisId = i === 0 ? 'y' : ('y' + i);
        if (i > 0) extraScales[axisId] = { display:false, beginAtZero:true };
        series.push({ label:m.label, data:rows.map(r => num(r[m.key])), color:m.color, fmt:m.fmt, axisId });
      });
      lineChart(inst.ids.chart, labels, series, {
        yDisplay: !multi,
        yFmt: v => primary.fmt(v),
        extraScales,
        tooltip: {
          title: items => {
            const i = (items && items.length) ? items[0].dataIndex : -1;
            if (i < 0) return '';
            return inst.gran === 'daily' ? String(rows[i].date) : `Week of ${String(rows[i].date)}`;
          },
          label: c => `${c.dataset.label}: ${c.dataset._fmt(c.raw)}`,
          // How many sessions the point is built from, so a freak one-visit
          // week reads as exactly that rather than as a great week. Skipped
          // when Total sessions is itself on the chart, where it would just
          // repeat a line above it.
          afterBody: items => {
            if (inst.sel.has('sessions')) return '';
            const i = (items && items.length) ? items[0].dataIndex : -1;
            return i < 0 ? '' : `${count(sess[i])} session${sess[i] === 1 ? '' : 's'}`;
          },
        },
        dates: rows.map(r => String(r.date)),
      });
      if (!legend) return;
      const range = multi ? '' : ` (${esc(currentStart.slice(5))} – ${esc(currentEnd.slice(5))})`;
      legend.innerHTML = defs.map(m => {
        const cur = avgDurMetricValue(inst.cache.cur, m.key);
        const prev = avgDurMetricValue(inst.cache.prev, m.key);
        const delta = (cur != null && prev != null && prev !== 0) ? ((cur - prev) / prev * 100) : null;
        const deltaTxt = delta == null ? '' : ` <span class="cmp-delta ${delta>=0?'up':'down'}">${delta>=0?'+':''}${delta.toFixed(0)}%</span>`;
        return `<span class="cmp-item"><span class="cmp-swatch" style="border-top-color:${m.color}"></span>${esc(m.label)}${range} · ${m.fmt(cur)}${deltaTxt}</span>`;
      }).join('')
        + ((!multi && hasPrev)
          ? `<span class="cmp-item"><span class="cmp-swatch prev"></span>${esc(cmpSeriesLabel())} (${esc(compareMode==='prev_year'?compareStart:compareStart.slice(5))} – ${esc(compareMode==='prev_year'?compareEnd:compareEnd.slice(5))}) · ${primary.fmt(avgDurMetricValue(inst.cache.prev, primary.key))}</span>`
          : '');
    }
    async function loadSeInstance(inst) {
      const cards = document.getElementById(inst.ids.cards);
      // The Overview panel is one of several optional cards, so its half of
      // this may not be on the page at all.
      if (!cards) return;
      setStatus(inst.ids.status,'Loading…');
      cards.innerHTML = skelCards(4);
      skelChart(inst.ids.chart,'trend-md-svg');
      try {
        const [cur, prev] = await Promise.all([
          getJson(withDatesRange(SESSION_DURATION_API, currentStart, currentEnd)),
          compareStart ? getJson(withDatesRange(SESSION_DURATION_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
        ]);
        inst.cache = { cur: (cur && cur.daily) || [], prev: (prev && prev.daily) || [] };
        renderSeCards(inst);
        renderSeTrend(inst);
        if (inst.ids.warn) setCmpWarn(inst.ids.warn, ['google_analytics']);
        const hasData = inst.cache.cur.some(d => num(d.sessions) > 0);
        setStatus(inst.ids.status, hasData ? '' : 'No data for this range yet.');
      } catch(err) {
        cards.innerHTML = '';
        clearSkelChart(inst.ids.chart);
        __destroyChart(inst.ids.chart);
        setStatus(inst.ids.status, err.message||String(err), true);
      }
    }
    function loadSessionDuration() { return loadSeInstance(seAnalytics); }
    // ---- Loaders ----
    function loadAllAnalytics() {
      // Staggered, not simultaneous: 8 modules x 1-3 sub-fetches each means
      // ~12-14 concurrent BigQuery queries if fired all at once, which was
      // intermittently tripping transient 500s under load. Spreading module
      // starts out keeps peak concurrency down without a noticeable delay.
      const modules=getModules();
      const loaders=[];
      if (modules.top_pages)        loaders.push(loadPages);
      if (modules.traffic)          loaders.push(loadTrafficAcq);
      if (modules.audience)         loaders.push(loadDeviceSplit);
      if (modules.landing)          loaders.push(loadLandingPages);
      if (modules.user_acquisition) loaders.push(loadUserAcquisition);
      if (modules.avg_duration)     loaders.push(loadSessionDuration);
      if (modules.demographics)     loaders.push(loadDemographics);
      loaders.forEach((fn,i)=>setTimeout(fn, i*250));
    }
    // ---- Overview home: a widget per section, each with a "See more" jump ----
    // The Website analytics panel is the same Sessions & engagement card the
    // Analytics tab opens with -- four multi-select metric cards over a Daily/Weekly
    // trend (see seOverview above) -- so the number a client reads on the home
    // page is the number they see when they click through. The AI traffic panel
    // still overlays the equivalent prior period (compareStart/compareEnd) and
    // keeps its Daily/Weekly toggle, re-rendering from cache without
    // refetching. Search Console shows branded vs. target keyword
    // rank-distribution bands side by side.
    let ovAiGran='daily';
    let ovAiCache={cur:[],prev:[]};
    // Collapse daily rows to one {date,value} per date (AI rows are per-platform,
    // so this also sums sessions across assistants for the total-traffic line).
    function ovSumByDate(rows, key) {
      const m=new Map();
      for (const r of (rows||[])) { const d=String(r.date); m.set(d,(m.get(d)||0)+num(r[key])); }
      return [...m.keys()].sort().map(d=>({date:d, value:m.get(d)}));
    }
    // Re-bucket {date,value} rows into Monday-start weeks (client-side, no refetch).
    function ovAggregateWeekly(rows) {
      if (!rows||!rows.length) return [];
      const out=[]; let cur=null;
      for (const r of rows) {
        const dt=new Date(String(r.date)+'T00:00:00');
        const dow=(dt.getDay()+6)%7;                 // 0 = Monday
        const mon=new Date(dt); mon.setDate(dt.getDate()-dow);
        const key=`${mon.getFullYear()}-${String(mon.getMonth()+1).padStart(2,'0')}-${String(mon.getDate()).padStart(2,'0')}`;
        if (!cur||cur.date!==key) { cur={date:key, value:0}; out.push(cur); }
        cur.value+=num(r.value);
      }
      return out;
    }
    // Current-vs-previous line, aligned by index (previous mapped onto the
    // current labels).
    function ovDrawCompareTrend(chartId, legendId, curRows, prevRows, color, unit) {
      const n=curRows.length;
      const lg=document.getElementById(legendId);
      if (!n) { __destroyChart(chartId); if(lg) lg.innerHTML=''; return; }
      const vals=curRows.map(d=>num(d.value));
      const prevVals=(prevRows||[]).map(d=>num(d.value));
      const hasPrev=prevVals.length>0;
      const labels=curRows.map(d=>String(d.date).slice(5));
      const series=[];
      if (hasPrev) series.push({label:cmpSeriesLabel(), data:prevVals.slice(0,n), color:'#9aa7bd', dashed:true});
      series.push({label:'Current', data:vals, color, fill:true});
      lineChart(chartId, labels, series, {
        yFmt: v=>count(v),
        tooltip: { label: c=>`${c.dataset.label}: ${count(c.raw)} ${unit}` },
        dates: curRows.map(d=>String(d.date)),
      });
      if (lg) {
        const curTot=vals.reduce((a,b)=>a+b,0), prevTot=prevVals.reduce((a,b)=>a+b,0);
        const delta=(hasPrev&&prevTot)?((curTot-prevTot)/prevTot*100):null;
        const deltaTxt=delta==null?'':` <span class="cmp-delta ${delta>=0?'up':'down'}">${delta>=0?'+':''}${delta.toFixed(0)}%</span>`;
        lg.innerHTML=`<span class="cmp-item"><span class="cmp-swatch cur"></span>Current · ${count(curTot)} ${unit}${deltaTxt}</span>`
          + (hasPrev?`<span class="cmp-item"><span class="cmp-swatch prev"></span>${esc(cmpSeriesLabel())} · ${count(prevTot)}</span>`:'');
      }
    }
    function ovRenderAi() {
      const c=ovAiCache, wk=ovAiGran==='weekly';
      ovDrawCompareTrend('ovAiTrend','ovAiLegend',
        wk?ovAggregateWeekly(c.cur):c.cur, wk?ovAggregateWeekly(c.prev):c.prev, '#7c3aed', 'sessions');
    }
    // Site Performance scorecard (the four Lighthouse scores) — reuses psScoreCard
    // + PAGESPEED_TARGETS from the Site Performance pane's JS. Emitted only when
    // the pagespeed connector is present (Python gates the #ovPsScores element).
    async function loadOverviewPagespeed() {
      const host=document.getElementById('ovPsScores');
      if (!host) return;
      setStatus('ovPsStatus','Loading…');
      host.innerHTML=skelCards(4);
      const strat=(typeof PS_STRATEGIES!=='undefined'&&PS_STRATEGIES.length)?PS_STRATEGIES[0]:'desktop';
      const url=PAGESPEED_API+(PAGESPEED_API.includes('?')?'&':'?')+'strategy='+strat;
      try {
        const p=await getJson(url);
        if (!p||!p.url) { host.innerHTML=''; setStatus('ovPsStatus','No PageSpeed data yet'); return; }
        const scores=[['Performance','performance',p.performance],['Accessibility','accessibility',p.accessibility],
          ['Best Practices','best_practices',p.best_practices],['SEO','seo',p.seo]];
        host.innerHTML=scores.map(([l,k,v])=>psScoreCard(l,v,PAGESPEED_TARGETS[k],PS_SCORE_TIPS[k])).join('');
        setStatus('ovPsStatus', p.metric_date?('measured '+p.metric_date):'');
      } catch(err) { host.innerHTML=''; setStatus('ovPsStatus', err.message||String(err), true); }
    }
    // Overview Search Console leaderboard: the top keywords by current rank
    // (best position first) — just keyword, position, and movement vs. the prior
    // period. Full sortable/paginated tables live on the Search Console tab; this
    // is the at-a-glance snapshot next to the rank-distribution trend.
    const OV_KW_LEADERS = 5;
    function renderKwLeaderboard(tableId, rows, configured) {
      const el=document.getElementById(tableId); if(!el) return;
      if (!configured) { el.innerHTML=`<tbody><tr><td class="empty">No keywords set — add them on the Search Console tab.</td></tr></tbody>`; return; }
      // Ordered by impressions, not by rank. Sorting on avg_position floated
      // whichever long-tail phrasing happened to sit at #1 (a PDF filename, say)
      // above the brand term itself, so the Overview's top row disagreed with
      // the Search Console tab's. Impressions put the keywords people actually
      // search first, and match how the full table reads.
      const top=(rows||[]).slice()
        .sort((a,b)=>num(b.impressions)-num(a.impressions) || num(b.clicks)-num(a.clicks))
        .slice(0,OV_KW_LEADERS);
      if (!top.length) { el.innerHTML=`<tbody><tr><td class="empty">No matching queries in this range.</td></tr></tbody>`; return; }
      const head=`<thead><tr><th class="left">Keyword</th><th>Impressions</th><th>Position</th>${compareStart?'<th>Movement</th>':''}</tr></thead>`;
      const body=top.map(r=>`<tr><td class="left" title="${esc(r.query)}"><span>${esc(r.query)}</span></td><td>${count(r.impressions)}</td><td>${gscPos(r.avg_position)}</td>${compareStart?`<td>${gscDelta(r.delta_position)}</td>`:''}</tr>`).join('');
      el.innerHTML=head+`<tbody>${body}</tbody>`;
    }
    // Website analytics + AI traffic trends. Every other Overview card is
    // optional — connector-gated in Python, or hidden by an admin — so this
    // loader must never depend on one of them being in the DOM. It used to run
    // in the same function as the Search Console block, which meant a client
    // without GSC (a freshly connected GA4-only client, say) hit a TypeError on
    // the missing #ovGscBrandedLeaders before either chart drew, leaving both
    // panels blank.
    async function loadOverviewTrends() {
      // Website analytics — the shared Sessions & engagement card, which owns
      // its own fetch, cards, chart and status line.
      loadSeInstance(seOverview);
      setStatus('ovAiStatus','Loading…');
      const hasPrev=!!compareStart;
      try {
        const [aiCur, aiPrev] = await Promise.all([
          getJson(withDatesRange(AI_TRAFFIC_DAILY_API, currentStart, currentEnd)).then(d=>d.rows||[]).catch(()=>[]),
          hasPrev ? getJson(withDatesRange(AI_TRAFFIC_DAILY_API, compareStart, compareEnd)).then(d=>d.rows||[]).catch(()=>[]) : Promise.resolve([]),
        ]);
        // AI traffic — total AI sessions, current vs previous.
        ovAiCache={ cur: ovSumByDate(aiCur,'sessions'), prev: ovSumByDate(aiPrev,'sessions') };
        ovRenderAi();
        const aTot=ovAiCache.cur.reduce((s,d)=>s+num(d.value),0);
        setStatus('ovAiStatus', aTot?count(aTot)+' sessions':'No AI traffic in this range.');
      } catch(err) {
        setStatus('ovAiStatus', err.message||String(err), true);
      }
    }
    // Search Console — branded & target keyword leaderboard (top by impressions) plus
    // the weekly avg-position trend over time. The whole card is dropped when
    // GSC isn't connected (and can be hidden by an admin), so bail out unless
    // it's actually on the page.
    async function loadOverviewGsc() {
      const brandedEl=document.getElementById('ovGscBrandedLeaders');
      const targetEl=document.getElementById('ovGscTargetLeaders');
      if (!brandedEl && !targetEl) return;
      setStatus('ovGscStatus','Loading…');
      if (brandedEl) brandedEl.innerHTML = skelTable(3,4);
      if (targetEl) targetEl.innerHTML = skelTable(3,4);
      try {
        const [branded, target] = await Promise.all([
          fetchKeywordMatches(gscBrandedRoots, gscBrandedExclude),
          fetchKeywordMatches(gscTargetKeywords, gscTargetExclude),
        ]);
        renderKwLeaderboard('ovGscBrandedLeaders', branded.rows, gscBrandedRoots.length);
        renderKwLeaderboard('ovGscTargetLeaders', target.rows, gscTargetKeywords.length);
        drawKeywordTrend('ovGscBrandedTrend', branded.weekly, 'avg_position', '#1d6fd0', true);
        drawKeywordTrend('ovGscTargetTrend', target.weekly, 'avg_position', '#7c3aed', true);
        const noteFor=(roots, weekly)=> !roots.length ? 'Set keywords on the Search Console tab.'
          : (!(weekly||[]).length ? 'No matching queries in this range.' : '');
        const bn=document.getElementById('ovGscBrandedNote'); if(bn) bn.textContent=noteFor(gscBrandedRoots, branded.weekly);
        const tn=document.getElementById('ovGscTargetNote'); if(tn) tn.textContent=noteFor(gscTargetKeywords, target.weekly);
        setStatus('ovGscStatus', (!gscBrandedRoots.length && !gscTargetKeywords.length) ? ''
          : `${gscBrandedRoots.length} branded · ${gscTargetKeywords.length} target`);
      } catch(err) {
        setStatus('ovGscStatus', err.message||String(err), true);
      }
    }
    // Each card loads on its own so one card's failure (or absence) can't blank
    // the others.
    function loadOverviewHome() {
      loadOverviewTrends();
      loadOverviewGsc();
      // Site performance scorecard (only present when the connector is on).
      loadOverviewPagespeed();
    }
    // Daily/Weekly interval toggle for the AI traffic panel. The Website
    // analytics panel has none: it is the Sessions & engagement card, which is
    // weekly-only for the reason spelled out above it.
    document.querySelectorAll('#ovAiGranChips .chip').forEach(btn=>
      btn.addEventListener('click',()=>{
        if (btn.dataset.gran===ovAiGran) return;
        ovAiGran=btn.dataset.gran;
        document.querySelectorAll('#ovAiGranChips .chip').forEach(b=>b.classList.toggle('active', b===btn));
        ovRenderAi();
      })
    );
    document.querySelectorAll('.ov-more[data-goto]').forEach(btn=>
      btn.addEventListener('click', ()=>switchTab(btn.dataset.goto))
    );
    function loadCurrentTab() {
      // Goals are window-scoped (the stored monthly target is prorated to the
      // selected range), so they reload with the tab; benchmarks are not, and
      // loadBenchmarks() no-ops after its first success.
      if (currentTab==='overview' && HAS_PAID_ADS) { loadGoals(); loadBenchmarks(); }
      // Annotations are window-independent (the API returns the client's whole
      // timeline and each chart filters to what it is showing), so one load
      // serves every tab and every range change.
      loadAnnotations();
      // The Overview's cards start immediately and health rides alongside them.
      // It used to gate them (`loadHealth().then(...)`), which put a whole
      // BigQuery round-trip in front of the first number on the page -- and on a
      // client nobody had opened since its last sync, that read is cold, so the
      // first visit after switching clients paid it in full before anything
      // started loading. Nothing here needs it: /marketing/health only supplies
      // the earliest-synced-date warnings, and loadHealth() repaints those
      // itself whenever it lands (see refreshCmpWarns).
      if (currentTab==='overview') {
        loadHealth();
        if (HAS_PAID_ADS) loadSummary();
        loadOverviewHome();
      }
      else if (currentTab==='explorer') { explorerLoaded=false; loadExplorer(); explorerLoaded=true; }
      else if (currentTab==='analytics') { analyticsLoaded=false; applyModules(); loadAllAnalytics(); analyticsLoaded=true; }
      else if (currentTab==='ai_traffic') { aiTrafficLoaded=false; loadAiTraffic(); aiTrafficLoaded=true; }
      // Search Console reads the selected window (loadGsc builds its URL from
      // currentStart/currentEnd), so it has to reload here or the tab stays
      // pinned to whatever range was active when it first opened. loadSemrush()
      // is deliberately not called: its endpoint takes no dates.
      else if (currentTab==='gsc') { gscLoaded=false; loadGsc(); gscLoaded=true; }
      // Google Business reads the selected window too, so the same rule applies:
      // without this the tab stays pinned to the range it first opened with.
      else if (currentTab==='google_business') { googleBusinessLoaded=false; loadGoogleBusiness(); googleBusinessLoaded=true; }
    }

    // ---- Date presets ----
    // The window every "vs previous" figure on the page is measured against.
    // Which window that is depends on the Compare picker: the equivalent period
    // immediately before the selected range (the default -- e.g. "This month"
    // July 1-6 -> June 1-6), or the same range a year earlier. applyPreset()
    // computes the previous-period option into prevPeriodStart/End;
    // resolveCompare() picks between that and the year-ago shift.
    let compareStart='', compareEnd='';
    let prevPeriodStart='', prevPeriodEnd='';
    const fmtDate=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    function mondayOf(d) {
      const day=d.getDay(); const diff=(day===0?-6:1)-day;
      const m=new Date(d); m.setDate(d.getDate()+diff); return m;
    }
    // First day of the calendar quarter `d` falls in (Jan/Apr/Jul/Oct 1).
    function quarterStart(d) { return new Date(d.getFullYear(), Math.floor(d.getMonth()/3)*3, 1); }
    // Shift an ISO date back n years, clamping to the last day of the target
    // month so Feb 29 lands on Feb 28 rather than rolling into March.
    function shiftYears(iso, n) {
      const p=String(iso).split('-').map(Number);
      const y=p[0]-n, m=p[1], day=p[2];
      const daysInMonth=new Date(y, m, 0).getDate();
      return fmtDate(new Date(y, m-1, Math.min(day, daysInMonth)));
    }
    function applyPreset(name) {
      const today=new Date(); let s, e=today, cs, ce;
      const lastN=n=>{
        e=new Date(today);e.setDate(today.getDate()-1);
        s=new Date(today);s.setDate(today.getDate()-n);
        ce=new Date(s); ce.setDate(s.getDate()-1);
        cs=new Date(ce); cs.setDate(ce.getDate()-(n-1));
      };
      // GA4/GSC syncs typically lag a day or more, so "today" itself is
      // usually incomplete or entirely unsynced -- these presets end at
      // yesterday like the trailing (last_N) ones do, falling back to the
      // period start when yesterday would fall outside it (e.g. Monday for
      // "this_week", the 1st for "this_month").
      const yesterday=new Date(today); yesterday.setDate(today.getDate()-1);
      if (name==='this_week') {
        s=mondayOf(today); e=(yesterday>=s)?yesterday:s;
        cs=new Date(s); cs.setDate(s.getDate()-7);
        ce=new Date(e); ce.setDate(e.getDate()-7);
      } else if (name==='last_week') {
        const lw=new Date(today); lw.setDate(today.getDate()-7);
        s=mondayOf(lw); e=new Date(s); e.setDate(s.getDate()+6);
        cs=new Date(s); cs.setDate(s.getDate()-7);
        ce=new Date(e); ce.setDate(e.getDate()-7);
      } else if (name==='this_month') {
        s=new Date(today.getFullYear(),today.getMonth(),1);
        e=(yesterday>=s)?yesterday:s;
        const dom=e.getDate();
        const daysInPrevMonth=new Date(today.getFullYear(),today.getMonth(),0).getDate();
        cs=new Date(today.getFullYear(),today.getMonth()-1,1);
        ce=new Date(today.getFullYear(),today.getMonth()-1,Math.min(dom,daysInPrevMonth));
      } else if (name==='last_month') {
        s=new Date(today.getFullYear(),today.getMonth()-1,1); e=new Date(today.getFullYear(),today.getMonth(),0);
        cs=new Date(today.getFullYear(),today.getMonth()-2,1); ce=new Date(today.getFullYear(),today.getMonth()-1,0);
      } else if (name==='this_quarter') {
        // Quarter-to-date, ending yesterday like the other "this_*" presets.
        // Previous period = the same number of days into the prior quarter,
        // clamped to that quarter's last day (quarters differ by a day or two).
        s=quarterStart(today);
        e=(yesterday>=s)?yesterday:s;
        cs=new Date(s.getFullYear(), s.getMonth()-3, 1);
        const prevQEnd=new Date(cs.getFullYear(), cs.getMonth()+3, 0);
        const daysIn=Math.round((e-s)/86400000);
        ce=new Date(cs); ce.setDate(cs.getDate()+daysIn);
        if (ce>prevQEnd) ce=prevQEnd;
      } else if (name==='last_quarter') {
        const qs=quarterStart(today);
        s=new Date(qs.getFullYear(), qs.getMonth()-3, 1);
        e=new Date(qs.getFullYear(), qs.getMonth(), 0);
        cs=new Date(qs.getFullYear(), qs.getMonth()-6, 1);
        ce=new Date(qs.getFullYear(), qs.getMonth()-3, 0);
      } else if (name==='this_year') {
        // Year-to-date, ending yesterday like the other "this_*" presets.
        // Previous period = the same number of days into the prior year,
        // clamped to that year's last day (leap years differ by a day).
        s=new Date(today.getFullYear(),0,1);
        e=(yesterday>=s)?yesterday:s;
        cs=new Date(today.getFullYear()-1,0,1);
        const prevYEnd=new Date(today.getFullYear()-1,11,31);
        const daysIn=Math.round((e-s)/86400000);
        ce=new Date(cs); ce.setDate(cs.getDate()+daysIn);
        if (ce>prevYEnd) ce=prevYEnd;
      } else if (name==='last_7') lastN(7);
      else if (name==='last_30') lastN(30);
      else if (name==='last_90') lastN(90);
      else if (name==='last_365') lastN(365);
      else return;
      currentStart=fmtDate(s); currentEnd=fmtDate(e);
      prevPeriodStart=fmtDate(cs); prevPeriodEnd=fmtDate(ce);
      resolveCompare();
      syncRangeUI(name);
      loadCurrentTab();
    }
    // ---- Custom range ----
    // Parse a YYYY-MM-DD value as a *local* date. `new Date(iso)` would read it
    // as UTC and land on the previous day west of Greenwich, which would shift
    // every window the user picked by a day.
    function parseIsoDate(iso) {
      const p=String(iso||'').split('-').map(Number);
      if (p.length!==3 || p.some(n=>!Number.isFinite(n))) return null;
      const d=new Date(p[0], p[1]-1, p[2]);
      return isNaN(d.getTime()) ? null : d;
    }
    // A hand-picked window. Once applied it behaves exactly like a preset: the
    // comparison period is the same number of days immediately before it, so
    // every "vs previous" figure on the page keeps working (and the Compare
    // picker's "Previous year" still shifts the chosen window back 12 months).
    // Returns an error string to show in the panel, or '' on success.
    function applyCustomRange(startIso, endIso) {
      const s=parseIsoDate(startIso), e=parseIsoDate(endIso);
      if (!s || !e) return 'Pick a start and end date.';
      if (e<s) return 'The end date must be on or after the start date.';
      const days=Math.round((e-s)/86400000)+1;
      const ce=new Date(s); ce.setDate(s.getDate()-1);
      const cs=new Date(ce); cs.setDate(ce.getDate()-(days-1));
      currentStart=fmtDate(s); currentEnd=fmtDate(e);
      prevPeriodStart=fmtDate(cs); prevPeriodEnd=fmtDate(ce);
      resolveCompare();
      syncRangeUI('custom');
      loadCurrentTab();
      return '';
    }
    // ---- Comparison period ----
    // 'none' (the default), 'prev_period' or 'prev_year'. Remembered per client
    // in this browser -- it's a reading preference, not a client-wide setting.
    let compareMode=COMPARE_DEFAULT_MODE;
    try {
      const saved=localStorage.getItem(COMPARE_MODE_STORAGE_KEY);
      if (saved && COMPARE_MODE_LABELS[saved]) compareMode=saved;
      // Migration from the switch: it stored on/off separately, so an explicit
      // "off" means No comparison no matter which window is remembered, and its
      // "on" means the remembered window rather than the new default.
      const legacyOn=localStorage.getItem(COMPARE_ON_LEGACY_KEY);
      if (legacyOn==='0') compareMode='none';
      else if (legacyOn==='1' && compareMode==='none') compareMode='prev_period';
    } catch(e) {}
    // Point compareStart/compareEnd at the window the current mode asks for and
    // refresh everything that spells the comparison out in words. 'none' leaves
    // both blank -- every "vs previous" figure and every comparison-window fetch
    // on the page is gated on compareStart being truthy, so this alone turns
    // them all off.
    function resolveCompare() {
      if (compareMode==='none') {
        compareStart=''; compareEnd='';
      } else if (compareMode==='prev_year' && currentStart && currentEnd) {
        compareStart=shiftYears(currentStart,1); compareEnd=shiftYears(currentEnd,1);
      } else {
        compareStart=prevPeriodStart; compareEnd=prevPeriodEnd;
      }
      syncCompareUI();
    }
    // Wording for the comparison, used by every delta label and tooltip so the
    // page never says "vs prior period" while comparing against last year.
    function cmpNoun() { return compareMode==='prev_year' ? 'prior year' : 'prior period'; }
    function cmpSeriesLabel() { return compareMode==='prev_year' ? 'Previous year' : 'Previous'; }
    function syncCompareUI() {
      const lbl=document.getElementById('compareToggleLabel');
      if (lbl && COMPARE_MODE_LABELS[compareMode]) lbl.textContent=COMPARE_MODE_LABELS[compareMode];
      // "vs No comparison" would be nonsense, so the lead only shows once the
      // label after it names a window.
      const vs=document.getElementById('compareVs');
      if (vs) vs.hidden = compareMode==='none';
      document.querySelectorAll('#compareList .range-opt').forEach(o =>
        o.classList.toggle('active', o.dataset.cmp===compareMode));
      const foot=document.getElementById('compareRangeLabel');
      if (foot) foot.textContent = compareStart ? `${compareStart} – ${compareEnd}` : '';
      syncCompareNotice();
    }
    function setCompareMode(mode) {
      if (!COMPARE_MODE_LABELS[mode] || mode===compareMode) return;
      compareMode=mode;
      try { localStorage.setItem(COMPARE_MODE_STORAGE_KEY, mode); } catch(e) {}
      resolveCompare();
      loadCurrentTab();
    }
    (function(){
      const dd=document.getElementById('compareDropdown'); if (!dd) return;
      const toggle=document.getElementById('compareToggle');
      const panel=document.getElementById('comparePanel');
      const list=document.getElementById('compareList');
      const setOpen=o=>{ panel.hidden=!o; dd.classList.toggle('open', o); toggle.setAttribute('aria-expanded', o?'true':'false'); };
      toggle.addEventListener('click', ()=>setOpen(panel.hidden));
      document.addEventListener('click', e=>{ if (!dd.contains(e.target)) setOpen(false); });
      document.addEventListener('keydown', e=>{ if (e.key==='Escape') setOpen(false); });
      list.addEventListener('click', e=>{
        const opt=e.target.closest('.range-opt'); if (!opt) return;
        setCompareMode(opt.dataset.cmp);
        setOpen(false);
      });
    })();
    // ---- Range dropdown (custom): preset list + admin "Make default" / Apply ----
    // The preset list instant-applies on click (the panel stays open so an admin
    // can then tick "Make default" and Apply). syncRangeUI is hoisted so
    // applyPreset can refresh the toggle label + active row on every change.
    let currentPreset = DEFAULT_DATE_PRESET || 'last_30';
    function syncRangeUI(name) {
      currentPreset = name;
      const lbl = document.getElementById('rangeToggleLabel');
      // A custom window has no preset label, so the toggle spells out the dates.
      if (lbl) {
        if (name === 'custom') lbl.textContent = `${currentStart} – ${currentEnd}`;
        else if (DATE_PRESET_LABELS[name]) lbl.textContent = DATE_PRESET_LABELS[name];
      }
      document.querySelectorAll('#rangeList .range-opt').forEach(o =>
        o.classList.toggle('active', !!o.dataset.preset && o.dataset.preset === name));
      const customRow = document.getElementById('rangeCustomOpen');
      if (customRow) customRow.classList.toggle('active', name === 'custom');
      const chk = document.getElementById('rangeMakeDefault');
      if (chk) {
        chk.checked = !!STORED_DEFAULT_PRESET && STORED_DEFAULT_PRESET === name;
        // Only presets can be a client's landing range -- a one-off window is
        // not something the API stores, so "Make default" is off the table.
        chk.disabled = name === 'custom';
      }
    }
    (function(){
      const dd = document.getElementById('rangeDropdown'); if (!dd) return;
      const toggle = document.getElementById('rangeToggle');
      const panel = document.getElementById('rangePanel');
      const list = document.getElementById('rangeList');
      const setOpen = (o) => { panel.hidden=!o; dd.classList.toggle('open', o); toggle.setAttribute('aria-expanded', o?'true':'false'); };
      // The toggle deliberately lets the click bubble: the document handler
      // below ignores clicks inside this dropdown, so a click on a *sibling*
      // picker (Compare, Events) still closes this one instead of leaving two
      // panels stacked over each other.
      toggle.addEventListener('click', ()=>setOpen(panel.hidden));
      document.addEventListener('click', e=>{ if (!dd.contains(e.target)) setOpen(false); });
      document.addEventListener('keydown', e=>{ if (e.key==='Escape') setOpen(false); });
      // Custom range: the last row in the list toggles a start/end form inside
      // the panel instead of applying anything, so picking dates never costs a
      // round of loaders until the user hits Apply.
      const customRow = document.getElementById('rangeCustomOpen');
      const customBox = document.getElementById('rangeCustom');
      const customStart = document.getElementById('rangeCustomStart');
      const customEnd = document.getElementById('rangeCustomEnd');
      const customApply = document.getElementById('rangeCustomApply');
      const customErr = document.getElementById('rangeCustomErr');
      const setCustomOpen = (o) => {
        if (!customBox || !customRow) return;
        customBox.hidden = !o;
        customRow.setAttribute('aria-expanded', o ? 'true' : 'false');
        if (o) {
          // Seed from whatever is on screen, so the form opens on the range the
          // user is already looking at rather than empty fields.
          if (customStart && !customStart.value) customStart.value = currentStart;
          if (customEnd && !customEnd.value) customEnd.value = currentEnd;
          if (customStart) customStart.focus();
        } else if (customErr) {
          customErr.textContent = '';
        }
      };
      list.addEventListener('click', e=>{
        const opt = e.target.closest('.range-opt'); if (!opt) return;
        if (opt.dataset.custom) { setCustomOpen(customBox && customBox.hidden); return; }
        setCustomOpen(false);
        applyPreset(opt.dataset.preset);
      });
      if (customApply) {
        const submit = () => {
          const err = applyCustomRange(customStart.value, customEnd.value);
          customErr.textContent = err;
          if (!err) { setCustomOpen(false); setOpen(false); }
        };
        customApply.addEventListener('click', submit);
        [customStart, customEnd].forEach(inp => inp.addEventListener('keydown', ev=>{
          if (ev.key === 'Enter') { ev.preventDefault(); submit(); }
        }));
      }
      // Admin footer: persist (or clear) the applied preset as the client default.
      const apply = document.getElementById('rangeApply');
      const chk = document.getElementById('rangeMakeDefault');
      const status = document.getElementById('rangeDefaultStatus');
      if (apply && chk) {
        apply.addEventListener('click', async () => {
          // Non-destructive: tick → save the applied preset; untick → clear only a
          // stored default that matches what's applied, so viewing a non-default
          // range and hitting Apply never wipes an unrelated default.
          let preset;
          if (chk.checked) preset = currentPreset;
          else if (STORED_DEFAULT_PRESET && STORED_DEFAULT_PRESET === currentPreset) preset = '';
          else { setOpen(false); return; }
          apply.disabled = true; status.className='range-default-status'; status.textContent='Saving…';
          try {
            const r = await fetch(DEFAULT_DATE_RANGE_API, {
              method:'POST', credentials:'same-origin',
              headers:{ 'Content-Type':'application/json' },
              body: JSON.stringify({ preset }),
            });
            const b = await r.json().catch(()=>({}));
            if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
            STORED_DEFAULT_PRESET = preset;
            status.className='range-default-status ok';
            status.textContent = preset ? 'Saved as default' : 'Default cleared';
            setTimeout(()=>setOpen(false), 700);
          } catch (err) {
            status.className='range-default-status err'; status.textContent='Save failed';
          } finally {
            apply.disabled = false;
          }
        });
      }
    })();

    // ---- Platform chips ----
    // One row per card the filter acts on (Paid summary, Campaign explorer),
    // both driving the same platformFilter Set — so a click in either has to
    // repaint the other's active states as well as the two views.
    if (HAS_PAID_ADS) {
      const platformRows = [...document.querySelectorAll('.platform-chips')];
      const syncPlatformRows = () => platformRows.forEach(el =>
        el.querySelectorAll('.chip').forEach(b => b.classList.toggle(
          'active', b.dataset.key==='All' ? platformFilter.size===0 : platformFilter.has(b.dataset.key))));
      platformRows.forEach(el => buildChips(el,['Google','LinkedIn','Meta','Microsoft'],platformFilter,()=>{
        syncPlatformRows(); renderSummary(); renderExplorer();
      }));
    }

    // ---- Explorer chips ----
    buildExplorerFilters();

    document.getElementById('explorerTable').addEventListener('click',ev=>{
      const shuf=ev.target.closest('.gads-shuffle');
      if (shuf) {
        ev.stopPropagation();
        const cell=shuf.closest('.ad-cell');
        if (cell) gadsUpdatePreview(cell, true);
        return;
      }
      const thumb=ev.target.closest('.ad-thumb');
      if (thumb) {
        openCreativePreview(thumb.dataset.previewImage||'', thumb.dataset.previewVideo||'');
        return;
      }
      const moreBtn=ev.target.closest('.ad-copy-more');
      if (moreBtn) {
        const extra=moreBtn.nextElementSibling;
        extra.hidden=!extra.hidden;
        moreBtn.textContent = extra.hidden ? moreBtn.dataset.moreLabel : 'Show less';
        return;
      }
      if (ev.target.closest('.ke-select-wrap')) return;  // filter selects handle their own change
      const sortTh=ev.target.closest('th.expl-sort');
      if (sortTh) {
        const key=sortTh.dataset.key;
        if (explorerSort.key===key) explorerSort.dir = explorerSort.dir==='asc'?'desc':'asc';
        else explorerSort = { key, dir: key==='name'?'asc':'desc' };
        renderExplorer();
        return;
      }
      const row=ev.target.closest('tr[data-expandable]');
      if (row) toggleExplorerRow(row);
    });
    document.getElementById('explorerTable').addEventListener('change',ev=>{
      const conv=ev.target.closest('.cv-select');
      if (conv) {
        selectedConvAction = conv.value || '__all__';
        try { localStorage.setItem(CONV_STORAGE_KEY, selectedConvAction); } catch(e) {}
        applyConvSelection(explorerRows);
        renderExplorer();
        return;
      }
      const sel=ev.target.closest('.ke-select');
      if (!sel) return;
      selectedKeyEvent = sel.value || '__all__';
      try { localStorage.setItem(KE_STORAGE_KEY, selectedKeyEvent); } catch(e) {}
      applyVerifiedSelection();
      renderExplorer();
    });
    // ---- LinkedIn audience: dimension tabs ----
    // Delegated: the tab strip is rebuilt from whichever dimensions came back
    // with rows, so there are no buttons to bind at startup. Null-guarded — an
    // admin can hide this panel from the layout editor, and a hidden panel is
    // not emitted at all for clients.
    const lidemoTabHost=document.getElementById('lidemoTabs');
    if (lidemoTabHost) lidemoTabHost.addEventListener('click',ev=>{
      const btn=ev.target.closest('.pnl-tab[data-lidim]');
      if (!btn) return;
      lidemoDim=btn.dataset.lidim;
      // A new breakdown is a new set of rows: collapse back to the top ten.
      tableExpanded['lidemoTable']=false;
      renderLidemoTabs();
      renderLidemo();
    });

    // ---- Google Ads demographics: dimension tabs ----
    // Delegated and null-guarded for the same reasons as the LinkedIn strip.
    const gdemoTabHost=document.getElementById('gdemoTabs');
    if (gdemoTabHost) gdemoTabHost.addEventListener('click',ev=>{
      const btn=ev.target.closest('.pnl-tab[data-gdim]');
      if (!btn) return;
      gdemoDim=btn.dataset.gdim;
      tableExpanded['gdemoTable']=false;
      renderGdemoTabs();
      renderGdemo();
    });

    // ---- Keyword Performance: search, match filter and column sorting ----
    document.getElementById('keywordSearch').addEventListener('input',ev=>{
      kwSearch=ev.target.value; kwPageNum=1; renderKeywordTable();
    });
    document.getElementById('keywordMatchChips').addEventListener('click',ev=>{
      const btn=ev.target.closest('.chip'); if (!btn) return;
      const m=btn.dataset.match;
      if (m==='All') kwMatchFilter.clear();
      else kwMatchFilter.has(m) ? kwMatchFilter.delete(m) : kwMatchFilter.add(m);
      kwPageNum=1; syncKeywordChips(); renderKeywordTable();
    });
    document.getElementById('keywordTable').addEventListener('click',ev=>{
      const th=ev.target.closest('th.expl-sort'); if (!th) return;
      const key=th.dataset.key;
      if (kwSort.key===key) kwSort.dir = kwSort.dir==='asc'?'desc':'asc';
      else kwSort = { key, dir: (key==='keyword_text'||key==='match_type')?'asc':'desc' };
      kwPageNum=1; renderKeywordTable();
    });
    // ---- Creative preview modal (click a thumbnail to see it full size) ----
    function openCreativePreview(imageUrl, videoUrl) {
      const body=document.getElementById('creativePreviewBody');
      const modal=document.getElementById('creativePreview');
      if (!body || !modal) return;
      if (videoUrl) {
        body.innerHTML = `<video src="${esc(videoUrl)}" controls autoplay playsinline poster="${esc(imageUrl)}"></video>`;
      } else if (imageUrl) {
        body.innerHTML = `<img src="${esc(imageUrl)}" alt="" referrerpolicy="no-referrer">`;
      } else {
        return;
      }
      modal.hidden = false;
    }
    function closeCreativePreview() {
      const modal=document.getElementById('creativePreview');
      const body=document.getElementById('creativePreviewBody');
      if (!modal) return;
      modal.hidden = true;
      if (body) body.innerHTML = ''; // stop any playing video
    }
    document.querySelectorAll('[data-close-preview]').forEach(el=>el.addEventListener('click', closeCreativePreview));
    document.addEventListener('keydown', ev=>{ if (ev.key==='Escape') closeCreativePreview(); });
    applyPreset(DEFAULT_DATE_PRESET || 'last_30');
    // Earliest-synced-date lookup drives the comparison-window notice, which is
    // page-level -- so fetch it even when the landing tab isn't Overview (that
    // loader asks for it too; loadHealth dedupes the concurrent call).
    loadHealth();

    // Deep-link + page-visibility prefs: land on the tab named in ?view= (set
    // by the sidebar links on Settings/Files/Connectors), unless an admin hid
    // that tab for this client (Admin > Advanced) -- then fall back to the first
    // visible page. The hidden set is server-side (window.__sfHiddenTabs, set by
    // the sidebar nav), so it's the same for every user and browser. Runs last,
    // after all loaders are initialized.
    (function(){
      const hidden = new Set(Array.isArray(window.__sfHiddenTabs) ? window.__sfHiddenTabs : []);
      // A tab is reachable only if it's a known tab, not admin-hidden, AND the
      // sidebar actually rendered a button for it. That last check covers the
      // connector-gated tabs (Search Console, Site Performance): their panes are
      // always emitted, so without it a stale ?view=gsc link would strand the
      // user on an empty pane with no nav item to click back from.
      function reachable(t) {
        return !!t && TABS.includes(t) && !hidden.has(t)
          && !!document.querySelector('.dash-view-btn[data-tab="' + t + '"]');
      }
      const v = new URLSearchParams(location.search).get('view');
      let target = reachable(v) ? v : 'overview';
      if (!reachable(target)) target = TABS.find(reachable) || 'overview';
      if (target !== currentTab) switchTab(target);
    })();