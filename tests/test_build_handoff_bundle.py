import unittest

from scripts.build_handoff_bundle import is_excluded_path, scrub_text, suspicious_text


class HandoffBundleSecurityTests(unittest.TestCase):
    def test_environment_and_local_credential_files_are_excluded(self):
        for value in (".env", ".env.production", ".dev.vars", "nested/.env.local",
                      "secrets/token.json", "credentials.json"):
            with self.subTest(value=value):
                self.assertTrue(is_excluded_path(value))
        self.assertFalse(is_excluded_path("config/mob_translation_limits.json"))

    def test_named_secret_values_are_redacted_without_quotes_breaking(self):
        sample = 'RUBIKA_CHANNEL_GUID="channel-guid-example-value-123456"\nMEGA_PASSWORD: "not-a-real-password"'
        output = scrub_text(sample, {"RUBIKA_CHANNEL_GUID", "MEGA_PASSWORD"})
        self.assertNotIn("channel-guid-example-value-123456", output)
        self.assertNotIn("not-a-real-password", output)
        self.assertIn('"[REDACTED_VALUE]"', output)
        self.assertIn("RUBIKA_CHANNEL_GUID", output)

    def test_workflow_secret_expressions_are_preserved(self):
        sample = "RUBIKA_CHANNEL_GUID: " + "$" + "{{ secrets.RUBIKA_CHANNEL_GUID }}"
        output = scrub_text(sample, {"RUBIKA_CHANNEL_GUID"})
        self.assertIn("$" + "{{ secrets.RUBIKA_CHANNEL_GUID }}", output)
        self.assertFalse(suspicious_text(output))

    def test_provider_token_pattern_is_redacted(self):
        sample = "placeholder = 'gsk_" + ("A" * 30) + "'"
        output = scrub_text(sample, set())
        self.assertNotIn("gsk_", output)
        self.assertFalse(suspicious_text(output))

    def test_full_pem_key_block_is_redacted_but_header_in_source_code_is_allowed(self):
        sample = "\n".join([
            "private_key = '''-----BEGIN PRIVATE KEY-----",
            "QUJDREVGR0hJSktMTU5PUA==",
            "-----END PRIVATE KEY-----'''",
        ])
        output = scrub_text(sample, set())
        self.assertNotIn("QUJDREVGR0hJSktMTU5PUA==", output)
        self.assertIn("[REDACTED_PRIVATE_KEY]", output)
        source_marker = 're.compile(r"-----BEGIN PRIVATE KEY-----")'
        self.assertFalse(suspicious_text(scrub_text(source_marker, set())))


if __name__ == "__main__":
    unittest.main()
