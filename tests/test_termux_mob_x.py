from __future__ import annotations

import json
import unittest
from urllib.parse import urlparse

from agent.discovery.termux_mob_x import (
    _media_url,
    _parse_post,
    _post_id_from_mirror_url,
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
    def __init__(
        self,
        search_body: str,
        posts: dict[str, dict],
        mirror_body: str = "",
        mirror_responses: dict[str, FakeResponse] | None = None,
        profile_response: FakeResponse | None = None,
    ):
        self.search_body = search_body
        self.posts = posts
        self.mirror_body = mirror_body
        self.mirror_responses = mirror_responses or {}
        self.profile_response = profile_response
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if url == "https://unifapi.com/tools/x/profile":
            return self.profile_response or FakeResponse(status_code=403)
        if url == "https://www.bing.com/search":
            return FakeResponse(text=self.search_body)
        if url == "https://cdn.syndication.twimg.com/tweet-result":
            post_id = str((kwargs.get("params") or {}).get("id") or "")
            return FakeResponse(payload=self.posts.get(post_id, {}))
        if url in self.mirror_responses:
            return self.mirror_responses[url]
        if url.startswith((
            "https://mobile.twstalker.com/",
            "https://www.twstalker.com/",
            "https://twstalker.com/",
            "https://ww.twstalker.com/",
            "https://www6.twstalker.com/",
        )):
            if url == "https://mobile.twstalker.com/mobgamesstudios":
                return FakeResponse(text=self.mirror_body)
            return FakeResponse(status_code=403)
        if url == "https://html.duckduckgo.com/html/":
            return FakeResponse(text="")
        raise AssertionError("unexpected_url")


def syndication_post(post_id: str, *, handle: str = "mobgamesstudios", media=None, text="Official Mob update", created_at="Wed Oct 14 12:00:00 +0000 2026"):
    return {
        "id_str": post_id,
        "text": text,
        "created_at": created_at,
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

    def test_results_are_sorted_by_newest_verified_publication_time(self):
        older_id = "1734258130186559716"
        newer_id = "1734258130186559717"
        body = (
            "<rss><channel>"
            f"<item><link>https://x.com/mobgamesstudios/status/{older_id}</link></item>"
            f"<item><link>https://x.com/mobgamesstudios/status/{newer_id}</link></item>"
            "</channel></rss>"
        )
        session = FakeSession(body, {
            older_id: syndication_post(older_id, created_at="Mon Oct 12 10:00:00 +0000 2026"),
            newer_id: syndication_post(newer_id, created_at="Wed Oct 14 12:00:00 +0000 2026"),
        })
        candidates, _ = discover_mob_x_posts(session=session, sleep_seconds=0)
        self.assertEqual(
            [row["external_id"] for row in candidates],
            ["x-" + newer_id, "x-" + older_id],
        )


    def test_public_profile_supplies_ids_but_x_syndication_is_authoritative(self):
        post_id = "2110158933356847501"
        other_account_id = "2110158933356847502"
        profile = FakeResponse(payload={
            "posts": {"items": [
                {
                    "id": post_id,
                    "url": f"https://x.com/mobgamesstudios/status/{post_id}",
                    "author": "not-the-real-author",
                    "text": "Untrusted profile text",
                    "createdAt": "not-a-trusted-timestamp",
                },
                {
                    "id": other_account_id,
                    "url": f"https://x.com/fanaccount/status/{other_account_id}",
                    "author": "mobgamesstudios",
                    "text": "Must never be used",
                },
            ]}
        })
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {
                post_id: syndication_post(
                    post_id,
                    text="Caption verified by X syndication",
                    created_at="Wed Oct 14 00:01:09 +0000 2026",
                ),
                other_account_id: syndication_post(other_account_id),
            },
            profile_response=profile,
        )

        candidates, attempts = discover_mob_x_posts(session=session, sleep_seconds=0)

        self.assertEqual([row["external_id"] for row in candidates], ["x-" + post_id])
        self.assertEqual(candidates[0]["author"], "@mobgamesstudios")
        self.assertEqual(candidates[0]["original_caption"], "Caption verified by X syndication")
        self.assertEqual(candidates[0]["published_at"], "2026-10-14T00:01:09+00:00")
        self.assertEqual(candidates[0]["media_url"], "https://pbs.twimg.com/media/example.jpg")
        profile_attempt = next(row for row in attempts if row.get("engine") == "unifapi_public_profile")
        self.assertEqual(profile_attempt["result"], "post_ids_found")
        self.assertEqual(profile_attempt["post_urls_found"], 1)
        self.assertTrue(any(url == "https://cdn.syndication.twimg.com/tweet-result" for url, _ in session.calls))
        profile_call = next(call for call in session.calls if call[0] == "https://unifapi.com/tools/x/profile")
        self.assertEqual(profile_call[1]["params"], {"handle": "mobgamesstudios"})
        self.assertFalse(any("Cookie" in (kwargs.get("headers") or {}) for _, kwargs in session.calls))

    def test_public_mirror_is_used_only_for_ids_then_x_verifies_post(self):
        post_id = "1734258130186559716"
        empty_bing = "<rss><channel></channel></rss>"
        mirror_body = (
            '<html><body><a href="/mobgamesstudios/status/'
            + post_id
            + '">View Details</a></body></html>'
        )
        session = FakeSession(empty_bing, {post_id: syndication_post(post_id)}, mirror_body=mirror_body)
        candidates, attempts = discover_mob_x_posts(session=session, sleep_seconds=0)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["external_id"], "x-" + post_id)
        self.assertEqual(candidates[0]["author"], "@mobgamesstudios")
        mirror_attempt = next(item for item in attempts if item.get("engine") == "public_profile_mirror")
        self.assertEqual(mirror_attempt["result"], "post_ids_found")
        self.assertEqual(mirror_attempt["candidate_post_ids"], [post_id])
        self.assertEqual(mirror_attempt["posts_returned"], 1)
        self.assertEqual(mirror_attempt.get("host_used"), "mobile.twstalker.com")
        self.assertTrue(any(url == "https://cdn.syndication.twimg.com/tweet-result" for url, _ in session.calls))

    def test_owner_seed_url_is_still_verified_through_x_syndication(self):
        post_id = "2107878755079213476"
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {post_id: syndication_post(post_id, created_at="Wed Oct 07 17:00:32 +0000 2026")},
        )
        candidates, attempts = discover_mob_x_posts(
            session=session,
            sleep_seconds=0,
            seed_urls=("https://x.com/mobgamesstudios/status/" + post_id,),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["external_id"], "x-" + post_id)
        self.assertEqual(candidates[0]["author"], "@mobgamesstudios")
        seed_attempt = next(item for item in attempts if item.get("engine") == "owner_seed_urls")
        self.assertEqual(seed_attempt["post_urls_found"], 1)
        self.assertEqual(seed_attempt["candidate_post_ids"], [post_id])
        self.assertEqual(seed_attempt["posts_returned"], 1)
        self.assertTrue(any(url == "https://cdn.syndication.twimg.com/tweet-result" for url, _ in session.calls))

    def test_public_mirror_failover_uses_working_alternate_host(self):
        post_id = "1734258130186559716"
        mirror_url = "https://www6.twstalker.com/mobgamesstudios"
        mirror_body = (
            '<html><body><a href="https://www6.twstalker.com/mobgamesstudios/status/'
            + post_id
            + '">View Details</a></body></html>'
        )
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {post_id: syndication_post(post_id)},
            mirror_responses={
                "https://mobile.twstalker.com/mobgamesstudios": FakeResponse(status_code=403),
                mirror_url: FakeResponse(text=mirror_body),
            },
        )
        candidates, attempts = discover_mob_x_posts(session=session, sleep_seconds=0)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["external_id"], "x-" + post_id)
        mirror_attempt = next(item for item in attempts if item.get("engine") == "public_profile_mirror")
        self.assertEqual(mirror_attempt["host_used"], "www6.twstalker.com")
        self.assertIn("mobile.twstalker.com", mirror_attempt["hosts_tried"])
        self.assertIn("www6.twstalker.com", mirror_attempt["hosts_tried"])

    def test_mirror_only_accepts_exact_handle_and_canonical_status_path(self):
        self.assertEqual(
            _post_id_from_mirror_url("https://mobile.twstalker.com/mobgamesstudios/status/1734258130186559716"),
            "1734258130186559716",
        )
        self.assertIsNone(_post_id_from_mirror_url("https://mobile.twstalker.com/fanaccount/status/1734258130186559716"))
        self.assertIsNone(_post_id_from_mirror_url("https://evil.example/mobgamesstudios/status/1734258130186559716"))

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
        self.assertIsNone(_parse_post(syndication_post(post_id, media=two_photos), post_id))


if __name__ == "__main__":
    unittest.main()
