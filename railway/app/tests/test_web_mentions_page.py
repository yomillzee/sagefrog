"""The Web Mentions page: what it shows, what it must never show, and the wiring.

The store's Postgres reads are not exercised here (they need a database); what is
covered is everything the page and report decide for themselves — the
previous-period comparison, the share-of-mentions maths and its disclaimer, the
admin-only alert panel, escaping, and that a feed URL cannot reach the browser.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import web_mentions_service as service  # noqa: E402
import web_mentions_store as store  # noqa: E402
from dashboard.renderers import web_mentions_renderer as R  # noqa: E402

FEED_URL = "https://www.google.com/alerts/feeds/01234567890123456789/9876543210"


def _alert(alert_id, name, category, *, active=True, **kw) -> store.Alert:
    return store.Alert(
        id=alert_id, client_slug="acme", name=name, subject=kw.pop("subject", name),
        feed_url=kw.pop("feed_url", FEED_URL), category=category, active=active, **kw
    )


def _mention(mid, *, alert_id=1, name="EOS Worldwide", category="brand",
             title="A headline", source="Business Journal", day=date(2026, 8, 26),
             estimated=False, url="https://bizjournal.example/a") -> store.Mention:
    return store.Mention(
        id=mid, client_slug="acme", alert_id=alert_id, alert_name=name, category=category,
        subject=name, title=title, url=url, google_url=None, source=source,
        snippet="A snippet", published_at=None if estimated else datetime(2026, 8, 26, tzinfo=UTC),
        published_estimated=estimated, mention_date=day,
        discovered_at=datetime(2026, 8, 27, tzinfo=UTC),
    )


def _report(**kw) -> service.WebMentionsReport:
    today = date(2026, 8, 28)
    report = service.WebMentionsReport(
        client_slug="acme", label="Acme Co", configured=True, range_days=30,
        start=date(2026, 7, 30), end=today,
        prev_start=date(2026, 6, 30), prev_end=date(2026, 7, 29),
        total=42, prev_total=31, brand=18, competitor=13, sources=9,
        daily=[{"date": date(2026, 8, 26), "count": 3}],
        mentions=[_mention(1)],
        alerts=[_alert(1, "EOS Worldwide", "brand"), _alert(2, "Ninety", "competitor")],
        alert_counts={1: 18, 2: 13},
        source_options=["Business Journal"],
    )
    for key, value in kw.items():
        setattr(report, key, value)
    return report


def _render(report, *, admin=False, **kw) -> str:
    return R.render_web_mentions_page(
        client_slug="acme", label="Acme Co", report=report, use_session=True,
        session_email="t@e.com", session_is_admin=admin, **kw
    )


class ShareOfMentionsTests(unittest.TestCase):
    def test_percentages_and_tail_rollup(self):
        raw = [{"subject": f"Sub{i}", "category": "competitor", "count": 10 - i} for i in range(9)]
        raw[0] = {"subject": "EOS Worldwide", "category": "brand", "count": 10}
        rows, total = service._share_rows(raw)
        self.assertEqual(total, sum(r["count"] for r in raw))
        # Six named subjects, then everything else as one "Other" row.
        self.assertEqual(len(rows), service.SHARE_MAX_SUBJECTS + 1)
        self.assertEqual(rows[-1].subject, "Other")
        self.assertAlmostEqual(sum(r.pct for r in rows), 100.0, places=4)

    def test_no_monitored_mentions_means_no_panel(self):
        self.assertEqual(service._share_rows([]), ([], 0))
        self.assertEqual(R._share_section(_report(share=[], share_total=0)), "")

    def test_the_panel_says_it_is_not_market_share(self):
        report = _report(share=[
            service.ShareRow("EOS Worldwide", "brand", 18, 58.0),
            service.ShareRow("Ninety", "competitor", 13, 42.0),
        ], share_total=31)
        html = R._share_section(report)
        self.assertIn("Share of mentions", html)
        self.assertIn("not market share", html)
        self.assertIn("58%", html)


class SummaryTests(unittest.TestCase):
    def test_comparison_badge_reflects_the_previous_period(self):
        up = R._delta(42, 31, window_days=30)
        self.assertIn("▲", up)
        self.assertIn("35%", up)
        self.assertIn("down", R._delta(20, 40, window_days=30))
        self.assertIn("flat", R._delta(40, 40, window_days=30))
        # Nothing to compare against says so rather than showing a fake +100%.
        self.assertIn("no prior", R._delta(5, 0, window_days=30))
        self.assertEqual(R._delta(0, 0, window_days=30), "")

    def test_trend_series_fills_days_with_no_coverage(self):
        report = _report(start=date(2026, 8, 24), end=date(2026, 8, 27),
                         daily=[{"date": date(2026, 8, 26), "count": 3}])
        labels, values = R._daily_series(report)
        self.assertEqual(labels, ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"])
        self.assertEqual(values, [0, 0, 3, 0])


class PageTests(unittest.TestCase):
    def test_the_feed_url_never_reaches_the_browser(self):
        report = _report()
        for admin in (True, False):
            html = _render(report, admin=admin)
            self.assertNotIn("01234567890123456789", html)
            self.assertNotIn("9876543210", html)

    def test_admins_get_the_alert_panel_and_clients_do_not(self):
        report = _report()
        admin_html = _render(report, admin=True)
        self.assertIn("Monitored alerts", admin_html)
        self.assertIn("Add alert", admin_html)
        self.assertIn("Sync now", admin_html)
        # …and the masked feed shape, which is all an admin ever needs.
        self.assertIn("google.com/alerts/feeds", admin_html)

        client_html = _render(report, admin=False)
        self.assertNotIn("Monitored alerts", client_html)
        self.assertNotIn("Add alert", client_html)
        self.assertNotIn("/web-mentions/alerts", client_html)

    def test_summary_trend_and_table_are_all_on_the_page(self):
        html = _render(_report(), admin=False)
        for expected in ("Total mentions", "Brand mentions", "Competitor mentions",
                         "Unique sources", "Mentions over time", "Recent mentions",
                         "Business Journal", "A headline"):
            self.assertIn(expected, html)

    def test_every_filter_control_is_rendered_once(self):
        html = _render(_report(), admin=False)
        for control in ('name="range"', 'name="alert"', 'name="category"', 'name="source"'):
            self.assertEqual(html.count(control), 1, control)

    def test_a_discovered_date_is_labelled_as_one(self):
        report = _report(mentions=[_mention(1, estimated=True)])
        self.assertIn("discovered", R._mentions_section(report))

    def test_headlines_and_alert_names_are_escaped(self):
        report = _report(
            mentions=[_mention(1, title="<script>alert(1)</script>", source="<b>src</b>")],
            alerts=[_alert(1, "<script>x</script>", "brand")],
        )
        html = _render(report, admin=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_an_account_with_no_alerts_tells_an_admin_how_to_start(self):
        report = service.WebMentionsReport(
            client_slug="acme", label="Acme Co", configured=False, range_days=30,
            start=date(2026, 7, 30), end=date(2026, 8, 28),
        )
        admin_html = _render(report, admin=True)
        self.assertIn("No Google Alerts are being monitored", admin_html)
        self.assertIn("Add alert", admin_html)
        # A client user is pointed at their team rather than an empty admin form.
        client_html = _render(report, admin=False)
        self.assertIn("Ask your Sagefrog team", client_html)

    def test_a_failing_feed_is_surfaced_to_admins_only(self):
        report = _report(alerts=[
            _alert(1, "EOS Worldwide", "brand",
                   last_error_message="The feed returned 404 — the Google Alert may have been deleted.",
                   last_checked_at=datetime(2026, 8, 28, tzinfo=UTC)),
        ])
        self.assertIn("feed failing", _render(report, admin=True))
        self.assertNotIn("feed failing", _render(report, admin=False))

    def test_flash_messages_render(self):
        self.assertIn("Alert saved.", _render(_report(), admin=True, flash="Alert saved."))
        self.assertIn("Bad feed", _render(_report(), admin=True, flash_error="Bad feed"))


class WiringTests(unittest.TestCase):
    def test_the_page_route_is_registered(self):
        from dashboard.routes.web_mentions_routes import router

        paths = {r.path for r in router.routes}
        self.assertIn("/dashboard/{client_slug}/web-mentions", paths)
        self.assertIn("/dashboard/{client_slug}/web-mentions.json", paths)
        self.assertIn("/dashboard/{client_slug}/web-mentions/alerts", paths)
        self.assertIn("/dashboard/{client_slug}/web-mentions/sync", paths)
        self.assertIn("/internal/web-mentions/ingest-due", paths)

    def test_the_router_is_attached_to_the_app(self):
        import dashboard.routes as routes

        self.assertIn("web_mentions_router", dir(routes))

    def test_the_sidebar_shows_the_tab_only_once_an_alert_exists(self):
        from dashboard.renderers import base_layout

        original = base_layout.platform_nav_flags
        try:
            def flags(_slug, *, show):
                base = {k: False for k in (
                    "show_connectors", "show_lead_tracking", "show_email_performance",
                    "show_linkedin_organic", "show_bluesky", "show_gsc", "show_gtm",
                    "show_pagespeed", "show_semrush", "show_consent", "show_web_mentions",
                )}
                base["_connector_read_ok"] = True
                base["show_web_mentions"] = show
                return base

            base_layout.platform_nav_flags = lambda slug: flags(slug, show=True)
            on = base_layout.dashboard_sidebar_view_nav_html(
                client_slug="acme", access_key=None, use_session=True, as_tabs=False)
            base_layout.platform_nav_flags = lambda slug: flags(slug, show=False)
            off = base_layout.dashboard_sidebar_view_nav_html(
                client_slug="acme", access_key=None, use_session=True, as_tabs=False)
        finally:
            base_layout.platform_nav_flags = original

        self.assertIn("Web Mentions", on)
        self.assertIn("/dashboard/acme/web-mentions", on)
        self.assertNotIn("Web Mentions", off)

    def test_the_tab_can_be_hidden_per_client(self):
        import client_dashboard_config

        self.assertIn("web_mentions", client_dashboard_config.SIDEBAR_TOGGLEABLE_TABS)

    def test_alerts_never_serialize_their_feed_url(self):
        payload = _alert(1, "EOS Worldwide", "brand").public_dict(mention_count=3)
        self.assertNotIn("feed_url", payload)
        self.assertNotIn(FEED_URL, str(payload))
        self.assertIn("feed_url_masked", payload)


if __name__ == "__main__":
    unittest.main()
