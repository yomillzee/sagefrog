"""Search Console keyword watchlist.

The watchlist is the curated benchmark list -- one row per keyword an admin
entered, with the page it was written for -- so the things worth pinning down are
that a row keys on the TERM (not the query), that the "*" marker is the only
thing separating an exact keyword from one that counts its variants, and that a
keyword with no impressions still reaches the table as a row.
"""
from __future__ import annotations

import contextlib
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bq_gsc_service  # noqa: E402
import demo_data  # noqa: E402
from dashboard.assets import dashboard_css  # noqa: E402
from _dashboard_page import render_bigquery_dashboard_page  # noqa: E402
from dashboard.renderers.bigquery_dashboard_renderer import (  # noqa: E402
    parse_gsc_watchlist,
)


@contextlib.contextmanager
def _no_client_context(_slug):
    yield


def _capture(fn, **kwargs):
    """Run a watchlist service call against a stubbed BigQuery, returning the SQL."""
    seen: dict = {}

    def _run(sql, max_rows=500, query_parameters=None):
        seen["sql"] = sql
        seen["params"] = query_parameters or []
        return seen.get("rows", [])

    with patch.object(bq_gsc_service, "_client_context", _no_client_context), \
         patch.object(bq_gsc_service, "_resolved_target",
                      lambda: SimpleNamespace(is_default_fallback=False, credentials_env=None)), \
         patch.object(bq_gsc_service, "_project_id", lambda: "proj"), \
         patch.object(bq_gsc_service, "_reporting_mart_ds", lambda: "marts"), \
         patch.object(bq_gsc_service, "_run", _run):
        result = fn(**kwargs)
    return seen, result


class ParseWatchlistTests(unittest.TestCase):
    def test_keyword_and_page_split_on_pipe(self):
        items = parse_gsc_watchlist("b2b marketing agency|/services/b2b\nhvac software*\n")
        self.assertEqual(
            items,
            [
                {"kw": "b2b marketing agency", "page": "/services/b2b"},
                {"kw": "hvac software*", "page": ""},
            ],
        )

    def test_blank_and_duplicate_lines_dropped(self):
        items = parse_gsc_watchlist("  \nseo audit|/a\nSEO AUDIT|/b\n*\n")
        self.assertEqual(items, [{"kw": "seo audit", "page": "/a"}])


class WatchlistSqlTests(unittest.TestCase):
    def test_rows_group_by_term_and_cover_both_windows(self):
        seen, _ = _capture(
            bq_gsc_service.gsc_watchlist_rows,
            start=date(2026, 7, 1), end=date(2026, 7, 31),
            terms=["b2b marketing agency", "hvac software*"],
            client_slug="acme",
        )
        sql = seen["sql"]
        # The row is the watched term, not the query -- an exact term collapses
        # to its query, a "*" term rolls its variants together.
        self.assertIn("t AS term", sql)
        self.assertIn("GROUP BY term", sql)
        # Prior avg position comes from the same scan, so the row can show Δ Pos.
        self.assertIn("prior_avg_position", sql)
        self.assertIn("2026-05-31", sql)  # preceding same-length window
        # Terms travel as a parameter, never interpolated.
        self.assertEqual([p.name for p in seen["params"]], ["terms"])
        self.assertEqual(
            seen["params"][0].values,
            ["b2b marketing agency", "hvac software*"],
        )

    def test_star_marker_is_what_widens_a_term(self):
        seen, _ = _capture(
            bq_gsc_service.gsc_watchlist_rows,
            start=date(2026, 7, 1), end=date(2026, 7, 31),
            terms=["hvac software*"], client_slug="acme",
        )
        sql = seen["sql"]
        self.assertIn("ENDS_WITH(t, '*')", sql)
        self.assertIn("LIKE CONCAT('%', RTRIM(LOWER(t), '*'), '%')", sql)
        self.assertIn("LOWER(q.query) = LOWER(t)", sql)

    def test_no_terms_never_reaches_bigquery(self):
        for fn, kwargs in (
            (bq_gsc_service.gsc_watchlist_rows, {"terms": ["*", "  "]}),
            (bq_gsc_service.gsc_watchlist_series, {"terms": []}),
            (bq_gsc_service.gsc_page_metrics, {"pages": ["", "  "]}),
        ):
            seen, result = _capture(
                fn, start=date(2026, 7, 1), end=date(2026, 7, 31),
                client_slug="acme", **kwargs,
            )
            self.assertEqual(result, [])
            self.assertNotIn("sql", seen)

    def test_series_is_weekly_per_term(self):
        seen, _ = _capture(
            bq_gsc_service.gsc_watchlist_series,
            start=date(2026, 5, 1), end=date(2026, 7, 31),
            terms=["seo audit"], client_slug="acme",
        )
        sql = seen["sql"]
        self.assertIn("DATE_TRUNC(q.date, WEEK(MONDAY))", sql)
        self.assertIn("GROUP BY term, week_start", sql)
        self.assertIn("avg_position", sql)

    def test_page_metrics_accept_a_path_or_a_full_url(self):
        seen, _ = _capture(
            bq_gsc_service.gsc_page_metrics,
            start=date(2026, 7, 1), end=date(2026, 7, 31),
            pages=["/services/b2b"], client_slug="acme",
        )
        sql = seen["sql"]
        # Keyed by what the caller asked for, so a row can find its own page.
        self.assertIn("p AS page_key", sql)
        self.assertIn("STARTS_WITH(p, '/') AND ENDS_WITH(g.page_url, p)", sql)


