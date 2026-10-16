from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.brain.mob_translation import (
    _strip_urls,
    format_mob_post,
    translate_to_persian,
)


class MobTranslationTests(unittest.TestCase):
    def test_production_translation_uses_gemini_and_durable_role(self):
        model_result = SimpleNamespace(
            provider="gemini",
            model="gemini-3.8-flash",
            text="ماب انترتینمنت خبر رسمی تازه‌ای منتشر کرد.",
            usage={"totalTokenCount": 21},
        )
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger", return_value=model_result) as call:
            translated = translate_to_persian(
                "Mob Entertainment published an official update. https://example.com/news"
            )

        self.assertEqual(translated, "ماب انترتینمنت خبر رسمی تازه‌ای منتشر کرد.")
        call.assert_called_once()
        args = call.call_args.kwargs
        self.assertEqual(args["provider"], "gemini")
        self.assertEqual(args["role"], "translate_official")
        self.assertLessEqual(args["timeout_seconds"], 45)
        self.assertIn("untrusted text", args["prompt"])
        self.assertNotIn("https://example.com/news", args["prompt"])

    def test_invalid_gemini_translation_fails_closed_without_fallback(self):
        invalid = SimpleNamespace(provider="gemini", model="gemini-3.8-flash", text="This is English.", usage={})
        with patch(
            "agent.brain.ai_provider_clients.call_provider_with_ledger",
            return_value=invalid,
        ) as call:
            result = translate_to_persian("An official source sentence.")

        self.assertIsNone(result)
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs["provider"], "gemini")

    def test_ledger_failure_fails_closed_without_untracked_fallback(self):
        from agent.brain.gemini_usage_client import UsageLedgerError

        with patch(
            "agent.brain.ai_provider_clients.call_provider_with_ledger",
            side_effect=UsageLedgerError("usage_ledger_unavailable"),
        ) as call:
            result = translate_to_persian("An official source sentence.")

        self.assertIsNone(result)
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs["provider"], "gemini")

    def test_official_caption_keeps_source_url_internal_and_requires_translation(self):
        item = {
            "original_caption": "Mob Entertainment published a new official update. https://example.com/news",
            "canonical_url": "https://example.com/news",
            "source_name": "Mob Entertainment",
        }
        formatted = format_mob_post(item, translation_provider=lambda _: "ماب انترتینمنت خبر رسمی تازه‌ای منتشر کرد.")
        self.assertEqual(formatted["translation_status"], "translated")
        self.assertNotIn("https://", formatted["caption"])
        self.assertEqual(formatted["source_url"], "https://example.com/news")
        self.assertIn("ماب انترتینمنت", formatted["caption"])

    def test_url_stripping_does_not_return_empty_or_unrelated_script_as_valid_translation(self):
        self.assertEqual(_strip_urls("خبر رسمی https://example.com/news منتشر شد"), "خبر رسمی منتشر شد")

    def test_translation_provider_factory_remains_injectable_for_offline_tests(self):
        result = translate_to_persian(
            "An official update.",
            translator_factory=lambda **_: type(
                "FakeTranslator", (), {"translate": lambda self, _value: "ترجمهٔ آزمایشی معتبر."}
            )(),
        )
        self.assertEqual(result, "ترجمهٔ آزمایشی معتبر.")


if __name__ == "__main__":
    unittest.main()
