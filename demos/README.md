# Integration demos

Small, self-contained demos to *see* two candidate open-source integrations
before committing to them. Neither is wired into the app — they're for
understanding the pattern and output.

## 1. DuckDB — agency-wide analytics in one query

```bash
pip install duckdb
python demos/duckdb_agency_analytics.py
```

Uses synthetic data shaped exactly like `railway/app` `metrics_daily`, joined to
an account→client map, and computes **week-over-week spend per client (biggest
drops first)** and **agency-wide channel mix** — each in a single query. This is
the class of cross-client question the HQ page can't answer cheaply today
without N more per-client BigQuery reads. In production DuckDB attaches Postgres
directly (`ATTACH ... TYPE postgres`) and queries `metrics_daily` with no copy.

## 2. htmx — the same "Refresh" button, two ways

```bash
pip install fastapi "uvicorn[standard]"
cd demos/htmx_demo && uvicorn app:app --port 8010
# open http://127.0.0.1:8010/  (needs internet for the htmx CDN tag,
# or vendor htmx.min.js locally)
```

One page, two buttons hitting the same data:

- **AFTER (htmx):** the button carries `hx-post="/htmx/refresh"` +
  `hx-target="#cards"`. The endpoint returns an **HTML fragment**; htmx swaps it
  in. You write **no** JavaScript.
- **BEFORE (today's pattern):** the endpoint returns **JSON**, and you
  hand-write the `fetch()` + parse + rebuild-the-DOM JavaScript — the same shape
  that's inlined across ~10 renderers in the app.

Headless comparison (no browser needed):

```bash
curl -s -X POST http://127.0.0.1:8010/htmx/refresh   # -> <div id="cards">...</div>  (HTML)
curl -s -X POST http://127.0.0.1:8010/json/refresh   # -> {"penn":..., "acme":...}   (JSON)
```
