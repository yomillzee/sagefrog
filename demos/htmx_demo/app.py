"""htmx demo — the same "Refresh" button, done two ways.

Run:  cd demos/htmx_demo && uvicorn app:app --port 8010
Open: http://127.0.0.1:8010/

Sagefrog is already a server-rendered, HTML-first app, so htmx fits the grain:
you delete hand-written fetch()+JSON+DOM code and replace it with attributes on
the HTML. The server just returns the HTML fragment your renderer already knows
how to produce.

Below, GET / serves ONE page with TWO buttons hitting the SAME data, so you can
compare the mechanics side by side.
"""

from __future__ import annotations

import random

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()


def _spend_numbers() -> dict[str, int]:
    """Pretend this is a fresh warehouse read."""
    return {"penn": random.randint(8000, 12000), "acme": random.randint(6000, 9000)}


def render_cards(data: dict[str, int]) -> str:
    """A renderer that returns an HTML *fragment* — exactly like Sagefrog's
    cards_renderer already does. Both approaches below reuse this."""
    return (
        '<div id="cards">'
        f'<div class="card">Penn — ${data["penn"]:,}</div>'
        f'<div class="card">Acme — ${data["acme"]:,}</div>'
        "</div>"
    )


# ---------------------------------------------------------------------------
# AFTER (htmx): the button carries attributes. hx-post says where to POST,
# hx-target says which element to replace with whatever HTML comes back. No
# JavaScript is written by you. The endpoint returns an HTML fragment.
# ---------------------------------------------------------------------------
@app.post("/htmx/refresh", response_class=HTMLResponse)
def htmx_refresh() -> str:
    return render_cards(_spend_numbers())          # returns HTML, htmx swaps it in


# ---------------------------------------------------------------------------
# BEFORE (the pattern you have today): endpoint returns JSON, and YOU write the
# JavaScript to fetch it, parse it, rebuild the HTML, and inject it.
# ---------------------------------------------------------------------------
@app.post("/json/refresh")
def json_refresh() -> JSONResponse:
    return JSONResponse(_spend_numbers())          # returns data; JS must rebuild UI


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    initial = render_cards(_spend_numbers())
    return f"""
    <!doctype html><html><head>
      <script src="https://unpkg.com/htmx.org@1.9.12"></script>
    </head><body style="font-family:system-ui;max-width:640px;margin:2rem auto">
      <h2>AFTER — htmx (no JS written)</h2>
      <!-- click -> POST /htmx/refresh -> swap the response HTML into #cards -->
      <button hx-post="/htmx/refresh" hx-target="#cards" hx-swap="outerHTML">
        Refresh
      </button>
      {initial}

      <h2 style="margin-top:2rem">BEFORE — fetch + JSON + hand-written JS</h2>
      <button onclick="refresh()">Refresh</button>
      <div id="json-cards">{initial.replace('id="cards"', 'id="jc"')}</div>
      <script>
        // You write, own, and debug all of this — per interaction, per page.
        async function refresh() {{
          const r = await fetch('/json/refresh', {{method:'POST'}});
          const d = await r.json();
          document.getElementById('json-cards').innerHTML =
            '<div class="card">Penn — $' + d.penn.toLocaleString() + '</div>' +
            '<div class="card">Acme — $' + d.acme.toLocaleString() + '</div>';
        }}
      </script>
    </body></html>
    """
