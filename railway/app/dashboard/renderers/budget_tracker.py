"""Shared "Budget tracking" module — cumulative paid spend vs monthly goal.

Rendered on both the Campaign Explorer (bigquery_dashboard_renderer) and the
BigQuery settings page (bigquery_settings_renderer) so the two never drift.

Three parts the host page embeds:
  - ``css()``        → style block contents (self-contained; structural helpers
                       are scoped under ``#sec-budget`` so they don't clobber the
                       host's own ``.chip`` / ``.sec-head`` rules).
  - ``section_html`` → the ``<section id="sec-budget">`` markup.
  - ``scripts()``    → a self-contained IIFE that defines its own helpers and
                       uses ``window.Chart`` (both pages load
                       ``/static/vendor/chart.umd.min.js``). It lazy-loads via an
                       IntersectionObserver, so it works whether visible on load
                       (settings) or revealed later (the hidden Explorer tab).
"""

from __future__ import annotations

import json

from dashboard.renderers.bigquery_dashboard_renderer import _api_url


def css() -> str:
    """Style block contents for the budget module (no surrounding <style>)."""
    return """
    /* ---- Budget tracking module (shared: Explorer + Settings) ---- */
    #sec-budget .sec-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:16px; }
    #sec-budget .sec-head h2 { margin:0; display:inline-flex; align-items:center; gap:6px; }
    /* Info icon on the heading (self-contained so the settings host, which has
       no global .info-tip, styles it the same as the Explorer host). */
    #sec-budget .info-tip { display:inline-flex; align-items:center; cursor:help; color:#9aa7bd; line-height:0; }
    #sec-budget .info-tip svg { display:block; }
    #sec-budget .info-tip:hover, #sec-budget .info-tip:focus { color:var(--navy); outline:none; }
    #sec-budget .ov-actions { display:flex; align-items:center; gap:10px; flex-shrink:0; }
    #sec-budget .status { color:var(--muted); font-size:.82rem; margin:0; }
    #sec-budget .budget-retry { font:inherit; font-size:.82rem; color:var(--navy); background:none; border:0; padding:0; cursor:pointer; text-decoration:underline; }
    #sec-budget .chips { display:flex; flex-wrap:wrap; gap:5px; }
    #sec-budget .chip { border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:4px 12px; font:inherit; font-size:.8rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s, color .12s; }
    #sec-budget .chip.active { background:var(--navy); color:#fff; border-color:var(--navy); }
    #sec-budget .chip.active:hover { background:#0d2c4d; }
    #sec-budget .chart-note { font-size:.74rem; color:var(--muted); margin-top:8px; }
    #sec-budget .chart-wrap { position:relative; border:1px solid var(--line-soft); border-radius:10px; padding:10px 12px; background:#fafcff; }
    #sec-budget .chart-canvas-host { position:relative; width:100%; }
    #sec-budget .chart-canvas-host canvas { display:block; }
    .budget-stats { display:flex; flex-wrap:wrap; gap:10px; margin:2px 0 12px; }
    .budget-stat { flex:1 1 120px; min-width:120px; border:1px solid var(--line-soft); border-radius:10px; padding:9px 12px; background:var(--card); }
    .budget-stat-label { font-size:.68rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:700; }
    .budget-stat-value { font-size:1.12rem; font-weight:750; color:var(--navy); margin-top:2px; }
    .budget-stat-sub { font-size:.72rem; color:var(--muted); margin-top:1px; }
    .budget-stat.is-over .budget-stat-value { color:#c02626; }
    .budget-stat.is-under .budget-stat-value { color:#0a7f3f; }
    .budget-goal-editor { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:0 0 10px; }
    .budget-goal-editor label { font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); display:inline; grid-template-columns:none; }
    .budget-goal-input { position:relative; display:inline-flex; align-items:center; }
    .budget-goal-prefix { position:absolute; left:9px; color:var(--muted); font-size:.85rem; pointer-events:none; }
    .budget-goal-input input { width:130px; padding:6px 10px 6px 18px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--navy); font:inherit; font-size:.86rem; text-transform:none; letter-spacing:0; }
    .budget-goal-save { cursor:pointer; }
    /* Loading skeletons (self-contained: the settings host has no global .skel) */
    @keyframes budgetShimmer { 0%{background-position:-200% 0} 100%{background-position:200% 0} }
    .budget-skel { display:block; height:9px; border-radius:5px;
      background:linear-gradient(90deg,#eef2f7 25%,#e4eaf2 50%,#eef2f7 75%); background-size:200% 100%;
      animation:budgetShimmer 1.4s ease-in-out infinite; }
    #sec-budget .chart-canvas-host.is-loading { border-radius:8px;
      background:linear-gradient(90deg,#eef2f7 25%,#e4eaf2 50%,#eef2f7 75%); background-size:200% 100%;
      animation:budgetShimmer 1.4s ease-in-out infinite; }
    """


