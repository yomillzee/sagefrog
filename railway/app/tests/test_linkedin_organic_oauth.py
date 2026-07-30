from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The LinkedIn Organic connector authenticates against its own Community-
# Management app (LINKEDIN_ORGANIC_*) on a dedicated "linkedin_organic" OAuth
# platform, separate from the paid/ads app. These tests pin that wiring so it
# can't silently regress back to sharing the ads token store.


class OrganicOAuthWiringTests(unittest.TestCase):
    def test_platform_registered_everywhere(self) -> None:
        import oauth_flows
        import oauth_store
        self.assertIn("linkedin_organic", oauth_flows.PLATFORMS)
        self.assertIn("linkedin_organic", oauth_store.PLATFORMS)

    def test_connector_uses_dedicated_platform_and_scope(self) -> None:
        from connectors.linkedin_organic import (
            LinkedInOrganicConnector,
            ORGANIC_ADMIN_SCOPE,
        )
        self.assertEqual(LinkedInOrganicConnector.oauth_platform, "linkedin_organic")
        # The real LinkedIn scope name — there is no r_organization_admin.
        self.assertEqual(ORGANIC_ADMIN_SCOPE, "rw_organization_admin")

    def test_scopes_are_organic_not_ads(self) -> None:
        import oauth_flows
        self.assertIn("rw_organization_admin", oauth_flows.LINKEDIN_ORGANIC_SCOPES)
        self.assertNotIn("r_ads", oauth_flows.LINKEDIN_ORGANIC_SCOPES)
        self.assertNotIn("r_organization_admin ", oauth_flows.LINKEDIN_ORGANIC_SCOPES + " ")

    def test_authorize_url_uses_organic_app_and_scopes(self) -> None:
        import oauth_flows
        prev = os.environ.get("LINKEDIN_ORGANIC_CLIENT_ID")
        os.environ["LINKEDIN_ORGANIC_CLIENT_ID"] = "organic-app-123"
        try:
            url = oauth_flows.build_authorize_url("linkedin_organic", state="st")
        finally:
            if prev is None:
                os.environ.pop("LINKEDIN_ORGANIC_CLIENT_ID", None)
            else:
                os.environ["LINKEDIN_ORGANIC_CLIENT_ID"] = prev
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["client_id"][0], "organic-app-123")
        self.assertEqual(q["scope"][0], oauth_flows.LINKEDIN_ORGANIC_SCOPES)
        self.assertTrue(q["redirect_uri"][0].endswith("/oauth/linkedin_organic/callback"))


class ListAccountsGuidanceTests(unittest.TestCase):
    def test_empty_discovery_raises_actionable_message(self) -> None:
        import connectors.linkedin_organic as lo
        import linkedin_organic_service as los

        orig_token = lo._get_access_token
        orig_list = los.list_organizations
        lo._get_access_token = lambda slug: "TOK"  # type: ignore
        los.list_organizations = lambda **kw: []  # type: ignore
        try:
            with self.assertRaises(RuntimeError) as ctx:
                lo.LinkedInOrganicConnector().list_accounts(client_slug="apex")
        finally:
            lo._get_access_token = orig_token
            los.list_organizations = orig_list
        msg = str(ctx.exception).lower()
        self.assertIn("company page", msg)
        self.assertIn("re-authorize", msg)

    def test_discovered_accounts_pass_through(self) -> None:
        import connectors.linkedin_organic as lo
        import linkedin_organic_service as los

        orig_token = lo._get_access_token
        orig_list = los.list_organizations
        lo._get_access_token = lambda slug: "TOK"  # type: ignore
        los.list_organizations = lambda **kw: [{"id": "1", "name": "Apex", "status": ""}]  # type: ignore
        try:
            out = lo.LinkedInOrganicConnector().list_accounts(client_slug="apex")
        finally:
            lo._get_access_token = orig_token
            los.list_organizations = orig_list
        self.assertEqual(out, [{"id": "1", "name": "Apex", "status": ""}])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
