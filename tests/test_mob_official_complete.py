from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.discovery.mob_official_complete import _detail_from_page, _discover_youtube, _fingerprints, discover_all_official_posts, discover_complete_official_posts


class FakeResponse:
    def __init__(self, body=None, payload=None, status=200):
        self.status_code = status
        self.content = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        self.text = body if isinstance(body, str) else self.content.decode("utf-8", errors="replace")
        self.encoding = "utf-8"
        self._payload = payload

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url not in self.responses:
            raise RuntimeError("unexpected_test_url")
        return self.responses[url]


def config_file(folder, sources):
    path = Path(folder) / "sources.json"
    path.write_text(json.dumps({"version": 1, "official_sources": sources}), encoding="utf-8")
    return path


class OfficialMobCompleteDiscoveryTests(unittest.TestCase):
    def test_same_long_caption_fingerprints_even_when_titles_differ(self):
        caption = (
            "Mob Entertainment announces an exciting new Poppy Playtime chapter with "
            "a new location, new characters, a new story, and details for the community."
        )
        first = {"title": "Official chapter announcement", "original_caption": caption}
        second = {"title": "A different syndicated title", "original_caption": caption}
        self.assertTrue(_fingerprints(first).intersection(_fingerprints(second)))


    def test_newsroom_article_uses_real_caption_and_article_image(self):
        homepage = "https://www.mobentertainment.com/"
        article = "https://www.mobentertainment.com/news/poppy-playtime-chapter-five"
        homepage_html = (
            '<html><body><a href="/news/poppy-playtime-chapter-five">'
            'Mob Entertainment announces Poppy Playtime Chapter Five</a></body></html>'
        )
        article_html = """
        <html><head>
          <meta property="og:title" content="Official Poppy Playtime announcement">
          <meta property="og:description" content="This is the original caption from the official article.">
          <meta property="og:image" content="https://cdn.mobentertainment.com/news/chapter-five.jpg">
          <meta property="article:published_time" content="2026-10-01T12:00:00Z">
        </head><body><article><p>Article body.</p></article></body></html>
        """
        session = FakeSession({
            homepage: FakeResponse(homepage_html),
            article: FakeResponse(article_html),
        })
        sources = [
            {"id": "mob_newsroom", "kind": "html_newsroom", "enabled": True,
             "source_name": "Mob Entertainment newsroom", "url": homepage},
            {"id": "mob_youtube", "kind": "youtube_atom", "enabled": False,
             "channel_id": "UCXqV4yoyC_mmZye6VbKFPeQ", "url": "https://www.youtube.com/channel/UCXqV4yoyC_mmZye6VbKFPeQ"},
            {"id": "steam_news", "kind": "steam_news_api", "enabled": False,
             "app_id": 1721470, "url": "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = config_file(folder, sources)
            rows, attempts = discover_complete_official_posts(config_path=path, session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_platform"], "website")
        self.assertEqual(rows[0]["original_caption"], "This is the original caption from the official article.")
        self.assertEqual(rows[0]["media_type"], "image")
        self.assertEqual(rows[0]["media_url"], "https://cdn.mobentertainment.com/news/chapter-five.jpg")
        self.assertIsNone(rows[0]["thumbnail_url"], "a post thumbnail must not replace its original media")
        self.assertEqual(rows[0]["published_at"], "2026-10-01T12:00:00+00:00")
        self.assertEqual(attempts[0]["result"], "ok")


    def test_nested_article_body_is_not_truncated_when_og_description_is_missing(self):
        homepage = "https://mobentertainment.com/"
        article = "https://mobentertainment.com/news/nested-story"
        homepage_html = (
            '<a href="/news/nested-story">'
            'Poppy Playtime official chapter announcement</a>'
        )
        article_html = """
        <html><head>
          <meta property="og:title" content="Official chapter announcement">
          <meta property="og:image" content="https://cdn.mobentertainment.com/news/story.jpg">
        </head><body>
          <article><div class="article-body">
            <div><p>The first genuine paragraph from the article.</p></div>
            <div><p>The second genuine paragraph remains part of the caption.</p></div>
          </div></article>
        </body></html>
        """
        session = FakeSession({
            homepage: FakeResponse(homepage_html),
            article: FakeResponse(article_html),
        })
        sources = [
            {"id": "mob_newsroom", "kind": "html_newsroom", "enabled": True,
             "source_name": "Mob Entertainment newsroom", "url": homepage},
            {"id": "mob_youtube", "kind": "youtube_atom", "enabled": False,
             "channel_id": "UCXqV4yoyC_mmZye6VbKFPeQ", "url": "https://www.youtube.com/channel/UCXqV4yoyC_mmZye6VbKFPeQ"},
            {"id": "steam_news", "kind": "steam_news_api", "enabled": False,
             "app_id": 1721470, "url": "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = config_file(folder, sources)
            rows, _ = discover_complete_official_posts(config_path=path, session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["original_caption"],
            "The first genuine paragraph from the article.",
        )
        self.assertNotIn("The second genuine paragraph", rows[0]["original_caption"])


    def test_site_header_logo_is_never_used_as_article_media(self):
        html = """
        <html><head>
          <meta property="og:title" content="Official Mob newsroom post">
          <meta property="og:description" content="An actual official news caption that has enough information.">
        </head><body>
          <header><img src="https://mobentertainment.com/payload-api/media/file/Mob_Entertainment_footer_logo.webp"></header>
          <main><article><p>The post text is present, but this story exposes no post media.</p></article></main>
        </body></html>
        """
        detail = _detail_from_page(html, "https://mobentertainment.com/news/example-story")
        self.assertEqual(detail["media_type"], "unknown")
        self.assertIsNone(detail["media_url"])

    def test_video_embedded_inside_article_is_selected_over_shared_header_logo(self):
        html = """
        <html><head>
          <meta property="og:title" content="Official Mob video post">
          <meta property="og:description" content="An actual official video announcement caption.">
        </head><body>
          <header><img src="https://mobentertainment.com/payload-api/media/file/Mob_Entertainment_footer_logo.webp"></header>
          <main><article>
            <p>Watch the reveal trailer here.</p>
            <iframe src="https://www.youtube.com/embed/fAXzLmsbrd8"></iframe>
          </article></main>
        </body></html>
        """
        detail = _detail_from_page(html, "https://mobentertainment.com/news/example-video")
        self.assertEqual(detail["media_type"], "video")
        self.assertEqual(detail["media_url"], "https://www.youtube.com/embed/fAXzLmsbrd8")


    def test_youtube_atom_404_falls_back_to_verified_channel_metadata(self):
        channel_id = "UCXqV4yoyC_mmZye6VbKFPeQ"
        channel_url = "https://www.youtube.com/channel/" + channel_id + "/videos"
        video_url = "https://www.youtube.com/watch?v=abcdefghijk"

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                if url == channel_url:
                    return {"entries": [{"id": "abcdefghijk", "title": "Official Mob trailer"}]}
                if url == video_url:
                    return {
                        "id": "abcdefghijk",
                        "title": "Official Mob trailer",
                        "description": "The genuine official Mob video description, with the details of this announcement.",
                        "timestamp": 1791200000,
                        "webpage_url": video_url,
                    }
                raise AssertionError("Unexpected YouTube URL")

        source = {
            "id": "mob_youtube",
            "enabled": True,
            "kind": "youtube_atom",
            "source_name": "Mob Entertainment YouTube",
            "source_platform": "youtube",
            "channel_id": channel_id,
            "url": "https://www.youtube.com/channel/" + channel_id,
        }

        # The Atom request is unavailable; the fallback must use the same verified
        # channel and retrieve the video description, not reuse a thumbnail.
        with patch("yt_dlp.YoutubeDL", FakeYoutubeDL):
            rows = _discover_youtube(FakeSession({}), [source])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_platform"], "youtube")
        self.assertEqual(rows[0]["media_type"], "video")
        self.assertEqual(rows[0]["media_url"], video_url)
        self.assertEqual(
            rows[0]["original_caption"],
            "The genuine official Mob video description, with the details of this announcement.",
        )

    def test_youtube_fallback_uses_the_video_post_not_its_thumbnail(self):
        homepage = "https://www.mobentertainment.com/"
        feed = "https://www.youtube.com/feeds/videos.xml?channel_id=UCXqV4yoyC_mmZye6VbKFPeQ"
        feed_xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:yt="http://www.youtube.com/xml/schemas/2015"
              xmlns:media="http://search.yahoo.com/mrss/">
          <entry>
            <yt:videoId>abcdefghijk</yt:videoId>
            <title>Official Mob trailer</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=abcdefghijk"/>
            <published>2026-10-10T12:00:00Z</published>
            <media:group><media:description>The actual original YouTube description.</media:description>
              <media:thumbnail url="https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"/>
            </media:group>
          </entry>
        </feed>"""
        session = FakeSession({
            homepage: FakeResponse("<html><body>No newsroom cards.</body></html>"),
            feed: FakeResponse(feed_xml),
        })
        sources = [
            {"id": "mob_newsroom", "kind": "html_newsroom", "enabled": True,
             "source_name": "Mob Entertainment newsroom", "url": homepage},
            {"id": "mob_youtube", "kind": "youtube_atom", "enabled": True,
             "channel_id": "UCXqV4yoyC_mmZye6VbKFPeQ", "url": "https://www.youtube.com/channel/UCXqV4yoyC_mmZye6VbKFPeQ"},
            {"id": "steam_news", "kind": "steam_news_api", "enabled": False,
             "app_id": 1721470, "url": "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = config_file(folder, sources)
            rows, _ = discover_complete_official_posts(config_path=path, session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_platform"], "youtube")
        self.assertEqual(rows[0]["media_type"], "video")
        self.assertEqual(rows[0]["media_url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertNotEqual(rows[0]["media_url"], "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg")
        self.assertEqual(rows[0]["original_caption"], "The actual original YouTube description.")


    def test_live_steam_api_external_post_resolves_canonical_item_and_embedded_youtube_media(self):
        homepage = "https://mobentertainment.com/"
        endpoint = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
        gid = "1844115010499376"
        steam_payload = {"appnews": {"newsitems": [{
            "gid": gid,
            "title": "Escape from Playtime Launches Soon",
            "url": "https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/" + gid,
            "is_external_url": True,
            "author": "Tyler (Mob Entertainment)",
            "date": 1789922676,
            "feedname": "steam_community_announcements",
            "contents": (
                '[p]Mob Entertainment announces an official Poppy Playtime update.[/p]'
                '[p]Read more at [url="https://example.com/signup"]the official signup page[/url].[/p]'
                '[previewyoutube="fAXzLmsbrd8;full"][/previewyoutube]'
            ),
        }]}}
        session = FakeSession({
            homepage: FakeResponse("<html><body>No newsroom cards.</body></html>"),
            endpoint: FakeResponse(payload=steam_payload),
        })
        sources = [
            {"id": "mob_newsroom", "kind": "html_newsroom", "enabled": True,
             "source_name": "Mob Entertainment newsroom", "url": homepage},
            {"id": "mob_youtube", "kind": "youtube_atom", "enabled": False,
             "channel_id": "UCXqV4yoyC_mmZye6VbKFPeQ", "url": "https://www.youtube.com/channel/UCXqV4yoyC_mmZye6VbKFPeQ"},
            {"id": "steam_news", "kind": "steam_news_api", "enabled": True,
             "app_id": 1721470, "url": endpoint},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = config_file(folder, sources)
            rows, _ = discover_complete_official_posts(config_path=path, session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_platform"], "steam")
        self.assertEqual(
            rows[0]["canonical_url"],
            "https://steamcommunity.com/games/1721470/announcements/detail/" + gid,
        )
        self.assertEqual(rows[0]["media_type"], "video")
        self.assertEqual(rows[0]["media_url"], "https://www.youtube.com/watch?v=fAXzLmsbrd8")
        self.assertIn("official Poppy Playtime update", rows[0]["original_caption"])
        self.assertNotIn("example.com", rows[0]["original_caption"])

    def test_steam_fallback_accepts_only_official_announcement_feed_with_media(self):
        homepage = "https://www.mobentertainment.com/"
        endpoint = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
        steam_payload = {"appnews": {"newsitems": [{
            "gid": "12345678901",
            "title": "Official Poppy Playtime update",
            "url": "https://steamcommunity.com/games/1721470/announcements/detail/12345678901",
            "date": 1791200000,
            "feedname": "steam_community_announcements",
            "contents": '<div>Official update caption.</div><img src="https://clan.cloudflare.steamstatic.com/images/12345/update.jpg">',
        }]}}
        session = FakeSession({
            homepage: FakeResponse("<html><body>No newsroom cards.</body></html>"),
            endpoint: FakeResponse(payload=steam_payload),
        })
        sources = [
            {"id": "mob_newsroom", "kind": "html_newsroom", "enabled": True,
             "source_name": "Mob Entertainment newsroom", "url": homepage},
            {"id": "mob_youtube", "kind": "youtube_atom", "enabled": False,
             "channel_id": "UCXqV4yoyC_mmZye6VbKFPeQ", "url": "https://www.youtube.com/channel/UCXqV4yoyC_mmZye6VbKFPeQ"},
            {"id": "steam_news", "kind": "steam_news_api", "enabled": True,
             "app_id": 1721470, "url": endpoint},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = config_file(folder, sources)
            rows, _ = discover_complete_official_posts(config_path=path, session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_platform"], "steam")
        self.assertEqual(rows[0]["media_type"], "image")
        self.assertEqual(rows[0]["media_url"], "https://clan.cloudflare.steamstatic.com/images/12345/update.jpg")
        self.assertIn("Official update caption.", rows[0]["original_caption"])


    def test_combined_sources_dedupe_crossposts_and_return_newest_verified_post_first(self):
        shared = (
            "Mob Entertainment announces an official Poppy Playtime update for the "
            "community with new details about the upcoming chapter and its characters."
        )
        website = {
            "platform": "mob_official",
            "source_platform": "website",
            "source_name": "Mob Entertainment",
            "source_priority": 0,
            "official_source": True,
            "id": "chapter-announcement",
            "external_id": "chapter-announcement",
            "canonical_url": "https://www.mobentertainment.com/news/chapter",
            "original_caption": shared,
            "caption": shared,
            "published_at": "2026-10-13T12:00:00+00:00",
            "media_type": "image",
            "media_url": "https://cdn.mobentertainment.com/news/chapter.jpg",
        }
        duplicate_x = {
            "platform": "mob_official",
            "source_platform": "x",
            "source_name": "Mob Entertainment on X",
            "source_priority": 3,
            "official_source": True,
            "id": "x-1734258130186559716",
            "external_id": "x-1734258130186559716",
            "canonical_url": "https://x.com/mobgamesstudios/status/1734258130186559716",
            "original_caption": shared,
            "caption": shared,
            "author": "@mobgamesstudios",
            "published_at": "2026-10-14T12:00:00+00:00",
            "media_type": "image",
            "media_url": "https://pbs.twimg.com/media/chapter.jpg",
        }
        latest_x = {
            **duplicate_x,
            "id": "x-1734258130186559717",
            "external_id": "x-1734258130186559717",
            "canonical_url": "https://x.com/mobgamesstudios/status/1734258130186559717",
            "original_caption": "A different verified official announcement with a fresh media attachment.",
            "caption": "A different verified official announcement with a fresh media attachment.",
            "published_at": "2026-10-14T13:00:00+00:00",
            "media_url": "https://pbs.twimg.com/media/latest.jpg",
        }
        with (
            patch(
                "agent.discovery.mob_official_complete.discover_complete_official_posts",
                return_value=([website], [{"source": "mob_newsroom", "result": "ok"}]),
            ),
            patch(
                "agent.discovery.termux_mob_x.discover_mob_x_posts",
                return_value=([duplicate_x, latest_x], [{"source": "mob_x", "result": "ok"}]),
            ),
        ):
            rows, attempts = discover_all_official_posts(max_results=12)

        self.assertEqual([row["external_id"] for row in rows], [
            "x-1734258130186559717",
            "chapter-announcement",
        ])
        self.assertIn("official_source_merge", [row.get("source") for row in attempts])


if __name__ == "__main__":
    unittest.main()
