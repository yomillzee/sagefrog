"""The admin "What's New" page: the release notes the team reads.

Two things are being guarded. The entry list is edited by hand on every
user-visible change, so these cover the invariants that edit can break (dates
parseable, kinds styleable, newest first). And the page has to render every entry
without the log itself becoming a way to inject markup into the admin panel.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import changelog  # noqa: E402
from dashboard.renderers import changelog_renderer  # noqa: E402
from dashboard.renderers.base_layout import _ADMIN_NAV_ITEMS  # noqa: E402


class EntryShapeTests(unittest.TestCase):
    def test_every_entry_has_a_parseable_iso_date(self):
        for entry in changelog.ENTRIES:
            date.fromisoformat(entry.date)  # must not raise

    def test_every_entry_uses_a_known_kind(self):
        # An unknown kind would render an unstyled badge.
        for entry in changelog.ENTRIES:
            self.assertIn(entry.kind, changelog.KINDS, entry.title)

    def test_every_entry_names_an_area_and_says_what_changed(self):
        for entry in changelog.ENTRIES:
            self.assertTrue(entry.area.strip(), entry.title)
            self.assertTrue(entry.title.strip(), entry.date)
            self.assertTrue(entry.summary.strip(), entry.title)

    def test_entries_read_newest_first_however_the_literal_is_ordered(self):
        dates = [e.date for e in changelog.entries()]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_latest_date_is_the_newest_entry(self):
        self.assertEqual(changelog.latest_date(), changelog.entries()[0].date)

    def test_unknown_kind_falls_back_rather_than_going_unstyled(self):
        self.assertEqual(
            changelog.kind_class("nonsense"), changelog.KINDS[changelog.DEFAULT_KIND][1]
        )
        self.assertEqual(
            changelog.kind_label(""), changelog.KINDS[changelog.DEFAULT_KIND][0]
        )


class RelativeDayTests(unittest.TestCase):
    """The freshness scent next to each date."""

    def test_reads_in_the_units_a_person_would_use(self):
        today = date(2026, 8, 13)
        cases = {
            "2026-08-13": "today",
            "2026-08-12": "yesterday",
            "2026-08-10": "3 days ago",
            "2026-08-01": "1 week ago",
            "2026-07-20": "3 weeks ago",
            "2026-06-01": "2 months ago",
            "2025-01-01": "1 year ago",
        }
        for iso, expected in cases.items():
            self.assertEqual(changelog_renderer._relative_day(iso, today), expected, iso)

    def test_a_junk_date_is_blank_not_a_crash(self):
        self.assertEqual(changelog_renderer._relative_day("soon", date(2026, 8, 13)), "")

    def test_dates_read_as_prose_without_a_zero_pad(self):
        self.assertEqual(changelog_renderer._pretty_date("2026-08-03"), "August 3, 2026")
        self.assertEqual(changelog_renderer._pretty_date("nope"), "nope")


class PageTests(unittest.TestCase):
    def _render(self, today=date(2026, 8, 13)):
        return changelog_renderer.render_changelog_page(
            user_email="admin@sagefrog.com", today=today
        )

    def test_every_entry_makes_it_onto_the_page(self):
        html = self._render()
        for entry in changelog.entries():
            self.assertIn(changelog_renderer._esc(entry.title), html, entry.title)
            self.assertIn(changelog_renderer._esc(entry.summary), html, entry.title)
            for line in entry.details:
                self.assertIn(changelog_renderer._esc(line), html, line)

    def test_same_day_entries_group_under_one_date_marker(self):
        html = self._render()
        days = {e.date for e in changelog.entries()}
        self.assertEqual(html.count('class="day-date"'), len(days))

    def test_entry_text_is_escaped_not_injected(self):
        entry = changelog.Entry(
            date="2026-08-13", title="<script>x</script>", area="Admin",
            summary="a & b", kind="new", details=("<b>hi</b>",),
        )
        html = changelog_renderer._entry_html(entry)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>hi</b>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("a &amp; b", html)

    def test_page_renders_inside_the_admin_shell_with_the_nav_lit(self):
        html = self._render()
        self.assertIn("dash-sidebar-nav", html)
        self.assertIn('href="/admin/changelog"', html)
        self.assertIn('aria-current="page"', html)

    def test_an_empty_log_is_a_page_not_a_crash(self):
        original = changelog.ENTRIES
        try:
            changelog.ENTRIES = ()
            html = self._render()
        finally:
            changelog.ENTRIES = original
        self.assertIn("Nothing shipped yet", html)


class NavTests(unittest.TestCase):
    def test_whats_new_is_reachable_from_the_admin_sidebar(self):
        keys = {key: href for key, _label, href in _ADMIN_NAV_ITEMS}
        self.assertEqual(keys.get("changelog"), "/admin/changelog")


if __name__ == "__main__":
    unittest.main()
