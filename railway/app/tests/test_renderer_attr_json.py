"""Tests for JSON carried in HTML *attributes* by the dashboard renderers.

The chart carries its labels and values as HTML attributes that ``_CHART_JS``
JSON-parses. The parse is wrapped in a bare ``catch { return; }``, so a value
the browser truncates produces a blank chart with nothing in the console — the
failure is invisible from the outside, which is why it is pinned here instead.

The month label for the first point always carries a two-digit year (``Apr '26``),
so the apostrophe is not an edge case: it is present on every render.
"""
from __future__ import annotations

import json
import sys
import unittest
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.renderers import lead_tracking_renderer as ltr  # noqa: E402
from dashboard.utils.formatting import json_for_html_attr  # noqa: E402


class MonthLabelTests(unittest.TestCase):
    def test_first_label_always_carries_an_apostrophe(self):
        self.assertEqual(ltr._month_label(2026, 4, with_year=True), "Apr '26")
        self.assertEqual(ltr._month_label(2026, 5, with_year=False), "May")


class AttrJsonTests(unittest.TestCase):
    def test_quotes_are_escaped(self):
        out = json_for_html_attr(["Apr '26", 'a "b"'])
        self.assertNotIn("'", out)
        self.assertNotIn('"', out)
        self.assertEqual(json.loads(unescape(out)), ["Apr '26", 'a "b"'])


class ChartAttrTests(unittest.TestCase):
    def _canvas(self, series):
        html = ltr._area_chart(series, "#0b5cab")
        # Parsing as XML is the point: a truncated attribute value spills the
        # rest of the JSON into stray attributes, which fails to parse here the
        # same way it silently misparses in a browser.
        frag = ElementTree.fromstring(html)
        canvas = frag.find("canvas")
        self.assertIsNotNone(canvas, "no canvas emitted")
        return canvas

    def test_labels_and_values_round_trip(self):
        series = [(2026, 4, 22), (2026, 5, 31), (2026, 6, 28)]
        canvas = self._canvas(series)
        self.assertEqual(
            json.loads(canvas.get("data-labels")),
            ["Apr '26", "May", "Jun"],
        )
        self.assertEqual(json.loads(canvas.get("data-values")), [22, 31, 28])

    def test_short_series_renders_a_message_not_a_broken_canvas(self):
        html = ltr._area_chart([(2026, 4, 22)], "#0b5cab")
        self.assertIn("lt-empty", html)
        self.assertNotIn("<canvas", html)


class GscAndSemrushAttrTests(unittest.TestCase):
    """The same class of bug: JSON in a single-quoted attribute.

    GSC's queries and Semrush's keywords routinely contain apostrophes
    ("children's daycare"), and unlike the chart their reader does not swallow
    the parse error — GSC's sortable-table script throws and stops working.
    """

    ROWS = [
        {"query": "children's daycare near me", "clicks": 120,
         "impressions": 3000, "ctr": 4.0, "avg_position": 3.2},
        {"query": 'he said "hi" & left <b>', "clicks": 80,
         "impressions": 2200, "ctr": 3.6, "avg_position": 5.1},
    ]
    COLS = [("query", "Query", "left", "name"), ("clicks", "Clicks", "right", "int")]

    def test_gsc_table_attrs_round_trip(self):
        from dashboard.renderers.gsc_renderer import _gsc_table

        table = ElementTree.fromstring(_gsc_table(self.ROWS, self.COLS)).find("table")
        self.assertIsNotNone(table)
        self.assertEqual(
            [r["query"] for r in json.loads(table.get("data-rows"))],
            ["children's daycare near me", 'he said "hi" & left <b>'],
        )
        self.assertEqual(json.loads(table.get("data-cols"))[0]["key"], "query")
        self.assertEqual(
            [k for k in table.attrib if k not in ("class", "data-rows", "data-cols")],
            [],
            "stray attributes mean the value was truncated",
        )

    def test_attr_helper_survives_a_quote_heavy_payload(self):
        kw = [{"keyword": "kid's \"best\" shoes & socks", "pos": 3}]
        frag = f'<table data-rows="{json_for_html_attr(kw)}"/>'
        self.assertEqual(json.loads(ElementTree.fromstring(frag).get("data-rows")), kw)


if __name__ == "__main__":
    unittest.main()
