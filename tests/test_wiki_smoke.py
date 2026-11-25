import unittest
from unittest.mock import patch

from agent.brain import wiki_smoke as smoke


class WikiSmokeTests(unittest.TestCase):
    def test_first_working_base_is_reported(self):
        def fake(base, params, timeout=30):
            if params.get("list") == "categorymembers":
                return 200, {"query": {"categorymembers": [{"title": "Huggy Wuggy"}]}}
            return 200, {"query": {"pages": {"1": {"extract": "x" * 80}}}}
        with patch.object(smoke, "_get", side_effect=fake):
            report = smoke.run_smoke()
        self.assertTrue(report["ok"])
        self.assertEqual(report["base"], smoke.BASES[0])
        self.assertEqual(report["tested"][0]["category_members"], 1)

    def test_blocked_wiki_is_reported_not_raised(self):
        with patch.object(smoke, "_get", return_value=(403, {})):
            report = smoke.run_smoke()
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["tested"]), 2)
        self.assertEqual(report["tested"][0]["extract_status"], 403)


if __name__ == "__main__":
    unittest.main()
