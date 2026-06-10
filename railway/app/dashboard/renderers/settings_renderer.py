"""Insights editor and card HTML (settings + overview)."""

from __future__ import annotations

import re
from typing import Any

from dashboard.utils.formatting import esc as _esc
from dashboard.utils.urls import insights_action_url as _insights_action_url

def insights_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    raw = (snapshot or {}).get("insights")
    if isinstance(raw, str):
        return {"body": raw.strip(), "updated_at": None, "updated_by": None}
    if isinstance(raw, dict):
        return {
            "body": str(raw.get("body") or "").strip(),
            "updated_at": raw.get("updated_at"),
            "updated_by": raw.get("updated_by"),
        }
    return {"body": "", "updated_at": None, "updated_by": None}


def format_insights_body_html(body: str) -> str:
    """Turn pasted GPT bullets into compact HTML."""
    text = str(body or "").strip()
    if not text:
        return ""
    blocks: list[str] = []
    list_items: list[str] = []
    bullet_re = re.compile(r"^[\s]*(?:[-*•]|\d+[.)])\s+")

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            items = "".join(f"<li>{_esc(line)}</li>" for line in list_items)
            blocks.append(f'<ul class="insights-list">{items}</ul>')
            list_items = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue
        if bullet_re.match(stripped):
            list_items.append(bullet_re.sub("", stripped, count=1).strip())
        else:
            flush_list()
            blocks.append(f'<p class="insights-para">{_esc(stripped)}</p>')
    flush_list()
    return "\n".join(blocks)


def insights_editor_html(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
    snapshot: dict[str, Any] | None,
) -> str:
    action = _insights_action_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    if not action:
        return ""
    insights = insights_from_snapshot(snapshot)
    body = insights.get("body") or ""
    updated = insights.get("updated_at")
    meta = ""
    if updated:
        meta = f'<p class="insights-editor-meta muted">Last saved {_esc(str(updated)[:19])} UTC</p>'
    return f"""
    <section class="insights-editor">
      <h3 class="insights-editor-title">Insights</h3>
      <p class="muted insights-editor-hint">Paste short, actionable notes from your Custom GPT (bullets work well).</p>
      {meta}
      <form method="post" action="{action}" class="insights-editor-form">
        <textarea name="body" class="insights-textarea" rows="7" maxlength="8000"
          placeholder="• Shift budget to …&#10;• Pause ad set …&#10;• Test landing page …">{_esc(body)}</textarea>
        <button type="submit" class="refresh-btn insights-save-btn">Save insights</button>
      </form>
    </section>"""


def insights_card_html(snapshot: dict[str, Any] | None) -> str:
    insights = insights_from_snapshot(snapshot)
    body = insights.get("body") or ""
    if body:
        content = format_insights_body_html(body)
        updated = insights.get("updated_at")
        foot = ""
        if updated:
            foot = (
                f'<p class="insights-foot muted">Updated {_esc(str(updated)[:10])}</p>'
            )
        inner = f'<div class="insights-body">{content}</div>{foot}'
    else:
        inner = (
            '<p class="insights-empty muted">Add insights in Settings — paste weekly notes '
            "from your Custom GPT.</p>"
        )
    return f"""
    <section class="panel insights-panel" aria-label="Insights">
      <div class="insights-head">
        <h2 class="insights-title">Insights</h2>
        <button type="button" class="info-tip info-tip--light"
          data-tip="Short, actionable takeaways. AI-generated summaries coming later; edit in Settings for now."
          aria-label="About insights">i</button>
      </div>
      {inner}
    </section>"""


