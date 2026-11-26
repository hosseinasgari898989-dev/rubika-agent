import unittest

from agent.source import wiki_source as wiki

HTML = (
    '<aside><div><h3 class="pi-data-label">Species</h3><div class="pi-data-value">Toy<br>Hybrid<sup>[1]</sup></div></div>'
    '<div><h3 class="pi-data-label">Status</h3><div class="pi-data-value"><a href="/x">Alive</a></div></div>'
    '<div><h3 class="pi-data-label">Debut</h3><div class="pi-data-value"><ul><li>Chapter 1</li><li>Chapter 2</li></ul></div></div></aside>'
)


class WikiSourceTests(unittest.TestCase):
    def test_parser_separates_multi_values_and_drops_footnotes(self):
        rows = dict(wiki.parse_infobox(HTML))
        self.assertEqual(rows["Species"], "Toy | Hybrid")
        self.assertEqual(rows["Status"], "Alive")
        self.assertEqual(rows["Debut"], "Chapter 1 | Chapter 2")

    def test_character_filter_needs_species_status_debut_and_no_type(self):
        base = {"Species": "Toy", "Status": "Alive", "Debut": "Chapter 1"}
        self.assertTrue(wiki.is_character(base))
        self.assertFalse(wiki.is_character({**base, "Type": "Documentary"}))
        self.assertFalse(wiki.is_character({"Species": "Toy", "Status": "Alive"}))

    def test_page_url_is_wiki_path(self):
        self.assertEqual(wiki.page_url("Huggy Wuggy"), "https://poppy-playtime.fandom.com/wiki/Huggy_Wuggy")


if __name__ == "__main__":
    unittest.main()
