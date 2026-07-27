from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import meta_service  # noqa: E402

_ENV = meta_service.MetaEnv(
    app_id="app",
    app_secret="secret",
    access_token="tok",
    business_id="biz",
    api_version="v25.0",
)


class FetchVideoDetailsBatchingTests(unittest.TestCase):
    """_fetch_video_details must use Meta's ?ids= multi-get so a large video set
    costs ceil(N/50) calls, not N — the fix for the ad account Ads API call
    limit (code 17 / subcode 2446079) on video-heavy accounts."""

    def test_batches_into_multi_get_calls(self):
        ids = {f"v{i}" for i in range(120)}
        calls = []

        def fake_graph_get(path, *, access_token, params=None, env=None):
            calls.append((path, params))
            requested = (params or {})["ids"].split(",")
            return {vid: {"source": f"src-{vid}", "picture": f"pic-{vid}"} for vid in requested}

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            details = meta_service._fetch_video_details(ids, access_token="tok", env=_ENV)

        # 120 ids / 50 per batch = 3 calls, all multi-get (path "/", ids param)
        self.assertEqual(len(calls), 3)
        for path, params in calls:
            self.assertEqual(path, "/")
            self.assertIn("ids", params)
            self.assertLessEqual(len(params["ids"].split(",")), meta_service._VIDEO_DETAIL_BATCH)
        # every id resolved and parsed
        self.assertEqual(len(details), 120)
        self.assertEqual(details["v7"]["video_url"], "src-v7")
        self.assertEqual(details["v7"]["thumbnail_url"], "pic-v7")

    def test_missing_id_in_batch_response_is_empty_detail(self):
        def fake_graph_get(path, *, access_token, params=None, env=None):
            # Meta omits an inaccessible id from the multi-get response.
            return {"a": {"source": "src-a", "picture": "pic-a"}}

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            details = meta_service._fetch_video_details({"a", "b"}, access_token="tok", env=_ENV)

        self.assertEqual(details["a"]["video_url"], "src-a")
        self.assertEqual(details["b"], {"video_url": "", "thumbnail_url": "", "video_title": ""})

    def test_batch_failure_falls_back_to_individual_fetches(self):
        def fake_graph_get(path, *, access_token, params=None, env=None):
            if path == "/":  # the batch multi-get fails wholesale
                raise RuntimeError("Meta Graph API error 400 on /: bad id in batch")
            # per-id fallback: path is /{video_id}
            vid = path.lstrip("/")
            return {"source": f"src-{vid}", "picture": f"pic-{vid}"}

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            details = meta_service._fetch_video_details({"a", "b"}, access_token="tok", env=_ENV)

        # both resolved via the per-id fallback, so one bad id doesn't blank the chunk
        self.assertEqual(details["a"]["video_url"], "src-a")
        self.assertEqual(details["b"]["thumbnail_url"], "pic-b")

    def test_empty_input_makes_no_calls(self):
        with patch.object(meta_service, "_graph_get") as gg:
            details = meta_service._fetch_video_details(set(), access_token="tok", env=_ENV)
        self.assertEqual(details, {})
        gg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
