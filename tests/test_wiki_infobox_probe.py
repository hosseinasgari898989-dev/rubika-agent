import unittest
from unittest.mock import patch

from agent.brain import wiki_infobox_probe as probe

HTML = (
    '<aside class="portable-infobox">'
    '<div class="pi-item pi-data"><h3 class="pi-data-label">Species</h3><div class="pi-data-value">Toy<br>(Hybrid) <sup>[1]</sup></div></div>'
    '<div class="pi-item pi-data"><h3 class="pi-data-label">First appearance</h3><div class="pi-data-value"><a href="/x">Chapter 1</a></div></div>'
    '<div class="pi-item pi-data"><h3 class="pi-data-label">Gender</h3><div class="pi-data-value">Male</div></div>'
    "</aside>"
)


class InfoboxProbeTests(unittest.TestCase):
    def test_parser_reads_label_value_pairs_with_nested_markup(self):
        rows = dict(probe.parse_infobox(HTML))
        self.assertEqual(rows["First appearance"], "Chapter 1")
        self.assertEqual(rows["Gender"], "Male")
        self.assertTrue(rows["Species"].startswith("Toy"))

    def test_probe_counts_label_frequency_and_skips_pages_without_infobox(self):
        def fake(params, timeout=30):
            if params.get("list") == "categorymembers":
                return 200, {"query": {"categorymembers": [{"title": f"Page {i}"} for i in range(5)]}}
            title = params.get("page")
            body = HTML if title != "Page 4" else "<p>video page</p>"
            return 200, {"parse": {"text": {"*": body}}}
        with patch.object(probe, "_get", side_effect=fake):
            report = probe.run_probe(sleep_seconds=0)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["pages"]), 4)
        self.assertEqual(report["label_frequency"]["Gender"], 4)

    def test_blocked_wiki_is_reported_not_raised(self):
        with patch.object(probe, "_get", return_value=(403, {})):
            report = probe.run_probe(sleep_seconds=0)
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
