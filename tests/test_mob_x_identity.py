from __future__ import annotations

import unittest

from agent.brain.mob_x_identity import (
    mob_x_identity_aliases,
    normalize_mob_x_external_id,
)


POST_ID = "2111111111111111111"


class MobXIdentityTests(unittest.TestCase):
    def test_raw_prefixed_and_d1_qualified_ids_share_one_alias_set(self):
        expected = {
            POST_ID,
            "x-" + POST_ID,
            "mob_official:" + POST_ID,
            "mob_official:x-" + POST_ID,
        }
        for value in (
            POST_ID,
            "x-" + POST_ID,
            "mob_official:" + POST_ID,
            "mob_official:x-" + POST_ID,
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_mob_x_external_id(value), POST_ID)
                self.assertEqual(mob_x_identity_aliases(value), expected)

    def test_non_numeric_and_out_of_range_ids_never_create_aliases(self):
        for value in (
            "",
            "x-not-a-number",
            "x-" + POST_ID[:-1],
            "mob_official:legacy-id",
            "mob_official:tiktok:1234567890123456",
            "https://x.com/mobgamesstudios/status/" + POST_ID,
            None,
            12345678901234567,
        ):
            with self.subTest(value=value):
                self.assertIsNone(normalize_mob_x_external_id(value))
                self.assertEqual(mob_x_identity_aliases(value), set())


if __name__ == "__main__":
    unittest.main()
