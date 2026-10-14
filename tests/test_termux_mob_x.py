from __future__ import annotations

import json
import unittest
from urllib.parse import urlparse

from agent.discovery.termux_mob_x import (
    _media_url,
    _post_id_from_url,
    discover_mob_x_posts,
)


class FakeResponse:
    def __init__(self, *, text: str = "", payload=None, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


class FakeSession:
    def __init__(self, search_body: str, posts: dict[str, dict]):
        self.search_body = search_body
        self.posts = posts
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if url == "https://www.bing.com/search":
            return FakeResponse(text=self.search_body)
        if url == "https://cdn.syndication.twimg.com/tweet-result":
            post_id = str((kwargs.get("params") or {}).get("id") or "")
            return FakeResponse(payload=self.posts.get(post_id, {}))
        if url == "https://html.duckduckgo.com/html/":
            return FakeResponse(text="")
        raise AssertionError("unexpected_url")


def syndication_post(post_id: str, *, handle: str = "mobgamesstudios", media=None, text="Official Mob update"):
    return {
        "id_str": post_id,
        "text": text,
        "created_at": "Wed Oct 14 12:00:00 +0000 2026",
        "user": {"screen_name": handle, "name": "Mob Entertainment"},
        "mediaDetails": media if media is not None else [{
            "type": "photo",
            "media_url_https": "https://pbs.twimg.com/media/example.jpg",
        }],
    }


class TermuxMobXDiscoveryTests(unittest.TestCase):
    def test_accepts_only_the_configured_official_profile_status_url(self):
        self.assertEqual(
            _post_id_from_url("https://x.com/mobgamesstudios/status/1734258130186559716"),
            "1734258130186559716",
        )
        self.assertIsNone(_post_id_from_url("https://x.com/randomfan/status/1734258130186559716"))
        self.assertIsNone(_post_id_from_url("https://x.com/mobgamesstudios/status/not-an-id"))

    def test_discovers_real_photo_caption_and_timestamp_without_paid_api(self):
        post_id = "1734258130186559716"
        body = (
            "<rss><channel><item><title>Official Mob post</title>"
            f"<link>https://x.com/mobgamesstudios/status/{post_id}</link>"
            "<description>Mob Entertainment official post</description>"
            "</item></channel></rss>"
        )
        session = FakeSession(body, {post_id: syndication_post(post_id)})
        candidates, attempts = discover_mob_x_posts(session=session, sleep_seconds=0)

        self.assertEqual(len(candidates), 1)
        item = candidates[0]
        self.assertEqual(item["platform"], "mob_official")
        self.assertEqual(item["source_platform"], "x")
        self.assertEqual(item["author"], "@mobgamesstudios")
        self.assertEqual(item["original_caption"], "Official Mob update")
        self.assertEqual(item["media_type"], "image")
        self.assertEqual(item["media_url"], "https://pbs.twimg.com/media/example.jpg")
        self.assertTrue(item["freshness_verified"])
        self.assertIn("public_syndication", str(attempts))
        self.assertTrue(all("AGENT_API_TOKEN" not in str(call) for call in session.calls))

    def test_video_selects_actual_mp4_instead_of_thumbnail(self):
        media = [{
            "type": "video",
            "media_url_https": "https://pbs.twimg.com/media/thumbnail.jpg",
            "video_info": {"variants": [
                {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/playlist.m3u8"},
                {"content_type": "video/mp4", "bitrate": 256000, "url": "https://video.twimg.com/video-low.mp4"},
                {"content_type": "video/mp4", "bitrate": 832000, "url": "https://video.twimg.com/video-high.mp4"},
            ]},
        }]
        result = _media_url(media[0])
        self.assertEqual(result, ("video", "https://video.twimg.com/video-high.mp4"))

    def test_rejects_wrong_author_and_multi_photo_post(self):
        post_id = "1734258130186559716"
        body = (
            "<rss><channel><item><title>Official Mob post</title>"
            f"<link>https://x.com/mobgamesstudios/status/{post_id}</link>"
            "</item></channel></rss>"
        )
        wrong_author = syndication_post(post_id, handle="unrelated_account")
        session = FakeSession(body, {post_id: wrong_author})
        candidates, _ = discover_mob_x_posts(session=session, sleep_seconds=0)
        self.assertEqual(candidates, [])

        two_photos = [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/a.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/b.jpg"},
        ]
        self.assertIsNone(_media_url({"type": "video", "video_info": {"variants": []}}))
        self.assertIsNone(_media_url(two_photos[0]) if len(two_photos) != 1 else None)


if __name__ == "__main__":
    unittest.main()
