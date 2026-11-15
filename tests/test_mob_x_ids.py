from __future__ import annotations

import unittest

from agent.brain.mob_x_ids import (
    canonical_mob_x_external_id,
    canonical_mob_x_item_id,
    mob_x_item_id_aliases,
)


class MobXCanonicalIdentityTests(unittest.TestCase):
    def test_numeric_and_prefixed_external_ids_have_one_canonical_form(self):
        post_id = "2121390526633001062"
        for value in (post_id, "x-" + post_id):
            with self.subTest(value=value):
                self.assertEqual(canonical_mob_x_external_id(value), "x-" + post_id)
                self.assertEqual(canonical_mob_x_item_id(value), "mob_official:x-" + post_id)
                self.assertEqual(
                    mob_x_item_id_aliases(value),
                    ("mob_official:x-" + post_id, "mob_official:" + post_id),
                )

    def test_invalid_or_non_numeric_ids_are_rejected(self):
        for value in ("", "x-not-numeric", "12345678901234", "x-12345678901234567890123", "../12345678901234567"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "invalid_mob_x_external_id"):
                    canonical_mob_x_external_id(value)


if __name__ == "__main__":
    unittest.main()
