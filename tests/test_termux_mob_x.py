from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch
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
        fx_timeline_response: FakeResponse | None = None,
        fx_status_responses: dict[str, FakeResponse] | None = None,
        vx_status_responses: dict[str, FakeResponse] | None = None,
    ):
        self.search_body = search_body
        self.posts = posts
        self.mirror_body = mirror_body
        self.mirror_responses = mirror_responses or {}
        self.profile_response = profile_response
        self.fx_timeline_response = fx_timeline_response
        self.fx_status_responses = fx_status_responses or {}
        self.vx_status_responses = vx_status_responses or {}
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if url == "https://unifapi.com/tools/x/profile":
            return self.profile_response or FakeResponse(status_code=403)
        if url == "https://api.fxtwitter.com/2/profile/mobgamesstudios/statuses":
            return self.fx_timeline_response or FakeResponse(status_code=403)
        if url in self.fx_status_responses:
            return self.fx_status_responses[url]
        if url in self.vx_status_responses:
            return self.vx_status_responses[url]
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


def syndication_post(post_id: str, *, handle: str = "mobgamesstudios", media=None, text="Official Mob update", created_at="Thu Nov 05 12:00:00 +0000 2026"):
    return {
        "id_str": post_id,
        "text": text,
        "created_at": created_at,
        "is_pinned": False,
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

    def test_unordered_public_index_results_are_sorted_oldest_first_by_verified_time(self):
        older_id = "1734258130186559716"
        newer_id = "1734258130186559717"
        body = (
            "<rss><channel>"
            f"<item><link>https://x.com/mobgamesstudios/status/{older_id}</link></item>"
            f"<item><link>https://x.com/mobgamesstudios/status/{newer_id}</link></item>"
            "</channel></rss>"
        )
        session = FakeSession(body, {
            older_id: syndication_post(older_id, created_at="Thu Nov 05 01:00:00 +0000 2026"),
            newer_id: syndication_post(newer_id, created_at="Thu Nov 05 03:00:00 +0000 2026"),
        })
        candidates, _ = discover_mob_x_posts(session=session, sleep_seconds=0)
        self.assertEqual(
            [row["external_id"] for row in candidates],
            ["x-" + older_id, "x-" + newer_id],
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
                    created_at="Thu Nov 05 00:01:09 +0000 2026",
                ),
                other_account_id: syndication_post(other_account_id),
            },
            profile_response=profile,
        )

        candidates, attempts = discover_mob_x_posts(session=session, sleep_seconds=0)

        self.assertEqual([row["external_id"] for row in candidates], ["x-" + post_id])
        self.assertEqual(candidates[0]["author"], "@mobgamesstudios")
        self.assertEqual(candidates[0]["original_caption"], "Caption verified by X syndication")
        self.assertEqual(candidates[0]["published_at"], "2026-11-05T00:01:09+00:00")
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
        mirror_outcomes = mirror_attempt.get("outcomes", [])
        chosen = [row for row in mirror_outcomes if row.get("host") == mirror_attempt.get("host_used")]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["candidate_post_ids"], [post_id])
        self.assertEqual(chosen[0]["posts_returned"], 1)
        self.assertTrue(any(url == "https://cdn.syndication.twimg.com/tweet-result" for url, _ in session.calls))

    def test_owner_seed_url_is_still_verified_through_x_syndication(self):
        post_id = "2107878755079213476"
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {post_id: syndication_post(post_id, created_at="Thu Nov 05 00:30:00 +0000 2026")},
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


    @staticmethod
    def x_cli_row(post_id: str, created_at: str, *, author: str = "mobgamesstudios", **changes):
        row = {
            "id": post_id,
            "created_at": created_at,
            "author": {"username": author},
            "text": "Official Mob post",
            "media": [{"type": "photo", "url": "https://pbs.twimg.com/media/example.jpg"}],
        }
        row.update(changes)
        return row

    def run_x_cli(self, rows, session, *, exit_code=0, stderr=""):
        stdout = "".join(json.dumps(row) + "\\n" for row in rows)
        completed = Mock(returncode=exit_code, stdout=stdout, stderr=stderr)
        with (
            patch.dict("os.environ", {"MOB_X_CLI_PATH": "/tmp/x-cli"}),
            patch("agent.discovery.termux_mob_x.os.path.isfile", return_value=True),
            patch("agent.discovery.termux_mob_x.subprocess.run", return_value=completed) as run,
        ):
            result = discover_mob_x_posts(
                session=session,
                sleep_seconds=0,
                since_utc="2026-11-05T00:00:00Z",
            )
        return result, run

    def test_x_cli_is_first_id_source_and_unordered_timeline_is_filtered_and_oldest_first(self):
        older = "2111111111111111111"
        newer = "2111111111111111112"
        too_old = "2111111111111111113"
        x_rows = [
            self.x_cli_row(newer, "2026-11-06T00:00:00Z"),
            self.x_cli_row(too_old, "2026-11-04T23:59:59Z"),
            self.x_cli_row(older, "2026-11-05T01:00:00Z"),
            self.x_cli_row(newer, "2026-11-06T00:00:00Z"),
        ]
        pin_rows = [
            {"id": older, "created_at": "2026-11-05T01:00:00Z", "author": {"username": "mobgamesstudios"}, "is_pinned": False},
            {"id": newer, "created_at": "2026-11-06T00:00:00Z", "author": {"username": "mobgamesstudios"}, "is_pinned": False},
            {"id": too_old, "created_at": "2026-11-04T23:59:59Z", "author": {"username": "mobgamesstudios"}, "is_pinned": False},
        ]
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {
                older: syndication_post(older, created_at="Thu Nov 05 01:00:00 +0000 2026"),
                newer: syndication_post(newer, created_at="Fri Nov 06 00:00:00 +0000 2026"),
            },
            fx_timeline_response=FakeResponse(payload={"results": pin_rows}),
        )
        candidates, attempts = self.run_x_cli(x_rows, session)
        self.assertEqual([row["external_id"] for row in candidates], ["x-" + older, "x-" + newer])
        self.assertEqual({row["id_source"] for row in candidates}, {"x-cli"})
        self.assertEqual(next(row for row in attempts if row["engine"] == "x_cli_guest_timeline")["candidate_count"], 3)
        fx_call = next(call for call in session.calls if call[0].endswith("/2/profile/mobgamesstudios/statuses"))
        self.assertEqual(fx_call[1]["params"], {"count": "100"})

    def test_reply_retweet_quote_pinned_non_mob_and_text_only_are_skipped(self):
        ids = [str(2111111111111111120 + index) for index in range(7)]
        rows = [
            self.x_cli_row(ids[0], "2026-11-05T01:00:00Z", reply_to="2110000000000000001"),
            self.x_cli_row(ids[1], "2026-11-05T01:00:00Z", is_retweet=True),
            self.x_cli_row(ids[2], "2026-11-05T01:00:00Z", quoted={"id": "2110000000000000001"}),
            self.x_cli_row(ids[3], "2026-11-05T01:00:00Z", is_pinned=True),
            self.x_cli_row(ids[4], "2026-11-05T01:00:00Z", author="fan_account"),
            self.x_cli_row(ids[5], "2026-11-05T01:00:00Z", media=[]),
            self.x_cli_row(ids[6], "2026-11-05T01:00:00Z"),
        ]
        fx_rows = [
            {"id": post_id, "created_at": "2026-11-05T01:00:00Z",
             "author": {"username": "mobgamesstudios"}, "is_pinned": post_id == ids[3]}
            for post_id in ids
        ]
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {ids[6]: syndication_post(ids[6], created_at="Thu Nov 05 01:00:00 +0000 2026")},
            fx_timeline_response=FakeResponse(payload={"results": fx_rows}),
        )
        candidates, attempts = self.run_x_cli(rows, session)
        self.assertEqual([row["external_id"] for row in candidates], ["x-" + ids[6]])
        final_attempt = next(row for row in attempts if row["engine"] == "mob_x_final_filter")
        self.assertGreaterEqual(final_attempt["skipped"].get("reply", 0), 1)
        self.assertGreaterEqual(final_attempt["skipped"].get("retweet", 0), 1)
        self.assertGreaterEqual(final_attempt["skipped"].get("quote", 0), 1)
        self.assertGreaterEqual(final_attempt["skipped"].get("text_only", 0), 1)

    def test_x_cli_failure_falls_back_to_fxtwitter_timeline_then_vxtwitter_status(self):
        post_id = "2111111111111111199"
        fx_url = "https://api.fxtwitter.com/mobgamesstudios/status/" + post_id
        vx_url = "https://api.vxtwitter.com/Twitter/status/" + post_id
        fx_timeline = {
            "id": post_id, "created_at": "2026-11-05T02:00:00Z",
            "author": {"username": "mobgamesstudios"}, "is_pinned": False,
            "media": [{"type": "photo", "url": IMAGE_URL}],
            "text": "Official Mob fallback",
        }
        vx_status = {
            "tweetID": post_id,
            "user_screen_name": "mobgamesstudios",
            "text": "Official Mob fallback",
            "date": "2026-11-05T02:00:00Z",
            "media_extended": [{"type": "photo", "url": IMAGE_URL}],
            "is_pinned": False,
        }
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {},
            fx_timeline_response=FakeResponse(payload={"results": [fx_timeline]}),
            fx_status_responses={fx_url: FakeResponse(status_code=503)},
            vx_status_responses={vx_url: FakeResponse(payload=vx_status)},
        )
        candidates, run = self.run_x_cli([], session, exit_code=5, stderr="rate limit")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["external_id"], "x-" + post_id)
        self.assertEqual(candidates[0]["id_source"], "fxtwitter")
        self.assertEqual(candidates[0]["method"], "vxtwitter_status_fallback")
        command = run.call_args.args[0]
        self.assertIn("--retries=0", command)
        self.assertIn("--timeout=15s", command)
        self.assertIn("--guest", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        self.assertEqual(run.call_args.kwargs["env"]["HOME"].split("/")[-1], "home")

    def test_x_cli_rate_limit_falls_back_to_fxtwitter_timeline(self):
        post_id = "2111111111111111201"
        timeline = {
            "id": post_id, "created_at": "2026-11-05T02:00:00Z",
            "author": {"username": "mobgamesstudios"}, "is_pinned": False,
        }
        session = FakeSession(
            "<rss><channel></channel></rss>",
            {post_id: syndication_post(post_id, created_at="Thu Nov 05 02:00:00 +0000 2026")},
            fx_timeline_response=FakeResponse(payload={"results": [timeline]}),
        )
        candidates, _ = self.run_x_cli([], session, exit_code=5, stderr="HTTP 429 rate limit")
        self.assertEqual([row["external_id"] for row in candidates], ["x-" + post_id])
        self.assertEqual(candidates[0]["id_source"], "fxtwitter")

    def test_empty_result_is_safe_and_has_no_candidates(self):
        session = FakeSession("<rss><channel></channel></rss>", {})
        candidates, attempts = self.run_x_cli([], session, exit_code=5, stderr="rate limit")
        self.assertEqual(candidates, [])
        self.assertEqual(attempts[-1]["result"], "empty")


if __name__ == "__main__":
    unittest.main()
