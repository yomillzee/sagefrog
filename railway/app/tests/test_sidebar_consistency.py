from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# A Nixon-style dashboard must present the identical chrome on every page --
# same section nav in the sidebar, same account cluster (Files / notifications /
# avatar) top right in the sticky bar -- so items never appear/vanish as the user
# moves between the dashboard, settings, connectors, and files pages.


def _section_items(html: str) -> list[str]:
    m = re.search(r'<nav class="dash-sidebar-nav"[^>]*>(.*?)</nav>', html, re.S)
    assert m, "no section nav found"
    return re.findall(
        r'class="dash-view-btn[^"]*"[^>]*>\s*<svg.*?</svg>\s*<span>([^<]+)</span>',
        m.group(1), re.S,
    )


def _account_bar_items(html: str) -> list[str]:
    """What the top-right account cluster offers, in order: the icon buttons by
    their title, then "Avatar" for the profile trigger that closes it out."""
    m = re.search(r'<div class="acct-bar">(.*?)\n        </div>', html, re.S)
    assert m, "no account bar found"
    inner = m.group(1)
    items = re.findall(r'class="acct-btn[^"]*"[^>]*title="([^"]+)"', inner)
    if 'id="dashProfileTrigger"' in inner:
        items.append("Avatar")
    return items


def _admin_bucket_items(html: str) -> list[str]:
    m = re.search(r'<nav class="dash-sidebar-nav dash-sidebar-nav--admin"[^>]*>(.*?)</nav>',
                  html, re.S)
    assert m, "no admin bucket found"
    return re.findall(
        r'class="dash-view-btn[^"]*"[^>]*>\s*<svg.*?</svg>\s*<span>([^<]+)</span>',
        m.group(1), re.S,
    )


def _admin_tab_items(html: str) -> list[str]:
    m = re.search(r'<nav class="admin-top-tabs"[^>]*>(.*?)</nav>', html, re.S)
    assert m, "no admin tab strip found"
    return re.findall(r'class="admin-top-tab[^"]*"[^>]*>([^<]+)<', m.group(1))


class SidebarConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        ccs.list_configs = lambda slug: []  # a fresh client with no connectors

        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
        )

        import client_insight_documents as docs
        self._orig_docs = docs.enabled
        docs.enabled = lambda: True

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg
        import client_insight_documents as docs
        docs.enabled = self._orig_docs

    def _render_all(self) -> dict[str, str]:
        from dashboard.renderers.bigquery_dashboard_renderer import render_bigquery_dashboard_page
        from dashboard.renderers.bigquery_settings_renderer import render_bigquery_settings_page
        from dashboard.renderers.base_layout import render_client_shell_page
        from dashboard.renderers.gtm_renderer import render_gtm_page
        kw = dict(access_key="k", session_is_admin=True, session_email="admin@sf.com")
        return {
            # Event Tracking is a standalone page and used to hand-roll its own
            # section nav, which is how it drifted out of step with every other
            # page. It goes through the shared shell now — assert it here so it
            # can't drift again.
            "event_tracking": render_gtm_page(
                client_slug="test", label="Test Co", **kw),
            "dashboard": render_bigquery_dashboard_page(
                client_slug="test", api_client_key="test", label="Test Co", **kw),
            "settings": render_bigquery_settings_page(
                client_slug="test", api_client_key="test", label="Test Co",
                show_linkedin_backfill=False, **kw),
            "connectors": render_client_shell_page(
                client_slug="test", label="Test Co", active_nav="connectors",
                page_title="C", page_subtitle="", content_html="x",
                admin_tab="connectors", **kw),
            "files": render_client_shell_page(
                client_slug="test", label="Test Co", active_nav="files",
                page_title="F", page_subtitle="", content_html="x", **kw),
        }

    def test_section_nav_identical_on_every_page(self) -> None:
        # setUp gives this client no connectors, so the connector-gated sections
        # (Search Console, Site Performance, …) are absent — what matters here is
        # that whatever the sidebar shows, it shows identically on every page.
        pages = self._render_all()
        expected = ["Overview", "Campaign Explorer", "Website Analytics", "AI Traffic"]
        for name, html in pages.items():
            self.assertEqual(_section_items(html), expected, f"section nav differs on {name}")

    def test_search_console_section_appears_on_every_page_once_connected(self) -> None:
        # The same identical-everywhere guarantee, with GSC connected: the tab
        # must show up on all four pages, not just the dashboard.
        import connector_config_store as ccs
        ccs.list_configs = lambda slug: [
            types.SimpleNamespace(connector_type="gsc", status="connected")
        ]
        expected = ["Overview", "Campaign Explorer", "Website Analytics", "AI Traffic",
                    "Search Console"]
        for name, html in self._render_all().items():
            self.assertEqual(_section_items(html), expected, f"section nav differs on {name}")

    def test_account_bar_identical_on_every_page(self) -> None:
        # Files, the notification bell and the avatar left the sidebar footer for
        # the top right of every page's sticky bar. Same three, same order, on
        # every page -- that is the whole point of moving them somewhere shared.
        pages = self._render_all()
        expected = ["Files", "Notifications", "Avatar"]
        for name, html in pages.items():
            self.assertEqual(_account_bar_items(html), expected,
                             f"account bar differs on {name}")

    def test_account_bar_is_the_only_home_for_those_items(self) -> None:
        # They must not also linger in the sidebar footer, which is gone.
        for name, html in self._render_all().items():
            self.assertNotIn('<div class="dash-sidebar-footer">', html,
                             f"sidebar footer still rendered on {name}")
            self.assertNotIn('class="dash-sidebar-links"', html,
                             f"sidebar footer nav still rendered on {name}")

    def test_settings_lives_in_the_sidebar_admin_bucket(self) -> None:
        # Settings moved out of the profile popover into an "Admin" bucket at the
        # foot of the section nav, on every page, for admins (and standard users).
        pages = self._render_all()
        for name, html in pages.items():
            self.assertEqual(
                _admin_bucket_items(html), ["Settings"],
                f"admin bucket differs on {name}",
            )
            self.assertIn('<div class="dash-sidebar-group">Admin</div>', html,
                          f"Admin group label missing on {name}")
            # …and no longer inside the profile dropdown.
            self.assertNotRegex(
                html,
                r'class="dash-profile-item[^"]*"[^>]*>\s*<svg.*?</svg>\s*<span>Settings</span>',
                f"Settings still in the profile menu on {name}",
            )

    def test_sidebar_layout_order(self) -> None:
        # Logo → client switcher → section nav (with the Admin bucket at its
        # foot) → collapse control, which is now the last thing in the rail: the
        # footer that used to sit under it moved to the top bar.
        for name, html in self._render_all().items():
            order = [
                html.index('class="dash-sidebar-head"'),
                html.index('<div class="dash-sidebar-client">'),
                html.index('<div class="dash-sidebar-scroll">'),
                html.index('<nav class="dash-sidebar-nav dash-sidebar-nav--admin"'),
                html.index('class="dash-sidebar-collapse-row"'),
            ]
            self.assertEqual(order, sorted(order), f"sidebar order wrong on {name}")

    def test_profile_dropdown_present_on_every_page(self) -> None:
        # Every page carries the profile dropdown trigger (the avatar) and a Sign
        # out item inside it. The name and email moved into the menu when the
        # trigger shrank to the avatar alone, so assert they still read there.
        pages = self._render_all()
        for name, html in pages.items():
            self.assertIn('id="dashProfileTrigger"', html, f"profile trigger missing on {name}")
            self.assertIn("<span>Sign out</span>", html, f"Sign out missing on {name}")
            self.assertIn('class="dash-profile-who"', html, f"who-am-I missing on {name}")
            self.assertIn("admin@sf.com", html, f"signed-in email missing on {name}")

    def test_profile_menu_opens_downward_from_the_top_bar(self) -> None:
        # The menu used to hang above a trigger pinned to the sidebar's bottom.
        # From the top bar it has to drop below, or it opens off-screen.
        for name, html in self._render_all().items():
            self.assertIn("dash-profile dash-profile--top", html,
                          f"profile menu still anchored upward on {name}")

    def test_admin_tab_strip_carries_the_agency_options(self) -> None:
        # The Admin surface pages (Connectors, Insights) render the same top tab
        # strip so the agency options read as tabs of one page.
        pages = self._render_all()
        expected = [
            "Connectors", "Insights", "Consent Health",
            "Advanced", "View As",
        ]
        for name in ("connectors", "settings"):
            self.assertEqual(
                _admin_tab_items(pages[name]), expected,
                f"admin tab strip differs on {name}",
            )

    def test_admin_hidden_tab_is_hidden_in_sidebar_server_side(self) -> None:
        # A tab an admin hides (client_dashboard_config.sidebar_hidden_tabs) must
        # render already hidden in the section nav — server-side, so it's gone for
        # every user and browser, not just the one that toggled it.
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            consent_sidebar_enabled=False, sidebar_hidden_tabs=("ai_traffic",),
        )
        pages = self._render_all()
        for name, html in pages.items():
            m = re.search(r'data-tab="ai_traffic"[^>]*>', html)
            self.assertIsNotNone(m, f"AI Traffic nav item missing on {name}")
            self.assertIn("display:none", m.group(0), f"AI Traffic not hidden on {name}")
            self.assertIn('window.__sfHiddenTabs=["ai_traffic"]', html,
                          f"hidden-tabs global missing on {name}")
            # No per-browser localStorage prefs key lingering anywhere.
            self.assertNotIn("nixon_sidebar_pages", html, f"stale prefs key on {name}")

    def test_admin_advanced_tab_reflects_stored_hidden_tabs(self) -> None:
        # The Advanced admin tab's checkboxes mirror the stored server state:
        # a hidden tab is unchecked, everything else checked, and toggling POSTs
        # to the per-client sidebar-tabs endpoint (no localStorage).
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            consent_sidebar_enabled=False, sidebar_hidden_tabs=("ai_traffic",),
        )
        from dashboard.renderers.base_layout import render_admin_tools_page
        html = render_admin_tools_page(
            client_slug="test", label="Test Co", tab="sidebar",
            access_key="k", session_is_admin=True, session_email="admin@sf.com",
        )
        self.assertRegex(html, r'data-tab="ai_traffic"\s*>')  # unchecked
        self.assertRegex(html, r'data-tab="overview" checked>')
        self.assertIn("/dashboard/test/sidebar-tabs", html)
        self.assertNotIn("nixon_sidebar_pages", html)

    def test_advanced_tab_lists_every_toggleable_section(self) -> None:
        # The Advanced tab must offer a checkbox for every section the sidebar can
        # show (core + connector-gated standalone pages), so any can be
        # included/excluded — driven by the canonical SIDEBAR_TOGGLEABLE_TABS.
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            consent_sidebar_enabled=False, sidebar_hidden_tabs=(),
        )
        from dashboard.renderers.base_layout import render_admin_tools_page
        html = render_admin_tools_page(
            client_slug="test", label="Test Co", tab="sidebar",
            access_key="k", session_is_admin=True, session_email="admin@sf.com",
        )
        for key in cdc.SIDEBAR_TOGGLEABLE_TABS:
            self.assertRegex(
                html, r'class="dash-tab-toggle" data-tab="%s"' % re.escape(key),
                f"Advanced tab missing a toggle for '{key}'",
            )
        # The newly-added standalone pages are represented.
        for key in ("lead_tracking", "email_performance", "linkedin_organic",
                    "event_tracking", "site_performance"):
            self.assertIn(key, cdc.SIDEBAR_TOGGLEABLE_TABS)

    def test_hidden_standalone_tab_is_hidden_in_sidebar_server_side(self) -> None:
        # Hiding a connector-gated standalone page (e.g. Email Performance) must
        # render it already display:none in the section nav, just like a core tab.
        import connector_config_store as ccs
        ccs.list_configs = lambda slug: [
            types.SimpleNamespace(connector_type=t, status="connected")
            for t in ("hubspot", "linkedin_organic", "gtm")
        ]
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            consent_sidebar_enabled=False,
            sidebar_hidden_tabs=("email_performance",),
        )
        from dashboard.renderers.base_layout import dashboard_sidebar_view_nav_html
        nav = dashboard_sidebar_view_nav_html(
            client_slug="test", access_key="k", use_session=True, as_tabs=True,
        )
        m = re.search(r'data-tab="email_performance"[^>]*>', nav)
        self.assertIsNotNone(m, "Email Performance nav item missing")
        self.assertIn("display:none", m.group(0), "Email Performance not hidden")
        # A non-hidden standalone page (Lead Tracking) stays visible.
        m2 = re.search(r'data-tab="lead_tracking"[^>]*>', nav)
        self.assertIsNotNone(m2, "Lead Tracking nav item missing")
        self.assertNotIn("display:none", m2.group(0), "Lead Tracking should be visible")

    def test_admin_affordances_hidden_from_non_admins(self) -> None:
        # A non-admin session never renders the admin tab strip, and (with no
        # resolvable internal role) no Settings link — but still gets the profile
        # dropdown with Sign out.
        from dashboard.renderers.base_layout import render_client_shell_page
        html = render_client_shell_page(
            client_slug="test", label="Test Co", active_nav="connectors",
            page_title="C", page_subtitle="", content_html="x",
            admin_tab="connectors",
            access_key="k", session_is_admin=False, session_email="c@x.com",
        )
        self.assertNotIn("<span>Admin</span>", html)
        self.assertNotIn("<span>Settings</span>", html)
        self.assertNotIn('<nav class="admin-top-tabs"', html)
        self.assertIn('id="dashProfileTrigger"', html)
        self.assertIn("<span>Sign out</span>", html)

    def test_settings_gated_by_role(self) -> None:
        # The Admin bucket shows for admin + standard internal users, never for
        # client logins. Role is resolved from web_users, so drive the helper
        # directly with a stubbed lookup (isolated from the DB-backed page render).
        import web_users
        from dashboard.renderers import base_layout

        orig_enabled = web_users.enabled
        orig_lookup = web_users.get_user_by_email
        web_users.enabled = lambda: True

        def _admin_bucket(role: str) -> str:
            web_users.get_user_by_email = lambda email: types.SimpleNamespace(
                role=role, avatar=None
            )
            return base_layout._sidebar_admin_nav_html(
                email="person@example.com",
                session_is_admin=(role == "admin"),
                active_nav="files",
                connectors_url="/dashboard/test/connectors",
            )

        try:
            for role in ("admin", "standard"):
                self.assertIn("<span>Settings</span>", _admin_bucket(role),
                              f"Settings should show for {role}")
            self.assertEqual(_admin_bucket("client"), "",
                             "Settings must be hidden for client logins")
            # A client still gets the profile dropdown + Sign out.
            web_users.get_user_by_email = lambda email: types.SimpleNamespace(
                role="client", avatar=None
            )
            bar = base_layout.account_topbar_html(
                session_email="person@example.com",
                session_is_admin=False,
                active_nav="files",
            )
            self.assertIn('id="dashProfileTrigger"', bar)
            self.assertIn("<span>Sign out</span>", bar)
            # …but no bell: a comment on a client dashboard is internal.
            self.assertNotIn('href="/notifications"', bar)
        finally:
            web_users.enabled = orig_enabled
            web_users.get_user_by_email = orig_lookup

    def test_settings_dropped_in_admin_context(self) -> None:
        # Inside the admin environment the workspace switcher already carries
        # "Admin panel", so the sidebar drops the Admin bucket entirely; the
        # profile dropdown still offers Sign out.
        from dashboard.renderers import base_layout
        self.assertEqual(
            base_layout._sidebar_admin_nav_html(
                email="admin@sf.com",
                session_is_admin=True,
                active_nav="connectors",
                connectors_url="/dashboard/test/connectors",
                admin_context=True,
            ),
            "",
        )
        bar = base_layout.account_topbar_html(
            session_email="admin@sf.com",
            session_is_admin=True,
            active_nav="connectors",
        )
        self.assertNotIn("<span>Settings</span>", bar)
        self.assertIn("<span>Sign out</span>", bar)


if __name__ == "__main__":
    unittest.main()