def section_html(*, can_edit: bool) -> str:
    """The budget module <section>. ``can_edit`` shows the inline goal editor."""
    goal_editor = "" if not can_edit else """
        <form class="budget-goal-editor" id="budgetGoalForm" autocomplete="off">
          <label for="budgetGoalInput">Monthly goal (USD)</label>
          <div class="budget-goal-input">
            <span class="budget-goal-prefix">$</span>
            <input type="number" id="budgetGoalInput" name="monthly_budget_usd" min="0" step="100" placeholder="e.g. 25000">
          </div>
          <button type="submit" class="chip budget-goal-save">Save goal</button>
          <span class="status" id="budgetGoalStatus"></span>
        </form>"""
    _help = (
        "Cumulative paid spend by platform against the monthly budget. Spend "
        "resets at the start of each calendar month; the projection extends the "
        "current month at its run-rate to month end."
    )
    _ico = (
        '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" '
        'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" '
        'aria-hidden="true"><circle cx="8" cy="8" r="6.5"/>'
        '<path d="M8 7.3v3.4"/>'
        '<circle cx="8" cy="4.9" r=".85" fill="currentColor" stroke="none"/></svg>'
    )
    return f"""
      <section id="sec-budget">
        <div class="sec-head">
          <h2>Budget tracking<span class="info-tip" tabindex="0" role="img" aria-label="How this chart works. {_help}" title="{_help}">{_ico}</span></h2>
          <div class="ov-actions">
            <span class="status" id="budgetStatus"></span>
            <div class="chips" id="budgetRangeChips" role="tablist" aria-label="Budget range">
              <button type="button" class="chip active" data-range="month">This month</button>
              <button type="button" class="chip" data-range="last3m">Last 3 months</button>
            </div>
          </div>
        </div>
        {goal_editor}
        <div class="budget-stats" id="budgetStats"></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:260px"><canvas id="budgetChart"></canvas></div></div>
      </section>"""