class WatchlistDemoDataTests(unittest.TestCase):
    def test_demo_returns_a_row_and_a_series_per_watched_term(self):
        payload = {"terms": ["b2b marketing agency", "hvac software*"],
                   "start": "2026-05-01", "end": "2026-07-31"}
        rows = demo_data.generate("gsc.watchlist", payload)
        self.assertEqual({r["term"] for r in rows}, set(payload["terms"]))
        weekly = demo_data.generate("gsc.watchlist_weekly", payload)
        self.assertTrue(weekly)
        for term in payload["terms"]:
            series = [w for w in weekly if w["term"] == term]
            self.assertEqual(len(series), 13)
            self.assertTrue(all(w["avg_position"] >= 1 for w in series))

    def test_unknown_watchlist_keys_degrade_to_a_list(self):
        self.assertEqual(demo_data.generate("gsc.watchlist", {}), [])
        self.assertEqual(demo_data.generate("gsc.watchlist_pages", {}), [])


class WatchlistRenderTests(unittest.TestCase):
    def setUp(self):
        self.html = render_bigquery_dashboard_page(
            client_slug="demo", api_client_key="demo", label="Demo",
            use_session=True, session_email="t@e.com",
        )

    def test_section_renders_with_its_table(self):
        self.assertIn('id="sec-gsc-watchlist"', self.html)
        self.assertIn('id="gscWatchTable"', self.html)
        self.assertIn("/api/clients/demo/gsc/watchlist", self.html)
        self.assertIn("/api/clients/demo/gsc/watchlist-config", self.html)
        # Sits above the broad branded/target card it complements.
        self.assertLess(self.html.index('id="sec-gsc-watchlist"'),
                        self.html.index('id="card-gsc-kw"'))

    def test_editing_lives_in_the_list_not_a_separate_form(self):
        """The list IS the editor: an add button on the panel and click-to-edit
        cells, with no second copy of the watchlist to keep in sync."""
        self.assertIn('id="gscWatchAdd"', self.html)
        self.assertIn("data-watch-edit=", self.html)   # click-to-edit cells
        self.assertIn("data-watch-rm=", self.html)     # per-row remove
        self.assertNotIn("gscWatchEditor", self.html)  # the old side panel

    def test_rows_can_be_reordered_and_list_order_is_the_default(self):
        """The stored order is the list's own order, so it is what the table
        opens in and what a drag rewrites."""
        self.assertIn("data-watch-grip=", self.html)          # per-row drag handle
        self.assertIn('data-watch-key="manual"', self.html)   # header back to list order
        self.assertIn("sortKey:'manual'", self.html)          # ...and the default
        self.assertIn("function moveWatchItem", self.html)
        # Keyboard reordering, for anyone not dragging with a mouse.
        self.assertIn("ArrowUp", self.html)

    def test_bulk_add_is_a_popover_that_starts_closed(self):
        self.assertIn('id="gscWatchBulkText"', self.html)
        self.assertIn('id="gscWatchBulkAdd"', self.html)
        # The box is a popover on its button, and it is closed until clicked.
        self.assertIn('class="watch-pop" id="gscWatchBulk"', self.html)
        self.assertRegex(self.html, r'id="gscWatchBulk"[^>]*\shidden')
        self.assertIn("One keyword per line, or several separated by commas", self.html)

    def test_the_popover_never_sets_display(self):
        """`hidden` is what opens and closes the popover. An author `display`
        rule outranks the attribute, which is how the first version of this box
        ended up permanently open for admins."""
        css = dashboard_css()[1]
        rule = css.split(".watch-pop {", 1)[1].split("}", 1)[0]
        self.assertNotIn("display", rule)
        self.assertNotIn(".is-admin .watch-bulk {", css)

    def test_adding_a_keyword_is_the_primary_action(self):
        self.assertRegex(
            self.html,
            r'class="watch-btn watch-btn-primary[^"]*" id="gscWatchAdd"',
        )
        # Bulk add stays secondary beside it.
        self.assertNotRegex(self.html, r'watch-btn-primary[^"]*" id="gscWatchBulkBtn"')

    def test_page_column_is_not_rendered(self):
        """Pulled for now: the stored page per keyword is untouched (it still
        saves and still round-trips through bulk add), but nothing shows it, so
        the request stops paying for page metrics too."""
        self.assertNotIn('<th class="left">Page</th>', self.html)
        self.assertNotIn("watchPageCell", self.html)
        self.assertNotIn("'&pages='", self.html)


if __name__ == "__main__":
    unittest.main()
