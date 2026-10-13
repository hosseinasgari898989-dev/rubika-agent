from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.brain.authorization import SourceRegistry, SourceRegistryError
from agent.brain.models import Candidate


class SourceRegistryTests(unittest.TestCase):
    def candidate(self, **changes):
        values = {
            "platform": "youtube",
            "canonical_url": "https://www.youtube.com/watch?v=vkVFgmgLo1s",
            "external_id": "vkVFgmgLo1s",
            "title": "Poppy Playtime edit",
            "source_key": "UC_authorized_channel",
            "media_type": "video",
        }
        values.update(changes)
        return Candidate(**values)

    @staticmethod
    def payload():
        return {
            "version": 1,
            "sources": [],
            "items": [],
        }

    def test_empty_registry_denies_all_sources(self):
        registry = SourceRegistry.from_mapping(self.payload())
        decision = registry.check(self.candidate())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "source_not_authorized")

    def test_source_permission_requires_all_three_conditions(self):
        payload = self.payload()
        payload["sources"] = [{
            "platform": "youtube",
            "source_key": "UC_authorized_channel",
            "permission_basis": "license",
            "permission_confirmed": True,
            "enabled": True,
        }]
        registry = SourceRegistry.from_mapping(payload)
        decision = registry.check(self.candidate())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.basis, "license")

        disabled = self.payload()
        disabled["sources"] = [{
            "platform": "youtube",
            "source_key": "UC_authorized_channel",
            "permission_basis": "license",
            "permission_confirmed": True,
            "enabled": False,
        }]
        denied = SourceRegistry.from_mapping(disabled).check(self.candidate())
        self.assertFalse(denied.allowed)

    def test_platform_and_source_identity_must_match_exactly(self):
        payload = self.payload()
        payload["sources"] = [{
            "platform": "youtube",
            "source_key": "UC_authorized_channel",
            "permission_basis": "explicit_permission",
            "permission_confirmed": True,
            "enabled": True,
        }]
        registry = SourceRegistry.from_mapping(payload)
        self.assertFalse(registry.check(self.candidate(source_key="UC_other")).allowed)
        self.assertFalse(registry.check(
            self.candidate(platform="tiktok", source_key="UC_authorized_channel",
                           canonical_url="https://www.tiktok.com/@creator/video/7616247520657771796",
                           external_id="7616247520657771796")
        ).allowed)

    def test_specific_item_permission_can_authorize_one_video(self):
        payload = self.payload()
        payload["items"] = [{
            "platform": "youtube",
            "external_id": "vkVFgmgLo1s",
            "permission_basis": "owner",
            "permission_confirmed": True,
            "enabled": True,
        }]
        registry = SourceRegistry.from_mapping(payload)
        self.assertTrue(registry.check(self.candidate()).allowed)
        self.assertFalse(registry.check(
            self.candidate(external_id="n38wy3o0DhY",
                           canonical_url="https://www.youtube.com/watch?v=n38wy3o0DhY")
        ).allowed)

    def test_missing_or_invalid_permission_fields_fail_closed(self):
        for basis in ("", "trust", "public", "fair-use"):
            payload = self.payload()
            payload["sources"] = [{
                "platform": "youtube",
                "source_key": "UC_authorized_channel",
                "permission_basis": basis,
                "permission_confirmed": True,
                "enabled": True,
            }]
            with self.subTest(basis=basis), self.assertRaises(SourceRegistryError):
                SourceRegistry.from_mapping(payload)

    def test_duplicate_source_and_item_entries_are_rejected(self):
        payload = self.payload()
        row = {
            "platform": "youtube", "source_key": "UC_authorized_channel",
            "permission_basis": "owner", "permission_confirmed": True, "enabled": True,
        }
        payload["sources"] = [row, dict(row)]
        with self.assertRaises(SourceRegistryError):
            SourceRegistry.from_mapping(payload)

        payload = self.payload()
        item = {
            "platform": "youtube", "external_id": "vkVFgmgLo1s",
            "permission_basis": "owner", "permission_confirmed": True, "enabled": True,
        }
        payload["items"] = [item, dict(item)]
        with self.assertRaises(SourceRegistryError):
            SourceRegistry.from_mapping(payload)

    def test_missing_registry_file_is_empty_and_denies(self):
        registry = SourceRegistry.load("/definitely/missing/rubika-agent-registry.json")
        self.assertFalse(registry.check(self.candidate()).allowed)

    def test_project_level_poppy_edit_permission_matches_platform_wildcard(self):
        registry = SourceRegistry.load(
            Path(__file__).resolve().parents[1] / "config" / "trusted_sources.json"
        )
        youtube = registry.check(self.candidate(source_key="new_creator"))
        self.assertTrue(youtube.allowed)
        self.assertEqual(youtube.reason, "explicit_source_permission")
        self.assertEqual(youtube.basis, "explicit_permission")

        tiktok = self.candidate(
            platform="tiktok",
            canonical_url="https://www.tiktok.com/@new_creator/video/7616247520657771796",
            external_id="7616247520657771796",
            source_key="new_creator",
        )
        self.assertTrue(registry.check(tiktok).allowed)

    def test_project_permission_does_not_make_every_candidate_trusted(self):
        registry = SourceRegistry.load(
            Path(__file__).resolve().parents[1] / "config" / "trusted_sources.json"
        )
        self.assertNotIn(
            "new_creator",
            registry.authorized_source_keys("tiktok"),
        )
        self.assertIn("*", registry.authorized_source_keys("tiktok"))


if __name__ == "__main__":
    unittest.main()
