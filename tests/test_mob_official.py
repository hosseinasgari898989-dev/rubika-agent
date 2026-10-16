from __future__ import annotations

import unittest
from unittest.mock import patch

from agent.brain.mob_translation import format_mob_post, translate_to_persian
from agent.discovery.mob_official import (
    _atom_entries,
    _fingerprint,
    _newsroom_entries,
    mark_crossposts,
)


class FakeTranslator:
    def __init__(self, source="auto", target="fa"):
        self.source = source
        self.target = target

    def translate(self, value):
        self.last_value = value
        return "پنج سال از زمانی که برای اولین بار از هاگی فرار کردی، گذشته است."


class MobOfficialDiscoveryTests(unittest.TestCase):
    def test_atom_feed_extracts_official_post_metadata(self):
        source = {
            "id": "official-blog",
            "source_platform": "website",
            "source_name": "Poppy Playtime official news",
            "url": "https://poppyplaytime.com/blogs/news.atom",
        }
        body = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>tag:example,2026:one</id>
            <title>Five years since you first ran from Huggy 🎈</title>
            <link rel="alternate" href="https://poppyplaytime.com/blogs/news/five-years"/>
            <published>2026-10-14T12:00:00Z</published>
            <summary>Celebrate the anniversary.</summary>
          </entry>
        </feed>"""
        rows = _atom_entries(body, source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "mob_official")
        self.assertEqual(rows[0]["title"], "Five years since you first ran from Huggy 🎈")
        self.assertEqual(rows[0]["caption"], "Celebrate the anniversary.")
        self.assertEqual(rows[0]["published_at"], "2026-10-14T12:00:00+00:00")
        self.assertTrue(rows[0]["official_source"])

    def test_youtube_atom_entry_is_classified_as_video(self):
        source = {
            "id": "mob-youtube",
            "source_platform": "youtube",
            "source_name": "Mob Entertainment YouTube",
            "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCXqV4yoyC_mmZye6VbKFPeQ",
        }
        body = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:yt="http://www.youtube.com/xml/schemas/2015"
              xmlns:media="http://search.yahoo.com/mrss/">
          <entry>
            <yt:videoId>abcdefghijk</yt:videoId>
            <title>Official trailer</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=abcdefghijk"/>
            <published>2026-10-14T12:00:00Z</published>
            <media:group>
              <media:description>Watch the new trailer.</media:description>
              <media:thumbnail url="https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"/>
            </media:group>
          </entry>
        </feed>"""
        rows = _atom_entries(body, source)
        self.assertEqual(rows[0]["media_type"], "video")
        self.assertEqual(rows[0]["id"], "youtube:abcdefghijk")
        self.assertEqual(rows[0]["thumbnail_url"], "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg")

    def test_crosspost_marks_first_only_when_timestamps_can_be_compared(self):
        rows = [
            {"title": "Same launch post caption", "caption": "", "source_name": "YouTube",
             "canonical_url": "https://www.youtube.com/watch?v=abcdefghijk",
             "published_at": "2026-10-14T12:00:00Z"},
            {"title": "Same launch post caption", "caption": "", "source_name": "TikTok",
             "canonical_url": "https://www.tiktok.com/@mob.entertainment/video/12345678901",
             "published_at": "2026-10-14T11:00:00Z"},
        ]
        result = mark_crossposts(rows)
        self.assertEqual(result[0]["first_published_source"], "TikTok")
        self.assertEqual(result[0]["first_source_confidence"], "timestamp_comparison")
        self.assertTrue(_fingerprint(rows[0]))

    def test_crosspost_does_not_guess_first_source_without_two_timestamps(self):
        rows = [
            {"title": "Same launch post caption", "caption": "", "source_name": "YouTube",
             "canonical_url": "https://www.youtube.com/watch?v=abcdefghijk",
             "published_at": "2026-10-14T12:00:00Z"},
            {"title": "Same launch post caption", "caption": "", "source_name": "Instagram",
             "canonical_url": "https://www.instagram.com/p/abcdefghijk/",
             "published_at": None},
        ]
        result = mark_crossposts(rows)
        self.assertIsNone(result[0]["first_published_source"])
        self.assertEqual(result[1]["first_source_confidence"], "unknown_insufficient_timestamps")

    def test_newsroom_does_not_assign_homepage_description_to_every_article(self):
        source = {
            "id": "newsroom",
            "source_platform": "website",
            "source_name": "Mob Entertainment newsroom",
            "url": "https://mobentertainment.com/",
        }
        body = """<html><head><meta name="description" content="Generic homepage text"></head>
        <body><a href="/news/poppy-playtime-chapter-five">Mob Entertainment announces Poppy Playtime Chapter Five</a></body></html>"""
        rows = _newsroom_entries(body, source)
        if rows:
            self.assertNotEqual(rows[0]["caption"], "Generic homepage text")


