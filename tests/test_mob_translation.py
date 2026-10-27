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
    def test_production_translation_uses_groq_and_durable_role(self):
        model_result = SimpleNamespace(
            provider="groq",
            model="openai/gpt-oss-20b",
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
        self.assertEqual(args["provider"], "groq")
        self.assertEqual(args["role"], "translate_official")
        self.assertLessEqual(args["timeout_seconds"], 45)
        self.assertIn("untrusted text", args["prompt"])
        self.assertNotIn("https://example.com/news", args["prompt"])

    def test_invalid_groq_translation_falls_back_to_openrouter_once(self):
        invalid = SimpleNamespace(provider="groq", model="model", text="This is English.", usage={})
        valid = SimpleNamespace(provider="openrouter", model="openrouter/free", text="متن فارسی معتبر.", usage={})
        with patch(
            "agent.brain.ai_provider_clients.call_provider_with_ledger",
            side_effect=[invalid, valid],
        ) as call:
            result = translate_to_persian("An official source sentence.")

        self.assertEqual(result, "متن فارسی معتبر.")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0].kwargs["provider"], "groq")
        self.assertEqual(call.call_args_list[1].kwargs["provider"], "openrouter")
        self.assertEqual(call.call_args_list[0].kwargs["run_id"], call.call_args_list[1].kwargs["run_id"])

    def test_cloudflare_worker_ai_is_last_fallback_after_groq_and_openrouter(self):
        from agent.brain.ai_provider_clients import ProviderClientError
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger",side_effect=[ProviderClientError("http_503"),ProviderClientError("http_429")]) as call, \
             patch("agent.brain.cloud_state.CloudState") as cloud:
            cloud.return_value.translate_official.return_value="ترجمه فارسی معتبر."
            self.assertEqual(translate_to_persian("An official source sentence."),"ترجمه فارسی معتبر.")
        self.assertEqual([x.kwargs["provider"] for x in call.call_args_list],["groq","openrouter"])
        cloud.return_value.translate_official.assert_called_once()

    def test_all_three_translation_providers_failing_returns_none(self):
        from agent.brain.ai_provider_clients import ProviderClientError
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger",side_effect=[ProviderClientError("http_503"),ProviderClientError("http_503")]), \
             patch("agent.brain.cloud_state.CloudState") as cloud:
            cloud.return_value.translate_official.side_effect=RuntimeError("unavailable")
            self.assertIsNone(translate_to_persian("An official source sentence."))
        cloud.return_value.translate_official.assert_called_once()

    def test_worker_fallback_output_is_validated(self):
        from agent.brain.ai_provider_clients import ProviderClientError
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger",side_effect=[ProviderClientError("http_503"),ProviderClientError("http_503")]), \
             patch("agent.brain.cloud_state.CloudState") as cloud:
            cloud.return_value.translate_official.return_value="English only"
            self.assertIsNone(translate_to_persian("An official source sentence."))

    def test_caption_above_2100_characters_never_calls_provider(self):
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger") as call:
            self.assertIsNone(translate_to_persian("a"*2101))
        call.assert_not_called()

    def test_subtitle_batch_uses_1200_tokens(self):
        source="0|The door is open.\n1|Go inside now."
        result=SimpleNamespace(provider="groq",model="test",text="0|در باز است.\n1|همین حالا وارد شوید.",usage={})
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger",return_value=result) as call:
            self.assertEqual(translate_to_persian(source,kind="subtitle",run_id="mobsub_"+"a"*32),result.text)
        self.assertEqual(call.call_args.kwargs["max_output_tokens"],1200)

    def test_ledger_failure_fails_closed_without_untracked_fallback(self):
        from agent.brain.ai_usage_ledger import UsageLedgerError
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger",side_effect=UsageLedgerError("usage_ledger_unavailable")) as call, \
             patch("agent.brain.cloud_state.CloudState") as cloud:
            self.assertIsNone(translate_to_persian("An official source sentence."))
        call.assert_called_once()
        cloud.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
