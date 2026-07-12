"""HTML renderer for the Lead Tracking page (HubSpot reports)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc
from hubspot_reports_service import LeadTrackingReport

_EXTRA_CSS = """
.lt-wrap { max-width: 1080px; }
.lt-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin:20px 0 28px; }
.lt-tile { background:#fff; border:1px solid #e6e8ee; border-radius:12px; padding:18px 20px; }
.lt-tile-label { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:#6b7280; margin-bottom:6px; }
.lt-tile-value { font-size:28px; font-weight:700; color:#111827; line-height:1.1; }
.lt-tile-sub { font-size:12px; color:#9ca3af; margin-top:4px; }
.lt-card { background:#fff; border:1px solid #e6e8ee; border-radius:12px; padding:20px 22px; margin-bottom:22px; }
.lt-card h2 { font-size:15px; font-weight:600; color:#111827; margin:0 0 16px; }
.lt-bars { display:flex; flex-direction:column; gap:10px; }
.lt-bar-row { display:grid; grid-template-columns:150px 1fr 64px; align-items:center; gap:12px; font-size:13px; }
.lt-bar-label { color:#374151; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.lt-bar-track { background:#f1f3f8; border-radius:6px; height:20px; overflow:hidden; }
.lt-bar-fill { background:#ff7a59; height:100%; border-radius:6px; min-width:2px; }
.lt-bar-val { text-align:right; color:#111827; font-variant-numeric:tabular-nums; font-weight:600; }
.lt-table-wrap { overflow-x:auto; }
.lt-table { width:100%; border-collapse:collapse; font-size:13px; }
.lt-table th { text-align:left; color:#6b7280; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; padding:8px 10px; border-bottom:2px solid #eef0f4; }
.lt-table td { padding:9px 10px; border-bottom:1px solid #f2f4f8; color:#374151; }
.lt-empty { color:#9ca3af; font-size:13px; padding:8px 0; }
.lt-note { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:10px; padding:12px 16px; font-size:13px; margin-bottom:20px; }
.lt-cols { display:grid; grid-template-columns:1fr 1fr; gap:22px; }
@media (max-width:720px){ .lt-cols{ grid-template-columns:1fr; } .lt-bar-row{ grid-template-columns:110px 1fr 56px; } }
"""


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_money(n: Any) -> str:
    try:
        return f"${float(n):,.0f}"
    except (TypeError, ValueError):
        return "$0"


def _fmt_dt(v: Any) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%b %d, %Y")
    return _esc(str(v)) if v else "—"


def _bars(rows: list[dict[str, Any]], *, label_key: str, value_key: str, money: bool = False) -> str:
    if not rows:
        return '<div class="lt-empty">No data yet.</div>'
    top = rows[:10]
    peak = max((float(r.get(value_key) or 0) for r in top), default=0) or 1
    out = ['<div class="lt-bars">']
    for r in top:
        val = float(r.get(value_key) or 0)
        pct = max(2, round(val / peak * 100))
        shown = _fmt_money(val) if money else _fmt_int(val)
        out.append(
            f'<div class="lt-bar-row">'
            f'<span class="lt-bar-label">{_esc(str(r.get(label_key) or "Unknown"))}</span>'
            f'<span class="lt-bar-track"><span class="lt-bar-fill" style="width:{pct}%"></span></span>'
            f'<span class="lt-bar-val">{shown}</span>'
            f'</div>'
        )
    out.append("</div>")
    return "".join(out)


def _source_label(src: str) -> str:
    return (src or "Unknown").replace("_", " ").title()


def render_lead_tracking(
    *,
    client_slug: str,
    label: str,
    report: LeadTrackingReport,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    if not report.configured:
        content = (
            '<div class="lt-wrap">'
            '<h1 class="connectors-page-title">Lead Tracking</h1>'
            f'<div class="lt-note">{_esc(report.error or "HubSpot is not configured for this client.")} '
            'Connect HubSpot from the Connectors page to enable these reports.</div>'
            '</div>'
        )
        return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)

    note = ""
    if not report.contacts_available and not report.deals_available:
        note = ('<div class="lt-note">No HubSpot data has synced yet. Run a sync from the '
                'HubSpot connector, then refresh this page.</div>')

    tiles = f"""
      <div class="lt-tiles">
        <div class="lt-tile"><div class="lt-tile-label">MQLs</div><div class="lt-tile-value">{_fmt_int(report.mql_count)}</div><div class="lt-tile-sub">is or has been MQL</div></div>
        <div class="lt-tile"><div class="lt-tile-label">Contacts tracked</div><div class="lt-tile-value">{_fmt_int(report.contact_count)}</div></div>
        <div class="lt-tile"><div class="lt-tile-label">Deals</div><div class="lt-tile-value">{_fmt_int(report.deal_count)}</div></div>
        <div class="lt-tile"><div class="lt-tile-label">Pipeline</div><div class="lt-tile-value">{_fmt_money(report.pipeline_amount)}</div></div>
        <div class="lt-tile"><div class="lt-tile-label">Won revenue</div><div class="lt-tile-value">{_fmt_money(report.won_amount)}</div><div class="lt-tile-sub">closed-won</div></div>
      </div>
    """

    # MQLs by month (bars)
    month_rows = [{"m": r.get("month"), "c": r.get("contacts")} for r in report.mqls_by_month]
    mqls_month_html = _bars(month_rows, label_key="m", value_key="c")

    # Leads by source
    lead_src_rows = [{"s": _source_label(r.get("source")), "c": r.get("contacts")} for r in report.leads_by_source]
    leads_src_html = _bars(lead_src_rows, label_key="s", value_key="c")

    # Deals by source table
    if report.deals_by_source:
        deal_rows = "".join(
            f'<tr><td>{_esc(_source_label(r.get("source")))}</td>'
            f'<td>{_fmt_int(r.get("deals"))}</td>'
            f'<td>{_fmt_money(r.get("amount"))}</td>'
            f'<td>{_fmt_money(r.get("won_amount"))}</td></tr>'
            for r in report.deals_by_source[:12]
        )
        deals_src_html = (
            '<div class="lt-table-wrap"><table class="lt-table"><thead><tr><th>Source</th><th>Deals</th>'
            '<th>Pipeline</th><th>Won</th></tr></thead><tbody>' + deal_rows + '</tbody></table></div>'
        )
    else:
        deals_src_html = '<div class="lt-empty">No deals synced yet.</div>'

    # Recent MQLs table
    if report.recent_mqls:
        recent_rows = "".join(
            f'<tr><td>{_esc(r.get("email") or "—")}</td>'
            f'<td>{_esc(r.get("company") or "—")}</td>'
            f'<td>{_esc(_source_label(r.get("source")))}</td>'
            f'<td>{_fmt_dt(r.get("became_stage_date"))}</td></tr>'
            for r in report.recent_mqls
        )
        recent_html = (
            '<div class="lt-table-wrap"><table class="lt-table"><thead><tr><th>Email</th><th>Company</th>'
            '<th>Original source</th><th>Became MQL</th></tr></thead><tbody>' + recent_rows + '</tbody></table></div>'
        )
    else:
        recent_html = '<div class="lt-empty">No MQLs synced yet.</div>'

    content = f"""
      <div class="lt-wrap">
        <h1 class="connectors-page-title">Lead Tracking</h1>
        <p class="connectors-page-sub">HubSpot lead &amp; pipeline reporting for {_esc(label)}.</p>
        {note}
        {tiles}
        <div class="lt-cols">
          <div class="lt-card"><h2>MQLs by month</h2>{mqls_month_html}</div>
          <div class="lt-card"><h2>Leads by original source</h2>{leads_src_html}</div>
        </div>
        <div class="lt-card"><h2>Pipeline &amp; revenue by source</h2>{deals_src_html}</div>
        <div class="lt-card"><h2>Recent MQLs</h2>{recent_html}</div>
      </div>
    """
    return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)


def _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin) -> str:
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="lead-tracking",
        page_title="Lead Tracking",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
    )
