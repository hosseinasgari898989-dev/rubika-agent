from __future__ import annotations
import unittest
from unittest.mock import Mock
from agent.brain.tiktok_urls import normalize_tiktok_url


class TikTokUrlNormalizationTests(unittest.TestCase):
    def test_canonicalizes_tracking_parameters(self):
        self.assertEqual(normalize_tiktok_url("https://www.tiktok.com/@meloaep_/video/7616247520657771796?is_from_webapp=1"), ("meloaep_", "7616247520657771796", "https://www.tiktok.com/@meloaep_/video/7616247520657771796"))

    def test_rejects_non_numeric_ids_and_unrelated_hosts(self):
        self.assertIsNone(normalize_tiktok_url("https://www.tiktok.com/@meloaep_/video/not-a-number"))
        self.assertIsNone(normalize_tiktok_url("https://example.com/@meloaep_/video/7616247520657771796"))

    def test_resolves_vm_short_link_once(self):
        session = Mock()
        session.get.return_value.url = "https://www.tiktok.com/@smyxae/video/7616247520657771796?utm_source=share"
        self.assertEqual(normalize_tiktok_url("https://vm.tiktok.com/ZMk123abc/", session=session), ("smyxae", "7616247520657771796", "https://www.tiktok.com/@smyxae/video/7616247520657771796"))
        session.get.assert_called_once_with("https://vm.tiktok.com/ZMk123abc/", timeout=8, allow_redirects=True)

    def test_resolves_tiktok_com_t_short_link_once(self):
        session = Mock()
        session.get.return_value.url = "https://www.tiktok.com/@poppytv_ae/video/7616247520657771796"
        self.assertEqual(normalize_tiktok_url("https://www.tiktok.com/t/ZMk123abc/", session=session)[:2], ("poppytv_ae", "7616247520657771796"))
        session.get.assert_called_once()