# Static JS body (plain string — single braces). Reads config from BT_CFG, which
# the caller injects. Self-contained IIFE: its own helpers + window.Chart, so it
# never collides with the host page's globals.
_JS_BODY = r"""
(function(){
  const section = document.getElementById('sec-budget');
  if (!section) return;

  const money = v => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(v||0));
  const num = v => Number(v||0);
  const fmtDate = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const setStatus = (id,txt) => { const el=document.getElementById(id); if (el) el.textContent = txt||''; };
  const sleep = ms => new Promise(r=>setTimeout(r,ms));
  async function getJson(url){ const r = await fetch(url,{credentials:'same-origin'}); if (!r.ok) throw new Error('HTTP '+r.status); return r.json(); }
  function withRange(base,s,e){ const sep=base.includes('?')?'&':'?'; return base+sep+'start_date='+s+'&end_date='+e; }
  const _charts = {};
  function chart(id,cfg){ const el=document.getElementById(id); if (!el||!window.Chart) return null; if (_charts[id]) _charts[id].destroy(); _charts[id]=new Chart(el.getContext('2d'),cfg); return _charts[id]; }
  function destroyChart(id){ if (_charts[id]){ _charts[id].destroy(); delete _charts[id]; } }

  let monthlyBudget = Number(BT_CFG.monthlyBudget)||0;
  let budgetRange = 'month';
  const PLATFORMS = [
    { key:'paid_google',   label:'Google Ads', color:'#1d6fd0' },
    { key:'paid_linkedin', label:'LinkedIn',   color:'#0a7f3f' },
    { key:'paid_meta',     label:'Meta',       color:'#7c3aed' },
  ];
  const monthKey = ds => String(ds).slice(0,7);
  function dayRange(from,to){ const out=[]; const d=new Date(from); while (d<=to){ out.push(fmtDate(d)); d.setDate(d.getDate()+1); } return out; }
  function budgetWindow(){
    const today=new Date();
    const yesterday=new Date(today); yesterday.setDate(today.getDate()-1);
    const monthEnd=new Date(today.getFullYear(),today.getMonth()+1,0);
    let start;
    if (budgetRange==='last3m') start=new Date(today.getFullYear(),today.getMonth()-2,1);
    else start=new Date(today.getFullYear(),today.getMonth(),1);
    const lastData = yesterday>=start ? yesterday : start;
    return { start, lastData, axisEnd: monthEnd };
  }
  function syncGoalInput(){
    const inp=document.getElementById('budgetGoalInput');
    if (inp && !inp.matches(':focus')) inp.value = monthlyBudget>0 ? monthlyBudget : '';
  }
  function showBudgetSkeleton(){
    const stats=document.getElementById('budgetStats');
    if (stats) stats.innerHTML = Array.from({length:3},()=>
      '<div class="budget-stat"><span class="budget-skel" style="width:58%"></span>'
      + '<span class="budget-skel" style="height:18px;width:74%;margin-top:7px"></span></div>').join('');
    const host=document.getElementById('budgetChart');
    if (host && host.parentElement) host.parentElement.classList.add('is-loading');
  }
  async function loadBudget(){
    syncGoalInput();
    setStatus('budgetStatus','Loading…');
    showBudgetSkeleton();
    const win=budgetWindow();
    const startStr=fmtDate(win.start), lastStr=fmtDate(win.lastData);
    const url=withRange(BT_CFG.summaryApi,startStr,lastStr);
    // The first paid-summary read on a cold Explorer open can fail/time out — the
    // tab fires several BQ-backed queries at once and this one loses the race,
    // then succeeds once warm (which is why a manual range toggle "fixed" it).
    // Swallowing the error used to render as "No paid spend in this range", so
    // the chart looked empty. Retry with backoff, and surface a real error state
    // instead of masquerading a fetch failure as zero spend.
    let res=null, lastErr=null;
    for (let attempt=0; attempt<4; attempt++){
      try { res=await getJson(url); lastErr=null; break; }
      catch(e){ lastErr=e; if (attempt<3) await sleep(500*(attempt+1)); }
    }
    if (lastErr){ renderBudgetError(); return; }
    renderBudget((res&&res.daily)||[],win);
  }
  function renderBudgetError(){
    const _ch=document.getElementById('budgetChart');
    if (_ch && _ch.parentElement) _ch.parentElement.classList.remove('is-loading');
    const stats=document.getElementById('budgetStats'); if (stats) stats.innerHTML='';
    destroyChart('budgetChart');
    const st=document.getElementById('budgetStatus');
    if (st){
      st.innerHTML='Couldn’t load budget data. <button type="button" class="budget-retry">Retry</button>';
      const b=st.querySelector('.budget-retry'); if (b) b.addEventListener('click',loadBudget);
    }
  }
  function renderBudget(daily,win){
    const _ch=document.getElementById('budgetChart');
    if (_ch && _ch.parentElement) _ch.parentElement.classList.remove('is-loading');
    const axis=dayRange(win.start,win.axisEnd);
    const lastStr=fmtDate(win.lastData);
    const spend={}; PLATFORMS.forEach(p=>spend[p.key]={});
    let anySpend=false;
    for (const r of (daily||[])){
      const k=String(r.source||'').toLowerCase(); if (!spend[k]) continue;
      const d=String(r.date); spend[k][d]=(spend[k][d]||0)+num(r.spend); if (num(r.spend)) anySpend=true;
    }
    const perPlatformCum={};
    PLATFORMS.forEach(p=>{
      let run=0, curMonth=null;
      perPlatformCum[p.key]=axis.map(d=>{
        const mk=monthKey(d);
        if (mk!==curMonth){ curMonth=mk; run=0; }
        if (d>lastStr) return undefined;
        run+=(spend[p.key][d]||0);
        return run;
      });
    });
    const stackAcc=axis.map(()=>0);
    const platformSets=PLATFORMS.map((p,i)=>{
      const raw=perPlatformCum[p.key];
      const data=raw.map((v,idx)=>(v===undefined?undefined:(stackAcc[idx]+=v)));
      return { label:p.label, data, _raw:raw, _fmt:money, borderColor:p.color, backgroundColor:p.color+'4d',
               fill:i===0?'origin':'-1', borderWidth:2, tension:0.25, pointRadius:0, pointHoverRadius:4, spanGaps:false };
    });
    const totalCum=axis.map((d,idx)=>(d>lastStr?undefined:stackAcc[idx]));
    const lastIdx=axis.indexOf(lastStr);
    const spentToDate=lastIdx>=0?(totalCum[lastIdx]||0):0;
    const datasets=[...platformSets];
    const hasGoal=monthlyBudget>0;
    if (hasGoal){
      datasets.push({ label:'Goal', data:axis.map(()=>monthlyBudget), _raw:axis.map(()=>monthlyBudget), _fmt:money,
        borderColor:'#64748b', backgroundColor:'transparent', fill:false, borderWidth:1.6, borderDash:[6,5], tension:0, pointRadius:0, pointHoverRadius:0 });
    }
    const curMonthKey=monthKey(fmtDate(new Date()));
    const curDays=axis.filter(d=>monthKey(d)===curMonthKey);
    let projectedTotal=null;
    if (curDays.length){
      const elapsed=curDays.filter(d=>d<=lastStr).length;
      const dim=curDays.length;
      const monthStartIdx=axis.indexOf(curDays[0]);
      const monthSpend=lastIdx>=monthStartIdx?(totalCum[lastIdx]||0):0;
      if (elapsed>0){
        projectedTotal=monthSpend/elapsed*dim;
        const projData=axis.map((d,idx)=>{
          if (monthKey(d)!==curMonthKey) return undefined;
          const dayNo=idx-monthStartIdx+1;
          return monthSpend/elapsed*dayNo;
        });
        datasets.push({ label:'Projected', data:projData, _raw:projData, _fmt:money,
          borderColor:'#e08a1e', backgroundColor:'transparent', fill:false, borderWidth:1.8, borderDash:[3,3], tension:0, pointRadius:0, pointHoverRadius:0 });
      }
    }
    renderStats(spentToDate, hasGoal?monthlyBudget:0, projectedTotal);
    if (!anySpend){ destroyChart('budgetChart'); setStatus('budgetStatus','No paid spend in this range.'); return; }
    chart('budgetChart',{
      type:'line',
      data:{ labels:axis.map(d=>d.slice(5)), datasets },
      options:{
        interaction:{ mode:'index', intersect:false },
        maintainAspectRatio:false,
        scales:{
          x:{ grid:{ display:false }, border:{ display:false }, ticks:{ maxRotation:0, autoSkip:true, maxTicksLimit:8 } },
          y:{ beginAtZero:true, grid:{ color:'#f1f4f9' }, border:{ display:false },
              ticks:{ maxTicksLimit:5, callback:v=>'$'+Math.round(num(v)).toLocaleString() } },
        },
        plugins:{
          legend:{ display:true, position:'bottom', labels:{ usePointStyle:true, boxWidth:8, padding:12 } },
          tooltip:{
            filter:item=>{ const raw=item.dataset._raw?item.dataset._raw[item.dataIndex]:item.parsed.y; return raw!==undefined&&raw!==null; },
            callbacks:{ label:c=>{ const raw=c.dataset._raw?c.dataset._raw[c.dataIndex]:c.parsed.y; return `${c.dataset.label}: ${(c.dataset._fmt||money)(raw)}`; } },
          },
        },
      },
    });
    setStatus('budgetStatus','');
  }
  function renderStats(spent,goal,projected){
    const el=document.getElementById('budgetStats'); if (!el) return;
    const pills=[];
    pills.push(`<div class="budget-stat"><div class="budget-stat-label">Spent to date</div><div class="budget-stat-value">${money(spent)}</div></div>`);
    if (goal>0){
      const usePct=spent/goal*100;
      pills.push(`<div class="budget-stat"><div class="budget-stat-label">Monthly goal</div><div class="budget-stat-value">${money(goal)}</div><div class="budget-stat-sub">${usePct.toFixed(0)}% used</div></div>`);
      if (projected!==null&&projected!==undefined){
        const over=projected>goal, diff=Math.abs(projected-goal);
        pills.push(`<div class="budget-stat ${over?'is-over':'is-under'}"><div class="budget-stat-label">Projected month end</div><div class="budget-stat-value">${money(projected)}</div><div class="budget-stat-sub">${over?money(diff)+' over':money(diff)+' under'} goal</div></div>`);
      }
    } else {
      const hint=BT_CFG.canEdit?'Set a goal above':'Set one in Settings';
      pills.push(`<div class="budget-stat"><div class="budget-stat-label">Monthly goal</div><div class="budget-stat-value">—</div><div class="budget-stat-sub">${hint}</div></div>`);
    }
    el.innerHTML=pills.join('');
  }

  const chips=document.getElementById('budgetRangeChips');
  if (chips) chips.addEventListener('click', e=>{
    const btn=e.target.closest('.chip[data-range]');
    if (!btn||btn.classList.contains('active')) return;
    chips.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active', c===btn));
    budgetRange=btn.dataset.range;
    loadBudget();
  });
  const form=document.getElementById('budgetGoalForm');
  if (form) form.addEventListener('submit', async e=>{
    e.preventDefault();
    if (!BT_CFG.canEdit) return;
    const inp=document.getElementById('budgetGoalInput');
    const raw=(inp&&inp.value||'').trim();
    setStatus('budgetGoalStatus','Saving…');
    try {
      const res=await fetch(BT_CFG.saveUrl,{ method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:new URLSearchParams({ monthly_budget_usd: raw }) });
      const data=await res.json().catch(()=>({}));
      if (!res.ok||!data.ok) throw new Error(data.error||('HTTP '+res.status));
      monthlyBudget=Number(data.monthly_budget_usd)||0;
      setStatus('budgetGoalStatus','Saved ✓');
      setTimeout(()=>setStatus('budgetGoalStatus',''),2000);
      syncGoalInput();
      loadBudget();
    } catch(err){ setStatus('budgetGoalStatus','Save failed: '+(err.message||'error')); }
  });

  // Lazy-load: fire once when the section is (or becomes) visible. Covers the
  // always-visible settings page and the initially-hidden Explorer tab alike.
  let started=false;
  function ensureLoad(){ if (started) return; started=true; loadBudget(); }
  // Expose a manual trigger so a host page that reveals this section by un-hiding
  // an ancestor (e.g. the Campaign Explorer tab) can fire the load directly,
  // rather than depending on the IntersectionObserver noticing the change — which
  // only fires on scroll and left the chart blank until a refresh/toggle.
  window.ensureBudgetLoaded = ensureLoad;
  if (section.offsetParent !== null) ensureLoad();
  else {
    const io=new IntersectionObserver(ents=>{
      // Guard on real visibility (offsetParent). With a rootMargin a
      // display:none target still reports isIntersecting — its 0x0 box sits at
      // the origin, inside the expanded root — so without this guard the load
      // latches while the pane is hidden and renders into a 0-size canvas,
      // leaving the chart blank until a manual toggle. Only fire once laid out.
      if (section.offsetParent !== null && ents.some(en=>en.isIntersecting)){ io.disconnect(); ensureLoad(); }
    }, { rootMargin:'300px' });
    io.observe(section);
  }
})();
"""


def scripts(
    *,
    api_client_key: str,
    access_key: str | None,
    monthly_budget: float | None,
    can_edit: bool,
    client_slug: str,
) -> str:
    """Return the module's <script> block (config header + shared IIFE body)."""
    cfg = {
        "summaryApi": _api_url(
            f"/api/clients/{api_client_key}/summary", access_key=access_key
        ),
        "monthlyBudget": float(monthly_budget) if monthly_budget else 0,
        "canEdit": bool(can_edit),
        "saveUrl": _api_url(f"/dashboard/{client_slug}/budget", access_key=access_key),
    }
    header = f"const BT_CFG = {json.dumps(cfg)};\n"
    return f"<script>\n{header}{_JS_BODY}\n</script>"