class MobTranslationTests(unittest.TestCase):
    def test_formats_original_and_persian_translation_with_source(self):
        item = {
            "source_name": "Mob Entertainment",
            "original_caption": "Five years since you first ran from Huggy 🎈",
            "canonical_url": "https://mobentertainment.com/news/anniversary",
        }
        result = format_mob_post(item, translation_provider=lambda _: "پنج سال از فرار اولت از هاگی گذشته است.")
        self.assertIn("🎮 ماب انترتینمنت پستی منتشر کرد و گفت:", result["caption"])
        self.assertIn("Five years since you first ran from Huggy", result["caption"])
        self.assertIn("📝 ترجمه فارسی:", result["caption"])
        self.assertIn("پنج سال از فرار اولت از هاگی گذشته است.", result["caption"])
        self.assertNotIn("https://", result["caption"])
        self.assertEqual(result["source_url"], "https://mobentertainment.com/news/anniversary")
        self.assertEqual(result["translation_status"], "translated")

    def test_no_caption_does_not_invent_a_quote_or_translation(self):
        result = format_mob_post({
            "source_name": "Mob Entertainment",
            "title": "Official image",
            "canonical_url": "https://mobentertainment.com/news/image",
        })
        self.assertEqual(result["translation_status"], "no_caption")
        self.assertNotIn("📝 ترجمه فارسی:", result["caption"])
        self.assertNotIn('""', result["caption"])
        self.assertEqual(result["caption"], "")

    def test_translation_provider_errors_do_not_crash_formatting(self):
        def broken(_):
            raise RuntimeError("provider unavailable")

        result = format_mob_post({
            "source_name": "Mob Entertainment",
            "caption": "A caption worth translating",
            "canonical_url": "https://mobentertainment.com/news/a-post",
        }, translation_provider=broken)
        self.assertEqual(result["translation_status"], "unavailable")
        self.assertIn("A caption worth translating", result["caption"])
        self.assertNotIn("https://", result["caption"])
        self.assertEqual(result["source_url"], "https://mobentertainment.com/news/a-post")


    def test_urls_are_removed_from_original_and_translation_but_source_is_kept_as_metadata(self):
        result = format_mob_post(
            {
                "original_caption": "Read more at https://example.com/news now or visit mobentertainment.com/news",
                "canonical_url": "https://mobentertainment.com/news/sample",
            },
            translation_provider=lambda _: "بیشتر بخوانید: https://example.com/fa یا mobentertainment.com/fa",
        )
        self.assertEqual(result["translation_status"], "translated")
        self.assertNotIn("http://", result["caption"])
        self.assertNotIn("https://", result["caption"])
        self.assertNotIn("www.", result["caption"])
        self.assertNotIn("mobentertainment.com", result["caption"])
        self.assertNotIn("example.com", result["caption"])
        self.assertIn("🎮 ماب انترتینمنت پستی منتشر کرد و گفت:", result["caption"])
        self.assertIn("📝 ترجمه فارسی:", result["caption"])
        self.assertEqual(result["source_url"], "https://mobentertainment.com/news/sample")



    def test_groq_translation_is_primary_and_ledgered(self):
        from types import SimpleNamespace

        result_value = SimpleNamespace(
            provider="groq",
            model="openai/gpt-oss-20b",
            text="ماب انترتینمنت یک خبر رسمی منتشر کرد.",
            usage={"totalTokenCount": 32},
        )
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger", return_value=result_value) as call:
            result = translate_to_persian(
                "Mob Entertainment published an update at https://example.com/news."
            )

        self.assertEqual(result, "ماب انترتینمنت یک خبر رسمی منتشر کرد.")
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs["provider"], "groq")
        self.assertEqual(call.call_args.kwargs["role"], "translate_official")
        self.assertNotIn("example.com", call.call_args.kwargs["prompt"])

    def test_invalid_groq_output_falls_back_to_openrouter(self):
        from types import SimpleNamespace

        groq = SimpleNamespace(provider="groq", model="test", text="This is still English.", usage={})
        openrouter = SimpleNamespace(
            provider="openrouter",
            model="openrouter/free",
            text="به عمق کارخانه پلی‌تایم بروید و برای ساخت مکانی مرموز تلاش کنید.",
            usage={},
        )
        with patch(
            "agent.brain.ai_provider_clients.call_provider_with_ledger",
            side_effect=[groq, openrouter],
        ) as call:
            result = translate_to_persian(
                "Explore the depths of Playtime Co. while working to build a mysterious location."
            )

        self.assertIsInstance(result, str)
        self.assertIn("به عمق", result)
        self.assertNotRegex(result, r"[\u0400-\u04FF]")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0].kwargs["provider"], "groq")
        self.assertEqual(call.call_args_list[1].kwargs["provider"], "openrouter")

    def test_long_official_caption_is_translated_in_bounded_chunks(self):
        calls = []

        class ChunkTranslator:
            def translate(self, value):
                calls.append(value)
                self.assert_short = len(value) <= 700
                return "ترجمه " + value

        def factory(**kwargs):
            return ChunkTranslator()

        source = (
            "Mob Entertainment announced an official update. " * 24
        ).strip()
        translated = translate_to_persian(source, translator_factory=factory)
        self.assertIsInstance(translated, str)
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(len(value) <= 700 for value in calls))
        self.assertTrue(translated.startswith("ترجمه "))


    def test_translator_factory_output_with_cyrillic_is_rejected(self):
        translated = translate_to_persian(
            "Explore the depths of Playtime Co.",
            translator_factory=lambda **_: type(
                "Translator",
                (),
                {"translate": lambda self, _value: "به عمق Playtime Co. بروید, еще اسرارآمیزتر."},
            )(),
        )
        self.assertIsNone(translated)

    def test_translation_provider_factory_is_injectable(self):
        result = translate_to_persian(
            "Five years since you first ran from Huggy 🎈",
            translator_factory=lambda **kwargs: FakeTranslator(**kwargs),
        )
        self.assertIn("پنج سال", result)


if __name__ == "__main__":
    unittest.main()
