import unittest
from unittest.mock import patch

from agent.brain import wiki_smoke as smoke


class WikiSmokeTests(unittest.TestCase):
    def test_extracts_method_is_used_when_available(self):
        def fake(base, params, timeout=30):
            if params.get("list") == "categorymembers":
                return 200, {"query": {"categorymembers": [{"title": "Huggy Wuggy"}]}}
            if params.get("prop") == "extracts":
                return 200, {"query": {"pages": {"1": {"extract": "x" * 80}}}}
            return 200, {}
        with patch.object(smoke, "_get", side_effect=fake):
            report = smoke.run_smoke()
        self.assertTrue(report["ok"])
        self.assertEqual(report["method"], "extracts")
        self.assertEqual(report["base"], smoke.BASES[0])

    def test_parse_fallback_is_used_when_extracts_are_empty(self):
        def fake(base, params, timeout=30):
            if params.get("list") == "categorymembers":
                return 200, {"query": {"categorymembers": []}}
            if params.get("prop") == "extracts":
                return 200, {"query": {"pages": {"1": {}}}}
            if params.get("action") == "parse":
                return 200, {"parse": {"text": {"*": "<p>" + "text " * 30 + "</p>"}}}
            return 200, {}
        with patch.object(smoke, "_get", side_effect=fake):
            report = smoke.run_smoke()
        self.assertTrue(report["ok"])
        self.assertEqual(report["method"], "parse")

    def test_blocked_wiki_is_reported_not_raised(self):
        with patch.object(smoke, "_get", return_value=(403, {})):
            report = smoke.run_smoke()
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["tested"]), 2)


if __name__ == "__main__":
    unittest.main()
