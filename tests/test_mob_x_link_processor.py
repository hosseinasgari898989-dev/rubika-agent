from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from agent.brain.mob_x_link_processor import (
    MobXLinkError,
    _fxtwitter,
    _vxtwitter,
    clean_caption,
    download_public_media,
    normalize_published_at,
    parse_official_link,
    probe_public_sources,
    select_media,
)


POST_ID = "2107878755079213476"
IMAGE_URL = "https://pbs.twimg.com/media/example.jpg"
VIDEO_URL = "https://video.twimg.com/ext_tw_video/example/pu/vid/avc1/1280x720/example.mp4"


def response(status=200, payload=None, headers=None, url=None):
    item = Mock()
    item.status_code = status
    item.url = url
    item.headers = headers or {}
    item.json.return_value = payload
    item.close = Mock()
    item.iter_content.return_value = iter([])
    return item


class MobXLinkParserTests(unittest.TestCase):

    def test_public_api_probe_does_not_run_on_schedule(self):
        workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "mob-x-link-processor.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        start = workflow.index("  public-api-probe:")
        end = workflow.index("\n  process-one-confirmed-link:", start)
        probe_job = workflow[start:end]
        self.assertIn("github.event_name == 'push'", probe_job)
        self.assertIn("github.event_name == 'pull_request'", probe_job)
        self.assertIn("github.event_name == 'workflow_dispatch'", probe_job)
        self.assertNotIn("github.event_name == 'schedule'", probe_job)

    def test_link_parser_requires_https_exact_official_handle_and_numeric_post_id(self):
        expected = f"https://x.com/mobgamesstudios/status/{POST_ID}"
        for url in (
            expected,
            f"https://twitter.com/mobgamesstudios/status/{POST_ID}",
            f"https://www.x.com/mobgamesstudios/status/{POST_ID}/",
            f"https://mobile.twitter.com/mobgamesstudios/status/{POST_ID}",
        ):
            with self.subTest(url=url):
                self.assertEqual(parse_official_link(url), (expected, POST_ID))
        for url in (
            f"http://x.com/mobgamesstudios/status/{POST_ID}",
            f"https://x.com/someoneelse/status/{POST_ID}",
            f"https://x.com/mobgamesstudios/status/{POST_ID}?s=20",
            "https://example.com/mobgamesstudios/status/" + POST_ID,
            "https://x.com/mobgamesstudios/status/not-a-number",
            "some text https://x.com/mobgamesstudios/status/" + POST_ID,
        ):
            with self.subTest(url=url):
                self.assertIsNone(parse_official_link(url))

    def test_fxtwitter_checks_author_and_post_id_before_returning_media(self):
        payload = {
            "tweet": {
                "id": POST_ID,
                "author": {"screen_name": "mobgamesstudios"},
                "text": "Official Mob announcement https://example.org/info",
                "media": {"photos": [{"type": "photo", "url": IMAGE_URL}]},
            }
        }
        post = _fxtwitter(payload, POST_ID)
        self.assertEqual(post["external_id"], POST_ID)
        self.assertEqual(post["media"][0]["url"], IMAGE_URL)
        self.assertEqual(post["text"].startswith("Official Mob"), True)
        with self.assertRaisesRegex(MobXLinkError, "mob_x_author_mismatch"):
            _fxtwitter({"tweet": {**payload["tweet"], "author": {"screen_name": "imposter"}}}, POST_ID)
        with self.assertRaisesRegex(MobXLinkError, "mob_x_post_id_mismatch"):
            _fxtwitter({"tweet": {**payload["tweet"], "id": "2107878755079213477"}}, POST_ID)

    def test_vxtwitter_checks_exact_author_and_id_and_extracts_video(self):
        payload = {
            "tweetID": POST_ID,
            "user_screen_name": "@mobgamesstudios",
            "text": "Official video announcement",
            "media_extended": [{"type": "video", "url": VIDEO_URL}],
        }
        post = _vxtwitter(payload, POST_ID)
        self.assertEqual(post["media"][0]["url"], VIDEO_URL)
        self.assertEqual(post["provider"], "vxtwitter")
        with self.assertRaisesRegex(MobXLinkError, "mob_x_author_mismatch"):
            _vxtwitter({**payload, "user_screen_name": "not_mob"}, POST_ID)

    def test_probe_is_read_only_and_uses_no_api_key(self):
        session = Mock()
        session.get.side_effect = [
            response(status=503),
            response(payload={
                "tweetID": POST_ID,
                "user_screen_name": "mobgamesstudios",
                "text": "Mob update",
                "media_extended": [{"type": "photo", "url": IMAGE_URL}],
            }),
        ]
        report = probe_public_sources(POST_ID, session=session)
        self.assertTrue(report["available"])
        self.assertEqual(report["usable_sources"], ["vxtwitter"])
        self.assertFalse(report["api_keys_used"])
        self.assertFalse(report["media_downloaded"])
        self.assertFalse(report["publishing_attempted"])
        self.assertEqual(len(report["results"]), 2)
        self.assertEqual(session.get.call_count, 2)
        self.assertNotIn("Mob update", str(report))

    def test_probe_rejects_invalid_id_without_network_call(self):
        session = Mock()
        with self.assertRaisesRegex(MobXLinkError, "invalid_external_id"):
            probe_public_sources("../unexpected", session=session)
        session.get.assert_not_called()

    def test_media_selection_accepts_only_expected_cdn_host(self):
        post = {"media": [{"type": "photo", "media_url_https": IMAGE_URL}]}
        with patch("agent.brain.mob_x_link_processor.socket.getaddrinfo",
                   return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
            self.assertEqual(select_media(post), ("image", IMAGE_URL))
            with self.assertRaisesRegex(MobXLinkError, "mob_x_media_host_rejected"):
                select_media({"media": [{"type": "photo", "url": "https://example.com/img.jpg"}]})

    def test_oversized_video_fails_with_explicit_error_and_no_body_download(self):
        item = response(
            headers={"Content-Type": "video/mp4", "Content-Length": "1001"},
            url=VIDEO_URL,
        )
        session = Mock()
        session.get.return_value = item
        with patch("agent.brain.mob_x_link_processor.socket.getaddrinfo",
                   return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
            with self.assertRaisesRegex(MobXLinkError, "mob_x_video_too_large"):
                download_public_media(VIDEO_URL, "video", "/tmp/rubika-agent-mob-x-test", session=session, max_bytes=1000)
        item.iter_content.assert_not_called()
        item.close.assert_called()

    def test_caption_is_plain_and_published_time_is_normalized(self):
        self.assertEqual(clean_caption(" **Hello**\n\nhttps://example.org/a "), "Hello")
        self.assertEqual(
            normalize_published_at("2026-10-20T12:34:56+02:00"),
            "2026-10-20T10:34:56Z",
        )
        self.assertIsNone(normalize_published_at("not-a-date"))


if __name__ == "__main__":
    unittest.main()
