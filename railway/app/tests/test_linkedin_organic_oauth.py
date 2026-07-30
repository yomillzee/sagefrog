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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
